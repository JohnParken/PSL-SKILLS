"""Exercise the public CLI with isolated task directories and source protection."""

from hashlib import sha256
import json
import os
from pathlib import Path
from struct import pack
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from zlib import compress, crc32

from docx import Document
from docx.oxml.ns import qn

CLI = Path(__file__).resolve().parents[1] / 'tl_docx.py'


def members(path):
    """Snapshot ZIP member contents for independent comparison."""
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def png(pixel):
    """Build a one-pixel RGB PNG with valid chunks without image tooling."""
    def chunk(kind, data):
        return pack('>I', len(data)) + kind + data + pack('>I', crc32(kind + data))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', compress(b'\x00' + pixel)) + chunk(b'IEND', b''))


class WorkflowTests(unittest.TestCase):
    """Verify actual CLI results and produced Word package bytes."""

    def setUp(self):
        task = tempfile.TemporaryDirectory(prefix='tl-docx-cli-')
        self.addCleanup(task.cleanup)
        self.task = Path(task.name)

    def invoke(self, *args, payload=None, expected=0, question_id=None):
        """Execute an isolated public command with a bounded timeout."""
        environ = dict(os.environ, PATH='')
        environ.pop('ROUTER_CONFIG_VARIABLES', None)
        if question_id:
            environ['ROUTER_CONFIG_VARIABLES'] = json.dumps({'commonParam': [{'name': 'questionId', 'value': question_id}]})
        result = subprocess.run([sys.executable, '-I', '-B', str(CLI), *args], cwd=self.task, env=environ,
                                input=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
                                capture_output=True, timeout=20, check=False)
        self.assertEqual(expected, result.returncode, result.stderr.decode())
        return json.loads(result.stdout), result

    def create(self, blocks=None, **options):
        """Create a test document via the same public command as the Skill."""
        blocks = blocks or [{'type': 'paragraph', 'text': '原文字'}]
        receipt, _ = self.invoke('create', 'source.docx', payload={'blocks': blocks}, **options)
        self.assertEqual('verified', receipt['status'])
        self.assertTrue(receipt['publication']['cleanup_complete'])
        return receipt

    def test_check_and_archive(self):
        check, _ = self.invoke('check', '--write')
        self.assertEqual('environment-ok', check['status'])
        self.assertEqual('passed', check['task_write_check'])
        self.assertEqual([], list(self.task.iterdir()))
        receipt = self.create()
        self.assertEqual('failed', receipt['archive_status'])
        archived, _ = self.invoke('archive', 'source.docx', '--sha256', receipt['sha256'], question_id='case')
        self.assertEqual('success', archived['archive_status'])
        self.assertEqual((self.task / 'source.docx').read_bytes(), (self.task / 'output/case/source.docx').read_bytes())
        again, _ = self.invoke('archive', 'source.docx', '--sha256', receipt['sha256'], question_id='case')
        self.assertEqual('already_archived', again['files'][0]['archive_status'])
        self.invoke('archive', 'source.docx', '--sha256', '0' * 64, question_id='other', expected=1)
        self.assertFalse((self.task / 'output/other').exists())

    def test_font_edit_changes_only_expected_package_part(self):
        self.create()
        source = self.task / 'source.docx'
        before = source.read_bytes()
        spec = {'operations': [{'type': 'format_run', 'paragraph_index': 0, 'expected_text': '原文字',
                                'run_index': 0, 'expected_run_text': '原文字', 'format': {'font_east_asia': '黑体', 'size_pt': 18}}]}
        receipt, _ = self.invoke('edit', 'source.docx', 'edited.docx', payload=spec)
        self.assertEqual(['word/document.xml'], receipt['result']['changed_parts'])
        edited = self.task / 'edited.docx'
        original_parts, edited_parts = members(source), members(edited)
        self.assertEqual(original_parts.keys(), edited_parts.keys())
        self.assertEqual(['word/document.xml'], [key for key in original_parts if original_parts[key] != edited_parts[key]])
        old_para, new_para = Document(source).paragraphs[0], Document(edited).paragraphs[0]
        self.assertEqual(old_para.text, new_para.text)
        self.assertEqual(old_para._p.pPr.xml, new_para._p.pPr.xml)  # pylint: disable=protected-access
        run = new_para.runs[0]
        self.assertEqual('黑体', run._r.rPr.rFonts.get(qn('w:eastAsia')))  # pylint: disable=protected-access
        self.assertEqual(18, run.font.size.pt)
        self.assertEqual(before, source.read_bytes())

    def test_guard_and_output_collisions_never_publish(self):
        self.create()
        source = self.task / 'source.docx'
        before = sha256(source.read_bytes()).digest()
        spec = {'operations': [{'type': 'replace_run_text', 'paragraph_index': 0, 'expected_text': '原文字',
                                'run_index': 0, 'expected_run_text': '原文字', 'text': '新字'}]}
        self.invoke('edit', 'source.docx', 'source.docx', payload=spec, expected=1)
        spec['operations'].append({'type': 'insert_paragraph', 'paragraph_index': 0, 'expected_text': '过期', 'text': '禁止发布'})
        self.invoke('edit', 'source.docx', 'edited.docx', payload=spec, expected=1)
        self.assertEqual(before, sha256(source.read_bytes()).digest())
        self.assertEqual(['source.docx'], sorted(item.name for item in self.task.iterdir()))
        (self.task / 'link.docx').symlink_to(source.name)
        self.invoke('read', 'link.docx', expected=1)
        self.invoke('read', '../source.docx', expected=1)

    def test_versioned_names_consider_main_and_current_archive(self):
        spec = {'blocks': [{'type': 'title', 'text': '运营/分析'}, {'type': 'paragraph', 'text': '第一份'}]}
        first, _ = self.invoke('create', payload=spec, question_id='name-test')
        self.assertEqual('运营 分析.docx', first['output'])
        self.assertTrue(first['cleanup']['manifest_removed'])
        first_file = self.task / first['output']
        first_bytes = first_file.read_bytes()
        archive = self.task / 'output/name-test/运营 分析_第2版.docx'
        archive.write_bytes(b'archived beforehand')
        second, _ = self.invoke('create', first['output'], payload=spec, question_id='name-test')
        self.assertEqual('运营 分析_第3版.docx', second['output'])
        self.assertEqual(b'archived beforehand', archive.read_bytes())
        self.assertEqual(first_bytes, first_file.read_bytes())
        self.invoke('create', first['output'], '--exact-name', payload=spec, expected=1)
        next_version, _ = self.invoke('create', second['output'], payload=spec)
        self.assertEqual('运营 分析_第4版.docx', next_version['output'])
        self.assertFalse(list(self.task.glob('.tl-docx-*')))

    def test_explicit_task_id_never_adopts_a_preexisting_manifest(self):
        task_id = 'a' * 32
        existing = self.task / ('.tl-docx-' + task_id + '.cleanup.json')
        existing.write_text('unrelated state', encoding='utf-8')
        spec = {'blocks': [{'type': 'paragraph', 'text': '正文'}]}
        self.invoke('create', 'result.docx', '--task-id', task_id, payload=spec, expected=1)
        self.assertEqual('unrelated state', existing.read_text(encoding='utf-8'))
        self.assertFalse((self.task / 'result.docx').exists())
        success, _ = self.invoke('create', 'result.docx', '--task-id', 'b' * 32, payload=spec)
        self.assertTrue(success['cleanup']['manifest_removed'])
        self.assertIsNone(success['task_manifest'])

    def test_pagination_warnings_are_nonblocking_and_strict_validate_exits_two(self):
        result = self.create([{'type': 'page_break'}, {'type': 'page_break'},
                              {'type': 'heading', 'level': 1, 'text': '末尾标题', 'page_break_before': True, 'keep_with_next': False}])
        warnings = result['result']['layout']['pagination_warnings']
        self.assertTrue({'LEADING_PAGE_BREAK', 'REPEATED_PAGE_BREAK', 'REDUNDANT_HEADING_PAGE_BREAK',
                         'HEADING_WITHOUT_CONTENT', 'HEADING_ORPHAN_RISK'} <= {item['code'] for item in warnings})
        self.assertTrue(any(item.startswith('layout-warning:') for item in result['warnings']))
        original = (self.task / 'source.docx').read_bytes()
        validated, _ = self.invoke('validate', 'source.docx', '--strict-pagination', expected=2)
        self.assertEqual('verified', validated['status'])
        self.assertEqual(warnings, validated['layout']['pagination_warnings'])
        self.assertEqual(original, (self.task / 'source.docx').read_bytes())

    def test_read_pages_reassemble_and_reject_different_source(self):
        blocks = [{'type': 'paragraph', 'text': f'第 {number} 段 ' + '文' * 60} for number in range(25)]
        self.create(blocks)
        full, _ = self.invoke('read', 'source.docx')
        first, result = self.invoke('read', 'source.docx', '--max-output-bytes', '4500')
        self.assertTrue(first['has_more'])
        gathered = list(first['records'])
        page = first
        while page['has_more']:
            self.assertLessEqual(len(result.stdout), 4500)
            page, result = self.invoke('read', 'source.docx', '--max-output-bytes', '4500', '--cursor', page['next_cursor'])
            self.assertEqual(len(gathered), page['start'])
            gathered.extend(page['records'])
        self.assertLessEqual(len(result.stdout), 4500)
        self.assertEqual(full['records'], gathered)
        self.invoke('read', 'source.docx', '--headings', '--cursor', first['next_cursor'], expected=1)
        doc = Document(self.task / 'source.docx')
        doc.add_paragraph('新段')
        doc.save(self.task / 'changed.docx')
        self.invoke('read', 'changed.docx', '--cursor', first['next_cursor'], expected=1)

    def test_select_heading_section_and_direct_paragraph_range(self):
        self.create([{'type': 'heading', 'level': 1, 'text': '第一章'}, {'type': 'paragraph', 'text': '范围内'},
                     {'type': 'table', 'rows': [['同节表格']]}, {'type': 'heading', 'level': 2, 'text': '小节'},
                     {'type': 'paragraph', 'text': '同小节'}, {'type': 'heading', 'level': 1, 'text': '第二章'},
                     {'type': 'paragraph', 'text': '范围外'}])
        selected, _ = self.invoke('read', 'source.docx', '--section', '第一章')
        self.assertIn('table_row', [item['type'] for item in selected['records']])
        self.assertIn('同小节', [item.get('text') for item in selected['records']])
        self.assertNotIn('范围外', [item.get('text') for item in selected['records']])
        excerpt, _ = self.invoke('read', 'source.docx', '--paragraph-range', '2:3')
        self.assertEqual(['范围内', '小节'], [item['text'] for item in excerpt['records']])
        self.invoke('read', 'source.docx', '--paragraph-range', '0:3', expected=1)

    def test_insert_and_delete_regular_table_rows(self):
        self.create([{'type': 'table', 'header': True, 'repeat_header': True, 'rows': [['表头'], ['原行'], ['末行']]}])
        operations = [{'type': 'insert_row', 'table_index': 0, 'row_index': 1, 'expected_rows': 3,
                       'expected_text': ['原行'], 'position': 'after', 'cells': ['新行']},
                      {'type': 'delete_row', 'table_index': 0, 'row_index': 3, 'expected_rows': 4, 'expected_text': ['末行']}]
        self.invoke('edit', 'source.docx', 'rows.docx', payload={'operations': operations})
        result = Document(self.task / 'rows.docx').tables[0]
        self.assertEqual(['表头', '原行', '新行'], [row.cells[0].text for row in result.rows])
        header_delete = {'type': 'delete_row', 'table_index': 0, 'row_index': 0, 'expected_rows': 3, 'expected_text': ['表头']}
        self.invoke('edit', 'source.docx', 'bad.docx', payload={'operations': [header_delete]}, expected=1)

    def test_classic_comment_add_update_delete(self):
        self.create()
        add = {'type': 'add_comment', 'paragraph_index': 0, 'expected_text': '原文字', 'run_index': 0,
               'expected_run_text': '原文字', 'text': '首条', 'author': '测试'}
        self.invoke('edit', 'source.docx', 'one.docx', payload={'operations': [add]})
        update = {'type': 'update_comment', 'comment_id': 0, 'expected_text': '首条', 'text': '修订'}
        self.invoke('edit', 'one.docx', 'two.docx', payload={'operations': [update]})
        self.assertEqual('修订', Document(self.task / 'two.docx').comments.get(0).text)
        delete = {'type': 'delete_comment', 'comment_id': 0, 'expected_text': '修订'}
        self.invoke('edit', 'two.docx', 'three.docx', payload={'operations': [delete]})
        self.assertEqual([], list(Document(self.task / 'three.docx').comments))

    def test_replace_image_only_changes_selected_member(self):
        red, blue = png(b'\xff\x00\x00'), png(b'\x00\x00\xff')
        (self.task / 'red.png').write_bytes(red)
        (self.task / 'blue.png').write_bytes(blue)
        self.create([{'type': 'image', 'path': 'red.png', 'alt': '原图'}])
        data, _ = self.invoke('read', 'source.docx')
        image = next(item for item in data['records'] if item['type'] == 'image')
        part = image['resource_part']
        self.assertEqual(sha256(red).hexdigest(), image['resource_sha256'])
        operation = {'type': 'replace_image', 'part': part, 'expected_sha256': image['resource_sha256'], 'path': 'blue.png'}
        self.invoke('edit', 'source.docx', 'image.docx', payload={'operations': [operation]})
        old, new = (members(self.task / name) for name in ('source.docx', 'image.docx'))
        self.assertEqual([part], [name for name in old if old[name] != new[name]])
        self.assertEqual(blue, new[part])

    def test_dotx_read_and_type_matching(self):
        self.create()
        original = members(self.task / 'source.docx')
        types = '[Content_Types].xml'
        original[types] = original[types].replace(b'wordprocessingml.document.main+xml', b'wordprocessingml.template.main+xml')
        with ZipFile(self.task / 'template.dotx', 'w') as archive:
            for name, content in original.items():
                archive.writestr(name, content)
        result, _ = self.invoke('read', 'template.dotx')
        self.assertTrue(any(item.get('text') == '原文字' for item in result['records']))
        self.invoke('edit', 'template.dotx', 'edited.docx', payload={'operations': []}, expected=1)
        (self.task / 'misnamed.docx').write_bytes((self.task / 'template.dotx').read_bytes())
        self.invoke('read', 'misnamed.docx', expected=1)

    def test_duplicate_member_and_dtd_are_rejected(self):
        self.create()
        original = members(self.task / 'source.docx')
        with ZipFile(self.task / 'duplicate.docx', 'w') as archive:
            for name, data in original.items():
                archive.writestr(name, data)
            archive.writestr('WORD/document.xml', original['word/document.xml'])
        self.invoke('read', 'duplicate.docx', expected=1)
        with ZipFile(self.task / 'dtd.docx', 'w') as archive:
            for name, data in original.items():
                if name == 'word/document.xml':
                    end = data.index(b'?>') + 2
                    data = data[:end] + b'<!DOCTYPE w:document [<!ENTITY local "unsafe">]>' + data[end:]
                archive.writestr(name, data)
        self.invoke('read', 'dtd.docx', expected=1)


if __name__ == '__main__':
    unittest.main()
