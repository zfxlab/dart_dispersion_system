from abc import ABC, abstractmethod
from dataclasses import dataclass
import cv2
import numpy as np
from .camera_model import points2


@dataclass
class TagDetection:
    tag_id: int
    corners_raw: np.ndarray
    decision_margin: float = 0.

    def __post_init__(self):
        self.corners_raw = points2(self.corners_raw)
        if self.corners_raw.shape != (4, 2):
            raise ValueError("每个 Tag 必须有四个角点")


class TagDetector(ABC):
    """Backend boundary. All implementations MUST return canonical BL,BR,TR,TL.

    These labels refer to the unrotated printed tag, NOT its screen position.
    A future OpenCV adapter must explicitly reorder its native corners.
    """
    @abstractmethod
    def detect(self, image: np.ndarray) -> list[TagDetection]:
        raise NotImplementedError


class AprilTag3Detector(TagDetector):
    def __init__(self, family="tagStandard41h12"):
        from pupil_apriltags import Detector
        supported = {"tag16h5", "tag25h9", "tag36h11", "tagCircle21h7", "tagCircle49h12",
                     "tagStandard41h12", "tagStandard52h13", "tagCustom48h12"}
        if family not in supported:
            raise ValueError(f"AprilTag3 后端不支持 family={family}")
        self._detector = Detector(families=family, nthreads=2, quad_decimate=1.0)

    def detect(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if gray.dtype != np.uint8:
            raise ValueError("检测图像必须为 uint8")
        # Pupil preserves AprilTag 3 det->p exactly; no image-position sorting.
        return [TagDetection(int(d.tag_id), d.corners.copy(), float(d.decision_margin))
                for d in self._detector.detect(np.ascontiguousarray(gray))]
