from dataclasses import dataclass
from pathlib import Path
import numpy as np

from .camera_model import points2
from ..storage.yaml_io import fingerprint, read_yaml


@dataclass
class TagLayout:
    family: str
    tag_size_mm: float
    tags: dict
    origin_description: str = "grid_lower_left"
    file: str = ""

    def __post_init__(self):
        if not isinstance(self.family, str) or not self.family:
            raise ValueError("缺少 Tag family")
        if not np.isfinite(self.tag_size_mm) or self.tag_size_mm <= 0 or not self.tags:
            raise ValueError("Tag 尺寸必须 > 0，布局不能为空")
        clean = {}
        for tag_id, entry in self.tags.items():
            if isinstance(tag_id, bool) or str(int(tag_id)) != str(tag_id) or int(tag_id) < 0:
                raise ValueError(f"非法 Tag ID: {tag_id}")
            tag_id = int(tag_id)
            if tag_id in clean:
                raise ValueError(f"重复 Tag ID: {tag_id}")
            center = points2([entry["center_mm"]])[0]
            angle = float(entry["rotation_deg"])
            if not np.isfinite(angle):
                raise ValueError("旋转角度必须有限")
            clean[tag_id] = dict(center_mm=center.tolist(), rotation_deg=angle)
        self.tags = clean

    @classmethod
    def from_dict(cls, d, file=""):
        if d.get("unit") != "mm":
            raise ValueError("布局 unit 必须为 mm")
        return cls(d["family"], float(d["tag_size_mm"]), d["tags"], d.get("origin_description", ""), file)

    @classmethod
    def load(cls, path):
        return cls.from_dict(read_yaml(path), str(Path(path).resolve()))

    def to_dict(self):
        return dict(family=self.family, unit="mm", tag_size_mm=self.tag_size_mm,
                    origin_description=self.origin_description, tags={str(k): v for k, v in self.tags.items()})

    @property
    def layout_hash(self):
        d = self.to_dict()
        d["tags"] = {str(k): v for k, v in self.tags.items()}
        return fingerprint(d)

    def corners(self, tag_id):
        """AprilTag 3 p[0..3]: BL, BR, TR, TL in canonical printed orientation.

        Its homography uses y-down (-1,+1),(+1,+1),(+1,-1),(-1,-1).
        Our y-UP plane therefore uses (-s,-s),(+s,-s),(+s,+s),(-s,+s).
        Never sort by screen x/y: decoding already accounts for tag rotation.
        rotation_deg is counterclockwise in the physical +X/+Y plane.
        Size is the detected quad edge, not paper or outer quiet-zone width.
        """
        tag = self.tags[tag_id]
        a = np.deg2rad(tag["rotation_deg"])
        rotation = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        local = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * self.tag_size_mm / 2
        return local @ rotation.T + tag["center_mm"]
