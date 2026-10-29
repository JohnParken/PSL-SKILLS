"""Explicit XLSX auxiliary-file lifecycle; uses the pinned common runtime."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artifact_runtime.cleanup_task import main as cleanup_main

if __name__ == '__main__':
    raise SystemExit(cleanup_main())
