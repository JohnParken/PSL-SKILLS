"""Offline backend control-flow tests, not service/model/Office acceptance."""
from __future__ import annotations
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tts_backends import backend_common, backend_edge, backend_openai, backend_qwen


class BackendTests(unittest.TestCase):
    """Synthetic inputs only: no credentials, network, models or Office."""

    def test_qwen_request_parameters_are_forwarded(self):
        """Mock both HTTP and download, asserting provider parameter composition."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'speech.wav'
            with patch.object(backend_qwen, 'post_json', return_value={'output': {'audio': {'url': 'https://example.invalid/fixture'}}}) as post:
                with patch.object(backend_qwen, 'download_audio') as download:
                    backend_qwen.generate('Fixture words', target, api_key='synthetic-not-a-key', voice_id='Fixture',
                                          model='fixture-model', language_type='English', instructions='Calm',
                                          optimize_instructions=False, base_url='https://example.invalid/tts')
            self.assertEqual(post.call_args.kwargs['payload']['input']['text'], 'Fixture words')
            self.assertEqual(post.call_args.kwargs['payload']['model'], 'fixture-model')
            self.assertEqual(post.call_args.kwargs['timeout'], 180)
            download.assert_called_once_with('https://example.invalid/fixture', target)
            self.assertFalse(target.exists())

    def test_missing_provider_audio_url_is_failure(self):
        """A syntactically successful response must still contain actual audio metadata."""
        with patch.object(backend_qwen, 'post_json', return_value={'output': {}}):
            with patch.object(backend_qwen, 'download_audio') as download:
                with self.assertRaisesRegex(RuntimeError, 'missing audio URL'):
                    backend_qwen.generate('Fixture', Path('unused.wav'), api_key='synthetic-not-a-key', voice_id='Fixture',
                                          model='fixture', language_type='', instructions=None, optimize_instructions=None,
                                          base_url='https://example.invalid/tts')
                download.assert_not_called()

    def test_publish_pair_and_empty_audio_rejection(self):
        """Local staged publication leaves matching audio/subtitle and no owned temp files."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            audio, subtitle = base / 'speech.wav', base / 'speech.srt'
            backend_common.publish_audio_subtitle(b'fixture-audio', audio, 'fixture subtitle', subtitle)
            self.assertEqual(audio.read_bytes(), b'fixture-audio')
            self.assertEqual(subtitle.read_text(encoding='utf-8'), 'fixture subtitle')
            self.assertEqual(sorted(p.name for p in base.iterdir()), ['speech.srt', 'speech.wav'])
            with self.assertRaisesRegex(RuntimeError, 'empty audio'):
                backend_common.publish_audio_subtitle(b'', audio, 'new', subtitle)
            self.assertEqual(audio.read_bytes(), b'fixture-audio')

    def test_word_boundaries_preserve_frozen_transcript(self):
        """Synthetic provider timings produce deterministic, text-faithful SRT."""
        result = backend_edge.format_word_timed_srt('Hello world.', [
            {'text': 'Hello', 'offset': 0, 'duration': 5_000_000},
            {'text': 'world', 'offset': 5_000_000, 'duration': 5_000_000},
        ])
        self.assertIn('Hello world.', result)
        self.assertIn('00:00:00,000 --> 00:00:01,000', result)

    def test_openai_pcm_finalization_and_transcript_guard(self):
        """Fabricated completed stream becomes correct WAV; mismatch remains an error."""
        part = backend_openai._AudioPart(pcm=bytearray(b'\x00\x00\x01\x00'), audio_done=True, transcript='Hello')
        response = {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'status': 'completed', 'id': 'fixture-id',
             'content': [{'type': 'output_audio', 'transcript': 'Hello'}]},
        ]}
        payload = backend_openai._finish_response(response, {(0, 'fixture-id', 0): part}, 'Hello')
        with wave.open(io.BytesIO(payload), 'rb') as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 24000, 2))
            self.assertEqual(audio.readframes(2), bytes(part.pcm))
        with self.assertRaises(RuntimeError):
            backend_openai._finish_response(response, {(0, 'fixture-id', 0): part}, 'Different text')


if __name__ == '__main__':
    unittest.main()
