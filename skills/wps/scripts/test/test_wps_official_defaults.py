"""Official preset controls, explicit overrides and actual CLI content readback."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import json
import re
import subprocess
import tempfile
import unittest

from read_wps import read_bytes
from run_wps_task import create_document
from wps_rtf_builder import build_document_from_spec, escape_rtf_text


SCRIPT = (Path(__file__).resolve().parents[1] / 'run_wps_task.py')
TEXT = '中英文 ABC 123 café 😀'


class OfficialDefaultsTests(unittest.TestCase):
    def test_default_geometry_body_and_composite_fonts(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': TEXT}]}
        raw = build_document_from_spec(spec).render()
        self.assertEqual(raw, build_document_from_spec(dict(spec, theme='official')).render())
        for control in (r'\paperw11906\paperh16838', r'\margl1803\margr1803', r'\margt1440\margb1440',
                        r'\pard\ql', r'\sl-560\slmult0', r'\fi640\cufi200', r'\hich\af5\dbch\af2\loch\f5\fs32'):
            self.assertIn(control, raw)
        self.assertNotIn(r'\footer', raw)
        self.assertNotIn(r'\header', raw)
        result = read_bytes(raw.encode('ascii'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['paragraphs'][0]['text'], TEXT)

    def test_titles_and_long_document_do_not_insert_new_pages(self):
        blocks = [{'type': 'title', 'text': '主标题'}, {'type': 'subtitle', 'text': '副标题'}]
        blocks += [{'type': 'heading', 'level': level, 'text': f'标题{level}'} for level in range(1, 10)]
        blocks += [{'type': 'paragraph', 'text': f'{index} {TEXT}'} for index in range(100)]
        document = build_document_from_spec({'blocks': blocks})
        raw = document.render()
        self.assertFalse(re.search(r'\\(?:page|pagebb|sect)\b', raw))
        self.assertEqual(raw.count(r'\keepn'), 11)
        self.assertIn(escape_rtf_text('方正小标宋简体'), raw)
        self.assertIn(r'\dbch\af9\loch\f5\fs44', document.elements[0].to_rtf())
        self.assertNotIn(r'\b ', document.elements[0].to_rtf())
        self.assertIn(r'\pard\qc', document.elements[0].to_rtf())
        self.assertIn(r'\dbch\af9\loch\f5\fs28', document.elements[1].to_rtf())
        for index, font, size, alignment, bold in ((2, 1, 32, 'ql', False), (3, 3, 32, 'ql', False),
                                                  (4, 2, 32, 'ql', False), (5, 2, 32, 'ql', False)):
            paragraph = document.elements[index].to_rtf()
            self.assertIn(f'\\{alignment}\\sl276\\slmult1', paragraph)
            self.assertIn(f'\\dbch\\af{font}\\loch\\f5\\fs{size}', paragraph)
            self.assertEqual(bool(re.search(r'\\b(?:\s|\\)', paragraph)), bold)
        self.assertEqual(len(read_bytes(raw.encode('ascii'))['paragraphs']), len(blocks))

    def test_explicit_page_numbers_and_running_content(self):
        for page_numbers in (True, False):
            spec = {'page_numbering': page_numbers, 'header': '页眉 ABC', 'footer': '页脚',
                    'blocks': [{'type': 'paragraph', 'text': '正文'}]}
            raw = build_document_from_spec(spec).render()
            self.assertIn(r'{\header \pard\ql\sl276\slmult1', raw)
            self.assertIn(r'{\footer \pard\ql\sl276\slmult1', raw)
            self.assertEqual(r'\fldinst PAGE' in raw, page_numbers)
            self.assertIn(r'\dbch\af2\loch\f5\fs32', raw)
            parsed = read_bytes(raw.encode('ascii'))
            self.assertEqual(parsed['status'], 'partial')
            self.assertEqual(parsed['paragraphs'][0]['text'], '正文')
        empty = build_document_from_spec({'header': '', 'blocks': [{'type': 'paragraph', 'text': '正文'}]}).render()
        self.assertIn(r'{\header ', empty)
        aliased = build_document_from_spec({'header_text': None, 'header': '保留别名', 'blocks': [{'type': 'paragraph', 'text': '正文'}]}).render()
        self.assertIn(escape_rtf_text('保留别名'), aliased)

    def test_custom_measurements_fonts_and_breaks_take_precedence(self):
        spec = {'page': {'width_pt': 600, 'height_pt': 800, 'margin_left_pt': 40, 'margin_top_pt': 50}, 'blocks': [
            {'type': 'heading', 'level': 1, 'text': '新页', 'align': 'right', 'page_break_before': True, 'keep_with_next': False},
            {'type': 'paragraph', 'runs': [{'text': '默认'}, {'text': '显式', 'font': '宋体', 'size_pt': 10}],
             'align': 'justify', 'line_spacing_exact_pt': 18, 'space_before_pt': 3, 'space_after_pt': 4,
             'first_line_indent_pt': -12, 'keep_together': True, 'widow_control': False},
            {'type': 'page_break'}, {'type': 'paragraph', 'text': '末段', 'line_spacing_mult': 1.5},
        ]}
        document = build_document_from_spec(spec)
        raw = document.render()
        for control in (r'\paperw12000\paperh16000', r'\margl800', r'\margt1000', r'\pagebb', r'\sl-360\slmult0',
                        r'\sb60\sa80', r'\fi-240', r'\nowidctlpar', r'\sl360\slmult1'):
            self.assertIn(control, raw)
        heading, paragraph = document.elements[:2]
        self.assertNotIn(r'\keepn', heading.to_rtf())
        self.assertIn(r'\qr', heading.to_rtf())
        self.assertNotIn(r'\cufi', paragraph.to_rtf())
        self.assertIn('}\\f0\\fs20 ', paragraph.to_rtf())
        self.assertEqual(len(re.findall(r'\\page\b', raw)), 1)
        self.assertEqual(len(re.findall(r'\\pagebb\b', raw)), 1)
        self.assertEqual(document.spec_warning_count, 0)
        self.assertIn('默认显式', read_bytes(raw.encode('ascii'))['text'])

    def test_table_and_list_avoid_body_indent_and_fixed_line_spacing(self):
        document = build_document_from_spec({'blocks': [
            {'type': 'table', 'rows': [['列', '金额'], ['甲', '100']], 'col_alignments': ['left', 'right']},
            {'type': 'list', 'ordered': True, 'start': 3, 'items': ['事项甲', '事项乙']},
        ]})
        table = document.elements[0].to_rtf()
        self.assertNotIn(r'\sl-560', table)
        self.assertNotIn(r'\fi640', table)
        self.assertEqual(table.count('\\trkeep\\'), 2)
        self.assertIn(r'\fs32', table)
        self.assertIn(r'\sl276\slmult1', table)
        self.assertNotIn(r'\clcbpat6', table)
        parsed = read_bytes(document.render().encode('ascii'))
        self.assertEqual([[cell['text'] for cell in row] for row in parsed['tables'][0]['rows']], [['列', '金额'], ['甲', '100']])
        self.assertIn('3. 事项甲', parsed['text'])
        custom = build_document_from_spec({'blocks': [{'type': 'table', 'rows': [['甲'], ['乙']],
                                                       'row_keep_together': False, 'repeat_header': False}]}).render()
        self.assertNotRegex(custom, r'\\trkeep(?:\\|\s)')
        self.assertNotIn(r'\trhdr', custom)

    def test_explicit_font_fallback_is_disclosed(self):
        with tempfile.TemporaryDirectory(prefix='wps-official-test-') as directory:
            target = Path(directory) / 'font.wps'
            spec = {'blocks': [{'type': 'paragraph', 'runs': [{'text': '保留文字', 'font': 'unknown-family'}]}]}
            receipt = create_document(spec, target, compact_receipt=True)
            self.assertTrue(receipt['partial'])
            self.assertEqual(receipt['spec_warnings'][0]['code'], 'unsupported_font_substituted')
            self.assertEqual(read_bytes(target.read_bytes())['paragraphs'][0]['text'], '保留文字')
            spec['blocks'][0]['runs'][0]['font'] = '方正小标宋简体'
            supported = build_document_from_spec(spec)
            self.assertEqual(supported.spec_warning_count, 0)
            self.assertIn(r'\f9\fs32', supported.render())

    def test_other_themes_and_legacy_reading_remain_supported(self):
        for theme, body_size, spacing in (('business', 22, 300), ('general', 24, 288)):
            raw = build_document_from_spec({'theme': theme, 'blocks': [{'type': 'paragraph', 'text': '旧主题'}]}).render()
            self.assertIn(r'\fldinst PAGE', raw)
            self.assertIn(f'\\fs{body_size}', raw)
            self.assertIn(f'\\sl{spacing}\\slmult1', raw)
        legacy = br'{\rtf1\ansi\margt2098\margb1984\margl1588\margr1474\pard\qj\sl-576\slmult0\fi560\f2\fs28 old\par}'
        self.assertEqual(read_bytes(legacy)['paragraphs'][0]['text'], 'old')

    def test_actual_cli_creates_readable_default_and_custom_files(self):
        with tempfile.TemporaryDirectory(prefix='wps-official-cli-') as directory:
            for name, extra in (('default.wps', {}), ('custom.rtf', {'page_numbering': True})):
                spec = {'blocks': [{'type': 'title', 'text': '标题'}, {'type': 'paragraph', 'text': TEXT}], **extra}
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), 'create', name, '--compact-receipt'],
                                        input=json.dumps(spec), text=True, capture_output=True, cwd=directory, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['content_validation']['status'], 'verified_within_scope')
                self.assertEqual(receipt['layout_verification'], 'not_run')
                self.assertFalse(receipt['native_wps'])
                self.assertEqual(receipt['partial'], bool(extra))
                self.assertEqual(read_bytes((Path(directory) / name).read_bytes())['paragraphs'][-1]['text'], TEXT)
            self.assertEqual({path.name for path in Path(directory).iterdir()}, {'default.wps', 'custom.rtf'})


if __name__ == '__main__':
    unittest.main()
