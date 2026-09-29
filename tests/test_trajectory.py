import json

import cv2
import numpy as np
import pytest

from plane_picker.trajectory import (
    TrajectoryConfig, find_active_overlay, process_project,
)


def write_image(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), image)


def project(tmp_path):
    root = tmp_path / "project"
    background = np.full((120, 180, 3), 70, np.uint8)
    write_image(root / "_background" / "1.png", background)
    write_image(root / "_background" / "2.png", background + 1)
    foreground = background.copy()
    cv2.line(foreground, (20, 100), (150, 20), (220, 220, 220), 3)
    write_image(root / "dart_1" / "1.png", foreground)
    return root


def test_batch_creates_versioned_outputs_and_active_overlay(tmp_path):
    root = project(tmp_path)
    first = process_project(root, TrajectoryConfig(minimum_area_px=5))
    overlay = find_active_overlay(root, "dart_1", 1)

    assert first["completed"] == 1
    assert first["failed"] == 0
    assert overlay is not None
    assert cv2.imread(str(overlay)).shape == (120, 180, 3)
    mask = cv2.imread(str(overlay.parent / "mask.png"), cv2.IMREAD_GRAYSCALE)
    assert mask[100, 20] == 255
    assert np.count_nonzero(mask) < mask.size / 4

    second = process_project(root, TrajectoryConfig(minimum_area_px=5, enhancement_gain=6))
    assert second["run_id"] != first["run_id"]
    assert (root / "_processed" / "runs" / first["run_id"]).is_dir()
    active = json.loads((root / "_processed" / "active.json").read_text())
    assert active["run_id"] == second["run_id"]


def test_batch_requires_flat_background_directory(tmp_path):
    root = tmp_path / "project"
    write_image(root / "dart_1" / "1.png", np.zeros((20, 20, 3), np.uint8))
    with pytest.raises(ValueError, match="背景目录"):
        process_project(root)


def test_size_mismatch_is_recorded_without_activating_run(tmp_path):
    root = tmp_path / "project"
    write_image(root / "_background" / "1.png", np.zeros((20, 20, 3), np.uint8))
    write_image(root / "dart_1" / "1.png", np.zeros((30, 20, 3), np.uint8))

    with pytest.raises(ValueError, match="全部处理失败"):
        process_project(root)

    manifests = list((root / "_processed" / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text())
    assert data["items"][0]["status"] == "failed"
    assert not (root / "_processed" / "active.json").exists()
