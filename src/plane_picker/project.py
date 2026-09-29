from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import tempfile

import cv2

from .calibration.camera_model import CameraModel
from .calibration.tag_layout import TagLayout
from .models.calibration import PlaneCalibration
from .camera.file_source import FileCameraSource
from .storage.yaml_io import atomic_text


@dataclass(frozen=True)
class ProjectPaths:
    """Canonical, portable paths inside one dart measurement project."""

    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root).resolve())

    @property
    def calibration_dir(self):
        return self.root / "_calibration"

    @property
    def calibration_image(self):
        return self.calibration_dir / "calibration_image.png"

    @property
    def camera_intrinsics(self):
        return self.calibration_dir / "camera_intrinsics.yaml"

    @property
    def tag_layout(self):
        return self.calibration_dir / "tag_layout.yaml"

    @property
    def plane_calibration(self):
        return self.calibration_dir / "plane_calibration.yaml"

    @property
    def background_dir(self):
        return self.root / "_background"

    @property
    def processed_dir(self):
        return self.root / "_processed"

    @property
    def results(self):
        return self.root / "results.json"

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.calibration_dir.mkdir(exist_ok=True)
        self.background_dir.mkdir(exist_ok=True)

    def archive_plane_calibration(self):
        if not self.plane_calibration.exists():
            return None
        history = self.calibration_dir / "_history"
        history.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = history / f"plane_calibration_{stamp}.yaml"
        os.replace(self.plane_calibration, target)
        return target

    def import_calibration_image(self, source):
        image = FileCameraSource(source).read()
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("标定照片无法无损转换为 PNG")
        self.ensure()
        fd, temporary = tempfile.mkstemp(
            prefix=".calibration_image.", dir=self.calibration_dir,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded.tobytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.calibration_image)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.calibration_image

    def _import_yaml(self, source, target, validator):
        source = Path(source).resolve()
        validator(source)
        text = source.read_text(encoding="utf-8")
        self.ensure()
        atomic_text(target, text)
        return target

    def import_camera_intrinsics(self, source):
        return self._import_yaml(source, self.camera_intrinsics, CameraModel.load)

    def import_tag_layout(self, source):
        return self._import_yaml(source, self.tag_layout, TagLayout.load)

    def import_plane_calibration(self, source):
        return self._import_yaml(
            source, self.plane_calibration, PlaneCalibration.load,
        )

    def status(self):
        return {
            "calibration_image": self.calibration_image.is_file(),
            "camera_intrinsics": self.camera_intrinsics.is_file(),
            "tag_layout": self.tag_layout.is_file(),
            "plane_calibration": self.plane_calibration.is_file(),
            "background_count": sum(
                path.is_file() for path in self.background_dir.iterdir()
            ) if self.background_dir.is_dir() else 0,
        }
