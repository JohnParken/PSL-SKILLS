"""Scoped missing-template recovery without weakening required dependencies."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
import test_ofd as BASE

READER = BASE.READER


class TemplateRecoveryTests(unittest.TestCase):
    """Exercise the real parser with in-memory ZIP bytes, not filesystem mocks."""

    def read(self, files, *, full=False, **options):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in files.items():
                archive.writestr(name, contents)
        entry = READER.read_ofd_structure if full else READER.read_ofd
        with patch.object(READER, "read_input", return_value=buffer.getvalue()):
            return entry("input.ofd", **options)

    def fixture(self, location="absent.xml", *, selected=False):
        return BASE.fixture(
            text="正常正文",
            common_extra=f'<ofd:TemplatePage ID="9" BaseLoc="{location}"/>',
            page_extra='<ofd:Template TemplateID="9"/>' if selected else "")

    def test_missing_unused_template_retains_text_and_partial(self):
        result = self.read(self.fixture())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["records"][0]["text"], "正常正文")
        self.assertTrue(result["done"])
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["missing_unselected_template"])
        self.assertEqual(result["coverage"], "selected_page_text_occurrences_only")

    def test_full_structure_still_rejects_missing_template(self):
        with self.assertRaisesRegex(READER.ReadError, "missing package reference"):
            self.read(self.fixture(), full=True)

    def test_selected_missing_template_still_fails(self):
        with self.assertRaisesRegex(READER.ReadError, "missing XML"):
            self.read(self.fixture(selected=True))

    def test_later_page_dependency_is_not_silently_skipped(self):
        files = self.fixture()
        files["Doc/Document.xml"] = files["Doc/Document.xml"].replace(
            b"</ofd:Pages>", b'<ofd:Page ID="2" BaseLoc="Pages/Q.xml"/></ofd:Pages>')
        files["Doc/Pages/Q.xml"] = BASE.xml("Page", '<ofd:Template TemplateID="9"/>')
        self.assertEqual(self.read(files)["records"][0]["text"], "正常正文")
        with self.assertRaises(READER.ReadError):
            self.read(files, page_start=2)
        with self.assertRaises(READER.ReadError):
            self.read(files, page_end=2)

    def test_unsafe_unused_locations_still_fail(self):
        for location in ("../../escape.xml", "https://example.invalid/x", "//host/x", "x%2exml", ""):
            with self.subTest(location=location), self.assertRaises(READER.ReadError):
                self.read(self.fixture(location))

    def test_directory_and_duplicate_id_still_fail(self):
        files = self.fixture("folder")
        files["Doc/folder/"] = b""
        with self.assertRaisesRegex(READER.ReadError, "directory"):
            self.read(files)
        files = self.fixture()
        files["Doc/Document.xml"] = files["Doc/Document.xml"].replace(
            b"</ofd:CommonData>", b'<ofd:TemplatePage ID="9" BaseLoc="other.xml"/></ofd:CommonData>')
        with self.assertRaisesRegex(READER.ReadError, "duplicate/missing template ID"):
            self.read(files)

    def test_reference_budget_still_applies(self):
        with patch.dict(READER.LIMITS, references=1):
            with self.assertRaisesRegex(READER.ReadError, "limit: references"):
                self.read(self.fixture())

    def test_existing_selected_template_preserves_text(self):
        files = self.fixture("T.xml", selected=True)
        files["Doc/T.xml"] = BASE.xml(
            "Page", "<ofd:Content><ofd:TextObject><ofd:TextCode>模板</ofd:TextCode>"
            "</ofd:TextObject></ofd:Content>")
        result = self.read(files)
        self.assertEqual(result["records"][0]["text"], "模板正常正文")
        self.assertNotIn("missing_unselected_template", [d["code"] for d in result.get("diagnostics", [])])

    def test_cli_partial_and_failure_keep_exit_contract(self):
        buffer = io.StringIO()
        # Only input bytes are substituted; the real main/error mapping is exercised.
        files = self.fixture()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in files.items():
                archive.writestr(name, contents)
        with patch.object(READER, "read_input", return_value=data.getvalue()), patch.object(sys, "stdout", buffer):
            code = READER.main(["input.ofd"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(buffer.getvalue())["status"], "partial")


if __name__ == "__main__":
    unittest.main()
