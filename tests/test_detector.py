"""Exercise the real AprilTag 3 backend, including decoded orientation.

Render via AprilTag's own C API, not a mocked detection. The family size is
width_at_border rather than total_width (especially important for 41h12).
"""
import ctypes
import cv2
import numpy as np
import pytest
from pupil_apriltags import bindings
from plane_picker.calibration.tag_detector import AprilTag3Detector


def rendered_tag(detector, tag_id=0, cell=24, margin=70):
    native = detector._detector
    lib = native.libc
    lib.apriltag_to_image.argtypes = [ctypes.POINTER(bindings._ApriltagFamily),ctypes.c_uint32]
    lib.apriltag_to_image.restype = ctypes.POINTER(bindings._ImageU8)
    lib.image_u8_destroy.argtypes = [ctypes.POINTER(bindings._ImageU8)]
    family = native.tag_families["tagStandard41h12"]
    ptr = lib.apriltag_to_image(family,tag_id)
    try:
        im = ptr.contents
        tiny = np.ctypeslib.as_array(im.buf,shape=(im.height,im.stride))[:,:im.width].copy()
    finally:
        lib.image_u8_destroy(ptr)
    image = cv2.resize(tiny,None,fx=cell,fy=cell,interpolation=cv2.INTER_NEAREST)
    image = cv2.copyMakeBorder(image,margin,margin,margin,margin,cv2.BORDER_CONSTANT,value=255)
    edge = family.contents.width_at_border * cell
    center = image.shape[0]/2 - .5
    corners = np.array([[-1,1],[1,1],[1,-1],[-1,-1]]) * edge/2 + center
    return image,corners


@pytest.mark.parametrize("turns",[0,1,2,3])
def test_real_detector_corner_order(turns):
    detector = AprilTag3Detector()
    image,expected = rendered_tag(detector,3)
    n = image.shape[0]
    for _ in range(turns):
        expected = np.c_[expected[:,1], n-1-expected[:,0]]
    detections = detector.detect(np.ascontiguousarray(np.rot90(image,turns)))
    assert len(detections)==1
    assert detections[0].tag_id==3
    np.testing.assert_allclose(detections[0].corners_raw,expected,atol=1.)
