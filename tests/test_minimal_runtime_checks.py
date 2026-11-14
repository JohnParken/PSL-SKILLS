"""Repository contracts and read-check behavior, not production host enforcement."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.parsers import expat

ROOT = Path(__file__).resolve().parents[1]
READERS = ("docx", "tl-docx", "xlsx", "et", "ofd", "pptx", "wps", "table-workflow")
AUDIT = r"""
import os, runpy, sys
script, *args = sys.argv[1:]
sys.path.insert(0, os.path.dirname(script))
def guard(event, args):
    if event == 'open':
        flags = args[2] or 0
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise RuntimeError('read check attempted filesystem write')
    if event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.link', 'os.symlink'}:
        raise RuntimeError('read check attempted filesystem mutation')
    if event in {'os.system', 'subprocess.Popen'}:
        raise RuntimeError('read check attempted external command')
sys.addaudithook(guard)
sys.argv = [script] + args
runpy.run_path(script, run_name='__main__')
"""


class MinimalRuntimeChecks(unittest.TestCase):
    """Guard guidance without presenting string checks as semantic enforcement."""

    def test_reading_entries_keep_bounded_diagnostics(self):
        """Keep read-first guidance in each executable document entry."""
        for name in READERS:
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                section = text.split("**最小必要检查：**", 1)[1].split("\n", 1)[0]
                for phrase in ("不先测试写入", "`mount`", "`ro`", "安全", "实际", "不倾倒系统日志"):
                    self.assertIn(phrase, section)
                self.assertIn("顶层", section)

    def test_scriptless_and_lifecycle_are_not_misclassified(self):
        """Do not force execution on guidance or remove lifecycle write checks."""
        bank = (ROOT / "skills/bank-writing/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("纯对话写作无需目录探针或执行环境自检", bank)
        navigation = (ROOT / "skills/document-navigation/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("本技能无脚本，不运行环境命令或写入探针", navigation)
        lifecycle = (ROOT / "skills/artifact-lifecycle/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("继续执行下述写入自检", lifecycle)
        self.assertIn("纯对话和仅读任务不调用本技能", lifecycle)

    def test_project_and_development_standard_keep_rule(self):
        """Keep the maintenance rule synchronized."""
        for relative in ("AGENTS.md", "skills/skill-development-standards/SKILL.md"):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                for phrase in ("最小必要自检与有界环境诊断", "根挂载", "实际", "真实 DeepAgents", "安全门槛"):
                    self.assertIn(phrase, text)

    def test_read_checks_do_not_mutate_files_or_probe_system(self):
        """Reject filesystem mutations and external commands during read checks."""
        if sys.version_info < (3, 10) or expat.version_info < (2, 6, 0):
            self.skipTest("Reading gates require Python >=3.10 and Expat >=2.6")
        commands = [
            ("docx", "read_docx.py", ["--check"], "read-check-ok"),
            ("et", "et_cli.py", ["run", "--check"], "environment-ok"),
            ("wps", "read_wps.py", ["--check"], "environment-ok"),
            ("ofd", "read_ofd.py", ["--check"], "environment-ok"),
            ("xlsx", "xlsx_cli.py", ["run", "check"], "ready"),
            ("pptx", "pptx.py", ["check"], "environment-ok"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for name, script, args, marker in commands:
                with self.subTest(skill=name):
                    command = [sys.executable, "-I", "-S", "-B", "-c", AUDIT,
                               str(ROOT / "skills" / name / "scripts" / script), *args]
                    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(marker, result.stdout)
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_optional_write_checks_remain_not_requested(self):
        """Exercise explicitly optional write probes without enabling them."""
        commands = [
            ("pptx", "pptx.py", ["check"], "write_probe", "not_run"),
            ("table-workflow", "table_workflow.py", ["check", "--xlsx-root", str(ROOT / "skills/xlsx"),
                                                       "--et-root", str(ROOT / "skills/et")], "task_writable", "not_checked"),
        ]
        if sys.version_info < (3, 12) or expat.version_info < (2, 6, 0):
            self.skipTest("Workflow requires Python >=3.12 and Expat >=2.6")
        with tempfile.TemporaryDirectory() as directory:
            for name, script, args, key, value in commands:
                with self.subTest(skill=name):
                    result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                             str(ROOT / "skills" / name / "scripts" / script), *args],
                                            cwd=directory, capture_output=True, text=True, check=False, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)[key], value)
                    self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
