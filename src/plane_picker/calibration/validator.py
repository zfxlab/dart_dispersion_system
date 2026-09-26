import csv
import io
import json
from pathlib import Path
import numpy as np
from .camera_model import points2
from ..storage.yaml_io import atomic_text


class MappingValidator:
    def __init__(self, targets, calibration_id=""):
        self.targets = targets
        self.calibration_id = calibration_id
        self.results = []
        ids = [p["point_id"] for p in targets]
        if not ids or len(set(ids)) != len(ids) or any(not i for i in ids):
            raise ValueError("验证点 ID 不能为空或重复")
        points2([[p["x_mm"], p["y_mm"]] for p in targets])

    @classmethod
    def load(cls, path, calibration_id=""):
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rows = [dict(point_id=r["point_id"], x_mm=float(r["x_mm"]), y_mm=float(r["y_mm"]))
                    for r in csv.DictReader(f)]
        return cls(rows, calibration_id)

    @property
    def next_target(self):
        return self.targets[len(self.results)] if len(self.results) < len(self.targets) else None

    def add(self, xy, raw=None):
        target = self.next_target
        if target is None:
            raise ValueError("独立验证点已全部完成")
        xy = points2([xy])[0]
        delta = xy - [target["x_mm"], target["y_mm"]]
        self.results.append(dict(**target, measured_x_mm=float(xy[0]), measured_y_mm=float(xy[1]),
                                 error_x_mm=float(delta[0]), error_y_mm=float(delta[1]),
                                 error_mm=float(np.linalg.norm(delta)), pixel_raw=raw))

    def report(self):
        e = np.array([r["error_mm"] for r in self.results])
        stats = {} if not len(e) else dict(mean_mm=float(e.mean()), rms_mm=float(np.sqrt(np.mean(e**2))),
                                          median_mm=float(np.median(e)), p95_mm=float(np.percentile(e, 95)),
                                          max_mm=float(e.max()))
        return dict(calibration_id=self.calibration_id, count=len(e), expected_count=len(self.targets),
                    complete=self.next_target is None, statistics=stats, points=self.results)

    def export(self, base):
        base = Path(base).with_suffix("")
        report = self.report()
        atomic_text(base.with_suffix(".json"), json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        out = io.StringIO(newline="")
        fields = ["point_id", "x_mm", "y_mm", "measured_x_mm", "measured_y_mm", "error_x_mm", "error_y_mm", "error_mm"]
        fields += ["calibration_id", "mean_mm", "rms_mm", "median_mm", "p95_mm", "max_mm"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for row in self.results:
            writer.writerow({**{k: row[k] for k in fields[:8]}, "calibration_id": self.calibration_id,
                             **report["statistics"]})
        atomic_text(base.with_suffix(".csv"), out.getvalue())
