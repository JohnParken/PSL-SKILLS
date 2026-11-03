#!/usr/bin/env python3
"""Offline maintenance evidence; run from repository root with -I -S -B.

Downloads are separately authorized and hash-pinned in sources.json. No network,
Excel, source writes, shared-formula expansion, or use of formula caches as inputs.
The Fraction oracle below is deliberately narrow, independent of the tested parser,
and only covers the selected original formulas, not arbitrary Excel semantics.
"""

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import platform
import posixpath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SCRIPTS = REPO / 'skills' / 'xlsx' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from formula_engine import evaluate
from stdlib_xlsx import Workbook
from xlsx_analysis import summarize

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
CELL = r'\$?[A-Z]{1,3}\$?[1-9][0-9]*'
REF = re.compile(r'(?<![A-Za-z0-9_])(' + CELL + r')(?::(' + CELL + r'))?(?![A-Za-z0-9_])')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decimal_text(value):
    with localcontext() as context:
        context.prec = 100
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def cents(value):
    """Round an exact rational to cents, ties away from zero; no tested engine used."""
    scaled = abs(value) * 100
    whole, remainder = divmod(scaled.numerator, scaled.denominator)
    rounded = whole + (2 * remainder >= scaled.denominator)
    return (-1 if value < 0 else 1) * rounded


