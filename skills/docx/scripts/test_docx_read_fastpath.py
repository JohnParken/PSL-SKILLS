"""Stdlib-only read fastpath correctness and bounded output regressions."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from read_docx import read_docx, read_docx_structure, read_docx_compact

N = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def para(text, *, style=None):
    s = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{s}<w:r><w:t>{text}</w:t></w:r></w:p>'

class ReadFastpathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='read-fast-', dir='.')
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.path = self.folder/'sample.docx'
        table_rows = ''.join(f'<w:tr><w:tc>{para(str(i))}</w:tc><w:tc>{para("产品"+str(i))}</w:tc></w:tr>' for i in range(42))
        body = (para('封面') + para('第一部分', style='Heading1') + para('引言')
                + para('相同标题',style='Heading2') + para('第一段')
                + f'<w:tbl>{table_rows}</w:tbl>' + para('相同标题', style='Heading2')
                + para('第二段') + para('第二部分', style='Heading1') + para('结尾'))
        document = f'<w:document xmlns:w="{N}"><w:body>{body}<w:sectPr/></w:body></w:document>'
        styles = (
            f'<w:styles xmlns:w="{N}">'
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/>'
            '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading2">'
            '<w:name w:val="标题 2"/><w:basedOn w:val="Heading1"/>'
            '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style></w:styles>'
        )
        with zipfile.ZipFile(self.path,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml',document)
            z.writestr('word/styles.xml',styles)
        self.hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def cli(self,*args):
        return subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'read_docx.py'),self.path.name,*args], cwd=self.folder,text=True,capture_output=True)

    def test_summary_counts_only_and_includes_headings(self):
        data = read_docx_compact(self.path,mode='summary')
        self.assertEqual(data['stats']['paragraph_count'],9)
        self.assertEqual(data['stats']['table_count'],1)
        self.assertEqual(data['stats']['table_row_count'],42)
        self.assertEqual(data['stats']['heading_count'],4)
        self.assertEqual([h['level'] for h in data['blocks']], [1,2,2,1])
        self.assertNotIn('产品42',repr(data))

    def test_headings_cli_has_level_and_paragraph_index(self):
        r=self.cli('--heading-only')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('[H1] [p2] 第一部分',r.stdout)
        self.assertIn('[H2] [p4] 相同标题',r.stdout)
        self.assertNotIn('第一段',r.stdout)

    def test_paragraph_range_is_one_based_and_excludes_table_cells(self):
        r=self.cli('--paragraph-range','3:5')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout.splitlines(), ['引言','相同标题','第一段'])
        self.assertNotIn('产品',r.stdout)

    def test_section_respects_heading_levels(self):
        r=self.cli('--section','第一部分')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('第一段',r.stdout)
        self.assertIn('产品41',r.stdout)  # plain section reads complete table
        self.assertIn('第二段',r.stdout)
        self.assertNotIn('第二部分',r.stdout)
        self.assertNotIn('结尾',r.stdout)

    def test_duplicate_section_requires_occurrence(self):
        r=self.cli('--section','相同标题')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('ambiguous',r.stderr)
        r=self.cli('--section','相同标题','--section-occurrence','2')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('第二段',r.stdout)
        self.assertNotIn('第一段',r.stdout)

    def test_missing_section_rejects_without_fallback(self):
        self.assertNotEqual(self.cli('--section','并不存在').returncode,0)

    def test_compact_structure_has_no_raw_xml(self):
        r=self.cli('--compact-json')
        self.assertEqual(r.returncode,0,r.stderr)
        data=json.loads(r.stdout)
        self.assertEqual(data['format'],'docx-compact')
        self.assertNotIn('xmlns',r.stdout)
        self.assertNotIn('word/styles.xml',r.stdout)
        self.assertEqual(data['blocks'][5]['type'],'table')
        self.assertEqual(data['blocks'][5]['row_count'],42)
        self.assertEqual(len(data['blocks'][5]['rows']),2)

    def test_compact_large_table_rows_paged_without_skipping(self):
        all_rows=[]
        cursor=0
        while True:
            r=self.cli('--compact-json','--tables','rows','--max-output-chars','1100','--start-block',str(cursor))
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertLessEqual(len(r.stdout.rstrip('\n')),1100)
            data=json.loads(r.stdout)
            self.assertEqual(data['start_block'],cursor)
            all_rows.extend(e['row_index'] for e in data['blocks'] if e['type']=='table_row')
            if data['next_block'] is None:
                self.assertFalse(data['truncated'])
                break
            self.assertTrue(data['truncated'])
            self.assertGreater(data['next_block'],cursor)
            cursor=data['next_block']
        self.assertEqual(all_rows,list(range(1,43)))

    def test_plain_text_truncation_explicit(self):
        r=self.cli('--max-output-chars','180')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertLessEqual(len(r.stdout),180)
        self.assertIn('[TRUNCATED:',r.stdout)

    def test_full_structure_limit_refuses_instead_of_corrupt_json(self):
        r=self.cli('--format','json','--max-output-chars','1000')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('--compact-json',r.stderr)
        self.assertEqual(read_docx_structure(self.path)['format'],'docx-structure')

    def test_limits_and_selector_conflicts_fail(self):
        for args in (('--max-output-chars','30'),('--paragraph-range','2:1'),
                     ('--section','第一部分','--include-related'),
                     ('--section-occurrence','1'),('--start-block','2'),
                     ('--tables','rows'),('--heading-only','--format','json')):
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args).returncode,0)

    def test_old_text_interface_unchanged(self):
        old=read_docx(self.path)
        r=self.cli()
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout,old)
        self.assertIn('产品41',old)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.hash)

    def test_outline_style_inheritance(self):
        data=read_docx_compact(self.path,mode='headings')
        self.assertEqual(len(data['blocks']),4)
        self.assertEqual(data['blocks'][2]['level'],2)

    def test_read_plan_only_requires_main_entry(self):
        planner = ROOT/'reference_loading_audit.py'
        r=subprocess.run([sys.executable,'-I','-S','-B',str(planner),'plan','--task','read'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual([x['file'] for x in json.loads(r.stdout)['files']],['SKILL.md'])
        r=subprocess.run([sys.executable,'-I','-S','-B',str(planner),'plan','--task','read-advanced'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('references/inspection.md',[x['file'] for x in json.loads(r.stdout)['files']])

    def test_result_json_is_valid_when_bound_exactly(self):
        # The specified character budget includes the trailing newline.
        r=self.cli('--compact-json','--max-output-chars','850')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertLessEqual(len(r.stdout),850)
        self.assertTrue(isinstance(json.loads(r.stdout)['blocks'],list))

    def test_out_of_range_paragraph_has_explicit_error(self):
        r = self.cli('--paragraph-range', '100:200')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('past the end', r.stderr)

    def test_related_summary_does_not_output_full_text(self):
        with zipfile.ZipFile(self.path, 'a') as z:
            z.writestr('word/header1.xml', f'<w:hdr xmlns:w="{N}">{para("机密页眉" * 500)}</w:hdr>')
        r = self.cli('--summary', '--include-related')
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result['related'][0]['category'], 'Headers')
        self.assertGreater(result['related'][0]['text_chars'], 1000)
        self.assertNotIn('机密页眉', r.stdout)
        r = self.cli('--compact-json', '--include-related')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data['related'][0]['text_preview']), 200)
        self.assertTrue(data['related'][0]['preview_truncated'])

    def test_no_headings_is_explicit(self):
        with zipfile.ZipFile(self.path, 'w') as z:
            z.writestr('word/document.xml', f'<w:document xmlns:w="{N}"><w:body>{para("纯正文")}</w:body></w:document>')
        r = self.cli('--heading-only')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('NO_HEADINGS_RECOGNIZED', r.stdout)

    def test_compact_oversized_block_fails_without_silent_loss(self):
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml', f'<w:document xmlns:w="{N}"><w:body>{para("超长段落" * 1000)}<w:sectPr/></w:body></w:document>')
        r=self.cli('--compact-json','--max-output-chars','900')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('single block exceeds',r.stderr)

if __name__=='__main__':
    unittest.main()
