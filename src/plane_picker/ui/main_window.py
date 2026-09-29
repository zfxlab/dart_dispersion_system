from pathlib import Path
import traceback
import numpy as np

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QSplitter, QTabWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QFormLayout, QDoubleSpinBox,
    QPushButton, QWidget, QSizePolicy,
)

from ..measurement_batch import MeasurementBatch
from ..models.shot import ShotRecord
from ..project import ProjectPaths
from ..service import PickerService
from ..camera.file_source import FileCameraSource
from ..trajectory import find_active_overlay, process_project
from .dispersion_view import DispersionView, PALETTE, rotate_points
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
        camera_config = config.get("camera", {})
        if not isinstance(camera_config, dict):
            raise ValueError("camera 配置必须是 YAML mapping")
        camera_id = camera_config.get("camera_id", "cam_left")
        if not isinstance(camera_id, str) or not camera_id:
            raise ValueError("camera.camera_id 必须是非空字符串")
        self._camera_id = camera_id
        self._ransac_threshold_mm = config.get("ransac_threshold_mm", 3.0)
        self._ransac_threshold_px = config.get("ransac_threshold_px", 3.0)
        self._id_allocator = id_allocator
        self.service = self._new_service()
        self.grid_mm = float(config.get("grid_mm", 100.0))
        if not 0 < self.grid_mm < 1e9:
            raise ValueError("grid_mm 必须为有限正数且小于 1e9")
        self.default_impact_radius_mm = float(config.get("impact_radius_mm", 10.0))
        self.default_result_rotation_deg = float(config.get("result_rotation_deg", 0.0))
        if not np.isfinite(self.default_impact_radius_mm) or self.default_impact_radius_mm <= 0:
            raise ValueError("impact_radius_mm 必须是有限正数")
        if (
            not np.isfinite(self.default_result_rotation_deg)
            or not -180 <= self.default_result_rotation_deg <= 180
        ):
            raise ValueError("result_rotation_deg 必须在 -180 到 180 度之间")
        self.selected_id = None
        self.color = "red"
        self.validation = None
        self.busy = False
        self.dirty = False
        self.workflow_stage = "calibration"
        self.batch = None
        self.current_measurement = None
        self._tree_items = {}
        self._worker = None
        self._calibration_image = None
        self._calibration_detections = []
        self._selection_preview = None
        self._selection_preview_path = None
        self._preview_warning = ""
        self._trajectory_run_id = ""
        self._result_items = {}
        self._selected_result_object = None
        self.project = None
        self.pool = QThreadPool(self)

        self.setWindowTitle("飞行物平面标定与散布分析")
        self.resize(1500, 920)
        self._build_pages()
        self._build_actions()
        self._connect_views()
        self.refresh(image_changed=True)

    def _toolbar(self, title):
        toolbar = QToolBar(title)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.toolbars.append(toolbar)
        return toolbar

    def _action(self, toolbar, text, handler, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda _=False: self.safe(handler))
        toolbar.addAction(action)
        return action

    def _panel(self, title, view):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(title))
        layout.addWidget(view)
        return panel

    def _build_pages(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.project_corner = QWidget()
        project_layout = QHBoxLayout(self.project_corner)
        project_layout.setContentsMargins(4, 0, 4, 0)
        project_layout.setSpacing(6)
        self.project_label = QLabel("项目：未选择")
        self.project_label.setMaximumWidth(360)
        self.project_button = QPushButton("选择项目")
        self.project_button.clicked.connect(lambda: self.safe(self.select_project))
        project_layout.addWidget(self.project_label)
        project_layout.addWidget(self.project_button)
        self.tabs.setCornerWidget(self.project_corner, Qt.Corner.TopRightCorner)
        self.setCentralWidget(self.tabs)

        self.calibration_page = QWidget()
        calibration_layout = QVBoxLayout(self.calibration_page)
        self.calibration_toolbar = QToolBar("① 标定")
        calibration_layout.addWidget(self.calibration_toolbar)
        self.calibration_banner = QLabel()
        self.calibration_banner.setWordWrap(True)
        self.calibration_banner.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum,
        )
        calibration_layout.addWidget(self.calibration_banner)
        self.calibration_image_view = ImageView()
        self.calibration_plane_view = PlaneView()
        calibration_split = QSplitter()
        calibration_split.addWidget(self._panel(
            "AprilTag 标定图片 · 绿色边界为有效区，绿色角点为内点，橙色为外点",
            self.calibration_image_view,
        ))
        calibration_split.addWidget(self._panel(
            "平面预览 · 灰色为布局参考 Tag，绿色为有效区域",
            self.calibration_plane_view,
        ))
        calibration_split.setSizes([900, 550])
        calibration_layout.addWidget(calibration_split)
        self.tabs.addTab(self.calibration_page, "1  AprilTag 标定")

        self.picking_page = QWidget()
        picking_layout = QVBoxLayout(self.picking_page)
        self.picking_toolbar = QToolBar("② 图片选点")
        picking_layout.addWidget(self.picking_toolbar)
        self.picking_banner = QLabel()
        self.picking_banner.setWordWrap(True)
        self.picking_banner.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum,
        )
        picking_layout.addWidget(self.picking_banner)

        self.measurement_tree = QTreeWidget()
        self.measurement_tree.setHeaderLabels(["飞行物 / 测量", "状态"])
        self.measurement_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.measurement_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.measurement_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.measurement_tree.itemSelectionChanged.connect(self._tree_selection_changed)

        self.image_view = ImageView()
        self.plane_view = PlaneView()
        self.plane_view.set_point_editing_enabled(False)
        self.plane_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.plane_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.table = ShotTable()
        self.table.setParent(self.picking_page)
        self.table.hide()

        self.picking_sidebar = QSplitter(Qt.Orientation.Vertical)
        self.picking_sidebar.addWidget(self._panel(
            "测量文件夹 · 选择需要处理的图片",
            self.measurement_tree,
        ))
        self.picking_sidebar.addWidget(self._panel(
            "布局坐标预览 · 红色当前点 / 蓝色其他测量",
            self.plane_view,
        ))
        self.picking_sidebar.setSizes([430, 330])
        self.picking_sidebar.setMinimumWidth(280)

        self.picking_splitter = QSplitter()
        self.picking_splitter.addWidget(self.picking_sidebar)
        self.picking_splitter.addWidget(self._panel(
            "待选点图片 · 左键选点 / 拖动点位 / 拖动空白处平移",
            self.image_view,
        ))
        self.picking_splitter.setSizes([360, 1140])
        self.picking_splitter.setStretchFactor(0, 0)
        self.picking_splitter.setStretchFactor(1, 1)
        picking_layout.addWidget(self.picking_splitter)
        self.tabs.addTab(self.picking_page, "2  图片选点")

        self.results_page = QWidget()
        results_layout = QVBoxLayout(self.results_page)
        self.results_toolbar = QToolBar("③ 散布结果")
        results_layout.addWidget(self.results_toolbar)
        self.results_banner = QLabel()
        self.results_banner.setWordWrap(True)
        self.results_banner.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum,
        )
        results_layout.addWidget(self.results_banner)
        self.dispersion_view = DispersionView()
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["飞镖", "完成", "散布范围 / mm"])
        self.result_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.result_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.result_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.result_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        select_buttons = QWidget()
        select_layout = QHBoxLayout(select_buttons)
        select_layout.setContentsMargins(0, 0, 0, 0)
        self.results_all_button = QPushButton("全选")
        self.results_none_button = QPushButton("全不选")
        self.results_only_button = QPushButton("仅当前")
        select_layout.addWidget(self.results_all_button)
        select_layout.addWidget(self.results_none_button)
        select_layout.addWidget(self.results_only_button)

        settings = QWidget()
        settings_layout = QFormLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.result_rotation_spin = QDoubleSpinBox()
        self.result_rotation_spin.setRange(-180.0, 180.0)
        self.result_rotation_spin.setDecimals(1)
        self.result_rotation_spin.setSingleStep(1.0)
        self.result_rotation_spin.setSuffix(" °")
        self.result_rotation_spin.setValue(self.default_result_rotation_deg)
        self.result_rotation_spin.setToolTip("逆时针为正；只旋转结果坐标，不修改标定数据")
        self.impact_radius_spin = QDoubleSpinBox()
        self.impact_radius_spin.setRange(0.1, 1000.0)
        self.impact_radius_spin.setDecimals(1)
        self.impact_radius_spin.setSingleStep(1.0)
        self.impact_radius_spin.setSuffix(" mm")
        self.impact_radius_spin.setValue(self.default_impact_radius_mm)
        settings_layout.addRow("结果旋转（逆时针）", self.result_rotation_spin)
        settings_layout.addRow("落点圆半径", self.impact_radius_spin)

        self.result_region_label = QLabel("当前飞镖：未选择")
        self.result_region_label.setWordWrap(True)
        self.draw_region_button = QPushButton("框选当前飞镖散布范围")
        self.draw_region_button.setCheckable(True)
        self.clear_region_button = QPushButton("清除当前范围")

        results_sidebar = QWidget()
        sidebar_layout = QVBoxLayout(results_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(QLabel("选择需要显示的飞镖"))
        sidebar_layout.addWidget(self.result_tree, 1)
        sidebar_layout.addWidget(select_buttons)
        sidebar_layout.addWidget(QLabel("结果坐标与落点范围"))
        sidebar_layout.addWidget(settings)
        sidebar_layout.addWidget(self.result_region_label)
        sidebar_layout.addWidget(self.draw_region_button)
        sidebar_layout.addWidget(self.clear_region_button)

        self.results_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.results_splitter.addWidget(results_sidebar)
        self.results_splitter.addWidget(self._panel(
            "旋转后的散布结果 · 圆为落点范围 / 矩形为手动散布范围",
            self.dispersion_view,
        ))
        self.results_splitter.setSizes([330, 1170])
        self.results_splitter.setStretchFactor(0, 0)
        self.results_splitter.setStretchFactor(1, 1)
        results_split = self.results_splitter
        results_layout.addWidget(results_split)
        self.tabs.addTab(self.results_page, "3  散布结果")

    def _build_actions(self):
        self.toolbars = [
            self.calibration_toolbar, self.picking_toolbar, self.results_toolbar,
        ]
        self.open_calibration_action = self._action(
            self.calibration_toolbar, "1  导入标定照片", self.open_image, "Ctrl+O",
        )
        self.camera_action = self._action(
            self.calibration_toolbar, "2  导入相机内参", self.load_camera,
        )
        self.layout_action = self._action(
            self.calibration_toolbar, "3  导入 Tag 布局", self.load_layout,
        )
        self.calibrate_action = self._action(
            self.calibration_toolbar, "4  检测并完成标定", self.calibrate,
        )
        self.load_calibration_action = self._action(
            self.calibration_toolbar, "导入已有标定", self.load_calibration,
        )
        self.save_calibration_action = self._action(
            self.calibration_toolbar, "保存标定", self.save_calibration,
        )

        self.open_selection_action = self._action(
            self.picking_toolbar, "加载项目测量", self.open_measurement_folder, "Ctrl+Shift+O",
        )
        self.refresh_folder_action = self._action(
            self.picking_toolbar, "刷新文件夹", self.refresh_measurement_folder,
        )
        self.next_action = self._action(
            self.picking_toolbar, "下一个未完成", self.next_measurement,
        )
        self.undo_action = self._action(
            self.picking_toolbar, "撤销", self.undo_point, "Ctrl+Z",
        )
        self.delete_action = self._action(
            self.picking_toolbar, "删除当前点", self.delete_current_point, "Delete",
        )
        self.fit_action = self._action(
            self.picking_toolbar, "适配两视图", self.fit_views, "F",
        )
        self.save_results_action = self._action(
            self.picking_toolbar, "保存 results.json", self.save_results, "Ctrl+S",
        )
        self.process_trajectory_action = self._action(
            self.picking_toolbar, "批量增强轨迹", self.process_trajectories,
        )
        self.enhanced_display_action = QAction("显示增强图", self)
        self.enhanced_display_action.setCheckable(True)
        self.enhanced_display_action.setChecked(True)
        self.enhanced_display_action.triggered.connect(
            lambda _=False: self.safe(self.toggle_selection_preview)
        )
        self.picking_toolbar.addAction(self.enhanced_display_action)
        self.results_refresh_action = self._action(
            self.results_toolbar, "刷新散布结果", self.update_results,
        )
        self.results_fit_action = self._action(
            self.results_toolbar, "适配散布图", self.dispersion_view.fit_all,
        )

        # Kept for the coordinate adapter and backwards-compatible tests.
        self.debug_action = QAction(self)
        self.debug_action.setCheckable(True)
        self.und_action = QAction(self)
        self.und_action.setCheckable(True)

    def _connect_views(self):
        self.image_view.clicked.connect(lambda x, y: self.safe(lambda: self.add_point(x, y)))
        self.image_view.hovered.connect(self.hover)
        self.image_view.moved.connect(
            lambda shot_id, x, y: self.safe(lambda: self.move_point(shot_id, x, y))
        )
        self.image_view.selected.connect(self.select)
        self.table.selected.connect(self.select)
        self.result_tree.itemChanged.connect(self._result_visibility_changed)
        self.result_tree.itemSelectionChanged.connect(self._result_selection_changed)
        self.results_all_button.clicked.connect(lambda: self._set_result_visibility(True))
        self.results_none_button.clicked.connect(lambda: self._set_result_visibility(False))
        self.results_only_button.clicked.connect(self._show_only_current_result)
        self.result_rotation_spin.valueChanged.connect(self._result_settings_changed)
        self.impact_radius_spin.valueChanged.connect(self._result_settings_changed)
        self.draw_region_button.toggled.connect(self._toggle_region_drawing)
        self.clear_region_button.clicked.connect(
            lambda: self.safe(self._clear_current_dispersion_region)
        )
        self.dispersion_view.regionDrawn.connect(
            lambda x, y, width, height: self.safe(
                lambda: self._accept_dispersion_region(x, y, width, height)
            )
        )

    def safe(self, function):
        if self.busy:
            return
        try:
            function()
        except Exception as exc:
            QMessageBox.warning(self, "操作未完成", str(exc))

    def choose_open(self, title, filters):
        return QFileDialog.getOpenFileName(self, title, "", filters)[0]

    def choose_save(self, title, name, filters):
        return QFileDialog.getSaveFileName(self, title, name, filters)[0]

    def choose_directory(self, title):
        return QFileDialog.getExistingDirectory(self, title, "")

    def _new_service(self):
        return PickerService(
            self._camera_id, self._ransac_threshold_mm,
            self._ransac_threshold_px, self._id_allocator,
        )

    def _require_project(self):
        if self.project is None:
            raise ValueError("请先在页面标签栏右侧选择项目文件夹")
        return self.project

    def select_project(self):
        root = self.choose_directory("选择或创建飞镖项目文件夹")
        if root:
            self.open_project(root)

    def open_project(self, root, load_measurements=True):
        if self.batch is not None:
            self.batch.save(self.service.session.calibration_id)
        project = ProjectPaths(root)
        project.ensure()
        service = self._new_service()
        errors = []
        if project.calibration_image.is_file():
            try:
                service.open_image(project.calibration_image)
            except Exception as exc:
                errors.append(f"标定照片：{exc}")
        if project.camera_intrinsics.is_file():
            try:
                service.load_camera(project.camera_intrinsics)
            except Exception as exc:
                errors.append(f"相机内参：{exc}")
        if project.tag_layout.is_file():
            try:
                service.load_layout(project.tag_layout)
            except Exception as exc:
                errors.append(f"Tag 布局：{exc}")
        dependencies = (
            service.image is not None and service.camera is not None
            and service.layout is not None
        )
        if project.plane_calibration.is_file() and dependencies:
            try:
                service.load_calibration(project.plane_calibration)
            except Exception as exc:
                errors.append(f"平面标定：{exc}")

        self.project = project
        self.service = service
        self.project_label.setText(f"项目：{project.root.name}")
        self.project_label.setToolTip(str(project.root))
        self._reset_batch()
        self.selected_id = None
        self._calibration_image = (
            service.image.copy() if service.image is not None else None
        )
        self._calibration_detections = []
        self.workflow_stage = "calibrated" if service.mapper else "calibration"
        self.refresh(image_changed=True, fit=True)

        if load_measurements and service.mapper is not None:
            try:
                MeasurementBatch.scan(project.root)
            except ValueError as exc:
                if "未找到" not in str(exc):
                    errors.append(f"测量目录：{exc}")
            else:
                try:
                    self._load_batch(project.root)
                except Exception as exc:
                    errors.append(f"测量结果：{exc}")
        if errors:
            raise ValueError("项目部分内容未能加载：\n" + "\n".join(errors))

    def _confirm_dependency_import(self, label, target):
        project = self._require_project()
        if not target.exists() and not project.plane_calibration.exists():
            return True
        answer = QMessageBox.question(
            self, f"替换{label}",
            f"替换{label}会使当前平面标定失效。继续后，旧平面标定将移入 "
            "_calibration/_history。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        project.archive_plane_calibration()
        return True

    def _reset_batch(self):
        self.batch = None
        self.current_measurement = None
        self._selection_preview = None
        self._selection_preview_path = None
        self._preview_warning = ""
        self._trajectory_run_id = ""
        self._tree_items = {}
        self.measurement_tree.clear()

    def open_image(self):
        project = self._require_project()
        path = self.choose_open(
            "导入 AprilTag 标定照片到当前项目",
            "图像 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*)",
        )
        if path:
            FileCameraSource(path, self._camera_id).read()
            if Path(path).resolve() == project.calibration_image.resolve():
                self.open_project(project.root, load_measurements=False)
                return
            if self._confirm_dependency_import("标定照片", project.calibration_image):
                project.import_calibration_image(path)
                self.open_project(project.root, load_measurements=False)

    def load_camera(self):
        project = self._require_project()
        path = self.choose_open("导入相机内参到当前项目", "YAML (*.yaml *.yml)")
        if path:
            from ..calibration.camera_model import CameraModel
            CameraModel.load(path)
            if Path(path).resolve() == project.camera_intrinsics.resolve():
                self.open_project(project.root, load_measurements=False)
                return
            if self._confirm_dependency_import("相机内参", project.camera_intrinsics):
                project.import_camera_intrinsics(path)
                self.open_project(project.root, load_measurements=False)

    def load_layout(self):
        project = self._require_project()
        path = self.choose_open("导入 Tag 布局到当前项目", "YAML (*.yaml *.yml)")
        if path:
            from ..calibration.tag_layout import TagLayout
            TagLayout.load(path)
            if Path(path).resolve() == project.tag_layout.resolve():
                self.open_project(project.root, load_measurements=False)
                return
            if self._confirm_dependency_import("Tag 布局", project.tag_layout):
                project.import_tag_layout(path)
                self.open_project(project.root, load_measurements=False)

    def calibrate(self):
        def accept(result):
            detections, mapper = result
            self.service.detections = detections
            self.service.accept_mapping(mapper)
            self._calibration_image = self.service.image.copy()
            self._calibration_detections = list(detections)
            self.workflow_stage = "calibrated"
            if self.project is not None:
                self.service.save_calibration(self.project.plane_calibration)
            self.refresh(fit=True)

        self.background(self.service.calibrate, accept)

    def load_calibration(self):
        project = self._require_project()
        if self.service.image is None or self.service.layout is None:
            raise ValueError("导入平面标定前，项目必须已有标定照片和 Tag 布局")
        path = self.choose_open("导入已有平面标定到当前项目", "YAML (*.yaml *.yml)")
        if path:
            # Full dependency validation occurs before the canonical file changes.
            self.service.load_calibration(path)
            if Path(path).resolve() == project.plane_calibration.resolve():
                self.open_project(project.root)
                return
            if project.plane_calibration.exists():
                answer = QMessageBox.question(
                    self, "替换平面标定", "当前项目已有平面标定，是否归档并替换？",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.open_project(project.root, load_measurements=False)
                    return
                project.archive_plane_calibration()
            project.import_plane_calibration(path)
            self.open_project(project.root)

    def save_calibration(self):
        project = self._require_project()
        self.service.save_calibration(project.plane_calibration)
        self.statusBar().showMessage(f"已保存 {project.plane_calibration}")
        self.refresh()

    def open_measurement_folder(self):
        project = self._require_project()
        if self.service.mapper is None:
            raise ValueError("请先在第一个页面完成 AprilTag 标定")
        self._load_batch(project.root)

    def _load_batch(self, root, preferred_key=None):
        had_results = (Path(root) / "results.json").is_file()
        batch = MeasurementBatch.load(root, self.service.session.calibration_id)
        if not had_results or not batch.has_result_view:
            batch.impact_radius_mm = self.default_impact_radius_mm
            batch.result_rotation_deg = self.default_result_rotation_deg
        for measurement in batch.measurements:
            if not measurement.complete:
                continue
            undistorted, plane = self.service.mapper.map_raw(measurement.pixel_raw)
            if (
                not np.allclose(undistorted, measurement.pixel_undistorted, atol=1e-4, rtol=0)
                or not np.allclose(plane, measurement.plane_mm, atol=1e-4, rtol=0)
            ):
                raise ValueError(
                    f"results.json 中 {measurement.object_id}/{measurement.trial} "
                    "的坐标与当前标定不一致"
                )
        self.service.session.shots = []
        self.service.history.clear()
        self.batch = batch
        self.current_measurement = None
        self.selected_id = None
        self.populate_measurement_tree()
        measurement = batch.get(preferred_key) if preferred_key else None
        measurement = measurement or batch.next_incomplete() or batch.measurements[0]
        self.select_measurement(measurement)
        self.tabs.setCurrentWidget(self.picking_page)

    def refresh_measurement_folder(self):
        if self.batch is None:
            raise ValueError("请先打开测量文件夹")
        self.save_results()
        key = self.current_measurement.key if self.current_measurement else None
        self._load_batch(self.batch.root, key)

    def populate_measurement_tree(self):
        self.measurement_tree.blockSignals(True)
        self.measurement_tree.clear()
        self._tree_items = {}
        if self.batch is not None:
            for object_id in self.batch.objects:
                done, total = self.batch.progress(object_id)
                parent = QTreeWidgetItem([object_id, f"{done}/{total}"])
                self.measurement_tree.addTopLevelItem(parent)
                for measurement in [
                    item for item in self.batch.measurements if item.object_id == object_id
                ]:
                    status = "✓ 已完成" if measurement.complete else "○ 待选点"
                    child = QTreeWidgetItem([f"第 {measurement.trial} 次", status])
                    child.setData(0, Qt.ItemDataRole.UserRole, measurement.key)
                    parent.addChild(child)
                    self._tree_items[measurement.key] = child
                parent.setExpanded(True)
        self.measurement_tree.blockSignals(False)

    def _tree_selection_changed(self):
        items = self.measurement_tree.selectedItems()
        if not items:
            return
        key = items[0].data(0, Qt.ItemDataRole.UserRole)
        if key and self.batch:
            measurement = self.batch.get(tuple(key))
            if measurement is not None and measurement is not self.current_measurement:
                self.safe(lambda: self.select_measurement(measurement))

    def _shot_for_measurement(self, measurement, shot_id, color="red"):
        return ShotRecord(
            shot_id, f"{measurement.object_id}-{measurement.trial}", color,
            self.service.session.camera_id, measurement.image_file,
            measurement.pixel_raw, measurement.pixel_undistorted,
            measurement.plane_mm, True, self.service.session.calibration_id,
            measurement.timestamp,
        )

    def select_measurement(self, measurement):
        if self.service.mapper is None:
            raise ValueError("当前没有有效标定")
        self.service.session.shots = []
        self.service.history.clear()
        self.service.open_selection_image(measurement.image_file)
        self.current_measurement = measurement
        self._load_selection_preview(measurement)
        self.workflow_stage = "picking"
        self.selected_id = None
        if measurement.complete:
            _, mapped = self.service.mapping(measurement.pixel_raw)
            if not np.allclose(mapped, measurement.plane_mm, atol=1e-4, rtol=0):
                raise ValueError(f"{measurement.object_id}/{measurement.trial} 的结果与当前标定不一致")
            shot = self._shot_for_measurement(measurement, 1)
            self.service.session.shots = [shot]
            self.service.session.next_shot_id = 2
            self.selected_id = shot.shot_id
        item = self._tree_items.get(measurement.key)
        if item is not None:
            self.measurement_tree.blockSignals(True)
            self.measurement_tree.setCurrentItem(item)
            self.measurement_tree.blockSignals(False)
        self.refresh(image_changed=True, fit=True)

    def _load_selection_preview(self, measurement):
        self._selection_preview = None
        self._selection_preview_path = None
        self._preview_warning = ""
        if self.batch is None:
            return
        path = find_active_overlay(
            self.batch.root, measurement.object_id, measurement.trial,
        )
        if path is None:
            return
        try:
            preview = FileCameraSource(path, self.service.session.camera_id).read()
            if preview.shape != self.service.image.shape:
                raise ValueError("增强图尺寸与原图不一致")
        except Exception as exc:
            self._preview_warning = str(exc)
            return
        self._selection_preview = preview
        self._selection_preview_path = path

    def toggle_selection_preview(self):
        self.refresh(image_changed=True, fit=True)

    def process_trajectories(self):
        if self.batch is None:
            raise ValueError("请先打开包含 dart_1、dart_2… 的项目文件夹")
        root = self.batch.root

        def accept(manifest):
            self._trajectory_run_id = manifest["run_id"]
            if self.current_measurement is not None:
                self._load_selection_preview(self.current_measurement)
            self.refresh(image_changed=True, fit=True)
            QMessageBox.information(
                self, "轨迹增强完成",
                f"运行 {manifest['run_id']}\n"
                f"成功 {manifest['completed']} 张，失败 {manifest['failed']} 张。",
            )

        self.background(
            lambda: process_project(root), accept,
            self.picking_banner, "正在批量增强长曝光轨迹，请稍候…",
        )

    def next_measurement(self):
        if self.batch is None:
            raise ValueError("请先打开测量文件夹")
        measurement = self.batch.next_incomplete(self.current_measurement)
        if measurement is None:
            QMessageBox.information(self, "选点完成", "所有图片都已经完成选点。")
            self.tabs.setCurrentWidget(self.results_page)
            return
        self.select_measurement(measurement)

    def _sync_current_result(self):
        if self.batch is None or self.current_measurement is None:
            return
        if self.service.session.shots:
            shot = self.service.session.shots[0]
            self.batch.set_result(
                self.current_measurement.key, shot.pixel_raw,
                shot.pixel_undistorted, shot.plane_mm, shot.timestamp,
            )
        else:
            self.batch.clear_result(self.current_measurement.key)
        self.batch.save(self.service.session.calibration_id)
        self.populate_measurement_tree()
        item = self._tree_items.get(self.current_measurement.key)
        if item is not None:
            self.measurement_tree.setCurrentItem(item)

    def save_results(self):
        if self.batch is None:
            raise ValueError("尚未打开测量文件夹")
        self.batch.save(self.service.session.calibration_id)
        self.statusBar().showMessage(f"已保存 {self.batch.results_path}")

    def add_point(self, x, y):
        raw = self.service.display_to_raw([x, y], self.und_action.isChecked())
        if self.current_measurement is not None and self.service.session.shots:
            shot = self.service.session.shots[0]
            self.service.edit_shot(shot.shot_id, raw, "red")
        else:
            shot = self.service.add_shot(raw, self.color)
        self.selected_id = shot.shot_id
        self._sync_current_result()
        self.refresh()

    def select(self, shot_id):
        if not self.busy:
            self.selected_id = shot_id
            self.refresh()

    def mutate(self, function):
        function()
        self.dirty = True
        self._sync_current_result()
        self.refresh()

    def move_point(self, shot_id, x, y):
        raw = self.service.display_to_raw([x, y], self.und_action.isChecked())
        shot = next(shot for shot in self.service.session.shots if shot.shot_id == shot_id)
        self.mutate(lambda: self.service.edit_shot(shot_id, raw, shot.color))

    def move_plane(self, shot_id, x, y):
        raw = self.service.plane_to_raw([x, -y])
        shot = next(shot for shot in self.service.session.shots if shot.shot_id == shot_id)
        self.mutate(lambda: self.service.edit_shot(shot_id, raw, shot.color))

    def undo_point(self):
        self.service.undo()
        self._sync_current_result()
        self.refresh()

    def delete_current_point(self):
        if not self.service.session.shots:
            raise ValueError("当前图片尚未选点")
        self.service.delete_shot(self.service.session.shots[0].shot_id)
        self.selected_id = None
        self._sync_current_result()
        self.refresh()

    def clear_round(self):
        self.delete_current_point()

    def fit_views(self):
        self.image_view.fit_all()
        self.plane_view.fit_all()

    def toggle_display(self):
        if self.und_action.isChecked() and self.service.camera is None:
            self.und_action.setChecked(False)
            raise ValueError("请先加载相机内参")
        self.refresh(image_changed=True)

    def background(self, function, callback, status_label=None,
                   status_text="正在后台检测并计算标定，请稍候…"):
        self.busy = True
        for toolbar in self.toolbars:
            toolbar.setEnabled(False)
        (status_label or self.calibration_banner).setText(status_text)
        self._worker = Worker(function)

        def finish(result):
            self.busy = False
            for toolbar in self.toolbars:
                toolbar.setEnabled(True)
            self.safe(lambda: callback(result))

        def fail(message):
            self.busy = False
            for toolbar in self.toolbars:
                toolbar.setEnabled(True)
            self.refresh()
            QMessageBox.warning(self, "后台处理失败", message)

        self._worker.signals.done.connect(finish)
        self._worker.signals.failed.connect(fail)
        self.pool.start(self._worker)

    def _object_plane_shots(self):
        if self.batch is None or self.current_measurement is None:
            return self.service.session.shots
        shots = list(self.service.session.shots)
        next_id = max((shot.shot_id for shot in shots), default=0) + 1
        for measurement in self.batch.measurements:
            if (
                measurement.object_id == self.current_measurement.object_id
                and measurement.complete
                and measurement.key != self.current_measurement.key
            ):
                shots.append(self._shot_for_measurement(measurement, next_id, "blue"))
                next_id += 1
        return shots

    def _result_object_id(self):
        items = self.result_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _populate_result_tree(self):
        previous_checks = {
            object_id: item.checkState(0)
            for object_id, item in self._result_items.items()
        }
        previous_selection = self._selected_result_object
        completed = [m for m in (self.batch.measurements if self.batch else []) if m.complete]
        object_ids = sorted({m.object_id for m in completed}, key=str.casefold)
        colors = {
            object_id: PALETTE[index % len(PALETTE)]
            for index, object_id in enumerate(object_ids)
        }
        self.result_tree.blockSignals(True)
        self.result_tree.clear()
        self._result_items = {}
        for object_id in object_ids:
            count = sum(m.object_id == object_id for m in completed)
            region = self.batch.dispersion_regions.get(object_id)
            region_text = (
                f'{region["width_mm"]:.1f} × {region["height_mm"]:.1f}'
                if region else "未框选"
            )
            item = QTreeWidgetItem([object_id, str(count), region_text])
            item.setData(0, Qt.ItemDataRole.UserRole, object_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0, previous_checks.get(object_id, Qt.CheckState.Checked),
            )
            item.setForeground(0, QBrush(QColor(colors[object_id])))
            self.result_tree.addTopLevelItem(item)
            self._result_items[object_id] = item
        selected = previous_selection if previous_selection in self._result_items else None
        selected = selected or (object_ids[0] if object_ids else None)
        self._selected_result_object = selected
        if selected is not None:
            self.result_tree.setCurrentItem(self._result_items[selected])
        self.result_tree.blockSignals(False)
        self._update_result_region_label()

    def _visible_result_ids(self):
        return {
            object_id for object_id, item in self._result_items.items()
            if item.checkState(0) == Qt.CheckState.Checked
        }

    def _draw_results(self):
        measurements = self.batch.measurements if self.batch else []
        polygon = (
            self.service.mapper.polygon
            if self.service is not None and self.service.mapper is not None else None
        )
        self.dispersion_view.draw(
            measurements,
            self.grid_mm,
            self._visible_result_ids(),
            self.batch.impact_radius_mm if self.batch else self.default_impact_radius_mm,
            self.batch.result_rotation_deg if self.batch else self.default_result_rotation_deg,
            polygon,
            self.batch.dispersion_regions if self.batch else {},
            self._selected_result_object,
        )

    def _update_result_region_label(self):
        object_id = self._selected_result_object
        region = self.batch.dispersion_regions.get(object_id) if self.batch and object_id else None
        if object_id is None:
            self.result_region_label.setText("当前飞镖：未选择")
        elif region is None:
            self.result_region_label.setText(f"当前飞镖：{object_id}\n散布范围：未框选")
        else:
            self.result_region_label.setText(
                f"当前飞镖：{object_id}\n"
                f'散布范围：长 {region["width_mm"]:.1f} mm × '
                f'宽 {region["height_mm"]:.1f} mm'
            )

    def _result_visibility_changed(self, _item=None, _column=0):
        self._draw_results()

    def _result_selection_changed(self):
        self._selected_result_object = self._result_object_id()
        if self.draw_region_button.isChecked():
            self.draw_region_button.setChecked(False)
        self._update_result_region_label()
        self._draw_results()

    def _set_result_visibility(self, checked):
        self.result_tree.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._result_items.values():
            item.setCheckState(0, state)
        self.result_tree.blockSignals(False)
        self._draw_results()

    def _show_only_current_result(self):
        object_id = self._selected_result_object
        if object_id is None:
            return
        self.result_tree.blockSignals(True)
        for candidate, item in self._result_items.items():
            item.setCheckState(
                0, Qt.CheckState.Checked if candidate == object_id
                else Qt.CheckState.Unchecked,
            )
        self.result_tree.blockSignals(False)
        self._draw_results()

    def _result_settings_changed(self, _value=None):
        if self.batch is None:
            return
        if self.draw_region_button.isChecked():
            self.draw_region_button.setChecked(False)
        self.batch.result_rotation_deg = self.result_rotation_spin.value()
        self.batch.impact_radius_mm = self.impact_radius_spin.value()
        self.batch.save(self.service.session.calibration_id)
        self._draw_results()

    def _toggle_region_drawing(self, checked):
        if checked:
            if self._selected_result_object is None:
                self.draw_region_button.setChecked(False)
                return
            item = self._result_items.get(self._selected_result_object)
            if item is not None and item.checkState(0) != Qt.CheckState.Checked:
                item.setCheckState(0, Qt.CheckState.Checked)
            self.draw_region_button.setText("在右侧拖动框选（点击取消）")
            self.dispersion_view.start_region_selection()
        else:
            self.draw_region_button.setText("框选当前飞镖散布范围")
            self.dispersion_view.cancel_region_selection()

    def _accept_dispersion_region(self, x, y, width, height):
        if self.batch is None or self._selected_result_object is None:
            return
        center_plane = rotate_points(
            [[x, y]], -self.batch.result_rotation_deg,
        )[0]
        self.batch.set_dispersion_region(
            self._selected_result_object,
            center_plane,
            width,
            height,
            -self.batch.result_rotation_deg,
        )
        self.draw_region_button.setChecked(False)
        self.batch.save(self.service.session.calibration_id)
        self.update_results()

    def _clear_current_dispersion_region(self):
        if self.batch is None or self._selected_result_object is None:
            return
        self.batch.clear_dispersion_region(self._selected_result_object)
        self.batch.save(self.service.session.calibration_id)
        self.update_results()

    def update_results(self):
        if self.batch is not None:
            self.result_rotation_spin.blockSignals(True)
            self.impact_radius_spin.blockSignals(True)
            self.result_rotation_spin.setValue(self.batch.result_rotation_deg)
            self.impact_radius_spin.setValue(self.batch.impact_radius_mm)
            self.result_rotation_spin.blockSignals(False)
            self.impact_radius_spin.blockSignals(False)
        self._populate_result_tree()
        self._draw_results()
        measurements = self.batch.measurements if self.batch else []
        complete = sum(measurement.complete for measurement in measurements)
        visible = len(self._visible_result_ids())
        total = len(self._result_items)
        rotation = self.batch.result_rotation_deg if self.batch else self.default_result_rotation_deg
        self.results_banner.setText(
            f"已完成 {total} 个飞镖、{complete} 次测量；当前显示 {visible} 个。"
            f"结果坐标相对标定平面逆时针旋转 {rotation:.1f}°。"
        )

    def update_action_states(self):
        service = self.service
        has_project = self.project is not None
        has_mapping = service.mapper is not None
        has_shot = bool(service.session.shots)
        has_batch = self.batch is not None
        complete = bool(has_batch and any(m.complete for m in self.batch.measurements))
        self.open_calibration_action.setEnabled(not self.busy and has_project)
        self.camera_action.setEnabled(not self.busy and has_project)
        self.layout_action.setEnabled(not self.busy and has_project)
        self.calibrate_action.setEnabled(
            not self.busy and has_project
            and service.image is not None and service.camera is not None
            and service.layout is not None and self.workflow_stage == "calibration"
        )
        self.load_calibration_action.setEnabled(
            not self.busy and has_project
            and service.image is not None and service.layout is not None
            and not has_shot
        )
        self.save_calibration_action.setEnabled(
            not self.busy and has_project and has_mapping
        )
        self.open_selection_action.setEnabled(
            not self.busy and has_project and has_mapping
        )
        self.refresh_folder_action.setEnabled(not self.busy and has_batch)
        self.next_action.setEnabled(not self.busy and has_batch)
        self.undo_action.setEnabled(not self.busy and bool(service.history))
        self.delete_action.setEnabled(not self.busy and has_shot)
        self.save_results_action.setEnabled(not self.busy and has_batch)
        self.process_trajectory_action.setEnabled(
            not self.busy and has_project and has_batch
        )
        self.enhanced_display_action.setEnabled(
            not self.busy and self._selection_preview is not None
        )
        self.results_refresh_action.setEnabled(has_batch)
        self.result_rotation_spin.setEnabled(complete)
        self.impact_radius_spin.setEnabled(complete)
        self.draw_region_button.setEnabled(complete)
        self.clear_region_button.setEnabled(complete)
        self.results_all_button.setEnabled(complete)
        self.results_none_button.setEnabled(complete)
        self.results_only_button.setEnabled(complete)
        self.tabs.setTabEnabled(1, has_mapping)
        self.tabs.setTabEnabled(2, complete)

    def _refresh_calibration_page(self, fit=False):
        service = self.service
        image = self._calibration_image if self._calibration_image is not None else service.image
        self.calibration_image_view.set_image(image)
        detections = (
            self._calibration_detections
            or service.detections
            or service.display_detections()
        )
        self.calibration_image_view.draw_valid_region(service.mapper, False)
        self.calibration_image_view.draw_tags(detections, service.mapper, False)
        self.calibration_plane_view.draw(
            service.layout, service.mapper, [], None, self.grid_mm, None,
        )
        if service.mapper:
            calibration = service.mapper.calibration
            text = (
                f"标定有效 · 检测 Tag {len(detections)}（参与标定 "
                f"{len(calibration.detected_tag_ids)}） · "
                f"内点 {calibration.inlier_count}/{len(calibration.inlier_mask)} · "
                f"RMS {calibration.image_reprojection_rms_px:.3f} px / "
                f"{calibration.plane_mapping_rms_mm:.3f} mm"
            )
            color = "#173b32; color: #9ff0d0"
        else:
            missing = []
            if service.image is None:
                missing.append("标定照片")
            if service.camera is None:
                missing.append("相机内参")
            if service.layout is None:
                missing.append("Tag 布局")
            text = "准备标定" + (" · 还需 " + "、".join(missing) if missing else " · 可以开始检测")
            color = "#3e3420; color: #ffe39b"
        if self.project is None:
            text = "请先在页面标签栏右侧选择项目文件夹"
            color = "#3e3420; color: #ffe39b"
        else:
            status = self.project.status()
            text += (
                f" · 项目 {self.project.root.name} · "
                f"背景 {status['background_count']} 张"
            )
        self.calibration_banner.setText(text)
        self.calibration_banner.setStyleSheet(f"padding: 10px; background: {color};")
        if fit:
            self.calibration_image_view.fit_all()
            self.calibration_plane_view.fit_all()

    def refresh(self, image_changed=False, fit=False):
        service = self.service
        undistorted = self.und_action.isChecked()
        if image_changed:
            use_preview = (
                self.enhanced_display_action.isChecked()
                and self._selection_preview is not None
                and not undistorted
            )
            image = self._selection_preview if use_preview else service.display_image(undistorted)
            self.image_view.set_image(image)
        self.image_view.clear_overlays()
        if self.workflow_stage == "picking" or self.batch is None:
            self.image_view.draw_shots(
                service.session.shots, undistorted, self.selected_id,
            )
        plane_shots = self._object_plane_shots()
        self.plane_view.draw(
            service.layout, service.mapper, plane_shots,
            self.selected_id, self.grid_mm, None, compact=True,
        )
        self.table.update_shots(service.session.shots, self.selected_id)
        self._refresh_calibration_page(fit)

        if self.batch:
            done, total = self.batch.progress()
            current = (
                f"{self.current_measurement.object_id} / 第 {self.current_measurement.trial} 次"
                if self.current_measurement else "未选择图片"
            )
            self.picking_banner.setText(
                f"{self.batch.root} · 当前：{current} · 进度 {done}/{total} · "
                f"显示：{'增强图' if self.enhanced_display_action.isChecked() and self._selection_preview is not None else '原图'} · "
                "选点变化会自动保存到 results.json"
                + (f" · 警告：{self._preview_warning}" if self._preview_warning else "")
            )
            self.picking_banner.setStyleSheet(
                "padding: 10px; background: #173b32; color: #9ff0d0;"
            )
        else:
            self.picking_banner.setText(
                "完成标定后，打开测量根目录。一级文件夹名作为飞行物 ID，"
                "其中 1、2、3… 图片代表不同次测量。"
            )
            self.picking_banner.setStyleSheet(
                "padding: 10px; background: #20354f; color: #b9d8ff;"
            )
        self.update_results()
        self.update_action_states()
        if fit:
            self.fit_views()
        self._status_summary = self.picking_banner.text()
        self.statusBar().showMessage(self._status_summary)

    def hover(self, x, y):
        if self.busy or self.service.image is None:
            return
        try:
            raw, undistorted, plane = self.service.pixel_info(
                [x, y], self.und_action.isChecked(),
            )
            text = f"原始 ({raw[0]:.2f}, {raw[1]:.2f}) px"
            if undistorted is not None:
                text += f" | 去畸变 ({undistorted[0]:.2f}, {undistorted[1]:.2f}) px"
            if plane is not None:
                text += f" | 平面 ({plane[0]:.2f}, {plane[1]:.2f}) mm"
            self.statusBar().showMessage(text)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))

    def _tab_changed(self, index):
        if index == 1:
            # Fit after the tab has received its final visible geometry.
            QTimer.singleShot(0, self.fit_views)
        elif index == 2:
            self.update_results()
            # Wait until the horizontal splitter has received its visible size.
            QTimer.singleShot(0, self.dispersion_view.fit_all)

    def discard_ok(self):
        if self.batch is not None:
            self.batch.save(self.service.session.calibration_id)
            return True
        return not self.dirty or QMessageBox.question(
            self, "未保存的修改", "当前修改尚未保存。是否放弃这些修改？",
        ) == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "任务正在运行", "请等待后台任务完成后关闭。")
            event.ignore()
        elif self.discard_ok():
            event.accept()
        else:
            event.ignore()
