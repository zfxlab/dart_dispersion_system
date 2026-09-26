from abc import ABC, abstractmethod
import numpy as np


class CameraSource(ABC):
    """One source per camera_id; future live sources retain independent K, D, H."""
    def __init__(self, camera_id="cam_left"):
        self.camera_id = camera_id

    @abstractmethod
    def read(self) -> np.ndarray:
        """Return a raw, unrectified uint8 BGR frame."""
        raise NotImplementedError

    def close(self):
        pass
