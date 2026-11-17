"""CLI-chain acceptance, not a model/harness or WPS-rendering acceptance test.

Public tests use only hash-pinned repository public_samples and skip if absent.
Synthetic tests exercise bound cursors and explicit handling of partial exit 3.
"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[2]
PUBLIC = ROOT / 'test_workspace' / 'four_stage_20261026' / 'public_samples'
sys.path[:0] = [str(SCRIPTS), str(Path(__file__).resolve().parent)]
from test_wps_ooxml_sheet import package, T, R


def run_cli(task, script, *arguments):
    """Capture all channels and parse complete JSON even on source partial/3."""
    process = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *arguments],
                             cwd=task, capture_output=True, check=False)
    return process, json.loads(process.stdout)


class ReadChainTests(unittest.TestCase):
    """Use synthetic saved integer values as independent arithmetic expectations."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='et-chain-')
        self.addCleanup(self.directory.cleanup)
        self.task = Path(self.directory.name)
        rows = ''.join(f'<row r="{row}"><c r="A{row}"><v>{row}</v></c></row>' for row in range(1, 21))
        self.raw = package(overrides={'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{T}"><sheetData>{rows}</sheetData></worksheet>'})
        (self.task / 'source.et').write_bytes(self.raw)
        (self.task / 'query.json').write_text('{"limit":2}', encoding='utf-8')

    def table(self, preview, *extra):
        arguments = ['source.et', '--sheet', 'Data', '--range', 'A1:A20', '--query', 'query.json', '--max-output-bytes', '32768']
        if preview:
            arguments.append('--preview')
        return run_cli(self.task, 'analyze_et.py', *arguments, *extra)

    def test_content_preview_analysis_cursors_are_not_interchangeable(self):
        _, content = run_cli(self.task, 'et_cli.py', 'run', 'source.et', '--content-page', '--page-size', '2')
        _, preview = self.table(True)
        _, analysis = self.table(False)
        for value in (content, preview, analysis):
            self.assertTrue(value['has_more'])
            self.assertIsNotNone(value['next_cursor'])
        for is_preview, foreign in ((True, content), (True, analysis), (False, content), (False, preview)):
            process, rejected = self.table(is_preview, '--cursor', foreign['next_cursor'])
            self.assertEqual(process.returncode, 1)
            self.assertEqual(rejected['status'], 'output_view_error')
        for foreign in (preview, analysis):
            process, rejected = run_cli(self.task, 'et_cli.py', 'run', 'source.et', '--content-page', '--page-size', '2', '--cursor', foreign['next_cursor'])
            self.assertEqual(process.returncode, 1)
            self.assertEqual(rejected['status'], 'output_view_error')

    def test_each_cursor_continues_all_rows_and_analysis_remains_global(self):
        cursor = None
        values = []
        for _ in range(20):
            arguments = ['run', 'source.et', '--content-page', '--page-size', '2']
            if cursor is not None:
                arguments.extend(['--cursor', cursor])
            process, result = run_cli(self.task, 'et_cli.py', *arguments)
            self.assertIn(process.returncode, (0, 3))
            values.extend(item['data']['value'] for item in result['items'] if item['kind'] == 'cell')
            if not result['has_more']:
                break
            cursor = result['next_cursor']
        self.assertEqual(values, [str(value) for value in range(1, 21)])
        for is_preview in (True, False):
            cursor = None
            rows = []
            for _ in range(20):
                process, result = self.table(is_preview, *([] if cursor is None else ['--cursor', cursor]))
                self.assertIn(process.returncode, (0, 3))
                self.assertEqual(process.stderr, b'')
                self.assertEqual(result['source_sha256'], hashlib.sha256(self.raw).hexdigest())
                rows.extend(row['source_row'] for row in result['rows'])
                if not is_preview:
                    self.assertEqual(result['profiles']['A']['numeric_count'], 20)
                    self.assertEqual(result['profiles']['A']['sum'], '210')
                if not result['has_more']:
                    break
                cursor = result['next_cursor']
            self.assertEqual(rows, list(range(1, 21)))

    def test_next_arguments_replay_all_modes_without_shell_interpretation(self):
        sheet = '--表 \' " $(touch SENTINEL)'
        escaped = sheet.replace('&', '&amp;').replace('"', '&quot;')
        workbook = (f'<workbook xmlns="{T}" xmlns:r="{R}"><sheets>'
                    f'<sheet name="{escaped}" sheetId="7" r:id="s1"/></sheets></workbook>')
        rows = ''.join(f'<row r="{row}"><c r="A{row}"><v>{row}</v></c></row>' for row in range(1, 21))
        raw = package(overrides={'xl/workbook.xml': workbook,
                                'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{T}"><sheetData>{rows}</sheetData></worksheet>'})
        (self.task / 'source.et').write_bytes(raw)
        query = '--查询 \' " $(touch SENTINEL).json'
        (self.task / query).write_text('{"limit":2}', encoding='utf-8')
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.task.iterdir()}
        common = ['--sheet=' + sheet, '--range', 'A1:A20', '--query=' + query, '--max-output-bytes', '32768']
        cases = [('read_et.py', ['--content-page', '--sheet=' + sheet, '--page-size', '2', '--max-output-bytes', '32768']),
                 ('analyze_et.py', [*common, '--preview']),
                 ('analyze_et.py', [*common, '--header-rows', '1'])]
        for script, initial in cases:
            arguments = initial
            observed = []
            for _ in range(30):
                process, result = run_cli(self.task, script, 'source.et', *arguments)
                self.assertIn(process.returncode, (0, 3), process.stdout)
                self.assertEqual(process.stderr, b'')
                self.assertLessEqual(len(process.stdout), 32768)
                self.assertEqual(result['continuation_entrypoint'], script)
                if script == 'read_et.py':
                    observed.extend(int(item['data']['value']) for item in result['items'] if item['kind'] == 'cell')
                else:
                    observed.extend(row['source_row'] for row in result['rows'])
                    if '--preview' not in initial:
                        self.assertEqual(result['profiles']['A']['sum'], '209')
                        self.assertEqual(result['query']['header_rows'], '1')
                if not result['has_more']:
                    self.assertIsNone(result['next_arguments'])
                    break
                arguments = result['next_arguments']
                self.assertIsInstance(arguments, list)
                self.assertTrue(all(isinstance(arg, str) for arg in arguments))
                self.assertNotIn('source.et', arguments)
                self.assertIn('--sheet=' + sheet, arguments)
                self.assertEqual(arguments[arguments.index('--max-output-bytes') + 1], '32768')
                if script == 'analyze_et.py':
                    self.assertIn('--query=' + query, arguments)
            self.assertEqual(observed, list(range(2 if '--header-rows' in initial else 1, 21)))
        self.assertFalse((self.task / 'SENTINEL').exists())
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.task.iterdir()}
        self.assertEqual(before, after)

    def test_source_change_invalidates_all_three_cursor_families(self):
        _, content = run_cli(self.task, 'et_cli.py', 'run', 'source.et', '--content-page', '--page-size', '2')
        _, preview = self.table(True)
        _, analysis = self.table(False)
        (self.task / 'source.et').write_bytes(package('<c r="A1"><v>999</v></c>'))
        calls = [run_cli(self.task, 'et_cli.py', 'run', 'source.et', '--content-page', '--page-size', '2', '--cursor', content['next_cursor']),
                 self.table(True, '--cursor', preview['next_cursor']), self.table(False, '--cursor', analysis['next_cursor'])]
        for process, result in calls:
            self.assertEqual(process.returncode, 1)
            self.assertEqual(result['status'], 'output_view_error')


