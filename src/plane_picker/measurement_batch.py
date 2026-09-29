from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

from .storage.yaml_io import atomic_text


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
RESULTS_NAME = "results.json"


def _point(value, name):
    point = np.asarray(value, dtype=float)
    if point.shape != (2,) or not np.all(np.isfinite(point)):
        raise ValueError(f"{name} 必须是两个有限数值")
    return point.tolist()


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return value


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数值")
    return value


@dataclass
class Measurement:
    object_id: str
    trial: int
    image_file: str
    pixel_raw: list | None = None
    pixel_undistorted: list | None = None
    plane_mm: list | None = None
    timestamp: str = ""

    @property
    def key(self):
        return self.object_id, self.trial

    @property
    def complete(self):
        return self.pixel_raw is not None

    def set_result(self, pixel_raw, pixel_undistorted, plane_mm, timestamp=None):
        self.pixel_raw = _point(pixel_raw, "pixel_raw")
        self.pixel_undistorted = _point(pixel_undistorted, "pixel_undistorted")
        self.plane_mm = _point(plane_mm, "plane_mm")
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def clear_result(self):
        self.pixel_raw = self.pixel_undistorted = self.plane_mm = None
        self.timestamp = ""


class MeasurementBatch:
    """A live, one-directory-deep view of object/trial images."""

    def __init__(self, root, measurements):
        self.root = Path(root).resolve()
        self.measurements = measurements
        self.calibration_id = ""
        self.ignored_results = []
        self.result_rotation_deg = 0.0
        self.impact_radius_mm = 10.0
        self.dispersion_regions = {}
        self.has_result_view = False

    @classmethod
    def scan(cls, root):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("测量文件夹不存在或不是目录")
        measurements = []
        seen_ids = set()
        for directory in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            if not directory.is_dir() or directory.name.startswith((".", "_")):
                continue
            normalized = directory.name.casefold()
            if normalized in seen_ids:
                raise ValueError(f"飞行物 ID 仅大小写不同：{directory.name}")
            seen_ids.add(normalized)
            trials = {}
            for image in directory.iterdir():
                if not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if not image.stem.isascii() or not image.stem.isdigit() or int(image.stem) < 1:
                    continue
                trial = int(image.stem)
                if trial in trials:
                    raise ValueError(f"{directory.name} 的第 {trial} 次测量存在多个图片")
                trials[trial] = image.resolve()
            measurements.extend(
                Measurement(directory.name, trial, str(path))
                for trial, path in sorted(trials.items())
            )
        if not measurements:
            raise ValueError("未找到“飞行物 ID 文件夹 / 正整数图片名”结构")
        return cls(root, measurements)

    @classmethod
    def load(cls, root, calibration_id=""):
        batch = cls.scan(root)
        path = batch.results_path
        if not path.exists():
            batch.calibration_id = calibration_id
            return batch
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("schema_version") != 1:
            raise ValueError("不支持的测量结果 JSON 版本")
        stored_calibration = data.get("calibration_id", "")
        if calibration_id and stored_calibration and calibration_id != stored_calibration:
            raise ValueError("results.json 使用了不同的标定，不能直接恢复")
        batch.calibration_id = stored_calibration or calibration_id
        view = data.get("result_view", {})
        if not isinstance(view, dict):
            raise ValueError("results.json 的 result_view 必须是 mapping")
        batch.has_result_view = "result_view" in data
        batch.result_rotation_deg = _finite(
            view.get("rotation_deg", 0.0), "result_view.rotation_deg",
        )
        batch.impact_radius_mm = _positive(
            view.get("impact_radius_mm", 10.0), "result_view.impact_radius_mm",
        )
        if not -180.0 <= batch.result_rotation_deg <= 180.0:
            raise ValueError("result_view.rotation_deg 必须在 -180 到 180 度之间")
        known_objects = set(batch.objects)
        for region in data.get("dispersion_regions", []):
            if not isinstance(region, dict):
                raise ValueError("results.json 包含非法散布范围")
            object_id = region.get("object_id")
            if not isinstance(object_id, str) or not object_id:
                raise ValueError("散布范围缺少有效的飞镖 ID")
            if object_id not in known_objects:
                continue
            if object_id in batch.dispersion_regions:
                raise ValueError(f"results.json 存在重复散布范围：{object_id}")
            batch.set_dispersion_region(
                object_id,
                region.get("center_plane_mm"),
                region.get("width_mm"),
                region.get("height_mm"),
                region.get("angle_plane_deg", 0.0),
            )
        current = {measurement.key: measurement for measurement in batch.measurements}
        seen = set()
        for result in data.get("measurements", []):
            object_id = result.get("object_id")
            trial = result.get("trial")
            key = (object_id, trial)
            if not isinstance(object_id, str) or type(trial) is not int or trial < 1:
                raise ValueError("results.json 包含非法飞行物 ID 或测量序号")
            if key in seen:
                raise ValueError(f"results.json 存在重复结果：{object_id}/{trial}")
            seen.add(key)
            measurement = current.get(key)
            if measurement is None:
                batch.ignored_results.append(key)
                continue
            measurement.set_result(
                result.get("pixel_raw"), result.get("pixel_undistorted"),
                result.get("plane_mm"), result.get("timestamp", ""),
            )
        return batch

    @property
    def results_path(self):
        return self.root / RESULTS_NAME

    @property
    def objects(self):
        return sorted({measurement.object_id for measurement in self.measurements}, key=str.casefold)

    def get(self, key):
        return next((measurement for measurement in self.measurements if measurement.key == key), None)

    def set_result(self, key, pixel_raw, pixel_undistorted, plane_mm, timestamp=None):
        measurement = self.get(key)
        if measurement is None:
            raise ValueError("当前测量图片已经不在文件夹中")
        measurement.set_result(pixel_raw, pixel_undistorted, plane_mm, timestamp)

    def clear_result(self, key):
        measurement = self.get(key)
        if measurement is not None:
            measurement.clear_result()

    def set_dispersion_region(self, object_id, center_plane_mm, width_mm,
                              height_mm, angle_plane_deg=0.0):
        if object_id not in self.objects:
            raise ValueError(f"未找到飞镖 ID：{object_id}")
        self.dispersion_regions[object_id] = {
            "center_plane_mm": _point(center_plane_mm, "center_plane_mm"),
            "width_mm": _positive(width_mm, "width_mm"),
            "height_mm": _positive(height_mm, "height_mm"),
            "angle_plane_deg": _finite(angle_plane_deg, "angle_plane_deg"),
        }

    def clear_dispersion_region(self, object_id):
        self.dispersion_regions.pop(object_id, None)

    def save(self, calibration_id):
        self.calibration_id = calibration_id
        self.has_result_view = True
        data = {
            "schema_version": 1,
            "dataset_root": self.root.name,
            "calibration_id": calibration_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "result_view": {
                "rotation_deg": self.result_rotation_deg,
                "impact_radius_mm": self.impact_radius_mm,
            },
            "dispersion_regions": [
                {"object_id": object_id, **region}
                for object_id, region in sorted(
                    self.dispersion_regions.items(), key=lambda item: item[0].casefold()
                )
            ],
            "measurements": [
                {
                    "object_id": measurement.object_id,
                    "trial": measurement.trial,
                    "image_file": str(Path(measurement.image_file).relative_to(self.root)),
                    "pixel_raw": measurement.pixel_raw,
                    "pixel_undistorted": measurement.pixel_undistorted,
                    "plane_mm": measurement.plane_mm,
                    "timestamp": measurement.timestamp,
                }
                for measurement in self.measurements if measurement.complete
            ],
        }
        atomic_text(self.results_path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))

    def progress(self, object_id=None):
        items = [m for m in self.measurements if object_id is None or m.object_id == object_id]
        return sum(m.complete for m in items), len(items)

    def next_incomplete(self, after=None):
        items = self.measurements
        start = items.index(after) + 1 if after in items else 0
        ordered = items[start:] + items[:start]
        return next((measurement for measurement in ordered if not measurement.complete), None)


def dispersion_statistics(measurements):
    groups = {}
    for measurement in measurements:
        if measurement.complete:
            groups.setdefault(measurement.object_id, []).append(measurement)
    rows = []
    for object_id in sorted(groups, key=str.casefold):
        items = groups[object_id]
        points = np.asarray([item.plane_mm for item in items], dtype=float)
        center = points.mean(axis=0)
        radii = np.linalg.norm(points - center, axis=1)
        if len(points) > 1:
            distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
            max_span = float(distances.max())
        else:
            max_span = 0.0
        rows.append({
            "object_id": object_id,
            "count": len(points),
            "mean_x_mm": float(center[0]),
            "mean_y_mm": float(center[1]),
            "rms_radius_mm": float(math.sqrt(np.mean(radii ** 2))),
            "max_span_mm": max_span,
        })
    return rows
