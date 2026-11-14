"""Exercise tracked text replacements and non-target package preservation."""
from io import BytesIO
import json
import os
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_edit import edit_document
from tl_package import Package
from tl_read import report


class RevisionTests(unittest.TestCase):
    """Guard text, metadata, formatting and unsupported boundaries."""

    def sample(self, original=' old ', change=None):
        """Build a document independently from the editor implementation."""
        doc = Document()
        para = doc.add_paragraph()
        para.paragraph_format.keep_with_next = True
        para.add_run('prefix ')
        para.add_run(original).bold = True
        para.add_run(' suffix')
        doc.add_paragraph('unrelated paragraph')
        doc.sections[0].header.paragraphs[0].text = 'header retained'
        if change:
            change(para)
        output = BytesIO()
        doc.save(output)
        return Package(output.getvalue()), {
            'type': 'tracked_replace_run', 'paragraph_index': 0, 'expected_text': para.text,
            'run_index': 1, 'expected_run_text': original, 'text': ' new ',
            'author': 'Reviewer', 'date': '2026-11-14T01:02:03Z'}

    def apply(self, package, operation):
        """Use the same public editor path as ordinary edits."""
        result, receipt = edit_document(package, {'operations': [operation]}, None)
        return Package(result), receipt

    def test_replacement_preserves_non_targets(self):
        """Original and accepted views differ only at the requested run."""
        package, op = self.sample()
        result, receipt = self.apply(package, op)
        before = package.xml(package.main)
        after = result.xml(result.main)
        para = after.find('.//' + qn('w:body') + '/' + qn('w:p'))
        deletion, insertion = para.find(qn('w:del')), para.find(qn('w:ins'))
        self.assertEqual(' old ', deletion.find('.//' + qn('w:delText')).text)
        self.assertEqual(' new ', insertion.find('.//' + qn('w:t')).text)
        for node in (deletion, insertion):
            self.assertEqual('Reviewer', node.get(qn('w:author')))
            self.assertEqual(op['date'], node.get(qn('w:date')))
            self.assertIsNotNone(node.find('.//' + qn('w:b')))
        self.assertNotEqual(deletion.get(qn('w:id')), insertion.get(qn('w:id')))
        self.assertEqual(before.find('.//' + qn('w:pPr')).xml, para.find(qn('w:pPr')).xml)
        self.assertEqual([package.main], receipt['changed_parts'])
        for name in package.names - {package.main}:
            self.assertEqual(package.read(name), result.read(name), name)
        self.assertEqual('unrelated paragraph', result.document().paragraphs[1].text)

    def test_read_views(self):
        """The reader exposes actual original and accepted revision text."""
        package, op = self.sample()
        result, _ = self.apply(package, op)
        for view, expected in (('original', 'prefix  old  suffix'), ('accepted', 'prefix  new  suffix')):
            records, _, _ = report(result, revisions=view)
            paragraphs = [item for item in records if item.get('type') == 'paragraph']
            self.assertEqual(expected, paragraphs[0]['text'])

    def test_cli_publication_and_archive(self):
        """The public edit command preserves the input and archives its output."""
        package, op = self.sample()
        cli = Path(__file__).resolve().parents[1] / 'tl_docx.py'
        with tempfile.TemporaryDirectory(prefix='tl-revisions-') as temporary:
            task = Path(temporary)
            (task / 'source.docx').write_bytes(package.data)
            config = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'revision-test'}]})
            result = subprocess.run([sys.executable, '-I', '-B', str(cli), 'edit', 'source.docx', 'revised.docx'],
                                    cwd=task, env=dict(os.environ, ROUTER_CONFIG_VARIABLES=config),
                                    input=json.dumps({'operations': [op]}).encode(), capture_output=True,
                                    timeout=30, check=False)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            receipt = json.loads(result.stdout)
            self.assertEqual('edited', receipt['status'])
            self.assertEqual('success', receipt['archive_status'])
            self.assertEqual(package.data, (task / 'source.docx').read_bytes())
            self.assertEqual((task / 'revised.docx').read_bytes(),
                             (task / 'output/revision-test/revised.docx').read_bytes())

    def test_insertion_and_deletion(self):
        """Do not create empty revision wrappers for one-sided changes."""
        for original, replacement, present, absent in (
                ('', 'inserted', 'ins', 'del'), ('deleted', '', 'del', 'ins')):
            package, op = self.sample(original)
            result, _ = self.apply(package, {**op, 'text': replacement})
            root = result.xml(result.main)
            self.assertIsNotNone(root.find('.//' + qn('w:' + present)))
            self.assertIsNone(root.find('.//' + qn('w:' + absent)))

    def test_stale_and_invalid_values(self):
        """Invalid metadata or target guards fail before producing a candidate."""
        package, op = self.sample()
        for changes in ({'expected_text': 'stale'}, {'expected_run_text': 'stale'},
                        {'run_index': True}, {'paragraph_index': -1}, {'author': ''},
                        {'date': '2026-02-30T00:00:00Z'}, {'date': 'yesterday'},
                        {'text': '\n'}, {'text': '\ud800'}, {'text': ' old '}, {'unexpected': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.apply(package, {**op, **changes})

    def test_unsupported_objects_rejected(self):
        """Do not drop drawings, fields, bookmarks or prior revisions."""
        def marker(para):
            para._p.append(OxmlElement('w:bookmarkStart'))  # pylint: disable=protected-access
        def drawing(para):
            para.runs[1]._r.append(OxmlElement('w:drawing'))  # pylint: disable=protected-access
        def revision(para):
            para.runs[1]._r.get_or_add_rPr().append(OxmlElement('w:rPrChange'))  # pylint: disable=protected-access
        for change in (marker, drawing, revision):
            package, op = self.sample(change=change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply(package, op)

    def test_existing_ids_not_reused(self):
        """Select free IDs even when a later existing ID is not contiguous."""
        def other(para):
            doc_body = para._p.getparent()  # pylint: disable=protected-access
            node = OxmlElement('w:p')
            for identifier in ('0', '2'):
                bookmark = OxmlElement('w:bookmarkStart')
                bookmark.set(qn('w:id'), identifier)
                bookmark.set(qn('w:name'), 'existing' + identifier)
                node.append(bookmark)
            doc_body.insert(1, node)
        package, op = self.sample(change=other)
        result, _ = self.apply(package, op)
        root = result.xml(result.main)
        self.assertEqual('1', root.find('.//' + qn('w:del')).get(qn('w:id')))
        self.assertEqual('3', root.find('.//' + qn('w:ins')).get(qn('w:id')))


if __name__ == '__main__':
    unittest.main()
