from pathlib import Path

import cv2
import numpy as np

from plane_picker.calibration.camera_model import CameraModel
from plane_picker.calibration.tag_layout import TagLayout
from plane_picker.project import ProjectPaths
from plane_picker.storage.yaml_io import write_yaml


def test_project_imports_canonical_files_and_archives_plane(tmp_path):
    project = ProjectPaths(tmp_path / "dart_project")
    project.ensure()
    assert project.calibration_dir.is_dir()
    assert project.background_dir.is_dir()

    image = tmp_path / "source.jpg"
    cv2.imwrite(str(image), np.full((60, 80, 3), 120, np.uint8))
    imported_image = project.import_calibration_image(image)
    assert imported_image.name == "calibration_image.png"
    assert cv2.imread(str(imported_image)).shape == (60, 80, 3)

    camera = CameraModel.debug((80, 60))
    camera_file = tmp_path / "camera.yml"
    write_yaml(camera_file, camera.to_intrinsics_dict(include_validity=True))
    project.import_camera_intrinsics(camera_file)
    assert CameraModel.load(project.camera_intrinsics).image_size == (80, 60)

    layout = TagLayout("tag36h11", 20, {
        1: {"center_mm": [0, 0], "rotation_deg": 0},
    })
    layout_file = tmp_path / "layout.yml"
    write_yaml(layout_file, {**layout.to_dict(), "unit": "mm"})
    project.import_tag_layout(layout_file)
    assert TagLayout.load(project.tag_layout).tags[1]["center_mm"] == [0.0, 0.0]

    project.plane_calibration.write_text("old calibration", encoding="utf-8")
    archived = project.archive_plane_calibration()
    assert archived is not None and archived.is_file()
    assert not project.plane_calibration.exists()


def test_project_status_uses_fixed_names(tmp_path):
    project = ProjectPaths(tmp_path / "dart_project")
    project.ensure()
    (project.background_dir / "1.png").write_bytes(b"background")
    status = project.status()
    assert not status["calibration_image"]
    assert not status["plane_calibration"]
    assert status["background_count"] == 1
    assert project.results == project.root / "results.json"
