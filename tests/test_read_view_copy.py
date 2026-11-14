"""Repository-maintenance parity check; not required by either standalone runtime."""
import hashlib
import json
from pathlib import Path
import unittest
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class ReadViewCopies(unittest.TestCase):
    def test_copies_match_except_documented_et_byte_budget(self):
        source = (ROOT / 'skills/wps/scripts/wps_read_view.py').read_bytes()
        other = (ROOT / 'skills/et/scripts/wps_read_view.py').read_bytes()
        expected_et = source.replace(b'max_bytes <= 16 * 1024 * 1024', b'max_bytes <= 64 * 1024 * 1024')
        expected_et = expected_et.replace(b'512..16777216', b'512..67108864')
        self.assertNotEqual(source, expected_et)
        self.assertEqual(expected_et, other)
        records = [json.loads((ROOT / 'skills' / skill / 'references/read-view-runtime.json').read_text(encoding='utf-8'))
                   for skill in ('wps', 'et')]
        self.assertEqual(records[0]['sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(records[1]['sha256'], hashlib.sha256(other).hexdigest())
        self.assertEqual(records[0]['intentional_differences'], [])
        self.assertEqual(records[1]['intentional_differences'], ['ET accepts max-output-bytes up to 64 MiB; WPS remains 16 MiB. Validation text matches each limit.'])
        for key in ('component', 'source_repository_path', 'source_version', 'projection_revision', 'copies', 'dependencies', 'license'):
            self.assertEqual(records[0][key], records[1][key], key)


    def test_standalone_wps_cli_byte_budget(self):
        with tempfile.TemporaryDirectory(prefix='.wps-budget-test-', dir=ROOT) as directory:
            work = Path(directory)
            install = work / 'isolated-wps'
            shutil.copytree(ROOT / 'skills/wps/scripts', install, ignore=shutil.ignore_patterns('test', '__pycache__'))
            (work / 'input.wps').write_bytes(b'{\\rtf1\\ansi Hello world}')
            command = [sys.executable, '-I', '-S', '-B', str(install / 'read_wps.py'), './input.wps', '--reading-mode', 'standard', '--page-size', '3',
                       '--max-output-bytes', '3000']
            cursor, chunks = None, []
            while True:
                proc = subprocess.run(command + (['--cursor', cursor] if cursor else []), cwd=work, capture_output=True, check=False)
                self.assertIn(proc.returncode, (0, 3), proc.stderr + proc.stdout)
                self.assertLessEqual(len(proc.stdout), 3000)
                value = json.loads(proc.stdout)
                chunks.append(value['text'])
                cursor = value['next_cursor']
                if cursor is None:
                    break
            self.assertEqual(''.join(chunks), 'Hello world')


if __name__ == '__main__':
    unittest.main()
