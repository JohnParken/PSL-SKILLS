#!/usr/bin/env python3
"""Integrity gate for tl-pptx, an unofficial derivative of PPT Master.
Upstream Copyright (c) 2025-2026 Hugo He, MIT; full notice and source
mapping are distributed in references. This does not certify upstream identity.
"""
import sys
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))


def require_skill_integrity():
    """Stop the active command unless all independent release checks pass."""
    from tl_release_integrity import verify
    try:
        if verify(Path(__file__).resolve().parents[1]):
            return
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        pass
    print("tl-pptx integrity gate failed; restore the verified independent distribution.", file=sys.stderr)
    raise SystemExit(78)

if __name__ == "__main__":
    require_skill_integrity()
