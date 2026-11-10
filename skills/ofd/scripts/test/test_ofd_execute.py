"""Regression tests for execute-style CLI invocation and stdin isolation."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'generate_ofd.py'
READER = SCRIPT.with_name('read_ofd.py')
BASE = {'schema_version': 'ofd-generate-1', 'pages': [
    {'width': 210, 'height': 297, 'items': [{'kind': 'rectangle', 'x': 20, 'y': 20, 'width': 60, 'height': 30}]}
]}


class ExecuteInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ofd-execute-')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, args, data=b''):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), *args], cwd=self.root,
                              input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_shell_quoted_heredoc_and_roundtrip(self):
        title = '中文 "引号" \\路径 $(touch injected) `touch injected2`'
        spec = dict(BASE, title=title)
        payload = json.dumps(spec, ensure_ascii=False)
        script_dir = self.root / "skill ' $(touch injected3)"
        shutil.copytree(SCRIPT.parent, script_dir, ignore=shutil.ignore_patterns('test', '__pycache__'))
        script = script_dir / SCRIPT.name
        output = "结果 '$(touch injected4)' 空格.ofd"
        cmd = (f'{shlex.quote(sys.executable)} -I -S -B {shlex.quote(str(script))} '
               f"--spec-stdin {shlex.quote(output)} <<'OFD_SPEC'\n{payload}\nOFD_SPEC\n")
        result = subprocess.run(['/bin/sh', '-c', cmd], cwd=self.root, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'generated')
        self.assertFalse((self.root / 'injected').exists())
        self.assertFalse((self.root / 'injected2').exists())
        self.assertFalse((self.root / 'injected3').exists())
        self.assertFalse((self.root / 'injected4').exists())
        with zipfile.ZipFile(self.root / output) as ofd:
            self.assertIn(title.encode('utf-8'), ofd.read('OFD.xml'))
        read = subprocess.run([sys.executable, '-I', '-S', '-B', str(READER), './' + output],
                              cwd=self.root, capture_output=True, check=False)
        self.assertEqual(read.returncode, 3)
        self.assertEqual(json.loads(read.stdout)['status'], 'partial')

    def test_stdin_rejects_invalid_and_oversized_without_output(self):
        for data in (b'', b'{', b'{"a":1,"a":2}', b'\xff', b' ' * (256 * 1024 + 1)):
            with self.subTest(size=len(data)):
                result = self.run_cli(['--spec-stdin', 'bad.ofd'], data)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'failed')
                self.assertFalse((self.root / 'bad.ofd').exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_maximum_input_size_and_truncated_result(self):
        payload = json.dumps(BASE).encode()
        payload += b' ' * (256 * 1024 - len(payload))
        result = self.run_cli(['--spec-stdin', 'limit.ofd'], payload)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'generated')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(result.stdout[:len(result.stdout) // 2])

    def test_arguments_and_file_cli_remain_compatible(self):
        payload = json.dumps(BASE).encode()
        for args in (['--spec-stdin'], ['--spec-stdin', 'a.ofd', 'b.ofd'],
                     ['--check', '--spec-stdin', 'a.ofd'], ['--spec-stdin', '']):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(args, payload).returncode, 2)
        (self.root / '-').write_bytes(payload)
        result = self.run_cli(['-', 'old.ofd'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / 'existing.ofd').write_bytes(b'unchanged')
        result = self.run_cli(['--spec-stdin', 'existing.ofd'], payload)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.root / 'existing.ofd').read_bytes(), b'unchanged')
        result = self.run_cli(['--spec-stdin', './bad.ofd'], payload)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / 'bad.ofd').exists())


if __name__ == '__main__':
    unittest.main()
