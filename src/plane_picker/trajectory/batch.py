from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np

from ..measurement_batch import IMAGE_SUFFIXES, MeasurementBatch
from ..storage.yaml_io import atomic_text
from .pipeline import TrajectoryConfig, enhance_trajectory


PROCESSED_DIR = "_processed"
BACKGROUND_DIR = "_background"


def _read_image(path):
    path = Path(path)
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"无法读取图像: {path}")
    return image


def _write_png(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError(f"无法编码处理结果: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def background_files(root):
    directory = Path(root).resolve() / BACKGROUND_DIR
    if not directory.is_dir():
        raise ValueError(f"缺少背景目录: {directory}")
    files = sorted(
        path.resolve() for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not files:
        raise ValueError("_background 中没有可用背景图片")
    return files


def _run_id():
    return datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")


def process_project(root, config=None):
    """Process every dart/trial image and activate the new immutable run."""
    root = Path(root).resolve()
    config = config or TrajectoryConfig()
    batch = MeasurementBatch.scan(root)
    backgrounds_paths = background_files(root)
    backgrounds = [_read_image(path) for path in backgrounds_paths]
    run_id = _run_id()
    run_root = root / PROCESSED_DIR / "runs" / run_id
    items = []
    completed = 0
    for measurement in batch.measurements:
        relative_source = Path(measurement.image_file).relative_to(root)
        output = run_root / measurement.object_id / str(measurement.trial)
        item = {
            "object_id": measurement.object_id,
            "trial": measurement.trial,
            "source": str(relative_source),
            "source_sha256": _hash(measurement.image_file),
        }
        try:
            result = enhance_trajectory(
                _read_image(measurement.image_file), backgrounds, config,
            )
            _write_png(output / "overlay.png", result["overlay"])
            _write_png(output / "difference.png", result["difference"])
            _write_png(output / "mask.png", result["mask"])
            atomic_text(
                output / "metrics.json",
                json.dumps(result["metrics"], ensure_ascii=False, indent=2),
            )
            item.update({
                "status": "complete",
                "outputs": {
                    "overlay": str((output / "overlay.png").relative_to(root)),
                    "difference": str((output / "difference.png").relative_to(root)),
                    "mask": str((output / "mask.png").relative_to(root)),
                },
                "metrics": result["metrics"],
            })
            completed += 1
        except Exception as exc:
            item.update(status="failed", error=str(exc))
        items.append(item)

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(),
        "backgrounds": [
            {"file": str(path.relative_to(root)), "sha256": _hash(path)}
            for path in backgrounds_paths
        ],
        "completed": completed,
        "failed": len(items) - completed,
        "items": items,
    }
    atomic_text(
        run_root / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
    )
    if completed == 0:
        raise ValueError(f"{len(items)} 张图片全部处理失败，详见 {run_root / 'manifest.json'}")
    atomic_text(
        root / PROCESSED_DIR / "active.json",
        json.dumps({"schema_version": 1, "run_id": run_id}, indent=2),
    )
    return manifest


def active_run_id(root):
    path = Path(root).resolve() / PROCESSED_DIR / "active.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("run_") or "/" in run_id:
        return None
    return run_id


def find_active_overlay(root, object_id, trial):
    run_id = active_run_id(root)
    if run_id is None:
        return None
    path = (
        Path(root).resolve() / PROCESSED_DIR / "runs" / run_id
        / object_id / str(trial) / "overlay.png"
    )
    return path if path.is_file() else None
