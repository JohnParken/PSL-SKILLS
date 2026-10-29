"""Exact diagnostic transport and unchanged data channels."""
import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_docx_task import _print_worker_diagnostics


class OutputChannelTests(unittest.TestCase):
    def test_text_diagnostics_preserve_bytes_and_newlines(self):
        for raw in (b'', b'error', b'error\n', b'a\r\nb\n', '错误\n'.encode()):
            buffer=io.BytesIO()
            stream=io.TextIOWrapper(buffer,encoding='utf-8',newline='')
            with redirect_stderr(stream), redirect_stdout(io.StringIO()) as output:
                _print_worker_diagnostics(raw)
                stream.flush()
                self.assertEqual(buffer.getvalue(),raw)
                self.assertEqual(output.getvalue(),'')
            stream.detach()

    def test_nontext_transport_keeps_original_bytes(self):
        buffer=io.BytesIO(); stream=io.TextIOWrapper(buffer,encoding='utf-8',newline='')
        with redirect_stderr(stream): _print_worker_diagnostics(b'error:\xff\n')
        stream.flush(); self.assertEqual(buffer.getvalue(),b'error:\xff\n'); stream.detach()

    def test_string_capture_preserves_prior_replacement_semantics(self):
        with redirect_stderr(io.StringIO()) as error:
            _print_worker_diagnostics(b'bad:\xff')
        self.assertEqual(error.getvalue(),'bad:\ufffd')
