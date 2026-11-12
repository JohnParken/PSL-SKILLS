"""Explicit OFD output views preserve source failure/partial and page provenance."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import importlib.util
import json
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('ofd_view_helpers', Path(__file__).with_name('test_ofd.py'))
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
READER = BASE.READER


class OfdViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ofd-view-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'input.ofd'
        BASE.write_package(self.path, BASE.fixture(text='中😀"\\测试'))
        self.source = READER.read_ofd_structure(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).parents[1] / 'read_ofd.py')), *args],
                              cwd=self.root, capture_output=True, text=True, encoding='utf-8', check=False)

    def test_explicit_full_and_api_identity_unchanged(self):
        self.assertIs(READER.project_output(self.source, 'full'), self.source)
        child = self.cli('./input.ofd', '--view', 'full')
        self.assertEqual(child.returncode, 3 if self.source['status'] == 'partial' else 0)
        self.assertEqual(json.loads(child.stdout), self.source)
        self.assertNotIn('mode', self.source)

    def test_complete_source_stays_complete_for_both_views(self):
        BASE.write_package(self.path, BASE.fixture(text='正文'))
        for args in [(), ('--view', 'summary'), ('--view', 'page-text', '--document', '1', '--page', '1')]:
            child = self.cli('./input.ofd', *args)
            self.assertEqual(child.returncode, 0)
            self.assertEqual(json.loads(child.stdout)['status'], 'complete')

    def test_summary_has_counts_no_body_and_preserves_assessments(self):
        result = READER.project_output(self.source, 'summary')
        self.assertEqual(result['schema_version'], 'ofd-view-1')
        self.assertEqual(result['documents'][0]['page_count'], 1)
        self.assertEqual(result['documents'][0]['page_text_runs'], 1)
        self.assertNotIn('text_runs', result['documents'][0])
        self.assertNotIn('中😀', json.dumps(result, ensure_ascii=False))
        self.assertEqual(result['text_assessment'], self.source['text_assessment'])

    def test_page_text_keeps_complete_occurrence_records(self):
        result = READER.project_output(self.source, 'page-text', document=1, page=1)
        self.assertEqual(result['documents'][0]['pages'][0]['text_runs'], self.source['documents'][0]['pages'][0]['text_runs'])
        self.assertEqual(result['source_sha256'], self.source['sha256'])
        self.assertEqual(result['coverage'], 'selected_page_occurrences_only')
        self.assertIn('structure', result['omitted_top_level_fields'])

    def test_partial_diagnostics_not_hidden_by_smaller_view(self):
        BASE.write_package(self.path, BASE.fixture(page_extra='<ofd:UnknownFeature/>'))
        original = READER.read_ofd_structure(self.path)
        result = READER.project_output(original, 'summary')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['diagnostics'], original['diagnostics'])
        child = self.cli('./input.ofd', '--view', 'summary')
        self.assertEqual(child.returncode, 3)

    def test_budget_exact_boundary_and_large_diagnostics(self):
        result = READER.project_output(self.source, 'page-text', document=1, page=1, max_chars=2000000)
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False)) + 1
        self.assertEqual(READER.project_output(self.source, 'page-text', document=1, page=1, max_chars=size), result)
        with self.assertRaises(READER.ReadError):
            READER.project_output(self.source, 'page-text', document=1, page=1, max_chars=size - 1)
        self.source['diagnostics'] = [{'code': 'synthetic', 'detail': 'x' * 20000}]
        with self.assertRaisesRegex(READER.ReadError, 'budget'):
            READER.project_output(self.source, 'summary')

    def test_indices_and_changed_source_are_rejected(self):
        for arguments in ({'document': 0}, {'document': 2}, {'document': True}, {'document': 1.0},
                          {'page': 1}, {'source_sha256': 'bad'}, {'source_sha256': '0' * 64}):
            with self.subTest(arguments=arguments), self.assertRaises(READER.ReadError):
                READER.project_output(self.source, 'summary', **arguments)
        with self.assertRaises(READER.ReadError):
            READER.project_output(self.source, 'page-text', document=1, page=2)
        child = self.cli('./input.ofd', '--view', 'page-text', '--document', '1', '--page', '1', '--source-sha256', '0' * 64)
        self.assertEqual(child.returncode, 1)
        self.assertEqual(json.loads(child.stdout)['status'], 'failed')
        self.assertNotIn('documents', json.loads(child.stdout))

    def test_view_argument_errors_do_not_start_reading(self):
        for args in [('--view', 'page-text'), ('--view', 'summary', '--page', '1'), ('--view', 'full', '--document', '1'),
                     ('--check', '--view', 'summary'), ('--view', 'summary', '--source-sha256', 'bad')]:
            child = self.cli('./missing.ofd', *args)
            self.assertEqual(child.returncode, 2)
            self.assertEqual(child.stdout, '')


if __name__ == '__main__':
    unittest.main()
