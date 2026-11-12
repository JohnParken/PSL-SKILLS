"""Offline original-font generation and pinned resource/security regressions."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

SPEC = importlib.util.spec_from_file_location('ofd_generation_helpers', Path(__file__).with_name('test_ofd_generate.py'))
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)
WRITER = HELPERS.WRITER
CHECKER = HELPERS.CHECKER
ROOT = Path(__file__).resolve().parents[2]


class BundledFontTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.gettempdir()) / ('ofd-bundle-test-' + uuid.uuid4().hex)
        self.registered = [self.directory / name for name in ('layout.json', 'result.ofd')]
        self.directory.mkdir(mode=0o700)

    def tearDown(self):
        entries = list(self.directory.iterdir())
        self.assertTrue(all(path in self.registered for path in entries), 'unexpected temporary residue')
        for path in entries:
            path.unlink()
        self.directory.rmdir()

    def example(self):
        return json.loads((ROOT / 'assets/examples/zhuque_layout.json').read_text(encoding='utf-8'))

    def test_original_bytes_version_permissions_and_complete_licenses(self):
        data, license_data, source = WRITER.bundled_font(WRITER.ZHUQUE_REFERENCE)
        self.assertEqual(len(data), 8824084)
        self.assertEqual(hashlib.sha256(data).hexdigest(), '558c62730844fe54ba220146ed62f859d4e2880188d92d985f8921c6e3743bc4')
        font = WRITER.ASSETS.TrueType(data)
        self.assertEqual((font.name, font.fs_type), ('ZhuqueFangsong-Regular', 8))
        self.assertTrue(source['prerelease'])
        self.assertIn(b'Copyright (c) 2023, Zhejiang JadeFoci', license_data)
        self.assertIn(b'Copyright 2011 The Alegreya Project Authors', license_data)
        for name in ('LICENSE.txt', 'LICENSE_Alegreya.txt'):
            original = (ROOT / 'assets/fonts/zhuque_fangsong' / name).read_bytes()
            self.assertIn(original, license_data)

    def test_offline_generation_preserves_original_and_roundtrip_text(self):
        spec = self.example()
        def no_user_asset(_path, _limit):
            raise AssertionError('bundled source must not search task or system font paths')
        data, report = WRITER.Builder(spec, no_user_asset).build()
        audited = WRITER.validate_model(data, spec, report, CHECKER)
        expected = ['中文生成验证：朱雀仿宋 v0.212', '离线示例字体，保留完整原版与许可。',
                    '项目', '说明', '字体', '预览测试版', '许可', 'OFL 1.1',
                    '第二页：按请求逐字检查覆盖。', '缺字时停止，不替换或静默截断。']
        self.assertEqual([text['text'] for page in audited['pages'] for text in page['text']], expected)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(hashlib.sha256(archive.read('Doc_0/Res/Font_1.ttf')).hexdigest(),
                             '558c62730844fe54ba220146ed62f859d4e2880188d92d985f8921c6e3743bc4')
            self.assertIn(b'FontNotice:1', archive.read('OFD.xml'))
        path = self.directory / 'result.ofd'
        path.write_bytes(data)
        readback = WRITER.READER.read_ofd_structure(path)
        self.assertEqual([run['text'] for doc in readback['documents'] for page in doc['pages'] for run in page['text_runs']], expected)
        self.assertEqual(readback['status'], 'partial')

    def test_known_missing_character_explicitly_rejected_before_output(self):
        spec = self.example()
        spec['pages'][0]['items'][0]['text'] = '\u3400'
        with self.assertRaisesRegex(WRITER.GenerationError, 'font coverage missing U\\+3400'):
            WRITER.Builder(spec).build()
        self.assertFalse((self.directory / 'result.ofd').exists())

    def test_unsupported_symbol_is_not_replaced(self):
        spec = self.example()
        spec['pages'][0]['items'][0]['text'] = '\u2611'
        with self.assertRaisesRegex(WRITER.GenerationError, 'unsupported horizontal character U\\+2611'):
            WRITER.Builder(spec).build()

    def test_reference_is_allowlisted_not_a_path_or_url(self):
        for value in ('../../font.ttf', '/tmp/font.ttf', 'https://example.invalid/font.ttf', 'unknown', None):
            with self.subTest(value=value), patch.object(WRITER.READER, 'read_input') as reader:
                with self.assertRaises(WRITER.GenerationError):
                    WRITER.bundled_font(value)
                reader.assert_not_called()

    def test_bundled_and_custom_paths_cannot_be_mixed(self):
        spec = self.example()
        spec['fonts']['example']['path'] = '../../untrusted.ttf'
        with self.assertRaises(WRITER.GenerationError):
            WRITER.Builder(spec)

    def test_tampered_or_missing_assets_fail_without_fallback(self):
        original = WRITER.READER.read_input
        for filename in WRITER.ZHUQUE_FILES:
            def altered(path, *, tampered_name=filename, **options):
                data = original(path, **options)
                return data + b'x' if Path(path).name == tampered_name else data
            with self.subTest(filename=filename), patch.object(WRITER.READER, 'read_input', side_effect=altered):
                with self.assertRaisesRegex(WRITER.GenerationError, 'integrity check failed'):
                    WRITER.bundled_font(WRITER.ZHUQUE_REFERENCE)
        with patch.object(WRITER.READER, 'read_input', side_effect=FileNotFoundError('missing')):
            with self.assertRaisesRegex(WRITER.GenerationError, 'missing/inaccessible'):
                WRITER.bundled_font(WRITER.ZHUQUE_REFERENCE)

    def test_font_over_ten_mib_still_rejected(self):
        bad = HELPERS.font_fixture() + b'\0' * WRITER.ASSETS.MAX_FONT_BYTES
        with self.assertRaisesRegex(WRITER.GenerationError, 'at most 10 MiB'):
            WRITER.ASSETS.TrueType(bad)

    def test_image_limit_not_increased_with_font_limit(self):
        data, _ = HELPERS.WRITER.Builder(HELPERS.spec_fixture(),
                   lambda path, _limit: {'font.ttf': HELPERS.font_fixture(), 'license.txt': b'Synthetic test font license',
                                         'image.png': HELPERS.png_fixture()}[path]).build()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        name = next(name for name in files if name.endswith('.png'))
        files[name] = b'\x89PNG\r\n\x1a\n' + b'\0' * (8 * 1024 * 1024)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(ValueError, 'image member limit'):
            CHECKER.audit(output.getvalue())

    def test_cli_uses_offline_bundle_from_real_skill_root(self):
        (self.directory / 'layout.json').write_text(json.dumps(self.example(), ensure_ascii=False), encoding='utf-8')
        command = [sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/generate_ofd.py'), 'layout.json', 'result.ofd']
        run = subprocess.run(command, cwd=self.directory, capture_output=True, text=True,
                             encoding='utf-8', env={**os.environ, 'PATH': ''}, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['fonts'][0]['source']['reference'], WRITER.ZHUQUE_REFERENCE)
        self.assertTrue(result['fonts'][0]['source']['prerelease'])
        self.assertEqual(result['structural_validation']['status'], 'passed')


if __name__ == '__main__':
    unittest.main()


class PrivateFontTests(unittest.TestCase):
    def test_explicit_private_font_and_security(self):
        with tempfile.TemporaryDirectory(prefix='ofd-private-') as directory:
            root = Path(directory)
            # Register all temporary paths before creation.
            paths = [root / 'assets/user_fonts', root / 'assets/user_fonts/Example.ttf', root / 'assets/user_fonts/Link.ttf', root / 'package.zip']
            paths[0].mkdir(parents=True)
            paths[1].write_bytes(HELPERS.font_fixture())
            with patch.object(WRITER, '__file__', str(root / 'scripts/generate_ofd.py')):
                self.assertEqual(WRITER.private_font('Example.ttf'), HELPERS.font_fixture())
                spec = HELPERS.spec_fixture()
                spec['fonts']['main'] = {'skill_font': 'Example.ttf'}
                data, report = WRITER.Builder(spec, lambda name, limit: HELPERS.png_fixture()).build()
                self.assertEqual(CHECKER.audit(data)['status'], 'passed')
                self.assertIsNone(report['fonts'][0]['license_sha256'])
                for name in ('../Example.ttf', '/Example.ttf', 'sub/Example.ttf', 'x\\Example.ttf', 'file.otf', '中文.ttf'):
                    with self.subTest(name=name), self.assertRaises(WRITER.GenerationError):
                        WRITER.private_font(name)
                paths[2].symlink_to(paths[1])
                with self.assertRaises((WRITER.READER.ReadError, OSError)):
                    WRITER.private_font('Link.ttf')
                for bits in (2, 4):
                    paths[1].write_bytes(HELPERS.font_fixture(bits))
                    with self.assertRaises(WRITER.GenerationError):
                        WRITER.Builder(spec)
            import shutil
            shutil.copytree(ROOT, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
            package_spec = importlib.util.spec_from_file_location('ofd_packaging', ROOT.parents[1] / 'tools/package_ofd.py')
            module = importlib.util.module_from_spec(package_spec)
            package_spec.loader.exec_module(module)
            # ZIP target lies outside the fake Skill root to avoid packaging itself.
            with tempfile.TemporaryDirectory(prefix='ofd-package-') as target:
                destination = Path(target) / 'result.zip'
                module.package(root, destination)
                with zipfile.ZipFile(destination) as archive:
                    self.assertEqual([name for name in archive.namelist() if name.startswith('ofd/assets/user_fonts/')], [])
