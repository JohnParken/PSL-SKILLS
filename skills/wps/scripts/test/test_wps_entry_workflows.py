"""Exercise entry examples from a read-only standalone copy without test resources."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[2]


class EntryWorkflowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="wps-entry-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.skill = cls.root / "standalone-wps"
        shutil.copytree(SKILL, cls.skill, ignore=shutil.ignore_patterns("test", "__pycache__", "*.pyc"))
        cls.before = cls.fingerprints()
        cls.addClassCleanup(cls.restore_permissions)
        for path in cls.skill.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        cls.skill.chmod(0o555)

    @classmethod
    def restore_permissions(cls):
        cls.skill.chmod(0o755)
        for path in cls.skill.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)

    @classmethod
    def fingerprints(cls):
        return {str(path.relative_to(cls.skill)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in cls.skill.rglob("*") if path.is_file()}

    @classmethod
    def tearDownClass(cls):
        if cls.before != cls.fingerprints():
            raise AssertionError("Standalone installation was modified")

    def setUp(self):
        self.task = Path(tempfile.mkdtemp(prefix="task-", dir=self.root))
        self.environment = dict(os.environ)
        self.environment.pop("ROUTER_CONFIG_VARIABLES", None)
        entry = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        calls = re.findall(r'(?m)^\s*(execute\(command=.*\))\s*$', entry)
        commands = [ast.literal_eval(ast.parse(call, mode='eval').body.keywords[0].value) for call in calls]
        command = next((item for item in commands if 'run_wps_task.py' in item and "<<'JSON'" in item), '')
        match = re.search(r"<<'JSON'\n(.*?)\nJSON", command, re.S)
        self.assertIsNotNone(match, 'The entry must contain the runnable stdin example')
        self.spec = json.loads(match.group(1))

    def cli(self, script, *args, spec=None):
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.skill / "scripts" / script), *args],
            cwd=self.task, env=self.environment, input=None if spec is None else json.dumps(spec),
            text=True, capture_output=True, timeout=20,
        )

    def create(self, name="通知.wps", spec=None):
        process = self.cli("run_wps_task.py", "create", name, "--compact-receipt", spec=spec or self.spec)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_entry_example_and_reading_modes(self):
        for script, argument in (("read_wps.py", "--check"), ("run_wps_task.py", "check")):
            process = self.cli(script, argument)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("environment-ok", process.stdout)
        receipt = self.create()
        self.assertEqual(receipt["publication_outcome"], "published_verified")
        self.assertTrue(receipt["cleanup_complete"])
        self.assertFalse(receipt["partial"])
        self.assertEqual(receipt["output_format"], "rtf")
        self.assertFalse(receipt["native_wps"])
        self.assertFalse(receipt["binary_doc"])
        self.assertEqual(receipt["delivery"]["archive_status"], "failed")
        self.assertNotIn("preview_text", receipt)
        for args in ((), ("--summary",), ("--paragraph-range", "1:20")):
            process = self.cli("read_wps.py", "通知.wps", *args)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["status"], "ok")
            if "--summary" in args:
                self.assertEqual(result["coverage"], "overview_only_no_values_or_text")
                self.assertNotIn("本阶段工作已完成。", process.stdout)
            else:
                self.assertIn("本阶段工作已完成。", process.stdout)

    def test_compact_retains_warning_and_readback_partial(self):
        spec = dict(self.spec, header="已确认的页眉", unsupported_option=True)
        receipt = self.create(spec=spec)
        self.assertTrue(receipt["partial"])
        self.assertEqual(receipt["readback_status"], "partial")
        self.assertGreater(receipt["spec_warning_count"], 0)
        self.assertTrue(receipt["spec_warnings"])
        self.assertTrue(receipt["issues"])
        process = self.cli("read_wps.py", "通知.wps", "--summary")
        self.assertEqual(process.returncode, 3, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "partial")

    def test_existing_target_and_invalid_suffix_fail(self):
        self.create()
        original = (self.task / "通知.wps").read_bytes()
        process = self.cli("run_wps_task.py", "create", "通知.wps", "--compact-receipt", spec=self.spec)
        self.assertEqual(process.returncode, 1)
        self.assertEqual((self.task / "通知.wps").read_bytes(), original)
        self.assertIn("failed", process.stderr)
        process = self.cli("run_wps_task.py", "create", "fake.doc", "--compact-receipt", spec=self.spec)
        self.assertEqual(process.returncode, 2)
        self.assertFalse((self.task / "fake.doc").exists())

    def test_edit_has_no_compact_flag(self):
        process = self.cli("run_wps_edit.py", "text", "--help")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn("--compact-receipt", process.stdout)


if __name__ == "__main__":
    unittest.main()
