"""PPTX registration in the repository's existing check-result adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "tools" / "run_skill_check.py"
SPEC = importlib.util.spec_from_file_location("pptx_test_check_adapter", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class PptxCheckAdapterTests(unittest.TestCase):
    def test_real_environment_check_preserves_child_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pptx-adapter-") as directory:
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(ADAPTER), "pptx", "--task-dir", "."],
                cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=30,
                env={**os.environ, "PATH": ""}, check=False,
            )
            report = json.loads(process.stdout)
            self.assertEqual(process.returncode, 0, report)
            self.assertEqual((report["execution_status"], report["check_status"], report["exit_code"]), ("completed", "passed", 0))
            child = json.loads(report["stdout"])
            self.assertTrue(child["ok"])
            self.assertEqual(child["artifact_validation"], "not_run")
            self.assertEqual(child["self_test"]["structural_validation"], "passed")
            self.assertEqual(report["stderr"], "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_real_failed_check_keeps_both_channels_and_child_exit(self) -> None:
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(ADAPTER), "pptx", "--task-dir", "."],
            cwd=ROOT / "skills" / "pptx", capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        report = json.loads(process.stdout)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["check_status"], "failed")
        self.assertFalse(json.loads(report["stdout"])["ok"])
        self.assertTrue(report["stderr"])

    def test_missing_pptx_success_markers_are_not_accepted(self) -> None:
        self.assertIn("pptx", adapter.SKILLS)
        report = adapter.envelope("pptx")
        child = {"status": "environment-ok", "check_kind": "environment", "artifact_validation": "not_run"}
        report["stdout"] = json.dumps(child)
        self.assertFalse(adapter.check_passed("pptx", report, "environment-ok"))
        child.update(ok=True, command="check", in_memory_export="passed")
        report["stdout"] = json.dumps(child)
        self.assertTrue(adapter.check_passed("pptx", report, "environment-ok"))
        report["stdout_truncated"] = True
        self.assertFalse(adapter.check_passed("pptx", report, "environment-ok"))


if __name__ == "__main__":
    unittest.main()
