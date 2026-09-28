from abc import ABC, abstractmethod
class CameraSource(ABC):
    """One source per camera_id; future live sources retain independent K, D, H."""
    def __init__(self, camera_id="cam_left"):
        self.camera_id = camera_id

    @abstractmethod
    def read(self):
        """Return a source capture; live sources include synchronized metadata."""
        raise NotImplementedError

    def close(self):
        pass
