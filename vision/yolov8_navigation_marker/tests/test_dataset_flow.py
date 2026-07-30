"""Synthetic smoke tests for conversion and dataset checking."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import yaml
from PIL import Image


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DatasetFlowTest(unittest.TestCase):
    def test_darknet_zip_can_be_prepared_and_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for split in ("train", "valid", "test"):
                split_dir = source / split
                split_dir.mkdir(parents=True)
                (split_dir / "_darknet.labels").write_text(
                    "label\n", encoding="utf-8"
                )
                image = Image.new("RGB", (64, 64), (220, 220, 220))
                image.save(split_dir / f"{split}_sample.jpg")
                (split_dir / f"{split}_sample.txt").write_text(
                    "0 0.5 0.5 0.25 0.25\n", encoding="utf-8"
                )
            archive_path = root / "fixture.zip"
            with ZipFile(archive_path, "w") as archive:
                for path in source.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(source).as_posix())

            output = root / "dataset"
            config = root / "data.yaml"
            prepare_report = root / "prepare.json"
            check_report = root / "check.json"
            prepare = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "prepare_dataset.py"),
                    "--input",
                    str(archive_path),
                    "--output",
                    str(output),
                    "--config",
                    str(config),
                    "--report",
                    str(prepare_report),
                    "--class-name",
                    "navigation_marker",
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
            check = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "check_dataset.py"),
                    "--data",
                    str(config),
                    "--report",
                    str(check_report),
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertTrue((output / "images" / "val" / "valid_sample.jpg").is_file())
            self.assertTrue((output / "labels" / "val" / "valid_sample.txt").is_file())
            self.assertFalse((output / "labels" / "val" / "_darknet.labels").exists())
            self.assertTrue(prepare_report.is_file())
            self.assertTrue(check_report.is_file())
            config_content = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(Path(config_content["path"]), output.resolve())


if __name__ == "__main__":
    unittest.main()
