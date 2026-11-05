"""Tests for read_et CLI entry point and environment self-check."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_et import check_environment, read_bytes, read
from test_wps_biff8 import workbook, rec


class EtCliTests(unittest.TestCase):
    def test_environment_check_direct(self):
        code = check_environment()
        self.assertEqual(code, 0)

    def test_cli_check_flag(self):
        script = (Path(__file__).resolve().parents[1] / 'read_et.py')
        proc = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(script), '--check'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn('environment-ok', proc.stdout)
        self.assertIn('ooxml-sheet=', proc.stdout)

    def test_cli_read_biff8_workbook(self):
        from test_wps_cfb import synthetic_cfb
        cfb_data = synthetic_cfb({'Workbook': workbook()})
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / 'test.et'
            file_path.write_bytes(cfb_data)
            script = (Path(__file__).resolve().parents[1] / 'read_et.py')
            proc = subprocess.run(
                [sys.executable, '-I', '-S', '-B', str(script), str(file_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertEqual(data.get('status'), 'ok')
            self.assertIn('sheets', data)


if __name__ == '__main__':
    unittest.main()
