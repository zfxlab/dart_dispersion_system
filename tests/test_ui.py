import threading
import time
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from test_core import scene
from test_service import service
from plane_picker.ui.main_window import MainWindow
from plane_picker.ui.coordinate_transform import CoordinateTransform


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, service, monkeypatch):
    w = MainWindow()
    w.service = service[0]
    errors = []
    monkeypatch.setattr(QMessageBox,"warning",lambda *a: errors.append(a[-1]))
    w.show()
    app.processEvents()
    w.refresh(image_changed=True,fit=True)
    app.processEvents()
    yield w,errors
    w.dirty = False
    w.close()


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
    assert len([i for i in w.plane_view.scene().items() if i.data(0)==3])==2
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
