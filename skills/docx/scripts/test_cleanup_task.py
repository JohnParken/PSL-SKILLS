"""Task cleanup lifecycle and protection tests using isolated task directories."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import cleanup_task as cleanup
from create_docx import create_document_delivery
from read_docx import read_docx


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.original = Path.cwd()
        self.folder = tempfile.TemporaryDirectory(prefix="docx-cleanup-test-", dir=".")
        os.chdir(self.folder.name)

    def tearDown(self):
        os.chdir(self.original)
        self.folder.cleanup()

    def plan(self, **options):
        return cleanup.plan_cleanup("cleanup.json", **options)

    def seal(self, **options):
        return cleanup.seal_cleanup("cleanup.json", **options)

    def test_verified_generation_archive_failure_still_cleans_and_preserves_document(self):
        Path("source.txt").write_text("用户输入", encoding="utf-8")
        self.plan(files=["spec.json", "helper.py", "intermediate.docx"], protected=["source.txt"], deliverables=["报告.docx"])
        spec = {"theme": "official", "blocks": [{"type": "paragraph", "text": "最终内容"}]}
        Path("spec.json").write_text(json.dumps(spec), encoding="utf-8")
        Path("helper.py").write_text("# temporary")
        Path("intermediate.docx").write_bytes(b"unused intermediate")
        with patch("archive_deliverables.archive_outputs", side_effect=PermissionError("archive denied")):
            result = create_document_delivery(spec, "报告.docx", protected_inputs=["source.txt"])
        self.assertEqual(result["generation_status"], "verified")
        self.assertEqual(result["archive_status"], "failed")
        before = Path("报告.docx").read_bytes()
        self.seal(outcome="success", verified=True, archive_status=result["archive_status"])
        report = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(report["status"], "cleaned")
        self.assertTrue(report["cleanup_complete"])
        self.assertTrue(report["deliverables_unchanged"])
        self.assertFalse(Path("spec.json").exists())
        self.assertFalse(Path("helper.py").exists())
        self.assertFalse(Path("intermediate.docx").exists())
        self.assertEqual(Path("报告.docx").read_bytes(), before)
        self.assertEqual(read_docx("报告.docx"), "最终内容")
        self.assertEqual(Path("source.txt").read_text(encoding="utf-8"), "用户输入")
        self.assertTrue(Path("cleanup.json").is_file())

    def test_final_failure_and_stop_remove_only_unneeded_files(self):
        for outcome in ("failed", "stopped"):
            manifest = outcome + ".json"
            temporary, backup = outcome + ".py", outcome + ".backup"
            cleanup.plan_cleanup(manifest, files=[temporary, backup])
            Path(temporary).write_text("temporary")
            Path(backup).write_text("recovery required")
            cleanup.seal_cleanup(manifest, outcome=outcome, retain={backup: "rollback not confirmed"})
            result = cleanup.cleanup_task(manifest)
            self.assertEqual(result["status"], "retained")
            self.assertFalse(Path(temporary).exists())
            self.assertEqual(result["retained"], [{"path": backup, "reason": "rollback not confirmed"}])
            self.assertEqual(Path(backup).read_text(), "recovery required")

    def test_retry_retains_files_and_can_be_resealed_after_final_outcome(self):
        self.plan(files=["helper.py"])
        Path("helper.py").write_text("first")
        self.seal(outcome="retry")
        result = cleanup.cleanup_task("cleanup.json")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["retained"][0]["reason"], "retry_pending")
        Path("helper.py").write_text("corrected")
        self.seal(outcome="failed")
        self.assertEqual(cleanup.cleanup_task("cleanup.json")["status"], "cleaned")

    def test_permission_failure_reports_residual_and_can_resume(self):
        self.plan(files=["helper.py", "unused.json"])
        Path("helper.py").write_text("temporary")
        Path("unused.json").write_text("{}")
        self.seal(outcome="failed")
        original_unlink = os.unlink

        def deny_helper(path, **options):
            if path == "helper.py":
                raise PermissionError(errno.EACCES, "permission denied", path)
            return original_unlink(path, **options)

        with patch("cleanup_task.os.unlink", side_effect=deny_helper):
            # supports_dir_fd contains the real callable; keep capability detection independent of the mock.
            with patch("cleanup_task.os.supports_dir_fd", os.supports_dir_fd | {os.unlink}):
                result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"][0]["path"], "helper.py")
        self.assertFalse(Path("unused.json").exists())
        self.assertTrue(Path("helper.py").exists())
        self.assertEqual(cleanup.cleanup_task("cleanup.json")["status"], "cleaned")

    def legacy_plan(self, *, files=(), directories=(), protected=()):
        """Fixture for a preexisting v1 plan; production plan now refuses this layout."""
        with cleanup.TaskFiles() as task:
            data = {"schema": cleanup.SCHEMA, "manifest": "cleanup.json", "task_identity": task.identity,
                    "phase": "planned", "entries": [
                        {"path": path, "kind": kind, "absent_at_plan": True}
                        for kind, paths in (("file", files), ("directory", directories)) for path in paths],
                    "protected": [{"path": p, "initial": task.optional(p)} for p in protected],
                    "deliverables": []}
            cleanup._validate(data, "cleanup.json", task)
            task.save("cleanup.json", data, new=True)

    def test_new_plans_reject_nested_files_directories_and_manifest(self):
        for options in ({"files": ["nested/file"]}, {"directories": ["nested"]}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "directly in task"):
                self.plan(**options)
        with self.assertRaisesRegex(ValueError, "directly in task"):
            cleanup.plan_cleanup("nested/cleanup.json", files=["file"])
        self.assertEqual(list(Path(".").iterdir()), [])

    def test_unknown_directory_content_is_not_scanned_or_deleted(self):
        self.legacy_plan(files=["work/owned.py"], directories=["work"])
        Path("work").mkdir()
        Path("work/owned.py").write_text("owned")
        Path("work/other-task.txt").write_text("not owned")
        self.seal(outcome="failed")
        with patch("os.scandir", side_effect=AssertionError("No scan permitted")):
            result = cleanup.cleanup_task("cleanup.json")
        self.assertFalse(Path("work/owned.py").exists())
        self.assertEqual(Path("work/other-task.txt").read_text(), "not owned")
        self.assertEqual(result["retained"][0]["path"], "work")

    def test_existing_inputs_outputs_and_ambiguous_paths_cannot_be_claimed(self):
        Path("source.docx").write_bytes(b"original")
        for options in ({"files": ["source.docx"]},
                        {"files": ["future.docx"], "deliverables": ["future.docx"]},
                        {"files": ["input.json"], "protected": ["input.json"]},
                        {"files": ["cleanup.json"]}, {"files": ["../outside"]},
                        {"files": [str(Path.cwd() / "absolute")]},
                        {"files": ["x", "x"]}, {"files": ["work/x"], "protected": ["work"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.plan(**options)
        self.assertEqual(Path("source.docx").read_bytes(), b"original")
        self.assertFalse(Path("cleanup.json").exists())

    def test_file_symlink_parent_symlink_type_change_and_hardlink_are_retained(self):
        Path("input.txt").write_text("keep")
        self.legacy_plan(files=["alias", "hard", "typed", "work/file"], directories=["work"],
                  protected=["input.txt"])
        Path("alias").symlink_to("input.txt")
        os.link("input.txt", "hard")
        Path("typed").mkdir()
        Path("elsewhere").mkdir()
        Path("elsewhere/file").write_text("unowned")
        Path("work").symlink_to("elsewhere", target_is_directory=True)
        self.seal(outcome="failed")
        result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "retained")
        self.assertEqual(len(result["retained"]), 5)
        self.assertEqual(Path("input.txt").read_text(), "keep")
        self.assertEqual(Path("elsewhere/file").read_text(), "unowned")
        self.assertTrue(Path("alias").is_symlink())

    def test_changed_temporary_file_or_new_file_after_seal_is_retained(self):
        self.plan(files=["changed.py", "late.json"])
        Path("changed.py").write_text("old")
        self.seal(outcome="failed")
        Path("changed.py").write_text("new")
        Path("late.json").write_text("unknown late file")
        result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(len(result["retained"]), 2)
        self.assertFalse(result["cleanup_complete"])

    def test_final_document_changed_or_missing_blocks_all_deletion(self):
        self.plan(files=["helper.py"], deliverables=["final.docx"])
        Path("helper.py").write_text("temporary")
        Path("final.docx").write_bytes(b"verified result")
        self.seal(outcome="success", verified=True, archive_status="copied")
        Path("final.docx").write_bytes(b"unexpected change")
        result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(Path("helper.py").exists())
        Path("final.docx").unlink()
        self.assertEqual(cleanup.cleanup_task("cleanup.json")["status"], "blocked")

    def test_success_requires_verification_archive_result_and_nonempty_deliverable(self):
        self.plan(files=["helper.py"], deliverables=["final.docx"])
        Path("helper.py").write_text("temporary")
        for options in ({"outcome": "success"},
                        {"outcome": "success", "verified": True},
                        {"outcome": "success", "verified": True, "archive_status": "skipped"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.seal(**options)
        with self.assertRaises(ValueError):
            cleanup.cleanup_task("cleanup.json")
        Path("final.docx").touch()
        with self.assertRaises(ValueError):
            self.seal(outcome="success", verified=True, archive_status="failed")
        self.assertTrue(Path("helper.py").exists())

    def test_replaced_parent_after_seal_does_not_follow_link(self):
        self.legacy_plan(files=["work/file"], directories=["work"])
        Path("work").mkdir()
        Path("work/file").write_text("owned")
        self.seal(outcome="failed")
        Path("work").rename("saved")
        Path("work").symlink_to("saved", target_is_directory=True)
        result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "retained")
        self.assertEqual(Path("saved/file").read_text(), "owned")

    def test_interrupted_cleanup_keeps_manifest_and_resumes_without_recreating_inputs(self):
        self.plan(files=["first.py", "second.py"])
        Path("first.py").write_text("one")
        Path("second.py").write_text("two")
        self.seal(outcome="stopped")
        before = Path("cleanup.json").read_bytes()
        code = """
