"""Offline regressions for repository-only WPS/ET/OFD evidence paths."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceMaterialPaths(unittest.TestCase):
    def test_et_manifest_and_frozen_values_match(self):
        base = ROOT / "docs/maintenance/et"
        manifest = json.loads((base / "et-real-samples.json").read_text())["samples"]
        expected = json.loads((base / "real-value-expectations.json").read_text())["samples"]
        self.assertEqual(len(manifest), 4)
        for item in manifest:
            self.assertEqual(item["sha256"], expected[item["filename"]]["sha256"])

    def test_verifier_loads_moved_manifest_without_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-I", "-S", "-B",
                str(ROOT / "skills/et/scripts/test/verify_et_samples.py"), directory],
                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report["samples"]), 4)
        self.assertTrue(all(not item["passed"] for item in report["samples"]))
        self.assertTrue(all("No such file" in item["message"] for item in report["samples"]))

    def test_probe_imports_test_helper_without_downloading(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
            str(ROOT / "tests/et_template_compatibility_probe.py"), "--help"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--download", result.stdout)

    def test_all_moved_evidence_is_valid_json(self):
        count = 0
        for name in ("et", "wps", "ofd"):
            for path in (ROOT / "docs/maintenance" / name).glob("*.json"):
                json.loads(path.read_text())
                count += 1
        self.assertGreater(count, 30)


if __name__ == "__main__":
    unittest.main()
