"""Regression coverage for protected audit outputs and advisory delivery receipts."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
import validate_layout
from read_docx import read_docx

SCRIPTS = Path(__file__).resolve().parent
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


@contextmanager
def task_directory(path):
    """Limit in-process failure injection to this test's exclusive directory."""
    previous = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


class LayoutDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='docx-layout-delivery-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'source.docx'
        self.spec = {'blocks': [{'type': 'paragraph', 'text': '原件正文'}]}
        create_docx.create_document(self.spec, self.source, verify=True)
        self.original = self.source.read_bytes()
        self.environment = dict(os.environ, PATH='')
        self.environment.pop('ROUTER_CONFIG_VARIABLES', None)

    def cli(self, script, *arguments, spec=None):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *arguments],
            cwd=self.work, env=self.environment, capture_output=True, encoding='utf-8',
            input=None if spec is None else json.dumps(spec, ensure_ascii=False),
        )

    def audit_main(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with task_directory(self.work), redirect_stdout(stdout), redirect_stderr(stderr):
            code = validate_layout.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_audit_stdout_is_read_only_and_new_report_matches(self):
        result = self.cli('validate_layout.py', './source.docx')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = json.loads(result.stdout)
        self.assertEqual(set(self.work.iterdir()), {self.source})
        (self.work / 'reports').mkdir()
        result = self.cli('validate_layout.py', 'source.docx', '-o', './reports/layout.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(json.loads((self.work / 'reports/layout.json').read_text(encoding='utf-8')), expected)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_audit_rejects_original_aliases_and_existing_files(self):
        os.link(self.source, self.work / 'hardlink.docx')
        (self.work / 'symlink.docx').symlink_to('source.docx')
        (self.work / 'dangling.json').symlink_to('absent.json')
        (self.work / 'existing.json').write_bytes(b'previous report')
        for target in ('source.docx', './source.docx', 'hardlink.docx', 'symlink.docx',
                       'dangling.json', 'existing.json'):
            with self.subTest(target=target):
                result = self.cli('validate_layout.py', 'source.docx', '-o', target)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('layout-audit-error:', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(self.source.read_bytes(), self.original)
                self.assertEqual((self.work / 'existing.json').read_bytes(), b'previous report')
        self.assertFalse((self.work / 'absent.json').exists())
        self.assertTrue((self.work / 'symlink.docx').is_symlink())

    def test_audit_rejects_task_boundary_and_symlink_parents(self):
        (self.work / 'real').mkdir()
        (self.work / 'linked').symlink_to('real', target_is_directory=True)
        cases = [('source.docx', '-o', str(self.work / 'absolute.json')),
                 ('source.docx', '-o', '../outside.json'),
                 ('source.docx', '-o', 'linked/report.json'),
                 (str(self.source),), ('../source.docx',)]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.cli('validate_layout.py', *arguments)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')
        self.assertEqual(list((self.work / 'real').iterdir()), [])
        self.assertFalse((self.work / 'absolute.json').exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_audit_write_failure_leaves_no_partial_report_or_staging(self):
        with patch('controlled_cleanup.os.fsync', side_effect=OSError('injected sync failure')):
            code, stdout, stderr = self.audit_main('source.docx', '-o', 'report.json')
        self.assertEqual(code, 1)
        self.assertEqual(stdout, '')
        self.assertIn('injected sync failure', stderr)
        self.assertEqual(set(self.work.iterdir()), {self.source})
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_audit_competing_output_is_not_replaced(self):
        real_link = os.link

        def competing_link(*args, **kwargs):
            (self.work / 'report.json').write_bytes(b'other task result')
            return real_link(*args, **kwargs)

        with patch('controlled_cleanup.os.link', side_effect=competing_link):
            # The capability gate checks the actual primitive's identity.
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {os.link}):
                code, stdout, stderr = self.audit_main('source.docx', '-o', 'report.json')
        self.assertEqual(code, 1, stdout + stderr)
        self.assertEqual((self.work / 'report.json').read_bytes(), b'other task result')
        self.assertEqual({p.name for p in self.work.iterdir()}, {'source.docx', 'report.json'})
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_saved_audit_preserves_strict_and_invalid_exit_codes(self):
        create_docx.create_document({'blocks': [*self.spec['blocks'], {'type': 'page_break'}]}, self.source, verify=True)
        result = self.cli('validate_layout.py', 'source.docx', '--strict-pagination', '-o', 'warning.json')
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads((self.work / 'warning.json').read_text(encoding='utf-8'))
        self.assertTrue(report['structure_valid'])
        self.assertIn('TRAILING_PAGE_BREAK', {item['code'] for item in report['issues']})
        (self.work / 'invalid.docx').write_bytes(b'not a DOCX')
        result = self.cli('validate_layout.py', 'invalid.docx', '-o', 'invalid.json')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads((self.work / 'invalid.json').read_text(encoding='utf-8'))['structure_valid'])

    def test_audit_missing_safe_write_capability_stops_without_output(self):
        with patch.object(os, 'supports_dir_fd', set()):
            code, stdout, stderr = self.audit_main('source.docx', '-o', 'report.json')
        self.assertEqual(code, 1)
        self.assertEqual(stdout, '')
        self.assertIn('unavailable', stderr)
        self.assertEqual(set(self.work.iterdir()), {self.source})

    @staticmethod
    def separated_heading():
        return {'blocks': [{'type': 'heading', 'level': 1, 'text': '章节'},
                           {'type': 'page_break'}, {'type': 'paragraph', 'text': '正文'}]}

    def test_full_and_compact_runner_receipts_keep_advisories_and_content(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                name = 'compact.docx' if compact else 'full.docx'
                arguments = ['create', name] + (['--compact-receipt'] if compact else [])
                result = self.cli('run_docx_task.py', *arguments, spec=self.separated_heading())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['status'], 'verified')
                self.assertEqual(receipt['task_status'], 'completed')
                self.assertTrue(receipt['cleanup']['cleanup_complete'])
                self.assertTrue(receipt['cleanup']['manifest_removed'])
                self.assertTrue(receipt['cleanup']['deliverables_unchanged'])
                self.assertEqual(len(receipt['warnings']), 1)
                self.assertIn('HEADING_SEPARATED_FROM_CONTENT', receipt['warnings'][0])
                # The explicit break contributes one newline, in addition to
                # the two separators around its standalone paragraph.
                self.assertEqual(read_docx(self.work / name), '章节\n\n\n正文')
                with ZipFile(self.work / name) as archive:
                    document = ET.fromstring(archive.read('word/document.xml'))
                self.assertEqual(len([br for br in document.iter(W + 'br') if br.get(W + 'type') == 'page']), 1)
                self.assertFalse(list(document.iter(W + 'pageBreakBefore')))

    def test_delivery_preserves_archive_warning_alongside_layout_warning(self):
        with task_directory(self.work), patch.dict(os.environ, {}, clear=True):
            with patch('archive_deliverables.finalize_delivery', return_value={
                    'status': 'verified', 'archive_status': 'failed', 'files': [], 'warnings': ['archive failed']}):
                result = create_docx.create_document_delivery(self.separated_heading(), 'delivery.docx')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertEqual(result['warnings'][0], 'archive failed')
        self.assertIn('HEADING_SEPARATED_FROM_CONTENT', result['warnings'][1])

    def test_legacy_cli_labels_layout_warning_correctly(self):
        (self.work / 'spec.json').write_text(json.dumps(self.separated_heading()), encoding='utf-8')
        result = self.cli('create_docx.py', 'spec.json', 'legacy.docx', '--verify')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('layout-warning: HEADING_SEPARATED_FROM_CONTENT', result.stderr)
        self.assertNotIn('archive-warning: layout-warning:', result.stderr)
        self.assertIn('HEADING_SEPARATED_FROM_CONTENT', json.loads(result.stdout)['warnings'][0])

    def test_static_errors_still_block_publication(self):
        report = {'issues': [{'level': 'error', 'code': 'INVALID_SECTION_END',
                              'location': 'word/document.xml', 'message': 'invalid final section'}]}
        with task_directory(self.work), patch('office.formatting.audit.audit_roots', return_value=report):
            with self.assertRaisesRegex(ValueError, 'Generated static layout validation failed'):
                create_docx.create_document_delivery(self.spec, 'rejected.docx')
        self.assertEqual(set(self.work.iterdir()), {self.source})
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_many_layout_warnings_have_explicit_omission_count(self):
        spec = {'blocks': [{'type': 'page_break'} for _ in range(110)]}
        result = self.cli('run_docx_task.py', 'create', 'many.docx', '--compact-receipt', spec=spec)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        warnings = json.loads(result.stdout)['warnings']
        self.assertEqual(len(warnings), 101)
        self.assertIn('11 additional layout warnings omitted', warnings[-1])

    def test_natural_pagination_receipt_has_no_layout_warnings(self):
        spec = {'blocks': [{'type': 'title', 'text': '短诗'}, {'type': 'paragraph', 'text': '第一行'},
                           {'type': 'paragraph', 'text': '第二行'}]}
        result = self.cli('run_docx_task.py', 'create', 'natural.docx', '--compact-receipt', spec=spec)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['warnings'], [])
        with ZipFile(self.work / 'natural.docx') as archive:
            document = ET.fromstring(archive.read('word/document.xml'))
        self.assertFalse(list(document.iter(W + 'br')))
        self.assertFalse(list(document.iter(W + 'pageBreakBefore')))


if __name__ == '__main__':
    unittest.main()
