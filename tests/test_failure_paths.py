import copy
import numpy as np
import pytest

from test_core import scene
from plane_picker.calibration.homography import PlaneMapper
from plane_picker.calibration.tag_detector import TagDetection


def test_rejects_duplicate_detection_and_corrupt_calibration(scene):
    layout,camera,_,detections = scene
    with pytest.raises(ValueError,match="重复"):
        PlaneMapper.fit(detections+[detections[0]],layout,camera)
    mapper = PlaneMapper.fit(detections,layout,camera)
    c = copy.deepcopy(mapper.calibration)
    c.image_reprojection_rms_px = 100.
    with pytest.raises(ValueError,match="误差记录"):
        PlaneMapper(c)
    c = copy.deepcopy(mapper.calibration)
    c.valid_plane_polygon_mm = (np.asarray(c.valid_plane_polygon_mm)*2).tolist()
    with pytest.raises(ValueError,match="有效区域"):
        PlaneMapper(c)


def test_collinear_points_rejected(scene):
    layout,camera,_,_ = scene
    points = np.array([[100,200],[200,200],[300,200],[400,200]],float)
    camera.distortion[:] = 0
    detections = [TagDetection(0,points),TagDetection(5,points+[500,0])]
    with pytest.raises(ValueError,match="非共线"):
        PlaneMapper.fit(detections,layout,camera)
