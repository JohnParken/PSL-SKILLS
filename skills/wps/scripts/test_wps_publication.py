"""Publication, cleanup and source-format mapping fault injection."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from read_wps import read_bytes
from run_wps_task import create_document, main
from docx_to_wps_rtf import convert_docx_to_rtf_wps
from test_wps_ooxml import package
from test_wps_task_paths import scoped
from wps_word_styles import format_mapping_diagnostics


class PublicationTests(unittest.TestCase):
    @contextlib.contextmanager
    def linking(self, callback):
        with (patch.object(os, 'link', callback),
              patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {callback}),
              patch.object(os, 'supports_follow_symlinks', os.supports_follow_symlinks | {callback})):
            yield

    def test_mutation_at_link_fails_postpublication_verification(self):
        real_link = os.link
        with tempfile.TemporaryDirectory() as directory, scoped(directory):
            def altered(source, destination, **kwargs):
                descriptor = os.open(source, os.O_RDWR, dir_fd=kwargs['src_dir_fd'])
                with os.fdopen(descriptor, 'r+b') as stream:
                    raw = stream.read()
                    stream.seek(0)
                    stream.write(raw.replace(b'Reliable', b'Changed!'))
                return real_link(source, destination, **kwargs)
            with self.linking(altered), self.assertRaises(ValueError) as raised:
                create_document({'blocks': [{'type': 'paragraph', 'text': 'Reliable'}]}, Path('new.wps'))
            report = raised.exception.publication
            self.assertTrue(report['output_written'])
            self.assertEqual(report['publication_outcome'], 'published_verification_failed')
            self.assertTrue(report['cleanup_complete'])
            self.assertEqual(read_bytes(Path('new.wps').read_bytes())['text'], 'Changed!')
            self.assertFalse(list(Path('.').glob('.tmp_wps_*')))

    def test_final_name_replacement_is_retained_for_inspection(self):
        real_link = os.link
        with tempfile.TemporaryDirectory() as directory, scoped(directory):
            Path('other.wps').write_bytes(b'unknown file')
            def replaced(source, destination, **kwargs):
                real_link(source, destination, **kwargs)
                os.unlink(destination, dir_fd=kwargs['dst_dir_fd'])
                real_link('other.wps', destination, dst_dir_fd=kwargs['dst_dir_fd'])
            with self.linking(replaced), self.assertRaises(ValueError) as raised:
                create_document({'blocks': [{'type': 'paragraph', 'text': 'Reliable'}]}, Path('new.wps'))
            self.assertEqual(raised.exception.publication['publication_verification'], 'failed')
            self.assertEqual(Path('new.wps').read_bytes(), b'unknown file')
            self.assertEqual(Path('other.wps').read_bytes(), b'unknown file')

    def test_cleanup_failure_cli_preserves_published_outcome(self):
        real_unlink = os.unlink
        with tempfile.TemporaryDirectory() as directory, scoped(directory):
            def denied(path, *args, **kwargs):
                if str(path).startswith('.tmp_wps_'):
                    raise PermissionError('injected cleanup failure')
                return real_unlink(path, *args, **kwargs)
            stdout, stderr = io.StringIO(), io.StringIO()
            with (patch.object(os, 'unlink', denied), patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {denied}),
                  contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
                code = main(['create', 'new.wps', '--spec', json.dumps({'blocks': [{'type': 'paragraph', 'text': 'Delivered'}]})])
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), '')
            report = json.loads(stderr.getvalue())
            self.assertTrue(report['output_written'])
            self.assertEqual(report['publication_outcome'], 'published_verified')
            self.assertFalse(report['cleanup_complete'])
            self.assertEqual(read_bytes(Path('new.wps').read_bytes())['text'], 'Delivered')
            self.assertTrue(report['cleanup_issues'])

    def test_success_adds_independent_publication_and_cleanup_states(self):
        with tempfile.TemporaryDirectory() as directory:
            result = create_document({'blocks': [{'type': 'paragraph', 'text': 'OK'}]}, Path(directory) / 'new.wps')
            self.assertTrue(result['output_written'])
            self.assertTrue(result['cleanup_complete'])
            self.assertEqual(result['publication_verification'], 'identity_and_bytes_verified')

    def test_publication_acknowledgement_failure_is_uncertain_not_unwritten(self):
        original_link = os.link
        with tempfile.TemporaryDirectory() as directory, scoped(directory):
            def interrupted(source, destination, **kwargs):
                original_link(source, destination, **kwargs)
                raise OSError('injected lost acknowledgement')
            with self.linking(interrupted), self.assertRaises(OSError) as raised:
                create_document({'blocks': [{'type': 'paragraph', 'text': 'Inspect'}]}, Path('new.wps'))
            self.assertIsNone(raised.exception.publication['output_written'])
            self.assertEqual(raised.exception.publication['publication_outcome'], 'publication_uncertain')
            self.assertTrue(Path('new.wps').is_file())

    def test_intermediate_failure_does_not_claim_final_output_was_written(self):
        import export_wps
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'final.wps'
            source.write_bytes(package('<w:p><w:r><w:t>X</w:t></w:r></w:p>'))
            def failed_stage(_source, candidate):
                error = OSError('staged cleanup failure')
                error.publication = {'output_written': True, 'publication_target': candidate,
                                     'publication_outcome': 'published_verified', 'cleanup_complete': False}
                raise error
            with patch('export_wps.convert_docx_to_rtf_wps', side_effect=failed_stage):
                report = export_wps.convert_docx_to_wps_rtf(source, target)
            self.assertFalse(report['output_written'])
            self.assertEqual(report['publication_outcome'], 'not_attempted')
            self.assertIn('intermediate_publication', report)
            self.assertFalse(target.exists())

    def test_source_format_omissions_are_separate_from_emitted_control_check(self):
        body = ('<w:p><w:pPr><w:spacing w:line="600" w:lineRule="atLeast"/><w:keepNext/>'
                '<w:pageBreakBefore/></w:pPr><w:r><w:t>Text</w:t></w:r></w:p>')
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'out.wps'
            source.write_bytes(package(body))
            result = convert_docx_to_rtf_wps(str(source), str(target))
            self.assertEqual(result['status'], 'partial')
            properties = {issue.get('property') for issue in result['conversion_issues']}
            self.assertTrue({'spacing', 'keepNext', 'pageBreakBefore'} <= properties)
            self.assertEqual(result['source_format_mapping']['status'], 'detected_omissions')
            self.assertEqual(result['format_validation']['status'], 'verified_raw_controls')
            self.assertEqual(read_bytes(target.read_bytes())['text'], 'Text')

    def test_mapping_diagnostics_are_bounded_and_exclude_deleted_properties(self):
        namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        root = fromstring(f'<w:document xmlns:w="{namespace}"><w:body>'
                          + '<w:p><w:pPr><w:keepNext/></w:pPr></w:p>' * 140
                          + '<w:del><w:p><w:pPr><w:pageBreakBefore/></w:pPr></w:p></w:del></w:body></w:document>')
        issues = format_mapping_diagnostics(root)
        self.assertEqual(len(issues), 129)
        self.assertEqual(issues[-1], {'code': 'source_format_diagnostics_truncated', 'total': 140, 'omitted': 12})
        self.assertNotIn('pageBreakBefore', {issue.get('property') for issue in issues})

    def test_font_fallback_and_script_font_loss_are_explicit(self):
        namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        root = fromstring(f'<w:document xmlns:w="{namespace}"><w:p><w:r><w:rPr>'
                          '<w:rFonts w:eastAsia="Unknown Font" w:ascii="Arial"/></w:rPr><w:t>X</w:t></w:r></w:p></w:document>')
        issues = format_mapping_diagnostics(root)
        self.assertEqual({issue['reason'] for issue in issues}, {'font_name_fallback', 'separate_script_font_not_mapped'})


if __name__ == '__main__':
    unittest.main()
