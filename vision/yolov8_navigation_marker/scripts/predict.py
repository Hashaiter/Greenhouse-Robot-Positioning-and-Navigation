"""Run YOLOv8 inference on an image, directory, video, or camera source."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLO


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLOv8目标检测推理")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default="")
    parser.add_argument("--project", default=str(REPOSITORY_ROOT / "runs"))
    parser.add_argument("--name", default="greenhouse_predict")
    parser.add_argument("--no-save", action="store_true")
    return parser.parse_args()


def is_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def is_camera_index(value: str) -> bool:
    return value.isdigit()


def main() -> int:
    args = parse_args()
    weights = Path(args.weights).expanduser().resolve()
    if not weights.is_file():
        raise FileNotFoundError(f"权重不存在: {weights}")
    if not 0.0 <= args.conf <= 1.0:
        raise ValueError("conf必须在0到1之间")
    source: str | int = args.source
    if is_camera_index(args.source):
        source = int(args.source)
    elif not is_url(args.source):
        source_path = Path(args.source).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"推理输入不存在: {source_path}")
        source = str(source_path)

    model = YOLO(str(weights))
    results = model.predict(
        source=source,
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device if args.device else None,
        project=str(Path(args.project).expanduser()),
        name=args.name,
        save=not args.no_save,
        task="detect",
    )
    detections = sum(
        len(result.boxes) for result in results if result.boxes is not None
    )
    print(f"\n推理完成，共处理{len(results)}帧/张，检测到{detections}个目标")
    if results and not args.no_save:
        print(f"PREDICT_DIR={Path(results[0].save_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
