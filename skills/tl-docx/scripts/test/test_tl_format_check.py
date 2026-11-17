"""Synthetic external-candidate checks with no input mutation or publication."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx import Document
from docx.oxml.ns import qn
from docx.opc.oxml import serialize_part_xml
import tl_edit
from tl_format_check import check_format
from tl_package import Package


def source():
    """Build only public synthetic text and simple table/story content."""
    document = Document()
    document.add_paragraph('alpha').add_run(' beta')
    document.add_table(1, 1).cell(0, 0).text = 'cell'
    document.sections[0].header.paragraphs[0].text = 'header'
    stream = BytesIO()
    document.save(stream)
    return Package(stream.getvalue())


def request(kind='format_run'):
    """Address an exact target with the edit JSON contract."""
    value = {'type': kind, 'paragraph_index': 0, 'run_index': 0,
             'expected_text': 'alpha beta', 'expected_run_text': 'alpha', 'format': {'bold': True}}
    if kind == 'format_cell_run':
        value.update(table_index=0, row_index=0, cell_index=0, expected_text='cell', expected_run_text='cell')
    if kind == 'format_story_run':
        value.update(part='word/header1.xml', expected_text='header', expected_run_text='header')
    return {'operations': [value]}


def external(original, specification):
    """Simulate an external editor, independently inserting bold XML."""
    operation = specification['operations'][0]
    part = operation.get('part', original.main)
    root = deepcopy(original.xml(part))
    run = next(root.iter(qn('w:r'))) if operation['type'] != 'format_cell_run' else next(root.iter(qn('w:tc'))).find('.//' + qn('w:r'))
    props = run.get_or_add_rPr()
    props.get_or_add_b()
    return Package(original.overlay({part: serialize_part_xml(root)}))


class ExternalFormatCheck(unittest.TestCase):
    """Constrain external changes, not just package validity."""
    def test_three_scopes_without_calling_setters(self):
        for kind in ('format_run', 'format_cell_run', 'format_story_run'):
            with self.subTest(kind=kind):
                before = source()
                spec = request(kind)
                after = external(before, spec)
                snapshot = deepcopy(spec)
                with patch.object(tl_edit, 'format_run', side_effect=AssertionError('setter called')):
                    result = check_format(before, after, spec)
                self.assertEqual(result['verification'], 'passed')
                self.assertTrue(result['read_only'])
                self.assertEqual(spec, snapshot)

    def test_unrelated_part_and_member_set_rejected(self):
        before = source()
        good = external(before, request())
        for changes in ({'word/styles.xml': good.read('word/styles.xml') + b' '}, {'extra.txt': b'new'}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                check_format(before, Package(good.overlay(changes)), request())

    def test_text_non_target_and_unrequested_format_rejected(self):
        before = source()
        good = external(before, request())
        for mutation in ('text', 'other_run', 'italic'):
            root = deepcopy(good.xml(good.main))
            runs = list(root.iter(qn('w:r')))
            if mutation == 'text':
                next(root.iter(qn('w:t'))).text = 'corrupt'
            elif mutation == 'other_run':
                runs[1].get_or_add_rPr().get_or_add_b()
            else:
                runs[0].get_or_add_rPr().get_or_add_i()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check_format(before, Package(good.overlay({good.main: serialize_part_xml(root)})), request())

    def test_invalid_values_and_multiple_operations_rejected(self):
        before = source()
        good = external(before, request())
        for formatting in ({}, {'bold': 1}, {'size_pt': True}, {'size_pt': float('nan')},
                           {'color': 'red'}, {'font_ascii': ''}, {'unsupported': 1}):
            spec = request()
            spec['operations'][0]['format'] = formatting
            with self.subTest(formatting=formatting), self.assertRaises(ValueError):
                check_format(before, good, spec)
        for operations in ([], request()['operations'] * 2, [{'type': 'replace_run_text'}]):
            with self.assertRaises(ValueError):
                check_format(before, good, {'operations': operations})

    def test_stale_anchor_same_bytes_and_unsupported_source_rejected(self):
        before = source()
        good = external(before, request())
        spec = request()
        spec['operations'][0]['expected_text'] = 'stale'
        with self.assertRaises(ValueError):
            check_format(before, good, spec)
        with self.assertRaises(ValueError):
            check_format(before, before, request())
        signed = Package(before.overlay({'_xmlsignatures/sig1.xml': b'<signature/>'}))
        candidate = external(signed, request())
        with self.assertRaises(ValueError):
            check_format(signed, candidate, request())

    def test_shared_story_rejected(self):
        before = source()
        root = deepcopy(before.xml(before.main))
        section = next(root.iter(qn('w:sectPr')))
        ref = deepcopy(section.find(qn('w:headerReference')))
        ref.set(qn('w:type'), 'even')
        section.append(ref)
        shared = Package(before.overlay({before.main: serialize_part_xml(root)}))
        with self.assertRaises(ValueError):
            check_format(shared, external(shared, request('format_story_run')), request('format_story_run'))

    def test_cli_read_only_success_failure_and_path_safety(self):
        before = source()
        after = external(before, request())
        cli = Path(__file__).resolve().parents[1] / 'tl_docx.py'
        with tempfile.TemporaryDirectory(prefix='tl-format-check-') as folder:
            root = Path(folder)
            (root / 'before.docx').write_bytes(before.data)
            (root / 'after.docx').write_bytes(after.data)
            os.link(root / 'before.docx', root / 'alias.docx')
            (root / 'link.docx').symlink_to(root / 'after.docx')
            initial = {p.name: (p.lstat().st_ino, p.lstat().st_mtime_ns) for p in root.iterdir()}
            for candidate, code in (('after.docx', 0), ('before.docx', 1), ('alias.docx', 1),
                                    ('link.docx', 1), ('../outside.docx', 1)):
                result = subprocess.run([sys.executable, '-I', '-B', str(cli), 'verify-format',
                                         'before.docx', candidate], input=json.dumps(request()).encode(),
                                        cwd=folder, capture_output=True, check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                response = json.loads(result.stdout)
                self.assertNotIn(folder, result.stdout.decode())
                if code:
                    self.assertFalse(response['retryable'])
                else:
                    self.assertEqual(response['status'], 'format_checked')
            self.assertEqual(initial, {p.name: (p.lstat().st_ino, p.lstat().st_mtime_ns) for p in root.iterdir()})
            self.assertEqual((root / 'before.docx').read_bytes(), before.data)
            self.assertEqual((root / 'after.docx').read_bytes(), after.data)

    def test_cli_distinct_inode_and_noop_codes(self):
        before = source()
        cli = Path(__file__).resolve().parents[1] / 'tl_docx.py'
        with tempfile.TemporaryDirectory(prefix='tl-format-identity-') as folder:
            root = Path(folder)
            (root / 'original.docx').write_bytes(before.data)
            (root / 'copy.docx').write_bytes(before.data)
            os.link(root / 'original.docx', root / 'hardlink.docx')
            for name, wanted in (('copy.docx', 'format_check_no_change'),
                                 ('hardlink.docx', 'format_check_inputs_not_distinct')):
                result = subprocess.run([sys.executable, '-I', '-B', str(cli), 'verify-format',
                                         'original.docx', name], input=json.dumps(request()).encode(),
                                        cwd=folder, capture_output=True, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)['error_code'], wanted)

    def test_same_opened_identity_rejected_even_if_bytes_change(self):
        import tl_docx
        import tl_io
        before = source()
        after = external(before, request())
        identity = (7, 42, len(before.data), 1, 1)
        args = tl_docx.arguments(['verify-format', 'original.docx', 'candidate.docx'])
        with patch.object(tl_io, 'read_task', side_effect=[(before.data, identity), (after.data, identity)]):
            with self.assertRaises(ValueError) as failure:
                tl_docx.execute(args)
        self.assertEqual(failure.exception.code, 'format_check_inputs_not_distinct')

    def test_merged_origin_request_uses_geometry_and_guard(self):
        from test_tl_merged_cell_edit import fixture, request as merged_request
        before = fixture()
        operation = merged_request(kind='format_cell_run')
        spec = {'operations': [operation]}
        data, _receipt = tl_edit.edit_document(before, spec, None)
        with patch.object(tl_edit, 'format_run', side_effect=AssertionError('setter called')):
            self.assertEqual(check_format(before, Package(data), spec)['verification'], 'passed')
        wrong = deepcopy(spec)
        wrong['operations'][0]['expected_merge']['colspan'] = 1
        with self.assertRaises(ValueError):
            check_format(before, Package(data), wrong)

    def test_resource_error_remains_classified(self):
        import tl_docx
        from tl_resource_limits import ResourceLimitError
        before = source()
        after = external(before, request())
        with tempfile.TemporaryDirectory(prefix='tl-format-resources-') as folder:
            root = Path(folder)
            (root / 'before.docx').write_bytes(before.data)
            (root / 'after.docx').write_bytes(after.data)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            from contextlib import contextmanager
            @contextmanager
            def task():
                yield descriptor
            from types import SimpleNamespace
            try:
                args = tl_docx.arguments(['verify-format', 'before.docx', 'after.docx'])
                with patch('tl_io.task_root', task), patch.object(tl_docx.sys, 'stdin', SimpleNamespace(buffer=BytesIO(json.dumps(request()).encode()))):
                    with patch('tl_format_check.check_format', side_effect=ResourceLimitError('xml_nodes')):
                        with self.assertRaises(ResourceLimitError):
                            tl_docx.execute(args)
            finally:
                os.close(descriptor)

    def test_resource_failure_does_not_publish_or_create_cache(self):
        import tl_docx
        from tl_resource_limits import LIMITS, ResourceLimitError
        from contextlib import contextmanager
        from types import SimpleNamespace
        before = source()
        after = external(before, request())
        with tempfile.TemporaryDirectory(prefix='tl-entry-resource-') as folder:
            root = Path(folder)
            (root / 'before.docx').write_bytes(before.data)
            (root / 'after.docx').write_bytes(after.data)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            @contextmanager
            def task():
                yield descriptor
            try:
                for argv, spec in ((['create', 'never.docx'], {'blocks': [{'type': 'paragraph', 'text': 'x'}]}),
                                   (['edit', 'before.docx', 'never.docx'], request()),
                                   (['read', 'before.docx', '--cache', '--max-output-bytes', '512'], {}),
                                   (['verify-format', 'before.docx', 'after.docx'], request())):
                    with self.subTest(action=argv[0]), patch('tl_io.task_root', task), patch.dict(LIMITS, xml_nodes=1):
                        with patch.object(tl_docx.sys, 'stdin', SimpleNamespace(buffer=BytesIO(json.dumps(spec).encode()))):
                            with patch.object(tl_docx, 'finish') as publish:
                                with self.assertRaises(ResourceLimitError):
                                    tl_docx.execute(tl_docx.arguments(argv))
                                publish.assert_not_called()
                    self.assertEqual({'before.docx', 'after.docx'}, {p.name for p in root.iterdir()})
            finally:
                os.close(descriptor)

    def test_capabilities_work_without_new_expat_requirement(self):
        import tl_docx
        import tl_resource_limits
        with patch.object(tl_resource_limits.expat, 'version_info', (2, 5, 0)):
            response, status = tl_docx.execute(tl_docx.arguments(['capabilities']))
        self.assertEqual(status, 0)
        self.assertIn('bounded-resource-screening', response['capabilities'])


if __name__ == '__main__':
    unittest.main()
