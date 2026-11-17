"""Regression tests for the exact external interpreter slot, without running it."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_skill

SLOT = "<skill-root>/scripts/.venv/bin/python"


class DeploymentInterpreterTests(unittest.TestCase):
    def scan(self, root, target, *, text=None):
        issues = []
        check_skill._root_resources(text or f"`{target}`", "guide.md", root, issues, False)
        return [item.rule for item in issues]

    def test_exact_missing_slot_is_allowed_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual([], self.scan(root, SLOT))
            self.assertEqual([], self.scan(root, SLOT, text=r'execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -B")'))
            self.assertEqual([], list(root.iterdir()))

    def test_lookalikes_and_missing_package_resources_remain_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for near in (SLOT + "3", SLOT + "/", SLOT + "#", SLOT + "#missing",
                         SLOT + "/child", SLOT.replace("python", "%70ython"),
                         SLOT.replace(".venv", "venv"), "<skill-root>/scripts/missing.py"):
                with self.subTest(near=near):
                    self.assertEqual(["broken-link"], self.scan(root, near))
            for escape in (SLOT.replace(".venv", ".."), SLOT.replace(".venv", "%2e%2e")):
                self.assertEqual(["link-escape"], self.scan(root, escape))

    def test_intermediate_and_leaf_symlinks_are_not_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for i, rel in enumerate(("scripts", "scripts/.venv", "scripts/.venv/bin/python")):
                root = base / str(i)
                link = root / rel
                link.parent.mkdir(parents=True)
                link.symlink_to(base / "outside")
                with self.subTest(rel=rel):
                    self.assertEqual(["link-escape"], self.scan(root, SLOT))

    def test_existing_slot_and_obstructed_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "scripts/.venv/bin/python"
            binary.parent.mkdir(parents=True)
            binary.mkdir()
            self.assertEqual(["broken-link"], self.scan(root, SLOT))
            binary.rmdir()
            binary.write_text("not executed")
            self.assertEqual([], self.scan(root, SLOT))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").write_text("not a directory")
            self.assertTrue(self.scan(root, SLOT))
