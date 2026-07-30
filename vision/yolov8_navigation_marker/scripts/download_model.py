"""Download the official YOLOv8n pretrained weight from ModelScope."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


MODEL_ID = "AI-ModelScope/YOLOv8"
FILE_NAME = "yolov8n.pt"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "models" / "pretrained" / FILE_NAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从ModelScope下载YOLOv8n预训练权重"
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"权重输出路径，默认 {DEFAULT_OUTPUT}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = Path(args.output).expanduser().resolve()
    if target.suffix.lower() != ".pt":
        raise ValueError(f"权重输出必须是.pt文件: {target}")
    if target.is_file() and target.stat().st_size > 0:
        print(f"{FILE_NAME}已存在，跳过下载: {target}")
        return 0
    if target.exists() and not target.is_file():
        raise ValueError(f"权重输出路径不是文件: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "modelscope.cli.cli",
        "download",
        f"--model={MODEL_ID}",
        "--local_dir",
        str(target.parent),
        FILE_NAME,
    ]
    print(f"开始从ModelScope下载{FILE_NAME}...")
    process = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        shell=False,
    )
    if process.stdout:
        print(process.stdout)
    if process.returncode != 0:
        if process.stderr:
            print(process.stderr, file=sys.stderr)
        raise RuntimeError(
            f"权重下载失败，退出码{process.returncode}；请检查网络和modelscope安装"
        )
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"下载结束但权重不存在或为空: {target}")
    print(f"MODEL_PT={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
