"""Convert a Roboflow Darknet detection export to Ultralytics YOLO layout."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from statistics import median
from typing import BinaryIO, Iterable
from zipfile import BadZipFile, ZipFile, ZipInfo

import yaml
from PIL import Image, UnidentifiedImageError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "datasets" / "greenhouse_robot"
DEFAULT_CONFIG = REPOSITORY_ROOT / "configs" / "greenhouse_robot.yaml"
DEFAULT_REPORT = REPOSITORY_ROOT / "outputs" / "dataset_prepare_report.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLIT_ALIASES = {
    "train": ("train",),
    "val": ("valid", "val"),
    "test": ("test",),
}
TIMESTAMP_RE = re.compile(
    r"^(?P<source>output\d+)-mp4_"
    r"(?P<date>\d{8})_(?P<time>\d{6})-(?P<millis>\d{3})_jpg\.rf\."
)


class DatasetPreparationError(RuntimeError):
    """Raised when the source export cannot be converted safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将Roboflow Darknet压缩包转换为Ultralytics YOLO目标检测数据集"
    )
    parser.add_argument("--input", required=True, help="Darknet ZIP压缩包或已解压目录")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"标准数据集输出目录，默认 {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"生成的数据集YAML，默认 {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help=f"转换报告JSON，默认 {DEFAULT_REPORT}",
    )
    parser.add_argument(
        "--class-name",
        default="navigation_marker",
        help="单类别数据集的正式类别名，默认navigation_marker",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="输出目录已存在时，用新数据替换并保留时间戳备份",
    )
    return parser.parse_args()


