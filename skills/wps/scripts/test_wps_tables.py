"""Independent hand-stated Word table matrices; synthetic, not WPS provenance."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wps_ooxml as reader
import wps_ooxml_tables as tables
from test_wps_ooxml import package
from test_wps_ooxml_related import package as related_package, rel, root


def p(text):
    return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'


def tc(text='', properties='', extra=''):
    return '<w:tc><w:tcPr>'+properties+'</w:tcPr>'+p(text)+extra+'</w:tc>'


def tr(cells, properties=''):
    return '<w:tr><w:trPr>'+properties+'</w:trPr>'+cells+'</w:tr>'


def table(rows, columns=2):
    grid='' if columns is None else '<w:tblGrid>'+'<w:gridCol w:w="1000"/>'*columns+'</w:tblGrid>'
    return '<w:tbl>'+grid+rows+'</w:tbl>'


@unittest.skipUnless(reader.expat.version_info >= (2,6,0), 'requires Expat >=2.6')
class TableTests(unittest.TestCase):
    def read(self, xml):
        return reader.read_bytes(package(xml))

    def test_horizontal_vertical_matrix_and_empty_cell(self):
        # Three columns: A spans [0,1] for two rows; B/C occupy column 2.
        xml=table(tr(tc('A','<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>')+tc('B'))+
                  tr(tc('','<w:gridSpan w:val="2"/><w:vMerge/>')+tc('C'))+
                  tr(tc('D')+tc('')+tc('E')),3)
        result=self.read(xml)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(result['paragraphs'],['A\tB','\tC','D\t\tE'])
        item=result['tables'][0]
        cells=[c for row in item['rows'] for c in row['cells']]
        self.assertEqual([(c['row'],c['column'],c['grid_span'],c['text']) for c in cells],
                         [(0,0,2,'A'),(0,2,1,'B'),(1,0,2,''),(1,2,1,'C'),(2,0,1,'D'),(2,1,1,''),(2,2,1,'E')])
        self.assertEqual(cells[2]['merge_anchor_id'],cells[0]['id'])
        self.assertEqual(cells[2]['v_merge'],'continue')
        self.assertEqual(cells[0]['v_merge'],'restart')
        self.assertEqual(len({c['id'] for c in cells}),7)

    def test_run_order_and_paragraph_sources(self):
        content='<w:tc><w:p><w:r><w:t>甲</w:t><w:tab/><w:t>A</w:t></w:r><w:r><w:t>乙</w:t><w:br/><w:t>B</w:t></w:r></w:p>'+p('末')+'</w:tc>'
        result=self.read(table(tr(content),1))
        cell=result['tables'][0]['rows'][0]['cells'][0]
        self.assertEqual(cell['text'],'甲\tA乙\nB\n末')
        self.assertEqual([x['text'] for x in cell['paragraphs']],['甲\tA乙\nB','末'])
        self.assertEqual([x['text'] for x in cell['paragraphs'][0]['runs']],['甲\tA','乙\nB'])
        self.assertEqual(cell['source']['part'],'word/document.xml')
        self.assertTrue(cell['source']['path'])

    def test_nested_table_parent_child_and_no_duplicated_text(self):
        inner=table(tr(tc('INNER')),1)
        outer=table(tr(tc('BEFORE',extra=inner+p('AFTER'))),1)
        result=self.read(outer)
        self.assertEqual(result['text'],'BEFORE\nINNER\nAFTER')
        self.assertEqual(len(result['tables']),2)
        parent,child=result['tables']
        cell=parent['rows'][0]['cells'][0]
        self.assertEqual(child['parent_table_id'],parent['id'])
        self.assertEqual(child['parent_cell_id'],cell['id'])
        self.assertEqual(cell['text'],'BEFORE\nAFTER')
        self.assertEqual(cell['nested_table_ids'],[child['id']])
        self.assertEqual(child['depth'],parent['depth']+1)
        self.assertEqual(result['text'].count('INNER'),1)

    def test_missing_grid_preserves_text_and_unknown_coordinates(self):
        result=self.read(table(tr(tc('KEEP')+tc('')),None))
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(result['text'],'KEEP\t')
        self.assertIsNone(result['tables'][0]['grid_columns'])
        self.assertIsNone(result['tables'][0]['rows'][0]['cells'][0]['column'])
        self.assertTrue(result['diagnostics'])

    def test_empty_table_and_empty_cell_are_distinct(self):
        result=self.read(table('',1)+table(tr(tc('')),1))
        self.assertEqual(len(result['tables']),2)
        self.assertEqual(result['tables'][0]['rows'],[])
        self.assertEqual(result['tables'][1]['rows'][0]['cells'][0]['text'],'')
        self.assertEqual(result['tables'][1]['rows'][0]['cells'][0]['paragraphs'][0]['text'],'')

    def test_grid_omissions_preserve_logical_offsets(self):
        result=self.read(table(tr(tc('MIDDLE'),'<w:gridBefore w:val="1"/><w:gridAfter w:val="1"/>'),3))
        self.assertEqual(result['text'],'MIDDLE')
        self.assertEqual(result['tables'][0]['rows'][0]['cells'][0]['column'],1)

    def test_invalid_spans_and_bounds_refused(self):
        for value in ('0','-1','a','1000000000'):
            with self.subTest(value=value):
                result=self.read(table(tr(tc('X','<w:gridSpan w:val="'+value+'"/>')),1))
                self.assertIn(result['status'],('corrupt','resource_limit'))
                self.assertIsNone(result['text'])
        result=self.read(table(tr(tc('X','<w:gridSpan w:val="2"/>')),1))
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(result['text'],'X')
        self.assertIn('table_grid_augmentation_not_applied',[x['code'] for x in result['diagnostics']])

    def test_vertical_merge_requires_contiguous_equal_width_anchor(self):
        for rows in (tr(tc('X','<w:vMerge/>')+tc('B')),
                     tr(tc('A','<w:vMerge w:val="restart"/>')+tc('B'))+tr(tc('C')+tc('D'))+tr(tc('','<w:vMerge/>')+tc('E')),
                     tr(tc('A','<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>'))+tr(tc('','<w:vMerge/>')+tc('B'))):
            with self.subTest(rows=rows):
                self.assertEqual(self.read(table(rows,2))['status'],'corrupt')

    def test_related_tables_have_unique_source_and_ids(self):
        body=p('MAIN')+'<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        data=related_package(body,rel('h','header','h.xml'),[('word/h.xml','header',root('header',table(tr(tc('HEAD')),1)))])
        self.assertEqual(reader.read_bytes(data)['tables'],[])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['text'],'MAIN')
        self.assertEqual(result['related_stories'][0]['text'],'HEAD')
        self.assertEqual(len(result['tables']),1)
        self.assertEqual(result['tables'][0]['source']['part'],'word/h.xml')

    def test_related_missing_grid_is_partial_at_story_and_document(self):
        body=p('MAIN')+'<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        data=related_package(body,rel('h','header','h.xml'),[('word/h.xml','header',root('header',table(tr(tc('HEAD')),None)))])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['text'],'MAIN')
        self.assertEqual(result['status'],'partial',result)
        story=result['related_stories'][0]
        self.assertEqual(story['status'],'partial')
        self.assertEqual(story['text'],'HEAD')
        self.assertEqual([x['code'] for x in story['issues']],['table_grid_unknown'])
        self.assertEqual([x['code'] for x in result['diagnostics']].count('table_grid_unknown'),1)
        self.assertIsNone(result['tables'][0]['grid_columns'])

    def test_revision_view_does_not_add_deleted_table(self):
        xml='<w:del>'+table(tr(tc('OLD')),1)+'</w:del>'+table(tr(tc('NEW')),1)
        self.assertEqual(len(self.read(xml)['tables']),1)
        result=reader.read_bytes(package(xml),revisions='all')
        self.assertEqual([t['rows'][0]['cells'][0]['text'] for t in result['tables']],['OLD','NEW'])

    def test_budgets_precede_unbounded_table_output(self):
        data=package(table(tr(tc('AB')),1))
        for name in ('MAX_TABLES','MAX_TABLE_CELLS','MAX_GRID_COLUMNS','MAX_TABLE_DEPTH','MAX_TABLE_TEXT'):
            with self.subTest(budget=name),patch.object(tables,name,0):
                result=reader.read_bytes(data)
                self.assertEqual(result['status'],'resource_limit',result)
                self.assertIsNone(result['text'])

    def test_duplicate_property_and_omitted_grid_mismatch_refused(self):
        for rows in (tr(tc('A','<w:gridSpan w:val="1"/><w:gridSpan w:val="1"/>')),
                     tr(tc('A'),'<w:gridAfter w:val="1"/><w:gridAfter w:val="1"/>')):
            self.assertEqual(self.read(table(rows,1))['status'],'corrupt')

    def test_wrapped_rows_and_cells_preserve_structure_with_legacy_warning(self):
        row='<w:tr><w:sdt><w:sdtContent>'+tc('KEPT')+'</w:sdtContent></w:sdt></w:tr>'
        xml=table('<w:sdt><w:sdtContent>'+row+'</w:sdtContent></w:sdt>',1)
        result=self.read(xml)
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(result['tables'][0]['rows'][0]['cells'][0]['text'],'KEPT')
        self.assertIn('table_wrapped_content_legacy_text_omission',[x['code'] for x in result['diagnostics']])
        self.assertEqual(result['text'],'')  # Existing text contract is not silently rewritten.

    def test_incomplete_row_does_not_invent_empty_cells(self):
        result=self.read(table(tr(tc('ONLY')),3))
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(len(result['tables'][0]['rows'][0]['cells']),1)
        self.assertEqual(result['text'],'ONLY')
        self.assertIn('table_row_grid_incomplete',[x['code'] for x in result['diagnostics']])

    def test_legacy_horizontal_merge_is_raw_and_explicit_partial(self):
        xml=table(tr(tc('LEFT','<w:hMerge w:val="restart"/>')+tc('RIGHT','<w:hMerge/>')),2)
        result=self.read(xml)
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(result['text'],'LEFT\tRIGHT')
        cells=result['tables'][0]['rows'][0]['cells']
        self.assertEqual([x['legacy_h_merge_raw'] for x in cells],['restart','continue'])
        self.assertEqual([x['grid_span'] for x in cells],[1,1])
        self.assertIn('table_legacy_horizontal_merge_not_decoded',[x['code'] for x in result['diagnostics']])
        self.assertEqual(self.read(table(tr(tc('X','<w:hMerge/><w:hMerge/>')),1))['status'],'corrupt')

    def test_cli_json_contract_and_text_compatibility(self):
        data=package(p('START')+table(tr(tc('A')+tc('B')),2))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.wps';path.write_bytes(data)
            command=[sys.executable,'-I','-S','-B',str(Path(__file__).with_name('read_wps.py')),str(path)]
            json_run=subprocess.run(command,capture_output=True,text=True,env={'PATH':''},timeout=10)
            self.assertEqual(json_run.returncode,0,json_run.stderr)
            result=json.loads(json_run.stdout)
            self.assertEqual(result['text'],'START\nA\tB')
            self.assertEqual(len(result['tables']),1)
            text_run=subprocess.run(command+['--format','text'],capture_output=True,text=True,env={'PATH':''},timeout=10)
            self.assertEqual(text_run.returncode,0,text_run.stderr)
            self.assertEqual(text_run.stdout,'START\nA\tB')

if __name__=='__main__': unittest.main()
