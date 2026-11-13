"""Explicit XLSX auxiliary-file cleanup; archival uses archive_delivery.py."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artifact_runtime.cleanup_task import main

if __name__ == '__main__':
    raise SystemExit(main())
