"""Public reusable modules and compatibility boundaries; isolated standard library tests."""
import json
import os
import tempfile
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controlled_cleanup
import cleanup_task
from router_config import get_common_parameter, read_environment_parameter, RouterConfigError


class RuntimeModuleTests(unittest.TestCase):
    def test_environment_mapping_is_explicit_and_unmodified(self):
        env = {'CUSTOM': '原值'}
        self.assertEqual(read_environment_parameter('CUSTOM', environ=env), '原值')
        self.assertIsNone(read_environment_parameter('MISSING', environ=env))
        self.assertEqual(env, {'CUSTOM': '原值'})

    def test_named_common_parameter_preserves_value_type(self):
        for value in (None, False, 0, ['甲'], {'x': 1}):
            inner = [{'name': 'business', 'value': value}]
            for outer in ({'commonParam': inner}, [{'name': 'commonParam', 'value': json.dumps(inner)}]):
                self.assertEqual(get_common_parameter(json.dumps(outer), 'business'), value)

    def test_named_string_parameter_strips_edges_only_without_mutating_environment(self):
        for value, expected in (('  abc123  ', 'abc123'), ('\t\u3000中 间\n', '中 间'), (' \t\n', '')):
            inner = [{'name': 'business', 'value': value}]
            for common in (inner, json.dumps(inner)):
                for outer in ({'commonParam': common}, [{'name': 'commonParam', 'value': common}]):
                    raw = json.dumps(outer)
                    environment = {'ROUTER_CONFIG_VARIABLES': raw}
                    actual = get_common_parameter(read_environment_parameter(
                        'ROUTER_CONFIG_VARIABLES', environ=environment), 'business')
                    self.assertEqual(actual, expected)
                    self.assertEqual(environment['ROUTER_CONFIG_VARIABLES'], raw)
        nested = ['  unchanged  ']
        self.assertEqual(get_common_parameter(json.dumps({'commonParam': [
            {'name': 'business', 'value': nested}]}), 'business'), nested)

    def test_named_lookup_rejects_duplicates_and_invalid_json(self):
        for raw, reason in (( '{"commonParam":[{"name":"x","value":1},{"name":"x","value":2}]}', 'duplicate_parameter'),
                            ('{"commonParam":[],"commonParam":[]}', 'invalid_router_config_json'),
                            ('{"commonParam":[{"name":"x","value":NaN}]}', 'invalid_router_config_json')):
            with self.subTest(raw=raw), self.assertRaisesRegex(RouterConfigError, reason):
                get_common_parameter(raw, 'x')

    def test_non_docx_artifact_archive_and_controlled_cleanup(self):
        from archive_deliverables import archive_outputs
        previous = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='module-lifecycle-') as temporary:
            os.chdir(temporary)
            try:
                Path('user.json').write_text('original', encoding='utf-8')
                controlled_cleanup.plan_cleanup('plan.json', files=['worker.py', 'config.json'], protected=['user.json'], deliverables=['report.txt'])
                Path('worker.py').write_text('# owned', encoding='utf-8')
                Path('config.json').write_text('{}', encoding='utf-8')
                Path('report.txt').write_text('已验收结果', encoding='utf-8')
                env = {'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [{'name': 'questionId', 'value': 'module'}]}), 'DOCX_DEBUG_ROUTER_CONFIG': '0'}
                with patch.dict(os.environ, env, clear=True):
                    archived = archive_outputs([Path('report.txt')], [Path('user.json')])
                self.assertEqual(archived['archive_message'], '归档完成')
                controlled_cleanup.seal_cleanup('plan.json', outcome='success', verified=True, archive_status=archived['archive_status'])
                cleaned = controlled_cleanup.cleanup_task('plan.json')
                self.assertTrue(cleaned['cleanup_complete'])
                self.assertFalse(Path('worker.py').exists())
                self.assertFalse(Path('config.json').exists())
                self.assertEqual(Path('user.json').read_text(encoding='utf-8'), 'original')
                self.assertEqual(Path('report.txt').read_bytes(), Path('output/module/report.txt').read_bytes())
                self.assertTrue(controlled_cleanup.cleanup_task('plan.json')['cleanup_complete'])
            finally:
                os.chdir(previous)

    def test_cleanup_compatibility_exports_same_implementation(self):
        for name in ('TaskFiles', 'plan_cleanup', 'seal_cleanup', 'cleanup_task'):
            self.assertIs(getattr(cleanup_task, name), getattr(controlled_cleanup, name))

    def test_archive_and_cleanup_import_without_docx_generator(self):
        code = 'import sys; sys.path.insert(0, sys.argv[1]); import router_config, artifact_files, archive_deliverables, controlled_cleanup; assert "create_docx" not in sys.modules; assert "office.xmlio" not in sys.modules'
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(Path(__file__).resolve().parent)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
