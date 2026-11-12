"""Default text API, lightweight parsing and explicit structure migration regressions."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
import test_ofd as BASE

READER = BASE.READER


class DefaultContentTests(unittest.TestCase):
    """Check independently specified text and retained safety boundaries."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ofd-default-content-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'input.ofd'

    def cli(self, *options):
        """Capture the actual command's stdout, stderr and exit code."""
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', READER.__file__, *options],
            cwd=self.temp.name, capture_output=True, text=True, encoding='utf-8', check=False)

    def write(self, files):
        """Create only this test's fixture."""
        BASE.write_package(self.path, files)

    def test_many_fragmented_objects_are_one_page_text_without_structure_budget(self):
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer>'
            + '<ofd:TextObject><ofd:TextCode>甲😀</ofd:TextCode></ofd:TextObject>' * 5000
            + '</ofd:Layer></ofd:Content>')
        self.write(files)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        response = self.cli('input.ofd')
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertEqual(response.stderr, '')
        view = json.loads(response.stdout)
        self.assertLessEqual(len(response.stdout), 12000)
        self.assertEqual(view['records'], [{'page': 1, 'character_offset': 0, 'text': '甲😀' * 5000}])
        self.assertTrue(view['done'])
        with self.assertRaisesRegex(READER.ReadError, 'expanded output'):
            READER.read_ofd_structure(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_reading_does_not_collect_events_node_indexes_or_structural_records(self):
        self.write(BASE.fixture(text='文本'))
        packages = []
        original_package = READER.Package

        def create_package(*args, **kwargs):
            package = original_package(*args, **kwargs)
            packages.append(package)
            return package

        with patch.object(READER, 'Package', side_effect=create_package), patch.object(
                READER, 'catalogue_structure', side_effect=AssertionError('structure requested')):
            view = READER.read_ofd(self.path)
        self.assertEqual(view['records'][0]['text'], '文本')
        package = packages[0]
        self.assertEqual(package.xml_events, {})
        self.assertEqual(package.node_indexes, {})
        self.assertEqual(package.xml_digests, {})
        self.assertEqual(package.evidence, [])
        self.assertGreater(package.event_count, 0)
        self.assertGreater(package.expanded_nodes, 0)

    def test_default_skips_broken_unselected_xml_but_full_still_rejects_it(self):
        files = BASE.fixture(text='第一页')
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Pages>', b'<ofd:Page ID="2" BaseLoc="Pages/Q.xml"/></ofd:Pages>')
        files['Doc/Pages/Q.xml'] = b'<!DOCTYPE x SYSTEM "https://invalid.example"><x/>'
        files['unused.xml'] = b'<broken>'
        self.write(files)
        view = READER.read_ofd(self.path)
        self.assertEqual(view['records'][0]['text'], '第一页')
        self.assertEqual(view['scope']['page_count'], 2)
        self.assertEqual(view['coverage'], 'selected_page_text_occurrences_only')
        with self.assertRaisesRegex(READER.ReadError, 'DTD/entity'):
            READER.read_ofd(self.path, page_start=2)
        self.assertEqual(self.cli('input.ofd', '--view', 'full').returncode, 1)

    def test_template_and_repeated_composite_text_match_explicit_structure(self):
        files = BASE.fixture(
            text='正文', page_extra='<ofd:Template TemplateID="9"/>',
            common_extra='<ofd:TemplatePage ID="9" BaseLoc="T.xml"/><ofd:PublicRes>R.xml</ofd:PublicRes>',
            extra={
                'Doc/T.xml': BASE.xml('Page', '<ofd:Content><ofd:TextObject><ofd:TextCode>模板</ofd:TextCode>'
                                     '</ofd:TextObject></ofd:Content>'),
                'Doc/R.xml': BASE.xml('Res', '<ofd:CompositeGraphicUnits><ofd:CompositeGraphicUnit ID="8">'
                                     '<ofd:Content><ofd:TextObject><ofd:TextCode>复合</ofd:TextCode></ofd:TextObject>'
                                     '</ofd:Content></ofd:CompositeGraphicUnit></ofd:CompositeGraphicUnits>')})
        files['Doc/Pages/P.xml'] = files['Doc/Pages/P.xml'].replace(
            b'</ofd:Layer>', b'<ofd:CompositeObject ResourceID="8"/><ofd:CompositeObject ResourceID="8"/></ofd:Layer>')
        self.write(files)
        view = READER.read_ofd(self.path)
        full = READER.read_ofd_structure(self.path)
        stored = ''.join(run['text'] for run in full['documents'][0]['pages'][0]['text_runs'])
        self.assertEqual(view['records'][0]['text'], '模板正文复合复合')
        self.assertEqual(view['records'][0]['text'], stored)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(READER.read_ofd(self.path, text_layer='page')['records'][0]['text'], '正文复合复合')
        self.assertEqual(READER.read_ofd(self.path, text_layer='template')['records'][0]['text'], '模板')

    def test_compact_cursor_rejects_changed_source_and_page(self):
        self.write(BASE.fixture(text='甲' * 5000))
        view = READER.read_ofd(self.path, max_chars=1000)
        cursor = view['next_cursor']
        self.write(BASE.fixture(text='乙' * 5000))
        with self.assertRaisesRegex(READER.ReadError, 'scope changed'):
            READER.read_ofd(self.path, cursor=cursor)
        with self.assertRaises(READER.ReadError):
            READER.read_ofd(self.path, page_start=2, cursor=cursor)

    def test_content_cursor_reassembles_joined_runs_and_rejects_format_switch(self):
        fragments = ['甲😀' * 700, '乙\n' * 900, '结尾']
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:TextObject>'
            + ''.join('<ofd:TextCode>' + text + '</ofd:TextCode>' for text in fragments)
            + '</ofd:TextObject></ofd:Layer></ofd:Content>')
        self.write(files)
        chunks = []
        cursor = None
        for _ in range(30):
            view = READER.read_ofd(self.path, max_chars=1000, cursor=cursor)
            self.assertLessEqual(len(json.dumps(view, ensure_ascii=False)) + 1, 1000)
            for record in view['records']:
                self.assertEqual(record['character_offset'], sum(map(len, chunks)))
                chunks.append(record['text'])
            if view['done']:
                break
            cursor = view['next_cursor']
        self.assertTrue(view['done'])
        self.assertEqual(''.join(chunks), ''.join(fragments))
        with self.assertRaisesRegex(READER.ReadError, 'scope changed'):
            READER.read_ofd(self.path, reading_format='detail', cursor=cursor)
        detail = READER.read_ofd(self.path, reading_format='detail', max_chars=1200)
        with self.assertRaisesRegex(READER.ReadError, 'scope changed'):
            READER.read_ofd(self.path, cursor=detail['next_cursor'])

    def test_ambiguous_missing_and_nested_titles_are_not_inferred(self):
        for info in ('', '<ofd:Title>A</ofd:Title><ofd:Title>B</ofd:Title>',
                     '<ofd:Title>A<ofd:Other>B</ofd:Other></ofd:Title>'):
            with self.subTest(info=info):
                files = BASE.fixture(text='正文第一行并非标题')
                files['OFD.xml'] = files['OFD.xml'].replace(
                    b'<ofd:DocBody>', ('<ofd:DocBody><ofd:DocInfo>' + info + '</ofd:DocInfo>').encode())
                self.write(files)
                view = READER.read_ofd(self.path)
                self.assertIsNone(view['title'])
                self.assertEqual(view['records'][0]['text'], '正文第一行并非标题')

    def test_repeated_diagnostics_keep_counts_and_partial_text(self):
        files = BASE.fixture(text='可靠文字', page_extra='<ofd:UnknownFeature/>' * 600)
        self.write(files)
        response = self.cli('input.ofd')
        self.assertEqual(response.returncode, 3)
        view = json.loads(response.stdout)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['records'][0]['text'], '可靠文字')
        self.assertEqual(len(view['diagnostics']), 1)
        self.assertEqual(view['diagnostics'][0]['code'], 'unknown_element')
        self.assertEqual(view['diagnostics'][0]['count'], 600)

    def test_safety_counters_still_enforce_limits_without_event_retention(self):
        self.write(BASE.fixture())
        for key, limit, message in (('xml_events', 5, 'XML events'), ('nodes', 5, 'XML structure'),
                                    ('text_chars', 2, 'XML characters')):
            with self.subTest(limit=key), patch.dict(READER.LIMITS, {key: limit}):
                with self.assertRaisesRegex(READER.ReadError, message):
                    READER.read_ofd(self.path)

    def test_selected_missing_resource_and_scan_pages_preserve_diagnostics(self):
        files = BASE.fixture(common_extra='<ofd:PublicRes>Missing.xml</ofd:PublicRes>')
        files['Doc/Pages/P.xml'] = BASE.xml('Page', '<ofd:Content><ofd:ImageObject ResourceID="8"/></ofd:Content>')
        self.write(files)
        view = READER.read_ofd(self.path)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['records'][0]['text'], '')
        codes = {item['code'] for item in view['diagnostics']}
        self.assertTrue({'missing_resource_index', 'image_not_read', 'no_explainable_text'} <= codes)

    def test_capabilities_and_rejected_argument_combinations(self):
        response = self.cli('--capabilities')
        self.assertEqual(response.returncode, 0)
        capability = json.loads(response.stdout)
        self.assertEqual(capability, READER.capabilities())
        self.assertEqual(capability['interface_version'], 2)
        self.assertEqual(capability['default_view'], 'reading')
        for options in (('--check', '--view', 'reading'), ('--capabilities', '--check'),
                        ('--capabilities', 'missing.ofd'), ('--view', 'full', '--page', '1')):
            with self.subTest(options=options):
                response = self.cli(*options)
                self.assertEqual(response.returncode, 2)
                self.assertEqual(response.stdout, '')
                self.assertIn('error:', response.stderr)


if __name__ == '__main__':
    unittest.main()
