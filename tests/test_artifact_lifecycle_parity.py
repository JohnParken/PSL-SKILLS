"""Repository-only guard: explicit shared DOCX/public vendoring cannot silently drift."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ParityTests(unittest.TestCase):
    def test_docx_public_and_recorded_hashes_agree(self):
        common = ROOT/'skills/artifact-lifecycle'
        metadata = json.loads((common/'references/upstream.json').read_text())
        for item in metadata['files']:
            with self.subTest(file=item['file']):
                source = (ROOT/'skills/docx/scripts'/item['file']).read_bytes()
                if item['mode'] == 'before-main': source = source[:source.index(b'def main(argv=None)')]
                public = (common/'scripts'/item['file']).read_bytes()
                self.assertEqual(public, source)
                self.assertEqual(hashlib.sha256(public).hexdigest(), item['sha256'])
