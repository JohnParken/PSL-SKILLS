"""Fixed host ./, private aggregate identity, cleanup outcomes and batch notices."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_pptx
from tl_guard import ContractError
from tl_workspace import check_task_root, lifecycle, private_workspace, receipt


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        os.chdir(self.temp.name)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def test_creation_is_registered_before_mkdir(self):
        mkdir = os.mkdir
        with lifecycle() as journal:
            def observing(name, mode, *, dir_fd):
                self.assertEqual(journal[-1], {"name": name, "state": "planned"})
                return mkdir(name, mode, dir_fd=dir_fd)
            with patch("tl_workspace.os.mkdir", side_effect=observing):
                with private_workspace(prefix="tl-test-") as work:
                    (work / "owned.txt").write_text("input")
            self.assertTrue(receipt(journal)["cleanup_complete"])
            self.assertFalse(work.exists())

    def test_producer_failure_reports_cleanup_and_retains_input(self):
        Path("input.svg").write_text("original")
        def failure():
            with private_workspace(prefix="tl-test-") as work:
                (work / "owned").write_text("synthetic")
                raise ValueError("producer failed")
        result, code = tl_pptx.run_artifact("result.json", ".json", ["input.svg"], failure)
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertTrue(result["cleanup"]["cleanup_complete"])
        self.assertEqual(set(os.listdir(".")), {"input.svg"})

    def test_replaced_root_retained_success_and_failure(self):
        for failing in (False, True):
            suffix = str(failing)
            def producer():
                with private_workspace(prefix="tl-test-") as work:
                    work.rename("moved" + suffix)
                    work.mkdir()
                    (work / "preserve").write_text("not ours")
                    if failing:
                        raise ValueError("business")
                return b"deliverable", {}
            with patch("archive_delivery.archive_delivery", return_value={"archive_status":"failed","files":[]}):
                result, code = tl_pptx.run_artifact("o"+suffix+".json", ".json", [], producer)
            self.assertEqual(code, 1 if failing else 3)
            self.assertFalse(result["cleanup"]["cleanup_complete"])
            self.assertEqual((Path(result["cleanup"]["retained"][0])/"preserve").read_text(), "not ours")
            self.assertEqual(Path("o"+suffix+".json").exists(), not failing)

    def test_unexpected_link_preserved_not_followed(self):
        Path("sentinel").write_text("protected")
        with lifecycle() as journal:
            with private_workspace(prefix="tl-test-") as work:
                (work/"unexpected").symlink_to(Path("sentinel").resolve())
        self.assertFalse(receipt(journal)["cleanup_complete"])
        self.assertEqual(Path("sentinel").read_text(), "protected")

    def test_install_root_and_child_rejected_before_probe(self):
        install = Path(tl_pptx.__file__).resolve().parents[1]
        for directory in (install, install / "scripts", install / "references"):
            os.chdir(directory)
            with self.assertRaises(ContractError):
                check_task_root()
            with self.assertRaises(ContractError):
                tl_pptx.environment_check(True, require_deployment=False)

    def test_partial_batch_never_announces_completion(self):
        result = {"archive_status":"partial","files":[{"output":"a","archive_status":"copied"},
                                                     {"output":"b","archive_status":"failed"}]}
        self.assertEqual(tl_pptx.delivery_notice(result), "归档未完成")
        self.assertEqual(tl_pptx.delivery_notice({"archive_status":"success","files":[]}), "归档未完成")
        result["archive_status"] = "success"
        result["files"][1]["archive_status"] = "already_archived"
        self.assertEqual(tl_pptx.delivery_notice(result, ["a","b"]), "归档完成")
        self.assertEqual(tl_pptx.delivery_notice(result, ["a","b","c"]), "归档未完成")
