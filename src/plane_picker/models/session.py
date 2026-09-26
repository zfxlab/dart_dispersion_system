from dataclasses import dataclass, field, asdict
from uuid import uuid4
from .shot import now
from .. import __version__


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=now)
    image_file: str = ""
    camera_id: str = "cam_left"
    calibration_file: str = ""
    calibration_id: str = ""
    tag_layout_file: str = ""
    shots: list = field(default_factory=list)
    next_shot_id: int = 1
    ui_version: str = __version__
    project_version: str = __version__
    schema_version: int = 1
    calibration_snapshot: dict | None = None
    tag_layout_snapshot: dict | None = None

    def to_dict(self):
        return asdict(self)
