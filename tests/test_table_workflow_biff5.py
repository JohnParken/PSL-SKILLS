"""BIFF5 crosses public ET/XLSX capabilities; synthetic data only."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/table-workflow/scripts'),
               str(ROOT / 'skills/et/scripts/test')]
from workflow_runtime import Dependencies
from table_workflow import Workflow, WorkflowError
from test_wps_biff5 import workbook, label, number, formula, synthetic_cfb


class Biff5WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biff5-workflow-')
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, previous)
        self.api = Dependencies(str(ROOT / 'skills/xlsx'), str(ROOT / 'skills/et'))
        self.addCleanup(self.api.close)

    def source(self, value=None, codepages=(1252,)):
        cells = label(b'Key', 0, 0) + label(b'Amount', 0, 1) + label(b'0001', 1, 0)
        cells += number(12.5, 1, 1) if value is None else value
        raw = synthetic_cfb({'Book': workbook(cells, codepages=codepages)})
        Path('source.et').write_bytes(raw)
        spec = {'path': 'source.et', 'sha256': hashlib.sha256(raw).hexdigest(),
                'sheet': 'S', 'range': 'A1:B2', 'header_rows': '1', 'keys': ['A'],
                'unit': 'units', 'period': '2026-11', 'repeated_keys': 'reject'}
        return raw, spec

    def test_names_and_complete_partial_source_use_distinct_schema(self):
        raw, spec = self.source()
        meta = self.api.metadata('source.et', raw)
        self.assertEqual(meta['input_schema'], 'biff5_readonly_v1')
        self.assertEqual(meta['sheet_names'], ['S'])
        self.assertEqual(meta['source_status'], 'partial')
        flow = Workflow(self.api)
        with self.assertRaises(WorkflowError):
            flow.source_table('source', spec)
        table, keys = flow.source_table('source', {**spec, 'accept_partial': True})
        self.assertEqual(table[2][0][1][0], ('text', '0001'))
        self.assertEqual(str(table[2][0][1][1][1]), '12.5')
        self.assertEqual(table[3]['input_schema'], 'biff5_readonly_v1')
        self.assertEqual(flow.warnings[-1]['accepted_scope'], 'declared_cells')
        self.assertEqual(Path('source.et').read_bytes(), raw)

    def test_unknown_coverage_cannot_be_accepted(self):
        _, spec = self.source(codepages=())
        with self.assertRaisesRegex(WorkflowError, 'declared cell coverage'):
            Workflow(self.api).source_table('source', {**spec, 'accept_partial': True})

    def test_cached_formula_requires_explicit_opt_in(self):
        _, spec = self.source(formula(struct.pack('<d', 9.25), 1, 1))
        with self.assertRaises(ValueError):
            Workflow(self.api).source_table('source', {**spec, 'accept_partial': True})
        table, _ = Workflow(self.api).source_table(
            'source', {**spec, 'accept_partial': True, 'formulas': 'cached'})
        self.assertEqual(str(table[2][0][1][1][1]), '9.25')

    def test_missing_formula_cache_not_unlocked_by_partial_acceptance(self):
        _, spec = self.source(formula(b'\x00\x00\x00\x00\x00\x00\xff\xff', 1, 1))
        with self.assertRaises(ValueError):
            Workflow(self.api).source_table('source', {**spec, 'accept_partial': True, 'formulas': 'cached'})

    def test_error_formula_cache_is_not_used(self):
        _, spec = self.source(formula(b'\x02\x00\x07\x00\x00\x00\xff\xff', 1, 1))
        with self.assertRaises(ValueError):
            Workflow(self.api).source_table('source', {**spec, 'accept_partial': True, 'formulas': 'cached'})

    def test_old_provider_without_schema_capability_fails_closed(self):
        raw, _ = self.source()
        self.api.checks['xlsx']['interface'].pop('extraction_schemas')
        with self.assertRaisesRegex(ValueError, 'does not declare BIFF5'):
            self.api.load('source.et', raw)

    def test_full_plan_keeps_partial_schema_audit_and_template(self):
        raw, source = self.source()
        self.api.xlsx.Workbook.create({'sheets': [{'name': 'Target', 'cells': {
            'A1': {'value': 'Key'}, 'B1': {'value': 'Amount'}, 'A2': {'value': '0001'}}}]}).save('target.xlsx')
        target = Path('target.xlsx').read_bytes()
        plan = {'schema_version': 'table_workflow_plan_v1',
                'sources': {'old': {**source, 'accept_partial': True}},
                'target': {'path': 'target.xlsx', 'sha256': hashlib.sha256(target).hexdigest(),
                           'sheet': 'Target', 'range': 'A1:B2', 'header_rows': '1', 'keys': ['A'],
                           'columns': [{'column': 'B', 'metric': 'total', 'unit': 'units',
                                        'period': '2026-11', 'decimals': 2}]},
                'metrics': [{'id': 'total', 'source': 'old', 'operation': 'sum', 'measure': 'B',
                             'basis': 'sum saved amount'}]}
        _, audit = Workflow(self.api).prepare(plan)
        self.assertEqual(audit['status'], 'partial')
        self.assertEqual(str(audit['writes'][0]['value']), '12.50')
        self.assertEqual(audit['source_regions']['old']['input_schema'], 'biff5_readonly_v1')
        self.assertEqual(Path('source.et').read_bytes(), raw)
        self.assertEqual(Path('target.xlsx').read_bytes(), target)

    def test_independent_provider_copies_consume_biff5(self):
        import shutil
        raw, source = self.source()
        root = Path('providers').resolve()
        for name in ('xlsx', 'et', 'table-workflow'):
            manifest = json.loads((ROOT / 'packaging/runtime-manifests' / (name + '.json')).read_text(encoding='utf-8'))
            for relative in manifest['files']:
                target = root / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / 'skills' / name / relative, target)
                target.chmod(0o444)
        api = Dependencies(str(root / 'xlsx'), str(root / 'et'))
        try:
            metadata = api.metadata('source.et', raw)
            self.assertEqual(metadata['input_schema'], 'biff5_readonly_v1')
            table, _ = Workflow(api).source_table('old', {**source, 'accept_partial': True})
            self.assertEqual(str(table[2][0][1][1][1]), '12.5')
        finally:
            api.close()

    def test_malformed_schema_capabilities_are_rejected(self):
        raw, _ = self.source()
        for schemas in ('not-biff5_readonly_v1', {'biff5_readonly_v1': False}, None,
                        ('biff5_readonly_v1',), [False], 1):
            with self.subTest(schemas=schemas):
                self.api.checks['xlsx']['interface']['extraction_schemas'] = schemas
                with self.assertRaisesRegex(ValueError, 'Invalid XLSX extraction_schemas'):
                    self.api.load('source.et', raw)

    def test_public_region_rejects_out_of_legacy_grid(self):
        raw, _ = self.source()
        book = self.api.load('source.et', raw)
        for cell_range in ('IW1', 'A16385', 'IV16384:IW16384', 'A16384:A16385'):
            with self.subTest(cell_range=cell_range):
                with self.assertRaisesRegex(ValueError, 'BIFF5 analysis range'):
                    self.api.analysis.region_from_book(book, 'S', cell_range, header=False)


if __name__ == '__main__':
    unittest.main()