import os, sys
sys.path.insert(0, sys.argv[1])
import cleanup_task
original = os.unlink
def interrupt(path, **options):
    result = original(path, **options)
    if path == 'first.py':
        os._exit(17)
    return result
os.unlink = interrupt
os.supports_dir_fd = os.supports_dir_fd | {interrupt}
cleanup_task.cleanup_task('cleanup.json')
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(SCRIPT_DIR)],
                                env={"PATH": ""}, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(Path("cleanup.json").read_bytes(), before)
        self.assertFalse(Path("first.py").exists())
        self.assertTrue(Path("second.py").exists())
        report = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(report["status"], "cleaned")
        self.assertIn({"path": "first.py", "reason": "already_absent"}, report["deleted"])

    def test_cli_persists_plan_and_returns_residual_status(self):
        def run(*arguments):
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "cleanup_task.py"),
                                     *arguments], env={"PATH": ""}, capture_output=True, text=True, timeout=15)
            return result.returncode, json.loads(result.stdout)
        self.assertEqual(run("plan", "cleanup.json", "--file", "helper.py")[0], 0)
        Path("helper.py").write_text("retry")
        self.assertEqual(run("seal", "cleanup.json", "--outcome", "retry")[0], 0)
        code, result = run("clean", "cleanup.json")
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "retained")
        self.assertEqual(run("seal", "cleanup.json", "--outcome", "failed")[0], 0)
        self.assertEqual(run("clean", "cleanup.json")[0], 0)

    def test_receipt_failure_preserves_deletion_report_and_original_plan(self):
        self.plan(files=["helper.py"])
        Path("helper.py").write_text("temporary")
        self.seal(outcome="failed")
        before = Path("cleanup.json").read_bytes()
        with patch.object(cleanup.TaskFiles, "save", side_effect=PermissionError("receipt denied")):
            result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["deleted"], [{"path": "helper.py", "reason": "removed"}])
        self.assertIn("receipt_write_failed", result["failed"][0]["reason"])
        self.assertEqual(Path("cleanup.json").read_bytes(), before)
        self.assertEqual(cleanup.cleanup_task("cleanup.json")["status"], "cleaned")

    def test_deliverable_modified_during_cleanup_is_reported_after_deletion(self):
        self.plan(files=["helper.py"], deliverables=["final.docx"])
        Path("helper.py").write_text("temporary")
        Path("final.docx").write_bytes(b"verified")
        self.seal(outcome="success", verified=True, archive_status="skipped")
        original_unlink = os.unlink

        def modify_final(path, **options):
            result = original_unlink(path, **options)
            if path == "helper.py":
                Path("final.docx").write_bytes(b"external change")
            return result

        with patch("cleanup_task.os.unlink", side_effect=modify_final):
            with patch("cleanup_task.os.supports_dir_fd", os.supports_dir_fd | {os.unlink}):
                result = cleanup.cleanup_task("cleanup.json")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["deliverables_unchanged"])
        self.assertFalse(result["cleanup_complete"])

    def test_existing_manifest_duplicate_json_and_unsupported_platform_fail_safely(self):
        self.plan(files=["helper.py"])
        before = Path("cleanup.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.plan(files=["different.py"])
        self.assertEqual(Path("cleanup.json").read_bytes(), before)
        with patch("cleanup_task.os.supports_dir_fd", set()):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                cleanup.cleanup_task("cleanup.json")
        Path("duplicate.json").write_text('{"schema": "a", "schema": "b"}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            cleanup.cleanup_task("duplicate.json")

    def test_malformed_manifest_and_wrong_task_directory_do_not_delete(self):
        self.plan(files=["helper.py"])
        Path("helper.py").write_text("keep")
        self.seal(outcome="failed")
        original = Path("cleanup.json").read_text()
        malformed = json.loads(original)
        malformed["entries"].append({"path": "../outside", "kind": "file", "absent_at_plan": True})
        Path("cleanup.json").write_text(json.dumps(malformed))
        with self.assertRaises(ValueError):
            cleanup.cleanup_task("cleanup.json")
        self.assertTrue(Path("helper.py").exists())
        Path("other").mkdir()
        Path("other/cleanup.json").write_text(original)
        os.chdir("other")
        try:
            with self.assertRaises(ValueError):
                cleanup.cleanup_task("cleanup.json")
        finally:
            os.chdir("..")


if __name__ == "__main__":
    unittest.main()
