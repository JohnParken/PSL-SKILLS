from __future__ import annotations
"""Compatibility CLI for the format-independent controlled_cleanup lifecycle."""
import argparse
import json
import os
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from .controlled_cleanup import (
    SCHEMA,
    MAX_ENTRIES,
    MAX_MANIFEST_BYTES,
    MAX_FILE_BYTES,
    OUTCOMES,
    ARCHIVE_STATUSES,
    _parts,
    _overlap,
    _identity,
    TaskFiles,
    _unique_object,
    _valid_snapshot,
    _validate,
    plan_cleanup,
    seal_cleanup,
    _check_outputs,
    cleanup_task)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("manifest")
    for name in ("file", "directory", "protect", "deliverable"):
        plan.add_argument("--" + name, action="append", default=[])
    seal = commands.add_parser("seal")
    seal.add_argument("manifest")
    seal.add_argument("--outcome", choices=OUTCOMES, required=True)
    seal.add_argument("--verified", action="store_true")
    seal.add_argument("--archive-status", choices=ARCHIVE_STATUSES)
    seal.add_argument("--retain", nargs=2, action="append", default=[], metavar=("PATH", "REASON"))
    clean = commands.add_parser("clean")
    clean.add_argument("manifest")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan_cleanup(args.manifest, files=args.file, directories=args.directory,
                                  protected=args.protect, deliverables=args.deliverable)
        elif args.command == "seal":
            result = seal_cleanup(args.manifest, outcome=args.outcome, verified=args.verified,
                                  archive_status=args.archive_status, retain=dict(args.retain))
        else:
            result = cleanup_task(args.manifest)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] in ("failed", "blocked") else 3 if result["status"] == "retained" else 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
