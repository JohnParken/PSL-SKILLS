"""Version and shared-structure compatibility; synthetic tests are not real samples."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import importlib.util
import json
import subprocess
import tempfile
import unittest

BASE_SPEC = importlib.util.spec_from_file_location('ofd_test_helpers', Path(__file__).with_name('test_ofd.py'))
BASE = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE)
READER = BASE.READER


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'input.ofd'

    def tearDown(self):
        self.temporary.cleanup()

    def read(self, files, **options):
        BASE.write_package(self.path, files)
        return READER.read_ofd_structure(self.path, **options)

    def files(self, version='1.0', doc_type='OFD'):
        files = BASE.fixture()
        files['OFD.xml'] = BASE.xml(
            'OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>',
            f'Version="{version}" DocType="{doc_type}"')
        return files

    def test_known_versions_read_common_text_with_honest_status(self):
        for version in ('1.0', '1.1', '1.2'):
            with self.subTest(version=version):
                result = self.read(self.files(version))
                self.assertEqual(result['format']['version'], version)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['documents'][0]['pages'][0]['text_runs'][0]['text'], '甲乙')

    def test_unknown_minor_requires_explicit_policy(self):
        files = self.files('1.7')
        with self.assertRaisesRegex(READER.ReadError, 'unsupported: OFD version'):
            self.read(files)
        result = self.read(files, version_policy='compatible')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['format']['version'], '1.7')

    def test_invalid_versions_and_major_remain_rejected(self):
        for version in ('2.0', '1.2.3', '1.１', '1.0000', '', '1.2 '):
            with self.subTest(version=version), self.assertRaises(READER.ReadError):
                self.read(self.files(version), version_policy='compatible')
        with self.assertRaises(READER.ReadError):
            self.read(self.files(), version_policy='anything')

    def test_ofda_does_not_claim_archival_validation(self):
        result = self.read(self.files('1.2', 'OFD-A'))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('archival_profile_not_validated', [d['code'] for d in result['diagnostics']])

    def test_missing_version_is_reported(self):
        files = self.files()
        files['OFD.xml'] = BASE.xml('OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>')
        result = self.read(files)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['format']['declared_version'])

    def test_document_info_is_separate_from_page_text(self):
        files = self.files()
        files['OFD.xml'] = BASE.xml(
            'OFD', '<ofd:DocBody><ofd:DocInfo><ofd:Title>标题</ofd:Title></ofd:DocInfo>'
            '<ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>', 'Version="1.0" DocType="OFD"')
        result = self.read(files)
        self.assertEqual(result['documents'][0]['info'][1]['text'], '标题')
        self.assertEqual(len(result['documents'][0]['pages'][0]['text_runs']), 1)

    def page_resources(self):
        files = self.files()
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:PageRes>../PageRes.xml</ofd:PageRes><ofd:Content><ofd:Layer>'
            '<ofd:TextObject ID="3" Font="7"><ofd:TextCode>页资源字</ofd:TextCode></ofd:TextObject>'
            '</ofd:Layer></ofd:Content>')
        files['Doc/PageRes.xml'] = BASE.xml('Res', '<ofd:Fonts><ofd:Font ID="7" FontName="Demo"/></ofd:Fonts>')
        return files

    def test_page_resource_font_and_ancestry(self):
        result = self.read(self.page_resources())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['documents'][0]['resources'][0]['scope'], 'Doc/Pages/P.xml')
        run = result['documents'][0]['pages'][0]['text_runs'][0]
        self.assertEqual(run['ancestry'][-1]['tag'], READER.q('Layer'))

    def test_page_resources_do_not_leak_to_other_pages(self):
        files = self.page_resources()
        files['Doc/Document.xml'] = BASE.xml(
            'Document', '<ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/P.xml"/>'
            '<ofd:Page ID="2" BaseLoc="Pages/Q.xml"/></ofd:Pages>')
        files['Doc/Pages/Q.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:TextObject Font="7">'
            '<ofd:TextCode>Q</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
        result = self.read(files)
        self.assertIn('unresolved_font', [d['code'] for d in result['diagnostics']])

    def test_repeated_page_resource_same_file_is_not_duplicate_id(self):
        files = self.page_resources()
        files['Doc/Pages/P.xml'] = files['Doc/Pages/P.xml'].replace(
            b'<ofd:Content>', b'<ofd:PageRes>../PageRes.xml</ofd:PageRes><ofd:Content>')
        self.assertEqual(self.read(files)['status'], 'complete')

    def composite_files(self, body=None):
        if body is None:
            body = '<ofd:TextObject ID="70"><ofd:TextCode>复合字</ofd:TextCode></ofd:TextObject>'
        files = self.files()
        files['Doc/Document.xml'] = BASE.xml(
            'Document', '<ofd:CommonData><ofd:DocumentRes>Res.xml</ofd:DocumentRes></ofd:CommonData>'
            '<ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/P.xml"/></ofd:Pages>')
        files['Doc/Res.xml'] = BASE.xml(
            'Res', '<ofd:CompositeGraphicUnits><ofd:CompositeGraphicUnit ID="9" Width="10" Height="10">'
            '<ofd:Content>' + body + '</ofd:Content></ofd:CompositeGraphicUnit></ofd:CompositeGraphicUnits>')
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:Content><ofd:Layer><ofd:CompositeObject ID="5" ResourceID="9" Boundary="1 2 10 10"/>'
            '<ofd:CompositeObject ID="6" ResourceID="9" Boundary="20 2 10 10"/></ofd:Layer></ofd:Content>')
        return files

    def test_composite_text_per_occurrence_with_source_chain(self):
        result = self.read(self.composite_files())
        runs = result['documents'][0]['pages'][0]['text_runs']
        self.assertEqual([r['text'] for r in runs], ['复合字', '复合字'])
        self.assertEqual([r['occurrence'][0]['object_id'] for r in runs], ['5', '6'])
        self.assertTrue(all(r['source'] == 'Doc/Res.xml' for r in runs))
        self.assertEqual(result['status'], 'partial')

    def test_nested_cross_file_composite_occurrences(self):
        files = self.composite_files('<ofd:CompositeObject ID="71" ResourceID="10"/>')
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:CommonData>', b'<ofd:DocumentRes>Other.xml</ofd:DocumentRes></ofd:CommonData>')
        files['Doc/Other.xml'] = BASE.xml(
            'Res', '<ofd:CompositeGraphicUnits><ofd:CompositeGraphicUnit ID="10">'
            '<ofd:Content><ofd:TextObject><ofd:TextCode>深层</ofd:TextCode></ofd:TextObject></ofd:Content>'
            '</ofd:CompositeGraphicUnit></ofd:CompositeGraphicUnits>')
        result = self.read(files)
        runs = result['documents'][0]['pages'][0]['text_runs']
        self.assertEqual([r['text'] for r in runs], ['深层', '深层'])
        self.assertTrue(all(len(r['occurrence']) == 2 and r['source'] == 'Doc/Other.xml' for r in runs))
        files['Doc/Other.xml'] = files['Doc/Other.xml'].replace(
            '<ofd:TextObject><ofd:TextCode>深层</ofd:TextCode></ofd:TextObject>'.encode(),
            b'<ofd:CompositeObject ResourceID="9"/>')
        with self.assertRaisesRegex(READER.ReadError, 'cyclic composite'):
            self.read(files)

    def test_cli_version_policy_contract(self):
        target = Path(__file__).parents[1] / 'read_ofd.py'
        for version, options, expected in (
                ('1.1', [], 0), ('1.2', [], 0), ('1.7', [], 4),
                ('1.7', ['--version-policy', 'compatible'], 3)):
            BASE.write_package(self.path, self.files(version))
            completed = subprocess.run(
                [sys.executable, '-I', '-S', '-B', str(target), 'input.ofd', '--view', 'full'] + options,
                cwd=self.path.parent, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, expected, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result['status'], {0: 'complete', 3: 'partial', 4: 'unsupported'}[expected])
            if expected in (0, 3):
                self.assertEqual(result['format']['version'], version)

    def test_composite_cycle_fails_without_partial_success(self):
        with self.assertRaisesRegex(READER.ReadError, 'cyclic composite'):
            self.read(self.composite_files('<ofd:CompositeObject ResourceID="9"/>'))

    def test_composite_depth_limit(self):
        original = READER.LIMITS['composite_depth']
        try:
            READER.LIMITS['composite_depth'] = 0
            with self.assertRaisesRegex(READER.ReadError, 'composite depth'):
                self.read(self.composite_files())
        finally:
            READER.LIMITS['composite_depth'] = original

    def test_foreign_resource_objects_not_interpreted(self):
        files = self.composite_files('<x:TextObject xmlns:x="urn:foreign"><ofd:TextCode>不可信</ofd:TextCode></x:TextObject>')
        result = self.read(files)
        self.assertEqual(result['documents'][0]['pages'][0]['text_runs'], [])
        self.assertIn('foreign_namespace', [d['code'] for d in result['diagnostics']])

    def test_page_resource_security_checks_unchanged(self):
        files = self.page_resources()
        files['Doc/PageRes.xml'] = b'<!DOCTYPE x [<!ENTITY x "boom">]><x/>'
        with self.assertRaisesRegex(READER.ReadError, 'DTD/entity'):
            self.read(files)
        files = self.page_resources()
        files['Doc/Pages/P.xml'] = files['Doc/Pages/P.xml'].replace(b'../PageRes.xml', b'https://example.org/evil.xml')
        with self.assertRaises(READER.ReadError):
            self.read(files)


if __name__ == '__main__':
    unittest.main()
