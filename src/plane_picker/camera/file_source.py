from pathlib import Path
import cv2
import numpy as np
from .base import CameraSource


class FileCameraSource(CameraSource):
    def __init__(self, path, camera_id="cam_left"):
        super().__init__(camera_id)
        self.path = str(Path(path).resolve())

    def read(self):
        image = cv2.imdecode(np.fromfile(self.path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法读取图像: {self.path}")
        return image
