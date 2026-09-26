"""Qt events use logical pixels; only physical screen coordinates need DPR division."""
from dataclasses import dataclass
import numpy as np
from ..calibration.homography import project


@dataclass
class CoordinateTransform:
    image_to_view: np.ndarray
    device_pixel_ratio: float = 1.

    @classmethod
    def fit(cls, image_size, viewport_size, zoom=1., pan=(0., 0.), device_pixel_ratio=1.):
        if min(*image_size, *viewport_size, zoom, device_pixel_ratio) <= 0:
            raise ValueError("尺寸、缩放和 DPI 比例必须为正数")
        scale = min(viewport_size[0] / image_size[0], viewport_size[1] / image_size[1]) * zoom
        offset = (np.asarray(viewport_size) - np.asarray(image_size) * scale) / 2 + pan
        return cls(np.array([[scale, 0, offset[0]], [0, scale, offset[1]], [0, 0, 1.]]), device_pixel_ratio)

    @classmethod
    def from_qtransform(cls, t, device_pixel_ratio=1.):
        # QTransform stores coefficients in the transposed convention.
        return cls(np.array([[t.m11(), t.m21(), t.m31()], [t.m12(), t.m22(), t.m32()],
                             [t.m13(), t.m23(), t.m33()]]), device_pixel_ratio)

    def view_to_image(self, point, physical_pixels=False):
        p = np.asarray(point, dtype=float)
        if physical_pixels:
            p = p / self.device_pixel_ratio
        return project(np.linalg.inv(self.image_to_view), [p])[0]

    def image_to_view_point(self, point):
        return project(self.image_to_view, [point])[0]

    def raw_from_view(self, point, camera, undistorted_display=False):
        p = self.view_to_image(point)
        return camera.distort([p])[0] if undistorted_display else p
