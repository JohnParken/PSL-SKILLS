"""Cross-skill maintenance regressions, not part of any runtime package.

Fresh processes block uname before importing project entrypoints. A separate
Linux-branch simulation exposes Python 3.11/3.12 uuid import probes on macOS;
it is deliberately not reported as native Linux acceptance.
"""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PROBES = {'uname', 'system', 'node', 'release', 'version', 'machine',
                   'processor', 'platform', 'architecture', 'libc_ver'}
BLOCKED_RUNNER = r'''
import os, runpy, subprocess, sys
entry, simulated, *args = sys.argv[1:]
if simulated == 'linux':
    sys.platform = 'linux'
# No project import has run. A cached uuid would hide older Python import probes.
assert 'uuid' not in sys.modules
calls = []
def blocked(*args, **kwargs):
    calls.append('forbidden system probe')
    raise PermissionError('sandbox: uname and external commands are disabled')
setattr(os, 'uname', blocked)
subprocess.Popen = blocked
os.system = blocked
sys.argv = [entry, *args]
try:
    runpy.run_path(entry, run_name='__main__')
except SystemExit as exc:
    if exc.code not in (None, 0):
        raise
finally:
    assert not calls, calls
'''


def probe_references(source):
    """Resolve simple imports/aliases/getattr without executing inspected code.

    Conservative regression guard, not arbitrary dynamic-code analysis. Shell
    and non-Python sources still require the documented manual repository scan.
    """
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = node.module + '.' + item.name

    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return resolve(node.value) + '.' + node.attr
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'getattr' and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            return resolve(node.args[0]) + '.' + node.args[1].value
        return ''

    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = resolve(node.value)
    forbidden = {'os.uname', *('platform.' + name for name in PLATFORM_PROBES)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Call, ast.Name)) and resolve(node) in forbidden:
            found.append((node.lineno, resolve(node)))
        if isinstance(node, ast.Import) and any(item.name == 'uuid' for item in node.names):
            found.append((node.lineno, 'uuid import may probe uname on older Python'))
        if isinstance(node, ast.ImportFrom) and node.module in ('uuid', 'os', 'platform'):
            for item in node.names:
                if node.module == 'uuid' or item.name == '*' or node.module + '.' + item.name in forbidden:
                    found.append((node.lineno, node.module + '.' + item.name))
    return sorted(set(found))


class UnameSandboxTests(unittest.TestCase):
    """Check imports, real check/read entrypoints and bounded cache continuation."""

    def run_entry(self, path, args, cwd, simulated='native'):
        """Launch a fresh isolated child; fail if any forbidden probe was attempted."""
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', BLOCKED_RUNNER,
                                 str(ROOT / path), simulated, *args], cwd=cwd,
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def test_guard_recognizes_aliases_and_indirect_probes(self):
        """Keep positive alias fixtures and an inert-string negative control."""
        for source in ('import os as posix\nposix.uname()',
                       'from os import uname as probe\nprobe()',
                       'import platform as p\np.system()',
                       'from platform import machine as cpu\ncpu()',
                       'import os\nf = getattr(os, "uname")\nf()',
                       'import uuid', 'from uuid import uuid4'):
            with self.subTest(source=source):
                self.assertTrue(probe_references(source))
        self.assertEqual(probe_references('import sys\nx = sys.platform\ntext = "os.uname()"'), [])

    def test_repository_python_sources_do_not_probe_uname(self):
        """Scan maintained source, including tests, without reading user samples."""
        failures = []
        count = 0
        for directory in ('skills', 'tools', 'tests'):
            for path in sorted((ROOT / directory).rglob('*.py')):
                if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT):
                    continue
                count += 1
                failures.extend((str(path.relative_to(ROOT)), *item)
                                for item in probe_references(path.read_text(encoding='utf-8')))
        self.assertGreater(count, 100)
        self.assertEqual(failures, [])

    def test_docx_and_pptx_checks_with_uname_denied(self):
        """Exercise real checks on native and simulated Linux import branches."""
        with tempfile.TemporaryDirectory(prefix='psl-uname-check-') as task:
            for simulated in ('native', 'linux'):
                with self.subTest(simulated=simulated):
                    docx = self.run_entry('skills/docx/scripts/read_docx.py', ['--check'], task, simulated)
                    self.assertIn('read-check-ok', docx)
                    pptx = json.loads(self.run_entry('skills/pptx/scripts/pptx.py', ['check'], task, simulated))
                    self.assertTrue(pptx['ok'])
                    expected_platform = 'Linux' if simulated == 'linux' else {
                        'darwin': 'Darwin', 'linux': 'Linux', 'win32': 'Windows'}.get(sys.platform, sys.platform)
                    self.assertEqual(pptx['platform'], expected_platform)
                    self.assertEqual(pptx['python'], '.'.join(map(str, sys.version_info[:3])))
                    self.assertIn('environment-ok', json.dumps(pptx))
            self.assertEqual(list(Path(task).iterdir()), [])

    def test_docx_read_and_page_cache_with_uname_denied(self):
        """Read a synthetic DOCX and verify cached continuation preserves content."""
        with tempfile.TemporaryDirectory(prefix='psl-uname-read-') as task:
            source = Path(task) / 'synthetic.docx'
            body = ''.join('<w:p><w:r><w:t>' + str(index) + ' synthetic text ' * 50 + '</w:t></w:r></w:p>'
                           for index in range(10))
            with ZipFile(source, 'w') as package:
                package.writestr('word/document.xml',
                                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                 '<w:body>' + body + '</w:body></w:document>')
            before = source.read_bytes()
            entry = 'skills/docx/scripts/read_docx.py'
            expected = json.loads(self.run_entry(entry, ['synthetic.docx'], task, 'linux'))
            first = json.loads(self.run_entry(entry, ['synthetic.docx', '--max-output-bytes', '3000'], task, 'linux'))
            self.assertTrue(first['has_more'])
            last = json.loads(self.run_entry(entry, ['synthetic.docx', '--max-output-bytes', '200000',
                                                     '--cursor', first['next_cursor']], task, 'linux'))
            self.assertFalse(last['has_more'])
            self.assertEqual(first['blocks'] + last['blocks'], expected['blocks'])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(Path(task).iterdir()), [source])


if __name__ == '__main__':
    unittest.main()
