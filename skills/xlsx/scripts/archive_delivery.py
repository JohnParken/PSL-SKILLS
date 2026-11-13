"""Archive only explicitly listed final deliverables using this Skill's runtime."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artifact_runtime.archive_delivery import main

if __name__ == '__main__':
    raise SystemExit(main())
