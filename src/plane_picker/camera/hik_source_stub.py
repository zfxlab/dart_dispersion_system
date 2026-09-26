from .base import CameraSource


class HikCameraSource(CameraSource):
    """Reserved MVS SDK boundary. No camera is opened in phase one."""
    def read(self):
        raise NotImplementedError("第一阶段不接入海康 MVS SDK；请使用 FileCameraSource")
