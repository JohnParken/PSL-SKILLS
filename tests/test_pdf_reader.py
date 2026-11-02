"""Behavioral PDF reader acceptance tests, with independently authored oracles.

Run from the repository root with:
    python -B -m unittest discover -s tests -p test_pdf_reader.py -v

ReportLab and Pillow are development-only fixture dependencies. Test task files
live in isolated temporary directories outside the repository. No network, OCR,
office application, or downloaded PDF is required by this reproducible suite.
"""

import errno
import hashlib
import importlib.util
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import pymupdf
    from PIL import Image
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
except ImportError as import_error:
    DEPENDENCY_ERROR = str(import_error)
else:
    DEPENDENCY_ERROR = None


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "pdf"
SCRIPT = SKILL_ROOT / "scripts" / "read_pdf.py"
TABLE_ORACLE = [
    ["Product", "Units", "Amount"],
    ["Widget A", "7", "1,234.50"],
    ["Widget B", "3", "987.65"],
    ["Total", "10", "2,222.15"],
]
PAGE_MARKERS = {1: "ALPHA_42", 2: "BETA_17", 3: "GAMMA_03"}


def make_reportlab_pdf():
    """Generate a three-page input whose contents are authored outside reader code."""
    target = io.BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(target, pagesize=(612, 792), invariant=1)
    document.setTitle("Independent PDF acceptance fixture")
    document.setAuthor("PSL-SKILLS PDF acceptance tests")
    document.bookmarkPage("sales")
    document.addOutlineEntry("Quarterly sales", "sales", level=0)
    document.setFont("Helvetica", 18)
    document.drawString(72, 740, "Quarterly sales 2026")
    document.setFont("STSong-Light", 16)
    document.drawString(72, 710, "季度销售报告")
    document.setFont("Helvetica", 12)
    document.drawString(72, 680, "Only page one contains ALPHA_42.")
    table = Table(TABLE_ORACLE, colWidths=[150, 80, 120], rowHeights=[28] * 4)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    table.wrap(500, 600)
    table.drawOn(document, 72, 480)
    document.drawString(72, 450, "Outside the title clip: DELTA_99.")
    document.drawString(72, 420, "Reference: example.org")
    document.linkURL("https://example.org/", (72, 418, 210, 434), relative=0)
    document.showPage()
    document.bookmarkPage("notes")
    document.addOutlineEntry("Notes and illustration", "notes", level=0)
    document.setFont("Helvetica", 18)
    document.drawString(72, 740, "Second page BETA_17")
    document.setFont("STSong-Light", 16)
    document.drawString(72, 710, "第二页：合计 10 件")
    document.setFont("Helvetica", 12)
    document.drawString(72, 680, "A small illustration is not a scanned text page.")
    illustration = Image.new("RGB", (120, 80), "#d13438")
    illustration.paste("#106ebe", (40, 0, 80, 80))
    illustration.paste("#107c10", (80, 0, 120, 80))
    document.drawImage(ImageReader(illustration), 360, 500, width=120, height=80)
    document.showPage()
    document.setFont("Helvetica", 18)
    document.drawString(72, 740, "Third page GAMMA_03")
    document.setFont("Helvetica", 12)
    document.drawString(72, 710, "A separate page tests source page numbers and selection.")
    document.showPage()
    document.save()
    return target.getvalue()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot(directory):
    """Record names and bytes, so unexpected caches and overwrites are visible."""
    return {
        path.relative_to(directory).as_posix(): sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def all_diagnostics(result):
    diagnostics = list(result.get("warnings", []))
    diagnostics.extend(result.get("diagnostics", []))
    for page in result.get("pages", []):
        diagnostics.extend(page.get("diagnostics", []))
    return diagnostics


@unittest.skipIf(DEPENDENCY_ERROR is not None, f"Development fixture dependency unavailable: {DEPENDENCY_ERROR}")
class PdfReaderAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT.is_file():
            raise unittest.SkipTest("PDF reader entry point is not present")
        cls.base_pdf = make_reportlab_pdf()
        with pymupdf.open(stream=cls.base_pdf, filetype="pdf") as original:
            cls.encrypted_pdf = original.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="independent-owner-password",
                user_pw="correct-reader-password",
            )
            page_image = original[0].get_pixmap(dpi=90).tobytes("png")
        with pymupdf.open() as scanned:
            scanned.new_page(width=612, height=792).insert_image(
                pymupdf.Rect(0, 0, 612, 792), stream=page_image,
            )
            cls.scanned_pdf = scanned.tobytes()
            scanned[0].insert_text((72, 25), "Native approval stamp", fontsize=10)
            cls.mixed_pdf = scanned.tobytes()
        with pymupdf.open() as blank:
            blank.new_page(width=612, height=792)
            cls.blank_pdf = blank.tobytes()
        with pymupdf.open(stream=cls.base_pdf, filetype="pdf") as rotated:
            rotated[0].set_rotation(90)
            cls.rotated_pdf = rotated.tobytes()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdf_phase1_tests_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = self.root / "task"
        self.task.mkdir()
        self.input_file = self.task / "source.pdf"
        self.input_file.write_bytes(self.base_pdf)

    def call_cli(self, *arguments, expected_exit=0, script=SCRIPT, guard_ocr=False, extra_env=None):
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if extra_env:
            environment.update(extra_env)
        if guard_ocr:
            # An observable trap proves PyMuPDF's OCR entry point was not called,
            # including calls whose exceptions might otherwise be caught.
            guard = (
                "from pathlib import Path\n"
                "import pymupdf,runpy,sys\n"
                "def denied_ocr(*args,**kwargs):\n"
                "    Path('ocr_was_called').write_text('called',encoding='utf-8')\n"
                "    raise RuntimeError('OCR must be opt-in and is not phase one')\n"
                "pymupdf.Page.get_textpage_ocr=denied_ocr\n"
                "script=sys.argv[1]\n"
                "sys.argv=sys.argv[1:]\n"
                "sys.path.insert(0,str(Path(script).parent))\n"
                "runpy.run_path(script,run_name='__main__')\n"
            )
            command = [sys.executable, "-B", "-c", guard, str(script), *arguments]
        else:
            command = [sys.executable, "-B", str(script), *arguments]
        process = subprocess.run(command, cwd=self.task, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=40, check=False)
        self.assertEqual(process.returncode, expected_exit, f"arguments={arguments!r}\nstdout={process.stdout}\nstderr={process.stderr}")
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"stdout must contain exactly one JSON object: {error}\n{process.stdout}\n{process.stderr}")
        self.assertIsInstance(result, dict)
        self.assertNotIn("Traceback (most recent call last)", process.stderr)
        return result, process

    def read_result(self, *arguments, expected_exit=0):
        summary, process = self.call_cli("read", "source.pdf", *arguments, expected_exit=expected_exit)
        if "--output-dir" in arguments:
            output_directory = self.task / arguments[arguments.index("--output-dir") + 1]
            result_file = output_directory / "result.json"
            self.assertTrue(result_file.is_file())
            result = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], result["status"])
            self.assertEqual(summary["coverage"], result["coverage"])
            return result, summary, process
        return summary, summary, process

    def assert_error(self, *arguments):
        result, process = self.call_cli(*arguments, expected_exit=1)
        self.assertEqual(result.get("status"), "error")
        self.assertTrue(process.stderr.strip(), "Errors must be visible on stderr as well as machine-readable stdout")
        return result

    def test_environment_check_distinguishes_environment_from_artifact_validation(self):
        before = file_snapshot(self.task)
        result, _ = self.call_cli("check", "--input", "source.pdf", "--write-check")
        self.assertEqual(result["status"], "environment-ok")
        self.assertEqual(result["check_kind"], "environment")
        self.assertEqual(file_snapshot(self.task), before, "The probe must remove only its own new file")

    def test_environment_check_honors_input_byte_budget_and_hard_limit(self):
        before = file_snapshot(self.task)
        actual_size = self.input_file.stat().st_size
        self.assert_error("check", "--input", "source.pdf", "--max-input-bytes", str(actual_size - 1))
        result, _ = self.call_cli("check", "--input", "source.pdf", "--max-input-bytes", str(actual_size))
        self.assertEqual(result["status"], "environment-ok")
        self.assertEqual(result["check_kind"], "environment")
        # Exercise configuration validation without allocating a large file.
        self.assert_error("check", "--input", "source.pdf", "--max-input-bytes", str(512 * 1024 * 1024 + 1))
        self.assertEqual(file_snapshot(self.task), before)

    def test_inspect_reads_metadata_and_outline_without_modifying_input(self):
        before = sha256(self.input_file)
        result, _ = self.call_cli("inspect", "source.pdf")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["document"]["page_count"], 3)
        self.assertEqual(result["document"]["metadata"]["title"], "Independent PDF acceptance fixture")
        self.assertIn("Quarterly sales", json.dumps(result["document"].get("toc", [])))
        self.assertEqual(sha256(self.input_file), before)
        self.assertEqual(set(file_snapshot(self.task)), {"source.pdf"})

    def test_default_text_has_independent_chinese_english_and_numeric_oracles(self):
        before = sha256(self.input_file)
        result, _, _ = self.read_result()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual([page["number"] for page in result["pages"]], [1, 2, 3])
        first = result["pages"][0]["text"]
        self.assertIn("季度销售报告", first)
        self.assertIn("ALPHA_42", first)
        self.assertIn("2,222.15", first)
        self.assertNotIn("BETA_17", first)
        self.assertIn("第二页", result["pages"][1]["text"])
        self.assertEqual(result["coverage"]["processed_pages"], [1, 2, 3])
        self.assertEqual(result["coverage"]["omitted_pages"], [])
        self.assertEqual(sha256(self.input_file), before)
        self.assertEqual(set(file_snapshot(self.task)), {"source.pdf"})

    def test_text_spans_retain_source_page_coordinates_and_fonts(self):
        result, _, _ = self.read_result("--pages", "1")
        page = result["pages"][0]
        spans = [span for block in page["blocks"] for line in block["lines"] for span in line["spans"]]
        title_spans = [span for span in spans if "Quarterly sales" in span["text"]]
        self.assertTrue(title_spans)
        bbox = title_spans[0]["bbox"]
        self.assertEqual(len(bbox), 4)
        # ReportLab authored the title at x=72, baseline=740 on a 792pt page.
        # Font metrics vary, so verify the independent top-left region, not an
        # exact bbox produced by the same extraction engine as the implementation.
        self.assertAlmostEqual(bbox[0], 72, delta=1)
        self.assertTrue(25 < bbox[1] < 60, bbox)
        self.assertTrue(45 < bbox[3] < 80, bbox)
        self.assertIn("Helvetica", title_spans[0]["font"])
        self.assertAlmostEqual(title_spans[0]["size"], 18, delta=0.1)
        self.assertTrue(page["coordinate_system"])

    def test_noncontiguous_page_selection_preserves_physical_page_numbers(self):
        result, _, _ = self.read_result("--pages", "1,3")
        self.assertEqual([page["number"] for page in result["pages"]], [1, 3])
        self.assertEqual(result["coverage"]["requested_pages"], [1, 3])
        self.assertEqual(result["coverage"]["omitted_pages"], [])
        self.assertIn(PAGE_MARKERS[3], result["pages"][1]["text"])
        self.assertNotIn(PAGE_MARKERS[2], "".join(page["text"] for page in result["pages"]))

    def test_page_range_and_json_configuration_agree(self):
        cli, _, _ = self.read_result("--pages", "2-3", "--extract", "text")
        configuration = {"operation": "read", "input": "source.pdf", "pages": [2, 3], "extract": ["text"]}
        (self.task / "task.json").write_text(json.dumps(configuration), encoding="utf-8")
        configured, _ = self.call_cli("run", "task.json")
        self.assertEqual(configured["coverage"], cli["coverage"])
        self.assertEqual([page["text"] for page in configured["pages"]], [page["text"] for page in cli["pages"]])

    def test_clip_filters_content_using_unrotated_top_left_points(self):
        result, _, _ = self.read_result("--pages", "1", "--clip", "60,20,500,95")
        text = result["pages"][0]["text"]
        self.assertIn("Quarterly sales", text)
        self.assertIn("季度销售报告", text)
        self.assertNotIn("ALPHA_42", text)
        self.assertNotIn("DELTA_99", text)

    def test_rotation_keeps_source_coordinates_and_reports_rotation(self):
        self.input_file.write_bytes(self.rotated_pdf)
        result, _, _ = self.read_result("--pages", "1", "--clip", "60,20,500,95")
        page = result["pages"][0]
        self.assertEqual(page["rotation"], 90)
        self.assertIn("Quarterly sales", page["text"])
        self.assertNotIn("DELTA_99", page["text"])

    def test_ruled_table_cells_match_independently_authored_values(self):
        result, _, _ = self.read_result("--pages", "1", "--extract", "text,tables")
        tables = result["pages"][0]["tables"]
        self.assertTrue(tables, "A four-row ruled table must be found")
        matching = [table for table in tables if table["cells"] == TABLE_ORACLE]
        self.assertTrue(matching, f"Expected independently authored rows; got {[table['cells'] for table in tables]!r}")
        table = matching[0]
        self.assertEqual((table["row_count"], table["col_count"]), (4, 3))
        self.assertEqual(len(table["cell_bboxes"]), 4)
        self.assertEqual(len(table["cell_bboxes"][0]), 3)
        self.assertAlmostEqual(table["bbox"][0], 72, delta=2)
        self.assertAlmostEqual(table["bbox"][1], 200, delta=2)

    def test_images_without_output_directory_are_metadata_only(self):
        before = file_snapshot(self.task)
        result, _, _ = self.read_result("--pages", "2", "--extract", "text,images")
        page = result["pages"][0]
        self.assertEqual(result["status"], "ok", "A small decorative image must not imply failed text extraction")
        self.assertTrue(page["images"])
        self.assertTrue(page["image_resources"])
        resource = page["image_resources"][0]
        self.assertEqual((resource["width"], resource["height"]), (120, 80))
        self.assertFalse(resource.get("asset"))
        self.assertEqual(file_snapshot(self.task), before)

    def test_export_json_markdown_images_and_previews_are_reopenable(self):
        before = sha256(self.input_file)
        result, summary, _ = self.read_result("--pages", "1-2", "--extract", "text,tables,images,preview", "--output-dir", "result")
        directory = self.task / "result"
        self.assertTrue(summary["artifacts"])
        markdown = (directory / "result.md").read_text(encoding="utf-8")
        self.assertIn("季度销售报告", markdown)
        self.assertIn("2,222.15", markdown)
        self.assertTrue(result["pages"][1]["image_resources"][0].get("asset"))
        self.assertTrue(all(page["preview"].get("asset") for page in result["pages"]))
        previews = list((directory / "previews").glob("*.png"))
        self.assertEqual(len(previews), 2)
        for preview in previews:
            with Image.open(preview) as image:
                self.assertGreater(image.width, 500)
                self.assertGreater(image.height, 500)
                image.verify()
        extracted = list((directory / "images").glob("*"))
        self.assertTrue(extracted)
        with Image.open(extracted[0]) as image:
            self.assertEqual(image.size, (120, 80))
        self.assertEqual(sha256(self.input_file), before)

    def test_markdown_only_export_has_explicit_summary(self):
        summary, _ = self.call_cli("read", "source.pdf", "--pages", "1", "--output-dir", "markdown", "--formats", "markdown")
        self.assertEqual(summary["status"], "ok")
        self.assertTrue((self.task / "markdown" / "result.md").is_file())
        self.assertFalse((self.task / "markdown" / "result.json").exists())
        self.assertTrue(summary["artifacts"])

    def test_preview_without_output_directory_is_rejected(self):
        self.assert_error("read", "source.pdf", "--extract", "preview")
        self.assertEqual(set(file_snapshot(self.task)), {"source.pdf"})

    def test_scanned_page_is_partial_and_does_not_invoke_ocr(self):
        self.input_file.write_bytes(self.scanned_pdf)
        result, _ = self.call_cli("read", "source.pdf", expected_exit=2, guard_ocr=True, extra_env={"PATH": ""})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["text"].strip(), "")
        self.assertTrue(all_diagnostics(result))
        self.assertIn("ocr", json.dumps(all_diagnostics(result)).lower())
        self.assertFalse((self.task / "ocr_was_called").exists())

    def test_mixed_scanned_and_native_text_reports_missing_scan_content(self):
        self.input_file.write_bytes(self.mixed_pdf)
        result, _, _ = self.read_result(expected_exit=2)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Native approval stamp", result["pages"][0]["text"])
        self.assertNotIn("ALPHA_42", result["pages"][0]["text"])
        self.assertIn("ocr", json.dumps(all_diagnostics(result)).lower())

    def test_truly_blank_page_is_not_misreported_as_an_ocr_failure(self):
        self.input_file.write_bytes(self.blank_pdf)
        result, _, _ = self.read_result()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pages"][0]["text"].strip(), "")
        self.assertNotIn("needs_ocr", json.dumps(all_diagnostics(result)).lower())

    def test_encrypted_pdf_requires_correct_password_file_and_never_echoes_password(self):
        self.input_file.write_bytes(self.encrypted_pdf)
        self.assert_error("read", "source.pdf")
        (self.task / "password.txt").write_text("wrong-secret-password\n", encoding="utf-8")
        wrong, process = self.call_cli("read", "source.pdf", "--password-file", "password.txt", expected_exit=1)
        self.assertEqual(wrong["status"], "error")
        self.assertNotIn("wrong-secret-password", process.stdout + process.stderr)
        (self.task / "password.txt").write_text("correct-reader-password\n", encoding="utf-8")
        result, process = self.call_cli("read", "source.pdf", "--password-file", "password.txt", "--pages", "1")
        self.assertIn("ALPHA_42", result["pages"][0]["text"])
        self.assertNotIn("correct-reader-password", process.stdout + process.stderr)

    def test_page_budget_returns_reliable_partial_coverage(self):
        result, _, _ = self.read_result("--max-pages", "1", expected_exit=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["requested_pages"], [1, 2, 3])
        self.assertEqual(result["coverage"]["processed_pages"], [1])
        self.assertEqual(result["coverage"]["omitted_pages"], [2, 3])
        self.assertIn("ALPHA_42", result["pages"][0]["text"])
        self.assertTrue(all_diagnostics(result))

    def test_character_budget_never_silently_claims_complete_text(self):
        result, _, _ = self.read_result("--max-chars", "40", expected_exit=2)
        self.assertEqual(result["status"], "partial")
        # Whole-item omission is allowed: null expressly means no text result.
        # It must remain a partial outcome with an explicit budget diagnostic.
        self.assertLessEqual(sum(len(page["text"] or "") for page in result["pages"]), 40)
        self.assertTrue(all_diagnostics(result))
        self.assertIn("budget", json.dumps(all_diagnostics(result)).lower())

    def test_input_byte_limit_rejects_before_export(self):
        self.assert_error("read", "source.pdf", "--max-input-bytes", "32", "--output-dir", "too_large")
        self.assertFalse((self.task / "too_large").exists())

    def test_image_pixel_budget_is_visible_partial_result(self):
        result, _, _ = self.read_result("--pages", "2", "--extract", "text,images", "--max-image-pixels", "100", "--output-dir", "pixel_limit", expected_exit=2)
        self.assertEqual(result["status"], "partial")
        self.assertIn("BETA_17", result["pages"][0]["text"])
        self.assertTrue(all_diagnostics(result))
        self.assertFalse(list((self.task / "pixel_limit" / "images").glob("*")))

    def test_output_budget_rejects_without_publishing_truncated_json_or_partial_files(self):
        result = self.assert_error("read", "source.pdf", "--max-output-bytes", "200", "--output-dir", "tiny_output")
        self.assertIn("budget", json.dumps(result).lower())
        self.assertFalse((self.task / "tiny_output").exists())
        self.assert_error("read", "source.pdf", "--max-output-bytes", "200")

    def test_inspect_honors_output_byte_budget(self):
        before = file_snapshot(self.task)
        result = self.assert_error("inspect", "source.pdf", "--max-output-bytes", "100")
        self.assertIn("output_budget_exceeded", json.dumps(result))
        self.assertEqual(file_snapshot(self.task), before)

    def test_markdown_export_summary_obeys_budget_before_creating_output(self):
        with pymupdf.open() as document:
            document.new_page().insert_text((72, 72), "Budget test")
            document.set_metadata({"title": "A" * 20000})
            self.input_file.write_bytes(document.tobytes())
        before = file_snapshot(self.task)
        result = self.assert_error("read", "source.pdf", "--formats", "markdown", "--max-output-bytes", "1000", "--output-dir", "summary_budget")
        self.assertIn("output_budget_exceeded", json.dumps(result))
        self.assertFalse((self.task / "summary_budget").exists())
        self.assertEqual(file_snapshot(self.task), before)

    def test_ancestor_task_directory_cannot_access_skill_installation_as_data(self):
        # Deliberately place a copied installation below the task directory.
        # The actual repository installation must never be the test target.
        install = self.task / "installed_skill"
        shutil.copytree(SKILL_ROOT, install, ignore=shutil.ignore_patterns("__pycache__"))
        (install / "protected.pdf").write_bytes(self.base_pdf)
        (install / "password.txt").write_text("correct-reader-password\n", encoding="utf-8")
        (install / "task.json").write_text(json.dumps({"operation": "read", "input": "source.pdf"}), encoding="utf-8")
        (self.task / "encrypted.pdf").write_bytes(self.encrypted_pdf)
        before = file_snapshot(install)
        script = install / "scripts" / "read_pdf.py"
        cases = [
            ("read", "installed_skill/protected.pdf"),
            ("read", "encrypted.pdf", "--password-file", "installed_skill/password.txt"),
            ("run", "installed_skill/task.json"),
            ("read", "source.pdf", "--output-dir", "installed_skill/result"),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, _ = self.call_cli(*arguments, expected_exit=1, script=script)
                self.assertEqual(result["status"], "error")
                self.assertEqual(file_snapshot(install), before)
                self.assertFalse((install / "result").exists())

    def test_output_root_open_failure_is_cleaned_or_explicitly_reported(self):
        specification = importlib.util.spec_from_file_location("pdf_io_regression_tests", SKILL_ROOT / "scripts" / "pdf_io.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        (self.task / "user_file.txt").write_text("preserve this existing file", encoding="utf-8")
        before = file_snapshot(self.task)
        original_cwd = Path.cwd()
        actual_open = os.open
        injected_calls = []

        def fail_open_after_mkdir(path, *arguments, **keywords):
            if path == "broken_output":
                injected_calls.append(path)
                self.assertTrue((self.task / "broken_output").is_dir(), "Failure must occur after creation, not before")
                raise OSError(errno.EIO, "Injected failure while opening newly created output root")
            return actual_open(path, *arguments, **keywords)

        try:
            os.chdir(self.task)
            with module.Workspace(SKILL_ROOT) as workspace:
                with patch.object(module.os, "open", side_effect=fail_open_after_mkdir):
                    with self.assertRaises((OSError, module.PdfTaskError)) as raised:
                        workspace.export("broken_output", {"result.json": b"{}"})
            self.assertTrue(injected_calls)
            if (self.task / "broken_output").exists():
                self.assertEqual(getattr(raised.exception, "code", None), "cleanup_incomplete")
            self.assertEqual(file_snapshot(self.task), before)
        finally:
            os.chdir(original_cwd)

    def test_concurrent_exports_to_distinct_directories_are_isolated(self):
        before = sha256(self.input_file)

        def export(directory):
            return self.read_result("--pages", "2", "--extract", "text,images,preview", "--output-dir", directory)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(export, ("concurrent_a", "concurrent_b")))
        self.assertEqual(first[0]["pages"], second[0]["pages"])
        self.assertEqual(file_snapshot(self.task / "concurrent_a"), file_snapshot(self.task / "concurrent_b"))
        self.assertEqual(sha256(self.input_file), before)

    def test_existing_output_directory_and_files_are_protected(self):
        destination = self.task / "existing"
        destination.mkdir()
        sentinel = destination / "result.json"
        sentinel.write_text("user-owned result", encoding="utf-8")
        before = file_snapshot(self.task)
        self.assert_error("read", "source.pdf", "--output-dir", "existing")
        self.assertEqual(file_snapshot(self.task), before)
        self.assert_error("read", "source.pdf", "--output-dir", "source.pdf")
        self.assertEqual(file_snapshot(self.task), before)

    def test_absolute_and_parent_input_paths_are_rejected(self):
        outside = self.root / "outside.pdf"
        outside.write_bytes(self.base_pdf)
        for value in (str(self.input_file), "../outside.pdf", "subdir/../../outside.pdf"):
            with self.subTest(value=value):
                self.assert_error("read", value)

    def test_absolute_and_parent_output_paths_are_rejected(self):
        for value in (str(self.root / "absolute_output"), "../outside_output"):
            with self.subTest(value=value):
                self.assert_error("read", "source.pdf", "--output-dir", value)
        self.assertFalse((self.root / "absolute_output").exists())
        self.assertFalse((self.root / "outside_output").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks are unavailable on this platform")
    def test_symlink_inputs_parent_directories_and_outputs_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "outside.pdf").write_bytes(self.base_pdf)
        (self.task / "linked.pdf").symlink_to(outside / "outside.pdf")
        (self.task / "linked_directory").symlink_to(outside, target_is_directory=True)
        (self.task / "linked_output").symlink_to(outside, target_is_directory=True)
        before = file_snapshot(outside)
        self.assert_error("read", "linked.pdf")
        self.assert_error("read", "linked_directory/outside.pdf")
        self.assert_error("read", "source.pdf", "--output-dir", "linked_output/new")
        self.assertEqual(file_snapshot(outside), before)

    def test_malformed_pdf_and_missing_file_report_errors_with_json(self):
        for data in (b"", b"not a PDF", b"%PDF-1.7\ntruncated and missing xref"):
            with self.subTest(data=data):
                self.input_file.write_bytes(data)
                self.assert_error("read", "source.pdf")
        self.assert_error("inspect", "missing.pdf")

    def test_invalid_page_and_clip_arguments_reject_instead_of_silent_fallback(self):
        for pages in ("0", "4", "3-1", "1,nope"):
            with self.subTest(pages=pages):
                self.assert_error("read", "source.pdf", "--pages", pages)
        for clip in ("100,100,20,20", "0,0,nan,90", "0,0,100"):
            with self.subTest(clip=clip):
                self.assert_error("read", "source.pdf", "--clip", clip)

    def test_json_config_rejects_plaintext_password_unknown_options_and_invalid_json(self):
        path = self.task / "task.json"
        for patch in ({"password": "secret-must-not-appear"}, {"unrecognized_option": True}):
            path.write_text(json.dumps({"operation": "read", "input": "source.pdf", **patch}), encoding="utf-8")
            result, process = self.call_cli("run", "task.json", expected_exit=1)
            self.assertEqual(result["status"], "error")
            self.assertNotIn("secret-must-not-appear", process.stdout + process.stderr)
        path.write_text("{invalid-json", encoding="utf-8")
        self.assert_error("run", "task.json")

    def test_repeated_reads_do_not_write_into_source_installation(self):
        install = self.root / "installed" / "pdf"
        shutil.copytree(SKILL_ROOT, install, ignore=shutil.ignore_patterns("__pycache__"))
        for path in install.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        install.chmod(0o555)
        before = file_snapshot(install)
        script = install / "scripts" / "read_pdf.py"
        first, _ = self.call_cli("read", "source.pdf", "--pages", "1", script=script)
        second, _ = self.call_cli("read", "source.pdf", "--pages", "1", script=script)
        self.assertEqual(first["pages"], second["pages"])
        self.assertEqual(file_snapshot(install), before)
        # Restore only this test's copied directory permissions for cleanup.
        install.chmod(0o755)
        for path in install.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)


if __name__ == "__main__":
    unittest.main()
