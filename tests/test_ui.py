import json
import threading
import time
import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from test_core import scene
from test_service import service
from plane_picker.ui.main_window import MainWindow
from plane_picker.ui.coordinate_transform import CoordinateTransform
from plane_picker.trajectory import TrajectoryConfig, process_project
from plane_picker.project import ProjectPaths
from plane_picker.storage.yaml_io import write_yaml


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, service, monkeypatch, tmp_path):
    w = MainWindow()
    w.service = service[0]
    w.project = ProjectPaths(tmp_path / "project_context")
    w.project.ensure()
    w.project_label.setText(f"项目：{w.project.root.name}")
    w.project_label.setToolTip(str(w.project.root))
    errors = []
    monkeypatch.setattr(QMessageBox,"warning",lambda *a: errors.append(a[-1]))
    w.show()
    app.processEvents()
    w.refresh(image_changed=True,fit=True)
    app.processEvents()
    yield w,errors
    w.dirty = False
    w.close()


def test_three_page_workflow_actions(window):
    w,_ = window
    assert [w.tabs.tabText(index) for index in range(w.tabs.count())] == [
        "1  AprilTag 标定", "2  图片选点", "3  散布结果",
    ]
    assert [bar.windowTitle() for bar in w.toolbars] == ["① 标定", "② 图片选点", "③ 散布结果"]
    labels = {action.text() for bar in w.toolbars for action in bar.actions()}
    assert {
        "1  导入标定照片", "4  检测并完成标定", "加载项目测量",
        "保存 results.json", "刷新散布结果",
    } <= labels
    assert w.tabs.cornerWidget(Qt.Corner.TopRightCorner) is w.project_corner
    assert w.project_button.text() == "选择项目"
    assert w.open_selection_action.isEnabled()
    assert w.tabs.isTabEnabled(1)
    assert not w.tabs.isTabEnabled(2)
    assert w.picking_splitter.count() == 2
    assert w.picking_sidebar.count() == 2
    assert w.picking_splitter.widget(0) is w.picking_sidebar
    assert w.results_splitter.orientation() == Qt.Orientation.Horizontal
    assert w.results_splitter.count() == 2
    assert w.table.isHidden()
    assert not w.plane_view._point_editing_enabled


def test_calibration_page_banner_and_saved_markers(window, app):
    w, _ = window
    app.processEvents()
    assert w.calibration_banner.height() < w.calibration_page.height() / 4
    assert "检测 Tag 4（参与标定 4）" in w.calibration_banner.text()
    # The fixture has a mapper and detections. Clear the live detections to
    # exercise the same reconstruction path used by "加载已有标定".
    w.service.detections = []
    w._calibration_detections = []
    w.refresh(fit=True)
    app.processEvents()
    assert "检测 Tag 4（参与标定 4）" in w.calibration_banner.text()
    # Image pixmap + valid polygon + 4 tag polygons + 4 tag labels +
    # 16 corner/label pairs.
    assert len(w.calibration_image_view.scene().items()) == 42


def test_project_auto_loads_fixed_calibration_files(window, tmp_path):
    w, errors = window
    source_service = w.service
    project = ProjectPaths(tmp_path / "automatic_project")
    project.ensure()
    project.import_calibration_image(source_service.session.image_file)
    camera_source = tmp_path / "camera.yaml"
    layout_source = tmp_path / "layout.yaml"
    plane_source = tmp_path / "plane.yaml"
    write_yaml(
        camera_source,
        source_service.camera.to_intrinsics_dict(include_validity=True),
    )
    write_yaml(layout_source, source_service.layout.to_dict())
    source_service.save_calibration(plane_source)
    project.import_camera_intrinsics(camera_source)
    project.import_tag_layout(layout_source)
    project.import_plane_calibration(plane_source)

    w.open_project(project.root)

    assert not errors
    assert w.project == project
    assert w.service.mapper is not None
    assert w.service.session.calibration_file == str(project.plane_calibration.resolve())
    assert w.project_label.text() == "项目：automatic_project"
    assert "标定有效" in w.calibration_banner.text()
    w.workflow_stage = "picking"
    w.refresh()
    assert "检测 Tag 4（参与标定 4）" in w.calibration_banner.text()


