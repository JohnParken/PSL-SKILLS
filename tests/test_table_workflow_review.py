"""Adversarial contract regressions for the second table-workflow review."""
import contextlib
import copy
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_table_workflow as fixtures
import table_workflow as workflow
from workflow_runtime import Dependencies, read_request


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkflowTests.setUpClass()

    def setUp(self):
        self.case = fixtures.WorkflowTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.api = self.case.api
        self.plan = self.case.plan

    def cli(self, command='apply', plan=None, extra=(), et=False):
        argv = ['table_workflow.py', command, '--xlsx-root', str(fixtures.XLSX_ROOT)]
        if command == 'apply':
            argv.extend(['--output', 'filled.xlsx', '--audit', 'audit.json'])
        if et:
            argv.extend(['--et-root', str(fixtures.ET_ROOT)])
        argv.extend(extra)
        stdin = SimpleNamespace(buffer=io.BytesIO(workflow.encode(self.plan if plan is None else plan).encode('utf-8')))
        output = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdin', stdin), contextlib.redirect_stdout(output):
            code = workflow.main()
        return code, json.loads(output.getvalue())

    def stale_formula(self):
        Path('current.xlsx').unlink()
        self.case.book('current.xlsx', [['机构', '金额'], ['甲'], ['乙', 50]], {'B2': {'formula': '120+80'}})
        book = self.api.xlsx.Workbook.load('current.xlsx')
        part = book.sheets['Data']
        book.parts[part] = book.parts[part].replace(b'<f>120+80</f>', b'<f>120+80</f><v>999</v>')
        Path('current.xlsx').unlink()
        book.save('current.xlsx')
        self.plan['sources']['current'].update(sha256=self.case.digest('current.xlsx'), range='A1:B3')

    def test_formula_modes_are_isolated_and_order_independent(self):
        self.stale_formula()
        original = Path('current.xlsx').read_bytes()
        fresh = {**self.plan['sources']['current'], 'formulas': 'calculate'}
        saved = {**fresh, 'formulas': 'cached'}
        self.plan['metrics'] = [{'id': name, 'source': name, 'operation': 'sum', 'measure': 'B', 'basis': 'Compare computation policies'}
                                for name in ('fresh', 'saved')]
        self.plan['derived'] = []
        self.plan['target']['columns'] = [
            {'column': 'B', 'metric': 'fresh', 'unit': '元', 'period': '2026-10', 'decimals': 2},
            {'column': 'C', 'metric': 'saved', 'unit': '元', 'period': '2026-10', 'decimals': 2}]
        for sources in ({'fresh': fresh, 'saved': saved}, {'saved': saved, 'fresh': fresh}):
            self.plan['sources'] = sources
            engine = workflow.Workflow(self.api)
            candidate, audit = engine.prepare(self.plan)
            self.assertEqual(candidate.cells['Data']['B2']['value'], Decimal(200))
            self.assertEqual(candidate.cells['Data']['C2']['value'], Decimal(999))
            self.assertEqual(audit['status'], 'partial')
            self.assertEqual(engine.books['current.xlsx'].cells['Data']['B2']['value'], Decimal(999))
            _, again = engine.prepare(self.plan)
            self.assertEqual(len(again['warnings']), 1)
        self.assertEqual(Path('current.xlsx').read_bytes(), original)

    def test_audit_plan_numeric_literals_roundtrip_without_coercion(self):
        number = Decimal('0.50000000000000000000000000000000001')
        self.plan['sources']['current']['filters'] = [{'column': 'B', 'op': 'gt', 'value': number}]
        candidate, report = workflow.Workflow(self.api).prepare(self.plan)
        restored = read_request(io.BytesIO(workflow.encode(report).encode('utf-8')))['plan']
        self.assertEqual(restored['sources']['current']['filters'][0]['value'], number)
        self.assertIsInstance(restored['sources']['current']['filters'][0]['value'], Decimal)
        again, _ = workflow.Workflow(self.api).prepare(restored)
        self.assertEqual(again.cells, candidate.cells)
        code, _ = self.cli()
        self.assertEqual(code, 0)
        audit = json.loads(Path('audit.json').read_text(encoding='utf-8'), parse_float=Decimal)
        self.assertEqual(audit['plan']['sources']['current']['filters'][0]['value'], number)

    def test_large_archive_diagnostics_preserve_published_partial_state(self):
        self.case.replace_source([['机构', '金额'], ['甲', 200], ['甲', None], ['乙', 50]])
        self.plan['metrics'][0]['missing'] = 'omit'
        payload = {'archive_status': 'failed', 'archive_message': '归档失败', 'warnings': ['synthetic ' * 10000],
                   'temporary_residuals': ['synthetic-owned-residual']}
        with patch('xlsx_delivery.archive_outputs', return_value=payload), patch.object(workflow, 'Dependencies', fixtures.Dependencies):
            code, result = self.cli()
        self.assertEqual(code, 3)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['output'], 'filled.xlsx')
        self.assertEqual(result['audit'], 'audit.json')
        self.assertTrue(result['output_written'])
        self.assertTrue(result['audit_written'])
        self.assertTrue(result['delivery_incomplete'])
        self.assertEqual(result['cleanup'], 'archive_cleanup_incomplete')
        self.assertTrue(result['projection_truncated'])
        self.assertEqual(result['delivery']['filled.xlsx']['temporary_residual_count'], 1)
        self.assertTrue(Path('filled.xlsx').is_file())
        self.assertTrue(Path('audit.json').is_file())

    def test_projection_does_not_mutate_complete_result(self):
        result = workflow.Workflow(self.api).inspect({'files': [{'path': 'current.xlsx'}] * 17})
        before = copy.deepcopy(result)
        projected = workflow.bounded_result(result, limit=12000)
        self.assertTrue(projected['projection_truncated'])
        self.assertEqual(result, before)
        self.assertEqual(workflow.bounded_result(result, limit=12000), projected)

    def test_audit_open_failure_reports_known_workbook_outcome(self):
        original_open = Path.open
        def open_path(path, mode='r', *args, **kwargs):
            if path == Path('audit.json') and mode == 'xb':
                raise OSError('synthetic audit permission error')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', open_path), patch('xlsx_delivery.finish_delivery') as archive:
            code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['output_written'])
        self.assertFalse(result['audit_written'])
        self.assertEqual(result['verification'], 'workbook_and_inputs_passed')
        self.assertTrue(result['diagnostics']['output_exists'])
        self.assertFalse(result['diagnostics']['audit_exists'])
        archive.assert_not_called()

    def test_audit_partial_write_is_explicit(self):
        original_open = Path.open
        @contextlib.contextmanager
        def partial_file(path):
            with original_open(path, 'xb') as handle:
                def write(raw):
                    handle.write(raw[:20])
                    raise OSError('synthetic disk full')
                yield SimpleNamespace(write=write)
        def open_path(path, mode='r', *args, **kwargs):
            return partial_file(path) if path == Path('audit.json') and mode == 'xb' else original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', open_path):
            code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['output_written'])
        self.assertEqual(result['audit_written'], 'incomplete')
        self.assertEqual(Path('audit.json').stat().st_size, 20)

    def test_preflight_failure_is_not_reported_as_unknown_write(self):
        self.plan['sources']['current']['repeated_keys'] = 'reject'
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertFalse(result['output_written'])
        self.assertFalse(result['audit_written'])
        self.assertFalse(Path('filled.xlsx').exists())

    def test_source_calculation_failure_preserves_engine_report(self):
        Path('current.xlsx').unlink()
        self.case.book('current.xlsx', [['机构', '金额'], ['甲'], ['乙', 50]], {'B2': {'formula': 'UNSUPPORTED(1)'}})
        self.plan['sources']['current'].update(sha256=self.case.digest('current.xlsx'), range='A1:B3', formulas='calculate')
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertIn('issues', result['diagnostics']['calculation'])
        self.assertEqual(result['diagnostics']['calculation']['issues'][0]['cell'], 'B2')
        self.assertFalse(result['output_written'])

    def test_inspection_retains_et_partial_and_exit_three(self):
        Path('prior.et').write_bytes(Path('prior.xlsx').read_bytes())
        code, result = self.cli(command='inspect', plan={'files': [{'path': 'prior.et'}]}, et=True)
        self.assertEqual(code, 3)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['source_partial_count'], 1)
        self.assertEqual(result['files'][0]['source_status'], 'partial')
        self.assertFalse(result['complete_data_for_computation'])

    def test_non_target_additions_and_wrong_target_values_are_rejected(self):
        original_patch = self.api.xlsx.Workbook.patch
        for coordinate, message in [('F99', 'Non-target cells'), ('B2', 'Target value differs')]:
            def corrupt(book, spec):
                original_patch(book, spec)
                original_patch(book, {'sheets': [{'name': 'Data', 'cells': {coordinate: {'value': 999}}}]})
            with self.subTest(coordinate=coordinate), patch.object(self.api.xlsx.Workbook, 'patch', corrupt):
                with self.assertRaisesRegex(ValueError, message):
                    workflow.Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
            self.assertFalse(Path('filled.xlsx').exists())

    def test_subtotals_filters_and_duplicate_keys_have_independent_counts(self):
        self.case.replace_source([['机构', '金额'], ['甲', 120], ['甲', 80], ['甲', 200], ['乙', 50]])
        self.plan['sources']['current'].update(exclude_rows=[4], repeated_keys='reject')
        with self.assertRaisesRegex(ValueError, 'repeated keys'):
            workflow.Workflow(self.api).prepare(self.plan)
        self.plan['sources']['current']['repeated_keys'] = 'aggregate'
        _, audit = workflow.Workflow(self.api).prepare(self.plan)
        summary = audit['metrics']['current']['summary']
        self.assertEqual(summary['reconciliation']['source_sum'], Decimal(250))
        self.assertEqual((summary['source_rows'], summary['selected_rows'], summary['excluded_rows']), (4, 3, 1))
        self.plan['sources']['current']['filters'] = [{'column': 'B', 'op': 'le', 'value': 100}]
        _, audit = workflow.Workflow(self.api).prepare(self.plan)
        summary = audit['metrics']['current']['summary']
        self.assertEqual((summary['selected_rows'], summary['excluded_rows'], summary['filter_excluded_rows']), (2, 2, 1))
        self.assertEqual(summary['reconciliation']['source_sum'], Decimal(130))

    def test_unshown_tail_rows_still_participate_in_validation(self):
        self.case.replace_source([['机构', '金额']] + [['甲', 1]] * 100 + [['乙', 50], [None, 999]])
        result = workflow.bounded_result(workflow.Workflow(self.api).inspect({'files': [{'path': 'current.xlsx'}]}))
        self.assertGreater(result['files'][0]['sheets'][0]['unshown_positions'], 190)
        with self.assertRaisesRegex(ValueError, 'Missing source keys'):
            workflow.Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
        self.assertFalse(Path('filled.xlsx').exists())

    def test_dependency_timeout_signal_and_malformed_success_are_not_passed(self):
        events = [subprocess.TimeoutExpired(['selfcheck'], 60, output=b'out', stderr=b'err'),
                  subprocess.CompletedProcess([], -9, 'out', 'err'),
                  subprocess.CompletedProcess([], 0, 'not JSON: "status": "ready"', '')]
        for event in events:
            kwargs = {'side_effect': event} if isinstance(event, Exception) else {'return_value': event}
            with self.subTest(event=type(event).__name__), patch('workflow_runtime.subprocess.run', **kwargs):
                with self.assertRaises(ValueError) as caught:
                    Dependencies(str(fixtures.XLSX_ROOT))
                evidence = caught.exception.diagnostics
                self.assertIn('stdout', evidence)
                self.assertIn('stderr', evidence)
                self.assertEqual(evidence['exit_code'], 0 if event is events[2] else None)

    def test_command_option_mismatch_and_template_alias_are_refused(self):
        code, result = self.cli(command='plan', extra=('--output', 'should-not-exist.xlsx'))
        self.assertEqual(code, 1)
        self.assertIn('only apply', result['message'])
        self.plan['sources']['prior'].update(path='./target.xlsx', sha256=self.case.digest('target.xlsx'))
        with self.assertRaisesRegex(ValueError, 'Template cannot also be a source'):
            workflow.Workflow(self.api).prepare(self.plan)

    def test_target_subtotal_formula_is_preserved_and_recomputed(self):
        Path('target.xlsx').unlink()
        self.case.book('target.xlsx', [['机构', '余额', '增量', '增幅'], ['甲'], ['乙'], ['合计']],
                       {'B4': {'formula': 'SUM(B2:B3)'}})
        self.plan['target'].update(sha256=self.case.digest('target.xlsx'), range='A1:D4', exclude_rows=[4])
        candidate, audit = workflow.Workflow(self.api).prepare(self.plan)
        self.assertEqual(candidate.cells['Data']['B4']['formula'], 'SUM(B2:B3)')
        self.assertEqual(candidate.cells['Data']['B4']['value'], Decimal(250))
        self.assertNotIn('B4', [item['cell'] for item in audit['writes']])

    def test_corrupt_et_keeps_original_failure_stage(self):
        Path('broken.et').write_bytes(b'unknown private bytes')
        code, result = self.cli(command='inspect', plan={'files': [{'path': 'broken.et'}]}, et=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['diagnostics']['source_path'], 'broken.et')
        self.assertEqual(result['diagnostics']['source_status'], 'unsupported')
        self.assertEqual(result['diagnostics']['source_location']['stage'], 'container')
        self.assertFalse(result['output_written'])

    def test_later_key_failure_does_not_erase_accepted_partial_warning(self):
        self.case.book('bad-prior.xlsx', [['机构', '金额'], [None, 150], ['乙', 100]])
        Path('prior.et').write_bytes(Path('bad-prior.xlsx').read_bytes())
        self.plan['sources']['prior'].update(path='prior.et', sha256=self.case.digest('prior.et'), accept_partial=True)
        code, result = self.cli(et=True)
        self.assertEqual(code, 1)
        self.assertGreater(result['warning_count'], 0)
        self.assertEqual(result['warnings_preview'][0]['source_status'], 'partial')
        self.assertEqual(result['diagnostics']['null_key_rows'], 1)
        self.assertFalse(result['output_written'])


if __name__ == '__main__':
    unittest.main()
