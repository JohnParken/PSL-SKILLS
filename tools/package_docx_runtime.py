"""Compatibility CLI for the common reviewed runtime packager (repository only)."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_skills import _version, build_package, collect_files

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "skills/docx"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "packaging/runtime-manifests/docx.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output
        if output is None:
            output = Path(__file__).resolve().parents[1] / "dist/runtime" / ("docx-runtime-" + _version(collect_files(args.source)) + ".zip")
            output.parent.mkdir(parents=True, exist_ok=True)
        report = build_package(args.source, output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("Runtime packaging failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
