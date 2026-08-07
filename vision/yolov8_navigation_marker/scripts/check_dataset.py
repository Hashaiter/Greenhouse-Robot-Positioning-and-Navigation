"""Validate an Ultralytics YOLO bounding-box detection dataset."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, UnidentifiedImageError


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
REQUIRED_SPLITS = ("train", "val")


class DatasetCheckError(ValueError):
    """Raised when data.yaml cannot describe a supported dataset."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查Ultralytics YOLO Bounding Box目标检测数据集"
    )
    parser.add_argument("--data", required=True, help="数据集YAML路径")
    parser.add_argument("--max-errors", type=int, default=20)
    parser.add_argument("--report", help="JSON检查报告保存路径")
    return parser.parse_args()


def load_config(data_yaml: Path) -> dict[str, Any]:
    if not data_yaml.is_file():
        raise DatasetCheckError(f"数据集YAML不存在: {data_yaml}")
    try:
        content = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DatasetCheckError(f"无法读取数据集YAML: {exc}") from exc
    if not isinstance(content, dict):
        raise DatasetCheckError("数据集YAML必须是字典")
    missing = [key for key in (*REQUIRED_SPLITS, "names") if key not in content]
    if missing:
        raise DatasetCheckError(f"数据集YAML缺少字段: {missing}")
    return content


def parse_names(value: Any) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    if not isinstance(value, dict) or not value:
        raise DatasetCheckError("names必须是非空列表或字典")
    parsed: dict[int, str] = {}
    for raw_id, name in value.items():
        try:
            class_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise DatasetCheckError(f"非法类别编号: {raw_id!r}") from exc
        parsed[class_id] = str(name)
    expected = list(range(len(parsed)))
    if sorted(parsed) != expected:
        raise DatasetCheckError(
            f"类别编号必须从0连续排列，实际为: {sorted(parsed)}"
        )
    return [parsed[index] for index in expected]


def resolve_root(config: dict[str, Any], data_yaml: Path) -> Path:
    raw = config.get("path", ".")
    if not isinstance(raw, str):
        raise DatasetCheckError("path必须是字符串")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = data_yaml.parent / path
    return path.resolve()


def resolve_split(value: Any, root: Path, split: str) -> Path:
    if not isinstance(value, str):
        raise DatasetCheckError(f"{split}必须是单个图片目录字符串")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def label_directory(image_directory: Path) -> Path:
    parts = list(image_directory.parts)
    indexes = [index for index, part in enumerate(parts) if part.lower() == "images"]
    if not indexes:
        raise DatasetCheckError(
            f"图片路径中缺少images目录，无法推导labels目录: {image_directory}"
        )
    parts[indexes[-1]] = "labels"
    return Path(*parts)


def validate_label(
    path: Path, class_count: int
) -> tuple[int, Counter[int], list[str], bool]:
    errors: list[str] = []
    counts: Counter[int] = Counter()
    objects = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return 0, counts, [f"无法读取标签 {path}: {exc}"], False
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty:
        return 0, counts, [], True
    for number, line in enumerate(nonempty, start=1):
        fields = line.split()
        prefix = f"{path}:{number}"
        if len(fields) != 5:
            errors.append(f"{prefix}: 应为5个字段，实际{len(fields)}个")
            continue
        try:
            class_id = int(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except ValueError:
            errors.append(f"{prefix}: 类别或坐标无法解析")
            continue
        if not 0 <= class_id < class_count:
            errors.append(f"{prefix}: class_id={class_id}超出类别范围")
            continue
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in coordinates):
            errors.append(f"{prefix}: 坐标必须在0到1之间")
            continue
        if coordinates[2] <= 0.0 or coordinates[3] <= 0.0:
            errors.append(f"{prefix}: width和height必须大于0")
            continue
        objects += 1
        counts[class_id] += 1
    return objects, counts, errors, False


