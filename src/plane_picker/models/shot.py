from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from ..calibration.camera_model import points2


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShotRecord:
    shot_id: int
    shot_label: str
    color: str
    camera_id: str
    image_file: str
    pixel_raw: list
    pixel_undistorted: list
    plane_mm: list
    manual: bool
    calibration_id: str
    timestamp: str

    def __post_init__(self):
        if type(self.shot_id) is not int or self.shot_id < 1 or self.color not in ("red", "blue"):
            raise ValueError("非法 shot_id 或颜色")
        for name in ("pixel_raw", "pixel_undistorted", "plane_mm"):
            setattr(self, name, points2([getattr(self, name)])[0].tolist())

    def to_dict(self):
        return asdict(self)
