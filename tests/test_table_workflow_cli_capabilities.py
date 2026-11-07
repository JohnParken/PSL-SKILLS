"""Exercise the default subprocess transport and independently expected business results."""
import copy
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
WORKFLOW_SCRIPTS = HERE.parent / "skills" / "table-workflow" / "scripts"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKFLOW_SCRIPTS))
import test_table_workflow as fixtures
from workflow_runtime import Dependencies
from workflow_cli_client import CapabilityError, Client
from table_workflow import Workflow
from table_workflow_cli_protocol import PROTOCOL, dumps, loads


class CliCapabilityTests(fixtures.WorkflowTests):
    # Deliberately replay existing expected-value tests across the actual process
    # boundary, rather than testing only an in-process compatibility adapter.
    @classmethod
    def setUpClass(cls):
        cls.api = Dependencies(str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT))

    @classmethod
    def tearDownClass(cls):
        cls.api.close()

    def tearDown(self):
        self.api.close()

    def test_names_cli_and_ninth_sheet(self):
        names = ['Sheet' + str(i) for i in range(40)]
        self.api.xlsx.Workbook.create({'sheets': [{'name': name, 'cells': {'A1': {'value': name}}} for name in names]}).save('many.xlsx')
        def run(command, request):
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(WORKFLOW_SCRIPTS/'table_workflow.py'),
                                    command, '--xlsx-root', str(fixtures.XLSX_ROOT)],
                                   input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(child.returncode, 0, child.stderr + child.stdout)
            self.assertLessEqual(len(child.stdout.strip()), 24000)
            return json.loads(child.stdout)
        first = run('names', {'path': 'many.xlsx'})
        second = run('names', {'path': 'many.xlsx', 'offset': first['next_offset'], 'sha256': first['sha256']})
        self.assertEqual(first['names'] + second['names'], names)
        result = run('inspect', {'files': [{'path': 'many.xlsx', 'sheet': names[8]}]})
        self.assertEqual(result['files'][0]['sheets'][0]['name'], names[8])

    def test_inspect_batches_ten_thousand_addresses(self):
        self.api.xlsx.Workbook.create({'sheets': [{'name': 'Data', 'cells': {
            'A' + str(i): {'value': i} for i in range(1, 10001)}}]}).save('large.xlsx')
        before = self.api.client.metrics['requests']
        result = Workflow(self.api).inspect({'files': [{'path': 'large.xlsx'}]})
        count = self.api.client.metrics['requests'] - before
        self.assertLess(count, 100)
        self.assertEqual(result['files'][0]['sheets'][0]['last_row'], 10000)

    def test_actual_cli_archive_success_conflict_and_invalid_configuration(self):
        config = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'workflow-cli-test'}]})
        with patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': config}):
            result = Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
            self.assertFalse(result['delivery_incomplete'])
            for name in ('filled.xlsx', 'audit.json'):
                self.assertEqual(result['delivery'][name]['archive_status'], 'copied')
                self.assertEqual(Path(name).read_bytes(), (Path('output/workflow-cli-test')/name).read_bytes())
            conflict = Path('output/workflow-cli-test/second.xlsx')
            conflict.write_bytes(b'existing protected archive')
            result = Workflow(self.api).apply(self.plan, 'second.xlsx', 'second.json')
            self.assertTrue(result['delivery_incomplete'])
            self.assertEqual(conflict.read_bytes(), b'existing protected archive')
            self.assertTrue(Path('second.xlsx').is_file())
        with patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': '{invalid'}):
            result = Workflow(self.api).apply(self.plan, 'third.xlsx', 'third.json')
            # Preserve existing output_location fallback semantics: invalid
            # configuration skips archival with a reason, rather than pretending copied.
            self.assertFalse(result['delivery_incomplete'])
            self.assertEqual(result['delivery']['third.xlsx']['archive_status'], 'skipped')
            self.assertEqual(result['delivery']['third.xlsx']['fallback_reason'], 'invalid_router_config_json')
            self.assertTrue(result['output_written'])
            self.assertTrue(Path('third.xlsx').is_file())


# Tests requiring fixture mutation of ZIP internals remain in the compatibility
# suite. Every ordinary/public-operation case is replayed here.
EXCLUDE = {'test_merged_multilevel_header_paths', 'test_source_multilevel_merge_and_duplicate_leaf_ambiguity',
           'test_preserve_untouched_zip_content_and_style', 'test_stale_chart_cache_and_shared_formula_preflight',
           'test_archive_success_and_failure_preserve_verified_outputs', 'test_archive_residual_report_does_not_claim_cleaned',
           'test_dependency_failure_preserves_both_channels_and_exit'}
for name in EXCLUDE:
    setattr(CliCapabilityTests, name, None)


import test_table_workflow_quality as quality_fixtures


class CliQualityTests(quality_fixtures.QualityTests):
    def setUp(self):
        super().setUp()
        self.api = Dependencies(str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT))
        self.case.api = self.api
        self.addCleanup(self.api.close)

    # Direct internal API immutability stays in its original low-level test.
    test_lookup_api_preserves_inputs_and_requires_header_schema = None


