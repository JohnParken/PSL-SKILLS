"""Public cache, portable-part and static-layout integration contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from docx import Document

CLI = Path(__file__).resolve().parents[1] / 'tl_docx.py'


class AdvancedCliTests(unittest.TestCase):
    """Exercise source protection through real isolated commands."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='tl-advanced-cli-', dir='.')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        doc = Document()
        for number in range(20):
            doc.add_paragraph(f'Paragraph {number} ' + 'text ' * 25)
        doc.save(self.root / 'source.docx')
        self.source = (self.root / 'source.docx').read_bytes()

    def invoke(self, *args, specification=None, status=0):
        """Invoke the entry used by consumers."""
        environment = dict(os.environ, ROUTER_CONFIG_VARIABLES=json.dumps({
            'commonParam': [{'name': 'questionId', 'value': 'advanced-cli'}]}))
        process = subprocess.run([sys.executable, '-I', '-B', str(CLI), *args], cwd=self.root,
                                 input=None if specification is None else json.dumps(specification).encode(),
                                 env=environment, capture_output=True, timeout=30, check=False)
        self.assertEqual(status, process.returncode, process.stderr.decode())
        return json.loads(process.stdout)

    def test_cache_continuations_and_release(self):
        """Final page releases the cache and explicit release is idempotent."""
        first = self.invoke('read', 'source.docx', '--cache', '--max-output-bytes', '5000')
        self.assertTrue(first['has_more'])
        token = first['cache']['token']
        last = self.invoke('read', 'source.docx', '--cache-token', token, '--cursor', first['next_cursor'],
                           '--max-output-bytes', '100000')
        self.assertFalse(last['has_more'])
        self.assertEqual('released', last['cache']['status'])
        self.assertEqual('already_absent', self.invoke('release-cache', token)['status'])
        self.assertEqual({'source.docx'}, {item.name for item in self.root.iterdir()})

    def test_cache_budget_failure_cleans_new_file(self):
        """A failed first page does not strand cache data."""
        self.invoke('read', 'source.docx', '--cache', '--max-output-bytes', '512', status=1)
        self.assertEqual({'source.docx'}, {item.name for item in self.root.iterdir()})

    def test_unpack_export_repack(self):
        """Exported JSON is directly consumable, and only result is archived."""
        exported = self.invoke('unpack', 'source.docx', '-o', 'parts.json')
        self.assertEqual('not_required', exported['archive_status'])
        bundle = json.loads((self.root / 'parts.json').read_text(encoding='utf-8'))
        for part in bundle['parts']:
            if part['member'] == 'word/document.xml':
                part['content'] = part['content'].replace('Paragraph 0 ', 'Changed 0 ', 1)
        result = self.invoke('repack', 'source.docx', 'repacked.docx', specification=bundle)
        self.assertEqual('success', result['archive_status'])
        self.assertEqual(self.source, (self.root / 'source.docx').read_bytes())
        self.assertEqual(['word/document.xml'], result['result']['changed_parts'])
        self.assertTrue(Document(self.root / 'repacked.docx').paragraphs[0].text.startswith('Changed 0'))

    def test_explicit_long_record_fragments_reassemble(self):
        """JSON fragments remain source-bound and reconstruct long paragraphs."""
        doc = Document()
        expected = 'long text ' * 1200
        doc.add_paragraph(expected)
        doc.save(self.root / 'long.docx')
        original = self.invoke('read', 'long.docx')
        output = []
        cursor = None
        while True:
            options = ['read', 'long.docx', '--fragment-records', '--max-output-bytes', '5000']
            if cursor:
                options.extend(['--cursor', cursor])
            result = self.invoke(*options)
            self.assertEqual('json-record-v1', result['fragment_mode'])
            output.extend(result['records'])
            if not result['has_more']:
                break
            cursor = result['next_cursor']
        reconstructed = []
        current = ''
        for record in output:
            if record['type'] != 'record_fragment':
                reconstructed.append(record)
                continue
            self.assertEqual(len(current), record['character_start'])
            current += record['json_fragment']
            if record['final_fragment']:
                reconstructed.append(json.loads(current))
                current = ''
        self.assertEqual('', current)
        self.assertEqual(original['records'], reconstructed)

    def test_static_audit_boundary(self):
        """Static success never claims rendered validation."""
        result = self.invoke('validate', 'source.docx', '--strict-layout')
        self.assertEqual('static_only', result['layout']['audit']['layout_validation'])
        self.assertEqual('not_run', result['layout']['audit']['visual_validation'])


if __name__ == '__main__':
    unittest.main()
