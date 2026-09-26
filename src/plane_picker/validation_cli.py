import argparse
import csv
import json
from pathlib import Path
import sys

from .calibration.validator import MappingValidator
from .calibration.homography import PlaneMapper
from .models.calibration import PlaneCalibration


def main(argv=None):
    parser = argparse.ArgumentParser(description="使用独立实测点验证平面映射")
    parser.add_argument("--calibration",required=True,type=Path)
    parser.add_argument("--points",required=True,type=Path,help="CSV: point_id,x_mm,y_mm")
    parser.add_argument("--clicks",required=True,type=Path,help="CSV: point_id,u_raw,v_raw")
    parser.add_argument("--output",type=Path,default=Path("validation_report"))
    args = parser.parse_args(argv)
    try:
        mapper = PlaneMapper(PlaneCalibration.load(args.calibration))
        validator = MappingValidator.load(args.points,mapper.calibration.calibration_id)
        with args.clicks.open(encoding="utf-8-sig",newline="") as f:
            rows = list(csv.DictReader(f))
        clicks = {r["point_id"]:[float(r["u_raw"]),float(r["v_raw"])] for r in rows}
        if len(clicks) != len(rows) or set(clicks) != {t["point_id"] for t in validator.targets}:
            raise ValueError("点击 ID 必须与验证点 ID 一一对应，不能重复、缺失或多余")
        for target in validator.targets:
            raw = clicks[target["point_id"]]
            _,xy = mapper.map_raw(raw)
            validator.add(xy,raw)
        validator.export(args.output)
        print(json.dumps(validator.report()["statistics"],ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,OSError,TypeError) as exc:
        print(f"验证失败: {exc}",file=sys.stderr)
        return 2
