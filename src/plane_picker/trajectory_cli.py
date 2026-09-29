import argparse
import json

from .trajectory import TrajectoryConfig, process_project


def main(argv=None):
    parser = argparse.ArgumentParser(description="长曝光飞镖轨迹批量增强")
    parser.add_argument("root", help="包含 dart_1、dart_2 和 _background 的项目目录")
    parser.add_argument("--gain", type=float, default=4.0, help="轨迹增强倍数")
    parser.add_argument("--noise-multiplier", type=float, default=5.0,
                        help="背景噪声阈值倍数")
    parser.add_argument("--minimum-area", type=int, default=20,
                        help="保留连通区域的最小像素数")
    args = parser.parse_args(argv)
    config = TrajectoryConfig(
        enhancement_gain=args.gain,
        noise_multiplier=args.noise_multiplier,
        minimum_area_px=args.minimum_area,
    )
    manifest = process_project(args.root, config)
    print(json.dumps({
        "run_id": manifest["run_id"],
        "completed": manifest["completed"],
        "failed": manifest["failed"],
    }, ensure_ascii=False))
    return 0 if manifest["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
