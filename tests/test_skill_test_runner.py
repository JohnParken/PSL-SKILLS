"""Repository test-discovery guard; never a production Skill dependency."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / 'tools' / 'run_skill_tests.py'


class TestRunnerTests(unittest.TestCase):
    def invoke(self, source=None, pattern='test_*.py'):
        with tempfile.TemporaryDirectory(prefix='skill-test-runner-') as directory:
            if source is not None:
                (Path(directory) / 'test_sample.py').write_text(source, encoding='utf-8')
            return subprocess.run([sys.executable, '-I', '-S', '-B', str(RUNNER), directory, '-p', pattern],
                                  capture_output=True, text=True, timeout=30)

    def test_empty_discovery_is_not_success(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Collected 0 tests', result.stdout)
        self.assertIn('failed verification', result.stderr)

    def test_real_tests_are_counted_and_run(self):
        result = self.invoke('import unittest\nclass Sample(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(2 + 2, 4)\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Collected 1 tests', result.stdout)
        self.assertIn('Ran 1 test', result.stderr)

    def test_failures_keep_nonzero_exit_and_diagnostics(self):
        result = self.invoke('import unittest\nclass Sample(unittest.TestCase):\n    def test_bad(self):\n        self.fail("expected failure")\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('expected failure', result.stderr)

    def test_import_error_cannot_be_reported_as_a_passing_suite(self):
        result = self.invoke('raise ImportError("fixture unavailable")\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('fixture unavailable', result.stderr)

    def test_nonmatching_pattern_cannot_be_reported_as_a_passing_suite(self):
        result = self.invoke('import unittest\n', pattern='missing_*.py')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Collected 0 tests', result.stdout)


if __name__ == '__main__':
    unittest.main()