def resolve_from_repository(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path.resolve()


def ensure_safe_output(target: Path) -> None:
    forbidden = {
        Path(target.anchor).resolve(),
        REPOSITORY_ROOT.resolve(),
        REPOSITORY_ROOT.parent.resolve(),
    }
    if target.resolve() in forbidden:
        raise DatasetPreparationError(f"拒绝使用过宽的输出目录: {target}")
    if target.name in {"", ".", ".."}:
        raise DatasetPreparationError(f"非法输出目录: {target}")


def safe_zip_members(archive: ZipFile) -> list[ZipInfo]:
    members: list[ZipInfo] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        pure = PurePosixPath(info.filename)
        if pure.is_absolute() or ".." in pure.parts or "\\" in info.filename:
            raise DatasetPreparationError(f"ZIP包含不安全路径: {info.filename}")
        if not pure.parts:
            continue
        members.append(info)
    return members


class SourceFiles:
    """Uniform read access for either a ZIP archive or an extracted directory."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self.archive: ZipFile | None = None
        self.names: list[str] = []

    def __enter__(self) -> "SourceFiles":
        if self.source.is_file():
            try:
                self.archive = ZipFile(self.source)
                self.names = [info.filename for info in safe_zip_members(self.archive)]
            except BadZipFile as exc:
                raise DatasetPreparationError(f"不是有效ZIP压缩包: {self.source}") from exc
        elif self.source.is_dir():
            self.names = [
                path.relative_to(self.source).as_posix()
                for path in self.source.rglob("*")
                if path.is_file()
            ]
        else:
            raise DatasetPreparationError(f"输入不存在: {self.source}")
        return self

    def __exit__(self, *_: object) -> None:
        if self.archive is not None:
            self.archive.close()

    def open_binary(self, name: str) -> BinaryIO:
        if self.archive is not None:
            return self.archive.open(name, "r")
        return (self.source / PurePosixPath(name)).open("rb")

    def read_text(self, name: str) -> str:
        with self.open_binary(name) as stream:
            return stream.read().decode("utf-8-sig")


def find_source_split(source: SourceFiles, aliases: Iterable[str]) -> str | None:
    top_levels = {PurePosixPath(name).parts[0] for name in source.names}
    for alias in aliases:
        if alias in top_levels:
            return alias
    return None


def files_in_split(source: SourceFiles, split: str) -> list[str]:
    result: list[str] = []
    for name in source.names:
        pure = PurePosixPath(name)
        if len(pure.parts) == 2 and pure.parts[0] == split:
            result.append(name)
    return sorted(result)


def read_source_classes(source: SourceFiles, split_names: Iterable[str]) -> list[str]:
    discovered: list[list[str]] = []
    for split in split_names:
        label_name = f"{split}/_darknet.labels"
        if label_name not in source.names:
            continue
        names = [line.strip() for line in source.read_text(label_name).splitlines() if line.strip()]
        if names:
            discovered.append(names)
    if not discovered:
        raise DatasetPreparationError("没有找到任何 _darknet.labels 类别文件")
    if any(names != discovered[0] for names in discovered[1:]):
        raise DatasetPreparationError("不同数据划分的 _darknet.labels 内容不一致")
    return discovered[0]


def check_label_text(
    text: str, label_name: str, class_count: int
) -> tuple[int, Counter[int], list[str]]:
    objects = 0
    counts: Counter[int] = Counter()
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        prefix = f"{label_name}:{line_number}"
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
            errors.append(f"{prefix}: class_id={class_id} 超出0..{class_count - 1}")
            continue
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in coordinates):
            errors.append(f"{prefix}: 坐标必须是0到1之间的有限数值")
            continue
        if coordinates[2] <= 0.0 or coordinates[3] <= 0.0:
            errors.append(f"{prefix}: width和height必须大于0")
            continue
        objects += 1
        counts[class_id] += 1
    return objects, counts, errors


def copy_stream(source: SourceFiles, source_name: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open_binary(source_name) as input_stream, destination.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)


def parse_timestamp(name: str) -> tuple[str, float] | None:
    match = TIMESTAMP_RE.match(name)
    if not match:
        return None
    value = (
        match.group("date")
        + match.group("time")
        + match.group("millis")
    )
    timestamp = datetime.strptime(value, "%Y%m%d%H%M%S%f").timestamp()
    return match.group("source"), timestamp


def nearest_distance(value: float, candidates: list[float]) -> float | None:
    if not candidates:
        return None
    return min(abs(value - candidate) for candidate in candidates)


def analyze_temporal_leakage(
    times: dict[str, dict[str, list[float]]]
) -> dict[str, object]:
    sources: dict[str, object] = {}
    total_evaluation = 0
    within_one_second = 0
    for source_name, by_split in sorted(times.items()):
        training = by_split.get("train", [])
        source_result: dict[str, object] = {}
        for split in ("val", "test"):
            distances = [
                distance
                for value in by_split.get(split, [])
                if (distance := nearest_distance(value, training)) is not None
            ]
            total_evaluation += len(distances)
            within = sum(distance <= 1.0 for distance in distances)
            within_one_second += within
            source_result[split] = {
                "count": len(distances),
                "within_1_second": within,
                "within_2_seconds": sum(distance <= 2.0 for distance in distances),
                "median_nearest_train_seconds": (
                    round(median(distances), 3) if distances else None
                ),
            }
        sources[source_name] = source_result
    ratio = within_one_second / total_evaluation if total_evaluation else 0.0
    return {
        "sources": sources,
        "evaluation_frames": total_evaluation,
        "within_1_second_of_train": within_one_second,
        "within_1_second_ratio": round(ratio, 4),
        "warning": (
            "验证/测试帧大量接近训练帧，当前划分可能高估泛化性能"
            if ratio >= 0.5
            else None
        ),
    }


def convert_dataset(
    source: SourceFiles,
    staging: Path,
    class_names: list[str],
) -> dict[str, object]:
    split_report: dict[str, object] = {}
    temporal: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    total_class_counts: Counter[int] = Counter()
    total_images = 0
    total_objects = 0
    errors: list[str] = []

    resolved_splits: dict[str, str] = {}
    for target_split, aliases in SPLIT_ALIASES.items():
        source_split = find_source_split(source, aliases)
        if target_split in {"train", "val"} and source_split is None:
            raise DatasetPreparationError(
                f"缺少必需划分 {target_split}，可接受源目录: {aliases}"
            )
        if source_split is not None:
            resolved_splits[target_split] = source_split

    for target_split, source_split in resolved_splits.items():
        members = files_in_split(source, source_split)
        image_members = [
            name
            for name in members
            if PurePosixPath(name).suffix.lower() in IMAGE_SUFFIXES
        ]
        label_members = {
            PurePosixPath(name).stem: name
            for name in members
            if PurePosixPath(name).suffix.lower() == ".txt"
            and PurePosixPath(name).name != "_darknet.labels"
        }
        expected_stems = {PurePosixPath(name).stem for name in image_members}
        missing_labels = sorted(expected_stems - set(label_members))
        orphan_labels = sorted(set(label_members) - expected_stems)
        if missing_labels:
            errors.extend(
                f"{source_split}: 图片缺少标签 {stem}" for stem in missing_labels
            )
        if orphan_labels:
            errors.extend(
                f"{source_split}: 孤立标签 {stem}" for stem in orphan_labels
            )

        objects = 0
        class_counts: Counter[int] = Counter()
        dimensions: Counter[str] = Counter()
        for image_member in image_members:
            pure = PurePosixPath(image_member)
            image_destination = (
                staging / "images" / target_split / pure.name
            )
            copy_stream(source, image_member, image_destination)
            try:
                with Image.open(image_destination) as image:
                    image.verify()
                with Image.open(image_destination) as image:
                    dimensions[f"{image.width}x{image.height}"] += 1
            except (OSError, SyntaxError, UnidentifiedImageError) as exc:
                errors.append(f"损坏图片 {image_member}: {exc}")

            parsed = parse_timestamp(pure.name)
            if parsed:
                source_name, timestamp = parsed
                temporal[source_name][target_split].append(timestamp)

            label_member = label_members.get(pure.stem)
            if label_member is None:
                continue
            label_destination = (
                staging / "labels" / target_split / f"{pure.stem}.txt"
            )
            label_text = source.read_text(label_member)
            label_objects, label_counts, label_errors = check_label_text(
                label_text, label_member, len(class_names)
            )
            errors.extend(label_errors)
            objects += label_objects
            class_counts.update(label_counts)
            copy_stream(source, label_member, label_destination)

        split_report[target_split] = {
            "source_split": source_split,
            "images": len(image_members),
            "labels": len(label_members),
            "objects": objects,
            "class_counts": {
                str(index): class_counts[index]
                for index in range(len(class_names))
            },
            "image_dimensions": dict(dimensions),
            "missing_labels": len(missing_labels),
            "orphan_labels": len(orphan_labels),
        }
        total_images += len(image_members)
        total_objects += objects
        total_class_counts.update(class_counts)

    if errors:
        preview = "\n".join(f"- {error}" for error in errors[:20])
        raise DatasetPreparationError(
            f"源数据检查失败，共{len(errors)}个问题：\n{preview}"
        )

    return {
        "source_classes": class_names,
        "splits": split_report,
        "total_images": total_images,
        "total_objects": total_objects,
        "class_counts": {
            str(index): total_class_counts[index]
            for index in range(len(class_names))
        },
        "temporal_leakage": analyze_temporal_leakage(temporal),
    }


def write_data_yaml(config_path: Path, dataset_root: Path, names: list[str]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        # Ultralytics may otherwise resolve relative dataset paths against the
        # global datasets_dir saved by a different project.
        "path": dataset_root.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {index: name for index, name in enumerate(names)},
    }
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def publish_staging(staging: Path, target: Path, overwrite: bool) -> Path | None:
    backup: Path | None = None
    if target.exists():
        if not overwrite:
            raise DatasetPreparationError(
                f"输出目录已存在: {target}\n"
                "确认要重新转换时请添加 --overwrite；旧目录会被保留为备份。"
            )
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.backup-{timestamp}")
        if backup.exists():
            raise DatasetPreparationError(f"备份目录已存在: {backup}")
        target.rename(backup)
    staging.rename(target)
    return backup


def main() -> int:
    args = parse_args()
    source_path = resolve_from_repository(args.input)
    output_path = resolve_from_repository(args.output)
    config_path = resolve_from_repository(args.config)
    report_path = resolve_from_repository(args.report)
    ensure_safe_output(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_path.name}.staging-",
            dir=output_path.parent,
        )
    )

    try:
        with SourceFiles(source_path) as source:
            source_split_names = [
                split
                for aliases in SPLIT_ALIASES.values()
                if (split := find_source_split(source, aliases)) is not None
            ]
            source_classes = read_source_classes(source, source_split_names)
            if len(source_classes) != 1:
                raise DatasetPreparationError(
                    "当前大创适配器只配置为单类别；"
                    f"源数据实际有{len(source_classes)}类: {source_classes}"
                )
            class_name = args.class_name.strip()
            if not class_name:
                raise DatasetPreparationError("--class-name 不能为空")
            report = convert_dataset(source, staging, [class_name])
            report["source"] = str(source_path)
            report["source_class_name"] = source_classes[0]
            report["configured_class_name"] = class_name

        backup = publish_staging(staging, output_path, args.overwrite)
        write_data_yaml(config_path, output_path, [class_name])
        report["output"] = str(output_path)
        report["config"] = str(config_path)
        report["backup"] = str(backup) if backup else None
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print("\n数据集转换成功")
    print(f"图片数量: {report['total_images']}")
    print(f"目标数量: {report['total_objects']}")
    print(f"类别: {class_name}")
    print(f"数据集目录: {output_path}")
    print(f"配置文件: {config_path}")
    print(f"转换报告: {report_path}")
    leakage = report["temporal_leakage"]
    if isinstance(leakage, dict) and leakage.get("warning"):
        print(f"警告: {leakage['warning']}")
        print(
            "验证/测试帧中距离训练帧不超过1秒的比例: "
            f"{float(leakage['within_1_second_ratio']):.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
