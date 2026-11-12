"""Canonical-JSON oracle for incremental budgets, independent of size arithmetic."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
IS_COPY_WORKER = '--copy-worker' in sys.argv
if IS_COPY_WORKER:
    sys.path.insert(0, str(ROOT / 'skills/xlsx/scripts'))
    # Each copy runs in its own isolated interpreter; never share same-name imports.
    COPY = sys.argv.pop()
    assert COPY in ('wps', 'et')
    sys.path.insert(0, str(ROOT / 'skills' / COPY / 'scripts'))
    sys.path.insert(0, str(ROOT / 'skills/et/scripts/test'))
    import xlsx_read_view
    import wps_read_view
    from stdlib_xlsx import Workbook
    import wps_access
    from wps_access import ParseError
    assert Path(wps_read_view.__file__).resolve() == ROOT / "skills" / COPY / "scripts/wps_read_view.py"
    assert Path(wps_access.__file__).resolve() == ROOT / "skills" / COPY / "scripts/wps_access.py"

def canonical(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False, separators=(',', ':'))


class BudgetOracleTests(unittest.TestCase):
    def inputs(self):
        text = '中😀"\\\n\t' + ''.join(chr(i) for i in range(32))
        book = Workbook.create({'sheets': [{'name': 'Data', 'cells': {f'A{i}': {'value': 'safe'} for i in range(1, 21)}}]})
        # Projection layer may see strings with JSON escapes; no claim these control
        # characters are valid new OOXML source text. No parser validation is bypassed.
        for index, cell in enumerate(book.cells['Data'].values()):
            cell['value'] = text * (1 + index % 3)
        source = {'status': 'partial', 'partial': True, 'issues': [{'code': 'synthetic', 'message': 'not a full format claim'}],
                  'reading_assessment': {'completeness': 'partial'},
                  'sheets': [{'name': 'Data', 'cells': {f'A{i}': {'type': 'text', 'value': text * (1 + i % 3)} for i in range(1, 21)}}]}
        return book, source

    def check_oracle(self, skill):
        book, source = self.inputs()
        def invoke(budget, cursor=None):
            if skill == 'xlsx':
                return xlsx_read_view.read_view(book, b'input bytes', page_size=100, max_chars=budget, cursor=cursor)
            return wps_read_view.project(source, b'input bytes', {}, page_size=100, max_chars=budget, cursor=cursor)
        whole = invoke(2000000)
        all_items = whole['items']
        binding = hashlib.sha256(canonical(whole['scope']).encode()).hexdigest()
        def candidate(count):
            value = copy.deepcopy(whole)
            value.update(items=all_items[:count], returned_items=count, truncated=count < len(all_items), next_cursor=None)
            if 'has_more' in value:
                value['has_more'] = value['truncated']
            if value['truncated']:
                key = 'sha256' if skill == 'xlsx' else 'source'
                token = {key: whole['source_sha256'], 'scope': binding, 'next': count, 'revision': whole['projection_revision']}
                value['next_cursor'] = base64.urlsafe_b64encode(canonical(token).encode()).decode()
            return value
        # Test exact boundaries and adjacent budgets, including counter digit changes
        # and the final cursor disappearing. The oracle serializes complete candidates.
        budgets = {512, 700, 1000, 1500, 2500, 5000, 10000}
        for count in (0, 1, 2, 9, 10, 19, 20):
            size = len(canonical(candidate(count))) + 1
            budgets.update((size - 1, size, size + 1))
        for budget in sorted(x for x in budgets if x >= 512):
            expected = None
            if skill != 'xlsx' and len(canonical(candidate(len(all_items)))) + 1 <= budget:
                # A final page drops its cursor and may fit although an earlier
                # prefix does not. Check it independently, before prefix fitting.
                expected = candidate(len(all_items))
            elif len(canonical(candidate(0))) + 1 <= budget:
                for count in range(1, len(all_items) + 1):
                    value = candidate(count)
                    if len(canonical(value)) + 1 > budget:
                        break
                    expected = value
            with self.subTest(skill=skill, budget=budget):
                if expected is None:
                    with self.assertRaises((ValueError, ParseError)):
                        invoke(budget)
                else:
                    actual = invoke(budget)
                    self.assertEqual(actual, expected)
                    self.assertLessEqual(len(canonical(actual)) + 1, budget)
                    self.assertEqual(json.loads(canonical(actual))['returned_items'], len(actual['items']))
        self.assertGreater(len(canonical(whole).encode('utf-8')), len(canonical(whole)))

    def test_xlsx_matches_canonical_budget_oracle(self):
        self.check_oracle('xlsx')

    def test_wps_matches_canonical_budget_oracle_and_keeps_partial(self):
        self.check_oracle('wps')

    def test_legacy_and_future_cursor_revisions_are_refused(self):
        book, source = self.inputs()
        for skill in ('xlsx', 'wps'):
            def invoke(cursor=None):
                if skill == 'xlsx':
                    return xlsx_read_view.read_view(book, b'input bytes', page_size=1, cursor=cursor)
                return wps_read_view.project(source, b'input bytes', {}, page_size=1, cursor=cursor)
            first = invoke()
            token = json.loads(base64.urlsafe_b64decode(first['next_cursor']))
            for change in ('remove', 'future', 'float', 'bool'):
                altered = dict(token)
                if change == 'remove':
                    del altered['revision']
                elif change == 'future':
                    altered['revision'] += 1
                elif change == 'float':
                    altered['revision'] = float(altered['revision'])
                else:
                    altered['revision'] = True
                cursor = base64.urlsafe_b64encode(canonical(altered).encode()).decode()
                with self.subTest(skill=skill, change=change), self.assertRaises((ValueError, ParseError)):
                    invoke(cursor)
            self.assertEqual(invoke(first['next_cursor'])['start'], 1)

    def test_cli_rejects_legacy_cursor_as_read_failure_with_json(self):
        with tempfile.TemporaryDirectory(prefix='read-view-cursor-') as directory:
            task = Path(directory)
            previous = Path.cwd()
            try:
                os.chdir(task)
                Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 1}, 'A2': {'value': 2}}}]}).save('data.xlsx')
            finally:
                os.chdir(previous)
            # Synthetic OOXML exercises ET's cursor protocol without importing
            # ET's BIFF modules into the isolated WPS runtime namespace.
            # A changed suffix is not counted as real WPS/ET format acceptance.
            (task / 'data.et').write_bytes((task / 'data.xlsx').read_bytes())
            for skill, script, name in [('xlsx', 'stdlib_xlsx.py', 'data.xlsx'), ('et', 'read_et.py', 'data.et')]:
                command = [sys.executable, '-I', '-S', '-B', str(ROOT / 'skills' / skill / 'scripts' / script)]
                if skill == 'xlsx':
                    command.append('read')
                command += ['./' + name, '--page-size', '1']
                first = subprocess.run(command, cwd=task, capture_output=True, text=True, encoding='utf-8')
                self.assertIn(first.returncode, (0, 3))
                token = json.loads(base64.urlsafe_b64decode(json.loads(first.stdout)['next_cursor']))
                del token['revision']
                legacy = base64.urlsafe_b64encode(canonical(token).encode()).decode()
                rejected = subprocess.run(command + ['--cursor', legacy], cwd=task, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(rejected.returncode, 1)
                result = json.loads(rejected.stdout)
                self.assertIn('legacy cursor', result['message'])
                self.assertNotIn('items', result)

    def test_real_wps_text_cli_budget_and_continuation(self):
        with tempfile.TemporaryDirectory(prefix='wps-text-budget-') as directory:
            task = Path(directory)
            text = 'Hello bounded reading. ' * 40
            (task / 'input.wps').write_text('{\\rtf1\\ansi ' + text + '}', encoding='ascii')
            command = [sys.executable, '-I', '-S', '-B', str(ROOT / 'skills/wps/scripts/read_wps.py'), './input.wps']
            full = subprocess.run(command, cwd=task, capture_output=True, text=True, check=True)
            expected = json.loads(full.stdout)['text']
            parts = []
            cursor = None
            while True:
                args = command + ['--page-size', '31', '--max-output-chars', '12000']
                if cursor:
                    args += ['--cursor', cursor]
                result = subprocess.run(args, cwd=task, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertLessEqual(len(result.stdout), 12000)
                value = json.loads(result.stdout)
                parts.append(value['text'])
                cursor = value['next_cursor']
                if cursor is None:
                    break
            self.assertEqual(''.join(parts), expected)
            rejected = subprocess.run(command + ['--cursor', 'invalid'], cwd=task, capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stdout)['status'], 'output_view_error')

    def test_wps_summary_does_not_sort_and_keeps_diagnostics(self):
        _, source = self.inputs()
        with patch.object(wps_read_view, 'position', side_effect=AssertionError('Summary sorted cell coordinates')):
            summary = wps_read_view.project(source, b'input bytes', {}, summary=True)
        self.assertEqual(summary['total_items'], 20)
        self.assertEqual(summary['issues'], source['issues'])
        self.assertEqual(summary['status'], 'partial')
        self.assertNotIn('items', summary)

    def test_wps_large_text_and_diagnostics_still_respect_actual_wire_budget(self):
        source = {'status': 'partial', 'issues': [{'code': 'synthetic', 'message': '中' * 300}], 'text': '😀\n"\\' * 100}
        cursor = None
        parts = []
        while True:
            result = wps_read_view.project(source, b'text bytes', {}, cursor=cursor, page_size=100, max_chars=1200)
            self.assertLessEqual(len(canonical(result)) + 1, 1200)
            self.assertEqual(result['issues'], source['issues'])
            parts.append(result['text'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        self.assertEqual(''.join(parts), source['text'])


class IsolatedCopyTests(unittest.TestCase):
    """Repository maintenance wrapper; both discovery and direct runs isolate imports."""

    def run_copy(self, selected):
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', __file__, '--copy-worker', selected],
            capture_output=True, text=True, encoding='utf-8',
        )
        print('Isolated copy: ' + selected + '\n' + result.stdout + result.stderr, flush=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wps_copy(self):
        self.run_copy('wps')

    def test_et_copy(self):
        self.run_copy('et')


def load_tests(loader, tests, pattern):
    # Do not instantiate the oracle in the parent or wrappers in a child.
    target = BudgetOracleTests if IS_COPY_WORKER else IsolatedCopyTests
    return loader.loadTestsFromTestCase(target)


if __name__ == '__main__':
    if IS_COPY_WORKER:
        sys.argv.remove('--copy-worker')
    unittest.main()
