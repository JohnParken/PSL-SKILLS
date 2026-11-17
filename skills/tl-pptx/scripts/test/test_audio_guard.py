"""Audio filenames cannot turn ffprobe into a playlist/network reader."""
import io
from pathlib import Path
import sys
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_audio_guard import probe_options, validate_audio
from tl_guard import ContractError
from svg_to_pptx.pptx_package.narration import probe_audio_duration


def box(kind, data):
    return (len(data) + 8).to_bytes(4, 'big') + kind + data


class AudioGuardTests(unittest.TestCase):
    """Preserve supported local containers while excluding external references."""

    def test_real_pcm_wav(self):
        stream = io.BytesIO()
        with wave.Wave_write(stream) as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 100)
        self.assertEqual(validate_audio(stream.getvalue(), '.wav'), 'wav')

    def test_playlists_cannot_hide_behind_audio_extensions(self):
        for suffix in ('.wav', '.mp3', '.m4a'):
            for payload in (b'#EXTM3U\nhttps://example.com/secret', b'ffconcat version 1.0\nfile /secret'):
                with self.subTest(suffix=suffix), self.assertRaises(ContractError):
                    validate_audio(payload, suffix)

    def test_mp4_external_reference_rejected(self):
        header = box(b'ftyp', b'M4A \0\0\0\0M4A ')
        for flags, valid in ((1, True), (0, False)):
            reference = box(b'url ', flags.to_bytes(4, 'big'))
            dref = box(b'dref', b'\0\0\0\0\0\0\0\1' + reference)
            payload = header + box(b'moov', box(b'trak', box(b'mdia', box(b'minf', box(b'dinf', dref)))))
            if valid:
                self.assertEqual(validate_audio(payload, '.m4a'), 'mov')
            else:
                with self.assertRaisesRegex(ContractError, 'External'):
                    validate_audio(payload, '.m4a')

    def test_probe_has_forced_demuxer_protocols_and_timeout(self):
        with patch('svg_to_pptx.pptx_package.narration.subprocess.run',
                   return_value=SimpleNamespace(stdout='{"format":{"duration":"1.25"}}')) as run:
            self.assertEqual(probe_audio_duration(Path('local.wav')), 1.25)
        args = run.call_args.args[0]
        self.assertIn('-format_whitelist', args)
        self.assertEqual(args[args.index('-f') + 1], 'wav')
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertEqual(probe_options('local.m4a')[-1], 'mov')


if __name__ == '__main__':
    unittest.main()
