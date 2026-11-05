#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Output signatures, real readback, partial exits and legacy entry-point regressions."""
from test_task_cli_support import run_task_cli, run_main
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import docx_to_wps_rtf
import export_wps
from read_wps import read_bytes
from run_wps_edit import execute_edit_task
from run_wps_task import create_document
from test_wps import document, synthetic_cfb
from test_wps_ooxml import package
from wps_output_contract import inspect_output_bytes, readback_summary
from wps_rtf_builder import RtfDocument


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = {'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': '正文中文😀'}]}


def binary_document(partial=False):
    streams = document(other_stories={80: 'note\r'.encode('utf-16le')} if partial else None)
    return synthetic_cfb({name[0]: data for name, data in streams.items()})


class OutputContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wps-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.docx'
        self.ooxml = package('<w:p><w:r><w:t>正文中文😀</w:t></w:r></w:p>')
        self.source.write_bytes(self.ooxml)
        self.rtf_source = self.root / 'source.rtf'
        create_document(SPEC, self.rtf_source)
        self.rtf = self.rtf_source.read_bytes()

    def cli(self, script, *args, stdin=None):
        return run_task_cli([sys.executable, '-I', '-S', '-B', str(SCRIPT_DIR / script), *map(str, args)],
                              input=stdin, capture_output=True, text=True, check=False, timeout=30)

    def fake_office(self, data, code=0):
        def run(command, **kwargs):
            self.assertIn('doc:MS Word 97', command)
            self.assertTrue(any(item.startswith('-env:UserInstallation=file:') for item in command))
            private = Path(command[command.index('--outdir') + 1])
            self.assertNotEqual(private, self.root)
            if data is not None:
                (private / (Path(command[-1]).stem + '.doc')).write_bytes(data)
            return subprocess.CompletedProcess(command, code, 'converter stdout', 'converter stderr')
        return run

    def test_binary_string_paragraphs_real_reader(self):
        path = self.root / 'binary.doc'
        path.write_bytes(binary_document())
        result = export_wps.verify_readback(path)
        self.assertEqual((result['status'], result['returncode'], result['format']), ('readback-ok', 0, 'ms_doc_subset'))
        self.assertEqual(result['preview'], ['Hello 中文', '第二段'])
        self.assertEqual(result['paragraph_count'], 2)
        self.assertFalse(result['full_format_verified'])

    def test_binary_partial_diagnostics_and_exit_three(self):
        path = self.root / 'binary.doc'
        path.write_bytes(binary_document(partial=True))
        result = export_wps.verify_readback(path)
        self.assertEqual((result['status'], result['returncode']), ('readback-partial', 3))
        self.assertEqual(result['preview'], ['Hello 中文', '第二段'])
        self.assertEqual(result['omitted_regions'][0]['type'], 'footnotes')
        self.assertEqual(result['issues'][0]['code'], 'related_content_not_requested')

    def test_rtf_uses_reader_unicode_without_font_table_noise(self):
        result = export_wps.verify_readback(self.rtf_source)
        self.assertEqual(result['status'], 'readback-ok')
        self.assertEqual(result['preview'], ['正文中文😀'])

    def test_ooxml_misnamed_doc_still_readable(self):
        path = self.root / 'ooxml.doc'
        path.write_bytes(self.ooxml)
        result = export_wps.verify_readback(path)
        self.assertEqual(result['format'], 'ooxml-word')
        self.assertEqual(result['preview'], ['正文中文😀'])
        self.assertEqual(result['status'], 'readback-ok')
        self.assertFalse(export_wps.inspect_wps_structure(path)['valid'])

    def test_signature_suffix_matrix_and_legacy_inspectors(self):
        for data, actual, allowed in ((self.rtf, 'rtf', {'.rtf', '.wps'}),
                                      (binary_document(), 'ms_doc_subset', {'.doc', '.wps'}),
                                      (self.ooxml, 'ooxml-word', set())):
            for suffix in ('.rtf', '.wps', '.doc', '.docx'):
                with self.subTest(actual=actual, suffix=suffix):
                    path = self.root / ('matrix' + suffix)
                    path.write_bytes(data)
                    result = export_wps.inspect_wps_structure(path)
                    self.assertEqual(result['valid'], suffix in allowed)
                    self.assertEqual(result['detected_format'], actual)
                    self.assertEqual(export_wps.verify_readback(path)['status'], 'readback-ok')
        self.assertFalse(export_wps.inspect_cfb_wps(self.rtf_source, 9999, export_wps.CFB_SIGNATURE)['valid'])
        self.assertTrue(export_wps.inspect_rtf_wps(self.rtf_source, 1, b'fake')['valid'])

    def test_damaged_rtf_readable_but_not_publishable(self):
        path = self.root / 'truncated.doc'
        path.write_bytes(b'{\\rtf1\\ansi readable\\par')
        result = export_wps.verify_readback(path)
        self.assertEqual((result['status'], result['returncode']), ('readback-partial', 3))
        self.assertEqual(result['preview'], ['readable'])
        self.assertIn('rtf_unclosed_groups', [item['code'] for item in result['issues']])
        self.assertFalse(inspect_output_bytes(path.read_bytes(), 'candidate.rtf')['valid'])

    def test_corrupt_cfb_is_not_accepted_on_magic_alone(self):
        path = self.root / 'broken.doc'
        path.write_bytes(binary_document()[:700])
        self.assertFalse(export_wps.inspect_wps_structure(path)['valid'])
        result = export_wps.verify_readback(path)
        self.assertEqual((result['status'], result['returncode']), ('readback-error', 1))

    def test_corrupt_ooxml_fails_without_output_or_source_changes(self):
        self.source.write_bytes(self.ooxml[:100])
        target = self.root / 'existing.rtf'
        target.write_bytes(b'original')
        result = export_wps.convert_docx_to_wps(self.source, target, backend='rtf')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(self.source.read_bytes(), self.ooxml[:100])
        self.assertEqual(export_wps.verify_readback(self.source)['status'], 'readback-error')

    def test_readback_rejects_bad_payload_or_exit_mismatch(self):
        for payload, code in (([], 0), ({'status': 'ok', 'paragraphs': 'text'}, 0),
                              ({'status': 'ok', 'paragraphs': [3]}, 0),
                              ({'status': 'ok', 'paragraphs': []}, 3),
                              ({'status': 'partial', 'paragraphs': []}, 0)):
            with self.subTest(payload=payload, code=code):
                self.assertEqual(readback_summary(payload, code)['status'], 'readback-error')

    def test_custom_reader_failure_preserves_channels(self):
        result = subprocess.CompletedProcess([], 7, 'x' * 800, 'reader failed')
        with patch('export_wps.subprocess.run', return_value=result):
            summary = export_wps.verify_readback(self.rtf_source)
        self.assertEqual(summary['status'], 'readback-nonjson')
        self.assertEqual(summary['returncode'], 7)
        self.assertEqual(summary['stderr'], 'reader failed')
        self.assertEqual(summary['stdout_characters'], 800)
        self.assertTrue(summary['stdout_truncated'])
        self.assertEqual(len(summary['stdout_tail']), 500)
        self.assertEqual(export_wps.verify_readback(self.rtf_source, self.root / 'missing.py')['status'], 'error')

    def test_reader_timeout_does_not_invent_exit_code(self):
        with patch('export_wps.subprocess.run', side_effect=subprocess.TimeoutExpired('reader', 30)):
            result = export_wps.verify_readback(self.rtf_source)
        self.assertEqual(result['status'], 'readback-error')
        self.assertIsNone(result['returncode'])

    def test_creation_edit_and_builder_refuse_rtf_doc(self):
        target = self.root / 'existing.doc'
        original = binary_document()
        target.write_bytes(original)
        doc = RtfDocument()
        doc.add_body_paragraph('changed')
        calls = (lambda: create_document(SPEC, target),
                 lambda: execute_edit_task(self.rtf_source, target, [{'operation': 'replace_text', 'find': '正文', 'replace': '修订'}]),
                 lambda: doc.save(target),
                 lambda: docx_to_wps_rtf.convert_docx_to_rtf_wps(self.source, target))
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(ValueError, 'rtf output requires'):
                    call()
                self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), self.ooxml)

    def test_create_and_edit_cli_refuse_doc_and_preserve_target(self):
        target = self.root / 'existing.doc'
        target.write_bytes(b'original')
        create = self.cli('run_wps_task.py', 'create', target, stdin=json.dumps(SPEC))
        edit = self.cli('run_wps_edit.py', 'text', self.rtf_source, target, '--find', '正文', '--replace', '修订')
        self.assertEqual(create.returncode, 2)
        self.assertEqual(edit.returncode, 1)
        self.assertEqual(target.read_bytes(), b'original')

    def test_valid_create_edit_receipts_identify_rtf(self):
        target = self.root / 'new.wps'
        created = create_document(SPEC, target, compact_receipt=True)
        edited = execute_edit_task(target, self.root / 'edited.rtf', [{'operation': 'replace_text', 'find': '正文', 'replace': '修订'}])
        for result in (created, edited):
            self.assertEqual(result['output_format'], 'rtf')
            self.assertFalse(result['binary_doc'])

    def test_rtf_export_and_legacy_converter_refuse_doc(self):
        target = self.root / 'existing.doc'
        target.write_bytes(b'original')
        result = export_wps.convert_docx_to_wps(self.source, target, backend='rtf')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(target.read_bytes(), b'original')
        with self.assertRaises(ValueError):
            docx_to_wps_rtf.convert_docx_to_rtf_wps(self.source, self.root / 'unused' / 'wrong.doc')
        self.assertFalse((self.root / 'unused').exists())

    def test_auto_doc_never_falls_back(self):
        target = self.root / 'existing.doc'
        target.write_bytes(b'original')
        with patch('export_wps.find_soffice', return_value=None), patch('export_wps.convert_docx_to_wps_rtf') as rtf:
            result = export_wps.convert_docx_to_wps(self.source, target)
        self.assertEqual(result['status'], 'missing-dependency')
        self.assertFalse(result['fallback_allowed'])
        rtf.assert_not_called()
        self.assertEqual(target.read_bytes(), b'original')

    def test_auto_wps_reports_actual_rtf_fallback(self):
        target = self.root / 'fallback.wps'
        with patch('export_wps.find_soffice', return_value=None):
            result = export_wps.convert_docx_to_wps(self.source, target)
        self.assertEqual((result['status'], result['backend'], result['output_format']), ('partial', 'rtf', 'rtf'))
        self.assertFalse(result['binary_doc'])
        self.assertTrue(result['fallback_used'])
        self.assertEqual(result['fallback']['reason']['status'], 'missing-dependency')
        self.assertEqual(export_wps.verify_readback(target)['preview'], ['正文中文😀'])

    def test_auto_rtf_routes_directly(self):
        with patch('export_wps.convert_docx_to_wps_libreoffice') as office:
            result = export_wps.convert_docx_to_wps(self.source, self.root / 'direct.rtf')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['output_format'], 'rtf')
        office.assert_not_called()

    def test_create_cli_wps_name_contains_rtf_and_reads_back(self):
        target = self.root / '工作情况通报.wps'
        process = self.cli('run_wps_task.py', 'create', target, '--compact-receipt', stdin=json.dumps(SPEC))
        self.assertEqual(process.returncode, 0, process.stderr)
        receipt = json.loads(process.stdout)
        self.assertEqual(receipt['target'], target.name)
        self.assertEqual(receipt['output_format'], 'rtf')
        self.assertFalse(receipt['binary_doc'])
        self.assertFalse(receipt['native_wps'])
        self.assertTrue(target.read_bytes().startswith(b'{\\rtf1'))
        self.assertEqual(export_wps.verify_readback(target)['preview'], ['正文中文😀'])
        self.assertFalse(target.with_suffix('.rtf').exists())

    def test_rtf_export_cli_default_wps_name_and_readback(self):
        process = self.cli('export_wps.py', 'convert', self.source, '--backend', 'rtf', '--verify-read')
        self.assertEqual(process.returncode, 0, process.stderr)
        receipt = json.loads(process.stdout)
        target = self.source.with_suffix('.wps')
        self.assertEqual(Path(receipt['output_file']), target.resolve())
        self.assertEqual(receipt['output_format'], 'rtf')
        self.assertFalse(receipt['binary_doc'])
        self.assertFalse(receipt['native_wps'])
        self.assertEqual(receipt['readback']['status'], 'readback-ok')
        self.assertEqual(receipt['readback']['preview'], ['正文中文😀'])
        self.assertTrue(target.read_bytes().startswith(b'{\\rtf1'))
        self.assertEqual(self.source.read_bytes(), self.ooxml)
        self.assertEqual(self.rtf_source.read_bytes(), self.rtf)

    def test_explicit_rtf_names_are_kept_by_create_and_export_cli(self):
        created = self.root / 'requested-create.rtf'
        exported = self.root / 'requested-export.rtf'
        creation = self.cli('run_wps_task.py', 'create', created, '--compact-receipt', stdin=json.dumps(SPEC))
        conversion = self.cli('export_wps.py', 'convert', self.source, '--backend', 'rtf', '--output', exported, '--verify-read')
        for process, target in ((creation, created), (conversion, exported)):
            with self.subTest(target=target.name):
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)['output_format'], 'rtf')
                self.assertTrue(target.read_bytes().startswith(b'{\\rtf1'))
                self.assertFalse(target.with_suffix('.wps').exists())
                self.assertEqual(export_wps.verify_readback(target)['preview'], ['正文中文😀'])

    def test_auto_fallback_keeps_default_wps_name_and_partial(self):
        with patch('export_wps.find_soffice', return_value=None):
            receipt = export_wps.convert_docx_to_wps(self.source)
        self.assertEqual(Path(receipt['output_file']), self.source.with_suffix('.wps').resolve())
        self.assertEqual(receipt['status'], 'partial')
        self.assertEqual(receipt['output_format'], 'rtf')
        self.assertFalse(receipt['binary_doc'])
        self.assertTrue(receipt['fallback_used'])
        self.assertEqual(export_wps.verify_readback(receipt['output_file'])['preview'], ['正文中文😀'])

    def test_default_rtf_export_failure_preserves_existing_wps(self):
        target = self.source.with_suffix('.wps')
        target.write_bytes(b'previous result')
        self.source.write_bytes(self.ooxml[:100])
        receipt = export_wps.convert_docx_to_wps_rtf(self.source)
        self.assertEqual(receipt['status'], 'error')
        self.assertEqual(target.read_bytes(), b'previous result')
        self.assertEqual(self.source.read_bytes(), self.ooxml[:100])

    def test_binary_backend_validates_private_candidate_and_preserves_partial(self):
        self.source.write_bytes(package('<w:p><w:r><w:t>Hello 中文</w:t></w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p>'))
        for partial in (False, True):
            for suffix in ('.doc', '.wps'):
                with self.subTest(partial=partial, suffix=suffix):
                    target = self.root / ('binary' + suffix)
                    data = binary_document(partial)
                    with patch('export_wps.find_soffice', return_value='soffice'), patch('export_wps.subprocess.run', side_effect=self.fake_office(data)):
                        result = export_wps.convert_docx_to_wps(self.source, target, backend='libreoffice')
                    self.assertEqual(result['status'], 'partial' if partial else 'success')
                    self.assertEqual(result['output_format'], 'ms_doc_subset')
                    self.assertTrue(result['binary_doc'])
                    self.assertFalse(result['native_wps'])
                    self.assertEqual(target.read_bytes(), data)
                    self.assertEqual(result['readback']['preview'], ['Hello 中文', '第二段'])
                    self.assertEqual(result['readback']['status'], 'readback-partial' if partial else 'readback-ok')
        self.assertFalse(list(self.root.glob('.tmp_wps_export_*')))

    def test_binary_backend_rejects_wrong_or_missing_candidate_without_overwrite(self):
        target = self.root / 'existing.doc'
        for data in (None, self.rtf, self.ooxml, binary_document()[:700]):
            with self.subTest(data_type=None if data is None else data[:8]):
                target.write_bytes(b'original')
                with patch('export_wps.find_soffice', return_value='soffice'), patch('export_wps.subprocess.run', side_effect=self.fake_office(data)):
                    result = export_wps.convert_docx_to_wps(self.source, target)
                self.assertIn(result['status'], ('output-missing', 'structure-invalid'))
                self.assertFalse(result['fallback_allowed'])
                self.assertEqual(target.read_bytes(), b'original')
        self.assertFalse(list(self.root.glob('.tmp_wps_export_*')))

    def test_converter_failure_keeps_channels_and_existing_file(self):
        target = self.root / 'existing.doc'
        target.write_bytes(b'original')
        with patch('export_wps.find_soffice', return_value='soffice'), patch('export_wps.subprocess.run', side_effect=self.fake_office(None, 9)):
            result = export_wps.convert_docx_to_wps(self.source, target)
        self.assertEqual(result['status'], 'conversion-failed')
        self.assertEqual(result['returncode'], 9)
        self.assertEqual(result['stderr'], 'converter stderr')
        self.assertEqual(result['stdout'], 'converter stdout')
        self.assertEqual(target.read_bytes(), b'original')

    def test_source_change_during_conversion_stops_publication(self):
        target = self.root / 'existing.doc'
        target.write_bytes(b'original')
        fake = self.fake_office(binary_document())

        def changing_source(command, **kwargs):
            result = fake(command, **kwargs)
            self.source.write_bytes(b'changed concurrently')
            return result

        with patch('export_wps.find_soffice', return_value='soffice'), patch('export_wps.subprocess.run', side_effect=changing_source):
            result = export_wps.convert_docx_to_wps(self.source, target)
        self.assertEqual(result['status'], 'error')
        self.assertIn('Source changed', result['error'])
        self.assertEqual(target.read_bytes(), b'original')

    def test_converter_bad_signature_auto_wps_discloses_fallback(self):
        with patch('export_wps.find_soffice', return_value='soffice'), patch('export_wps.subprocess.run', side_effect=self.fake_office(self.rtf)):
            result = export_wps.convert_docx_to_wps(self.source, self.root / 'fallback.wps')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['binary_doc'])
        self.assertEqual(result['fallback']['reason']['status'], 'structure-invalid')

    def test_export_input_is_content_based_and_source_alias_protected(self):
        misnamed = self.root / 'ooxml.doc'
        misnamed.write_bytes(self.ooxml)
        result = export_wps.convert_docx_to_wps(misnamed, self.root / 'good.rtf', backend='rtf')
        self.assertEqual(result['status'], 'success')
        wrong = export_wps.convert_docx_to_wps(self.rtf_source, self.root / 'bad.rtf', backend='rtf')
        self.assertEqual(wrong['status'], 'error')
        self.assertFalse((self.root / 'bad.rtf').exists())
        alias = self.root / 'alias.rtf'
        os.link(self.source, alias)
        result = export_wps.convert_docx_to_wps(self.source, alias, backend='rtf')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.source.read_bytes(), self.ooxml)
        with self.assertRaisesRegex(ValueError, 'overwrite the source'):
            docx_to_wps_rtf.convert_docx_to_rtf_wps(self.source, alias)

    def test_source_partial_survives_successful_output(self):
        # Unsupported OOXML drawing is disclosed even when output text reads ok.
        source = self.root / 'partial.docx'
        source.write_bytes(package('<w:p><w:r><w:t>Main</w:t></w:r><w:drawing/></w:p>'))
        source_payload = read_bytes(source.read_bytes())
        self.assertEqual(source_payload['status'], 'partial')
        result = export_wps.convert_docx_to_wps(source, self.root / 'partial.rtf', backend='rtf')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['source_readback']['status'], 'readback-partial')
        self.assertEqual(result['readback']['status'], 'readback-ok')

    def test_legacy_converter_reports_complex_table_partial(self):
        source = SCRIPT_DIR.parents[1] / '03_复杂表格_跨行跨列合并.docx'
        if not source.is_file():
            self.skipTest('Repository complex-table fixture unavailable in standalone package')
        target = self.root / 'legacy.rtf'
        result = docx_to_wps_rtf.convert_docx_to_rtf_wps(source, target)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['readback']['status'], 'readback-partial')
        self.assertEqual(result['output_format'], 'rtf')
        self.assertFalse(result['binary_doc'])

    def test_inspect_cli_partial_and_wrong_suffix_exit_codes(self):
        path = self.root / 'partial.doc'
        path.write_bytes(binary_document(True))
        result = self.cli('export_wps.py', 'inspect', path)
        self.assertEqual(result.returncode, 3)
        self.assertTrue(json.loads(result.stdout)['partial'])
        path.write_bytes(self.rtf)
        result = self.cli('export_wps.py', 'inspect', path)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['valid'])

    def test_export_cli_verify_read_rejects_unavailable_reader(self):
        result = self.cli('export_wps.py', 'convert', self.source, '-o', self.root / 'cli.rtf', '-b', 'rtf',
                          '--verify-read', '--read-wps-script', self.root / 'missing.py')
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], 'readback-failed')
        self.assertTrue(Path(payload['output_file']).is_file())

    def test_cli_preserves_partial_exit_including_verify_read(self):
        for reader_status, expected_exit in (('readback-partial', 3), ('readback-error', 1), ('readback-ok', 0)):
            with self.subTest(reader_status=reader_status):
                output = io.StringIO()
                converted = {'status': 'success', 'output_file': str(self.rtf_source)}
                with (
                    patch.object(sys, 'argv', ['export_wps.py', 'convert', str(self.source), '--verify-read']),
                    patch('export_wps.convert_docx_to_wps', return_value=converted),
                    patch('export_wps.verify_readback', return_value={'status': reader_status}),
                    contextlib.redirect_stdout(output),
                ):
                    with self.assertRaises(SystemExit) as raised:
                        run_main(export_wps.main)
                self.assertEqual(raised.exception.code, expected_exit)
                self.assertEqual(json.loads(output.getvalue())['readback']['status'], reader_status)


if __name__ == '__main__':
    unittest.main()
