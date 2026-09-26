import copy
import numpy as np
import pytest

from plane_picker.calibration.camera_model import CameraModel
from plane_picker.calibration.tag_layout import TagLayout
from plane_picker.calibration.tag_detector import TagDetection
from plane_picker.calibration.homography import PlaneMapper, project
from plane_picker.ui.coordinate_transform import CoordinateTransform
from plane_picker.models.session import Session
from plane_picker.models.shot import ShotRecord
from plane_picker.storage.session_io import SessionRepository


@pytest.fixture
def scene():
    layout = TagLayout("tagStandard41h12", 100., {
        0: dict(center_mm=[0., 0.], rotation_deg=0.),
        5: dict(center_mm=[500., 0.], rotation_deg=90.),
        8: dict(center_mm=[0., 500.], rotation_deg=180.),
        9: dict(center_mm=[500., 500.], rotation_deg=30.)})
    camera = CameraModel((1440, 1080), [[1200, 0, 720], [0, 1200, 540], [0, 0, 1]], [.02, -.005, .0001, -.0002, 0])
    h = np.array([[1.2, .12, 260], [.03, -1.1, 850], [.0001, .00015, 1.]])
    detections = [TagDetection(i, camera.distort(project(h, layout.corners(i)))) for i in layout.tags]
    return layout, camera, h, detections


@pytest.mark.parametrize("angle,expected", [
    (0, [[-1,-1],[1,-1],[1,1],[-1,1]]),
    (90, [[1,-1],[1,1],[-1,1],[-1,-1]]),
    (180, [[1,1],[-1,1],[-1,-1],[1,-1]])])
def test_corner_order(angle, expected):
    layout = TagLayout("tagStandard41h12", 2, {17: dict(center_mm=[10,20], rotation_deg=angle)})
    np.testing.assert_allclose(layout.corners(17), np.array(expected) + [10,20], atol=1e-12)


def test_synthetic_mapping(scene):
    layout, camera, h, detections = scene
    mapper = PlaneMapper.fit(detections, layout, camera)
    for xy in ([0,0], [230,170], [500,500]):
        raw = camera.distort(project(h, [xy]))[0]
        _, recovered = mapper.map_raw(raw)
        np.testing.assert_allclose(recovered, xy, atol=1e-3)
    assert mapper.calibration.inlier_count == 16
    assert mapper.calibration.image_reprojection_rms_px < .001


def test_outlier(scene):
    layout, camera, h, detections = scene
    detections[0].corners_raw[0] += [80, 50]
    mapper = PlaneMapper.fit(detections, layout, camera)
    assert mapper.calibration.inlier_count == 15
    assert not mapper.calibration.inlier_mask[0]


def test_invalid_mapping(scene):
    layout, camera, h, detections = scene
    mapper = PlaneMapper.fit(detections, layout, camera)
    assert mapper.contains([200,200])
    assert not mapper.contains([1000,1000])
    with pytest.raises(ValueError, match="有效标定区域"):
        mapper.map_raw(camera.distort(project(h, [[650,300]]))[0])
    for bad in (np.zeros((3,3)), np.full((3,3), np.nan), np.diag([1,1,1e-15])):
        with pytest.raises(ValueError):
            project(bad, [[0,0]])
    with pytest.raises(ValueError, match="分母"):
        project([[1,0,0],[0,1,0],[1,0,-1]], [[1,2]])
    tampered = copy.deepcopy(mapper.calibration)
    tampered.homography_image_to_plane = h.tolist()
    with pytest.raises(ValueError):
        PlaneMapper(tampered)
    with pytest.raises(ValueError, match="2 个"):
        PlaneMapper.fit(detections[:1], layout, camera)


def test_distortion(scene):
    camera = scene[1]
    p = np.array([[500.,300.],[720.,540.]])
    assert camera.undistort(p).shape == (2,2)
    np.testing.assert_allclose(camera.undistort(camera.distort(p)), p, atol=1e-5)
    np.testing.assert_allclose(CameraModel.debug((1440,1080)).undistort(p), p, atol=1e-10)
    with pytest.raises(ValueError):
        camera.check_size((720,540))


@pytest.mark.parametrize("viewport,zoom,pan,dpr", [((100,100),1,(0,0),1), ((200,200),1,(0,0),1),
    ((200,100),1,(0,0),1), ((200,100),1,(30,-20),1), ((200,100),3,(-30,20),2)])
def test_view_coordinates(viewport, zoom, pan, dpr):
    tr = CoordinateTransform.fit((100,100), viewport, zoom, pan, dpr)
    original = [37., 66.]
    view = tr.image_to_view_point(original)
    np.testing.assert_allclose(tr.view_to_image(view), original, atol=1e-10)
    np.testing.assert_allclose(tr.view_to_image(view*dpr, physical_pixels=True), original, atol=1e-10)


def test_session_roundtrip(tmp_path):
    s = Session(shots=[ShotRecord(5,"R05","red","cam_left","image.png",[1,2],[1,2],[3,4],True,"abc","now")], next_shot_id=6)
    p = tmp_path / "session.json"
    SessionRepository.save(p, s)
    assert SessionRepository.load(p).to_dict() == s.to_dict()


def test_duplicate_yaml_and_units(tmp_path):
    p = tmp_path / "layout.yaml"
    p.write_text("tags:\n  0: {}\n  0: {}\n")
    with pytest.raises(ValueError, match="重复"):
        TagLayout.load(p)
    with pytest.raises(ValueError, match="mm"):
        TagLayout.from_dict(dict(unit="cm"))
