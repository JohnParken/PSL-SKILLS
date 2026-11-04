"""Quality/lookup regressions; real-source fixture and synthetic rules are labeled."""
import copy
from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_table_workflow as fixtures
from table_workflow import Workflow, encode


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkflowTests.setUpClass()

    def setUp(self):
        self.case = fixtures.WorkflowTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.api = self.case.api
        self.plan = self.case.plan

    def source(self, path, bounds, keys, repeated='reject'):
        return {'path': path, 'sha256': self.case.digest(path), 'sheet': 'Data', 'range': bounds,
                'header_rows': '1', 'keys': keys, 'unit': '元', 'period': '2026-10', 'repeated_keys': repeated}

    def lookup_plan(self):
        self.case.replace_source([['账户', '金额', '客户'], ['a', 120, 'x'], ['b', 80, 'x'], ['c', 50, 'y']])
        self.plan['sources']['current'].update(range='A1:C4', keys=['A'], repeated_keys='reject')
        self.case.book('dimension.xlsx', [['客户', '机构'], ['x', '甲'], ['y', '乙'], ['unused', '丙']])
        self.plan['sources']['dimension'] = self.source('dimension.xlsx', 'A1:B4', ['A'])
        self.plan['lookups'] = [{'id': 'enriched', 'left': 'current', 'right': 'dimension', 'left_keys': ['C'], 'right_keys': ['A'],
                                 'relationship': 'many_to_one', 'columns': [{'column': 'B', 'as': '机构'}],
                                 'keys': ['lookup:机构'], 'repeated_keys': 'aggregate', 'max_rows': 10, 'basis': '客户维表归属'}]
        self.plan['metrics'][0]['source'] = 'enriched'
        self.plan['metrics'][0]['measure'] = 'B'
        return self.plan

    def quality_check(self, **changes):
        item = {'id': 'balance', 'kind': 'equal', 'left': 'current', 'right': 'current',
                'scope': 'each_target', 'basis': 'Independent explicit control', 'absolute_tolerance': '0', 'relative_tolerance': '0'}
        item.update(changes)
        self.plan['checks'] = [item]
        return item

    def only_current(self):
        self.plan['metrics'] = [self.plan['metrics'][0]]
        self.plan['derived'] = []
        self.plan['target']['columns'] = [self.plan['target']['columns'][0]]

    def run_plan(self):
        return Workflow(self.api).prepare(self.plan)

    def test_lookup_full_apply_conservation_and_lineage(self):
        self.lookup_plan()
        result = Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
        self.assertEqual(result['status'], 'validated')
        book = self.api.xlsx.Workbook.load('filled.xlsx')
        self.assertEqual(book.cells['Data']['B2']['value'], Decimal(200))
        audit = json.loads(Path('audit.json').read_text(encoding='utf-8'))
        lookup = audit['lookups']['enriched']
        self.assertEqual(lookup['row_lineage'], [{'left_row': 2, 'right_row': 2}, {'left_row': 3, 'right_row': 2}, {'left_row': 4, 'right_row': 3}])
        self.assertEqual((lookup['input_rows'], lookup['output_rows'], lookup['diagnostics']['unused_right_rows']), (3, 3, 1))
        amount = next(item for item in lookup['fact_conservation'] if item['column'] == 'B')
        self.assertEqual((amount['before_sum'], amount['after_sum']), ('250', '250'))
        self.assertIn('dimension', audit['source_regions'])
        self.assertEqual(audit['metrics']['current']['row_membership'][0]['physical_rows'], [2, 3])

    def test_one_to_one_success_and_duplicate_fact_refusal(self):
        self.lookup_plan()['lookups'][0]['relationship'] = 'one_to_one'
        with self.assertRaisesRegex(ValueError, 'cardinality'):
            self.run_plan()
        self.plan['sources']['current']['exclude_rows'] = [3]
        candidate, _ = self.run_plan()
        self.assertEqual(candidate.cells['Data']['B2']['value'], Decimal(120))

    def test_duplicate_dimension_refused_even_when_raw_grouping_allows_it(self):
        self.lookup_plan()
        Path('dimension.xlsx').unlink()
        self.case.book('dimension.xlsx', [['客户', '机构'], ['x', '甲'], ['x', '乙'], ['y', '乙']])
        self.plan['sources']['dimension'].update(sha256=self.case.digest('dimension.xlsx'), repeated_keys='aggregate')
        with self.assertRaisesRegex(ValueError, 'cardinality'):
            self.run_plan()

    def test_lookup_unmatched_fact_never_drops_amount(self):
        self.lookup_plan()
        self.plan['sources']['dimension']['exclude_rows'] = [2]
        with self.assertRaisesRegex(ValueError, 'Unmatched lookup') as caught:
            self.run_plan()
        self.assertEqual(caught.exception.diagnostics['unmatched_left_rows'], 2)

    def test_lookup_type_mismatch_and_null_keys(self):
        self.lookup_plan()
        for value in (1, None):
            Path('dimension.xlsx').unlink()
            self.case.book('dimension.xlsx', [['客户', '机构'], [value, '甲'], ['y', '乙']])
            self.plan['sources']['dimension'].update(sha256=self.case.digest('dimension.xlsx'), range='A1:B3')
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'key types|Missing source keys'):
                self.run_plan()

    def test_lookup_relationship_budget_alias_and_numeric_dimension_refused(self):
        plan = copy.deepcopy(self.lookup_plan())
        variants = [({'relationship': 'many_to_many'}, 'relationship'), ({'max_rows': 2}, 'max_rows'),
                    ({'columns': [{'column': 'B', 'as': '金额'}]}, 'collides')]
        for changes, message in variants:
            self.plan = copy.deepcopy(plan)
            self.plan['lookups'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.run_plan()
        self.plan = copy.deepcopy(plan)
        self.plan['metrics'][0]['measure'] = 'lookup:机构'
        with self.assertRaisesRegex(ValueError, 'attributes'):
            self.run_plan()

    def test_lookup_chain_retains_both_edges(self):
        self.lookup_plan()
        self.case.book('labels.xlsx', [['机构', '标识'], ['甲', '甲'], ['乙', '乙']])
        self.plan['sources']['labels'] = self.source('labels.xlsx', 'A1:B3', ['A'])
        second = {**self.plan['lookups'][0], 'id': 'final', 'left': 'enriched', 'right': 'labels',
                  'left_keys': ['lookup:机构'], 'columns': [{'column': 'B', 'as': '归类'}], 'keys': ['lookup:归类']}
        self.plan['lookups'].append(second)
        self.plan['metrics'][0]['source'] = 'final'
        _, audit = self.run_plan()
        self.assertEqual(audit['source_regions']['final']['lookup_chain'], ['enriched', 'final'])
        self.assertEqual(len(audit['lookups']['final']['row_lineage']), 3)

    def test_lookup_dimension_et_partial_propagates(self):
        self.lookup_plan()
        Path('dimension.et').write_bytes(Path('dimension.xlsx').read_bytes())
        self.plan['sources']['dimension'].update(path='dimension.et', sha256=self.case.digest('dimension.et'))
        with self.assertRaisesRegex(ValueError, 'Partial ET'):
            self.run_plan()
        self.plan['sources']['dimension']['accept_partial'] = True
        _, audit = self.run_plan()
        self.assertEqual(audit['status'], 'partial')
        self.assertEqual(audit['source_regions']['dimension']['source_status'], 'partial')

    def test_equality_and_range_checks_pass(self):
        self.quality_check()
        self.plan['checks'].append({'id': 'range', 'kind': 'range', 'left': 'current', 'scope': 'each_target',
                                    'minimum': '50', 'maximum': '200', 'basis': 'Inclusive boundaries'})
        _, audit = self.run_plan()
        self.assertTrue(all(item['status'] == 'passed' for item in audit['checks']))
        self.assertEqual(audit['checks'][0]['observation_count'], 2)

    def test_abs_and_relative_tolerance_use_right_operand_and_inclusive_limit(self):
        self.plan['derived'].append({'id': 'control', 'formula': 'A1+1', 'bindings': {'A1': 'current'},
                                     'unit': '元', 'period': '2026-10', 'basis': 'Synthetic shifted control'})
        check = self.quality_check(right='control', absolute_tolerance='1')
        self.run_plan()
        check.update(absolute_tolerance='0', relative_tolerance='0.02')
        _, audit = self.run_plan()
        self.assertEqual(audit['checks'][0]['observations'][1]['tolerance'], Decimal('1.02'))
        check.update(relative_tolerance='0.019')
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.run_plan()

    def test_reconciliation_rejects_before_output_or_marks_explicit_partial(self):
        self.plan['derived'].append({'id': 'control', 'formula': 'A1+1', 'bindings': {'A1': 'current'},
                                     'unit': '元', 'period': '2026-10', 'basis': 'Deliberate mismatch'})
        check = self.quality_check(right='control')
        with self.assertRaisesRegex(ValueError, 'reconciliation') as caught:
            Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
        self.assertFalse(Path('filled.xlsx').exists())
        self.assertEqual(caught.exception.diagnostics['checks'][0]['failed_count'], 2)
        check['on_failure'] = 'partial'
        result = Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
        self.assertEqual(result['status'], 'partial')

    def test_total_scopes_include_only_the_declared_population(self):
        self.case.replace_source([['机构', '金额'], ['甲', 200], ['乙', 50], ['extra', 10]])
        self.quality_check(scope='source_total')
        _, audit = self.run_plan()
        self.assertEqual(audit['checks'][0]['observations'][0]['left'], Decimal(260))
        self.plan['checks'][0]['scope'] = 'target_total'
        _, audit = self.run_plan()
        self.assertEqual(audit['checks'][0]['observations'][0]['left'], Decimal(250))

    def test_tolerance_unknown_fields_units_and_periods_refused(self):
        for changes in ({'absolute_tolerance': '-1'}, {'relative_tolerance': 'NaN'}, {'relative_tolerance': '1.1'},
                        {'absolute_tolerance': True}, {'typo': 1}, {'right': 'prior'}, {'scope': 'all'}):
            self.quality_check(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_plan()

    def test_period_coverage_by_entity_and_partial_policy(self):
        self.case.replace_source([['机构', '金额', '月份'], ['甲', 120, '2026-09'], ['甲', 80, '2026-10'], ['乙', 50, '2026-10']])
        self.plan['sources']['current'].update(range='A1:C4', period_check={'column': 'C', 'expected': ['2026-09', '2026-10'], 'missing': 'reject'})
        with self.assertRaisesRegex(ValueError, 'periods are missing'):
            self.run_plan()
        self.plan['sources']['current']['period_check']['missing'] = 'partial'
        _, audit = self.run_plan()
        self.assertEqual(audit['status'], 'partial')
        coverage = audit['source_regions']['current']['period_coverage']
        self.assertEqual(coverage['missing_entity_periods'][0]['periods'], ['2026-09'])
        self.assertEqual(coverage['entity_count'], 2)

    def test_period_unexpected_blank_numeric_and_invalid_expected_refused(self):
        for value in ('2025-10', None, 45931):
            self.case.replace_source([['机构', '金额', '月份'], ['甲', 200, value], ['乙', 50, '2026-10']])
            self.plan['sources']['current'].update(range='A1:C3', period_check={'column': 'C', 'expected': ['2026-10'], 'missing': 'partial'})
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'period'):
                self.run_plan()
        self.plan['sources']['current']['period_check']['expected'] = ['2026-13']
        with self.assertRaisesRegex(ValueError, 'YYYY-MM'):
            self.run_plan()

    def test_blank_group_policy_and_downstream_missing_propagation(self):
        self.case.replace_source([['机构', '金额'], ['甲', 200], ['甲', None], ['乙', 50]])
        self.plan['metrics'][0]['missing'] = 'blank'
        with self.assertRaisesRegex(ValueError, 'Missing input'):
            self.run_plan()
        for metric in self.plan['derived']:
            metric['missing'] = 'blank'
        candidate, audit = self.run_plan()
        self.assertIsNone(candidate.cells['Data']['B2']['value'])
        self.assertIsNone(candidate.cells['Data']['C2']['value'])
        self.assertEqual(candidate.cells['Data']['B3']['value'], Decimal(50))
        self.assertEqual(audit['status'], 'partial')

    def test_missing_target_and_empty_selection_explicit_blank(self):
        self.only_current()
        self.case.replace_source([['机构', '金额'], ['甲', 200]])
        self.plan['metrics'][0]['missing_target'] = 'blank'
        candidate, _ = self.run_plan()
        self.assertIsNone(candidate.cells['Data']['B3']['value'])
        self.plan['sources']['current']['filters'] = [{'column': 'B', 'op': 'gt', 'value': 1000}]
        candidate, audit = self.run_plan()
        self.assertTrue(all(item['value'] is None for item in audit['writes']))
        self.assertEqual(audit['status'], 'partial')

    def test_blank_inputs_cannot_feed_template_formulas(self):
        self.only_current()
        self.case.replace_source([['机构', '金额'], ['甲', None], ['乙', 50]])
        self.plan['metrics'][0]['missing'] = 'blank'
        Path('target.xlsx').unlink()
        self.case.book('target.xlsx', [['机构', '余额'], ['甲'], ['乙']], {'F1': {'formula': 'SUM(B2:B3)'}})
        self.plan['target']['sha256'] = self.case.digest('target.xlsx')
        with self.assertRaisesRegex(ValueError, 'formula-free'):
            self.run_plan()

    def test_ratio_zero_and_missing_are_separate_policies(self):
        self.plan['derived'] = [{'id': 'ratio', 'operation': 'ratio', 'numerator': 'current', 'denominator': 'prior',
                                 'unit': '%', 'period': '2026-10/09', 'basis': 'Current divided by prior', 'scale': '100'}]
        self.plan['target']['columns'] = [{'column': 'B', 'metric': 'ratio', 'unit': '%', 'period': '2026-10/09', 'decimals': 2}]
        self.case.replace_source([['机构', '金额'], ['甲', 0], ['乙', -100]], 'prior')
        with self.assertRaisesRegex(ValueError, 'zero_denominator'):
            self.run_plan()
        self.plan['derived'][0]['zero_denominator'] = 'blank'
        candidate, audit = self.run_plan()
        self.assertIsNone(candidate.cells['Data']['B2']['value'])
        self.assertEqual(candidate.cells['Data']['B3']['value'], Decimal(-50))
        self.assertEqual(audit['status'], 'partial')
        self.case.replace_source([['机构', '金额'], ['乙', -100]], 'prior')
        self.plan['metrics'][1]['missing_target'] = 'blank'
        with self.assertRaisesRegex(ValueError, 'Ratio missing'):
            self.run_plan()
        self.plan['derived'][0]['missing'] = 'blank'
        self.run_plan()

    def test_missing_check_operand_is_unassessed_not_zero_or_pass(self):
        self.only_current()
        self.case.replace_source([['机构', '金额'], ['甲', None], ['乙', 50]])
        self.plan['metrics'][0]['missing'] = 'blank'
        check = self.quality_check()
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.run_plan()
        check['missing'] = 'partial'
        _, audit = self.run_plan()
        self.assertEqual(audit['checks'][0]['unassessed_count'], 1)
        self.assertEqual(audit['checks'][0]['observations'][0]['status'], 'unassessed')

    def test_quality_plan_roundtrip_and_summary_stays_bounded(self):
        self.lookup_plan()
        self.quality_check()
        engine = Workflow(self.api)
        _, audit = engine.prepare(self.plan)
        restored = json.loads(encode(audit), parse_float=Decimal)['plan']
        candidate, again = Workflow(self.api).prepare(restored)
        self.assertEqual(candidate.cells['Data']['B2']['value'], Decimal(200))
        self.assertEqual(again['checks'], audit['checks'])
        self.assertLess(len(encode(engine.receipt(audit))), 10000)

    def test_cli_reports_checks_and_lookup_without_printing_full_rows(self):
        self.lookup_plan()
        self.quality_check()
        command = [sys.executable, '-I', '-S', '-B', str(fixtures.WORKFLOW_ROOT / 'scripts/table_workflow.py'),
                   'apply', '--xlsx-root', str(fixtures.XLSX_ROOT), '--output', 'filled.xlsx', '--audit', 'audit.json']
        child = subprocess.run(command, input=encode(self.plan), text=True, capture_output=True, timeout=60)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(child.stderr, '')
        result = json.loads(child.stdout)
        self.assertEqual((result['lookup_count'], result['check_count'], result['checks_failed']), (1, 1, 0))
        self.assertNotIn('row_lineage', child.stdout)
        self.plan['checks'][0].update(kind='range', minimum='0', maximum='100', on_failure='partial')
        self.plan['checks'][0].pop('right')
        command[5] = 'plan'
        child = subprocess.run(command[:8], input=encode(self.plan), text=True, capture_output=True, timeout=60)
        self.assertEqual(child.returncode, 3, child.stdout + child.stderr)
        self.assertEqual(json.loads(child.stdout)['checks_failed'], 1)
        self.plan['checks'][0]['on_failure'] = 'reject'
        child = subprocess.run(command[:8], input=encode(self.plan), text=True, capture_output=True, timeout=60)
        self.assertEqual(child.returncode, 1, child.stdout + child.stderr)
        failure = json.loads(child.stdout)
        self.assertEqual(failure['diagnostics']['checks'][0]['failed_count'], 1)
        self.assertFalse(failure['output_written'])

    def test_composite_lookup_keys_and_source_total_blank_domain(self):
        self.lookup_plan()
        self.plan['lookups'][0]['left_keys'] = ['C', 'C']
        with self.assertRaisesRegex(ValueError, 'Duplicate lookup key'):
            self.run_plan()
        self.plan['lookups'][0]['left_keys'] = ['C', 'A']
        with self.assertRaisesRegex(ValueError, 'arity'):
            self.run_plan()
        self.plan = self.case.base_plan()
        self.plan['sources']['current'].update(keys=['A'], range='A1:C4')
        self.only_current()
        self.plan['metrics'][0]['measure'] = 'B'
        self.plan['metrics'][0]['missing_target'] = 'blank'
        self.quality_check(scope='source_total')
        _, audit = self.run_plan()
        self.assertEqual(audit['checks'][0]['observations'][0]['left'], Decimal(250))
        self.assertEqual(audit['checks'][0]['status'], 'passed')
        self.assertEqual(audit['status'], 'partial')

    def test_composite_lookup_success_distinguishes_same_customer_accounts(self):
        self.lookup_plan()
        Path('dimension.xlsx').unlink()
        self.case.book('dimension.xlsx', [['客户', '机构', '账户'], ['x', '甲', 'a'], ['x', '甲', 'b'], ['y', '乙', 'c']])
        self.plan['sources']['dimension'].update(range='A1:C4', keys=['A', 'C'], sha256=self.case.digest('dimension.xlsx'))
        self.plan['lookups'][0].update(left_keys=['C', 'A'], right_keys=['A', 'C'], relationship='one_to_one')
        candidate, audit = self.run_plan()
        self.assertEqual(candidate.cells['Data']['B2']['value'], Decimal(200))
        self.assertEqual([item['right_row'] for item in audit['lookups']['enriched']['row_lineage']], [2, 3, 4])

    def test_lookup_api_preserves_inputs_and_requires_header_schema(self):
        self.lookup_plan()
        engine = Workflow(self.api)
        left, _ = engine.source_table('current', self.plan['sources']['current'])
        right, _ = engine.source_table('dimension', self.plan['sources']['dimension'])
        before = copy.deepcopy((left, right))
        from xlsx_lookup import lookup_regions
        config = {key: self.plan['lookups'][0][key] for key in ('left_keys', 'right_keys', 'columns', 'relationship', 'max_rows')}
        lookup_regions(left, right, config)
        self.assertEqual((left, right), before)
        left[3]['header_schema'] = None
        with self.assertRaisesRegex(ValueError, 'header schemas'):
            lookup_regions(left, right, config)

    def test_real_public_source_with_synthetic_dimension_and_independent_fraction_control(self):
        root = fixtures.REPO / 'tests/xlsx_real_business_20261103'
        source = root / 'denham_bank_reconciliation.xlsx'
        expected_hash = '3d2177f4b1e51b6c86b2621a77975469bea13198f6d7a410ec5cf7b587679d5c'
        self.assertEqual(self.case.digest(source), expected_hash)
        spec = importlib.util.spec_from_file_location('real_business_oracle', root / 'verify_real_business.py')
        oracle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(oracle)
        raw = oracle.raw_read(source)['Sheet1']
        payees = sorted({raw['F' + str(row)]['value'] for row in range(23, 51)})
        # Synthetic classification is an explicit test rule, not publisher business semantics.
        categories = {payee: ('Group A' if index % 2 == 0 else 'Group B') for index, payee in enumerate(payees)}
        totals = {name: sum((raw['B' + str(row)]['value'] for row in range(23, 51)
                             if categories[raw['F' + str(row)]['value']] == name), Fraction(0)) for name in ('Group A', 'Group B')}
        self.assertEqual(oracle.cents(sum(totals.values())), 549573)
        shutil.copyfile(source, 'published.xlsx')
        self.case.book('labels.xlsx', [['Payee', 'Category']] + [[payee, categories[payee]] for payee in payees])
        self.case.book('control.xlsx', [['Category', 'Amount']] + [[name, Decimal(oracle.decimal_text(amount))] for name, amount in totals.items()])
        Path('target.xlsx').unlink()
        self.case.book('target.xlsx', [['Category', 'Amount'], ['Group A'], ['Group B']])
        source_spec = self.source('published.xlsx', 'B20:G50', ['F'], 'aggregate')
        source_spec.update(sheet='Sheet1', header_rows='20', exclude_rows=[21, 22], formulas='calculate', unit='GBP', period='2018/19')
        labels = self.source('labels.xlsx', 'A1:B' + str(len(payees) + 1), ['A'])
        control = self.source('control.xlsx', 'A1:B3', ['A'])
        control.update(unit='GBP', period='2018/19')
        self.plan = {'schema_version': 'table_workflow_plan_v1', 'sources': {'facts': source_spec, 'labels': labels, 'control': control},
                     'lookups': [{'id': 'classified', 'left': 'facts', 'right': 'labels', 'left_keys': ['F'], 'right_keys': ['A'],
                                  'relationship': 'many_to_one', 'columns': [{'column': 'B', 'as': 'Category'}], 'keys': ['lookup:Category'],
                                  'repeated_keys': 'aggregate', 'max_rows': 28, 'basis': 'Synthetic alternating classification'}],
                     'metrics': [{'id': name, 'source': table, 'operation': 'sum', 'measure': 'B', 'basis': 'Declared category total'}
                                 for name, table in [('actual', 'classified'), ('oracle', 'control')]],
                     'checks': [{'id': 'real-control', 'kind': 'equal', 'left': 'actual', 'right': 'oracle', 'scope': 'each_target',
                                 'absolute_tolerance': '0', 'basis': 'Independent OOXML/Fraction calculation of published literals'}],
                     'target': {'path': 'target.xlsx', 'sha256': self.case.digest('target.xlsx'), 'sheet': 'Data', 'range': 'A1:B3',
                                'header_rows': '1', 'keys': ['A'], 'columns': [{'column': 'B', 'metric': 'actual', 'unit': 'GBP', 'period': '2018/19', 'decimals': 2}]}}
        result = Workflow(self.api).apply(self.plan, 'filled.xlsx', 'audit.json')
        self.assertEqual(result['status'], 'validated')
        book = self.api.xlsx.Workbook.load('filled.xlsx')
        for row, name in enumerate(('Group A', 'Group B'), 2):
            self.assertEqual(Fraction(book.cells['Data']['B' + str(row)]['value']), Fraction(oracle.cents(totals[name]), 100))
        self.assertEqual(self.case.digest(source), expected_hash)
        self.assertEqual(self.case.digest('published.xlsx'), expected_hash)
        audit = json.loads(Path('audit.json').read_text(encoding='utf-8'))
        self.real_evidence = {
            'file': source.name, 'sha256': expected_hash, 'public_original_unchanged': True,
            'business_period': '2018/19 from workbook; URL filename says 2020',
            'source_rows': 28, 'source_selection': 'Sheet1!B20:G50; header20; exclude21,22',
            'synthetic_components': ['alternating payee classification', 'Fraction-derived control table', 'target template'],
            'independent_reader': 'existing raw_read OOXML parser and Fraction; no formula caches as expected/input',
            'independent_total_rounded_to_cents': '5495.73',
            'raw_total_fraction': str(sum(totals.values())),
            'expected_output': {name: str(Decimal(oracle.cents(amount)) / 100) for name, amount in totals.items()},
            'actual_output': {name: str(book.cells['Data']['B' + str(row)]['value']) for row, name in enumerate(('Group A', 'Group B'), 2)},
            'status': result['status'], 'checks': [{key: entry[key] for key in ('id', 'status', 'failed_count', 'unassessed_count')}
                                                for entry in audit['checks']],
            'fact_conservation': audit['lookups']['classified']['fact_conservation'],
            'formula_calculation': audit['source_regions']['facts']['formula_calculation']}

    def test_real_unsupported_formula_source_remains_blocked(self):
        source = fixtures.REPO / 'tests/xlsx_real_business_20261103/denham_cashbook.xlsx'
        shutil.copyfile(source, 'unsupported.xlsx')
        book = self.api.xlsx.Workbook.load('unsupported.xlsx')
        self.plan['sources']['current'].update(path='unsupported.xlsx', sha256=self.case.digest('unsupported.xlsx'),
                                                sheet=next(iter(book.cells)), range='A1:B4', formulas='calculate')
        with self.assertRaisesRegex(ValueError, 'recalculation'):
            self.run_plan()


if __name__ == '__main__':
    unittest.main()
