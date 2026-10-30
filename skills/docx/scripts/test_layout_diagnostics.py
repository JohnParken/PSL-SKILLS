"""Coverage for the two non-blocking static layout diagnostics."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest("DOCX requires Python >=3.10")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_docx
from office.layout_diagnostics import (
    FIXED_LINE_HEIGHT_RISK,
    LAYOUT_TRUNCATION_NOTICE,
    MAX_LAYOUT_ISSUES,
    PARAGRAPH_WIDTH_RISK,
    diagnose_layout,
    layout_warning_messages,
)


@unittest.skipUnless(expat.version_info >= (2, 6, 0), "DOCX requires Expat >=2.6")
class LayoutDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.original_directory = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-layout-test-", dir=".")
        self.work = Path(self.temporary.name).resolve()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.original_directory)
        self.temporary.cleanup()

    def preflight(self, specification):
        with redirect_stderr(io.StringIO()):
            return create_docx.preflight_document(specification, base_dir=self.work)

    @staticmethod
    def png_pixel():
        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
                + chunk(b"IEND", b""))

    def test_all_default_themes_return_empty_diagnostics(self):
        for theme in ("official", "business", "academic"):
            with self.subTest(theme=theme):
                result = self.preflight({
                    "theme": theme,
                    "blocks": [
                        {"type": "title", "text": "Heading"},
                        {"type": "heading", "level": 1, "text": "Section"},
                        {"type": "paragraph", "text": "Body"},
                        {"type": "table", "header": True,
                         "rows": [["Column"], ["Value"]]},
                    ],
                })
                self.assertEqual(result["status"], "preflight-ok")
                self.assertEqual(result["layout_issues"], [])
                self.assertFalse(result["layout_issues_truncated"])
                self.assertEqual(result["warnings"], [])

    def test_inherited_formats_and_run_sizes_use_serialized_effective_values(self):
        result = self.preflight({
            "styles": {"body": {"size_pt": 18, "line_spacing_pt": 16}},
            "blocks": [
                {"type": "paragraph", "runs": [{"text": "smaller", "size_pt": 9}]},
                {"type": "paragraph", "runs": [
                    {"text": "small", "size_pt": 9}, {"text": "visible", "size_pt": 17.1},
                ]},
                {"type": "paragraph", "runs": [
                    {"text": "visible", "size_pt": 9}, {"text": "", "size_pt": 200},
                ]},
                {"type": "paragraph", "runs": []},
                {"type": "paragraph", "runs": [{"text": "", "size_pt": 200}]},
                {"type": "paragraph", "text": "override", "format": {"size_pt": 15}},
            ],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [(FIXED_LINE_HEIGHT_RISK, "blocks[1]")],
        )

    def test_fixed_to_multiple_spacing_switch_disables_fixed_height_warning(self):
        result = self.preflight({
            "styles": {"body": {"size_pt": 16, "line_spacing_pt": 10}},
            "blocks": [
                {"type": "paragraph", "text": "fixed"},
                {"type": "paragraph", "text": "multiple",
                 "format": {"line_spacing_multiple": 1.2}},
            ],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [(FIXED_LINE_HEIGHT_RISK, "blocks[0]")],
        )

    def test_font_size_and_line_spacing_compare_after_generator_rounding(self):
        result = self.preflight({
            "styles": {"body": {"size_pt": 10.24, "line_spacing_pt": 10.49}},
            "blocks": [
                {"type": "paragraph", "text": "rounds equal"},
                {"type": "paragraph", "text": "rounds larger",
                 "format": {"size_pt": 10.25, "line_spacing_pt": 10.24}},
            ],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [(FIXED_LINE_HEIGHT_RISK, "blocks[1]")],
        )

    def test_heading_title_and_subtitle_styles_are_included(self):
        result = self.preflight({
            "styles": {
                "title": {"line_spacing_pt": 4},
                "subtitle": {"line_spacing_pt": 4},
                "heading1": {"line_spacing_pt": 4},
            },
            "blocks": [
                {"type": "title", "text": "Title"},
                {"type": "subtitle", "text": "Subtitle"},
                {"type": "heading", "level": 1, "text": "Heading"},
            ],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [
                (FIXED_LINE_HEIGHT_RISK, "blocks[0]"),
                (FIXED_LINE_HEIGHT_RISK, "blocks[1]"),
                (FIXED_LINE_HEIGHT_RISK, "blocks[2]"),
            ],
        )

    def test_paragraph_width_uses_serialized_page_and_indent_twips(self):
        result = self.preflight({
            "page": {"width_mm": 100, "height_mm": 200,
                     "margin_left_mm": 0, "margin_right_mm": 0},
            "styles": {"body": {"left_indent_pt": 141.7, "right_indent_pt": 141.75}},
            "blocks": [
                {"type": "paragraph", "text": "content"},
                {"type": "paragraph", "text": ""},
                {"type": "paragraph", "runs": []},
                {"type": "paragraph", "runs": [{"text": "", "size_pt": 200}]},
            ],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [(PARAGRAPH_WIDTH_RISK, "blocks[0]")],
        )

    def test_table_header_body_overrides_margins_and_image_only_width(self):
        image_path = self.work / "pixel.png"
        image_path.write_bytes(self.png_pixel())
        result = self.preflight({
            "styles": {
                "table_header": {"size_pt": 16, "line_spacing_pt": 10},
                "table_body": {"size_pt": 10, "line_spacing_pt": 16},
            },
            "blocks": [{
                "type": "table",
                "header_rows": 1,
                "widths_mm": [20],
                "format": {"cell_margin_left_mm": 1.5, "cell_margin_right_mm": 1.5},
                "header_format": {"line_spacing_multiple": 1.2},
                "body_format": {"left_indent_pt": 24, "right_indent_pt": 24.2,
                                "size_pt": 20, "line_spacing_multiple": 1.2},
                "rows": [
                    ["header"],
                    [{"text": "", "image": {"path": "pixel.png", "alt": "pixel",
                                                "width_mm": 1, "height_mm": 1}}],
                ],
            }],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [
                (PARAGRAPH_WIDTH_RISK, "blocks[0].rows[1][0]"),
            ],
        )

    def test_empty_table_cells_are_skipped_but_image_only_cells_are_content(self):
        image_path = self.work / "pixel.png"
        image_path.write_bytes(self.png_pixel())
        result = self.preflight({
            "styles": {"table_body": {"size_pt": 20, "line_spacing_pt": 10,
                                       "left_indent_pt": 1000}},
            "blocks": [{
                "type": "table", "widths_mm": [20],
                "rows": [[""], [{"text": "", "image": {
                    "path": "pixel.png", "alt": "pixel", "width_mm": 1, "height_mm": 1,
                }}]],
            }],
        })
        self.assertEqual(
            [(item["code"], item["location"]) for item in result["layout_issues"]],
            [(PARAGRAPH_WIDTH_RISK, "blocks[0].rows[1][0]")],
        )

    def test_input_and_normalized_spec_are_not_mutated_and_xml_bytes_are_unchanged(self):
        specification = {
            "styles": {"body": {"size_pt": 24, "line_spacing_pt": 10,
                                 "left_indent_pt": 1000}},
            "blocks": [{"type": "paragraph", "text": "private document text"}],
        }
        original = copy.deepcopy(specification)
        validated = create_docx._validate_spec(specification, self.work)
        normalized_before = copy.deepcopy(validated)
        expected_parts = create_docx._package_parts(validated)
        issues, truncated = diagnose_layout(validated)
        self.assertEqual(len(issues), 2)
        self.assertFalse(truncated)
        self.assertEqual(validated, normalized_before)
        self.assertEqual(create_docx._package_parts(validated), expected_parts)

        preflight = self.preflight(specification)
        self.assertEqual(specification, original)
        self.assertNotIn("private document text", json.dumps(preflight["layout_issues"]))
        self.assertNotIn(str(self.work), json.dumps(preflight["layout_issues"]))

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            output = create_docx.create_document(specification, "generated.docx", verify=True)
        self.assertEqual(output, Path("generated.docx"))
        self.assertIn("layout-warning: ", stderr.getvalue())
        self.assertNotIn("archive-warning: layout-warning:", stderr.getvalue())
        self.assertNotIn("private document text", stderr.getvalue())
        self.assertEqual(specification, original)
        with zipfile.ZipFile(output) as package:
            self.assertEqual({name: package.read(name) for name in expected_parts}, expected_parts)

    def test_cap_truncates_without_sentinel_and_warnings_match_issues(self):
        result = self.preflight({
            "page": {"width_mm": 100, "height_mm": 200,
                     "margin_left_mm": 0, "margin_right_mm": 0},
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10,
                                 "left_indent_pt": 300}},
            "blocks": [{"type": "paragraph", "text": f"secret {index}"}
                       for index in range(101)],
        })
        self.assertEqual(len(result["layout_issues"]), MAX_LAYOUT_ISSUES)
        self.assertEqual(len(result["warnings"]), MAX_LAYOUT_ISSUES)
        self.assertTrue(result["layout_issues_truncated"])
        self.assertEqual(result["layout_issues"][-1]["location"], "blocks[49]")
        self.assertEqual(result["layout_issues"][-1]["code"], PARAGRAPH_WIDTH_RISK)
        self.assertEqual(result["warnings"], layout_warning_messages(result["layout_issues"]))
        self.assertNotIn("truncated", json.dumps(result["layout_issues"]).lower())
        self.assertNotIn("secret", json.dumps(result["layout_issues"]).lower())

    def test_low_level_api_emits_truncation_notice_on_stderr(self):
        specification = {
            "page": {"width_mm": 100, "height_mm": 200,
                     "margin_left_mm": 0, "margin_right_mm": 0},
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10,
                                 "left_indent_pt": 300}},
            "blocks": [{"type": "paragraph", "text": f"secret {index}"}
                       for index in range(101)],
        }
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            output = create_docx.create_document(specification, "truncated.docx")
        self.assertEqual(output, Path("truncated.docx"))
        lines = stderr.getvalue().splitlines()
        self.assertEqual(sum(line.startswith("layout-warning:") for line in lines),
                         MAX_LAYOUT_ISSUES + 1)
        self.assertEqual(lines[-1], LAYOUT_TRUNCATION_NOTICE)
        self.assertNotIn("secret", stderr.getvalue())

    def test_low_level_api_swallows_only_stderr_write_failures_after_publish(self):
        class FailingStderr:
            def write(self, value):
                raise ValueError("closed stderr")

            def flush(self):
                raise ValueError("closed stderr")

        specification = {
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10}},
            "blocks": [{"type": "paragraph", "text": "saved"}],
        }
        with redirect_stderr(FailingStderr()):
            output = create_docx.create_document(specification, "published.docx")
        self.assertEqual(output, Path("published.docx"))
        self.assertTrue(output.is_file())

    def test_legacy_cli_preserves_json_stdout_and_does_not_relabel_layout_warnings(self):
        specification = {
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10}},
            "blocks": [{"type": "paragraph", "text": "private cli content"}],
        }
        Path("input.json").write_text(json.dumps(specification), encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = create_docx.main(["input.json", "output.docx"])
        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(len(result["layout_issues"]), 1)
        self.assertIn("layout-warning: FIXED_LINE_HEIGHT_RISK", stderr.getvalue())
        self.assertNotIn("archive-warning: layout-warning:", stderr.getvalue())
        self.assertNotIn("private cli content", stderr.getvalue())
        self.assertEqual(result["warnings"], layout_warning_messages(result["layout_issues"]))

    def test_standard_runner_preserves_layout_result_archive_and_cleanup(self):
        runner_script = SCRIPT_DIR / "run_docx_task.py"
        specification = {
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10}},
            "blocks": [{"type": "paragraph", "text": "private runner content"}],
        }
        environment = dict(os.environ)
        environment.update({
            "PATH": "",
            "DOCX_DEBUG_ROUTER_CONFIG": "0",
            "ROUTER_CONFIG_VARIABLES": json.dumps({"commonParam": [
                {"name": "questionId", "value": "layout-test-record"},
            ]}),
        })
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(runner_script), "create", "report.docx"],
            cwd=self.work,
            env=environment,
            input=json.dumps(specification),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["task_status"], "completed")
        self.assertEqual(result["archive_status"], "copied")
        self.assertTrue(result["cleanup"]["cleanup_complete"])
        self.assertTrue(result["cleanup"]["deliverables_unchanged"])
        self.assertFalse(result["cleanup"]["manifest_retained"])
        self.assertEqual(result["layout_issues"][0]["code"], FIXED_LINE_HEIGHT_RISK)
        self.assertIn("layout-warning: FIXED_LINE_HEIGHT_RISK", "\n".join(result["warnings"]))
        self.assertNotIn("archive-warning: layout-warning:", "\n".join(result["warnings"]))
        archived = self.work / "output" / "layout-test-record" / "report.docx"
        self.assertEqual(Path(result["output"]).read_bytes(), archived.read_bytes())
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ["output", "report.docx"])
        self.assertNotIn("private runner content", json.dumps(result["layout_issues"]))
        self.assertEqual(completed.stderr, "")

    def test_delivery_appends_layout_warnings_without_replacing_archive_warnings(self):
        specification = {
            "styles": {"body": {"size_pt": 20, "line_spacing_pt": 10}},
            "blocks": [{"type": "paragraph", "text": "private delivery content"}],
        }
        environment = {
            "DOCX_DEBUG_ROUTER_CONFIG": "0",
            "ROUTER_CONFIG_VARIABLES": json.dumps({"commonParam": [
                {"name": "questionId", "value": "layout-archive-failure"},
            ]}),
        }
        with patch.dict(os.environ, environment), patch(
            "archive_deliverables.archive_outputs",
            side_effect=RuntimeError("injected archive copy failure"),
        ):
            summary = create_docx.create_document_delivery(
                specification, "delivery.docx", base_dir=self.work,
            )
        self.assertEqual(summary["status"], "verified")
        self.assertEqual(summary["archive_status"], "failed")
        self.assertEqual(summary["warnings"][0], "injected archive copy failure")
        self.assertEqual(summary["warnings"][1], layout_warning_messages(summary["layout_issues"])[0])
        self.assertEqual(summary["layout_issues"][0]["code"], FIXED_LINE_HEIGHT_RISK)
        self.assertTrue(Path(summary["output"]).is_file())


if __name__ == "__main__":
    unittest.main()
