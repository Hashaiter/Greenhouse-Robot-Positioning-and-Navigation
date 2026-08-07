"""Train a YOLOv8 object detector from a local pretrained weight."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from ultralytics import YOLO


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = REPOSITORY_ROOT / "models" / "pretrained" / "yolov8n.pt"
DEFAULT_PROJECT = REPOSITORY_ROOT / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLOv8目标检测训练")
    parser.add_argument("--data", required=True, help="Ultralytics数据集YAML")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="本地.pt预训练权重")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="", help="GPU示例0，CPU示例cpu，留空自动")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--name", default="greenhouse_train")
    return parser.parse_args()


def validate_data_yaml(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"数据集YAML不存在: {path}")
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(content, dict):
        raise ValueError(f"数据集YAML必须是字典: {path}")
    missing = [key for key in ("train", "val", "names") if key not in content]
    if missing:
        raise ValueError(f"数据集YAML缺少字段{missing}: {path}")


def main() -> int:
    args = parse_args()
    if args.epochs <= 0 or args.batch <= 0 or args.imgsz <= 0:
        raise ValueError("epochs、batch和imgsz必须大于0")
    if args.workers < 0 or args.patience < 0:
        raise ValueError("workers和patience必须大于等于0")

    model_path = Path(args.model).expanduser().resolve()
    data_path = Path(args.data).expanduser().resolve()
    if model_path.suffix.lower() != ".pt" or not model_path.is_file():
        raise FileNotFoundError(
            f"本地YOLOv8权重不存在: {model_path}\n"
            "请先运行 scripts/download_model.py"
        )
    validate_data_yaml(data_path)

    model = YOLO(str(model_path))
    results = model.train(
        data=str(data_path),
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        device=args.device if args.device else None,
        workers=args.workers,
        patience=args.patience,
        seed=args.seed,
        deterministic=True,
        project=str(Path(args.project).expanduser()),
        name=args.name,
        task="detect",
    )
    save_dir = Path(results.save_dir).resolve()
    best = save_dir / "weights" / "best.pt"
    last = save_dir / "weights" / "last.pt"
    if not best.is_file() or best.stat().st_size == 0:
        raise RuntimeError(f"训练结束但未找到有效best.pt: {best}")
    print("\n训练完成")
    print(f"BEST_PT={best}")
    print(f"LAST_PT={last if last.is_file() else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
