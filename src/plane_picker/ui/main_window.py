from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QSplitter, QLabel, QToolBar,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox, QComboBox)

from ..service import PickerService
from ..calibration.validator import MappingValidator
from ..storage.csv_export import export_csv
from .image_view import ImageView
from .plane_view import PlaneView
from .shot_table import ShotTable


class WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, config=None, id_allocator=None):
        super().__init__()
        config = config or {}
        self.service = PickerService(config.get("camera_id", "cam_left"), config.get("ransac_threshold_mm",3.),
                                     config.get("ransac_threshold_px",3.),id_allocator)
        self.grid_mm = float(config.get("grid_mm",100.))
        if not 0 < self.grid_mm < 1e9:
            raise ValueError("grid_mm 必须为有限正数且小于 1e9")
        self.selected_id = None
        self.color = "red"
        self.validation = None
        self.validation_active = False
        self.busy = False
        self.dirty = False
        self._worker = None
        self.pool = QThreadPool(self)
        self.setWindowTitle("AprilTag 平面点选定位 · Phase 1")
        self.resize(1440,920)
        self.image_view, self.plane_view, self.table = ImageView(), PlaneView(), ShotTable()
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("padding: 10px; background: #3e3420; color: #ffe39b;")
        split = QSplitter()
        for title,view in (("相机图像 · 点击添加 / 拖动已有点 / 空白处拖拽平移",self.image_view),
                           ("AprilTag 平面 · +X 向右 / +Y 向上 / 单位 mm",self.plane_view)):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.addWidget(QLabel(title))
            layout.addWidget(view)
            split.addWidget(panel)
        bottom = QSplitter(Qt.Orientation.Vertical)
        bottom.addWidget(split)
        bottom.addWidget(self.table)
        bottom.setSizes([630,170])
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.banner)
        layout.addWidget(bottom)
        self.setCentralWidget(root)
        self._build_actions()
        self.image_view.clicked.connect(lambda x,y: self.safe(lambda: self.add_point(x,y)))
        self.image_view.hovered.connect(self.hover)
        self.image_view.moved.connect(lambda sid,x,y: self.safe(lambda: self.move_point(sid,x,y)))
        self.plane_view.moved.connect(lambda sid,x,y: self.safe(lambda: self.move_plane(sid,x,y)))
        self.image_view.selected.connect(self.select)
        self.plane_view.selected.connect(self.select)
        self.table.selected.connect(self.select)
        self.table.doubleClicked.connect(lambda _: self.safe(self.edit_point))
        self.refresh()

    def _build_actions(self):
        self.toolbars = []
        def bar(title):
            b = QToolBar(title)
            b.setMovable(False)
            self.addToolBar(b)
            self.toolbars.append(b)
            return b
        def action(b, text, handler, shortcut=None, checkable=False):
            a = QAction(text,self)
            a.setCheckable(checkable)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(lambda _=False: self.safe(handler))
            b.addAction(a)
            return a
        b = bar("输入与标定")
        action(b,"打开图像",self.open_image,"Ctrl+O")
        action(b,"相机内参",self.load_camera)
        action(b,"Tag 布局",self.load_layout)
        self.debug_action = action(b,"⚠ 零畸变调试",self.toggle_debug,checkable=True)
        action(b,"检测 AprilTag",self.detect)
        action(b,"计算映射",self.calculate)
        action(b,"加载平面标定",self.load_calibration)
        action(b,"保存平面标定",self.save_calibration)
        self.addToolBarBreak()
        b = bar("点选编辑")
        group = QActionGroup(self)
        for text,color in (("● 红色","red"),("● 蓝色","blue")):
            a = action(b,text,lambda c=color: setattr(self,"color",c),checkable=True)
            group.addAction(a)
            a.setChecked(color=="red")
        action(b,"撤销",lambda: self.mutate(self.service.undo),"Ctrl+Z")
        action(b,"删除选中",lambda: self.mutate(lambda: self.service.delete_shot(self.selected_id)),"Delete")
        action(b,"编辑 / 微调",self.edit_point)
        action(b,"重排显示编号",lambda: self.mutate(self.service.renumber_labels))
        action(b,"清空本轮",self.clear_round)
        action(b,"新建本轮",self.new_round)
        self.und_action = action(b,"去畸变显示",self.toggle_display,checkable=True)
        action(b,"适配两视图",self.fit_views,"F")
        self.addToolBarBreak()
        b = bar("保存与验证")
        action(b,"保存 Session",self.save_session,"Ctrl+S")
        action(b,"加载 Session",self.load_session)
        action(b,"导出 CSV",self.export_csv)
        action(b,"导出 JSON",self.export_json)
        action(b,"UI 截图",self.screenshot)
        action(b,"加载独立验证点",self.load_validation)
        action(b,"撤销验证点",self.undo_validation)
        action(b,"结束验证",self.end_validation)
        action(b,"导出验证报告",self.export_validation)

    def safe(self, function):
        if self.busy:
            return
        try:
            function()
        except Exception as exc:
            QMessageBox.warning(self,"操作未完成",str(exc))

    def choose_open(self,title,filter):
        return QFileDialog.getOpenFileName(self,title,"",filter)[0]

    def choose_save(self,title,name,filter):
        return QFileDialog.getSaveFileName(self,title,name,filter)[0]

    def discard_ok(self):
        return not self.dirty or QMessageBox.question(self,"未保存的修改","当前修改尚未保存。是否放弃这些修改？") == QMessageBox.StandardButton.Yes

    def reset_validation(self):
        self.validation = None
        self.validation_active = False

    def sync_flags(self):
        self.debug_action.setChecked(bool(self.service.camera and not self.service.camera.intrinsics_valid))
        if self.service.camera is None:
            self.und_action.setChecked(False)

    def open_image(self):
        p = self.choose_open("打开原始相机图像","图像 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*)")
        if p:
            self.service.open_image(p)
            self.reset_validation()
            self.sync_flags()
            self.refresh(image_changed=True,fit=True)
            self.dirty = True

    def load_camera(self):
        p = self.choose_open("加载相机内参","YAML (*.yaml *.yml)")
        if p:
            self.service.load_camera(p)
            self.reset_validation()
            self.sync_flags()
            self.refresh(image_changed=True)
            self.dirty = True

    def load_layout(self):
        p = self.choose_open("加载 Tag 布局","YAML (*.yaml *.yml)")
        if p:
            self.service.load_layout(p)
            self.reset_validation()
            self.refresh(fit=True)
            self.dirty = True

    def toggle_debug(self):
        try:
            self.service.set_debug(self.debug_action.isChecked())
        finally:
            self.sync_flags()
        self.reset_validation()
        self.refresh(image_changed=True)
        self.dirty = True

    def toggle_display(self):
        if self.und_action.isChecked() and self.service.camera is None:
            self.und_action.setChecked(False)
            raise ValueError("请先加载相机内参")
        self.refresh(image_changed=True)

    def background(self,function,callback):
        self.busy = True
        for b in self.toolbars:
            b.setEnabled(False)
        self.banner.setText("正在后台处理，请稍候…")
        self._worker = Worker(function)
        def finish(result):
            self.busy = False
            for b in self.toolbars:
                b.setEnabled(True)
            self.safe(lambda: callback(result))
        def fail(message):
            self.busy = False
            for b in self.toolbars:
                b.setEnabled(True)
            self.refresh()
            QMessageBox.warning(self,"后台处理失败",message)
        self._worker.signals.done.connect(finish)
        self._worker.signals.failed.connect(fail)
        self.pool.start(self._worker)

    def detect(self):
        def accept(result):
            self.service.detections = result
            self.refresh()
            if not result:
                QMessageBox.information(self,"检测结果","未检测到 AprilTag。请检查 family、清晰度和完整白边。")
        self.background(self.service.detect,accept)

    def calculate(self):
        def accept(mapper):
            self.service.accept_mapping(mapper)
            self.reset_validation()
            self.refresh(fit=True)
            self.dirty = True
        self.background(self.service.calculate,accept)

    def load_calibration(self):
        p = self.choose_open("加载平面标定（相机位置必须与标定时一致）","YAML (*.yaml *.yml)")
        if p:
            self.service.load_calibration(p)
            self.reset_validation()
            self.sync_flags()
            self.refresh(image_changed=True,fit=True)
            self.dirty = True

    def save_calibration(self):
        p = self.choose_save("保存平面标定","plane_calibration.yaml","YAML (*.yaml)")
        if p:
            self.service.save_calibration(p)
            self.refresh()

    def select(self,sid):
        if self.busy:
            return
        self.selected_id = sid
        self.refresh()

    def mutate(self,function):
        function()
        self.dirty = True
        self.refresh()

    def add_point(self,x,y):
        raw = self.service.display_to_raw([x,y], self.und_action.isChecked())
        if self.validation_active and self.validation:
            _,xy = self.service.mapping(raw)
            self.validation.add(xy,raw.tolist())
            if self.validation.next_target is None:
                self.validation_active = False
                report = self.validation.report()["statistics"]
                QMessageBox.information(self,"独立验证完成", "\n".join(f"{k}: {v:.4f} mm" for k,v in report.items()))
            self.refresh()
            return
        shot = self.service.add_shot(raw,self.color)
        self.selected_id = shot.shot_id
        self.dirty = True
        self.refresh()

    def move_point(self,sid,x,y):
        raw = self.service.display_to_raw([x,y], self.und_action.isChecked())
        shot = next(s for s in self.service.session.shots if s.shot_id==sid)
        self.mutate(lambda: self.service.edit_shot(sid,raw,shot.color))

    def move_plane(self,sid,x,y):
        raw = self.service.plane_to_raw([x,-y])
        shot = next(s for s in self.service.session.shots if s.shot_id==sid)
        self.mutate(lambda: self.service.edit_shot(sid,raw,shot.color))

    def edit_point(self):
        shot = next((s for s in self.service.session.shots if s.shot_id==self.selected_id),None)
        if shot is None:
            raise ValueError("请在表格或视图中选择一个点")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"编辑 Shot {shot.shot_id}（原始图像像素）")
        form = QFormLayout(dialog)
        coords = []
        for name,value,limit in zip(("u / px","v / px"),shot.pixel_raw,self.service.camera.image_size):
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(0,limit-.0001)
            spin.setSingleStep(.1)
            spin.setValue(value)
            form.addRow(name,spin)
            coords.append(spin)
        color = QComboBox()
        color.addItems(["red","blue"])
        color.setCurrentText(shot.color)
        form.addRow("颜色",color)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mutate(lambda: self.service.edit_shot(shot.shot_id,[s.value() for s in coords],color.currentText()))

    def clear_round(self):
        if QMessageBox.question(self,"清空本轮","清空所有 Shot？可以撤销。") == QMessageBox.StandardButton.Yes:
            self.mutate(self.service.clear)

    def new_round(self):
        if self.discard_ok():
            self.service.new_round()
            self.selected_id = None
            self.reset_validation()
            self.dirty = False
            self.refresh()

    def save_session(self):
        if not self.service.mapper:
            raise ValueError("请先完成平面标定，再保存可恢复的 Session")
        p = self.choose_save("保存 Session","session.json","JSON (*.json)")
        if p:
            self.service.save_session(p)
            self.dirty = False

    def load_session(self):
        if not self.discard_ok():
            return
        p = self.choose_open("加载 Session","JSON (*.json)")
        if p:
            self.service.load_session(p)
            self.selected_id = None
            self.reset_validation()
            self.sync_flags()
            self.dirty = False
            self.refresh(image_changed=True,fit=True)

    def export_csv(self):
        p = self.choose_save("导出点选 CSV","shots.csv","CSV (*.csv)")
        if p:
            export_csv(p,self.service.session)

    def export_json(self):
        p = self.choose_save("导出完整 JSON","shots.json","JSON (*.json)")
        if p:
            self.service.save_session(p)

    def screenshot(self):
        p = self.choose_save("导出 UI 截图","plane_picker.png","PNG (*.png)")
        if p and not self.grab().save(p,"PNG"):
            raise ValueError("截图保存失败")

    def load_validation(self):
        if not self.service.mapper:
            raise ValueError("请先完成平面标定")
        p = self.choose_open("加载独立验证点","CSV (*.csv)")
        if p:
            self.validation = MappingValidator.load(p,self.service.session.calibration_id)
            self.validation_active = True
            self.refresh(fit=True)

    def undo_validation(self):
        if self.validation and self.validation.results:
            self.validation.results.pop()
            self.validation_active = True
            self.refresh()

    def end_validation(self):
        self.validation_active = False
        self.refresh()

    def export_validation(self):
        if not self.validation or not self.validation.results:
            raise ValueError("尚无独立验证结果")
        p = self.choose_save("同时导出 CSV 和 JSON 验证报告","validation_report.json","JSON (*.json)")
        if p:
            self.validation.export(p)

    def fit_views(self):
        self.image_view.fit_all()
        self.plane_view.fit_all()

    def refresh(self,image_changed=False,fit=False):
        s = self.service
        und = self.und_action.isChecked()
        if image_changed:
            self.image_view.set_image(s.display_image(und))
        self.image_view.clear_overlays()
        detections = s.display_detections(und)
        self.image_view.draw_tags(detections,s.mapper,False)
        self.image_view.draw_shots(s.session.shots,und,self.selected_id)
        self.plane_view.draw(s.layout,s.mapper,s.session.shots,self.selected_id,self.grid_mm,self.validation)
        self.table.update_shots(s.session.shots,self.selected_id)
        warning = "⚠ 零畸变 / 示例内参调试模式，不可作为正式测量" if s.camera and not s.camera.intrinsics_valid else ""
        if not s.camera:
            warning = "⚠ 未加载相机内参，禁止正式标定"
        if s.mapper:
            c = s.mapper.calibration
            info = f"H 有效 · Tag {len(detections)} · 角点 {len(c.inlier_mask)} · 内点 {c.inlier_count} · RMS {c.image_reprojection_rms_px:.3f} px / {c.plane_mapping_rms_mm:.3f} mm"
            info += f" · {Path(s.session.calibration_file).name if s.session.calibration_file else '标定尚未单独保存'}"
        else:
            info = f"H 无效 / 尚未标定 · 检测 Tag {len(detections)} · 角点 {len(detections)*4}"
        if self.validation_active and self.validation and self.validation.next_target:
            t = self.validation.next_target
            info += f" · 验证模式：请点选 {t['point_id']} ({t['x_mm']}, {t['y_mm']}) mm"
        elif self.validation and self.validation.results:
            stats = self.validation.report()["statistics"]
            info += f" · 独立验证 {len(self.validation.results)}/{len(self.validation.targets)} 点：RMS {stats['rms_mm']:.3f} mm，P95 {stats['p95_mm']:.3f} mm"
        self.banner.setText(" · ".join(filter(None,[warning,info,"仅适用于 AprilTag 所在 Z=0 平面"])))
        self._status_summary = info
        self.statusBar().showMessage(info)
        if fit:
            self.fit_views()

    def hover(self,x,y):
        if self.busy or self.service.image is None:
            return
        try:
            raw,und,xy = self.service.pixel_info([x,y],self.und_action.isChecked())
            text = f"原始 ({raw[0]:.2f}, {raw[1]:.2f}) px"
            if und is not None:
                text += f" | 去畸变 ({und[0]:.2f}, {und[1]:.2f}) px"
            if xy is not None:
                text += f" | 平面 ({xy[0]:.2f}, {xy[1]:.2f}) mm | {'有效区域内' if self.service.mapper.contains(xy) else '有效区域外'}"
            self.statusBar().showMessage(text + " | " + self._status_summary)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc) + " | " + self._status_summary)

    def closeEvent(self,event):
        if self.busy:
            QMessageBox.information(self,"任务正在运行","请等待后台任务完成后关闭。")
            event.ignore()
        elif self.discard_ok():
            event.accept()
        else:
            event.ignore()
