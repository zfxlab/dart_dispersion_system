import json

import numpy as np
import pytest

from plane_picker.measurement_batch import MeasurementBatch, dispersion_statistics


def image(path):
    path.write_bytes(b"image")


def test_numeric_folder_scan_and_live_refresh(tmp_path):
    root = tmp_path / "batch"
    object_b = root / "object_B"
    object_a = root / "object_A"
    object_a.mkdir(parents=True)
    object_b.mkdir()
    for name in ("10.png", "2.jpg", "1.png", "preview.png"):
        image(object_a / name)
    image(object_b / "1.tif")
    image(object_b / "note.txt")

    batch = MeasurementBatch.scan(root)

    assert [(m.object_id, m.trial) for m in batch.measurements] == [
        ("object_A", 1), ("object_A", 2), ("object_A", 10), ("object_B", 1),
    ]
    (object_a / "2.jpg").unlink()
    image(object_a / "3.png")
    refreshed = MeasurementBatch.scan(root)
    assert [m.trial for m in refreshed.measurements if m.object_id == "object_A"] == [1, 3, 10]


def test_duplicate_trial_is_rejected(tmp_path):
    directory = tmp_path / "batch" / "object_A"
    directory.mkdir(parents=True)
    image(directory / "1.png")
    image(directory / "1.jpg")
    with pytest.raises(ValueError, match="多个图片"):
        MeasurementBatch.scan(tmp_path / "batch")


def test_results_json_roundtrip_and_statistics(tmp_path):
    directory = tmp_path / "batch" / "object_A"
    directory.mkdir(parents=True)
    image(directory / "1.png")
    image(directory / "2.png")
    batch = MeasurementBatch.scan(tmp_path / "batch")
    batch.set_result(("object_A", 1), [10, 20], [11, 21], [100, 200], "t1")
    batch.set_result(("object_A", 2), [30, 40], [31, 41], [106, 208], "t2")
    batch.save("calibration-1")

    saved = json.loads(batch.results_path.read_text())
    assert saved["calibration_id"] == "calibration-1"
    assert [item["trial"] for item in saved["measurements"]] == [1, 2]

    restored = MeasurementBatch.load(tmp_path / "batch", "calibration-1")
    assert restored.progress() == (2, 2)
    np.testing.assert_allclose(restored.get(("object_A", 2)).plane_mm, [106, 208])
    stats = dispersion_statistics(restored.measurements)[0]
    assert stats["count"] == 2
    np.testing.assert_allclose([stats["mean_x_mm"], stats["mean_y_mm"]], [103, 204])
    assert stats["rms_radius_mm"] == pytest.approx(5)
    assert stats["max_span_mm"] == pytest.approx(10)

    with pytest.raises(ValueError, match="不同的标定"):
        MeasurementBatch.load(tmp_path / "batch", "calibration-2")


def test_removed_images_are_ignored_on_result_restore(tmp_path):
    directory = tmp_path / "batch" / "object_A"
    directory.mkdir(parents=True)
    image(directory / "1.png")
    batch = MeasurementBatch.scan(tmp_path / "batch")
    batch.set_result(("object_A", 1), [1, 2], [1, 2], [3, 4])
    batch.save("cal")
    (directory / "1.png").unlink()
    image(directory / "2.png")

    restored = MeasurementBatch.load(tmp_path / "batch", "cal")

    assert restored.progress() == (0, 1)
    assert restored.ignored_results == [("object_A", 1)]


def test_result_rotation_radius_and_rectangle_roundtrip(tmp_path):
    directory = tmp_path / "batch" / "dart_1"
    directory.mkdir(parents=True)
    image(directory / "1.png")
    batch = MeasurementBatch.scan(tmp_path / "batch")
    batch.set_result(("dart_1", 1), [1, 2], [1, 2], [100, 200])
    batch.result_rotation_deg = 90.0
    batch.impact_radius_mm = 10.0
    batch.set_dispersion_region("dart_1", [120, 220], 80, 40, -90)
    batch.save("cal")

    restored = MeasurementBatch.load(tmp_path / "batch", "cal")

    assert restored.result_rotation_deg == pytest.approx(90)
    assert restored.impact_radius_mm == pytest.approx(10)
    assert restored.dispersion_regions["dart_1"] == {
        "center_plane_mm": [120.0, 220.0],
        "width_mm": 80.0,
        "height_mm": 40.0,
        "angle_plane_deg": -90.0,
    }
