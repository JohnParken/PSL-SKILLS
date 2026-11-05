"""Explicit lossy reflow preserves recovered data and exposes omissions."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import tempfile
import unittest
from unittest.mock import patch
from read_wps import read_bytes
from run_wps_edit import execute_edit_task
from run_wps_task import create_document


class PartialReflowTests(unittest.TestCase):
    def test_partial_result_seam_requires_opt_in_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'source.wps', Path(temporary) / 'copy.wps'
            create_document({'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': '可靠😀正文'}]}, source)
            original = source.read_bytes()
            partial = read_bytes(original)
            partial.update(status='partial', issues=[{'code': 'object_not_decoded'}], omitted_regions=[{'type': 'object'}])
            with patch('run_wps_edit.read_bytes', return_value=partial), self.assertRaises(ValueError):
                execute_edit_task(source, target, [])
            self.assertFalse(target.exists())
            calls = []
            def read_candidate(data):
                calls.append(data)
                return partial if len(calls) == 1 else read_bytes(data)
            with patch('run_wps_edit.read_bytes', side_effect=read_candidate):
                result = execute_edit_task(source, target, [], allow_lossy_reflow=True)
            self.assertTrue(result['partial'])
            self.assertEqual(result['source_issues'], partial['issues'])
            self.assertEqual(result['source_omitted_regions'], partial['omitted_regions'])
            self.assertIn('可靠😀正文', read_bytes(target.read_bytes())['text'])
            self.assertIn('部分内容重排副本', read_bytes(target.read_bytes())['text'])
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(ValueError):
                execute_edit_task(source, target, [], allow_lossy_reflow=True)

    def test_actual_binary_partial_recovers_without_mocking_parser(self):
        from test_wps import document
        from test_wps_cfb import synthetic_cfb
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'partial.wps', Path(temporary) / 'copy.rtf'
            streams = document([('前\x00后\r'.encode('utf-16le'), False)])
            raw = synthetic_cfb({key[0]: value for key, value in streams.items()})
            source.write_bytes(raw)
            self.assertEqual(read_bytes(raw)['status'], 'partial')
            result = execute_edit_task(source, target, [], allow_lossy_reflow=True)
            self.assertTrue(result['partial'])
            self.assertIn('前\ufffc后', read_bytes(target.read_bytes())['text'])
            self.assertEqual(source.read_bytes(), raw)

    def test_unknown_order_retains_paragraphs_and_tables_with_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'source.wps', Path(temporary) / 'copy.wps'
            create_document({'blocks': [{'type': 'paragraph', 'text': '正文'}]}, source)
            parsed = read_bytes(source.read_bytes())
            parsed.pop('blocks')
            parsed['tables'] = [{'rows': [[{'text': '可靠单元格'}]]}]
            calls = []
            def read_candidate(data):
                calls.append(data)
                return parsed if len(calls) == 1 else read_bytes(data)
            with patch('run_wps_edit.read_bytes', side_effect=read_candidate):
                result = execute_edit_task(source, target, [], allow_lossy_reflow=True)
            self.assertTrue(result['partial'])
            self.assertEqual(result['order_status'], 'unknown_paragraphs_then_tables')
            self.assertEqual(read_bytes(target.read_bytes())['tables'][0]['rows'][0][0]['text'], '可靠单元格')


if __name__ == '__main__':
    unittest.main()
