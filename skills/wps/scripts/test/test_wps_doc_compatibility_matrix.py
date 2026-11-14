"""Content routing and FIB contracts; not WPS application-version certification."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import ParseError, read_bytes
from test_wps import document, replace_word, synthetic_cfb
from test_wps_maximum_reading import ooxml_with_textbox

SCRIPT = Path(__file__).resolve().parents[1] / 'read_wps.py'


def versioned(version, table_one=False):
    streams = document([('正文 中文\r'.encode('utf-16le'), False)], table_one=table_one)
    pairs, words = {0xC1: (93, 0), 0xD9: (108, 2), 0x101: (136, 2),
                    0x10C: (164, 2), 0x112: (183, 5)}[version]
    if version != 0xC1:
        offset = 154 + pairs * 8
        for position, value in ((152, pairs), (898, 0), (offset, words), (offset + 2, version)):
            streams = replace_word(streams, position, value, 2)
    return streams


def packed(streams):
    return synthetic_cfb({name[0]: value for name, value in streams.items()})


class DocCompatibilityMatrixTests(unittest.TestCase):
    def test_five_binary_layouts_both_table_streams_default_maximum(self):
        for version in (0xC1, 0xD9, 0x101, 0x10C, 0x112):
            for table_one in (False, True):
                with self.subTest(version=version, table_one=table_one):
                    result = read_bytes(packed(versioned(version, table_one)))
                    self.assertEqual(result['text'], '正文 中文\n')
                    self.assertEqual(result['fib_version'], hex(version))
                    self.assertFalse(result['producer_verified'])
                    self.assertEqual(result['reading_mode'], 'maximum')
                    self.assertFalse(result['current_view_available'])

    def test_doc_cli_routes_actual_binary_rtf_and_ooxml_content(self):
        samples = [(packed(versioned(0x10C)), 'ms_doc_subset', '正文 中文'),
                   (b'{\\rtf1\\ansi Hello\\par}', 'rtf', 'Hello'),
                   (ooxml_with_textbox(), 'ooxml-word', 'Main')]
        with tempfile.TemporaryDirectory(prefix='wps-doc-routing-') as directory:
            source = Path(directory) / 'sample.doc'
            for data, family, expected in samples:
                source.write_bytes(data)
                completed = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT),
                                            source.name], cwd=directory, capture_output=True, check=False)
                self.assertIn(completed.returncode, (0, 3))
                result = json.loads(completed.stdout)
                self.assertEqual(result['format'], family)
                self.assertIn(expected, result['text'])
                self.assertEqual(source.read_bytes(), data)

    def test_unknown_version_and_encryption_not_guessed_from_doc_name(self):
        cases = [replace_word(versioned(0xC1), 2, 0xFFFF, 2),
                 replace_word(versioned(0xC1), 10, 0x1100, 2)]
        for streams in cases:
            with self.assertRaises(ParseError):
                read_bytes(packed(streams))


if __name__ == '__main__':
    unittest.main()
