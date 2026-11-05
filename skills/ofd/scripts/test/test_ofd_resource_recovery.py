"""Recover only absent resource indexes, retaining explicit storage gaps."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

SPEC = importlib.util.spec_from_file_location('ofd_recovery_helpers', Path(__file__).with_name('test_ofd.py'))
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
READER = BASE.READER


class ResourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.gettempdir()) / ('ofd-resource-recovery-' + uuid.uuid4().hex)
        self.path = self.directory / 'input.ofd'
        self.registered = (self.directory, self.path)
        self.directory.mkdir(mode=0o700)

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()
        self.directory.rmdir()

    def read(self, files):
        BASE.write_package(self.path, files)
        return READER.read_ofd(self.path)

    def files(self, kind='PublicRes', value='Missing.xml'):
        if kind == 'PageRes':
            return BASE.fixture(page_extra=f'<ofd:PageRes>{value}</ofd:PageRes>')
        return BASE.fixture(common_extra=f'<ofd:{kind}>{value}</ofd:{kind}>')

    def test_missing_indexes_preserve_literal_text_and_gap_evidence(self):
        for version in READER.KNOWN_VERSIONS:
            for kind in ('PublicRes', 'DocumentRes', 'PageRes'):
                with self.subTest(version=version, kind=kind):
                    files = self.files(kind)
                    files['OFD.xml'] = files['OFD.xml'].replace(b'Version="1.0"', f'Version="{version}"'.encode())
                    result = self.read(files)
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['structure']['status'], 'partial')
                    self.assertEqual(result['structure']['missing_resource_index_count'], 1)
                    self.assertEqual(result['text_assessment']['status'], 'partial')
                    self.assertIn('missing_resource_index', result['text_assessment']['gap_codes'])
                    page = result['documents'][0]['pages'][0]
                    self.assertEqual([run['text'] for run in page['text_runs']], ['甲乙'])
                    gap = result['documents'][0]['resource_gaps'][0]
                    self.assertEqual(gap['kind'], kind)
                    self.assertEqual(gap['target'], ('Doc/Pages/' if kind == 'PageRes' else 'Doc/') + 'Missing.xml')
                    self.assertFalse(any(r['target'] == gap['target'] for r in result['references']))

    def test_normalized_internal_reference_is_not_replaced_by_another_file(self):
        files = self.files('PageRes', '../absent/../Missing.xml')
        files['Elsewhere/Missing.xml'] = BASE.xml('Res', '<ofd:Fonts><ofd:Font ID="9"/></ofd:Fonts>')
        result = self.read(files)
        gap = result['documents'][0]['resource_gaps'][0]
        self.assertEqual(gap['target'], 'Doc/Missing.xml')
        self.assertEqual(result['documents'][0]['resources'], [])
        self.assertTrue(any(p['source'] == 'Elsewhere/Missing.xml' and not p['active_route']
                            for p in result['structure']['xml_parts']))

    def test_unsafe_resource_references_still_fail(self):
        for value in ('../../Escape.xml', 'https://example.invalid/R.xml', '//host/R.xml',
                      'R%2exml', 'R.xml#fragment', ''):
            with self.subTest(value=value), self.assertRaises(READER.ReadError):
                self.read(self.files(value=value))

    def test_resource_directory_is_not_treated_as_absent(self):
        files = self.files(value='R')
        files['Doc/R/'] = b''
        with self.assertRaises(READER.ReadError):
            self.read(files)

    def test_present_bad_resource_remains_a_hard_failure(self):
        for contents in (b'<broken', BASE.xml('Page'),
                         b'<!DOCTYPE Res [<!ENTITY x "bad">]><Res>&x;</Res>',
                         b'<Res xmlns="urn:foreign"/>'):
            files = self.files()
            files['Doc/Missing.xml'] = contents
            with self.subTest(contents=contents), self.assertRaises(READER.ReadError):
                self.read(files)

    def test_missing_payload_of_present_index_remains_a_hard_failure(self):
        files = self.files()
        files['Doc/Missing.xml'] = BASE.xml('Res', '<ofd:Fonts><ofd:Font ID="7">'
                                          '<ofd:FontFile>absent.ttf</ofd:FontFile></ofd:Font></ofd:Fonts>')
        with self.assertRaisesRegex(READER.ReadError, 'missing package reference'):
            self.read(files)

    def test_missing_document_page_and_template_remain_hard_failures(self):
        for missing in ('Doc/Document.xml', 'Doc/Pages/P.xml'):
            files = self.files()
            del files[missing]
            with self.subTest(missing=missing), self.assertRaises(READER.ReadError):
                self.read(files)
        files = BASE.fixture(common_extra='<ofd:TemplatePage ID="9" BaseLoc="absent.xml"/>')
        with self.assertRaises(READER.ReadError):
            self.read(files)

    def test_missing_composite_and_font_keep_independent_body_text(self):
        files = self.files()
        files['Doc/Pages/P.xml'] = files['Doc/Pages/P.xml'].replace(
            b'<ofd:TextObject ID="3"', b'<ofd:CompositeObject ResourceID="8"/><ofd:TextObject Font="7" ID="3"')
        result = self.read(files)
        self.assertEqual([run['text'] for run in result['documents'][0]['pages'][0]['text_runs']], ['甲乙'])
        self.assertTrue({'missing_resource_index', 'unresolved_font', 'unresolved_composite_resource'}
                        <= {d['code'] for d in result['diagnostics']})

    def test_absent_indexes_still_consume_reference_budget(self):
        files = BASE.fixture(common_extra='<ofd:PublicRes>absent.xml</ofd:PublicRes>' * 3)
        with patch.dict(READER.LIMITS, references=3):
            with self.assertRaisesRegex(READER.ReadError, 'limit: references'):
                self.read(files)

    def test_valid_present_resource_has_no_recovery_gaps(self):
        files = self.files()
        files['Doc/Missing.xml'] = BASE.xml('Res', '<ofd:Fonts><ofd:Font ID="7"/></ofd:Fonts>')
        result = self.read(files)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['documents'][0]['resource_gaps'], [])
        self.assertEqual(result['structure']['missing_resource_index_count'], 0)

    def test_cli_views_keep_partial_and_source_evidence(self):
        BASE.write_package(self.path, self.files())
        for view in ('full', 'summary', 'page-text'):
            command = [sys.executable, '-I', '-S', '-B', str(Path(READER.__file__)),
                       './input.ofd', '--view', view]
            if view == 'page-text':
                command += ['--document', '1', '--page', '1']
            run = subprocess.run(command, cwd=self.directory, capture_output=True, text=True, encoding='utf-8', check=False)
            with self.subTest(view=view):
                self.assertEqual(run.returncode, 3, run.stderr)
                self.assertEqual(run.stderr, '')
                result = json.loads(run.stdout)
                self.assertEqual(result['status'], 'partial')
                self.assertIn('missing_resource_index', {d['code'] for d in result['diagnostics']})


if __name__ == '__main__':
    unittest.main()
