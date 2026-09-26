from dataclasses import dataclass
import cv2
import numpy as np

from ..storage.yaml_io import fingerprint, read_yaml


def points2(value):
    p = np.asarray(value, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 2 or not np.isfinite(p).all():
        raise ValueError("坐标必须为有限的 N×2 数组")
    return p


@dataclass
class CameraModel:
    image_size: tuple[int, int]
    camera_matrix: np.ndarray
    distortion: np.ndarray
    intrinsics_valid: bool = True
    version: str = ""

    def __post_init__(self):
        self.image_size = tuple(self.image_size)
        self.camera_matrix = np.asarray(self.camera_matrix, dtype=float)
        self.distortion = np.asarray(self.distortion, dtype=float).reshape(-1)
        k = self.camera_matrix
        if len(self.image_size) != 2 or any(type(v) is not int or v <= 0 for v in self.image_size):
            raise ValueError("image_size 必须是正整数 [宽, 高]")
        if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
            raise ValueError("相机矩阵必须为有限的 3×3 矩阵，fx/fy > 0")
        if not np.allclose(k[2], [0, 0, 1]) or abs(k[0, 1]) > 1e-10 or abs(k[1, 0]) > 1e-10:
            raise ValueError("仅支持标准 OpenCV 无 skew 相机矩阵")
        if self.distortion.size not in (4, 5, 8, 12, 14) or not np.isfinite(self.distortion).all():
            raise ValueError("畸变参数须为 4/5/8/12/14 个有限数值")
        if type(self.intrinsics_valid) is not bool:
            raise ValueError("intrinsics_valid 必须为布尔值")
        if not self.version:
            self.version = fingerprint(self.to_dict(include_version=False))

    @classmethod
    def from_dict(cls, data):
        return cls(tuple(data["image_size"]), data["camera_matrix"], data["distortion"],
                   data.get("intrinsics_valid", True), data.get("intrinsics_version", ""))

    @classmethod
    def load(cls, path):
        return cls.from_dict(read_yaml(path))

    @classmethod
    def debug(cls, image_size):
        w, h = image_size
        return cls(image_size, [[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], [0.] * 5, False)

    def to_dict(self, include_version=True):
        d = dict(image_size=list(self.image_size), camera_matrix=self.camera_matrix.tolist(),
                 distortion=self.distortion.tolist(), intrinsics_valid=self.intrinsics_valid)
        if include_version:
            d["intrinsics_version"] = self.version
        return d

    def check_size(self, image_size):
        if tuple(image_size) != self.image_size:
            raise ValueError(f"图像尺寸 {tuple(image_size)} 与内参 {self.image_size} 不匹配；请重新标定，不能自动缩放")

    def undistort(self, raw):
        p = points2(raw)
        if not len(p):
            return p.copy()
        return cv2.undistortPoints(p.reshape(-1, 1, 2), self.camera_matrix,
                                   self.distortion, P=self.camera_matrix).reshape(-1, 2)

    def distort(self, undistorted):
        p = points2(undistorted)
        if not len(p):
            return p.copy()
        rays = np.c_[p, np.ones(len(p))] @ np.linalg.inv(self.camera_matrix).T
        raw, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), self.camera_matrix, self.distortion)
        return raw.reshape(-1, 2)

    def undistort_image(self, image):
        self.check_size((image.shape[1], image.shape[0]))
        return cv2.undistort(image, self.camera_matrix, self.distortion, None, self.camera_matrix)
