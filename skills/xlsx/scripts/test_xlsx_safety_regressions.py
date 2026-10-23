#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Regression coverage for hostile inputs and preservation of caller data."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stdlib_xlsx as x
from xlsx_xml import parse_xml
from xlsx_unpack import unpack
from xlsx_pack import pack, validate_xml_files
from xlsx_result_export import management_table, Report

SCRIPTS = Path(__file__).resolve().parent


def spec(cells=None):
    return {'sheets': [{'name': 'S', 'cells': cells or {}}]}


class SafetyRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, script, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script),
                               *map(str, args)], capture_output=True, text=True, timeout=15)

    def test_xml_declarations_rejected_for_all_encodings_and_builders(self):
        for encoding in ('utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'):
            for body in ('<!DOCTYPE x [<!ENTITY a "expanded">]><x>&a;</x>',
                         '<!DOCTYPE x SYSTEM "file:///nonexistent"><x/>',
                         '<!DOCTYPE x><x/>'):
                for dom in (False, True):
                    with self.subTest(encoding=encoding, body=body, dom=dom):
                        with self.assertRaisesRegex(ValueError, 'DTD/entity'):
                            parse_xml(body.encode(encoding), dom=dom)

    def test_valid_utf16_xml_and_predefined_entities(self):
        for encoding in ('utf-8', 'utf-16', 'utf-16-be'):
            self.assertEqual(parse_xml('<x>中&amp;文</x>'.encode(encoding)).text, '中&文')

    def test_read_cli_rejects_utf16_workbook_dtd(self):
        book = x.Workbook.create(spec())
        content = book.parts['xl/workbook.xml'].decode().split('?>', 1)[-1]
        book.parts['xl/workbook.xml'] = ('<!DOCTYPE workbook [<!ENTITY a "expanded">]>' + content).encode('utf-16')
        path = self.root / 'hostile.xlsx'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in book.parts.items():
                archive.writestr(name, data)
        result = self.cli('stdlib_xlsx.py', 'read', path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'failed')
        self.assertNotIn('Traceback', result.stderr)

    def test_calc_chain_rejects_dtd_in_both_parts(self):
        for target in ('xl/_rels/workbook.xml.rels', '[Content_Types].xml'):
            for encoding in ('utf-8', 'utf-16', 'utf-16-be'):
                with self.subTest(target=target, encoding=encoding):
                    book = x.Workbook.create(spec({'A1': {'formula': '1+1'}}))
                    root = x.xml(book.parts['xl/_rels/workbook.xml.rels'])
                    x.ET.SubElement(root, '{'+x.PKG+'}Relationship',
                                    {'Id': 'chain', 'Type': x.REL+'/calcChain', 'Target': 'calcChain.xml'})
                    book.parts['xl/_rels/workbook.xml.rels'] = x.encoded_xml(root)
                    book.parts['xl/calcChain.xml'] = b'<x/>'
                    content = book.parts[target].decode().split('?>', 1)[-1]
                    book.parts[target] = ('<!DOCTYPE x [<!ENTITY a "expanded">]>'+content).encode(encoding)
                    with self.assertRaisesRegex(ValueError, 'DTD/entity'):
                        book.calculate()

    def test_unpack_preserves_existing_directory_even_for_bad_zip(self):
        output = self.root / 'existing'
        output.mkdir()
        sentinel = output / 'user.txt'
        sentinel.write_bytes(b'keep me')
        source = self.root / 'bad.xlsx'
        source.write_bytes(b'not a zip')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            unpack(str(source), str(output))
        self.assertEqual(sentinel.read_bytes(), b'keep me')

    def test_unpack_rejects_hostile_zip_before_output_creation(self):
        for name, payload in (('../escape', b'x'), ('xl/a.xml', b'<!DOCTYPE x><x/>')):
            source = self.root / 'bad.xlsx'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr(name, payload)
            output = self.root / 'output'
            with self.assertRaises(ValueError):
                unpack(str(source), str(output))
            self.assertFalse(output.exists())
        with patch('xlsx_unpack.MAX_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'limits'):
                unpack(str(source), str(output))

    def test_legacy_pack_roundtrip_and_overwrite_refusal(self):
        original = self.root / 'original.xlsx'
        x.Workbook.create(spec({'A1': {'value': '中'}})).save(original)
        folder = self.root / 'unpacked'
        output = self.root / 'packed.xlsx'
        with contextlib.redirect_stdout(io.StringIO()):
            unpack(str(original), str(folder))
            pack(str(folder), str(output))
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                pack(str(folder), str(output))
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(x.Workbook.load(output).cells['S']['A1']['value'], '中')
        (folder / 'hostile.xml').write_bytes('<!DOCTYPE x><x/>'.encode('utf-16'))
        self.assertTrue(validate_xml_files(str(folder)))

    def test_csv_cli_failures_are_json(self):
        source = self.root / 'input.csv'
        for payload, extra in ((b'x'*131073, []), (b'\xff', []), (b'x', ['--encoding', 'not-an-encoding'])):
            source.write_bytes(payload)
            result = self.cli('stdlib_xlsx.py', 'read', source, *extra)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'failed')
            self.assertNotIn('Traceback', result.stderr)

    def test_csv_budgets_and_valid_quoted_rows(self):
        source = self.root / 'input.csv'
        source.write_bytes(b'"a\nb",c\n')
        self.assertEqual(x.read_csv(source)['rows'], [['a\nb', 'c']])
        with patch.object(x, 'MAX_CSV_BYTES', 2):
            with self.assertRaisesRegex(ValueError, 'byte limit'):
                x.read_csv(source)
        with patch.object(x, 'MAX_CELLS', 1):
            with self.assertRaisesRegex(ValueError, 'row/field limit'):
                x.read_csv(source)
            source.write_bytes(b'\n\n')
            with self.assertRaisesRegex(ValueError, 'row/field limit'):
                x.read_csv(source)

    def test_missing_styles_and_headers_raise_value_error(self):
        book = x.Workbook.create(spec())
        book.parts['xl/styles.xml'] = ('<styleSheet xmlns="'+x.MAIN+'"/>').encode()
        with self.assertRaisesRegex(ValueError, 'style index'):
            book.patch(spec({'A1': {'value': 1, 'style': 0}}))
        with self.assertRaisesRegex(ValueError, 'headers'):
            management_table({'schema_version': 'management_wide_v1', 'rows': [],
                              'columns': ['["dimension","name"]']}, Report(), {})

    def test_missing_style_and_headers_cli_fail_without_output(self):
        book = x.Workbook.create(spec())
        book.parts['xl/styles.xml'] = ('<styleSheet xmlns="'+x.MAIN+'"/>').encode()
        source = self.root / 'source.xlsx'
        book.save(source)
        patch_file = self.root / 'patch.json'
        patch_file.write_text(json.dumps(spec({'A1': {'value': 1, 'style': 0}})))
        output = self.root / 'out.xlsx'
        result = self.cli('stdlib_xlsx.py', 'edit', source, patch_file, output)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['output_written'])
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())
        report = self.root / 'report.json'
        report.write_text(json.dumps({'status': 'management_wide', 'sheet': 'S',
                                     'range': 'A1', 'schema_version': 'management_wide_v1',
                                     'rows': [], 'columns': ['["dimension","name"]'],
                                     'output_rows': 0}))
        result = self.cli('xlsx_result_export.py', report, output)
        self.assertEqual(result.returncode, 1)
        self.assertIn('headers', json.loads(result.stdout)['message'])
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())

    def test_json_bom_supported_with_and_without_budget(self):
        source = self.root / 'spec.json'
        source.write_text(json.dumps(spec()), encoding='utf-8-sig')
        self.assertEqual(x.read_spec(source), spec())
        self.assertEqual(x.read_spec(source, max_bytes=1024), spec())

    def test_unsafe_legacy_commands_refuse_before_writing(self):
        sentinel = self.root / 'sheet.xml'
        sentinel.write_bytes(b'LOG10(B5) SALES2025!B5')
        for script in ('xlsx_shift_rows.py', 'xlsx_insert_row.py'):
            result = self.cli(script, self.root, 'insert', '5', '1')
            self.assertEqual(result.returncode, 1)
            self.assertIn('disabled', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(sentinel.read_bytes(), b'LOG10(B5) SALES2025!B5')


if __name__ == '__main__':
    unittest.main()
