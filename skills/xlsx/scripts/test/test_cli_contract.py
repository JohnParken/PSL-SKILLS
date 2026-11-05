"""Public CLI compatibility, typed transport and independent template verification."""
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from xlsx_cli_protocol import PROTOCOL, dumps, loads
from stdlib_xlsx import Workbook
from formula_engine import FormulaError
from xlsx_cli import Session, capabilities, verify


class ContractTests(unittest.TestCase):
    def test_capabilities_and_documented_operations_match(self):
        doc = (SCRIPTS/'docs'/'cli-contract.md').read_text(encoding='utf-8')
        self.assertEqual(capabilities()['protocol'], PROTOCOL)
        for operation in capabilities()['operations']:
            self.assertIn(operation, doc)

    def test_document_wire_example_is_executable(self):
        import re
        doc = (SCRIPTS/'docs'/'cli-contract.md').read_text(encoding='utf-8')
        raw = re.search(r'```json\n([^`]+)\n```', doc).group(1).encode('utf-8')
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'xlsx_cli.py'), 'call'], input=raw+b'\n', capture_output=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(loads(child.stdout.strip())['result'], (1, 1))

    def test_run_reuses_legacy_success_and_failure_streams(self):
        with tempfile.TemporaryDirectory() as task:
            book = Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A1': {'value': '000123'}, 'B1': {'value': 1}}}]})
            book.save(Path(task)/'source.xlsx')
            for arguments in (['read', 'source.xlsx'], ['read', 'missing.xlsx'], ['unknown-command'], ['--help']):
                old = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'stdlib_xlsx.py'), *arguments],
                                     cwd=task, capture_output=True)
                new = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'xlsx_cli.py'), 'run', *arguments],
                                     cwd=task, capture_output=True)
                self.assertEqual((new.returncode, new.stdout, new.stderr), (old.returncode, old.stdout, old.stderr))

    def test_mutation_failure_invalidates_handle_before_any_publication(self):
        requests = [
            ('create', {'spec': {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 'original'}}}]}}),
            ('patch', {'handle': 0, 'spec': {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 'changed'}}},
                                                       {'name': 'Unknown', 'cells': {'A1': {'value': 'bad'}}}]}}),
            ('publish', {'handle': 0, 'output': 'must-not-exist.xlsx'})]
        raw = b''.join(dumps({'protocol': PROTOCOL, 'id': number, 'operation': operation, 'parameters': parameters})+b'\n'
                       for number, (operation, parameters) in enumerate(requests, 1))
        with tempfile.TemporaryDirectory() as task:
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'xlsx_cli.py'), 'serve'],
                                   input=raw, cwd=task, capture_output=True)
            replies = [loads(line) for line in child.stdout.splitlines()]
            self.assertTrue(replies[0]['ok'])
            self.assertFalse(replies[1]['ok'])
            self.assertFalse(replies[2]['ok'])
            self.assertIn('expired', replies[2]['error']['message'])
            self.assertFalse((Path(task)/'must-not-exist.xlsx').exists())

    def test_verify_rejects_plausible_but_wrong_values_or_non_target_change(self):
        for changes, message in [({'B2': {'value': 999}}, 'Target value differs'),
                                 ({'B2': {'value': 200}, 'A2': {'value': 'Changed'}}, 'Non-target cell changed')]:
            source = Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A2': {'value': '甲'}}}]})
            candidate = Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A2': {'value': '甲'}}}]})
            candidate.patch({'sheets': [{'name': 'Data', 'cells': changes}]})
            with self.assertRaisesRegex(ValueError, message):
                verify(source, candidate, {('Data', 'B2'): Decimal(200)})

    def test_formula_range_and_text_literal_use_explicit_bindings(self):
        session = Session()
        self.assertEqual(session.execute('evaluate', {'formula': 'SUM(A1:C1)', 'sheet': 'Metrics',
                                                      'bindings': {'A1': Decimal(1), 'B1': Decimal(2), 'C1': Decimal(3)}}), Decimal(6))
        self.assertEqual(session.execute('evaluate', {'formula': 'IF("A2"="A2",A1,0)', 'sheet': 'Metrics',
                                                      'bindings': {'A1': Decimal(7)}}), Decimal(7))
        with self.assertRaisesRegex(FormulaError, 'outside declared bindings'):
            session.execute('evaluate', {'formula': 'A1+B1', 'sheet': 'Metrics', 'bindings': {'A1': Decimal(1)}})

    def test_cell_snapshot_cannot_modify_remote_state_or_expose_package(self):
        session = Session()
        view = session.execute('create', {'spec': {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 'Protected'}}}]}})
        # View serialization must not expose mutable backing references even to
        # direct callers of this format-owner API.
        view['cells']['Data']['A1']['value'] = 'local'
        self.assertEqual(session.book(view['handle']).cells['Data']['A1']['value'], 'Protected')
        self.assertNotIn('parts', view)
        self.assertNotIn('sheets', view)
        session.execute('release', {'handle': view['handle']})
        with self.assertRaises(ValueError):
            session.execute('calculate', {'handle': view['handle']})


if __name__ == '__main__':
    unittest.main()
