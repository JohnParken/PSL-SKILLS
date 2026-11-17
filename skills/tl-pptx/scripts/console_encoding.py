"""UTF-8 console compatibility for the independently attributed tl-pptx derivative."""
from __future__ import annotations
import io
import sys
from typing import TextIO

def _reconfigure_stream(stream: TextIO) -> TextIO:
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
        return stream
    except AttributeError:
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return stream


def configure_utf8_stdio() -> None:
    """Configure UTF-8 streams only; never install recording or identity hooks."""
    sys.stdout = _reconfigure_stream(sys.stdout)
    sys.stderr = _reconfigure_stream(sys.stderr)
