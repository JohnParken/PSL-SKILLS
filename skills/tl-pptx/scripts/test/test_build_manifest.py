"""Safe manifest integration through the complete preserved native builder."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from tl_build import build_package, load_manifest, prepare_build
from tl_guard import ContractError

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
       '<rect id="box" x="10" y="20" width="100" height="80" fill="#ff0000"/></svg>')


class BuildManifestTests(unittest.TestCase):
    """Inputs stay unchanged, outputs remain private until explicit publication."""

    def setUp(self):
        self.original = Path.cwd()
        self.workspace = tempfile.TemporaryDirectory()
        os.chdir(self.workspace.name)
        Path('page.svg').write_text(SVG, encoding='utf-8')

    def tearDown(self):
        os.chdir(self.original)
        self.workspace.cleanup()

    def test_full_builder_notes_and_transition(self):
        manifest = {'slides': [{'svg': 'page.svg', 'notes': 'Speaker notes'}],
                    'options': {'transition': 'fade', 'transition_duration': 0.7}}
        payload, report = build_package(prepare_build(manifest))
        presentation = Presentation(io.BytesIO(payload))
        self.assertIn('Speaker notes', presentation.slides[0].notes_slide.notes_text_frame.text)
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            self.assertIn(b'fade', package.read('ppt/slides/slide1.xml'))
        self.assertEqual(report['builder'], 'full-upstream')
        self.assertEqual(Path('page.svg').read_text(), SVG)
        self.assertEqual(sorted(path.name for path in Path('.').iterdir()), ['page.svg'])

    def test_removed_media_inputs_fail_before_reading_sources(self):
        removed = (
            {'slides': [{'svg': 'missing.svg', 'audio': 'voice.wav'}]},
            {'slides': [{'svg': 'missing.svg'}], 'transition_sound': 'sound.wav'},
            {'slides': [{'svg': 'missing.svg'}], 'options': {'use_narration_timings': False}},
            {'slides': [{'svg': 'missing.svg'}], 'options': {'narration_padding': 0}},
            {'slides': [{'svg': 'missing.svg'}], 'options': {'narration_start_floor': 0}},
            {'slides': [{'svg': 'missing.svg'}], 'options': {'animation_config': {'slides': [
                {'transition': {'sound': 'sound.wav'}}]}}},
            {'slides': [{'svg': 'missing.svg'}], 'options': {'animation_cli_overrides': {'sound': 'sound.wav'}}},
        )
        for manifest in removed:
            with self.subTest(manifest=manifest):
                with patch('tl_build.read_local') as reader:
                    with self.assertRaisesRegex(ContractError, 'removed'):
                        prepare_build(manifest)
                    reader.assert_not_called()
        self.assertEqual(list(Path('.').iterdir()), [Path('page.svg')])

    def test_narration_processing_cannot_call_external_probe(self):
        from svg_to_pptx.pptx_package.narration import probe_audio_duration
        from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
        with patch('subprocess.run') as external:
            with self.assertRaisesRegex(ValueError, 'removed'):
                probe_audio_duration(Path('voice.wav'))
            with self.assertRaisesRegex(ValueError, 'removed'):
                create_pptx_with_native_svg([], Path('output.pptx'), resource_root=Path('.'), narration_audio={'page': Path('voice.wav')})
            external.assert_not_called()

    def test_unsafe_paths_and_private_options_rejected(self):
        for manifest in ({'slides': [{'svg': '../page.svg'}]},
                         {'slides': [{'svg': 'page.svg'}], 'options': {'cache_dir': '/tmp'}},
                         {'slides': [{'svg': 'page.svg'}], 'options': {'dangerous_nonconforming_export': True}},
                         {'slides': [{'svg': 'page.svg'}], 'transition_sound': '/tmp/audio.wav'},
                         {'slides': [{'svg': 'page.svg'}], 'options': {'animation_config': {'defaults': {'animation': {'sound': '../audio.wav'}}}}}):
            with self.subTest(manifest=manifest), self.assertRaises(ContractError):
                prepare_build(manifest)

    def test_explicit_roster_image_and_icon_are_embedded(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (2, 2), '#123456').save(stream, format='PNG')
        Path('photo.png').write_bytes(stream.getvalue())
        icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0 L24 0 L12 24 Z"/></svg>'
        Path('icon.svg').write_text(icon)
        page = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
                '<image href="images/photo.png" x="10" y="10" width="100" height="100"/>'
                '<use data-icon="custom/star" x="150" y="10" width="24" height="24" fill="#123456"/></svg>')
        Path('page.svg').write_text(page)
        payload, _report = build_package(prepare_build({
            'slides': [{'svg': 'page.svg'}],
            'resources': {'images/photo.png': 'photo.png', 'icons/custom/star.svg': 'icon.svg'},
        }))
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            self.assertTrue(any(name.startswith('ppt/media/') for name in package.namelist()))
            slide = package.read('ppt/slides/slide1.xml')
            self.assertIn(b'custGeom', slide)
        self.assertEqual(Path('page.svg').read_text(), page)
        self.assertEqual(Path('icon.svg').read_text(), icon)

    def test_explicit_theme_lock_preserves_theme_fonts_and_colors(self):
        lock = ('## typography\n- font_family: Arial\n- title: 40\n- body: 24\n'
                '## colors\n- bg: #FFFFFF\n- primary: #123456\n- accent: #789ABC\n- text: #222222\n')
        Path('theme.md').write_text(lock, encoding='utf-8')
        payload, _report = build_package(prepare_build({'slides': [{'svg': 'page.svg'}], 'spec_lock': 'theme.md'}))
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            theme = package.read('ppt/theme/theme1.xml')
            self.assertIn(b'Arial', theme)
            self.assertIn(b'123456', theme)
            self.assertIn(b'789ABC', theme)
        self.assertEqual(Path('theme.md').read_text(), lock)

    def test_builder_and_postflight_failure_clean_owned_workspace(self):
        prepared = prepare_build({'slides': [{'svg': 'page.svg'}]})
        for target in ('svg_to_pptx.pptx_package.builder.create_pptx_with_native_svg',
                       'tl_build.inspect_package'):
            with self.subTest(target=target), patch(target, side_effect=RuntimeError('fixture failure')):
                with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                    build_package(prepared)
                self.assertEqual(sorted(path.name for path in Path('.').iterdir()), ['page.svg'])
                self.assertEqual(Path('page.svg').read_text(), SVG)

    def test_option_types_and_resource_budgets(self):
        for key, value in (('kiosk', 'false'), ('image_max_dimension', 10 ** 400),
                           ('image_scale', float('inf')), ('image_quality', True),
                           ('animation_duration', float('nan'))):
            with self.subTest(key=key), self.assertRaises(ContractError):
                prepare_build({'slides': [{'svg': 'page.svg'}], 'options': {key: value}})

    def test_json_duplicate_nonfinite_and_regular_manifest(self):
        for content in ('{"slides": [], "slides": []}', '{"value": NaN}', '{"value": Infinity}', '{"value": 1e309}'):
            Path('manifest.json').write_text(content)
            with self.assertRaises(ContractError):
                load_manifest('manifest.json')
        Path('manifest.json').write_text(json.dumps({'slides': [{'svg': 'page.svg'}]}))
        self.assertEqual(load_manifest('manifest.json')['slides'][0]['svg'], 'page.svg')


if __name__ == '__main__':
    unittest.main()
