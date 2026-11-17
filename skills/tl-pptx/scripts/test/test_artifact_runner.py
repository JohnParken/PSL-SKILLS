"""Public entry publication failures must not erase verified deliverables."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_guard import ContractError
from tl_pptx import run_artifact


class ArtifactRunnerTests(unittest.TestCase):
    """Verify prepublication refusal and truthful postpublication failure."""

    def setUp(self):
        self.original = Path.cwd()
        self.workspace = tempfile.TemporaryDirectory()
        os.chdir(self.workspace.name)

    def tearDown(self):
        os.chdir(self.original)
        self.workspace.cleanup()

    def test_existing_output_stops_before_producer(self):
        Path('existing.json').write_bytes(b'original')
        producer = Mock()
        with self.assertRaises(ContractError):
            run_artifact('existing.json', '.json', [], producer)
        producer.assert_not_called()
        self.assertEqual(Path('existing.json').read_bytes(), b'original')

    def test_archive_failure_keeps_verified_bytes(self):
        with patch('archive_delivery.archive_delivery', side_effect=KeyError('fixture')):
            result, code = run_artifact('analysis.json', '.json', ['source.pptx'],
                                        lambda: (b'{"slide_count":1}', {'structural_validation': 'passed'}))
        self.assertEqual(code, 3)
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['publication']['published'])
        self.assertEqual(result['archive']['archive_status'], 'failed')
        self.assertEqual(Path('analysis.json').read_bytes(), b'{"slide_count":1}')

    def test_archive_receives_original_input_protection(self):
        with patch('archive_delivery.archive_delivery', return_value={'archive_status': 'success', 'files': [{'output': 'bundle.zip', 'archive_status': 'copied'}]}) as archive:
            result, code = run_artifact('bundle.zip', '.zip', ['source.pptx'],
                                        lambda: (b'fixture bytes', {'validation': 'fixture'}))
        self.assertEqual(code, 0)
        self.assertEqual(result['publication']['file'], 'bundle.zip')
        archive.assert_called_once_with(['bundle.zip'], protected_inputs=['source.pptx'])


if __name__ == '__main__':
    unittest.main()