class WireTests(unittest.TestCase):
    def test_wire_lossless_and_rejects_unsafe_values(self):
        value = {('text', '000123'): Decimal('0.123456789012345678901234567890123456789'),
                 'binary': b'\x00\xff', 'boolean': True, 'none': None, 'unicode': '银行𠀀'}
        self.assertEqual(loads(dumps(value)), value)
        for raw in (b'{"$map":[],"$map":[]}', b'{"$decimal":"NaN"}', b'{"$object":"os.system"}', b'1.2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                loads(raw)
        for name in ('xlsx', 'et'):
            self.assertEqual((fixtures.REPO/'skills'/name/'scripts'/(name+'_cli_protocol.py')).read_bytes(), (WORKFLOW_SCRIPTS/'table_workflow_cli_protocol.py').read_bytes())

    def test_default_adapter_never_imports_dependency_python_modules(self):
        program = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                   'from workflow_runtime import Dependencies; d=Dependencies(sys.argv[2],sys.argv[3]); '
                   'assert "stdlib_xlsx" not in sys.modules and "read_et" not in sys.modules; d.close(); print("isolated-ok")')
        with tempfile.TemporaryDirectory() as task:
            child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program, str(WORKFLOW_SCRIPTS),
                                    str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT)], cwd=task, capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr + child.stdout)
        self.assertIn('isolated-ok', child.stdout)

    def test_protocol_unknown_operation_version_and_expired_handle(self):
        client = Client(fixtures.XLSX_ROOT)
        self.addCleanup(client.close)
        for operation, parameters in [('execute_python', {}), ('region', {'handle': 999, 'sheet': 'Data', 'range': 'A1'})]:
            with self.subTest(operation=operation), self.assertRaises(CapabilityError):
                client.call(operation, **parameters)
        bad = dumps({'protocol': 'v999', 'id': 17, 'operation': 'open', 'parameters': {}})
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(fixtures.XLSX_ROOT/'scripts'/'xlsx_cli.py'), 'serve'],
                               input=bad+b'\n', capture_output=True)
        reply = loads(child.stdout.strip())
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['id'], 17)

    def test_missing_capability_stops_before_input_or_output(self):
        from workflow_runtime import DependencyCheckError
        result = subprocess.CompletedProcess([], 0, json.dumps({'status': 'ready', 'check_kind': 'environment',
                                                                'artifact_validation': 'not_run'}), '')
        bad = subprocess.CompletedProcess([], 0, json.dumps({'protocol': PROTOCOL, 'interface_version': 999, 'skill': 'xlsx'}), '')
        with patch('workflow_runtime.Dependencies.run_process', side_effect=[result, bad]), self.assertRaisesRegex(ValueError, 'Incompatible'):
            Dependencies(str(fixtures.XLSX_ROOT))

    def test_missing_address_batch_capability_is_rejected(self):
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(fixtures.XLSX_ROOT/'scripts/xlsx_cli.py'),
                                'capabilities'], capture_output=True, text=True, check=True)
        capabilities = json.loads(child.stdout)
        capabilities['operations'].remove('address_many')
        ready = subprocess.CompletedProcess([], 0, json.dumps({'status': 'ready', 'check_kind': 'environment',
                                                             'artifact_validation': 'not_run'}), '')
        missing = subprocess.CompletedProcess([], 0, json.dumps(capabilities), '')
        with patch('workflow_runtime.Dependencies.run_process', side_effect=[ready, missing]):
            with self.assertRaisesRegex(ValueError, 'lacks required capabilities'):
                Dependencies(str(fixtures.XLSX_ROOT))

    def test_old_dependency_keeps_legacy_cli_but_requires_explicit_upgrade_for_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'xlsx'
            shutil.copytree(fixtures.XLSX_ROOT, root, ignore=shutil.ignore_patterns('__pycache__', 'xlsx_cli.py'))
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(root/'scripts'/'stdlib_xlsx.py'), 'check'], capture_output=True)
            self.assertEqual(child.returncode, 0, child.stderr)
            with self.assertRaises(ValueError) as caught:
                Dependencies(str(root))
            self.assertIn('stderr', caught.exception.diagnostics)
            self.assertEqual(caught.exception.diagnostics['exit_code'], 2)

    def test_batch_contract_rejects_short_result_and_transport_failure(self):
        dependencies = Dependencies(str(fixtures.XLSX_ROOT))
        self.addCleanup(dependencies.close)
        with patch.object(dependencies.client, 'call', return_value=[]), self.assertRaisesRegex(ValueError, 'count mismatch'):
            dependencies.evaluate_many('A1', 'Metrics', [{'A1': Decimal(1)}])
        import queue
        with patch('workflow_cli_client.queue.Queue.get', side_effect=queue.Empty), self.assertRaises(CapabilityError) as caught:
            dependencies.client.call('address', ref='A1')
        self.assertEqual(caught.exception.diagnostics['state'], 'uncertain')
        self.assertIsNone(dependencies.client.process)

    def test_bound_formula_range_matches_legacy_without_regex_inference(self):
        dependencies = Dependencies(str(fixtures.XLSX_ROOT))
        self.addCleanup(dependencies.close)
        bindings = {'A1': Decimal('0.1'), 'B1': Decimal('0.2'), 'C1': Decimal('0.3')}
        self.assertEqual(dependencies.evaluate_bound('SUM(A1:C1)', 'Metrics', bindings), Decimal('0.6'))
        self.assertEqual(dependencies.evaluate_bound('IF("A2"="A2",A1,0)', 'Metrics', {'A1': Decimal(7)}), Decimal(7))

    def test_readonly_independent_roots_and_no_external_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, original in [('xlsx', fixtures.XLSX_ROOT), ('et', fixtures.ET_ROOT), ('table-workflow', fixtures.WORKFLOW_ROOT)]:
                shutil.copytree(original, root/name, ignore=shutil.ignore_patterns('__pycache__'))
            task = root/'task'
            task.mkdir()
            paths = list((root/'xlsx').rglob('*')) + list((root/'et').rglob('*')) + list((root/'table-workflow').rglob('*'))
            originals = {p: p.read_bytes() for p in paths if p.is_file()}
            try:
                for p in paths:
                    p.chmod(0o555 if p.is_dir() else 0o444)
                program = ('import sys; sys.path.insert(0, sys.argv[1]); from workflow_runtime import Dependencies; '
                           'd=Dependencies(sys.argv[2],sys.argv[3]); b=d.xlsx.Workbook.create({"sheets":[{"name":"Data","cells":{"A1":{"value":"独立"}}}]}); '
                           'b.calculate(); b.save("independent.xlsx"); assert b.cells["Data"]["A1"]["value"]=="独立"; d.close()')
                child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program, str(root/'table-workflow'/'scripts'),
                                        str(root/'xlsx'), str(root/'et')], cwd=task, env={**os.environ, 'PATH': ''}, capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, child.stderr + child.stdout)
                self.assertTrue((task/'independent.xlsx').is_file())
                self.assertTrue(all(p.read_bytes() == content for p, content in originals.items()))
            finally:
                for p in paths:
                    p.chmod(0o755 if p.is_dir() else 0o644)


if __name__ == '__main__':
    unittest.main()