class PublicReadChainTests(unittest.TestCase):
    """Frozen original titles/positions and types, not outputs copied from analysis."""

    def public(self, name):
        path = PUBLIC / name
        if not path.is_file():
            self.skipTest('Pinned public original is not available: ' + name)
        manifest = json.loads((ROOT / 'docs/maintenance/et/et-real-samples.json').read_text(encoding='utf-8'))
        expected = next(sample for sample in manifest['samples'] if sample['filename'] == name)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected['sha256'])
        self.addCleanup(lambda: self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected['sha256']))
        process, summary = run_cli(PUBLIC, 'et_cli.py', 'run', name, '--summary')
        self.assertEqual(process.returncode, 3)
        self.assertEqual(process.stderr, b'')
        return summary['sheets'][0]['name'], summary

    def test_balance_summary_preview_then_explicit_analysis(self):
        sheet, summary = self.public('Balance_Sheet.ett')
        self.assertEqual(summary['total_items'], 717)
        process, preview = run_cli(PUBLIC, 'analyze_et.py', 'Balance_Sheet.ett', '--sheet', sheet, '--range', 'A8:C10', '--preview')
        self.assertEqual(process.returncode, 3)
        self.assertEqual([row['cells']['A']['value'] for row in preview['rows']], ['Cash in bank', 'Accounts receivable', 'Inventory'])
        for column in ('B', 'C'):
            self.assertEqual([row['cells'][column]['type'] for row in preview['rows']], ['number', 'blank', 'number'])
        process, analysis = run_cli(PUBLIC, 'analyze_et.py', 'Balance_Sheet.ett', '--sheet', sheet, '--range', 'A8:C10')
        self.assertEqual(process.returncode, 3)
        self.assertEqual(summary['source_sha256'], preview['source_sha256'])
        self.assertEqual(preview['source_sha256'], analysis['source_sha256'])
        for column in ('B', 'C'):
            self.assertEqual(analysis['profiles'][column]['numeric_count'], 2)
            self.assertEqual(analysis['profiles'][column]['sum'], '0')
        self.assertFalse(analysis['formulas_executed'])

    def test_telephone_header_observation_then_blank_analysis(self):
        sheet, summary = self.public('Telephone_List.ett')
        self.assertEqual(summary['total_items'], 3976)
        process, preview = run_cli(PUBLIC, 'analyze_et.py', 'Telephone_List.ett', '--sheet', sheet, '--range', 'A1:G5', '--preview')
        self.assertEqual(process.returncode, 3)
        headers = [preview['rows'][1]['cells'][column]['value'] for column in 'ABCDEFG']
        self.assertEqual(headers, ['Last Name', 'First Name', 'Spouse/Kids', 'Address', 'Home Number', 'Work Number', 'Cell Number'])
        process, analysis = run_cli(PUBLIC, 'analyze_et.py', 'Telephone_List.ett', '--sheet', sheet, '--range', 'A2:G5', '--header-rows', '2')
        self.assertEqual(process.returncode, 3)
        self.assertEqual(summary['source_sha256'], preview['source_sha256'])
        self.assertEqual(preview['source_sha256'], analysis['source_sha256'])
        self.assertEqual(analysis['row_counts']['matched_rows'], 3)
        for column in 'ABCDEFG':
            self.assertEqual(analysis['profiles'][column]['types'], {'blank': 3})
            self.assertEqual(analysis['profiles'][column]['count'], 0)
            self.assertIsNone(analysis['profiles'][column]['sum'])

    def test_public_balance_next_arguments_keep_large_budget_and_query(self):
        sheet, _ = self.public('Balance_Sheet.ett')
        with tempfile.TemporaryDirectory(prefix='et-public-chain-') as directory:
            task = Path(directory)
            (task / 'source.ett').write_bytes((PUBLIC / 'Balance_Sheet.ett').read_bytes())
            (task / 'query.json').write_text('{"limit":2}', encoding='utf-8')
            for preview in (True, False):
                arguments = ['--sheet', sheet, '--range', 'A8:C10', '--query', 'query.json', '--max-output-bytes', '32768']
                if preview:
                    arguments.append('--preview')
                observed = []
                pages = 0
                for _ in range(10):
                    process, result = run_cli(task, 'analyze_et.py', 'source.ett', *arguments)
                    self.assertEqual(process.returncode, 3, process.stdout)
                    self.assertEqual(process.stderr, b'')
                    self.assertLessEqual(len(process.stdout), 32768)
                    self.assertEqual(result['continuation_entrypoint'], 'analyze_et.py')
                    observed.extend(row['source_row'] for row in result['rows'])
                    pages += 1
                    if not preview:
                        self.assertEqual(result['profiles']['B']['numeric_count'], 2)
                        self.assertEqual(result['profiles']['B']['sum'], '0')
                    if not result['has_more']:
                        self.assertIsNone(result['next_arguments'])
                        break
                    arguments = result['next_arguments']
                    self.assertEqual(arguments[arguments.index('--max-output-bytes') + 1], '32768')
                    self.assertIn('--query=query.json', arguments)
                    self.assertEqual('--preview' in arguments, preview)
                self.assertEqual(observed, [8, 9, 10])
                self.assertEqual(pages, 2)

    def test_partial_three_is_not_a_shell_success_and_budget_failure_is_explicit(self):
        sheet, _ = self.public('Balance_Sheet.ett')
        command = [sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'et_cli.py'), 'run', 'Balance_Sheet.ett', '--summary']
        process = subprocess.run(shlex.join(command) + ' && printf SHOULD_NOT_RUN', shell=True, cwd=PUBLIC, capture_output=True, check=False)
        self.assertEqual(process.returncode, 3)
        self.assertNotIn(b'SHOULD_NOT_RUN', process.stdout)
        self.assertEqual(json.loads(process.stdout)['status'], 'partial')
        process, rejected = run_cli(PUBLIC, 'analyze_et.py', 'Balance_Sheet.ett', '--sheet', sheet, '--range', 'A1:C10', '--preview', '--max-output-bytes', '12000')
        self.assertEqual(process.returncode, 1)
        self.assertEqual(rejected['status'], 'output_view_error')
        process, result = run_cli(PUBLIC, 'analyze_et.py', 'Balance_Sheet.ett', '--sheet', sheet, '--range', 'A1:C10', '--preview', '--max-output-bytes', '32768')
        self.assertEqual(process.returncode, 3)
        self.assertTrue(result['rows'])


if __name__ == '__main__':
    unittest.main()
