from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class RegressionRunnerTestCase(unittest.TestCase):
    def test_regression_script_supports_dataset_directory(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset_dir = repo_root / "evaluation" / "datasets"
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "regression_dir"
            command = [
                "python3",
                str(repo_root / "scripts" / "run_regression.py"),
                "--mode",
                "mock",
                "--dataset",
                str(dataset_dir),
                "--output-dir",
                str(output_dir),
                "--min-pass-rate",
                "0.9",
                "--fail-on-errors",
            ]
            completed = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())

    def test_regression_script_generates_latest_reports(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset = repo_root / "evaluation" / "dataset.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "regression"
            database_path = Path(tmp_dir) / "regression.db"
            command = [
                "python3",
                str(repo_root / "scripts" / "run_regression.py"),
                "--mode",
                "mock",
                "--dataset",
                str(dataset),
                "--output-dir",
                str(output_dir),
                "--database-path",
                str(database_path),
                "--fail-on-errors",
            ]
            completed = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())


if __name__ == "__main__":
    unittest.main()
