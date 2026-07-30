"""Run Darknet conversion, checking, YOLOv8 training, validation, and inference."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
DEFAULT_DATA = REPOSITORY_ROOT / "configs" / "greenhouse_robot.yaml"
DEFAULT_DATASET_OUTPUT = REPOSITORY_ROOT / "datasets" / "greenhouse_robot"
DEFAULT_MODEL = REPOSITORY_ROOT / "models" / "pretrained" / "yolov8n.pt"
BEST_RE = re.compile(r"^BEST_PT=(.+)$", re.MULTILINE)
LAST_RE = re.compile(r"^LAST_PT=(.*)$", re.MULTILINE)


class PipelineError(RuntimeError):
    """Failure in a named pipeline step."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="一键运行大创YOLOv8目标检测Pipeline"
    )
    parser.add_argument(
        "--dataset-zip",
        help="可选：Roboflow Darknet ZIP；提供后先自动转换",
    )
    parser.add_argument(
        "--dataset-output",
        default=str(DEFAULT_DATASET_OUTPUT),
        help="转换后的标准数据集目录",
    )
    parser.add_argument(
        "--overwrite-dataset",
        action="store_true",
        help="重新转换并备份已有数据集",
    )
    parser.add_argument(
        "--class-name",
        default="navigation_marker",
        help="目标类别正式名称",
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", default=str(REPOSITORY_ROOT / "runs"))
    parser.add_argument("--name", default="greenhouse_yolov8")
    parser.add_argument("--source", help="可选：训练后推理的图片、目录、视频或URL")
    parser.add_argument("--skip-data-check", action="store_true")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--summary", help="Pipeline摘要JSON")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_from_repository(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path.resolve()


def format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def run_step(step: str, command: Sequence[str]) -> str:
    command = [str(item) for item in command]
    print(f"\n===== {step} =====", flush=True)
    print(f"命令: {format_command(command)}", flush=True)
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(REPOSITORY_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=environment,
        shell=False,
    )
    assert process.stdout is not None
    lines: list[str] = []
    for line in process.stdout:
        lines.append(line)
        print(line, end="", flush=True)
    process.stdout.close()
    returncode = process.wait()
    output = "".join(lines)
    if returncode != 0:
        raise PipelineError(
            step,
            f"步骤{step}失败，退出码{returncode}；命令: {format_command(command)}",
        )
    return output


def validate_args(args: argparse.Namespace) -> None:
    if args.epochs <= 0 or args.batch <= 0 or args.imgsz <= 0:
        raise PipelineError("preflight", "epochs、batch和imgsz必须大于0")
    if args.workers < 0 or args.patience < 0:
        raise PipelineError("preflight", "workers和patience必须大于等于0")
    if not is_safe_run_name(args.name):
        raise PipelineError("preflight", "name必须是安全的单个目录名")


def is_safe_run_name(value: str) -> bool:
    return not (
        not value.strip()
        or value in {".", ".."}
        or Path(value).is_absolute()
        or "/" in value
        or "\\" in value
        or ":" in value
    )


def parse_marker(output: str, pattern: re.Pattern[str]) -> str | None:
    matches = pattern.findall(output)
    if not matches:
        return None
    value = matches[-1].strip()
    return value or None


def main() -> int:
    args = parse_args()
    started_at = utc_now()
    summary_name = args.name if is_safe_run_name(args.name) else "pipeline_invalid_name"
    summary_path = (
        resolve_from_repository(args.summary)
        if args.summary
        else (
            REPOSITORY_ROOT
            / "outputs"
            / "pipeline"
            / summary_name
            / "pipeline_summary.json"
        ).resolve()
    )
    summary: dict[str, object] = {
        "success": False,
        "started_at": started_at,
        "finished_at": None,
        "completed_steps": [],
        "skipped_steps": [],
        "failed_step": None,
        "error": None,
        "data": str(resolve_from_repository(args.data)),
        "model": str(resolve_from_repository(args.model)),
        "best_pt": None,
        "last_pt": None,
        "dataset_report": None,
        "validation_report": None,
        "step_outputs": {},
    }

    try:
        validate_args(args)
        data_path = resolve_from_repository(args.data)
        model_path = resolve_from_repository(args.model)
        output_dir = summary_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        if args.dataset_zip:
            prepare_command = [
                sys.executable,
                str(SCRIPTS_DIRECTORY / "prepare_dataset.py"),
                "--input",
                str(resolve_from_repository(args.dataset_zip)),
                "--output",
                str(resolve_from_repository(args.dataset_output)),
                "--config",
                str(data_path),
                "--class-name",
                args.class_name,
                "--report",
                str(output_dir / "dataset_prepare_report.json"),
            ]
            if args.overwrite_dataset:
                prepare_command.append("--overwrite")
            summary["step_outputs"]["prepare_dataset"] = run_step(
                "prepare_dataset", prepare_command
            )
            summary["completed_steps"].append("prepare_dataset")
        else:
            summary["skipped_steps"].append("prepare_dataset")

        if not data_path.is_file():
            raise PipelineError("preflight", f"数据集YAML不存在: {data_path}")

        if args.skip_data_check:
            summary["skipped_steps"].append("dataset_check")
        else:
            dataset_report = output_dir / "dataset_check_report.json"
            check_command = [
                sys.executable,
                str(SCRIPTS_DIRECTORY / "check_dataset.py"),
                "--data",
                str(data_path),
                "--report",
                str(dataset_report),
            ]
            summary["step_outputs"]["dataset_check"] = run_step(
                "dataset_check", check_command
            )
            summary["dataset_report"] = str(dataset_report)
            summary["completed_steps"].append("dataset_check")

        if not model_path.is_file() or model_path.stat().st_size == 0:
            if model_path.name.lower() != "yolov8n.pt":
                raise PipelineError(
                    "model_prepare",
                    f"自定义模型不存在，自动下载只支持yolov8n.pt: {model_path}",
                )
            download_command = [
                sys.executable,
                str(SCRIPTS_DIRECTORY / "download_model.py"),
                "--output",
                str(model_path),
            ]
            summary["step_outputs"]["model_prepare"] = run_step(
                "model_prepare", download_command
            )
        else:
            summary["step_outputs"]["model_prepare"] = (
                f"使用已有模型: {model_path}\n"
            )
        summary["completed_steps"].append("model_prepare")

        train_command = [
            sys.executable,
            str(SCRIPTS_DIRECTORY / "train.py"),
            "--model",
            str(model_path),
            "--data",
            str(data_path),
            "--epochs",
            str(args.epochs),
            "--batch",
            str(args.batch),
            "--imgsz",
            str(args.imgsz),
            "--workers",
            str(args.workers),
            "--patience",
            str(args.patience),
            "--seed",
            str(args.seed),
            "--project",
            str(resolve_from_repository(args.project)),
            "--name",
            args.name,
        ]
        if args.device:
            train_command.extend(["--device", args.device])
        train_output = run_step("train", train_command)
        summary["step_outputs"]["train"] = train_output
        best_value = parse_marker(train_output, BEST_RE)
        last_value = parse_marker(train_output, LAST_RE)
        if best_value is None:
            raise PipelineError("train", "训练成功但没有返回BEST_PT")
        best_path = Path(best_value).resolve()
        if not best_path.is_file() or best_path.stat().st_size == 0:
            raise PipelineError("train", f"BEST_PT不存在或为空: {best_path}")
        summary["best_pt"] = str(best_path)
        summary["last_pt"] = last_value
        summary["completed_steps"].append("train")

        if args.skip_validate:
            summary["skipped_steps"].append("validate")
        else:
            validation_report = output_dir / "validation_metrics.json"
            validate_command = [
                sys.executable,
                str(SCRIPTS_DIRECTORY / "validate.py"),
                "--weights",
                str(best_path),
                "--data",
                str(data_path),
                "--imgsz",
                str(args.imgsz),
                "--batch",
                str(args.batch),
                "--project",
                str(resolve_from_repository(args.project)),
                "--name",
                f"{args.name}_val",
                "--report",
                str(validation_report),
            ]
            if args.device:
                validate_command.extend(["--device", args.device])
            summary["step_outputs"]["validate"] = run_step(
                "validate", validate_command
            )
            summary["validation_report"] = str(validation_report)
            summary["completed_steps"].append("validate")

        if args.source:
            predict_command = [
                sys.executable,
                str(SCRIPTS_DIRECTORY / "predict.py"),
                "--weights",
                str(best_path),
                "--source",
                args.source,
                "--imgsz",
                str(args.imgsz),
                "--project",
                str(resolve_from_repository(args.project)),
                "--name",
                f"{args.name}_predict",
            ]
            if args.device:
                predict_command.extend(["--device", args.device])
            summary["step_outputs"]["predict"] = run_step(
                "predict", predict_command
            )
            summary["completed_steps"].append("predict")
        else:
            summary["skipped_steps"].append("predict")
        summary["success"] = True
    except PipelineError as exc:
        summary["failed_step"] = exc.step
        summary["error"] = str(exc)
    except KeyboardInterrupt:
        summary["failed_step"] = "interrupted"
        summary["error"] = "用户中断Pipeline"
    except Exception as exc:
        summary["failed_step"] = "pipeline"
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        summary["finished_at"] = utc_now()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if summary["success"]:
        print("\nPipeline执行成功")
        print(f"best.pt: {summary['best_pt']}")
        print(f"摘要: {summary_path}")
        return 0
    print("\nPipeline执行失败")
    print(f"失败步骤: {summary['failed_step']}")
    print(f"原因: {summary['error']}")
    print(f"摘要: {summary_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
