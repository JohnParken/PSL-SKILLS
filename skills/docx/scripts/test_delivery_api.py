"""Contract tests for the callable DOCX delivery APIs."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest("DOCX delivery requires Python >=3.10")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import archive_deliverables
import create_docx
import edit_docx
import edit_structure
import edit_tables
import manage_comments
import replace_image
from read_docx import read_docx
from test_docx_editing import png_pixel


@unittest.skipUnless(expat.version_info >= (2, 6, 0), "DOCX delivery requires Expat >=2.6")
class DeliveryApiTests(unittest.TestCase):
    def setUp(self):
        self.original_directory = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-delivery-api-", dir=".")
        self.work = Path(self.temporary.name).resolve()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.original_directory)
        self.temporary.cleanup()

    @staticmethod
    def router_config(request_id="api-record"):
        return json.dumps({"commonParam": [{"name": "requestId", "value": request_id}]})

    @contextlib.contextmanager
    def router_environment(self, value=None):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTER_CONFIG_VARIABLES", None)
            if value is not None:
                os.environ["ROUTER_CONFIG_VARIABLES"] = value
            yield

    def document(self, name, blocks):
        return create_docx.create_document(
            {"blocks": blocks}, name, base_dir=self.work, verify=True,
        )

    def invoke_delivery(self, callback):
        with patch.object(
            archive_deliverables,
            "archive_outputs",
            wraps=archive_deliverables.archive_outputs,
        ) as archive_call:
            result = callback()
        archive_call.assert_called_once()
        return result

    def assert_archived(self, summary, output, *, status="edited", request_id="api-record"):
        self.assertEqual(summary["status"], status)
        self.assertEqual(summary["archive_status"], "copied")
        self.assertEqual(summary["output"], output)
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(len(summary["files"]), 1)
        item = summary["files"][0]
        expected_archive = f"output/{request_id}/{Path(output).name}"
        self.assertEqual(item["output"], output)
        self.assertEqual(item["archive_output"], expected_archive)
        self.assertEqual(item["archive_status"], "copied")
        self.assertEqual(Path(output).read_bytes(), Path(expected_archive).read_bytes())
        return summary

    def image_selection(self, document, image_bytes):
        with zipfile.ZipFile(document) as package:
            media_part = next(name for name in package.namelist() if name.startswith("word/media/"))
        return {
            "media_part": media_part,
            "expected_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "expected_relationships": 1,
            "expected_references": 1,
        }

    def test_creation_accepts_output_docx_and_archives_verified_document(self):
        specification = {"blocks": [{"type": "paragraph", "text": "新建交付内容 & 可读取"}]}
        with self.router_environment(self.router_config()):
            summary = self.invoke_delivery(lambda: create_docx.create_document_delivery(
                specification, "output.docx", base_dir=self.work, verify=True,
            ))

        self.assert_archived(summary, "output.docx", status="verified")
        self.assertEqual(summary["generation_status"], "verified")
        self.assertEqual(summary["archive_output"], "output/api-record/output.docx")
        self.assertEqual(read_docx("output.docx"), "新建交付内容 & 可读取")

    def test_all_five_edit_delivery_apis_create_readable_archived_outputs(self):
        source = self.document("source.docx", [
            {"type": "paragraph", "text": "合同 ABC"},
        ])
        source_bytes = source.read_bytes()

        with self.router_environment(self.router_config()):
            text_summary = self.invoke_delivery(lambda: edit_docx.replace_text_delivery(
                source, "text-edited.docx",
                {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]},
            ))
            structure_summary = self.invoke_delivery(lambda: edit_structure.edit_structure_delivery(
                source, "structure-edited.docx",
                {"operations": [{"type": "insert_paragraph", "paragraph_index": 0,
                                 "expected_text": "合同 ABC", "position": "after",
                                 "text": "新增段落"}]},
            ))

            table_source = self.document("table-source.docx", [
                {"type": "table", "rows": [["项目", "金额"], ["甲", "10"]], "header": True},
            ])
            table_summary = self.invoke_delivery(lambda: edit_tables.edit_tables_delivery(
                table_source, "table-edited.docx",
                {"operation": {"type": "insert_row", "table_index": 0, "row_index": 1,
                               "expected_rows": 2, "expected_text": ["甲", "10"],
                               "position": "after", "cells": ["乙", "20"]}},
            ))

            red, blue = png_pixel(255, 0, 0), png_pixel(0, 0, 255)
            Path("red.png").write_bytes(red)
            Path("blue.png").write_bytes(blue)
            image_source = self.document("image-source.docx", [
                {"type": "image", "path": "red.png", "width_mm": 5,
                 "height_mm": 5, "alt": "red pixel"},
            ])
            image_summary = self.invoke_delivery(lambda: replace_image.replace_image_delivery(
                image_source, "blue.png", "image-edited.docx",
                self.image_selection(image_source, red),
            ))

            comments_summary = self.invoke_delivery(lambda: manage_comments.manage_comments_delivery(
                source,
                {"action": "add", "paragraph_index": 0, "expected_text": "合同 ABC",
                 "anchor_text": "合同 ABC", "text": "核实金额", "author": "审阅者"},
                "comments-edited.docx",
            ))

        for summary, output in (
            (text_summary, "text-edited.docx"),
            (structure_summary, "structure-edited.docx"),
            (table_summary, "table-edited.docx"),
            (image_summary, "image-edited.docx"),
            (comments_summary, "comments-edited.docx"),
        ):
            self.assert_archived(summary, output)

        self.assertEqual(read_docx("text-edited.docx"), "合同 XYZ")
        self.assertEqual(read_docx("structure-edited.docx"), "合同 ABC\n新增段落")
        self.assertEqual(read_docx("table-edited.docx"), "项目\t金额\n甲\t10\n乙\t20")
        image_members = self._package_members(Path("image-edited.docx"))
        selection = self.image_selection(image_source, red)
        self.assertEqual(image_members[selection["media_part"]], blue)
        comments = manage_comments.manage_comments("comments-edited.docx", {"action": "list"})
        self.assertIn("核实金额", [comment["text"] for comment in comments["comments"]])
        self.assertEqual(source.read_bytes(), source_bytes)

    @staticmethod
    def _package_members(path):
        with zipfile.ZipFile(path) as package:
            return {name: package.read(name) for name in package.namelist()}

    def test_missing_and_invalid_router_config_keep_primary_edit_readable(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        specification = {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]}

        with self.router_environment():
            missing = self.invoke_delivery(lambda: edit_docx.replace_text_delivery(
                source, "missing-config.docx", specification,
            ))
        self.assertEqual(missing["status"], "edited")
        self.assertEqual(missing["archive_status"], "skipped")
        self.assertEqual(missing["fallback_reason"], "missing_router_config")
        self.assertEqual(read_docx("missing-config.docx"), "合同 XYZ")
        self.assertFalse(Path("output").exists())

        with self.router_environment("not valid JSON"):
            invalid = self.invoke_delivery(lambda: edit_docx.replace_text_delivery(
                source, "invalid-config.docx", specification,
            ))
        self.assertEqual(invalid["status"], "edited")
        self.assertEqual(invalid["archive_status"], "skipped")
        self.assertEqual(invalid["fallback_reason"], "invalid_router_config_json")
        self.assertEqual(read_docx("invalid-config.docx"), "合同 XYZ")
        self.assertFalse(Path("output").exists())

    def test_unexpected_archive_exceptions_do_not_hide_creation_or_edit_results(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        specification = {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]}
        failure = RuntimeError("injected unexpected archive failure")

        with self.router_environment(self.router_config()):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                side_effect=failure,
            ) as archive_call:
                created = create_docx.create_document_delivery(
                    {"blocks": [{"type": "paragraph", "text": "created locally"}]},
                    "exception-create.docx", base_dir=self.work, verify=True,
                )
            archive_call.assert_called_once()

            with patch.object(
                archive_deliverables,
                "archive_outputs",
                side_effect=failure,
            ) as archive_call:
                edited = edit_docx.replace_text_delivery(
                    source, "exception-edit.docx", specification,
                )
            archive_call.assert_called_once()

        for summary, status in ((created, "verified"), (edited, "edited")):
            self.assertEqual(summary["status"], status)
            self.assertEqual(summary["archive_status"], "failed")
            self.assertIn("injected unexpected archive failure", "\n".join(summary["warnings"]))
        self.assertEqual(read_docx("exception-create.docx"), "created locally")
        self.assertEqual(read_docx("exception-edit.docx"), "合同 XYZ")

    def test_creation_and_edit_clis_keep_success_when_shared_archiver_raises(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        original = source.read_bytes()
        Path("spec.json").write_text(json.dumps({
            "blocks": [{"type": "paragraph", "text": "新建正文"}]}), encoding="utf-8")
        Path("operations.json").write_text(json.dumps({"replacements": [
            {"old": "ABC", "new": "XYZ", "count": 1}]}), encoding="utf-8")
        cases = [
            (create_docx.main, ["spec.json", "created.docx", "--verify"],
             "verified", "新建正文"),
            (edit_docx.main, ["source.docx", "operations.json", "edited.docx"],
             "edited", "合同 XYZ"),
        ]
        with self.router_environment(self.router_config()):
            for entry, arguments, status, expected_text in cases:
                with self.subTest(entry=entry.__module__):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with patch.object(archive_deliverables, "archive_outputs",
                                      side_effect=OSError("injected shared archive failure")) as call:
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = entry(arguments)
                    call.assert_called_once()
                    self.assertEqual(code, 0)
                    result = json.loads(stdout.getvalue())
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["archive_status"], "failed")
                    self.assertIn("injected shared archive failure", result["warnings"])
                    self.assertIn("archive-warning:", stderr.getvalue())
                    self.assertEqual(read_docx(result["output"]), expected_text)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(Path("output").exists())

    def test_dictionary_json_inputs_are_protected_from_output_and_archive_aliases(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        specification = {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]}
        protected_json = Path("operations.json")
        protected_json.write_text(json.dumps(specification), encoding="utf-8")
        protected_json_bytes = protected_json.read_bytes()
        local_alias = Path("operations.docx")
        os.link(protected_json, local_alias)

        with self.router_environment(self.router_config()):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with self.assertRaises(ValueError):
                    edit_docx.replace_text_delivery(
                        source, local_alias, specification, protected_inputs=(protected_json,),
                    )
            archive_call.assert_not_called()
        self.assertEqual(protected_json.read_bytes(), protected_json_bytes)
        self.assertEqual(local_alias.read_bytes(), protected_json_bytes)
        self.assertEqual(source.read_bytes(), source_bytes)

        archive_request_id = "archive-json-alias"
        archive_target = Path("output") / archive_request_id / "archive-alias.docx"
        archive_target.parent.mkdir(parents=True)
        os.link(protected_json, archive_target)
        with self.router_environment(self.router_config(archive_request_id)):
            summary = self.invoke_delivery(lambda: edit_docx.replace_text_delivery(
                source, "archive-alias.docx", specification, protected_inputs=(protected_json,),
            ))

        self.assertEqual(summary["status"], "edited")
        self.assertEqual(summary["archive_status"], "failed")
        self.assertTrue(summary["warnings"])
        self.assertEqual(read_docx("archive-alias.docx"), "合同 XYZ")
        self.assertEqual(protected_json.read_bytes(), protected_json_bytes)
        self.assertEqual(archive_target.read_bytes(), protected_json_bytes)
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_archive_copy_failure_keeps_new_main_document_and_old_archive(self):
        record = "copy-failure"
        output = Path("copy-failure.docx")
        archive = Path("output") / record / output.name
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"keep previous archive")
        original_copy = create_docx.shutil.copyfileobj

        def fail_archive_copy(source_stream, destination_stream, length=0):
            if Path(source_stream.name).resolve() == output.resolve():
                raise OSError("injected archive copy failure")
            return original_copy(source_stream, destination_stream, length)

        specification = {"blocks": [{"type": "paragraph", "text": "created despite archive failure"}]}
        with self.router_environment(self.router_config(record)):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with patch("create_docx.shutil.copyfileobj", side_effect=fail_archive_copy):
                    summary = create_docx.create_document_delivery(
                        specification, output.name, base_dir=self.work, verify=True,
                    )
            archive_call.assert_called_once()

        self.assertEqual(summary["status"], "verified")
        self.assertEqual(summary["archive_status"], "failed")
        self.assertIn("injected archive copy failure", "\n".join(summary["warnings"]))
        self.assertEqual(read_docx(output), "created despite archive failure")
        self.assertEqual(archive.read_bytes(), b"keep previous archive")
        self.assertEqual([path.name for path in archive.parent.iterdir()], [output.name])

    def test_archive_mkdir_failure_keeps_edited_main_document_and_old_archive(self):
        record = "mkdir-failure"
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        output = Path("mkdir-failure.docx")
        archive = Path("output") / record / output.name
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"keep previous archive")
        real_mkdir = Path.mkdir

        def fail_archive_directory(path, *args, **kwargs):
            if Path(path).resolve() == archive.parent.resolve():
                raise PermissionError("injected archive directory failure")
            return real_mkdir(path, *args, **kwargs)

        with self.router_environment(self.router_config(record)):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with patch.object(Path, "mkdir", new=fail_archive_directory):
                    summary = edit_docx.replace_text_delivery(
                        source, output,
                        {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]},
                    )
            archive_call.assert_called_once()

        self.assertEqual(summary["status"], "edited")
        self.assertEqual(summary["archive_status"], "failed")
        self.assertIn("injected archive directory failure", "\n".join(summary["warnings"]))
        self.assertEqual(read_docx(output), "合同 XYZ")
        self.assertEqual(archive.read_bytes(), b"keep previous archive")
        self.assertEqual([path.name for path in archive.parent.iterdir()], [output.name])

    def test_primary_failure_does_not_attempt_archiving_or_create_output(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        with self.router_environment(self.router_config()):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with self.assertRaises(ValueError):
                    edit_docx.replace_text_delivery(
                        source, "failed.docx",
                        {"replacements": [{"old": "ABC", "new": "XYZ", "count": 2}]},
                    )
            archive_call.assert_not_called()

        self.assertFalse(Path("failed.docx").exists())
        self.assertFalse(Path("output").exists())
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_source_and_explicitly_protected_collisions_fail_before_mutation(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        source_bytes = source.read_bytes()
        table_source = self.document("table-source.docx", [
            {"type": "table", "rows": [["项目", "金额"], ["甲", "10"]], "header": True},
        ])
        red, blue = png_pixel(255, 0, 0), png_pixel(0, 0, 255)
        Path("red.png").write_bytes(red)
        Path("blue.png").write_bytes(blue)
        image_source = self.document("image-source.docx", [
            {"type": "image", "path": "red.png", "width_mm": 5,
             "height_mm": 5, "alt": "red pixel"},
        ])
        image_bytes = image_source.read_bytes()
        collisions = [
            lambda: edit_docx.replace_text_delivery(source, source, {}),
            lambda: edit_structure.edit_structure_delivery(source, source, {}),
            lambda: edit_tables.edit_tables_delivery(table_source, table_source, {}),
            lambda: replace_image.replace_image_delivery(
                image_source, "blue.png", image_source, {},
            ),
            lambda: manage_comments.manage_comments_delivery(source, {}, source),
        ]
        with self.router_environment(self.router_config()):
            for invoke in collisions:
                with patch.object(
                    archive_deliverables,
                    "archive_outputs",
                    wraps=archive_deliverables.archive_outputs,
                ) as archive_call:
                    with self.assertRaises(ValueError):
                        invoke()
                archive_call.assert_not_called()

            protected = Path("protected.docx")
            protected.write_bytes(b"keep protected input")
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with self.assertRaises(ValueError):
                    edit_docx.replace_text_delivery(
                        source, protected,
                        {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]},
                        protected_inputs=(protected,),
                    )
            archive_call.assert_not_called()

        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(image_source.read_bytes(), image_bytes)
        self.assertEqual(protected.read_bytes(), b"keep protected input")
        self.assertFalse(Path("output").exists())

    def test_creation_protects_validated_image_alias_before_mutating_it(self):
        image = Path("image.png")
        image_bytes = png_pixel(255, 0, 0)
        image.write_bytes(image_bytes)
        output_alias = Path("image.docx")
        os.link(image, output_alias)
        alias_bytes = output_alias.read_bytes()
        specification = {"blocks": [{"type": "image", "path": image.name,
                                      "width_mm": 5, "height_mm": 5}]}

        with self.router_environment(self.router_config()):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                with self.assertRaises(ValueError):
                    create_docx.create_document_delivery(
                        specification, output_alias.name, base_dir=self.work,
                    )
            archive_call.assert_not_called()

        self.assertEqual(image.read_bytes(), image_bytes)
        self.assertEqual(output_alias.read_bytes(), alias_bytes)
        self.assertFalse(Path("output").exists())

    def test_comment_list_is_read_only_and_low_level_functions_do_not_archive(self):
        source = self.document("source.docx", [{"type": "paragraph", "text": "合同 ABC"}])
        table_source = self.document("table-source.docx", [
            {"type": "table", "rows": [["项目", "金额"], ["甲", "10"]], "header": True},
        ])
        red, blue = png_pixel(255, 0, 0), png_pixel(0, 0, 255)
        Path("red.png").write_bytes(red)
        Path("blue.png").write_bytes(blue)
        image_source = self.document("image-source.docx", [
            {"type": "image", "path": "red.png", "width_mm": 5,
             "height_mm": 5, "alt": "red pixel"},
        ])

        with self.router_environment(self.router_config()):
            with patch.object(
                archive_deliverables,
                "archive_outputs",
                wraps=archive_deliverables.archive_outputs,
            ) as archive_call:
                listed = manage_comments.manage_comments_delivery(source, {"action": "list"})
            archive_call.assert_not_called()
        self.assertEqual(listed["action"], "list")
        self.assertFalse(Path("output").exists())

        with self.router_environment(self.router_config("low-level-must-not-archive")):
            create_docx.create_document(
                {"blocks": [{"type": "paragraph", "text": "low-level generation"}]},
                "low-created.docx", base_dir=self.work, verify=True,
            )
            edit_docx.replace_text(
                source, "low-text.docx",
                {"replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]},
            )
            edit_structure.edit_structure(
                source, "low-structure.docx",
                {"operations": [{"type": "insert_paragraph", "paragraph_index": 0,
                                 "expected_text": "合同 ABC", "position": "after",
                                 "text": "low-level structure"}]},
            )
            edit_tables.edit_tables(
                table_source, "low-table.docx",
                {"operation": {"type": "insert_row", "table_index": 0, "row_index": 1,
                               "expected_rows": 2, "expected_text": ["甲", "10"],
                               "position": "after", "cells": ["乙", "20"]}},
            )
            replace_image.replace_image(
                image_source, "blue.png", "low-image.docx", self.image_selection(image_source, red),
            )
            manage_comments.manage_comments(
                source,
                {"action": "add", "paragraph_index": 0, "expected_text": "合同 ABC",
                 "anchor_text": "合同 ABC", "text": "low-level comment", "author": "审阅者"},
                "low-comments.docx",
            )

        for output, text in (
            ("low-created.docx", "low-level generation"),
            ("low-text.docx", "合同 XYZ"),
            ("low-structure.docx", "合同 ABC\nlow-level structure"),
            ("low-table.docx", "项目\t金额\n甲\t10\n乙\t20"),
        ):
            self.assertEqual(read_docx(output), text)
        self.assertEqual(self._package_members(Path("low-image.docx"))[self.image_selection(
            image_source, red)["media_part"]], blue)
        self.assertEqual(manage_comments.manage_comments("low-comments.docx", {"action": "list"})[
            "comments"][0]["text"], "low-level comment")
        self.assertFalse(Path("output").exists())


if __name__ == "__main__":
    unittest.main()
