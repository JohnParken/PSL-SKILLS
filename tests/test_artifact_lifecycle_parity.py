"""Repository-only guard: explicit shared DOCX/public vendoring cannot silently drift."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ParityTests(unittest.TestCase):
    def test_tl_docx_uses_exact_shared_delivery_copy(self):
        source = ROOT / 'skills/artifact-lifecycle/scripts'
        target = ROOT / 'skills/tl-docx'
        manifest = json.loads((target / 'references/lifecycle-upstream.json').read_text(encoding='utf-8'))
        self.assertEqual({item['file'] for item in manifest['files']},
                         {'archive_delivery.py', 'artifact_files.py', 'output_location.py', 'router_config.py'})
        for item in manifest['files']:
            with self.subTest(file=item['file']):
                original = (source / item['file']).read_bytes()
                self.assertEqual(original, (target / 'scripts' / item['file']).read_bytes())
                self.assertEqual(hashlib.sha256(original).hexdigest(), item['sha256'])

    def test_docx_public_and_recorded_hashes_agree(self):
        common = ROOT/'skills/artifact-lifecycle'
        metadata = json.loads((common/'references/upstream.json').read_text())
        for item in metadata['files']:
            with self.subTest(file=item['file']):
                source = (ROOT/'skills/docx/scripts'/item['file']).read_bytes()
                if item['mode'] == 'before-main':
                    source = source[:source.index(b'def main(argv=None)')]
                public = (common/'scripts'/item['file']).read_bytes()
                self.assertEqual(public, source)
                self.assertEqual(hashlib.sha256(public).hexdigest(), item['sha256'])

    def test_flat_independent_delivery_copies_and_records_agree(self):
        for skill in ('et', 'ofd', 'pptx'):
            metadata = json.loads((ROOT / 'skills' / skill / 'references/lifecycle-upstream.json').read_text())
            self.assertEqual({item['file'] for item in metadata['files']},
                             {'archive_delivery.py', 'artifact_files.py', 'output_location.py', 'router_config.py'})
            for item in metadata['files']:
                with self.subTest(skill=skill, file=item['file']):
                    source = (ROOT / 'skills/artifact-lifecycle/scripts' / item['file']).read_bytes()
                    installed = (ROOT / 'skills' / skill / 'scripts' / item['file']).read_bytes()
                    self.assertEqual(source, installed)
                    self.assertEqual(hashlib.sha256(source).hexdigest(), item['source_sha256'])
                    self.assertEqual(hashlib.sha256(installed).hexdigest(), item['installed_sha256'])
