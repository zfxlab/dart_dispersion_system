import numpy as np
from plane_picker.demo import generate
from plane_picker.validation_cli import main
from plane_picker.storage.shot_ids import ShotIdAllocator


def test_real_detector_to_session_and_validation(tmp_path):
    s = generate(tmp_path)
    assert len(s.detections)==4
    assert s.mapper.calibration.inlier_count==16
    assert not s.mapper.calibration.intrinsics_valid
    # A rasterized detector has subpixel corner uncertainty, unlike exact point tests.
    expected = [[150,150],[300,200],[220,350]]
    np.testing.assert_allclose([p.plane_mm for p in s.session.shots],expected,atol=1.)
    assert main(["--calibration",str(tmp_path/"plane_calibration.yaml"),"--points",str(tmp_path/"validation_points.csv"),
                 "--clicks",str(tmp_path/"validation_clicks.csv"),"--output",str(tmp_path/"report")])==0


def test_persistent_ids_across_instances(tmp_path):
    path = tmp_path/"ids.sqlite"
    a,b = ShotIdAllocator(path),ShotIdAllocator(path)
    assert a.allocate()==1
    assert b.allocate()==2
    assert a.allocate(50)==50
    b.reserve(80)
    assert ShotIdAllocator(path).allocate()==80
