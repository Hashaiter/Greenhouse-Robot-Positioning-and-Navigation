"""Validate a trained YOLOv8 detector and save core metrics as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLOv8目标检测验证")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="")
    parser.add_argument("--project", default=str(REPOSITORY_ROOT / "runs"))
    parser.add_argument("--name", default="greenhouse_val")
    parser.add_argument("--report", help="核心指标JSON报告")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    weights = Path(args.weights).expanduser().resolve()
    data = Path(args.data).expanduser().resolve()
    if not weights.is_file():
        raise FileNotFoundError(f"权重不存在: {weights}")
    if not data.is_file():
        raise FileNotFoundError(f"数据集YAML不存在: {data}")
    if args.batch <= 0 or args.imgsz <= 0:
        raise ValueError("batch和imgsz必须大于0")

    model = YOLO(str(weights))
    metrics = model.val(
        data=str(data),
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device if args.device else None,
        project=str(Path(args.project).expanduser()),
        name=args.name,
        plots=True,
        task="detect",
    )
    report = {
        "weights": str(weights),
        "data": str(data),
        "precision": float(metrics.box.mp),
        "recall": float(metrics.box.mr),
        "map50": float(metrics.box.map50),
        "map50_95": float(metrics.box.map),
        "save_dir": str(Path(metrics.save_dir).resolve()),
    }
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"METRICS_JSON={report_path}")
    print("\n验证完成")
    print(f"Precision: {report['precision']:.4f}")
    print(f"Recall: {report['recall']:.4f}")
    print(f"mAP50: {report['map50']:.4f}")
    print(f"mAP50-95: {report['map50_95']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
