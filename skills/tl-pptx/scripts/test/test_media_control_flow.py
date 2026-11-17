"""Pending gated module mock tests; do not bypass attribution to run this file.

Requires the approved intact distribution gate. Mocks below replace only external
services/programs, never console configuration or integrity checks. Passing tests
would prove control flow, not actual Office, model, ffmpeg or provider behavior.
"""
from __future__ import annotations
import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MediaControlTests(unittest.TestCase):
    """Gate calls remain active at module import; all fixtures are temporary."""

    @classmethod
    def setUpClass(cls):
        from attribution_guard import require_skill_integrity
        try:
            require_skill_integrity()
        except SystemExit as exc:
            raise unittest.SkipTest(f'Original integrity gate blocks media tests (exit {exc.code})') from exc
        cls.video = importlib.import_module('powerpoint_video')
        cls.subtitles = importlib.import_module('video_subtitles')
        cls.config = importlib.import_module('config')

    def test_powerpoint_arguments_without_office(self):
        """Mock the process boundary, asserting encoder options and timeout."""
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'fixture.pptx', Path(directory) / 'fixture.mp4'
            source.write_bytes(b'not-opened-by-office')
            with patch.object(self.video.sys, 'platform', 'win32'), patch.object(self.video.os, 'environ', {}):
                with patch.object(self.video, '_run_powershell', return_value=0) as launch:
                    result = self.video.main([str(source), '-o', str(output), '--fps', '24', '--resolution', '720', '--timeout', '120'])
            self.assertEqual(result, 0)
            self.assertEqual(launch.call_args.kwargs['env']['PPT_MASTER_VIDEO_FPS'], '24')
            self.assertEqual(launch.call_args.kwargs['env']['PPT_MASTER_VIDEO_RESOLUTION'], '720')
            self.assertEqual(launch.call_args.kwargs['timeout'], 180)
            self.assertFalse(output.exists())

    def test_subtitle_alignment_without_model(self):
        """Mock alignment/model loading, preserving transcript and atomic publication."""
        srt = '1\n00:00:00,000 --> 00:00:01,000\nHello world.\n\n'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            video = base / 'fixture.mp4'
            video.write_bytes(b'not-decoded')
            subtitles = base / 'subtitles'
            subtitles.mkdir()
            (subtitles / 'slide-01.srt').write_text(srt, encoding='utf-8')
            output = base / 'aligned.srt'
            result = SimpleNamespace(to_srt_vtt=lambda name, **_: Path(name).write_text(srt, encoding='utf-8'))
            model = Mock()
            model.align.return_value = result
            loader = SimpleNamespace(load_model=Mock(return_value=model))
            with patch.object(self.subtitles.shutil, 'which', return_value='/fixture/not-executed'):
                with patch.object(self.subtitles, '_require_stable_whisper', return_value=loader):
                    count, end = self.subtitles.align_video_subtitles(video_path=video, subtitle_dir=subtitles,
                                                                    output_path=output, language='en', model_name='fixture-model',
                                                                    device='cpu', max_chars=42, force=False)
            self.assertEqual((count, end), (1, 1000))
            loader.load_model.assert_called_once_with('fixture-model', device='cpu')
            self.assertEqual(model.align.call_args.args, (str(video), 'Hello world.'))
            self.assertEqual(output.read_text(encoding='utf-8'), srt)

    def test_explicit_provider_env_only(self):
        """No default dotenv discovery; an explicit fixture loads selected prefixes only."""
        self.assertEqual(self.config.get_env_candidates(), [])
        with patch.object(Path, 'open', side_effect=AssertionError('unexpected env read')):
            self.assertIsNone(self.config.load_prefixed_env_file(('FIXTURE_',)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            path.write_text('FIXTURE_VALUE="safe#value" # note\nOTHER_VALUE=not-loaded\n', encoding='utf-8')
            environment = {}
            with patch.object(self.config.os, 'environ', environment):
                self.config.load_prefixed_env_file(('FIXTURE_',), env_path=path)
            self.assertEqual(environment, {'FIXTURE_VALUE': 'safe#value'})


if __name__ == '__main__':
    unittest.main()