def raw_read(path):
    """Independent, read-only OOXML extraction of these hash-pinned public fixtures."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        assert len(infos) <= 10000 and sum(info.file_size for info in infos) <= 128 * 1024 * 1024

        def parse(name):
            data = archive.read(name)
            assert b'<!DOCTYPE' not in data and b'<!ENTITY' not in data
            return ET.fromstring(data)

        relationships = {r.get('Id'): r for r in parse('xl/_rels/workbook.xml.rels')}

        def target(rel):
            assert rel.get('TargetMode') != 'External'
            name = rel.get('Target')
            return name.lstrip('/') if name.startswith('/') else posixpath.normpath(posixpath.join('xl', name))

        strings = []
        for rel in relationships.values():
            if rel.get('Type', '').endswith('/sharedStrings'):
                strings = [''.join(t.text or '' for t in item.iter(NS + 't')) for item in parse(target(rel))]
        sheets = {}
        for sheet in parse('xl/workbook.xml').findall(NS + 'sheets/' + NS + 'sheet'):
            cells = {}
            for node in parse(target(relationships[sheet.get(REL + 'id')])).iter(NS + 'c'):
                formula = node.find(NS + 'f')
                value = node.find(NS + 'v')
                raw = None if value is None else value.text
                kind = node.get('t', 'n')
                decoded = raw
                if kind == 'n':
                    decoded = None if raw is None else Fraction(raw)
                elif kind == 's':
                    decoded = strings[int(raw)]
                elif kind == 'b':
                    decoded = raw == '1'
                elif kind == 'inlineStr':
                    decoded = ''.join(t.text or '' for t in node.iter(NS + 't'))
                elif kind == 'str' and value is not None:
                    decoded = raw or ''
                cells[node.get('r')] = {'value': decoded, 'type': kind, 'raw': raw,
                                       'formula': None if formula is None else formula.text or '',
                                       'attributes': {} if formula is None else dict(formula.attrib)}
            sheets[sheet.get('name')] = cells
        return sheets


def span(start, end):
    def split(ref):
        letters, row = re.fullmatch(r'([A-Z]+)([0-9]+)', ref.replace('$', '')).groups()
        col = 0
        for letter in letters:
            col = col * 26 + ord(letter) - ord('A') + 1
        return col, int(row)

    c1, r1 = split(start)
    c2, r2 = split(end)
    assert (c2 - c1 + 1) * (r2 - r1 + 1) <= 100000
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            letters = ''
            while col:
                col, remainder = divmod(col - 1, 26)
                letters = chr(ord('A') + remainder) + letters
            yield letters + str(row)


class Oracle:
    """Exact rational reference for SUM, SUMPRODUCT, basic arithmetic and A1 links."""

    def __init__(self, sheets):
        self.sheets = sheets
        self.memo = {}
        self.active = set()

    def literal(self, sheet, address):
        cell = self.sheets[sheet].get(address.replace('$', ''))
        if cell is None:
            return None
        assert cell['formula'] is None, 'A formula cache must not be used as a literal input'
        assert cell['type'] not in ('e', 'd')
        return cell['value']

    def cell(self, sheet, address):
        address = address.replace('$', '')
        key = (sheet, address)
        if key in self.memo:
            return self.memo[key]
        cell = self.sheets[sheet].get(address)
        if cell is None or cell['formula'] is None:
            return self.literal(sheet, address)
        assert not cell['attributes'], 'Oracle does not expand special/shared formulas'
        assert key not in self.active and len(self.active) < 100
        self.active.add(key)
        try:
            formula = cell['formula']
            direct = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)!(' + CELL + ')', formula)
            if direct:
                result = self.cell(*direct.groups())
            else:
                transformed = REF.sub(lambda m: 'SPAN(%r,%r)' % m.groups() if m.group(2) else 'CELL(%r)' % m.group(1), formula)
                result = self.node(ast.parse(transformed, mode='eval').body, transformed, sheet)
            if result is None:
                result = Fraction(0)
            self.memo[key] = result
            return result
        finally:
            self.active.remove(key)

    def node(self, node, text, sheet):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Fraction(ast.get_source_segment(text, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.node(node.operand, text, sheet)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = self.node(node.left, text, sheet)
            right = self.node(node.right, text, sheet)
            left = Fraction(0) if left is None else left
            right = Fraction(0) if right is None else right
            assert isinstance(left, Fraction) and isinstance(right, Fraction)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name == 'CELL':
                return self.cell(sheet, node.args[0].value)
            if name == 'SPAN':
                return [self.cell(sheet, ref) for ref in span(*(a.value for a in node.args))]
            args = [self.node(arg, text, sheet) for arg in node.args]
            if name == 'SUM':
                flattened = [item for arg in args for item in (arg if isinstance(arg, list) else [arg])]
                return sum((v for v in flattened if isinstance(v, Fraction)), Fraction(0))
            if name == 'SUMPRODUCT':
                assert args and len({len(a) for a in args}) == 1
                result = Fraction(0)
                for row in zip(*args):
                    product = Fraction(1)
                    for value in row:
                        product *= value if isinstance(value, Fraction) else 0
                    result += product
                return result
        raise AssertionError('Outside independent oracle scope: ' + ast.dump(node))


def comparison(actual, expected, cached, currency_scale=1):
    actual = Fraction(actual)
    assert actual == expected, (actual, expected)
    result = {'actual': decimal_text(actual), 'independent_expected': decimal_text(expected), 'exact_match': True}
    if isinstance(cached, Fraction):
        result.update(original_cache=decimal_text(cached), cache_delta=decimal_text(actual - cached),
                      currency_scale_to_gbp=currency_scale, cache_delta_gbp=decimal_text((actual - cached) * currency_scale),
                      cache_exact_match=actual == cached,
                      cache_matches_to_gbp_cents=cents(actual * currency_scale) == cents(cached * currency_scale))
    return result


def inventory(sheets):
    formulas = [c for cells in sheets.values() for c in cells.values() if c['formula'] is not None]
    return {'sheet_count': len(sheets), 'stored_cell_count': sum(len(c) for c in sheets.values()),
            'formula_count': len(formulas), 'special_formula_count': sum(bool(c['attributes']) for c in formulas),
            'functions_in_stored_formula_text': dict(Counter(name.upper() for c in formulas
                                                            for name in re.findall(r'([A-Za-z_][A-Za-z0-9_.]*)\s*\(', c['formula'])))}


def compact_report(report, sheets):
    result = {key: value for key, value in report.items() if key != 'issues'}
    counts = Counter((item['code'], item['message']) for item in report['issues'])
    result['issue_groups'] = []
    for (code, message), count in counts.most_common():
        example = next(item for item in report['issues'] if (item['code'], item['message']) == (code, message))
        result['issue_groups'].append({'code': code, 'message': message, 'count': count, 'example': example,
                                       'example_formula': sheets[example['sheet']][example['cell']]['formula']})
    return result


def diagnostic_calculate(sheets, sheet, address):
    """Isolated formula test; never substitutes caches or claims full workbook success."""
    memo = {}
    active = set()

    def resolve(name, ref):
        key = (name, ref)
        if key in memo:
            return memo[key]
        cell = sheets[name].get(ref)
        if cell is None:
            return None
        if cell['formula'] is None:
            assert cell['type'] not in ('e', 'd')
            return Decimal(cell['raw']) if isinstance(cell['value'], Fraction) else cell['value']
        assert not cell['attributes'] and key not in active and len(active) < 100
        active.add(key)
        try:
            value = evaluate(cell['formula'], name, resolve)
            memo[key] = Decimal(0) if value is None else value
            return memo[key]
        finally:
            active.remove(key)

    return resolve(sheet, address)


def derived_checks(sheets):
    """New formulas over unmodified published transactions, NOT publisher formulas."""
    oracle = Oracle(sheets)
    rows = list(range(23, 51))
    payments = [(oracle.literal('Sheet1', 'F%d' % row), oracle.literal('Sheet1', 'B%d' % row)) for row in rows]
    hmrc = [amount for payee, amount in payments if payee == 'HMRC']
    hmrc_over_75 = [amount for payee, amount in payments if payee == 'HMRC' and amount > 75]
    cases = [
        ('SUMIF(F23:F50,"HMRC",B23:B50)', sum(hmrc)),
        ('SUMIF(F23:F50,"hMrC",B23:B50)', sum(hmrc)),
        ('SUMIFS(B23:B50,F23:F50,"HMRC",B23:B50,">75")', sum(hmrc_over_75)),
        ('COUNTIF(F23:F50,"HMRC")', len(hmrc)),
        ('COUNTIFS(F23:F50,"HMRC",B23:B50,">75")', len(hmrc_over_75)),
        ('SUMIF(F7:F16,"Bank Interest",B7:B16)', sum(oracle.literal('Sheet1', 'B%d' % row)
                                                   for row in range(7, 17) if oracle.literal('Sheet1', 'F%d' % row) == 'Bank Interest')),
        ('COUNTIF(F7:F16,"Bank*")', sum(oracle.literal('Sheet1', 'F%d' % row).startswith('Bank') for row in range(7, 17))),
        ('SUMIFS(B23:B50,F23:F50,"not_a_payee",B23:B50,">75")', 0),
        ('ROUND(SUMIF(F23:F50,"HMRC",B23:B50),2)', Fraction(cents(sum(hmrc)), 100)),
    ]

    def literal_resolver(sheet, ref):
        value = oracle.literal(sheet, ref)
        return Decimal(sheets[sheet][ref]['raw']) if isinstance(value, Fraction) else value

    return [{'formula': formula, 'source': 'denham_bank_reconciliation.xlsx', 'scope': 'new_formula_on_original_data',
             **comparison(evaluate(formula, 'Sheet1', literal_resolver), Fraction(expected), None)} for formula, expected in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', choices=['results.json', 'results_py312.json'], help='Optional exclusive evidence file; otherwise JSON goes to stdout')
    args = parser.parse_args()
    assert Path.cwd().resolve() == REPO, 'Run from repository root'
    output = ROOT / args.output if args.output else None
    assert output is None or not output.exists(), 'Evidence output is exclusive; do not overwrite an earlier run'
    sources = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
    results = {'executed_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
               'python': platform.python_version(), 'command': [sys.executable, '-I', '-S', '-B', *sys.argv],
               'scope': 'original workbooks + independently labelled selected formula and derived transaction checks',
               'harness_sha256': digest(Path(__file__)), 'cache_is_oracle': False,
               'excel_recalculation_run': False, 'linux_validation_run': False, 'workbooks': []}
    raw_books = {}
    native_books = {}
    for source in sources:
        path = ROOT / source['file']
        assert source['status'] == 'downloaded' and digest(path) == source['sha256']
        raw = raw_read(path)
        raw_books[source['file']] = raw
        item = {'file': source['file'], 'sha256': source['sha256'], 'inventory': inventory(raw), 'verified_original_formulas': []}
        try:
            book = Workbook.load(path)
        except ValueError as exc:
            item['native'] = {'status': 'load_rejected', 'message': str(exc), 'calculated_count': 0}
        else:
            report = book.calculate()
            native_books[source['file']] = book
            item['native'] = compact_report(report, raw)
            if report['status'] in ('incomplete', 'calculated_subset'):
                failed = {(e['sheet'], e['cell']) for e in report['issues']}
                oracle = Oracle(raw)
                for sheet, cells in raw.items():
                    for ref, cell in cells.items():
                        if cell['formula'] is not None and (sheet, ref) not in failed:
                            check = comparison(book.cells[sheet][ref]['value'], oracle.cell(sheet, ref), cell['value'])
                            item['verified_original_formulas'].append({'sheet': sheet, 'cell': ref, 'formula': cell['formula'], **check})
                assert len(item['verified_original_formulas']) == report['calculated_count']
        # Real CLI boundary: a small selected region must not bypass whole-book failure.
        sheet = next(iter(raw))
        command = [sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'xlsx_analysis.py'), 'profile', str(path.relative_to(REPO)),
                   '--sheet', sheet, '--range', 'A1:B2', '--no-header', '--formulas', 'calculate']
        run = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=60)
        payload = json.loads(run.stdout)
        expected_code = 0 if item['native']['status'] == 'calculated_subset' else 1
        assert run.returncode == expected_code and run.stderr == '', (run.returncode, run.stderr)
        if expected_code == 0:
            assert payload['unverified_formula_caches'] == 0
            assert payload['formula_calculation']['status'] == 'calculated_subset'
            # The top-level field counts selected cells; the nested report counts the whole book.
            expected_selected = sum(raw[sheet].get(ref, {}).get('formula') is not None for ref in span('A1', 'B2'))
            assert payload['recalculated_formula_cells'] == expected_selected
            assert payload['formula_calculation']['calculated_count'] == item['native']['calculated_count']
        elif item['native']['status'] != 'load_rejected':
            assert payload['calculation']['status'] == item['native']['status']
            assert payload['calculation']['calculated_count'] == item['native']['calculated_count']
        item['analysis_cli'] = {'command': command, 'exit_code': run.returncode, 'stderr': run.stderr,
                                'stdout_status': payload['status'], 'stdout_bytes': len(run.stdout.encode('utf-8')),
                                'stdout_sha256': hashlib.sha256(run.stdout.encode('utf-8')).hexdigest(),
                                'selected_range_formula_count': payload.get('recalculated_formula_cells'),
                                'contract_passed': True}
        results['workbooks'].append(item)

    gas = raw_books['ofgem_gas_pcfm_draft.xlsx']
    oracle = Oracle(gas)
    results['selected_original_sumproduct_checks'] = []
    for col in ('AU', 'AV', 'AW', 'AX', 'AY'):
        ref = col + '120'
        cell = gas['Transform_TO'][ref]
        check = comparison(diagnostic_calculate(gas, 'Transform_TO', ref), oracle.cell('Transform_TO', ref), cell['value'], currency_scale=1000000)
        results['selected_original_sumproduct_checks'].append({'sheet': 'Transform_TO', 'cell': ref, 'formula': cell['formula'],
                                                              'source': 'ofgem_gas_pcfm_draft.xlsx', 'unit': 'GBP million, 2023/24 prices',
                                                              'scope': 'selected dependency closure only; native whole workbook failed', **check})
    results['derived_transaction_checks'] = derived_checks(raw_books['denham_bank_reconciliation.xlsx'])
    bank = native_books['denham_bank_reconciliation.xlsx']
    results['bank_business_checks'] = []
    for ref, amount in {'F5': '3906.41', 'B18': '5609.68', 'B52': '5495.73', 'B53': '4020.36', 'B55': '102.56', 'B57': '4122.92', 'B62': '4122.92'}.items():
        actual = Fraction(bank.cells['Sheet1'][ref]['value'])
        assert cents(actual) == cents(Fraction(amount))
        results['bank_business_checks'].append({'cell': ref, 'actual_raw': decimal_text(actual), 'expected_gbp_to_cents': amount, 'passed': True})
    assert cents(Fraction(bank.cells['Sheet1']['B57']['value'])) == cents(Fraction(bank.cells['Sheet1']['B62']['value']))
    results['source_content_note'] = 'Denham bank filename says March 31 2020; workbook dates and transactions are 2018/19. Validation follows workbook content.'
    grouped = summarize(ROOT / 'denham_bank_reconciliation.xlsx', 'Sheet1', {'group_by': ['F'], 'measure': 'B'},
                        cell_range='B23:G50', header=False, formulas='calculate')
    assert grouped['reconciliation']['status'] == 'passed' and grouped['unverified_formula_caches'] == 0
    total = sum(Fraction(g['numeric']['sum']) for g in grouped['groups'])
    expected = Oracle(raw_books['denham_bank_reconciliation.xlsx']).cell('Sheet1', 'B52')
    assert total == expected
    results['recalculated_group_summary'] = {'reconciliation': grouped['reconciliation'], 'sum': decimal_text(total),
                                           'group_count': len(grouped['groups']), 'unverified_formula_caches': 0, 'passed': True}
    results['input_integrity'] = {source['file']: digest(ROOT / source['file']) == source['sha256'] for source in sources}
    assert all(results['input_integrity'].values())
    if output is None:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return
    with output.open('x', encoding='utf-8') as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')
    print(json.dumps({'evidence_written': str(output.relative_to(REPO)), 'test_assertions': 'passed',
                      'whole_workbook_statuses': {item['file']: item['native']['status'] for item in results['workbooks']},
                      'original_formulas_independently_verified': sum(len(item['verified_original_formulas']) for item in results['workbooks']),
                      'selected_original_sumproduct_verified': 5, 'derived_transaction_formulas_verified': len(results['derived_transaction_checks']),
                      'source_files_unchanged': True}, indent=2))


if __name__ == '__main__':
    main()
