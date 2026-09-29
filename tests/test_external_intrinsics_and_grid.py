"""Synthetic 25-tag coverage; dimensions below are fixtures, not the user's board."""
from pathlib import Path

import numpy as np
import pytest

from plane_picker.calibration.camera_model import CameraModel
from plane_picker.calibration.tag_layout import TagLayout
from plane_picker.calibration.tag_detector import TagDetection
from plane_picker.calibration.homography import PlaneMapper, project
from plane_picker.storage.yaml_io import write_yaml


def test_physical_5x5_layout_uses_canonical_tag_direction():
    path = Path(__file__).parents[1] / "config" / "tag_layout.yaml"
    layout = TagLayout.load(path)
    assert layout.family == "tag36h11"
    assert layout.tag_size_mm == 168.0
    assert len(layout.tags) == 25
    assert layout.tags[11]["center_mm"] == [84.0, 84.0]
    assert layout.tags[67]["center_mm"] == [916.0, 916.0]
    assert {tag["rotation_deg"] for tag in layout.tags.values()} == {180.0}


def test_reference_intrinsics_yaml():
    path = Path(__file__).parents[1] / "config" / "calibration" / "00DA1923282.yaml"
    camera = CameraModel.load(path)
    assert camera.image_size == (1440, 1080)
    assert camera.camera_matrix.shape == (3, 3)
    assert camera.distortion.shape == (5,)
    assert camera.intrinsics_valid


def test_external_intrinsics_yaml(tmp_path):
    path = tmp_path / "external_camera.yaml"
    write_yaml(path, {
        "image_width": 1440,
        "image_height": 1080,
        "camera_name": "external_camera",
        "camera_matrix": {"rows": 3, "cols": 3,
                          "data": [1200., 0., 720., 0., 1200., 540., 0., 0., 1.]},
        "distortion_model": "plumb_bob",
        "distortion_coefficients": {"rows": 1, "cols": 5,
                                    "data": [.02, -.005, .0001, -.0002, 0.]},
        "rectification_matrix": {"rows": 3, "cols": 3,
                                 "data": [1., 0., 0., 0., 1., 0., 0., 0., 1.]},
        "projection_matrix": {"rows": 3, "cols": 4,
                              "data": [1200., 0., 720., 0., 0., 1200., 540., 0., 0., 0., 1., 0.]},
    })
    camera = CameraModel.load(path)
    assert camera.intrinsics_valid
    assert len(camera.version) == 64
    pixels = np.array([[250., 300.], [700., 800.]])
    np.testing.assert_allclose(camera.undistort(camera.distort(pixels)), pixels, atol=1e-5)


def test_intrinsics_rejects_wrong_shape_and_fisheye(tmp_path):
    camera = CameraModel((1440, 1080), [[1200, 0, 720], [0, 1200, 540], [0, 0, 1]],
                         [0.] * 5)
    data = camera.to_intrinsics_dict()
    data["camera_matrix"]["rows"] = 2
    with pytest.raises(ValueError, match="3×3"):
        CameraModel.from_dict(data)
    data = camera.to_intrinsics_dict()
    data["distortion_model"] = "equidistant"
    with pytest.raises(ValueError, match="仅支持"):
        CameraModel.from_dict(data)


def test_25_tag_grid_with_100_corners(tmp_path):
    # Arbitrary IDs, different orientations, 140 mm detection edges in 200 mm cells.
    # This is only a numerical test, not a proposed physical-board configuration.
    layout = TagLayout("tagStandard41h12", 140., {
        100 + r*5+c: {"center_mm": [100.+200*c, 100.+200*r],
                     "rotation_deg": float(90*((r+c)%4))}
        for r in range(5) for c in range(5)
    }, "board_lower_left")
    path = tmp_path / "grid.yaml"
    write_yaml(path, layout.to_dict())
    layout = TagLayout.load(path)
    camera = CameraModel((1440,1080), [[1200,0,720],[0,1200,540],[0,0,1]],
                         [.01,-.001,.0001,-.0001,0.])
    plane_to_image = np.array([[.8,.02,200.],[.01,-.8,950.],[.00001,.00002,1.]])
    detections = [TagDetection(i, camera.distort(project(plane_to_image, layout.corners(i))))
                  for i in layout.tags]
    mapper = PlaneMapper.fit(detections,layout,camera)
    assert len(mapper.calibration.detected_tag_ids) == 25
    assert mapper.calibration.inlier_count == 100
    for xy in ([100.,100.], [500.,500.], [900.,900.]):
        raw = camera.distort(project(plane_to_image,[xy]))[0]
        np.testing.assert_allclose(mapper.map_raw(raw)[1], xy, atol=.001)
    assert not mapper.contains([0.,0.])  # Board margin is not calibration coverage.