def check_split(
    split: str, image_dir: Path, class_count: int
) -> tuple[dict[str, Any], Counter[int], list[str]]:
    stats: dict[str, Any] = {
        "image_dir": str(image_dir),
        "label_dir": None,
        "images": 0,
        "objects": 0,
        "missing_labels": 0,
        "empty_labels": 0,
        "invalid_labels": 0,
        "broken_images": 0,
        "orphan_labels": 0,
    }
    counts: Counter[int] = Counter()
    errors: list[str] = []
    if not image_dir.is_dir():
        return stats, counts, [f"{split}图片目录不存在: {image_dir}"]
    try:
        label_dir = label_directory(image_dir)
    except DatasetCheckError as exc:
        return stats, counts, [str(exc)]
    stats["label_dir"] = str(label_dir)
    images = sorted(
        path
        for path in image_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    stats["images"] = len(images)
    if not images:
        errors.append(f"{split}中没有支持的图片: {image_dir}")
    expected_labels: set[Path] = set()
    for image_path in images:
        relative = image_path.relative_to(image_dir)
        label_path = label_dir / relative.with_suffix(".txt")
        expected_labels.add(label_path)
        try:
            with Image.open(image_path) as image:
                image.verify()
        except (OSError, SyntaxError, UnidentifiedImageError) as exc:
            stats["broken_images"] += 1
            errors.append(f"损坏图片 {image_path}: {exc}")
        if not label_path.is_file():
            stats["missing_labels"] += 1
            errors.append(f"图片缺少标签: {image_path} -> {label_path}")
            continue
        objects, label_counts, label_errors, empty = validate_label(
            label_path, class_count
        )
        stats["objects"] += objects
        counts.update(label_counts)
        if empty:
            stats["empty_labels"] += 1
        stats["invalid_labels"] += len(label_errors)
        errors.extend(label_errors)
    if label_dir.is_dir():
        for path in sorted(label_dir.rglob("*.txt")):
            if path not in expected_labels:
                stats["orphan_labels"] += 1
                errors.append(f"孤立标签没有对应图片: {path}")
    return stats, counts, errors


def build_report(data_yaml: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "passed": False,
        "data_yaml": str(data_yaml),
        "classes": [],
        "splits": {},
        "total_images": 0,
        "total_objects": 0,
        "class_counts": {},
        "errors": [],
    }
    try:
        config = load_config(data_yaml)
        names = parse_names(config["names"])
        report["classes"] = names
        root = resolve_root(config, data_yaml)
        total_counts: Counter[int] = Counter()
        split_names = [*REQUIRED_SPLITS]
        if config.get("test") not in (None, ""):
            split_names.append("test")
        for split in split_names:
            image_dir = resolve_split(config[split], root, split)
            stats, counts, errors = check_split(split, image_dir, len(names))
            report["splits"][split] = stats
            total_counts.update(counts)
            report["errors"].extend(errors)
        report["total_images"] = sum(
            item["images"] for item in report["splits"].values()
        )
        report["total_objects"] = sum(
            item["objects"] for item in report["splits"].values()
        )
        report["class_counts"] = {
            str(index): total_counts[index] for index in range(len(names))
        }
        report["passed"] = not report["errors"]
    except DatasetCheckError as exc:
        report["errors"].append(str(exc))
    return report


def print_report(report: dict[str, Any], max_errors: int) -> None:
    print(f"\n数据集检查{'通过' if report['passed'] else '失败'}")
    print(f"类别: {', '.join(report['classes']) or '(未解析)'}")
    for split in ("train", "val", "test"):
        stats = report["splits"].get(split)
        if stats:
            print(
                f"{split}: {stats['images']}张图片，"
                f"{stats['objects']}个目标，"
                f"{stats['empty_labels']}个空标签"
            )
    print(f"总图片: {report['total_images']}")
    print(f"总目标: {report['total_objects']}")
    errors = report["errors"]
    if errors:
        print(f"错误（显示{min(len(errors), max_errors)}/{len(errors)}条）:")
        for error in errors[:max_errors]:
            print(f"- {error}")


def main() -> int:
    args = parse_args()
    if args.max_errors < 0:
        print("--max-errors必须大于等于0", file=sys.stderr)
        return 2
    data_yaml = Path(args.data).expanduser().resolve()
    report = build_report(data_yaml)
    print_report(report, args.max_errors)
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"检查报告: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