def test_folder_selection_autosaves_results(window,app,tmp_path,monkeypatch):
    w,errors = window
    root = tmp_path/"batch"
    object_dir = root/"dart_A"
    object_dir.mkdir(parents=True)
    cv2.imwrite(str(object_dir/"1.png"),np.full((1080,1440,3),160,np.uint8))
    cv2.imwrite(str(object_dir/"2.png"),np.full((1080,1440,3),120,np.uint8))
    w.project = ProjectPaths(root)

    w.open_measurement_folder()
    assert w.current_measurement.key == ("dart_A",1)
    assert w.measurement_tree.topLevelItemCount()==1
    raw = w.service.plane_to_raw([200,200])
    point = w.image_view.mapFromScene(QPointF(*raw))
    QTest.mouseClick(w.image_view.viewport(),Qt.MouseButton.LeftButton,pos=point)

    assert not errors
    assert w.batch.progress()==(1,2)
    data = json.loads((root/"results.json").read_text())
    assert data["measurements"][0]["object_id"]=="dart_A"
    assert data["measurements"][0]["trial"]==1
    assert w.tabs.isTabEnabled(2)

    w._accept_dispersion_region(200, 200, 60, 40)
    data = json.loads((root/"results.json").read_text())
    assert data["dispersion_regions"][0]["object_id"] == "dart_A"
    assert data["dispersion_regions"][0]["width_mm"] == pytest.approx(60)
    assert data["dispersion_regions"][0]["height_mm"] == pytest.approx(40)

    data["measurements"][0]["plane_mm"][0] += 50
    (root/"results.json").write_text(json.dumps(data))
    with pytest.raises(ValueError,match="坐标与当前标定不一致"):
        w._load_batch(root)
    w.batch.save(w.service.session.calibration_id)

    w.next_measurement()
    assert w.current_measurement.key == ("dart_A",2)


def test_picking_page_uses_active_trajectory_overlay(window, tmp_path, monkeypatch):
    w, errors = window
    root = tmp_path / "project"
    dart = root / "dart_1"
    background = root / "_background"
    dart.mkdir(parents=True)
    background.mkdir()
    clean = np.full((1080, 1440, 3), 100, np.uint8)
    exposed = clean.copy()
    cv2.line(exposed, (200, 800), (1100, 250), (230, 230, 230), 4)
    cv2.imwrite(str(background / "1.png"), clean)
    cv2.imwrite(str(dart / "1.png"), exposed)
    manifest = process_project(root, TrajectoryConfig(minimum_area_px=5))
    w.project = ProjectPaths(root)

    w.open_measurement_folder()

    assert not errors
    assert w._selection_preview is not None
    assert manifest["run_id"] in str(w._selection_preview_path)
    assert w.service.session.image_file == str((dart / "1.png").resolve())
    assert w.enhanced_display_action.isEnabled()
    assert "显示：增强图" in w.picking_banner.text()


def test_click_zoom_edit_undo_restore(window,app,tmp_path):
    w,errors = window
    raw = w.service.plane_to_raw([200,200])
    view = w.image_view
    point = view.mapFromScene(QPointF(*raw))
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    assert not errors
    assert len(w.service.session.shots)==1
    np.testing.assert_allclose(w.service.session.shots[0].plane_mm,[200,200],atol=3)
    assert w.table.rowCount()==1
    assert any(i.data(0)==1 for i in w.plane_view.scene().items())
    w.color = "blue"
    view.scale(1.8,1.8)
    view.horizontalScrollBar().setValue(80)
    raw2 = w.service.plane_to_raw([260,220])
    point2 = view.mapFromScene(QPointF(*raw2))
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point2)
    assert not errors
    assert len(w.service.session.shots)==2
    assert w.service.session.shots[-1].color=="blue"
    # Drag the selected point in the raw image; both views and stored coordinates update.
    target = view.mapFromScene(QPointF(*w.service.plane_to_raw([280,240])))
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=point2)
    QTest.mouseMove(view.viewport(),target)
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=target)
    assert not errors
    np.testing.assert_allclose(w.service.session.shots[-1].plane_mm,[280,240],atol=3)
    w.mutate(w.service.undo)
    np.testing.assert_allclose(w.service.session.shots[-1].plane_mm,[260,220],atol=3)
    w.und_action.setChecked(True)
    w.toggle_display()
    und = w.service.camera.undistort([w.service.plane_to_raw([320,300])])[0]
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=view.mapFromScene(QPointF(*und)))
    assert not errors
    np.testing.assert_allclose(w.service.session.shots[-1].plane_mm,[320,300],atol=3)
    w.service.save_session(tmp_path/"session.json")
    w.service.load_session(tmp_path/"session.json")
    w.refresh(image_changed=True,fit=True)
    assert w.table.rowCount()==3
    assert len([i for i in w.plane_view.scene().items() if i.data(0)==3])==1
    assert w.grab().save(str(tmp_path/"ui.png"))


def test_qt_coordinate_adapter(window):
    w,_ = window
    view = w.image_view
    view.scale(2.3,2.3)
    view.horizontalScrollBar().setValue(37)
    view.verticalScrollBar().setValue(53)
    tr = CoordinateTransform.from_qtransform(view.viewportTransform(),2.)
    p = [20.,30.]
    qt = view.mapToScene(QPoint(*map(int,p)))
    np.testing.assert_allclose(tr.view_to_image(p),[qt.x(),qt.y()],atol=1e-10)


def test_worker_runs_off_ui_thread(window,app):
    w,errors = window
    owner = threading.get_ident()
    completed = []
    w.background(lambda: threading.get_ident(), lambda worker_id: completed.append((worker_id,threading.get_ident())))
    deadline = time.monotonic()+5
    while w.busy and time.monotonic()<deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not w.busy
    assert completed[0][0] != owner
    assert completed[0][1] == owner
    assert not errors
