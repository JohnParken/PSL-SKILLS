"""Dependency-free Boolean adapter security and unsupported-input regression."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_geometry import boolean_document
from tl_guard import ContractError

SOURCE = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
          b'<rect id="a" width="10" height="10"/>'
          b'<rect id="b" x="5" width="10" height="10"/></svg>')
CONFIG = {'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged'}

@contextmanager
def task_directory():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.chdir(directory)
            yield Path(directory)
        finally:
            os.chdir(previous)

class GeometryAdapterTests(unittest.TestCase):
    def test_reject_escape_and_external_resources(self):
        with task_directory():
            Path('input.svg').write_bytes(SOURCE)
            for name in ('../input.svg', './input.svg', '/input.svg'):
                with self.assertRaises(ContractError):
                    boolean_document(name, CONFIG)
            with self.assertRaises(ContractError):
                boolean_document('input.svg', {**CONFIG, 'fonts': ['../secret.ttf']})
            with self.assertRaises(ContractError):
                boolean_document('input.svg', {**CONFIG, 'style': {'fill': 'url(https://example.com/a)'}})
            Path('input.svg').write_bytes(SOURCE.replace(b'</svg>', b'<image href="private.png"/></svg>'))
            with self.assertRaises(ContractError):
                boolean_document('input.svg', CONFIG)

    def test_unsupported_text_fonts_transforms_preserve_source(self):
        variants = [SOURCE.replace(b'<rect id="b" x="5" width="10" height="10"/>',b'<text id="b">X</text>'),
                    SOURCE.replace(b'<rect id="a"',b'<rect transform="translate(2 3)" id="a"')]
        with task_directory() as task:
            for source in variants:
                Path('input.svg').write_bytes(source)
                for fonts in [[],['missing.ttf']]:
                    with self.assertRaises(ContractError):
                        boolean_document('input.svg',{**CONFIG,'fonts':fonts})
                self.assertEqual(Path('input.svg').read_bytes(),source)
            self.assertEqual([item.name for item in task.iterdir()],['input.svg'])

    def test_text_budget_precedes_geometry_and_font_reads(self):
        with task_directory():
            Path('input.svg').write_bytes(SOURCE.replace(b'</svg>', b'<text>' + b'X' * 5001 + b'</text></svg>'))
            with patch('tl_rect_boolean.rectangle_fragment',side_effect=AssertionError('must not run')):
                with self.assertRaisesRegex(ContractError,'text execution budget'):
                    boolean_document('input.svg',{**CONFIG,'fonts':['missing.ttf']})

    def test_paint_definitions_are_rejected_not_silently_approximated(self):
        with task_directory():
            for definition in [b'',b'<defs><linearGradient id="paint" href="#paint"/></defs>',
                               b'<defs><linearGradient id="paint" href="#a"/></defs>']:
                source = SOURCE.replace(b'</svg>',definition+b'</svg>').replace(b'id="a"',b'id="a" fill="url(#paint)"')
                Path('input.svg').write_bytes(source)
                for style in [{},{'fill':'#112233'}]:
                    with self.assertRaises(ContractError):
                        boolean_document('input.svg',{**CONFIG,'style':style})
                self.assertEqual(Path('input.svg').read_bytes(),source)

    def test_installation_directory_is_rejected_before_reading(self):
        previous = Path.cwd()
        try:
            os.chdir(Path(__file__).resolve().parents[1])
            with patch('tl_geometry.read_local',side_effect=AssertionError('must not read')):
                with self.assertRaisesRegex(ContractError,'skill installation'):
                    boolean_document('input.svg',CONFIG)
        finally:
            os.chdir(previous)

    def test_invalid_manifest_is_rejected(self):
        for config in ([], {'operation': []}, {'operation': 'union', 'source_ids': [[], []]},
                       {**CONFIG,'font_dirs':['.']}):
            with self.assertRaises(ContractError):
                boolean_document('input.svg',config)

    def test_fragment_and_stylesheet_rejected(self):
        with task_directory():
            Path('input.svg').write_bytes(SOURCE)
            with self.assertRaisesRegex(ContractError,'fragment'):
                boolean_document('input.svg',{**CONFIG,'operation':'fragment'})
            Path('input.svg').write_bytes(b'<?xml-stylesheet href="private.css"?>'+SOURCE)
            with self.assertRaises(ContractError):
                boolean_document('input.svg',CONFIG)

if __name__=='__main__':
    unittest.main()
