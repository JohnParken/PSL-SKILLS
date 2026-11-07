"""Cross-Skill regressions for bounded metadata and typed-key batches."""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_table_workflow as fixtures
from table_workflow import Workflow
from workflow_runtime import Dependencies


class LargeTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkflowTests.setUpClass()

    def setUp(self):
        self.case = fixtures.WorkflowTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.api = Dependencies(str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT))
        self.addCleanup(self.api.close)

    def test_ten_thousand_composite_target_keys_use_two_batches(self):
        self.case.book('large.xlsx', [['机构', '编号', '金额']]
                       + [['客户', str(i).zfill(6)] for i in range(10000)])
        spec = {'path': 'large.xlsx', 'sha256': self.case.digest('large.xlsx'), 'sheet': 'Data',
                'range': 'A1:C10001', 'header_rows': '1', 'keys': ['A', 'B'],
                'columns': [{'column': 'C', 'metric': 'amount', 'unit': '元', 'period': '2026-10', 'decimals': 2}]}
        with patch.object(self.api.client, 'call', wraps=self.api.client.call) as calls:
            _, _, keys, _ = Workflow(self.api).target(spec)
        batches = [call.kwargs['cells'] for call in calls.call_args_list if call.args == ('typed_values',)]
        self.assertEqual([len(batch) for batch in batches], [10000, 10000])
        self.assertFalse(any(call.args == ('typed_value',) for call in calls.call_args_list))
        self.assertEqual(keys[2], (('text', '客户'), ('text', '000000')))
        self.assertEqual(keys[10001], (('text', '客户'), ('text', '009999')))

    def test_names_do_not_transfer_cells_and_can_then_load(self):
        self.case.book('large.xlsx', [['机构', '金额']] + [[str(i), i] for i in range(10000)])
        flow = Workflow(self.api)
        before = self.api.client.metrics['response_bytes']
        with patch.object(self.api.client, 'call', wraps=self.api.client.call) as calls:
            result = flow.names({'path': 'large.xlsx'})
        self.assertEqual(result['names'], ['Data'])
        self.assertEqual([call.args[0] for call in calls.call_args_list], ['open_metadata', 'release'])
        self.assertLess(self.api.client.metrics['response_bytes'] - before, 3000)
        self.assertEqual(flow.books, {})
        with patch.object(self.api, 'metadata', side_effect=AssertionError('Metadata was unnecessarily reparsed')):
            self.assertEqual(flow.names({'path': 'large.xlsx'}), result)
        book = flow.load({'path': 'large.xlsx', 'sha256': result['sha256']})
        self.assertEqual(book.cells['Data']['B10001']['value'], Decimal(9999))

    def test_sha_mismatch_stops_before_metadata_request(self):
        with patch.object(self.api, 'metadata', wraps=self.api.metadata) as metadata:
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                Workflow(self.api).names({'path': 'target.xlsx', 'sha256': '0' * 64})
        metadata.assert_not_called()

    def test_target_formulas_errors_duplicates_and_blanks_are_still_rejected(self):
        original = self.case.plan['target']
        for name, extra, rows, message in (
            ('formula.xlsx', {'A2': {'formula': '1+1'}}, [['机构', '余额', '增量', '增幅'], ['甲'], ['乙']], 'Formula/error'),
            ('duplicates.xlsx', {}, [['机构', '余额', '增量', '增幅'], ['甲'], ['甲']], 'Target keys'),
            ('blank.xlsx', {}, [['机构', '余额', '增量', '增幅'], [None], ['乙']], 'Target keys'),
        ):
            self.case.book(name, rows, extra)
            spec = {**original, 'path': name, 'sha256': self.case.digest(name)}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                Workflow(self.api).target(spec)
        flow = Workflow(self.api)
        book = flow.load(original)
        book.cells['Data']['A2'] = {'type': 'e', 'value': '#N/A'}
        with self.assertRaisesRegex(ValueError, 'Formula/error'):
            flow.target(original)

    def test_batch_results_are_complete_and_precision_is_preserved(self):
        cells = [{'value': '001'}, {'value': Decimal('0.12345678901234567890123456789')}, {'value': True}, {}]
        self.assertEqual(self.api.typed_values(cells), self.case.api.typed_values(cells))
        self.assertEqual(self.api.typed_values([]), [])
        for response in ([], [('text', '001')], [('bad', None)] * 4, [['text', '001']] * 4):
            with self.subTest(response=response), patch.object(self.api.client, 'call', return_value=response):
                with self.assertRaisesRegex(ValueError, 'typed value results'):
                    self.api.typed_values(cells)

    def test_missing_new_capabilities_stop(self):
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(fixtures.XLSX_ROOT/'scripts/xlsx_cli.py'),
                                'capabilities'], capture_output=True, text=True, check=True)
        for operation in ('typed_values', 'open_metadata', 'release'):
            capabilities = json.loads(child.stdout)
            capabilities['operations'].remove(operation)
            ready = subprocess.CompletedProcess([], 0, json.dumps({'status': 'ready', 'check_kind': 'environment',
                                                                   'artifact_validation': 'not_run'}), '')
            missing = subprocess.CompletedProcess([], 0, json.dumps(capabilities), '')
            with self.subTest(operation=operation), patch('workflow_runtime.Dependencies.run_process', side_effect=[ready, missing]):
                with self.assertRaisesRegex(ValueError, 'lacks required capabilities'):
                    Dependencies(str(fixtures.XLSX_ROOT))
        original = Dependencies.run_process
        with patch.object(Dependencies, 'run_process', wraps=original) as run:
            def without_et_metadata(name, arguments, *args, **kwargs):
                response = original(name, arguments, *args, **kwargs)
                if name == 'et' and arguments[-1] == 'capabilities':
                    cap = json.loads(response.stdout)
                    cap['operations'].remove('metadata')
                    response.stdout = json.dumps(cap)
                return response
            run.side_effect = without_et_metadata
            with self.assertRaisesRegex(ValueError, 'lacks required capabilities'):
                Dependencies(str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT))

    def test_metadata_unknown_status_or_incomplete_diagnostics_stops(self):
        valid = {'schema_version': 'workbook_metadata_v1', 'native': False, 'sheet_names': ['Sheet1'],
                 'source_status': 'partial', 'source_issues': [{'code': 'missing'}],
                 'source_coverage': {'cell_value_coverage': 'missing_formula_caches'}, 'input_schema': 'biff8_readonly_v1'}
        with patch.object(self.api, 'et_request', return_value={'metadata': valid, 'exit_code': 3}):
            self.assertEqual(self.api.metadata('source.et', b'not used'), valid)
        for changed in ({'schema_version': 'v999'}, {'input_schema': 'unknown'}, {'source_status': 'unknown'}, {'native': True},
                        {'source_issues': None}, {'source_coverage': None}, {'sheet_names': ['Same', 'Same']}):
            with self.subTest(changed=changed), patch.object(self.api, 'et_request', return_value={'metadata': {**valid, **changed}, 'exit_code': 3}):
                with self.assertRaises(ValueError):
                    self.api.metadata('source.et', b'not used')


if __name__ == '__main__':
    unittest.main()
