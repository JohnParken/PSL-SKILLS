"""Verify guarded run merging and package preservation independently."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_edit import edit_document
from tl_package import Package


class MergeTests(unittest.TestCase):
    """Never merge through formatting, field, or range boundaries."""

    def make(self, change=None):
        """Return an independent sample and its expected guarded operation."""
        doc = Document()
        para = doc.add_paragraph()
        para.paragraph_format.keep_with_next = True
        for value in (' A ', '中文', ' B'):
            para.add_run(value).bold = True
        doc.sections[0].header.paragraphs[0].text = 'preserved header'
        if change:
            change(para)
        stream = BytesIO()
        doc.save(stream)
        operation = {'type': 'merge_runs', 'paragraph_index': 0,
                     'expected_text': para.text, 'start_run': 0,
                     'expected_runs': [run.text for run in para.runs]}
        return stream.getvalue(), operation

    def test_preserves_text_format_and_unrelated_parts(self):
        """Preserve exact text, paragraph properties and all other ZIP members."""
        original, operation = self.make()
        result, receipt = edit_document(Package(original), {'operations': [operation]}, None)
        before, after = Document(BytesIO(original)), Document(BytesIO(result))
        self.assertEqual(before.paragraphs[0].text, after.paragraphs[0].text)
        self.assertEqual(1, len(after.paragraphs[0].runs))
        self.assertTrue(after.paragraphs[0].runs[0].bold)
        self.assertEqual(before.paragraphs[0]._p.pPr.xml, after.paragraphs[0]._p.pPr.xml)  # pylint: disable=protected-access
        self.assertEqual(['word/document.xml'], receipt['changed_parts'])
        with ZipFile(BytesIO(original)) as old, ZipFile(BytesIO(result)) as new:
            for name in old.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(old.read(name), new.read(name))

    def test_different_format_is_rejected(self):
        """Refuse collapsing distinct character formatting."""
        original, operation = self.make(lambda para: setattr(para.runs[1], 'italic', True))
        with self.assertRaisesRegex(ValueError, 'identical explicit formatting'):
            edit_document(Package(original), {'operations': [operation]}, None)

    def test_bookmark_and_drawing_are_rejected(self):
        """Protect non-text objects and range anchors."""
        def bookmark(para):
            node = OxmlElement('w:bookmarkStart')
            node.set(qn('w:id'), '1')
            node.set(qn('w:name'), 'anchor')
            para.runs[0]._r.addnext(node)  # pylint: disable=protected-access
        for change in (bookmark, lambda para: para.runs[0]._r.append(OxmlElement('w:drawing'))):  # pylint: disable=protected-access
            with self.subTest(change=change):
                original, operation = self.make(change)
                with self.assertRaises(ValueError):
                    edit_document(Package(original), {'operations': [operation]}, None)

    def test_duplicate_run_properties_rejected(self):
        """Do not silently drop malformed duplicate properties."""
        def change(para):
            props = OxmlElement('w:rPr')
            props.append(OxmlElement('w:i'))
            para.runs[1]._r.append(props)  # pylint: disable=protected-access
        original, operation = self.make(change)
        with self.assertRaisesRegex(ValueError, 'duplicate run properties'):
            edit_document(Package(original), {'operations': [operation]}, None)

    def test_guard_and_invalid_range_rejected(self):
        """Reject stale guards and non-integer or short ranges."""
        original, operation = self.make()
        for changes in ({'start_run': -1}, {'start_run': True}, {'expected_runs': ['wrong', '中文']},
                        {'expected_runs': [' A ']}, {'start_run': 2}, {'expected_runs': [1, 2]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_document(Package(original), {'operations': [{**operation, **changes}]}, None)


if __name__ == '__main__':
    unittest.main()
