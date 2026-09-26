"""Reproducible software-only fixture using the real AprilTag 3 renderer."""
import argparse
import ctypes
from pathlib import Path
import cv2
import numpy as np
from pupil_apriltags import bindings

from .calibration.tag_detector import AprilTag3Detector
from .calibration.tag_layout import TagLayout
from .calibration.camera_model import CameraModel
from .service import PickerService
from .storage.yaml_io import write_yaml, atomic_text


def tag_bitmap(detector, tag_id, cell=24):
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
    return cv2.resize(tiny,None,fx=cell,fy=cell,interpolation=cv2.INTER_NEAREST),family.contents.width_at_border*cell


def generate(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    targets = [directory / name for name in ("image.png","tag_layout.yaml","camera_intrinsics.yaml",
               "plane_calibration.yaml","session.json","validation_points.csv","validation_clicks.csv")]
    if any(p.exists() for p in targets):
        raise ValueError("输出目录已有演示文件，请指定新目录以避免覆盖")
    detector = AprilTag3Detector()
    image = np.full((1080,1440,3),245,np.uint8)
    tags = {}
    origin = np.array([320.,840.])
    scale = None
    for i,center in enumerate(([0,0],[500,0],[0,500],[500,500])):
        bitmap,edge = tag_bitmap(detector,i)
        scale = edge/100.
        bitmap = np.rot90(bitmap,i).copy()
        xy = origin + np.asarray(center)*[scale,-scale]
        x,y = np.round(xy-np.array(bitmap.shape[::-1])/2).astype(int)
        image[y:y+bitmap.shape[0],x:x+bitmap.shape[1]] = bitmap[:,:,None]
        tags[i] = dict(center_mm=list(center),rotation_deg=float(i*90))
    layout = TagLayout("tagStandard41h12",100.,tags,"tag_0_center")
    write_yaml(targets[1],layout.to_dict())
    camera = CameraModel.debug((1440,1080))
    write_yaml(targets[2],camera.to_dict())
    points = [[150.,150.],[300.,200.],[220.,350.]]
    for p,color in zip(points,[(0,0,255),(255,0,0),(0,0,255)]):
        q = np.round(origin+np.asarray(p)*[scale,-scale]).astype(int)
        cv2.circle(image,tuple(q),4,color,-1)
    if not cv2.imwrite(str(targets[0]),image):
        raise ValueError("无法保存演示图像")
    service = PickerService()
    service.open_image(targets[0])
    service.load_layout(targets[1])
    service.load_camera(targets[2])
    service.detections = service.detect()
    service.accept_mapping(service.calculate())
    service.save_calibration(targets[3])
    for p,color in zip(points,["red","blue","red"]):
        service.add_shot(origin+np.asarray(p)*[scale,-scale],color)
    service.save_session(targets[4])
    validation_points = "point_id,x_mm,y_mm\n"
    validation_clicks = "point_id,u_raw,v_raw\n"
    for i,p in enumerate(points):
        raw = origin+np.asarray(p)*[scale,-scale]
        validation_points += f"V{i+1},{p[0]},{p[1]}\n"
        validation_clicks += f"V{i+1},{raw[0]},{raw[1]}\n"
    atomic_text(targets[5],validation_points)
    atomic_text(targets[6],validation_clicks)
    return service


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成软件验证演示数据（非实测精度证明）")
    parser.add_argument("directory",type=Path)
    args = parser.parse_args(argv)
    try:
        service = generate(args.directory)
        print(f"Generated {args.directory}: {len(service.detections)} tags, {len(service.session.shots)} shots")
        return 0
    except (ValueError,OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
