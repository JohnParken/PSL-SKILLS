#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdlib_xlsx import Workbook
from xlsx_identify import identify, COMPOUND_SIGNATURE


class IdentifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.bin'

    def test_family_suffix_is_routed_without_content_parsing(self):
        self.path = self.path.with_suffix('.et')
        self.path.write_bytes(b'PK not parsed')
        report = identify(self.path)
        self.assertEqual(report['status'], 'unsupported')
        self.assertFalse(report['parser_supported'])

    def test_cfb_signature_routes_even_under_native_suffix(self):
        self.path = self.path.with_suffix('.xlsx')
        self.path.write_bytes(COMPOUND_SIGNATURE)
        self.assertEqual(identify(self.path)['status'], 'unsupported')

    def test_unknown_empty_damaged_and_generic_zip(self):
        for content, status in ((b'not a spreadsheet', 'unrecognized'), (b'', 'invalid'),
                                (b'PK\x03\x04broken', 'invalid_or_unsupported')):
            self.path.write_bytes(content)
            self.assertEqual(identify(self.path)['status'], status)
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('unrelated.txt', 'data')
        self.assertEqual(identify(self.path)['status'], 'unsupported')

    def test_bounded_input_and_package(self):
        self.path.write_bytes(b'123456789')
        with patch('xlsx_identify.MAX_FILE_BYTES', 8):
            self.assertEqual(identify(self.path)['status'], 'limit_exceeded')
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('one', 'one')
        with patch('xlsx_identify.MAX_BYTES', 1):
            self.assertEqual(identify(self.path)['status'], 'limit_exceeded')

    def test_cli_json_and_missing_input(self):
        self.path.write_bytes(b'unknown')
        command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('xlsx_identify.py')), str(self.path)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)['status'], 'unrecognized')
        self.path.unlink()
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
