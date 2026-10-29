"""Behavior checks for task-root scratch files and memory package staging."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import comment
import merge_runs
from create_docx import create_document
from edit_docx import replace_text
from office.memory_package import MemoryPath, package_workspace
from office.package import unpack_document, pack_document
from office.validators.redlining import RedliningValidator
from office.helpers import atomic_write_bytes


class FlatTemporaryTests(unittest.TestCase):
    def setUp(self):
        self.original = Path.cwd()
        self.task = tempfile.TemporaryDirectory(prefix="docx-flat-test-", dir=".")
        os.chdir(self.task.name)
        self.root = Path.cwd()
        create_document({"theme": "official", "blocks": [{"type": "paragraph", "text": "original text"}]}, "source.docx")

    def tearDown(self):
        os.chdir(self.original)
        self.task.cleanup()

    def test_edit_check_comment_and_merge_create_no_scratch_directories(self):
        created = []
        original_mkstemp = tempfile.mkstemp
        def record_file(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            created.append(Path(result[1]))
            self.assertEqual(Path(result[1]).resolve().parent, self.root)
            return result
        with patch("tempfile.TemporaryDirectory", side_effect=AssertionError("No scratch directories")), \
                patch("tempfile.mkdtemp", side_effect=AssertionError("No scratch directories")), \
                patch("tempfile.mkstemp", side_effect=record_file):
            replace_text("source.docx", "edited.docx", {"replacements": [{"old": "original", "new": "modified"}]})
            with package_workspace("source.docx") as original:
                self.assertTrue(RedliningValidator(original, "source.docx").validate())
            with patch.object(sys, "argv", ["comment.py", "source.docx", "note", "-o", "commented.docx"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                comment.main()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(merge_runs.main(["source.docx", "-o", "merged.docx"]), 0)
        self.assertTrue(created)
        self.assertTrue(all(not path.exists() for path in created))
        self.assertTrue(all(path.is_file() for path in self.root.iterdir()))

    def test_explicit_unpack_is_output_and_direct_cli_roundtrips(self):
        unpack_document("source.docx", "requested-output")
        self.assertTrue(Path("requested-output/word/document.xml").is_file())
        pack_document("requested-output", "repacked.docx")
        for script, arguments in (("office/package.py", ["unpack", "source.docx", "second-output"]),
                                  ("office/validate.py", ["source.docx", "--original", "source.docx"])):
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / script), *arguments],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile("source.docx") as original, zipfile.ZipFile("repacked.docx") as actual:
            self.assertEqual({n: original.read(n) for n in original.namelist()},
                             {n: actual.read(n) for n in actual.namelist()})

    def test_directory_comment_failure_rolls_back_without_scratch_directory(self):
        unpack_document("source.docx", "input")
        before = {p.relative_to("input").as_posix(): p.read_bytes() for p in Path("input").rglob("*") if p.is_file()}
        calls = 0
        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("publish failed")
            atomic_write_bytes(path, data)
        with patch.object(comment, "atomic_write_bytes", side_effect=fail_second), \
                self.assertRaisesRegex(OSError, "publish failed"):
            comment.add_comment("input", "note")
        after = {p.relative_to("input").as_posix(): p.read_bytes() for p in Path("input").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["input", "source.docx"])

    def test_directory_comment_rollback_failure_retains_root_zip_backup(self):
        unpack_document("source.docx", "input")
        with patch.object(comment, "atomic_write_bytes", side_effect=OSError("write denied")), \
                self.assertRaisesRegex(RuntimeError, "backup retained"):
            comment.add_comment("input", "note")
        backups = list(self.root.glob("docx-comment-backup-*.zip"))
        self.assertEqual(len(backups), 1)
        with zipfile.ZipFile(backups[0]) as archive, zipfile.ZipFile("source.docx") as original:
            self.assertEqual({n: archive.read(n) for n in archive.namelist()},
                             {n: original.read(n) for n in original.namelist()})

    def test_corrupt_staged_package_does_not_replace_existing_output(self):
        import office.package as package
        Path("final.docx").write_bytes(b"existing user output")
        check = package._verify_written_package
        def corrupt(path):
            Path(path).write_bytes(b"truncated")
            check(path)
        with package_workspace("source.docx") as root, \
                patch.object(package, "_verify_written_package", side_effect=corrupt), \
                self.assertRaises(ValueError):
            pack_document(root, "final.docx")
        self.assertEqual(Path("final.docx").read_bytes(), b"existing user output")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["final.docx", "source.docx"])

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_memory_repack_preserves_existing_mode_and_respects_umask(self):
        mask = os.umask(0o027)
        try:
            with package_workspace("source.docx") as root:
                pack_document(root, "final.docx")
                self.assertEqual(Path("final.docx").stat().st_mode & 0o777, 0o640)
                Path("final.docx").chmod(0o600)
                pack_document(root, "final.docx")
                self.assertEqual(Path("final.docx").stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(mask)

    def test_memory_member_write_checks_budget_before_changing_contents(self):
        root = MemoryPath({"a": b"original"})
        with patch("office.memory_package.MAX_PART_BYTES", 3), self.assertRaises(ValueError):
            (root / "a").write_bytes(b"large")
        self.assertEqual((root / "a").read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
