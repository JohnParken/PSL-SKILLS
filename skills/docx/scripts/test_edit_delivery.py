"""CLI-level tests for controlled-edit delivery and optional archive handling."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest("DOCX editing requires Python >=3.10")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_docx
import edit_docx
import manage_comments
from read_docx import read_docx
from test_docx_editing import png_pixel


@unittest.skipUnless(expat.version_info >= (2, 6, 0), "DOCX editing requires Expat >=2.6")
class EditDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-edit-delivery-", dir=".")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()

    @staticmethod
    def config(record):
        return json.dumps({"commonParam": [{"name": "requestId", "value": record}]})

    def run_cli(self, script, *arguments, record="turn-record", cwd=None):
        environment = dict(os.environ, PATH="")
        environment.pop("ROUTER_CONFIG_VARIABLES", None)
        if record is not None:
            environment["ROUTER_CONFIG_VARIABLES"] = self.config(record)
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / script),
             *(str(argument) for argument in arguments)],
            cwd=cwd or self.work, env=environment, capture_output=True, encoding="utf-8",
        )

    def document(self, name, blocks):
        path = self.work / name
        create_docx.create_document(
            {"blocks": blocks}, path, base_dir=self.work, verify=True,
        )
        return path

    def json_file(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def members(self, path):
        with zipfile.ZipFile(path) as package:
            return {name: package.read(name) for name in package.namelist()}

    def assert_archived(self, process, output, record):
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        payload = json.loads(process.stdout)
        self.assertEqual(payload["output"], output)
        if isinstance(payload["result"], str):
            self.assertEqual(payload["result"], output)
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["archive_status"], "copied")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(len(payload["files"]), 1)
        item = payload["files"][0]
        expected_archive = f"output/{record}/{Path(output).name}"
        self.assertEqual(item["output"], output)
        self.assertEqual(item["archive_output"], expected_archive)
        self.assertEqual(item["archive_status"], "copied")
        self.assertEqual((self.work / output).read_bytes(),
                         (self.work / expected_archive).read_bytes())
        return payload

    def assert_archive_warning(self, process, output):
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("archive-warning:", process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["output"], output)
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["archive_status"], "failed")
        self.assertTrue(payload["warnings"])
        if payload["files"]:
            self.assertEqual(len(payload["files"]), 1)
            self.assertEqual(payload["files"][0]["output"], output)
            self.assertEqual(payload["files"][0]["archive_status"], "failed")
            self.assertTrue(payload["files"][0]["error"])
        return payload

    def test_all_five_edit_clis_publish_and_archive_their_results(self):
        source = self.document("source.docx", [
            {"type": "paragraph", "text": "合同 ABC"},
        ])
        source_bytes = source.read_bytes()
        replacement_json = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        replacement_json_bytes = replacement_json.read_bytes()
        output = "edited-text.docx"
        result = self.run_cli("edit_docx.py", "source.docx", "replace.json", output,
                              record="turn-1")
        self.assert_archived(result, output, "turn-1")
        self.assertEqual(read_docx(self.work / output), "合同 XYZ")

        structure_json = self.json_file("structure.json", {"operations": [
            {"type": "insert_paragraph", "paragraph_index": 0,
             "expected_text": "合同 ABC", "position": "after", "text": "新增段落"},
        ]})
        structure_json_bytes = structure_json.read_bytes()
        output = "edited-structure.docx"
        result = self.run_cli("edit_structure.py", "source.docx", "structure.json", output,
                              record="turn-2")
        self.assert_archived(result, output, "turn-2")
        self.assertEqual(read_docx(self.work / output), "合同 ABC\n新增段落")

        table_source = self.document("table-source.docx", [
            {"type": "table", "rows": [["项目", "金额"], ["甲", "10"]], "header": True},
        ])
        table_source_bytes = table_source.read_bytes()
        table_json = self.json_file("table.json", {"operation": {
            "type": "insert_row", "table_index": 0, "row_index": 1,
            "expected_rows": 2, "expected_text": ["甲", "10"],
            "position": "after", "cells": ["乙", "20"],
        }})
        table_json_bytes = table_json.read_bytes()
        output = "edited-table.docx"
        result = self.run_cli("edit_tables.py", "table-source.docx", "table.json", output,
                              record="turn-3")
        self.assert_archived(result, output, "turn-3")
        self.assertEqual(read_docx(self.work / output), "项目\t金额\n甲\t10\n乙\t20")

        red = self.work / "red.png"
        blue = self.work / "blue.png"
        red.write_bytes(png_pixel(255, 0, 0))
        blue.write_bytes(png_pixel(0, 0, 255))
        red_bytes = red.read_bytes()
        blue_bytes = blue.read_bytes()
        image_source = self.document("image-source.docx", [
            {"type": "image", "path": "red.png", "width_mm": 5,
             "height_mm": 5, "alt": "red pixel"},
        ])
        image_source_bytes = image_source.read_bytes()
        media = next(name for name in self.members(image_source) if name.startswith("word/media/"))
        selection = self.json_file("selection.json", {
            "media_part": media,
            "expected_sha256": hashlib.sha256(png_pixel(255, 0, 0)).hexdigest(),
            "expected_relationships": 1,
            "expected_references": 1,
        })
        selection_bytes = selection.read_bytes()
        output = "edited-image.docx"
        result = self.run_cli("replace_image.py", "image-source.docx", "blue.png",
                              "selection.json", output, record="turn-4")
        self.assert_archived(result, output, "turn-4")
        self.assertEqual(self.members(self.work / output)[media], blue.read_bytes())

        comments_json = self.json_file("comments.json", {
            "action": "add", "paragraph_index": 0, "expected_text": "合同 ABC",
            "anchor_text": "合同 ABC", "text": "核实金额", "author": "审阅者",
        })
        comments_json_bytes = comments_json.read_bytes()
        output = "edited-comments.docx"
        result = self.run_cli("manage_comments.py", "source.docx", "comments.json", output,
                              record="turn-5")
        payload = self.assert_archived(result, output, "turn-5")
        self.assertEqual(payload["result"], {"action": "add", "comment_id": 0})

        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(table_source.read_bytes(), table_source_bytes)
        self.assertEqual(image_source.read_bytes(), image_source_bytes)
        self.assertEqual(replacement_json.read_bytes(), replacement_json_bytes)
        self.assertEqual(red.read_bytes(), red_bytes)
        self.assertEqual(blue.read_bytes(), blue_bytes)
        self.assertEqual(structure_json.read_bytes(), structure_json_bytes)
        self.assertEqual(table_json.read_bytes(), table_json_bytes)
        self.assertEqual(selection.read_bytes(), selection_bytes)
        self.assertEqual(comments_json.read_bytes(), comments_json_bytes)

    def test_blocked_archive_keeps_check_generation_and_all_edit_clis_successful(self):
        archive_blocker = self.work / "output"
        archive_blocker.write_bytes(b"keep-blocker")

        check = self.run_cli("create_docx.py", "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("output_dir=. writable=True", check.stdout)
        self.assertIn("archive_dir=output/turn-record writable=False", check.stdout)
        self.assertIn("check-ok", check.stdout)
        self.assertIn("archive-warning:", check.stderr)
        self.assertEqual(archive_blocker.read_bytes(), b"keep-blocker")

        self.json_file("spec.json", {"blocks": [
            {"type": "paragraph", "text": "合同 ABC"},
        ]})
        generated = self.run_cli("create_docx.py", "spec.json", "source.docx", "--verify")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        generated_payload = json.loads(generated.stdout)
        self.assertEqual(generated_payload["status"], "verified")
        self.assertEqual(generated_payload["archive_status"], "failed")
        self.assertTrue(generated_payload["warnings"])
        self.assertIn("archive-warning:", generated.stderr)
        self.assertEqual(read_docx(self.work / "source.docx"), "合同 ABC")

        self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        edited = self.run_cli("edit_docx.py", "source.docx", "replace.json", "blocked-text.docx")
        self.assert_archive_warning(edited, "blocked-text.docx")
        self.assertEqual(read_docx(self.work / "blocked-text.docx"), "合同 XYZ")

        self.json_file("structure.json", {"operations": [
            {"type": "insert_paragraph", "paragraph_index": 0,
             "expected_text": "合同 ABC", "position": "after", "text": "新增段落"},
        ]})
        edited = self.run_cli("edit_structure.py", "source.docx", "structure.json",
                              "blocked-structure.docx")
        self.assert_archive_warning(edited, "blocked-structure.docx")
        self.assertEqual(read_docx(self.work / "blocked-structure.docx"), "合同 ABC\n新增段落")

        self.document("table-source.docx", [
            {"type": "table", "rows": [["项目", "金额"], ["甲", "10"]], "header": True},
        ])
        self.json_file("table.json", {"operation": {
            "type": "insert_row", "table_index": 0, "row_index": 1,
            "expected_rows": 2, "expected_text": ["甲", "10"],
            "position": "after", "cells": ["乙", "20"],
        }})
        edited = self.run_cli("edit_tables.py", "table-source.docx", "table.json",
                              "blocked-table.docx")
        self.assert_archive_warning(edited, "blocked-table.docx")
        self.assertEqual(read_docx(self.work / "blocked-table.docx"), "项目\t金额\n甲\t10\n乙\t20")

        red = self.work / "red.png"
        blue = self.work / "blue.png"
        red.write_bytes(png_pixel(255, 0, 0))
        blue.write_bytes(png_pixel(0, 0, 255))
        image_source = self.document("image-source.docx", [
            {"type": "image", "path": "red.png", "width_mm": 5,
             "height_mm": 5, "alt": "red pixel"},
        ])
        media = next(name for name in self.members(image_source)
                     if name.startswith("word/media/"))
        self.json_file("selection.json", {
            "media_part": media,
            "expected_sha256": hashlib.sha256(red.read_bytes()).hexdigest(),
            "expected_relationships": 1,
            "expected_references": 1,
        })
        edited = self.run_cli("replace_image.py", "image-source.docx", "blue.png",
                              "selection.json", "blocked-image.docx")
        self.assert_archive_warning(edited, "blocked-image.docx")
        self.assertEqual(self.members(self.work / "blocked-image.docx")[media], blue.read_bytes())

        self.json_file("comments.json", {
            "action": "add", "paragraph_index": 0, "expected_text": "合同 ABC",
            "anchor_text": "合同 ABC", "text": "核实金额", "author": "审阅者",
        })
        edited = self.run_cli("manage_comments.py", "source.docx", "comments.json",
                              "blocked-comments.docx")
        payload = self.assert_archive_warning(edited, "blocked-comments.docx")
        self.assertEqual(payload["result"], {"action": "add", "comment_id": 0})

        self.assertEqual(archive_blocker.read_bytes(), b"keep-blocker")

    def test_failed_edit_does_not_change_archive_contents(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        operation = self.json_file("wrong-count.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 2},
        ]})
        archive_dir = self.work / "output/failure-record"
        archive_dir.mkdir(parents=True)
        existing_archive = archive_dir / "existing.docx"
        existing_archive.write_bytes(b"keep existing archive")
        process = self.run_cli("edit_docx.py", "source.docx", "wrong-count.json", "failed.docx",
                               record="failure-record")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, "")
        self.assertIn("Error:", process.stderr)
        self.assertFalse((self.work / "failed.docx").exists())
        self.assertEqual(existing_archive.read_bytes(), b"keep existing archive")
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(operation.read_text(encoding="utf-8"), json.dumps(
            {"replacements": [{"old": "ABC", "new": "XYZ", "count": 2}]},
            ensure_ascii=False,
        ))
        self.assertEqual([path.name for path in archive_dir.iterdir()], ["existing.docx"])

    def test_archive_input_collision_warns_and_keeps_edited_output_and_source(self):
        record = "source-at-archive"
        archive_source = self.work / "output" / record / "edited.docx"
        archive_source.parent.mkdir(parents=True)
        create_docx.create_document(
            {"blocks": [{"type": "paragraph", "text": "合同 ABC"}]},
            archive_source, base_dir=self.work, verify=True,
        )
        original_archive_source = archive_source.read_bytes()
        operation = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        operation_bytes = operation.read_bytes()
        process = self.run_cli("edit_docx.py", archive_source.relative_to(self.work),
                               "replace.json", "edited.docx", record=record)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("archive-warning:", process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["archive_status"], "failed")
        self.assertEqual(payload["files"], [])
        self.assertTrue(payload["warnings"])
        self.assertEqual(read_docx(self.work / "edited.docx"), "合同 XYZ")
        self.assertEqual(archive_source.read_bytes(), original_archive_source)
        self.assertEqual(operation.read_bytes(), operation_bytes)

    def test_archive_copy_failure_keeps_new_output_old_archive_and_source(self):
        record = "copy-failure"
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        operation = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        operation_bytes = operation.read_bytes()
        output = self.work / "edited.docx"
        previous_output = b"old local output must be replaced after a successful edit"
        output.write_bytes(previous_output)
        archive_dir = self.work / "output" / record
        archive_dir.mkdir(parents=True)
        archive = archive_dir / "edited.docx"
        previous_archive = b"old archive must survive the failed copy"
        archive.write_bytes(previous_archive)
        original_copyfileobj = create_docx.shutil.copyfileobj

        def fail_archive_copy(source_stream, destination_stream, length=0):
            source_name = getattr(source_stream, "name", None)
            if (isinstance(source_name, str)
                    and Path(source_name).resolve() == output.resolve()):
                raise OSError("injected archive copy failure")
            return original_copyfileobj(source_stream, destination_stream, length)

        original_directory = Path.cwd()
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            os.chdir(self.work)
            with patch.dict(os.environ, {"ROUTER_CONFIG_VARIABLES": self.config(record)}):
                with patch("create_docx.shutil.copyfileobj", side_effect=fail_archive_copy):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = edit_docx.main([
                            "source.docx", "replace.json", "edited.docx",
                        ])
        finally:
            os.chdir(original_directory)

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("archive-warning:", stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["output"], "edited.docx")
        self.assertEqual(payload["archive_status"], "failed")
        self.assertTrue(payload["warnings"])
        self.assertIn("injected archive copy failure", "\n".join(payload["warnings"]))
        self.assertEqual(payload["files"][0]["archive_status"], "failed")
        self.assertIn("injected archive copy failure", payload["files"][0]["error"])
        self.assertNotEqual(output.read_bytes(), previous_output)
        self.assertEqual(read_docx(output), "合同 XYZ")
        self.assertEqual(archive.read_bytes(), previous_archive)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(operation.read_bytes(), operation_bytes)
        self.assertEqual([path.name for path in archive_dir.iterdir()], ["edited.docx"])

    def test_missing_config_skips_archive_and_keeps_edited_local_output(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        operation = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        old_output = self.work / "edited.docx"
        old_output.write_bytes(b"old local output")
        process = self.run_cli("edit_docx.py", "source.docx", "replace.json", "edited.docx",
                               record=None)

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["archive_status"], "skipped")
        self.assertTrue(payload["used_fallback"])
        self.assertEqual(payload["fallback_reason"], "missing_router_config")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["files"], [{
            "output": "edited.docx", "archive_output": None, "archive_status": "skipped",
        }])
        self.assertEqual(read_docx(old_output), "合同 XYZ")
        self.assertFalse((self.work / "output").exists())

    def test_output_validation_rejects_input_aliases_and_symlink_components(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        operation = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1},
        ]})
        operation_bytes = operation.read_bytes()

        process = self.run_cli("edit_docx.py", "source.docx", "replace.json", "replace.json")
        self.assertEqual(process.returncode, 1)
        self.assertFalse(process.stdout)
        self.assertIn("input file", process.stderr)
        self.assertEqual(operation.read_bytes(), operation_bytes)

        sentinel = self.work / "sentinel.docx"
        sentinel.write_bytes(b"keep symlink target")
        link = self.work / "linked.docx"
        link.symlink_to(sentinel)
        process = self.run_cli("edit_docx.py", "source.docx", "replace.json", "linked.docx")
        self.assertEqual(process.returncode, 1)
        self.assertTrue(link.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b"keep symlink target")

        nested = self.work / "nested"
        nested.mkdir()
        nested_source = self.document("nested-source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        nested_source.rename(nested / "nested-source.docx")
        operation.rename(nested / "replace.json")
        (nested / "link").symlink_to(self.work, target_is_directory=True)
        process = self.run_cli("edit_docx.py", "nested-source.docx", "replace.json",
                               "link/escaped.docx", cwd=nested)
        self.assertEqual(process.returncode, 1)
        self.assertIn("symbolic links", process.stderr)
        self.assertFalse((self.work / "escaped.docx").exists())
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_comment_list_keeps_existing_json_and_does_not_use_delivery_helpers(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        action = self.json_file("list.json", {"action": "list"})
        original_directory = Path.cwd()
        stdout = io.StringIO()
        stderr = io.StringIO()
        expected = None
        try:
            os.chdir(self.work)
            expected = manage_comments.manage_comments("source.docx", {"action": "list"})
            with patch.dict(os.environ, {"ROUTER_CONFIG_VARIABLES": self.config("must-not-be-read")}):
                with patch.object(manage_comments, "finalize_delivery",
                                  side_effect=AssertionError("archive helper called")), \
                        patch.object(manage_comments, "validate_output_path",
                                     side_effect=AssertionError("output helper called")):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = manage_comments.main(["source.docx", "list.json"])
        finally:
            os.chdir(original_directory)
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), expected)
        self.assertFalse((self.work / "output").exists())
        self.assertEqual(action.read_text(encoding="utf-8"), '{"action": "list"}')

    def test_current_record_is_resolved_for_each_edit_invocation(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        operation = self.json_file("replace.json", {"replacements": [
            {"old": "ABC", "new": "第一版", "count": 1},
        ]})
        first = self.run_cli("edit_docx.py", "source.docx", "replace.json", "same-name.docx",
                             record="first-record")
        self.assert_archived(first, "same-name.docx", "first-record")
        first_archive = self.work / "output/first-record/same-name.docx"
        first_bytes = first_archive.read_bytes()

        operation.write_text(json.dumps({"replacements": [
            {"old": "ABC", "new": "第二版", "count": 1},
        ]}, ensure_ascii=False), encoding="utf-8")
        second = self.run_cli("edit_docx.py", "source.docx", "replace.json", "same-name.docx",
                              record="second-record")
        self.assert_archived(second, "same-name.docx", "second-record")
        self.assertEqual(first_archive.read_bytes(), first_bytes)
        self.assertEqual(read_docx(self.work / "same-name.docx"), "合同 第二版")


if __name__ == "__main__":
    unittest.main()
