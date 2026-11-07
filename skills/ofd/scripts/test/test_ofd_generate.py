"""Generation profile regressions; synthetic fonts test contracts, not visual Chinese correctness."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import contextlib
import base64
import copy
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile
import zlib


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRITER = load('generate_ofd')
CHECKER = load('check_generated_ofd')


def font_fixture(fs_type=0, form=12):
    head = bytearray(54)
    struct.pack_into('>I', head, 12, 0x5F0F3CF5)
    struct.pack_into('>H', head, 18, 1000)
    struct.pack_into('>hhhh', head, 36, -100, -200, 1100, 800)
    struct.pack_into('>h', head, 50, 1)
    hhea = bytearray(36)
    struct.pack_into('>H', hhea, 34, 3)
    names = 'SyntheticTest'.encode('utf-16-be')
    name = struct.pack('>HHH', 0, 1, 18) + struct.pack('>HHHHHH', 3, 1, 0x409, 6, len(names), 0) + names
    if form == 12:
        groups = [(32, 32, 1), (38, 38, 1), (65, 66, 1), (92, 92, 1), (0x4E2D, 0x4E2D, 2)]
        subtable = struct.pack('>HHIII', 12, 0, 16 + 12 * len(groups), 0, len(groups))
        subtable += b''.join(struct.pack('>III', *group) for group in groups)
    else:
        # A -> glyph 1; terminal 0xFFFF maps to glyph 0.
        subtable = struct.pack('>HHHHHHH', 4, 32, 0, 4, 4, 1, 0)
        subtable += struct.pack('>HHHHHHHHH', 65, 0xFFFF, 0, 65, 0xFFFF, (1 - 65) & 0xFFFF, 1, 0, 0)
    tables = {b'head': bytes(head), b'maxp': struct.pack('>IH', 0x10000, 3), b'hhea': bytes(hhea),
              b'hmtx': struct.pack('>HhHhHh', 600, 0, 600, 0, 1000, 0), b'name': name,
              b'OS/2': b'\0' * 8 + struct.pack('>H', fs_type),
              b'glyf': struct.pack('>hhhhh', 1, 0, 0, 500, 700) * 3,
              b'loca': struct.pack('>IIII', 0, 10, 20, 30),
              b'cmap': struct.pack('>HHHHI', 0, 1, 3, 10 if form == 12 else 1, 12) + subtable}
    offset = 12 + 16 * len(tables)
    directory = []
    payload = bytearray()
    for tag, data in tables.items():
        directory.append(struct.pack('>4sIII', tag, 0, offset, len(data)))
        padded = data + b'\0' * (-len(data) % 4)
        payload.extend(padded)
        offset += len(padded)
    return struct.pack('>IHHHH', 0x10000, len(tables), 0, 0, 0) + b''.join(directory) + payload


def png_fixture(width=2, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    pixels = (b'\0' + b'\xff\x00\x00' * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def spec_fixture():
    return dict(schema_version='ofd-generate-1', title='Test',
                fonts={'main': dict(path='font.ttf', license_path='license.txt', embedding_permission='confirmed')},
                images={'photo': dict(path='image.png')},
                pages=[dict(width=210, height=297, margins=[10, 10, 10, 10], items=[
                    dict(kind='text', x=20, y=25, font='main', size_mm=4, text='A中 B\\'),
                    dict(kind='line', x1=20, y1=30, x2=60, y2=30),
                    dict(kind='rectangle', x=20, y=40, width=40, height=20),
                    dict(kind='image', ref='photo', x=80, y=40, width=40, height=40),
                    dict(kind='table', x=20, y=80, widths=[30, 40], row_height=12,
                         rows=[['A', '中'], ['', 'B']], font='main', size_mm=3)])])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('ofd-generate-test-' + uuid.uuid4().hex)
        self.names = ['font.ttf', 'license.txt', 'image.png', 'spec.json', 'new.ofd', 'link.ofd', 'sentinel.txt']
        self.registered = [self.root / name for name in self.names]
        self.root.mkdir(mode=0o700)
        self.assets = {'font.ttf': font_fixture(), 'license.txt': b'Synthetic test data created for this test; not a production font.',
                       'image.png': png_fixture()}

    def tearDown(self):
        remaining = list(self.root.iterdir())
        self.assertTrue(all(path in self.registered for path in remaining), 'unexpected temporary residue')
        for path in remaining:
            path.unlink()
        self.root.rmdir()

    def build(self, spec=None):
        return WRITER.Builder(spec_fixture() if spec is None else spec, lambda path, _limit: self.assets[path]).build()

    @contextlib.contextmanager
    def working(self):
        current = Path.cwd()
        os.chdir(self.root)
        try:
            yield
        finally:
            os.chdir(current)

    def test_multifeature_structure_text_and_table_cell_links(self):
        data, report = self.build()
        audited = CHECKER.audit(data)
        WRITER.validate_model(data, spec_fixture(), report, CHECKER)
        self.assertEqual([record['text'] for record in audited['pages'][0]['text']], ['A中 B\\', 'A', '中', 'B'])
        table = report['tables'][0]
        self.assertEqual((table['rows'], table['columns']), (2, 2))
        self.assertEqual(len(table['grid_object_ids']), 6)
        self.assertEqual(table['cells'][2]['object_ids'], [])
        self.assertEqual(len({cell['object_ids'][0] for cell in table['cells'] if cell['object_ids']}), 3)
        self.assertEqual(report['pages'][0]['items'][3]['boundary'], [80, 50, 40, 20])
        output = self.root / 'new.ofd'
        output.write_bytes(data)
        result = WRITER.READER.read_ofd(output)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([run['text'] for run in result['documents'][0]['pages'][0]['text_runs']], ['A中 B\\', 'A', '中', 'B'])

    def test_per_page_sizes_and_margin_overrides(self):
        spec = spec_fixture()
        spec['pages'].append(dict(width=100, height=150, margins=[5, 6, 7, 8], items=[]))
        data, _ = self.build(spec)
        self.assertEqual(len(CHECKER.audit(data)['pages']), 2)

    def test_table_bindings_and_text_are_verified_before_publication(self):
        data, report = self.build()
        for change in ('text', 'cell', 'id'):
            bad = copy.deepcopy(report)
            if change == 'text':
                spec = spec_fixture()
                spec['pages'][0]['items'][0]['text'] = 'Changed'
            else:
                spec = spec_fixture()
                cell = bad['tables'][0]['cells'][0]
                if change == 'cell':
                    cell['box'][0] += 1
                else:
                    cell['object_ids'] = bad['tables'][0]['cells'][1]['object_ids']
            with self.subTest(change=change), self.assertRaises(WRITER.GenerationError):
                WRITER.validate_model(data, spec, bad, CHECKER)

    def test_restricted_or_unconfirmed_font_rejected(self):
        for bits in (1, 2, 4, 0x200, 0x102, 0x104, 0x300, 0x308):
            self.assets['font.ttf'] = font_fixture(bits)
            with self.subTest(bits=bits), self.assertRaises(WRITER.GenerationError):
                self.build()
        self.assets['font.ttf'] = font_fixture()
        spec = spec_fixture()
        spec['fonts']['main']['embedding_permission'] = 'guessed'
        with self.assertRaises(WRITER.GenerationError):
            self.build(spec)

    def test_custom_font_path_only_without_license_reads(self):
        spec = spec_fixture()
        spec['fonts']['main'] = {'path': 'font.ttf'}
        del self.assets['license.txt']
        data, report = self.build(spec)
        self.assertIsNone(report['fonts'][0]['license_sha256'])
        self.assertIn('not assessed', report['fonts'][0]['permission'])
        self.assertEqual(CHECKER.audit(data)['status'], 'passed')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn(b'not_assessed', archive.read('OFD.xml'))
            self.assertNotIn(b'FontLicense:', archive.read('OFD.xml'))
        for bits in (2, 4, 0x200):
            self.assets['font.ttf'] = font_fixture(bits)
            with self.subTest(bits=bits), self.assertRaises(WRITER.GenerationError):
                self.build(spec)

    def test_missing_character_complex_text_and_control_rejected(self):
        for text in ('文', 'e\u0301', '\u0627', 'A\nB', '\u00a4', '\U0001f600', '\u200d', '\ud800'):
            spec = spec_fixture()
            spec['pages'][0]['items'][0]['text'] = text
            with self.subTest(text=repr(text)), self.assertRaises(WRITER.GenerationError):
                self.build(spec)

    def test_cmap4_metrics_and_coverage(self):
        font = WRITER.ASSETS.TrueType(font_fixture(form=4))
        self.assertEqual(font.measure('A', 10)['advances'], [6])
        with self.assertRaises(WRITER.GenerationError):
            font.measure('B', 10)

    def test_corrupt_font_offsets_and_variable_magic_rejected(self):
        for data in (b'OTTO' + b'\0' * 100, font_fixture()[:50], b'ttcf' + b'\0' * 100):
            with self.subTest(size=len(data)), self.assertRaises(WRITER.GenerationError):
                WRITER.ASSETS.TrueType(data)

    def test_overflow_no_truncation_or_final_file(self):
        for kind in ('text', 'table'):
            spec = spec_fixture()
            if kind == 'text':
                spec['pages'][0]['items'][0]['x'] = 205
            else:
                spec['pages'][0]['items'][-1]['rows'][0][0] = 'A' * 100
            with self.subTest(kind=kind), self.assertRaises(WRITER.GenerationError):
                self.build(spec)
        self.assertFalse((self.root / 'new.ofd').exists())

    def test_invalid_models_do_not_coerce(self):
        for key, value in (('width', True), ('height', float('nan')), ('margins', [100, 100, 150, 250]), ('items', {})):
            spec = spec_fixture()
            spec['pages'][0][key] = value
            with self.subTest(key=key), self.assertRaises(WRITER.GenerationError):
                self.build(spec)
        spec = spec_fixture()
        spec['unexpected'] = 1
        with self.assertRaises(WRITER.GenerationError):
            self.build(spec)

    def test_table_rejects_ragged_merged_and_short_rows(self):
        for change in ({'rows': [['A']]}, {'merges': [[0, 0, 1, 1]]}, {'row_height': 2}):
            spec = spec_fixture()
            spec['pages'][0]['items'][-1].update(change)
            with self.subTest(change=change), self.assertRaises(WRITER.GenerationError):
                self.build(spec)

    def test_table_alignment_matrix_uses_frozen_positions(self):
        expected_x = {'left': 21, 'center': 33.2, 'right': 45.4}
        expected_y = {'top': 81, 'middle': 84.5, 'bottom': 88}
        for align, x in expected_x.items():
            for valign, y in expected_y.items():
                with self.subTest(align=align, valign=valign):
                    spec = spec_fixture()
                    spec['pages'][0]['items'][-1].update(align=align, valign=valign)
                    data, report = self.build(spec)
                    audited = WRITER.validate_model(data, spec, report, CHECKER)
                    first = report['tables'][0]['cells'][0]['object_ids'][0]
                    obj = next(node for node in audited['pages'][0]['objects'] if node['id'] == first)
                    self.assertAlmostEqual(obj['boundary'][0], x, places=7)
                    self.assertAlmostEqual(obj['boundary'][1], y, places=7)
                    self.assertAlmostEqual(obj['boundary'][2], 3.6, places=7)
                    self.assertAlmostEqual(obj['boundary'][3], 3, places=7)

    def test_table_alignment_per_column_and_empty_cells(self):
        spec = spec_fixture()
        spec['pages'][0]['items'][-1].update(align=['right', 'center'], valign='bottom')
        data, report = self.build(spec)
        audited = WRITER.validate_model(data, spec, report, CHECKER)
        lookup = {node['id']: node for node in audited['pages'][0]['objects']}
        cells = report['tables'][0]['cells']
        self.assertAlmostEqual(lookup[cells[0]['object_ids'][0]]['boundary'][0], 45.4, places=7)
        self.assertAlmostEqual(lookup[cells[1]['object_ids'][0]]['boundary'][0], 68.2, places=7)
        self.assertEqual(cells[2]['object_ids'], [])

    def test_invalid_table_alignment_is_never_ignored(self):
        for change in ({'align': 'justify'}, {'align': []}, {'align': ['left']},
                       {'align': ['left', []]}, {'valign': 'center'}, {'valign': ['top']}):
            spec = spec_fixture()
            spec['pages'][0]['items'][-1].update(change)
            with self.subTest(change=change), self.assertRaises(WRITER.GenerationError):
                self.build(spec)

    def test_alignment_change_cannot_pass_input_model_comparison(self):
        data, report = self.build()
        spec = spec_fixture()
        spec['pages'][0]['items'][-1].update(align='right', valign='middle')
        with self.assertRaisesRegex(WRITER.GenerationError, 'requested alignment mismatch'):
            WRITER.validate_model(data, spec, report, CHECKER)

    def test_no_subsetting_font_is_embedded_whole_without_modification(self):
        original = font_fixture(fs_type=0x100)
        self.assets['font.ttf'] = original
        data, report = self.build()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read('Doc_0/Res/Font_1.ttf'), original)
        self.assertEqual(report['fonts'][0]['fs_type'], 0x100)
        self.assertEqual(report['fonts'][0]['embedding_mode'], 'full_font_unmodified')
        self.assertEqual(CHECKER.audit(data)['status'], 'passed')

    def test_editable_embedding_flags_preserved_with_explicit_license(self):
        for bits in (8, 0x108):
            original = font_fixture(fs_type=bits)
            self.assets['font.ttf'] = original
            data, report = self.build()
            with self.subTest(bits=bits), zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(archive.read('Doc_0/Res/Font_1.ttf'), original)
                self.assertEqual(report['fonts'][0]['fs_type'], bits)
        spec = spec_fixture()
        spec['fonts']['main']['embedding_permission'] = 'unknown'
        with self.assertRaises(WRITER.GenerationError):
            self.build(spec)

    def test_png_crc_decompression_and_trailing_bytes_rejected(self):
        good = png_fixture()
        for bad in (good + b'junk', good[:-2], good[:40] + bytes([good[40] ^ 1]) + good[41:]):
            with self.assertRaises(WRITER.GenerationError):
                WRITER.ASSETS.inspect_image(bad)

    def test_baseline_jpeg_header_profile_and_rejections(self):
        # A 3x2 RGB test picture created by the existing JDK ImageIO encoder.
        data = base64.b64decode(
            '/9j/4AAQSkZJRgABAgAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/'
            '2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAMDASIAAhEBAxEB/8QA'
            'HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK'
            'FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG'
            'x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC'
            'AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE'
            'hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDw/Wo44r6J'
            'Y0VFNpbMQowMmBCT9SST+NFFFTD4URT+Beh//9k='
        )
        info = WRITER.ASSETS.inspect_image(data)
        self.assertEqual((info['format'], info['width'], info['height']), ('JPEG', 3, 2))
        self.assertIn('not performed', info['validation'])
        for bad in (data[:-2], data.replace(b'\xff\xc0', b'\xff\xc2', 1),
                    data[:2] + b'\xff\xe1\x00\x04ab' + data[2:]):
            with self.assertRaises(WRITER.GenerationError):
                WRITER.ASSETS.inspect_image(bad)

    def test_check_environment_and_huge_integers_are_bounded(self):
        WRITER.write_environment()
        spec = spec_fixture()
        spec['pages'][0]['width'] = 10 ** 1000
        with self.assertRaises(WRITER.GenerationError):
            self.build(spec)
        with patch.object(WRITER.os, 'supports_dir_fd', set()):
            with self.assertRaises((WRITER.GenerationError, WRITER.READER.ReadError)):
                WRITER.write_environment()

    def test_existing_output_symlink_and_escape_never_overwritten(self):
        data, _ = self.build()
        (self.root / 'new.ofd').write_bytes(b'preserved')
        (self.root / 'sentinel.txt').write_bytes(b'private sentinel')
        (self.root / 'link.ofd').symlink_to(self.root / 'sentinel.txt')
        with self.working():
            for name in ('new.ofd', 'link.ofd', './bad.ofd', 'sub/bad.ofd', '../outside.ofd', '/tmp/outside.ofd', 'C:bad.ofd', 'C:/bad.ofd', 'sub\\bad.ofd'):
                with self.subTest(name=name), self.assertRaises((OSError, WRITER.GenerationError)):
                    WRITER.publish_new(data, name, CHECKER.audit)
        self.assertEqual((self.root / 'new.ofd').read_bytes(), b'preserved')
        self.assertEqual((self.root / 'sentinel.txt').read_bytes(), b'private sentinel')

    def test_bare_output_names_preserve_hard_links_and_raw_spelling(self):
        data, _ = self.build()
        (self.root / 'sentinel.txt').write_bytes(b'preserve input')
        os.link(self.root / 'sentinel.txt', self.root / 'new.ofd')
        with self.working():
            for name in ('./bad.ofd', 'sub/../bad.ofd', 'C:bad.ofd', 'C:\\bad.ofd', 'new.ofd', None, Path('new.ofd')):
                with self.subTest(name=name), self.assertRaises((OSError, WRITER.GenerationError)):
                    WRITER.publish_new(data, name, CHECKER.audit)
        self.assertEqual((self.root / 'sentinel.txt').read_bytes(), b'preserve input')
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['new.ofd', 'sentinel.txt'])

    def test_validation_failure_removes_only_private_candidate(self):
        data, _ = self.build()
        with self.working(), self.assertRaises(ValueError):
            WRITER.publish_new(data, 'new.ofd', lambda _data: (_ for _ in ()).throw(ValueError('deliberate rejection')))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_concurrent_publication_one_winner_no_overwrite(self):
        data, _ = self.build()
        outcomes = []
        def run():
            try:
                WRITER.publish_new(data, 'new.ofd', CHECKER.audit)
                outcomes.append('published')
            except FileExistsError:
                outcomes.append('exists')
        with self.working():
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertCountEqual(outcomes, ['published', 'exists'])
        self.assertEqual((self.root / 'new.ofd').read_bytes(), data)

    def test_postpublication_bytes_are_checked_and_retained_on_failure(self):
        data, _ = self.build()
        original_link = os.link
        def altered(source, destination, **kwargs):
            descriptor = os.open(source, os.O_WRONLY | os.O_TRUNC, dir_fd=kwargs['src_dir_fd'])
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(b'changed after candidate validation')
            return original_link(source, destination, **kwargs)
        with self.working(), patch.object(os, 'link', altered), self.assertRaises(WRITER.GenerationError) as raised:
            WRITER.publish_new(data, 'new.ofd', CHECKER.audit)
        self.assertTrue(raised.exception.publication['output_written'])
        self.assertEqual(raised.exception.publication['publication_outcome'], 'published_verification_failed')
        self.assertTrue(raised.exception.publication['cleanup_complete'])
        self.assertEqual((self.root / 'new.ofd').read_bytes(), b'changed after candidate validation')

    def test_postpublication_cleanup_failure_preserves_cli_outcome(self):
        for name, data in self.assets.items():
            (self.root / name).write_bytes(data)
        (self.root / 'spec.json').write_text(json.dumps(spec_fixture()), encoding='utf-8')
        original_unlink = os.unlink
        def denied(name, *args, **kwargs):
            if str(name).startswith('._ofd_candidate_'):
                self.registered.append(self.root / name)
                raise PermissionError('injected cleanup failure')
            return original_unlink(name, *args, **kwargs)
        output = io.StringIO()
        with (self.working(), patch.object(os, 'unlink', denied),
              patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {denied}), contextlib.redirect_stdout(output)):
            code = WRITER.main(['spec.json', 'new.ofd'])
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        self.assertTrue(report['output_written'])
        self.assertEqual(report['publication_outcome'], 'published_verified')
        self.assertFalse(report['cleanup_complete'])
        self.assertTrue((self.root / 'new.ofd').is_file())

    def test_final_name_substitution_is_not_deleted_or_accepted(self):
        data, _ = self.build()
        (self.root / 'sentinel.txt').write_bytes(b'unknown replacement')
        original_link = os.link
        def substituted(source, destination, **kwargs):
            original_link(source, destination, **kwargs)
            os.unlink(destination, dir_fd=kwargs['dst_dir_fd'])
            original_link('sentinel.txt', destination, dst_dir_fd=kwargs['dst_dir_fd'])
        with self.working(), patch.object(os, 'link', substituted), self.assertRaises(WRITER.GenerationError) as raised:
            WRITER.publish_new(data, 'new.ofd', CHECKER.audit)
        self.assertEqual(raised.exception.publication['publication_verification'], 'failed')
        self.assertEqual((self.root / 'new.ofd').read_bytes(), b'unknown replacement')
        self.assertEqual((self.root / 'sentinel.txt').read_bytes(), b'unknown replacement')

    def test_cli_json_contract_and_readonly_assets(self):
        for name, data in self.assets.items():
            (self.root / name).write_bytes(data)
        (self.root / 'spec.json').write_text(json.dumps(spec_fixture()), encoding='utf-8')
        command = [sys.executable, '-I', '-S', '-B', str(Path(WRITER.__file__)), './spec.json', 'new.ofd']
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, encoding='utf-8', env={**os.environ, 'PATH': ''})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'generated')
        again = subprocess.run(command, cwd=self.root, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(again.returncode, 1)
        self.assertEqual(json.loads(again.stdout)['publication_outcome'], 'not_published_existing_name')
        for name, data in self.assets.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_independent_installation_bare_filename_contract(self):
        with tempfile.TemporaryDirectory(prefix='ofd-independent-') as temporary:
            root = Path(temporary)
            installation = root / 'installation'
            shutil.copytree(Path(WRITER.__file__).parent, installation / 'scripts', ignore=shutil.ignore_patterns('test', '__pycache__'))
            task = root / 'task'
            task.mkdir()
            (task / 'inputs').mkdir()
            for name, data in self.assets.items():
                (task / name).write_bytes(data)
            (task / 'inputs/spec.json').write_text(json.dumps(spec_fixture()), encoding='utf-8')
            before = {str(path.relative_to(installation)): path.read_bytes() for path in installation.rglob('*') if path.is_file()}
            for path in installation.rglob('*'):
                path.chmod(0o555 if path.is_dir() else 0o444)
            installation.chmod(0o555)
            command = [sys.executable, '-I', '-S', '-B', str(installation / 'scripts/generate_ofd.py'), './inputs/spec.json']
            try:
                for name in ('./bad.ofd', 'inputs/bad.ofd', '../bad.ofd', str(task / 'bad.ofd'), 'C:bad.ofd', 'inputs\\bad.ofd'):
                    with self.subTest(name=name):
                        rejected = subprocess.run(command + [name], cwd=task, capture_output=True, text=True, encoding='utf-8')
                        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
                        self.assertEqual(json.loads(rejected.stdout)['publication_outcome'], 'not_attempted')
                success = subprocess.run(command + ['中文报告.ofd'], cwd=task, capture_output=True, text=True, encoding='utf-8',
                                         env={**os.environ, 'PATH': ''})
                self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
                self.assertEqual(json.loads(success.stdout)['output'], '中文报告.ofd')
                self.assertTrue((task / '中文报告.ofd').is_file())
                self.assertFalse((task / 'bad.ofd').exists())
                self.assertFalse(list(task.glob('._ofd_candidate_*')))
                self.assertEqual({str(path.relative_to(installation)): path.read_bytes()
                                  for path in installation.rglob('*') if path.is_file()}, before)
            finally:
                installation.chmod(0o755)
                for path in installation.rglob('*'):
                    path.chmod(0o755 if path.is_dir() else 0o644)

    def test_duplicate_json_and_nonfinite_json(self):
        for text in ('{"schema_version":1,"schema_version":2}', '{"schema_version":NaN}'):
            (self.root / 'spec.json').write_text(text, encoding='utf-8')
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(WRITER.__file__)), 'spec.json', 'new.ofd'],
                                    cwd=self.root, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['stage'], 'input')

    def test_independent_checker_rejects_ids_references_and_dtd(self):
        data, _ = self.build()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        for kind in ('ids', 'reference', 'dtd'):
            files = copy.deepcopy(original)
            if kind == 'ids':
                files['Doc_0/Document.xml'] = files['Doc_0/Document.xml'].replace(b'<ofd:MaxUnitID>', b'<ofd:MaxUnitID>999')
            elif kind == 'reference':
                files['OFD.xml'] = files['OFD.xml'].replace(b'Doc_0/Document.xml', b'../bad.xml')
            else:
                files['OFD.xml'] = files['OFD.xml'].replace(b'<ofd:OFD', b'<!DOCTYPE OFD><ofd:OFD', 1)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                CHECKER.audit(buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
