import json
import cv2
import numpy as np
import pytest

from test_core import scene
from plane_picker.service import PickerService
from plane_picker.calibration.homography import PlaneMapper, project
from plane_picker.calibration.validator import MappingValidator
from plane_picker.storage.csv_export import export_csv
from plane_picker.models.calibration import PlaneCalibration


@pytest.fixture
def service(scene,tmp_path):
    layout,camera,h,detections = scene
    path = tmp_path/"原始图像.png"
    cv2.imwrite(str(path),np.full((1080,1440,3),220,np.uint8))
    s = PickerService()
    s.open_image(path)
    s.layout,s.camera,s.detections = layout,camera,detections
    s.session.tag_layout_snapshot = layout.to_dict()
    s.accept_mapping(s.calculate())
    return s,h


def test_edits_ids_and_undo(service):
    s,h = service
    raw = s.camera.distort(project(h,[[200,200]]))[0]
    first = s.add_shot(raw,"red")
    second = s.add_shot(raw,"blue")
    s.undo()
    third = s.add_shot(raw,"red")
    assert [first.shot_id,second.shot_id,third.shot_id] == [1,2,3]
    s.edit_shot(3,raw+[1,1],"blue")
    assert s.session.shots[-1].color=="blue"
    s.undo()
    assert s.session.shots[-1].color=="red"
    s.delete_shot(1)
    s.undo()
    assert len(s.session.shots)==2
    s.clear()
    s.undo()
    assert len(s.session.shots)==2
    s.renumber_labels()
    assert [p.shot_label for p in s.session.shots]==["R01","R02"]
    assert [p.shot_id for p in s.session.shots]==[1,3]
    s.new_round()
    assert s.add_shot(raw,"blue").shot_id==4


def test_full_session_and_exports(service,tmp_path):
    s,h = service
    s.add_shot(s.camera.distort(project(h,[[200,200]]))[0],"blue")
    p = tmp_path/"session.json"
    s.save_session(p)
    restored = PickerService()
    restored.load_session(p)
    assert restored.session.to_dict()==s.session.to_dict()
    assert restored.image.shape==s.image.shape
    export_csv(tmp_path/"shots.csv",restored.session)
    assert "session_id,shot_id,shot_label" in (tmp_path/"shots.csv").read_text()
    s.save_calibration(tmp_path/"plane.yaml")
    loaded = PlaneMapper(PlaneCalibration.load(tmp_path/"plane.yaml"))
    assert loaded.calibration.calibration_id == s.session.calibration_id
    data = json.loads(p.read_text())
    data["shots"][0]["plane_mm"][0] += 20
    p.write_text(json.dumps(data))
    before = restored.session.to_dict()
    with pytest.raises(ValueError,match="坐标"):
        restored.load_session(p)
    assert restored.session.to_dict()==before


def test_invalidates_context(service,tmp_path):
    s,h = service
    raw = s.camera.distort(project(h,[[200,200]]))[0]
    s.add_shot(raw,"red")
    with pytest.raises(ValueError,match="本轮已有"):
        s.set_debug(True)
    s.clear()
    s.open_image(s.session.image_file)
    assert s.mapper is None
    assert not s.history


def test_selection_image_keeps_fixed_camera_mapping(service,tmp_path):
    s,_ = service
    mapper = s.mapper
    calibration_id = s.session.calibration_id
    target = tmp_path/"待选点.png"
    cv2.imwrite(str(target),np.full((1080,1440,3),80,np.uint8))

    s.open_selection_image(target)

    assert s.mapper is mapper
    assert s.session.calibration_id == calibration_id
    assert s.session.image_file == str(target.resolve())
    assert not s.detections

    wrong_size = tmp_path/"错误尺寸.png"
    cv2.imwrite(str(wrong_size),np.zeros((100,100,3),np.uint8))
    with pytest.raises(ValueError,match="尺寸"):
        s.open_selection_image(wrong_size)
    assert s.session.image_file == str(target.resolve())


def test_independent_validation(tmp_path):
    v = MappingValidator([dict(point_id="a",x_mm=0.,y_mm=0.),dict(point_id="b",x_mm=10.,y_mm=10.)],"cal")
    v.add([3,4])
    v.add([10,10])
    r = v.report()
    assert r["complete"]
    assert r["statistics"]["mean_mm"] == 2.5
    assert r["statistics"]["rms_mm"] == pytest.approx(np.sqrt(12.5))
    assert r["statistics"]["p95_mm"] == 4.75
    v.export(tmp_path/"validation_report.json")
    assert (tmp_path/"validation_report.csv").exists()
