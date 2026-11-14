"""Legal but semantically wrong RTF candidates must not replace outputs."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import task_api

from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch

from run_wps_task import create_document
from run_wps_edit import execute_edit_task
from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes


SPEC = {'page_numbering': False, 'blocks': [
    {'type': 'paragraph', 'text': 'Alpha😀'},
    {'type': 'table', 'rows': [['anchor', ''], ['', '']], 'merges': [[0, 0, 1, 1]], 'repeat_header': False},
    {'type': 'paragraph', 'text': 'Omega'},
]}


class ContentLedgerTests(unittest.TestCase):
    def test_legally_readable_wrong_candidates_preserve_existing_target(self):
        variants = []
        for kind in ('lost_text', 'lost_table', 'order', 'cell', 'merge'):
            spec = deepcopy(SPEC)
            if kind == 'lost_text':
                spec['blocks'][0]['text'] = 'Alph'
            elif kind == 'lost_table':
                spec['blocks'].pop(1)
            elif kind == 'order':
                spec['blocks'][0], spec['blocks'][2] = spec['blocks'][2], spec['blocks'][0]
            elif kind == 'cell':
                spec['blocks'][1]['rows'][0][0] = 'wrong'
            elif kind == 'merge':
                spec['blocks'][1]['merges'] = []
            variants.append((kind, build_document_from_spec(spec).render()))
        for kind, wrong_rtf in variants:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'out.rtf'
                output.write_bytes(b'existing')
                self.assertIn(read_bytes(wrong_rtf.encode('ascii'), reading_mode='standard')['status'], ('ok', 'partial'))
                with patch('wps_rtf_builder.RtfDocument.render', return_value=wrong_rtf), self.assertRaises(RuntimeError):
                    task_api(create_document, SPEC, output)
                self.assertEqual(output.read_bytes(), b'existing')
                self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_valid_content_and_supported_merge_geometry_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = task_api(create_document, SPEC, Path(directory) / 'good.rtf')
            self.assertEqual(receipt['content_validation']['status'], 'verified_within_scope')
            self.assertIn('visual_pagination', receipt['content_validation']['unverified'])

    def test_reflow_wrong_candidate_preserves_source_and_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.rtf', Path(directory) / 'out.rtf'
            task_api(create_document, {'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': 'old'}]}, source)
            original = source.read_bytes()
            output.write_bytes(b'existing')
            with patch('wps_rtf_builder.RtfDocument.render', return_value=original.decode('ascii')), self.assertRaises(RuntimeError):
                task_api(execute_edit_task, source, output, [{'operation': 'replace_text', 'find': 'old', 'replace': 'new'}])
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(output.read_bytes(), b'existing')

    def test_adjacent_table_boundaries_and_blank_paragraphs_are_disclosed(self):
        spec = {'page_numbering': False, 'blocks': [
            {'type': 'paragraph', 'text': ' '},
            {'type': 'table', 'rows': [['A']]}, {'type': 'table', 'rows': [['B']]},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            receipt = task_api(create_document, spec, Path(directory) / 'tables.rtf')
            self.assertIn('adjacent_table_boundaries', receipt['content_validation']['unverified'])
            self.assertIn('blank_paragraph_presence', receipt['content_validation']['unverified'])


if __name__ == '__main__':
    unittest.main()
