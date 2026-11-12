"""Regression tests for compact, scoped and resumable OFD text reading."""
import json
import importlib.util
import hashlib
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import tempfile
import unittest
import zipfile

import test_ofd as BASE
READER = BASE.READER
REPO = Path(__file__).resolve().parents[4]
PACKAGER_SPEC = importlib.util.spec_from_file_location('package_skills', REPO / 'tools' / 'package_skills.py')
PACKAGER = importlib.util.module_from_spec(PACKAGER_SPEC)
PACKAGER_SPEC.loader.exec_module(PACKAGER)


class OfdReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ofd-reading-')
        self.path = Path(self.temp.name) / 'source.ofd'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, files):
        BASE.write_package(self.path, files)

    def multi_page(self, first='正文甲😀', second='正文乙'):
        files = BASE.fixture(text=first)
        files['Doc/Document.xml'] = BASE.xml(
            'Document', '<ofd:CommonData/><ofd:Pages>'
            '<ofd:Page ID="1" BaseLoc="Pages/P.xml"/>'
            '<ofd:Page ID="2" BaseLoc="Pages/Q.xml"/>'
            '</ofd:Pages>')
        files['Doc/Pages/Q.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>'
            + second + '</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
        return files

    def cli(self, *args):
        import subprocess
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(Path(__file__).parents[1] / 'read_ofd.py'), *args],
            cwd=self.path.parent, capture_output=True, text=True, encoding='utf-8', check=False)

    def test_compact_reading_separates_template_and_page_text(self):
        files = BASE.fixture(text='正文', page_extra='<ofd:Template TemplateID="9"/>',
                             common_extra='<ofd:TemplatePage ID="9" BaseLoc="T.xml"/>',
                             extra={'Doc/T.xml': BASE.xml(
                                 'Page', '<ofd:Content><ofd:Layer><ofd:TextObject>'
                                 '<ofd:TextCode>页眉</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')})
        self.write(files)
        all_result = self.cli('./source.ofd', '--view', 'reading', '--document', '1', '--page', '1', '--reading-format', 'detail')
        self.assertEqual(all_result.returncode, 0)
        view = json.loads(all_result.stdout)
        self.assertEqual([record['text'] for record in view['records']], ['页眉', '正文'])
        self.assertEqual([record['template'] for record in view['records']], [True, False])
        page_result = self.cli('./source.ofd', '--view', 'reading', '--document', '1', '--page', '1',
                               '--text-layer', 'page', '--reading-format', 'detail')
        page_view = json.loads(page_result.stdout)
        self.assertEqual([record['text'] for record in page_view['records']], ['正文'])
        self.assertEqual(page_view['excluded_occurrences'], 1)
        self.assertEqual(page_view['coverage'], 'selected_page_text_occurrences_only')

    def test_scoped_parse_does_not_read_unselected_page_xml(self):
        files = self.multi_page()
        files['Doc/Pages/Q.xml'] = b'<!DOCTYPE x SYSTEM "https://example.invalid/no"><x/>'
        self.write(files)
        selected = self.cli('./source.ofd', '--view', 'reading', '--document', '1', '--page', '1', '--reading-format', 'detail')
        self.assertEqual(selected.returncode, 0, selected.stdout)
        result = json.loads(selected.stdout)
        self.assertEqual(result['records'][0]['text'], '正文甲😀')
        self.assertEqual(result['unexamined_member_count'], 1)
        self.assertTrue(result['omitted_member_inventory'])
        full = self.cli('./source.ofd', '--view', 'full')
        self.assertEqual(full.returncode, 1)

    def test_large_unexamined_inventory_does_not_fill_reading_view(self):
        files = BASE.fixture(text='正文')
        files.update({f'opaque/member-{index}.bin': b'x' for index in range(500)})
        self.write(files)
        result = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        view = READER.project_reading(result, document=1, page=1)
        self.assertEqual(view['unexamined_member_count'], 500)
        self.assertEqual(view['parsed_member_count'], 3)
        self.assertTrue(view['omitted_member_inventory'])
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False)) + 1, 12000)

    def test_content_format_shows_title_and_text_with_partial_diagnostics(self):
        """Expose a document title without repeating source fields per text run."""
        files = BASE.fixture(text='正文😀', page_extra='<ofd:UnknownFeature/>')
        files['OFD.xml'] = files['OFD.xml'].replace(
            b'<ofd:DocBody>', '<ofd:DocBody><ofd:DocInfo><ofd:Title>公文标题</ofd:Title></ofd:DocInfo>'.encode())
        self.write(files)
        args = ('source.ofd', '--view', 'reading', '--document', '1', '--page', '1')
        response = self.cli(*args, '--reading-format', 'content')
        self.assertEqual(response.returncode, 3, response.stdout)
        content = json.loads(response.stdout)
        detail = json.loads(self.cli(*args, '--reading-format', 'detail').stdout)
        self.assertEqual(content['schema_version'], 'ofd-content-2')
        self.assertEqual(content['title'], '公文标题')
        self.assertEqual(content['records'], [{'page': 1, 'character_offset': 0, 'text': '正文😀'}])
        self.assertEqual(content['diagnostics'], detail['diagnostics'])
        self.assertNotIn('parsed_member_count', content)
        self.assertNotIn('unexamined_member_count', content)
        self.assertLess(len(response.stdout), len(json.dumps(detail, ensure_ascii=False)))

    def test_content_keeps_scan_diagnostics_and_skips_structure_catalogue(self):
        from unittest.mock import patch
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:ImageObject ResourceID="1"/>'
            '</ofd:Layer></ofd:Content>')
        self.write(files)
        with patch.object(READER, 'catalogue_structure', side_effect=AssertionError('full catalogue called')):
            result = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        self.assertEqual(result['structure']['status'], 'not_read')
        response = self.cli('./source.ofd', '--view', 'reading', '--document', '1', '--page', '1',
                            '--reading-format', 'content')
        self.assertEqual(response.returncode, 3, response.stdout)
        view = json.loads(response.stdout)
        self.assertIn('image_not_read', [item['code'] for item in view['diagnostics']])
        self.assertNotIn('structure', view)

    def test_cli_and_api_default_to_content_with_explicit_full_compatibility(self):
        self.write(BASE.fixture(text='兼容'))
        default = self.cli('./source.ofd')
        reading = self.cli('./source.ofd', '--view', 'reading')
        explicit = self.cli('./source.ofd', '--view', 'full')
        self.assertEqual(default.returncode, 0)
        self.assertEqual(json.loads(default.stdout), json.loads(reading.stdout))
        self.assertEqual(json.loads(default.stdout), READER.read_ofd(self.path))
        self.assertEqual(json.loads(default.stdout)['schema_version'], 'ofd-content-2')
        self.assertNotIn('structure', json.loads(default.stdout))
        self.assertEqual(json.loads(explicit.stdout), READER.read_ofd_structure(self.path))
        self.assertEqual(json.loads(explicit.stdout), READER.read_ofd(self.path, view='full'))
        self.assertIn('structure', json.loads(explicit.stdout))

    def test_content_counts_support_traversing_all_documents_and_pages(self):
        files = self.multi_page()
        files['OFD.xml'] = BASE.xml(
            'OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>'
            '<ofd:DocBody><ofd:DocRoot>Doc/Document2.xml</ofd:DocRoot></ofd:DocBody>',
            'Version="1.0" DocType="OFD"')
        files['Doc/Document2.xml'] = BASE.xml(
            'Document', '<ofd:CommonData/><ofd:Pages><ofd:Page ID="4" BaseLoc="Second.xml"/>'
            '</ofd:Pages>')
        files['Doc/Second.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>第二份</ofd:TextCode>'
            '</ofd:TextObject></ofd:Layer></ofd:Content>')
        self.write(files)
        for document, page, count, expected in ((1, 1, 2, '正文甲😀'), (1, 2, 2, '正文乙'),
                                                 (2, 1, 1, '第二份')):
            response = self.cli('./source.ofd', '--view', 'reading', '--document', str(document),
                                '--page', str(page), '--reading-format', 'content')
            self.assertEqual(response.returncode, 0, response.stdout)
            view = json.loads(response.stdout)
            self.assertEqual(view['scope']['document_count'], 2)
            self.assertEqual(view['scope']['page_count'], count)
            self.assertEqual(view['records'][0]['text'], expected)
            self.assertTrue(view['done'])

    def test_content_format_continues_when_title_exceeds_budget(self):
        """Finish reading a long run under a small response budget."""
        text = '正文😀' * 600
        files = BASE.fixture(text=text)
        title = '<ofd:DocInfo><ofd:Title>' + '长' * 400 + '</ofd:Title></ofd:DocInfo>'
        files['OFD.xml'] = files['OFD.xml'].replace(b'<ofd:DocBody>', ('<ofd:DocBody>' + title).encode())
        self.write(files)
        args = ('source.ofd', '--view', 'reading', '--document', '1', '--page', '1',
                '--reading-format', 'content', '--max-output-chars', '900')
        chunks = []
        cursor_args = ()
        for _ in range(12):
            response = self.cli(*args, *cursor_args)
            self.assertEqual(response.returncode, 0, response.stdout)
            self.assertLessEqual(len(response.stdout), 900)
            view = json.loads(response.stdout)
            self.assertNotIn('diagnostics', view)
            self.assertTrue(view['title_omitted'])
            self.assertIsNone(view['title'])
            for record in view['records']:
                self.assertEqual(record['character_offset'], sum(map(len, chunks)))
                chunks.append(record['text'])
            if view['done']:
                break
            cursor_args = ('--cursor', view['next_cursor'])
        self.assertTrue(view['done'])
        self.assertEqual(''.join(chunks), text)

    def test_page_range_preserves_original_page_numbers(self):
        self.write(self.multi_page())
        result = self.cli('./source.ofd', '--view', 'reading', '--document', '1', '--page', '2')
        self.assertEqual(result.returncode, 0, result.stdout)
        view = json.loads(result.stdout)
        self.assertEqual(view['records'][0]['page'], 2)
        self.assertEqual(view['scope']['page'], 2)
        parsed_page_one = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        with self.assertRaisesRegex(READER.ReadError, 'not parsed'):
            READER.project_reading(parsed_page_one, document=1, page=2)

    def test_range_selects_second_document_without_renumbering(self):
        files = BASE.fixture(text='第一份')
        files['OFD.xml'] = BASE.xml(
            'OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>'
            '<ofd:DocBody><ofd:DocRoot>Doc/Second.xml</ofd:DocRoot></ofd:DocBody>',
            'Version="1.0" DocType="OFD"')
        files['Doc/Second.xml'] = BASE.xml(
            'Document', '<ofd:CommonData/><ofd:Pages><ofd:Page ID="4" BaseLoc="Second.xml"/>'
            '</ofd:Pages>')
        files['Doc/Second.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>第二份</ofd:TextCode>'
            '</ofd:TextObject></ofd:Layer></ofd:Content>')
        files['Doc/Document2.xml'] = BASE.xml(
            'Document', '<ofd:CommonData/><ofd:Pages><ofd:Page ID="4" BaseLoc="Second.xml"/>'
            '</ofd:Pages>')
        files['OFD.xml'] = BASE.xml(
            'OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>'
            '<ofd:DocBody><ofd:DocRoot>Doc/Document2.xml</ofd:DocRoot></ofd:DocBody>',
            'Version="1.0" DocType="OFD"')
        self.write(files)
        result = READER.read_ofd_structure(self.path, document=2, page_start=1, page_end=1)
        view = READER.project_reading(result, document=2, page=1)
        self.assertEqual(view['records'][0]['document'], 2)
        self.assertEqual(view['records'][0]['text'], '第二份')

    def test_long_run_continues_and_reassembles_unicode(self):
        text = ('甲😀é\n' * 3000)
        self.write(BASE.fixture(text=text))
        result = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        cursor = None
        chunks = []
        seen_offsets = []
        for _ in range(100):
            view = READER.project_reading(result, document=1, page=1, max_chars=1300, cursor=cursor)
            chunks.extend(record['text'] for record in view['records'])
            seen_offsets.extend(record['character_offset'] for record in view['records'])
            if view['done']:
                break
            cursor = view['next_cursor']
        self.assertTrue(view['done'])
        self.assertEqual(''.join(chunks), text)
        self.assertEqual(seen_offsets[0], 0)
        self.assertTrue(all(offset == 0 or offset > 0 for offset in seen_offsets))

    def test_cursor_rejects_damage_changed_source_and_scope(self):
        self.write(BASE.fixture(text='乙' * 12000))
        result = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        view = READER.project_reading(result, document=1, page=1, max_chars=2048)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False)) + 1, 2048)
        cursor = view['next_cursor']
        damaged = cursor[:-1] + ('A' if cursor[-1] != 'A' else 'B')
        with self.assertRaises(READER.ReadError):
            READER.project_reading(result, document=1, page=1, cursor=damaged, max_chars=2048)
        malformed = READER._make_cursor({'invalid': True})
        with self.assertRaises(READER.ReadError):
            READER.project_reading(result, document=1, page=1, cursor=malformed, max_chars=2048)
        with self.assertRaisesRegex(READER.ReadError, 'scope'):
            READER.project_reading(result, document=1, page=1, page_end=1,
                                   text_layer='template', cursor=cursor, max_chars=2048)
        BASE.write_package(self.path, BASE.fixture(text='丙' * 12000))
        changed = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=1)
        with self.assertRaisesRegex(READER.ReadError, 'source'):
            READER.project_reading(changed, document=1, page=1, cursor=cursor, max_chars=2048)

    def test_range_and_diagnostic_budgets_fail_without_truncation(self):
        self.write(BASE.fixture(text='正文', page_extra='<ofd:UnknownFeature/>'))
        result = READER.read_ofd_structure(self.path, document=1, page_start=1)
        with self.assertRaisesRegex(READER.ReadError, 'diagnostics'):
            READER.project_reading(result, document=1, page=1, max_chars=512)
        with self.assertRaisesRegex(READER.ReadError, 'page index'):
            READER.read_ofd_structure(self.path, document=1, page_start=9)

    def test_cross_page_resume_and_budget_boundaries(self):
        self.write(self.multi_page(first='甲' * 2000, second='乙' * 2000))
        result = READER.read_ofd_structure(self.path, document=1, page_start=1, page_end=2)
        for budget in (1100, 1300, 2048, 12000):
            chunks = {1: [], 2: []}
            cursor = None
            for _ in range(200):
                view = READER.project_reading(result, document=1, page=1, page_end=2,
                                              max_chars=budget, cursor=cursor)
                self.assertLessEqual(len(json.dumps(view, ensure_ascii=False)) + 1, budget)
                for record in view['records']:
                    chunks[record['page']].append(record['text'])
                if view['done']:
                    break
                self.assertTrue(view['records'])
                self.assertNotEqual(cursor, view['next_cursor'])
                cursor = view['next_cursor']
            self.assertTrue(view['done'])
            self.assertEqual(''.join(chunks[1]), '甲' * 2000)
            self.assertEqual(''.join(chunks[2]), '乙' * 2000)

    def test_unsafe_unselected_member_is_still_rejected(self):
        files = BASE.fixture()
        files['../unsafe.xml'] = b'<x/>'
        self.write(files)
        response = self.cli('source.ofd', '--view', 'reading', '--document', '1', '--page', '1')
        self.assertEqual(response.returncode, 1)
        self.assertNotIn('records', json.loads(response.stdout))

    def test_cursor_invalid_types_and_out_of_range(self):
        self.write(BASE.fixture(text='甲' * 2000))
        result = READER.read_ofd_structure(self.path, document=1, page_start=1)
        view = READER.project_reading(result, document=1, page=1, max_chars=1300)
        payload = READER._cursor_payload(view['next_cursor'])
        for invalid in ([], 1, dict(payload, index=999), dict(payload, offset=-1),
                        dict(payload, document=True)):
            with self.assertRaises(READER.ReadError):
                READER.project_reading(result, document=1, page=1,
                                       cursor=READER._make_cursor(invalid))

    def test_reading_runs_from_built_read_only_package(self):
        with tempfile.TemporaryDirectory(prefix='ofd-runtime-reading-') as directory:
            root = Path(directory)
            archive_path = root / 'ofd-runtime.zip'
            install = root / 'install'
            work = root / 'work'
            work.mkdir()
            PACKAGER.build_package(REPO / 'skills' / 'ofd', archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(install)
            installed_skill = install / 'ofd'
            for path in sorted(installed_skill.rglob('*'), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
            BASE.write_package(work / 'input.ofd', BASE.fixture(text='独立精简读取'))
            import subprocess
            before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in installed_skill.rglob('*') if path.is_file()}
            for options in ([], [], ['--view', 'full'], ['--reading-format', 'detail']):
                response = subprocess.run(
                    [sys.executable, '-I', '-S', '-B',
                     str(installed_skill / 'scripts' / 'read_ofd.py'), 'input.ofd', *options],
                    cwd=work, env={'PATH': ''}, capture_output=True, text=True, encoding='utf-8', check=False)
                self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
                view = json.loads(response.stdout)
                if options == ['--view', 'full']:
                    self.assertIn('structure', view)
                else:
                    self.assertEqual(view['records'][0]['text'], '独立精简读取')
                    self.assertNotIn('structure', view)
            after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in installed_skill.rglob('*') if path.is_file()}
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
