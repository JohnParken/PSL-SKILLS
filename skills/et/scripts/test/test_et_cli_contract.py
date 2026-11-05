"""Public ET gateway preserves original results, partial and parser failure location."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
from et_cli_protocol import PROTOCOL, dumps, loads
from test_wps_biff8 import workbook
from test_wps_cfb import synthetic_cfb
from read_et import read_bytes, encode_result


class EtContractTests(unittest.TestCase):
    def test_original_extraction_and_exit_are_preserved(self):
        raw = synthetic_cfb({'Workbook': workbook()})
        expected = read_bytes(raw, source='source.et')
        request = {'protocol': PROTOCOL, 'id': 7, 'operation': 'read', 'parameters': {'raw': raw, 'source': 'source.et'}}
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'et_cli.py'), 'read'],
                               input=dumps(request), capture_output=True)
        reply = loads(child.stdout.strip())
        self.assertEqual(reply['result']['extraction'], encode_result(expected).encode('utf-8'))
        self.assertEqual(child.returncode, 3 if expected['status'] == 'partial' else 0)
        self.assertEqual(reply['result']['exit_code'], child.returncode)
        self.assertEqual(reply['id'], 7)
        self.assertEqual(child.stderr, b'')

    def test_failure_keeps_original_status_location_and_stderr_channel(self):
        request = {'protocol': PROTOCOL, 'id': 2, 'operation': 'read', 'parameters': {'raw': b'unknown', 'source': 'bad.et'}}
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'et_cli.py'), 'read'], input=dumps(request), capture_output=True)
        reply = loads(child.stdout.strip())
        self.assertEqual(child.returncode, 1)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['error']['status'], 'unsupported')
        self.assertEqual(reply['error']['location']['stage'], 'container')

    def test_run_alias_preserves_normal_partial_missing_and_usage_results(self):
        raw = synthetic_cfb({'Workbook': workbook()})
        with tempfile.TemporaryDirectory() as task:
            (Path(task)/'source.et').write_bytes(raw)
            for arguments in (['source.et'], ['missing.et'], ['--not-an-option'], ['--help']):
                old = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'read_et.py'), *arguments], cwd=task, capture_output=True)
                new = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'et_cli.py'), 'run', *arguments], cwd=task, capture_output=True)
                self.assertEqual((new.returncode, new.stdout, new.stderr), (old.returncode, old.stdout, old.stderr))


if __name__ == '__main__':
    unittest.main()
