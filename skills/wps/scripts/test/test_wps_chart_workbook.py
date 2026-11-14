"""Literal synthetic embedded XLSX cases; no real-producer compatibility claim."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile
from xml.etree.ElementTree import fromstring, tostring

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from wps_binary_charts import PackageBudget
from wps_binary_chart_cache import CHART_NS, ChartCacheLimit, read_chart_cache
from wps_chart_workbook import (CONTENT_TYPES, R, RELATIONSHIPS, S, XLSX, MAIN, SHEET, STRINGS,
                                RELS, enrich_embedded, vector)


def archive(parts):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, value in parts.items():
            package.writestr(name, value)
    return output.getvalue()


def rels(items):
    return f'<Relationships xmlns="{RELATIONSHIPS}">' + ''.join(items) + '</Relationships>'


def relation(identity, kind, target, mode='Internal'):
    return f'<Relationship Id="{identity}" Type="{R}/{kind}" Target="{target}" TargetMode="{mode}"/>'


def workbook(cells=None, extra=None, strings=None):
    cells = cells if cells is not None else ('<c r="A1" t="inlineStr"><is><t>一月</t></is></c>'
        '<c r="A2" t="inlineStr"><is><t>二月</t></is></c><c r="B1"><v>99</v></c><c r="B2"><v>20.25</v></c>')
    overrides = ''.join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in
                        (('xl/book.xml', MAIN), ('xl/sheets/data.xml', SHEET)))
    relationships = [relation('sheet', 'worksheet', 'sheets/data.xml')]
    parts = {'_rels/.rels': rels([relation('book', 'officeDocument', 'xl/book.xml')]),
             'xl/book.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="数据" sheetId="1" r:id="sheet"/></sheets></workbook>',
             'xl/sheets/data.xml': f'<worksheet xmlns="{S}"><sheetData><row>{cells}</row></sheetData></worksheet>'}
    if strings is not None:
        relationships.append(relation('strings', 'sharedStrings', 'strings.xml'))
        overrides += f'<Override PartName="/xl/strings.xml" ContentType="{STRINGS}"/>'
        parts['xl/strings.xml'] = f'<sst xmlns="{S}">{strings}</sst>'
    parts['xl/_rels/book.xml.rels'] = rels(relationships)
    parts['[Content_Types].xml'] = f'<Types xmlns="{CONTENT_TYPES}"><Default Extension="rels" ContentType="{RELS}"/>{overrides}</Types>'
    if extra:
        parts.update(extra)
    return archive(parts)


def chart(formula="'数据'!$B$1:$B$2", cache='<c:numCache><c:ptCount val="2"/><c:pt idx="0"><c:v>10</c:v></c:pt></c:numCache>'):
    return fromstring(f'<c:chartSpace xmlns:c="{CHART_NS}" xmlns:r="{R}"><c:chart><c:plotArea><c:barChart>'
        '<c:ser><c:idx val="0"/><c:order val="0"/><c:cat><c:strRef><c:f>\'数据\'!$A$1:$A$2</c:f></c:strRef></c:cat>'
        f'<c:val><c:numRef><c:f>{formula}</c:f>{cache}</c:numRef></c:val></c:ser>'
        '</c:barChart></c:plotArea></c:chart><c:externalData r:id="data"/></c:chartSpace>')


def execute(root=None, payload=None, outer_relation=None, budget=None, outer_type=XLSX):
    root = chart() if root is None else root
    payload = workbook() if payload is None else payload
    budget = PackageBudget() if budget is None else budget
    parsed = read_chart_cache(root, budget.caches)
    original = deepcopy(parsed)
    parts = {'charts/_rels/chart.xml.rels': rels([outer_relation or relation('data', 'package', '../embeddings/book.xlsx')]),
             'embeddings/book.xlsx': payload}
    types = ({'rels': RELS}, {'embeddings/book.xlsx': outer_type})
    with zipfile.ZipFile(BytesIO(archive(parts))) as package:
        state = enrich_embedded(package, root, 'charts/chart.xml', types, budget, parsed)
    return state, parsed, original


class WorkbookTests(unittest.TestCase):
    def test_binary_public_entry_integrates_embedded_values_and_presentation(self):
        from read_wps import read_bytes
        from test_wps_binary_charts import chart_package, chart_fixture, TARGET, CHART_RELS
        with zipfile.ZipFile(BytesIO(chart_package())) as original:
            parts = {name: original.read(name) for name in original.namelist()}
        parts[TARGET] = tostring(chart(), encoding='utf-8')
        parts[CHART_RELS] = rels([relation('data', 'package', '../embeddings/book.xlsx')])
        parts['embeddings/book.xlsx'] = workbook()
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].decode().replace(
            '</Types>', f'<Override PartName="/embeddings/book.xlsx" ContentType="{XLSX}"/></Types>')
        result = read_bytes(chart_fixture(floating=archive(parts)), reading_mode='maximum')
        self.assertEqual(result['status'], 'partial')
        entry = result['binary_charts']['charts'][0]
        self.assertEqual(entry['workbook_status'], 'embedded_stored_values')
        self.assertEqual(entry['workbook_enrichment']['filled_points'], 3)
        self.assertEqual([p['value'] for p in entry['series'][0]['values']['points']], ['10', '20.25'])
        self.assertEqual(entry['series'][0]['values']['status'], 'partial')
        view = entry['presentation']['series'][0]
        self.assertEqual([row['category'] for row in view['rows']], ['一月', '二月'])
        self.assertEqual(view['rows'][1]['value_source'], 'embedded_workbook')
        self.assertEqual(view['missing_fields'], [])
        self.assertEqual(view['value_source'], 'mixed_chart_cache_and_embedded_workbook')

    def test_missing_values_filled_without_overwriting_cache(self):
        state, parsed, original = execute()
        self.assertEqual(state['status'], 'stored_values')
        self.assertEqual(state['filled_points'], 3)
        values = parsed['series'][0]['values']
        self.assertEqual([p['value'] for p in values['points']], ['10', '20.25'])
        self.assertEqual(values['points'][0], original['series'][0]['values']['points'][0])
        self.assertEqual(values['provenance'], 'mixed_chart_cache_and_embedded_workbook')
        self.assertEqual(values['points'][1]['source']['cell'], 'B2')
        self.assertEqual(values['missing_ranges'], original['series'][0]['values']['missing_ranges'])
        self.assertEqual(values['status'], original['series'][0]['values']['status'])
        self.assertEqual(values['effective_missing_indices'], [])
        self.assertFalse(state['formulas_evaluated'])

    def test_numeric_formula_stored_result_and_missing_result(self):
        for cached, expected in (('<v>12345678901234567890.12</v>', '12345678901234567890.12'), ('', None)):
            payload = workbook('<c r="B2"><f>SUM(B1:B1)</f>' + cached + '</c>')
            state, parsed, _ = execute(payload=payload)
            values = parsed['series'][0]['values']
            if expected is not None:
                self.assertEqual(values['points'][-1]['value'], expected)
                self.assertTrue(values['points'][-1]['source']['formula_cached'])
            else:
                self.assertEqual(len(values['points']), 1)
                self.assertIn(1, values['effective_missing_indices'])
            self.assertEqual(state['status'], 'partial')  # absent categories remain explicit

    def test_shared_rich_string_and_no_phonetic_duplication(self):
        payload = workbook('<c r="A1" t="s"><v>0</v></c><c r="A2" t="s"><v>1</v></c><c r="B2"><v>2</v></c>',
                           strings='<si><r><t>一</t></r><r><t>月</t></r><rPh><t>phonetic</t></rPh></si><si><t>二月</t></si>')
        _, parsed, _ = execute(payload=payload)
        self.assertEqual([p['value'] for p in parsed['series'][0]['categories']['points']], ['一月', '二月'])

    def test_error_boolean_and_missing_cells_never_become_zero(self):
        for cell in ('<c r="B2" t="e"><v>#N/A</v></c>', '<c r="B2" t="b"><v>1</v></c>', ''):
            state, parsed, _ = execute(payload=workbook(cell))
            values = parsed['series'][0]['values']
            self.assertEqual(state['status'], 'partial')
            self.assertEqual([p['value'] for p in values['points']], ['10'])
            self.assertEqual(values['effective_missing_indices'], [1])
            self.assertTrue(values['workbook_enrichment']['missing_cells'])

    def test_external_workbook_not_followed_or_disclosed(self):
        target = 'https://private.example/secret.xlsx'
        state, parsed, original = execute(outer_relation=relation('data', 'package', target, 'External'))
        self.assertEqual(state['status'], 'external_not_followed')
        self.assertEqual(parsed, original)
        self.assertNotIn(target, str(state))

    def test_formula_scope_is_narrow(self):
        for value in ("'[other.xlsx]数据'!A1:A2", 'SUM(数据!A1:A2)', '数据!A1:B2', '数据!A1,A2',
                      '数据!A:A', '数据!Name', 'Sheet1:Sheet2!A1', '数据!A0', '数据!XFE1', '=数据!A1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                vector(value)
        self.assertEqual(vector("'O''Brien'!$A$1:$C$1"), ("O'Brien", [(1, 1), (2, 1), (3, 1)]))

    def test_external_formula_does_not_change_existing_values(self):
        state, parsed, original = execute(root=chart("'[secret.xlsx]数据'!B1:B2"))
        self.assertEqual(parsed['series'][0]['values'], original['series'][0]['values'])
        self.assertEqual(state['status'], 'partial')
        self.assertNotIn('secret.xlsx', str(state))

    def test_bad_zip_wrong_type_and_bad_sheet_preserve_cache(self):
        for kwargs in ({'payload': b'not zip'}, {'outer_type': 'application/octet-stream'},
                       {'payload': workbook(extra={'xl/sheets/data.xml': '<broken>'})}):
            with self.subTest(kwargs=kwargs):
                state, parsed, original = execute(**kwargs)
                self.assertIn(state['status'], ('unresolved', 'embedded_format_unsupported', 'partial'))
                self.assertEqual(parsed['series'][0]['values']['points'], original['series'][0]['values']['points'])

    def test_duplicate_cell_and_duplicate_relationship_rejected(self):
        payload = workbook('<c r="B2"><v>2</v></c><c r="B2"><v>3</v></c>')
        state, parsed, original = execute(payload=payload)
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(parsed, original)
        rel = relation('book', 'officeDocument', 'xl/book.xml')
        state, parsed, original = execute(payload=workbook(extra={'_rels/.rels': rels([rel, rel])}))
        self.assertEqual(state['status'], 'unresolved')
        self.assertEqual(parsed, original)

    def test_unsafe_archive_path_rejected(self):
        state, parsed, original = execute(payload=workbook(extra={'../bad': 'x'}))
        self.assertEqual(state['status'], 'unresolved')
        self.assertEqual(parsed, original)

    def test_resource_limits_propagate_and_are_cumulative(self):
        with patch('wps_chart_workbook.MAX_BYTES', 1), self.assertRaises(ChartCacheLimit):
            execute()
        with patch('wps_chart_workbook.MAX_EXPANDED', 1), self.assertRaises(ChartCacheLimit):
            execute()
        with patch('wps_chart_workbook.MAX_CELLS', 1), self.assertRaises(ChartCacheLimit):
            execute()
        budget = PackageBudget()
        payload = workbook()
        with patch('wps_chart_workbook.MAX_TOTAL_BYTES', len(payload) + 1):
            execute(payload=payload, budget=budget)
            with self.assertRaises(ChartCacheLimit):
                execute(payload=payload, budget=budget)

    def test_count_mismatch_and_bad_cache_not_repaired_by_guess(self):
        for root in (chart("'数据'!B1:B3"), chart(cache='<c:numCache><c:ptCount val="2"/>'
                      '<c:pt idx="0"><c:v>1</c:v></c:pt><c:pt idx="0"><c:v>2</c:v></c:pt></c:numCache>')):
            state, parsed, original = execute(root=root)
            self.assertEqual(state['status'], 'partial')
            self.assertEqual(parsed['series'][0]['values'], original['series'][0]['values'])


if __name__ == '__main__':
    unittest.main()
