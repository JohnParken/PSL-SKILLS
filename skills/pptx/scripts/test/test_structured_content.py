"""Independent literal OOXML fixtures for native tables and saved chart caches."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from pptx_core.package import ConvertedSlide, build_presentation
from pptx_core.validation import ValidationError, inspect_presentation, validate_presentation
from test_package import SHAPE, read_parts, slide_xml
from test_validation import zipped
from test_v680_inspection import alternate, choice


C_NS = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
CX_NS = 'http://schemas.microsoft.com/office/drawing/2014/chartex'
A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'


def cell(text='', attributes=''):
    """A real cell, including the deliberate empty string."""
    return f'<a:tc {attributes}><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc>'


def table(rows=None, columns=2):
    """Return literal table markup, not output from the production converter."""
    rows = rows if rows is not None else [[cell('Header'), cell('')], [cell('001'), cell('2.50')]]
    grid = '<a:gridCol w="1000000"/>' * columns
    body = ''.join('<a:tr h="200000">' + ''.join(row) + '</a:tr>' for row in rows)
    return f'<a:tbl><a:tblPr/><a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl>'


def frame(payload, identifier=8):
    """Wrap a table or relationship reference in a native drawing frame."""
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{identifier}" name="Data"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="0" y="0"/>'
            '<a:ext cx="2000000" cy="2000000"/></p:xfrm><a:graphic><a:graphicData uri="urn:fixture">'
            + payload + '</a:graphicData></a:graphic></p:graphicFrame>')


def cache(kind='num', points=((0, '1.2500'), (1, '-2E+03')), count=2, formula='Sheet1!$B$2:$B$3'):
    """Keep exact lexical numeric representation and point order."""
    stored = ''.join(f'<c:pt idx="{index}"><c:v>{value}</c:v></c:pt>' for index, value in points)
    return (f'<c:{kind}Ref><c:f>{formula}</c:f><c:{kind}Cache><c:ptCount val="{count}"/>'
            f'{stored}</c:{kind}Cache></c:{kind}Ref>')


def series(values=None, categories=None):
    values = cache() if values is None else values
    categories = cache('str', ((0, 'Q1'), (1, 'Q2'))) if categories is None else categories
    return ('<c:ser><c:idx val="8"/><c:order val="0"/><c:tx><c:v>Revenue</c:v></c:tx>'
            f'<c:cat>{categories}</c:cat><c:val>{values}</c:val></c:ser>')


def chart_xml(groups=None, title=True):
    groups = '<c:barChart>' + series() + '</c:barChart>' if groups is None else groups
    title_xml = ('<c:title><c:tx><c:rich><a:bodyPr/><a:p><a:r><a:t>Saved results</a:t></a:r>'
                 '</a:p></c:rich></c:tx></c:title>') if title else ''
    return (f'<c:chartSpace xmlns:c="{C_NS}" xmlns:a="{A_NS}"><c:chart>{title_xml}'
            f'<c:plotArea>{groups}</c:plotArea></c:chart></c:chartSpace>').encode()


def deck(objects='', chart=None, notes_table=None, chartex=False):
    """Assemble a valid shell; inject independent native-content XML fixtures."""
    chart_frame = ''
    if chart is not None:
        namespace, prefix = (CX_NS, 'cx') if chartex else (C_NS, 'c')
        chart_frame = frame(f'<{prefix}:chart xmlns:{prefix}="{namespace}" xmlns:r="{R_NS}" r:id="rData"/>', 9)
    payload = build_presentation([ConvertedSlide(slide_xml(SHAPE + objects), notes='Speaker' if notes_table else '')])
    parts = read_parts(payload)
    if chart is not None:
        parts['ppt/slides/slide1.xml'] = parts['ppt/slides/slide1.xml'].replace(b'</p:spTree>', (chart_frame + '</p:spTree>').encode())
        parts['ppt/charts/chart1.xml'] = chart
        rel = ET.fromstring(parts['ppt/slides/_rels/slide1.xml.rels'])
        kind = 'http://schemas.microsoft.com/office/2014/relationships/chartEx' if chartex else R_NS + '/chart'
        ET.SubElement(rel, '{' + REL_NS + '}Relationship', {'Id': 'rData', 'Type': kind, 'Target': '../charts/chart1.xml'})
        parts['ppt/slides/_rels/slide1.xml.rels'] = ET.tostring(rel)
        types = ET.fromstring(parts['[Content_Types].xml'])
        content_type = 'application/vnd.ms-office.chartex+xml' if chartex else 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
        ET.SubElement(types, '{' + CT_NS + '}Override', {'PartName': '/ppt/charts/chart1.xml', 'ContentType': content_type})
        parts['[Content_Types].xml'] = ET.tostring(types)
    if notes_table:
        name = 'ppt/notesSlides/notesSlide1.xml'
        parts[name] = parts[name].replace(b'</p:spTree>', (frame(notes_table, 12) + '</p:spTree>').encode())
    return zipped(parts)


def diagnostics(slide):
    return {item['code'] for item in slide.get('content_assessment', {}).get('diagnostics', [])}


class StructuredTableTests(unittest.TestCase):
    """Grid shape and stored cell semantics are not flattened away."""

    def test_empty_cells_and_old_text_and_notes_are_preserved(self):
        result = inspect_presentation(deck(frame(table()), notes_table=table()))
        slide = result['slides'][0]
        native = slide['tables'][0]
        self.assertEqual((native['row_count'], native['column_count']), (2, 2))
        self.assertEqual([[c['text'] for c in r['cells']] for r in native['rows']], [['Header', ''], ['001', '2.50']])
        self.assertEqual(native['source'], {'part': 'ppt/slides/slide1.xml'})
        self.assertEqual(slide['notes_tables'][0]['rows'], native['rows'])
        self.assertEqual(slide['text'], ['季度收入 & Growth', 'Header', '001', '2.50'])
        self.assertEqual(slide['notes'], ['Speaker', 'Header', '', '001', '2.50'])
        self.assertEqual(slide['objects']['tables'], 1)
        self.assertNotIn('content_assessment', slide)
        self.assertEqual(result['structural_validation'], 'passed')

    def test_rectangular_merge_preserves_anchors_and_continuations(self):
        markup = table([[cell('merged', 'gridSpan="2" rowSpan="2"'), cell('', 'hMerge="1" rowSpan="2"')],
                        [cell('', 'vMerge="1" gridSpan="2"'), cell('', 'hMerge="1" vMerge="1"')]])
        slide = inspect_presentation(deck(frame(markup)))['slides'][0]
        self.assertNotIn('content_assessment', slide)
        rows = slide['tables'][0]['rows']
        self.assertEqual(rows[0]['cells'][0]['column_span'], 2)
        self.assertEqual(rows[0]['cells'][0]['row_span'], 2)
        self.assertTrue(rows[1]['cells'][1]['v_merge'])
        self.assertEqual(rows[1]['cells'][1]['text'], '')

    def test_irregular_and_orphan_merges_are_content_partial_only(self):
        markup = table([[cell('bad', 'rowSpan="9999999999"'), cell('', 'hMerge="1"')], [cell('short')]])
        result = inspect_presentation(deck(frame(markup)))
        codes = diagnostics(result['slides'][0])
        self.assertTrue({'table_merge_invalid', 'table_row_width_mismatch', 'table_merge_topology_inconsistent'} <= codes)
        self.assertEqual(result['structural_validation'], 'passed')

    def test_merge_origin_missing_continuation_and_overlong_chain(self):
        for row in ([cell('x', 'gridSpan="2"'), cell('not merged')],
                    [cell('x', 'gridSpan="2"'), cell('', 'hMerge="1"'), cell('', 'hMerge="1"')]):
            slide = inspect_presentation(deck(frame(table([row], columns=len(row)))))['slides'][0]
            self.assertIn('table_merge_topology_inconsistent', diagnostics(slide))

    def test_mce_selects_exactly_one_table_and_preserves_source_order(self):
        first = frame(table([[cell('selected')]], columns=1), 8)
        fallback = frame(table([[cell('fallback')]], columns=1), 10)
        selected = alternate(choice(first), fallback)
        result = inspect_presentation(deck(selected + frame(table(), 11), chart=chart_xml()))['slides'][0]
        self.assertEqual([t['order'] for t in result['tables']], [1, 2])
        self.assertEqual(result['charts'][0]['order'], 3)
        self.assertEqual(result['tables'][0]['rows'][0]['cells'][0]['text'], 'selected')
        self.assertNotIn('fallback', result['text'])


class SavedChartTests(unittest.TestCase):
    """Caches are observed, never recalculated or expanded from declared indexes."""

    def test_title_series_categories_precision_and_provenance(self):
        slide = inspect_presentation(deck(chart=chart_xml()))['slides'][0]
        chart = slide['charts'][0]
        self.assertEqual(chart['title'], {'source': 'stored_rich_text', 'text': 'Saved results'})
        self.assertEqual(chart['chart_types'], ['barChart'])
        data = chart['series'][0]
        self.assertEqual(data['name']['value'], 'Revenue')
        self.assertEqual(data['categories']['points'], [{'index': 0, 'value': 'Q1'}, {'index': 1, 'value': 'Q2'}])
        self.assertEqual(data['values']['points'], [{'index': 0, 'value': '1.2500'}, {'index': 1, 'value': '-2E+03'}])
        self.assertEqual(data['values']['formula'], 'Sheet1!$B$2:$B$3')
        self.assertEqual(data['values']['source'], 'saved_cache')
        self.assertEqual(chart['source']['chart_part'], 'ppt/charts/chart1.xml')
        self.assertEqual(chart['data_policy'], 'saved_xml_only_not_recalculated')
        self.assertNotIn('content_assessment', slide)

    def test_sparse_cache_is_not_padded_and_xml_order_is_preserved(self):
        values = cache(points=((100000, '001.00'), (0, '')), count=4294967295)
        slide = inspect_presentation(deck(chart=chart_xml('<c:lineChart>' + series(values) + '</c:lineChart>')))['slides'][0]
        points = slide['charts'][0]['series'][0]['values']['points']
        self.assertEqual(points, [{'index': 100000, 'value': '001.00'}, {'index': 0, 'value': ''}])
        self.assertIn('chart_cache_sparse', diagnostics(slide))

    def test_missing_cache_is_not_an_empty_or_zero_series(self):
        values = '<c:numRef><c:f>Sheet1!A1:A2</c:f></c:numRef>'
        slide = inspect_presentation(deck(chart=chart_xml('<c:barChart>' + series(values) + '</c:barChart>')))['slides'][0]
        data = slide['charts'][0]['series'][0]['values']
        self.assertEqual(data['status'], 'missing')
        self.assertNotIn('points', data)
        self.assertIn('chart_cache_missing_or_ambiguous', diagnostics(slide))

    def test_mixed_literal_scatter_and_multi_level_categories(self):
        levels = ('<c:multiLvlStrRef><c:f>S!A1:B2</c:f><c:multiLvlStrCache><c:ptCount val="2"/>'
                  '<c:lvl><c:pt idx="0"><c:v>East</c:v></c:pt><c:pt idx="1"><c:v>West</c:v></c:pt></c:lvl>'
                  '<c:lvl><c:pt idx="0"><c:v>2026</c:v></c:pt></c:lvl></c:multiLvlStrCache></c:multiLvlStrRef>')
        literal = '<c:numLit><c:formatCode>0.00%</c:formatCode><c:ptCount val="1"/><c:pt idx="0"><c:v>0.125</c:v></c:pt></c:numLit>'
        scatter = f'<c:scatterChart><c:ser><c:xVal>{literal}</c:xVal><c:yVal>{literal}</c:yVal></c:ser></c:scatterChart>'
        groups = '<c:lineChart>' + series(categories=levels) + '</c:lineChart>' + scatter
        slide = inspect_presentation(deck(chart=chart_xml(groups)))['slides'][0]
        chart = slide['charts'][0]
        self.assertEqual(chart['chart_types'], ['lineChart', 'scatterChart'])
        self.assertEqual(len(chart['series'][0]['categories']['levels']), 2)
        self.assertEqual(chart['series'][1]['x_values']['number_format'], '0.00%')
        self.assertEqual(chart['series'][1]['y_values']['source'], 'stored_literal')

    def test_cache_mce_duplicate_and_missing_value_diagnostics(self):
        invalid = '<c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>5</c:v></c:pt><c:pt idx="0"/></c:numLit>'
        selected = alternate(choice(invalid), '<c:numLit><c:ptCount val="0"/></c:numLit>')
        slide = inspect_presentation(deck(chart=chart_xml('<c:barChart>' + series(selected) + '</c:barChart>')))['slides'][0]
        self.assertEqual(slide['charts'][0]['series'][0]['values']['points'][0]['value'], '5')
        self.assertTrue({'chart_point_index_invalid_or_duplicate', 'chart_point_value_missing_or_ambiguous'} <= diagnostics(slide))

    def test_unsupported_chartex_and_unknown_chart_are_explicit(self):
        chart = f'<cx:chartSpace xmlns:cx="{CX_NS}"><cx:chart/></cx:chartSpace>'.encode()
        slide = inspect_presentation(deck(chart=chart, chartex=True))['slides'][0]
        self.assertIn('chart_format_unsupported', diagnostics(slide))
        slide = inspect_presentation(deck(chart=chart_xml('<c:futureChart/>')))['slides'][0]
        self.assertIn('chart_type_unsupported', diagnostics(slide))

    def test_axis_title_units_and_format_are_stored_not_applied(self):
        axis = ('<c:valAx><c:title><c:tx><c:strRef><c:f>S!A1</c:f><c:strCache><c:ptCount val="1"/>'
                '<c:pt idx="0"><c:v>USD</c:v></c:pt></c:strCache></c:strRef></c:tx></c:title>'
                '<c:numFmt formatCode="$0.00"/><c:dispUnits><c:builtInUnit val="millions"/></c:dispUnits></c:valAx>')
        slide = inspect_presentation(deck(chart=chart_xml('<c:barChart>' + series() + '</c:barChart>' + axis)))['slides'][0]
        data = slide['charts'][0]['axes'][0]
        self.assertEqual(data['title']['points'][0]['value'], 'USD')
        self.assertEqual(data['display_units'], {'builtInUnit': 'millions'})
        self.assertEqual(data['number_format'], '$0.00')

    def test_chart_space_extensions_report_gap_but_empty_list_does_not(self):
        extension = '<c:extLst><c:ext uri="urn:future"><future:data xmlns:future="urn:future">hidden</future:data></c:ext></c:extLst>'
        for extra, expected in ((extension, True), ('<c:extLst/>', False)):
            markup = chart_xml().replace(b'</c:chartSpace>', (extra + '</c:chartSpace>').encode())
            slide = inspect_presentation(deck(chart=markup))['slides'][0]
            self.assertEqual('chart_extensions_not_interpreted' in diagnostics(slide), expected)
            self.assertEqual(slide['charts'][0]['series'][0]['values']['points'][0]['value'], '1.2500')

    def test_notes_chart_relationship_uses_notes_owner(self):
        parts = read_parts(deck(chart=chart_xml(), notes_table=table()))
        slide_name, notes_name = 'ppt/slides/slide1.xml', 'ppt/notesSlides/notesSlide1.xml'
        slide = ET.fromstring(parts[slide_name])
        chart_frame = list(slide.find('{' + 'http://schemas.openxmlformats.org/presentationml/2006/main' + '}cSld')[0])[-1]
        note = ET.fromstring(parts[notes_name])
        note.find('{' + 'http://schemas.openxmlformats.org/presentationml/2006/main' + '}cSld')[0].append(chart_frame)
        parts[notes_name] = ET.tostring(note)
        relations_name = 'ppt/notesSlides/_rels/notesSlide1.xml.rels'
        relations = ET.fromstring(parts[relations_name])
        ET.SubElement(relations, '{' + REL_NS + '}Relationship', {'Id': 'rData', 'Type': R_NS + '/chart', 'Target': '../charts/chart1.xml'})
        parts[relations_name] = ET.tostring(relations)
        result = inspect_presentation(zipped(parts))['slides'][0]
        self.assertEqual(result['notes_charts'][0]['source']['part'], notes_name)
        self.assertEqual(result['notes_charts'][0]['series'], result['charts'][0]['series'])


class ContentBoundaryTests(unittest.TestCase):
    """Content gaps must not relax package rejection or alter structural status."""

    def test_budget_is_shared_and_diagnostic_survives_saturation(self):
        with patch('pptx_core.content.MAX_CONTENT_CHARACTERS', 5):
            result = inspect_presentation(deck(frame(table()), chart=chart_xml()))
        self.assertIn('content_limit_exceeded', diagnostics(result['slides'][0]))
        self.assertEqual(result['structural_validation'], 'passed')
        with patch('pptx_core.content.MAX_CONTENT_ITEMS', 30), patch('pptx_core.content.MAX_CONTENT_DIAGNOSTICS', 0):
            result = inspect_presentation(deck(chart=chart_xml()))
        self.assertIn('content_limit_exceeded', diagnostics(result['slides'][0]))

    def test_validate_does_not_run_additive_extraction(self):
        payload = deck(frame(table()), chart=chart_xml())
        with patch('pptx_core.validation.ContentReader', side_effect=AssertionError('must not extract')):
            self.assertEqual(validate_presentation(payload)['structural_validation'], 'passed')

    def test_unsafe_unselected_chart_relationship_still_rejected(self):
        parts = read_parts(deck(chart=chart_xml()))
        name = 'ppt/slides/_rels/slide1.xml.rels'
        relations = ET.fromstring(parts[name])
        for relation in relations:
            if relation.get('Id') == 'rData':
                relation.set('Target', 'https://example.invalid/chart.xml')
                relation.set('TargetMode', 'External')
        parts[name] = ET.tostring(relations)
        with self.assertRaises(ValidationError):
            inspect_presentation(zipped(parts))


if __name__ == '__main__':
    unittest.main()
