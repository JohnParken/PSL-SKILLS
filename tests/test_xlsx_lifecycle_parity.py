"""Repository guard for the explicit XLSX vendoring transformation."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class XlsxParityTests(unittest.TestCase):
    def test_public_runtime_matches_pinned_xlsx_copy(self):
        records = json.loads((ROOT/'skills/xlsx/references/lifecycle-upstream.json').read_text())
        for item in records['files']:
            with self.subTest(file=item['file']):
                source = (ROOT/'skills/artifact-lifecycle/scripts'/item['file']).read_text()
                expected = 'from __future__ import annotations\n' + source
                for module in ('router_config', 'output_location', 'artifact_files', 'controlled_cleanup'):
                    expected = expected.replace('from '+module+' import ', 'from .'+module+' import ')
                actual = (ROOT/'skills/xlsx/scripts/_artifact_runtime'/item['file']).read_bytes()
                self.assertEqual(actual, expected.encode())
                self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), item['source_sha256'])
                self.assertEqual(hashlib.sha256(actual).hexdigest(), item['installed_sha256'])
