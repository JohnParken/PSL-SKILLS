"""Failed candidate validation must not replace a pre-existing deliverable."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_wps_task import create_document


class PublishFailureTests(unittest.TestCase):
    def test_validation_exception_preserves_old_output_and_cleans_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out.wps'
            output.write_bytes(b'original')
            with patch('run_wps_task.read_bytes', side_effect=RuntimeError('injected validation failure')):
                with self.assertRaises(RuntimeError):
                    create_document({'blocks': [{'type': 'paragraph', 'text': '正文'}]}, output)
            self.assertEqual(output.read_bytes(), b'original')
            self.assertEqual(list(Path(temporary).iterdir()), [output])

    def test_edit_validation_failure_preserves_source_and_old_target(self):
        from read_wps import read_bytes
        from run_wps_edit import execute_edit_task
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.wps'
            output = Path(temporary) / 'output.wps'
            create_document({'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': '原文'}]}, source)
            before = source.read_bytes()
            parsed = read_bytes(before)
            output.write_bytes(b'old target')
            with patch('run_wps_edit.read_bytes', side_effect=[parsed, RuntimeError('candidate failed')]):
                with self.assertRaises(RuntimeError):
                    execute_edit_task(source, output, [{'operation': 'replace_text', 'find': '原文', 'replace': '新文'}])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(output.read_bytes(), b'old target')
            self.assertEqual(set(Path(temporary).iterdir()), {source, output})

    def test_partial_generation_receipt_retains_issues(self):
        from wps_rtf_builder import build_document_from_spec
        from read_wps import read_bytes
        with tempfile.TemporaryDirectory() as temporary:
            parsed = read_bytes(build_document_from_spec({'blocks': [{'type': 'paragraph', 'text': '正文'}]}).render().encode('ascii'))
            parsed.update(status='partial', issues=[{'code': 'rtf_destination_skipped'}])
            with patch('run_wps_task.read_bytes', return_value=parsed):
                receipt = create_document({'blocks': [{'type': 'paragraph', 'text': '正文'}]}, Path(temporary) / 'out.rtf')
            self.assertTrue(receipt['partial'])
            self.assertEqual(receipt['issues'], [{'code': 'rtf_destination_skipped'}])


if __name__ == '__main__':
    unittest.main()
