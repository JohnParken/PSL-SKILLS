"""Archive and owned-config lifecycle regressions; only isolated synthetic tasks."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdlib_xlsx import Workbook, CT
from run_xlsx_task import run_task
from xlsx_delivery import finish_delivery
from _artifact_runtime.controlled_cleanup import cleanup_task
from test_xlsx_management import fictional_facts, spec_for, config_example
from xlsx_management import build
from test_xlsx_dashboard import query

SCRIPTS = Path(__file__).resolve().parent
SPEC = {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': '中文'}, 'B1': {'value': 42}}}]}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='xlsx-delivery-')
        os.chdir(self.temp.name)
        self.env = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [{'name': 'questionId', 'value': 'test'}]}), 'DOCX_DEBUG_ROUTER_CONFIG': '0'}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.previous)
        self.temp.cleanup()

    def cli(self, script, *args):
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/script), *args], capture_output=True, text=True)
        return child.returncode, json.loads(child.stdout)

    def task(self, filename='report.xlsx', spec=None):
        return run_task({'script': 'stdlib_xlsx.py', 'arguments': ['create', '@spec', filename], 'output': filename, 'configs': {'spec': SPEC if spec is None else spec}})

    def assert_archived(self, result, filename):
        self.assertEqual(result['delivery']['archive_status'], 'copied')
        self.assertEqual(result['delivery']['archive_message'], '归档完成')
        self.assertEqual(Path(filename).read_bytes(), Path('output/test', Path(filename).name).read_bytes())

    def test_owned_json_success_cleans_and_final_remains(self):
        code, result = self.task()
        self.assertEqual(code, 0)
        self.assert_archived(result, 'report.xlsx')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertIsNone(result['manifest'])
        self.assertTrue(result['cleanup']['manifest_removed'])
        self.assertFalse(list(Path('.').glob('xlsx-task-*')))
        self.assertEqual(Workbook.load('report.xlsx').cells['Data']['A1']['value'], '中文')

    def test_partial_result_archive_residual_never_reports_cleaned(self):
        def partial_child(script, arguments):
            Path('report.xlsx').write_bytes(b'partial output')
            Path('archive-owned.tmp').write_bytes(b'archive residual')
            return 2, {'status': 'incomplete', 'delivery': {
                'archive_status': 'failed', 'temporary_residuals': ['archive-owned.tmp']}}
        with patch('run_xlsx_task._run_child', side_effect=partial_child):
            code, result = self.task()
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(Path(result['manifest']).exists())
        self.assertEqual(Path('report.xlsx').read_bytes(), b'partial output')
        self.assertEqual(Path('archive-owned.tmp').read_bytes(), b'archive residual')

    def test_no_config_archive_residual_never_reports_successful_cleanup(self):
        Path('user-spec.json').write_text(json.dumps(SPEC))
        result = {'status': 'created', 'delivery': {
            'archive_status': 'failed', 'temporary_residuals': ['archive-owned.tmp']}}
        with patch('run_xlsx_task._run_child', return_value=(0, result)):
            code, report = run_task({'script': 'stdlib_xlsx.py',
                'arguments': ['create', 'user-spec.json', 'report.xlsx'],
                'output': 'report.xlsx', 'configs': {}})
        self.assertEqual(code, 3)
        self.assertFalse(report['cleanup']['cleanup_complete'])
        self.assertEqual(report['task_status'], 'cleanup_incomplete')
        self.assertEqual(json.loads(Path('user-spec.json').read_text()), SPEC)

    def test_failure_cleans_spec_and_preserves_original(self):
        Path('user.json').write_text('original')
        code, result = self.task(spec={'invalid': True})
        self.assertEqual(code, 1)
        self.assertEqual(result['task_status'], 'failed_cleaned')
        self.assertIsNone(result['manifest'])
        self.assertFalse(list(Path('.').glob('xlsx-task-*')))
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertEqual(Path('user.json').read_text(), 'original')
        self.assertFalse(Path('report.xlsx').exists())

    def test_existing_output_never_adopted(self):
        Path('report.xlsx').write_bytes(b'original')
        with self.assertRaises(ValueError): self.task()
        self.assertEqual(Path('report.xlsx').read_bytes(), b'original')
        self.assertEqual(len(list(Path('.').iterdir())), 1)

    def test_archive_failure_keeps_primary_and_removes_helpers(self):
        Path('output').write_text('blocked')
        code, result = self.task()
        self.assertEqual(code, 0)
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assertNotEqual(result['delivery']['archive_message'], '归档完成')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertIsNone(result['manifest'])
        self.assertFalse(list(Path('.').glob('xlsx-task-*')))
        self.assertTrue(Path('report.xlsx').exists())

    def test_missing_config_skips_archive_and_cleans(self):
        os.environ.pop('ROUTER_CONFIG_VARIABLES')
        code, result = self.task()
        self.assertEqual(code, 0)
        self.assertEqual(result['delivery']['archive_status'], 'skipped')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertIsNone(result['manifest'])
        self.assertFalse(list(Path('.').glob('xlsx-task-*')))
        self.assertFalse(Path('output').exists())

    def test_read_stdout_does_not_create_lifecycle_files(self):
        Workbook.create(SPEC).save('source.xlsx')
        before = set(Path('.').iterdir())
        code, result = self.cli('stdlib_xlsx.py', 'read', 'source.xlsx')
        self.assertEqual(code, 0)
        self.assertNotIn('delivery', result)
        self.assertEqual(set(Path('.').iterdir()), before)

    def test_no_config_task_does_not_create_cleanup_manifest(self):
        Workbook.create(SPEC).save('source.xlsx')
        code, result = run_task({'script': 'stdlib_xlsx.py', 'arguments': ['calculate', 'source.xlsx', 'calculated.xlsx'], 'output': 'calculated.xlsx'})
        self.assertEqual(code, 0)
        self.assertEqual(result['task_status'], 'no_intermediates')
        self.assertFalse(list(Path('.').glob('*.cleanup.json')))
        self.assert_archived(result, 'calculated.xlsx')

    def test_old_create_edit_cli_retains_user_specs(self):
        Path('spec.json').write_text(json.dumps(SPEC), encoding='utf-8')
        code, result = self.cli('stdlib_xlsx.py', 'create', 'spec.json', 'new.xlsx')
        self.assertEqual(code, 0)
        self.assert_archived(result, 'new.xlsx')
        Path('patch.json').write_text(json.dumps({'sheets': [{'name': 'Data', 'cells': {'B1': {'value': 99}}}]}))
        code, result = self.cli('stdlib_xlsx.py', 'edit', 'new.xlsx', 'patch.json', 'edited.xlsx')
        self.assertEqual(code, 0)
        self.assert_archived(result, 'edited.xlsx')
        self.assertTrue(Path('spec.json').exists())
        self.assertTrue(Path('patch.json').exists())

    def test_csv_tsv_cli_archives_outputs_and_preserves_source(self):
        Path('input.csv').write_text('Name,Value\n甲,42\n', encoding='utf-8')
        original = Path('input.csv').read_bytes()
        code, result = self.cli('xlsx_tabular.py', 'import', 'input.csv', 'imported.xlsx')
        self.assertEqual(code, 0); self.assert_archived(result, 'imported.xlsx')
        code, result = self.cli('xlsx_tabular.py', 'export', 'imported.xlsx', 'table.tsv', '--sheet', 'Sheet1')
        self.assertEqual(code, 0); self.assert_archived(result, 'table.tsv')
        self.assertEqual(Path('input.csv').read_bytes(), original)

    def test_wide_dashboard_and_generic_result_exports(self):
        Workbook.create(spec_for(fictional_facts())).save('facts.xlsx')
        Path('query.json').write_text(json.dumps(config_example()))
        code, result = self.cli('xlsx_management.py', 'facts.xlsx', '--sheet', 'Facts', '--query', 'query.json', '--xlsx', 'wide.xlsx')
        self.assertEqual(code, 0); self.assert_archived(result, 'wide.xlsx')
        wide = build('facts.xlsx', 'Facts', config_example())
        Path('wide.json').write_text(json.dumps(wide, default=str))
        Path('dash.json').write_text(json.dumps(query()))
        code, result = self.cli('xlsx_dashboard.py', 'wide.json', '--query', 'dash.json', '--xlsx', 'dashboard.xlsx')
        self.assertEqual(code, 0); self.assert_archived(result, 'dashboard.xlsx')
        code, result = self.cli('xlsx_result_export.py', 'wide.json', 'export.xlsx')
        self.assertEqual(code, 0); self.assert_archived(result, 'export.xlsx')

    def test_archive_alias_never_overwrites_input(self):
        Path('source.json').write_text('input')
        Path('output/test').mkdir(parents=True)
        os.link('source.json', 'output/test/report.xlsx')
        Workbook.create(SPEC).save('report.xlsx')
        result = finish_delivery({'status': 'ok'}, 'report.xlsx', ['source.json'])
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assertEqual(Path('source.json').read_text(), 'input')

    def test_concurrent_same_output_one_winner_and_no_foreign_cleanup(self):
        spec = {'script': 'stdlib_xlsx.py', 'arguments': ['create', '@spec', 'same.xlsx'], 'output': 'same.xlsx', 'configs': {'spec': SPEC}}
        payload = json.dumps(spec)
        def run():
            p = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'run_xlsx_task.py')], input=payload, text=True, capture_output=True)
            return p.returncode, json.loads(p.stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(code == 0 for code, _ in results), 1)
        self.assertEqual(Workbook.load('same.xlsx').cells['Data']['B1']['value'], 42)

    def test_unknown_script_or_wrong_output_rejected_before_creation(self):
        for spec in ({'script':'unknown.py','arguments':[],'output':'x.xlsx'}, {'script':'stdlib_xlsx.py','arguments':['create','@spec','other.xlsx'],'output':'x.xlsx','configs':{'spec':SPEC}}):
            with self.assertRaises(ValueError): run_task(spec)
        self.assertFalse(list(Path('.').iterdir()))

    def test_cleanup_error_reports_incomplete_without_regeneration(self):
        with patch('run_xlsx_task.cleanup_task', side_effect=OSError('injected receipt failure')):
            code, result = self.task()
        self.assertEqual(code, 3)
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertTrue(Path('report.xlsx').exists())

    def test_macro_cli_preserves_opaque_vba_and_archives(self):
        book = Workbook.create(SPEC)
        book.parts['[Content_Types].xml'] = book.parts['[Content_Types].xml'].replace(b'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml', b'application/vnd.ms-excel.sheet.macroEnabled.main+xml')
        book.parts['xl/vbaProject.bin'] = b'opaque-macro-sentinel'
        book.save('source.xlsm')
        code, result = self.cli('stdlib_xlsx.py', 'calculate', 'source.xlsm', 'result.xlsm')
        self.assertEqual(code, 0)
        self.assert_archived(result, 'result.xlsm')
        with zipfile.ZipFile('result.xlsm') as package:
            self.assertEqual(package.read('xl/vbaProject.bin'), b'opaque-macro-sentinel')

    def test_incomplete_calculation_keeps_exit_and_source_status(self):
        book = Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A1': {'formula': 'VLOOKUP(1,B1:C2,2,FALSE)'}}}]})
        book.save('source.xlsx')
        code, result = self.cli('stdlib_xlsx.py', 'calculate', 'source.xlsx', 'partial.xlsx')
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'incomplete')
        self.assert_archived(result, 'partial.xlsx')

    def test_partial_task_cleans_configs_and_preserves_partial_output(self):
        spec = {'sheets': [{'name': 'Data', 'cells': {'A1': {'formula': 'UNKNOWN(1)'}}}]}
        code, result = self.task(spec=spec)
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['task_status'], 'failed_cleaned')
        self.assert_archived(result, 'report.xlsx')
        self.assertIsNone(result['manifest'])
        self.assertFalse(list(Path('.').glob('xlsx-task-*')))
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_symlink_output_and_parent_traversal_rejected(self):
        Path('user.txt').write_text('original')
        Path('report.xlsx').symlink_to('user.txt')
        with self.assertRaises(ValueError): self.task()
        with self.assertRaises(ValueError): self.task(filename='../escape.xlsx')
        self.assertEqual(Path('user.txt').read_text(), 'original')

    def test_remaining_report_clis_archive_relative_task_outputs(self):
        from copy import deepcopy
        from test_xlsx_deposits import rows_example, snapshot_spec, config_example as deposit_config
        from test_xlsx_deposit_structure import structure_config
        from test_xlsx_customers import records, spec_for as customer_spec, config_example as customer_config
        from test_xlsx_hierarchy import two_branches, config_with_tree
        from test_xlsx_reconcile import query as reconcile_query
        from test_xlsx_management_policy import policy_config
        def write(name, value):
            Path(name).write_text(json.dumps(value, default=str), encoding='utf-8')
        left, right = rows_example()
        Workbook.create(snapshot_spec(left)).save('before.xlsx')
        Workbook.create(snapshot_spec(right)).save('after.xlsx')
        write('deposit-query.json', deposit_config())
        write('structure-query.json', structure_config())
        cases = [('xlsx_deposits.py', ['before.xlsx', 'after.xlsx', '--query', 'deposit-query.json'], 'deposit.xlsx'),
                 ('xlsx_deposit_structure.py', ['after.xlsx', '--query', 'structure-query.json'], 'structure.xlsx')]
        left, right = records()
        Workbook.create(customer_spec(left)).save('customers-left.xlsx')
        Workbook.create(customer_spec(right)).save('customers-right.xlsx')
        write('customer-query.json', customer_config())
        cases.append(('xlsx_customers.py', ['customers-left.xlsx', 'customers-right.xlsx', '--query', 'customer-query.json'], 'customers.xlsx'))
        config = config_with_tree()
        config['metrics'] = [m for m in config['metrics'] if m['metric_id'] == 'balance']
        all_facts = []
        for index, period in enumerate(('2025-01', '2025-02', '2025-03')):
            facts = deepcopy(two_branches())
            for row in facts: row[0] = period
            facts = [row for row in facts if row[6] == 'balance']
            all_facts.extend(facts)
            name = 'period%d.xlsx' % index
            Workbook.create(spec_for(facts)).save(name)
            write('period%d.json' % index, build(name, 'Facts', config))
        reconciliation = reconcile_query()
        reconciliation['tolerance'] = 0.01
        write('reconcile-query.json', reconciliation)
        cases.append(('xlsx_reconcile.py', ['period0.json', 'period1.json', '--query', 'reconcile-query.json'], 'reconcile.xlsx'))
        Workbook.create(spec_for(all_facts)).save('all-periods.xlsx')
        write('policy-source.json', build('all-periods.xlsx', 'Facts', config))
        write('policy-query.json', policy_config())
        cases.append(('xlsx_management_policy.py', ['policy-source.json', '--query', 'policy-query.json'], 'policy.xlsx'))
        for script, arguments, output in cases:
            with self.subTest(script=script):
                code, result = self.cli(script, *arguments, '--xlsx', output)
                self.assertEqual(code, 0, result)
                self.assert_archived(result, output)

    def test_timeout_cleans_all_config_and_manifest_files(self):
        with patch('run_xlsx_task.subprocess.run',side_effect=subprocess.TimeoutExpired('synthetic',120)):
            code,result=self.task()
        self.assertEqual(code,1)
        self.assertEqual(result['task_status'],'failed_cleaned')
        self.assertIsNone(result['manifest'])
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_manifest_delete_failure_keeps_primary_and_receipt(self):
        original=os.unlink
        def refuse(path,*args,**kwargs):
            if str(path).endswith('.cleanup.json'):
                raise PermissionError('injected retirement denial')
            return original(path,*args,**kwargs)
        with patch('run_xlsx_task.os.unlink',side_effect=refuse) as mocked:
            with patch('run_xlsx_task.os.supports_dir_fd',os.supports_dir_fd | {mocked}):
                code,result=self.task()
        self.assertEqual(code,3)
        self.assertTrue(Path('report.xlsx').exists())
        self.assertTrue(Path(result['manifest']).exists())
        self.assertFalse(result['cleanup']['cleanup_complete'])
