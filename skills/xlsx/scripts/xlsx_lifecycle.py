"""Explicit XLSX auxiliary-file lifecycle; uses the pinned common runtime."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artifact_runtime.cleanup_task import main as cleanup_main
from _artifact_runtime.archive_deliverables import archive_outputs


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != 'archive':
        return cleanup_main(arguments)
    parser = argparse.ArgumentParser(description='Archive explicit completed XLSX task outputs')
    parser.add_argument('command', choices=['archive'])
    parser.add_argument('outputs', nargs='+', type=Path)
    parser.add_argument('--protect-input', action='append', default=[], type=Path)
    parser.add_argument('--replace-existing', action='store_true',
                        help='Replace archives only for this explicitly authorized output list')
    args = parser.parse_args(arguments)
    result = archive_outputs(args.outputs, args.protect_input, replace_existing=args.replace_existing)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return {'copied': 0, 'skipped': 3, 'failed': 1}.get(result['archive_status'], 1)

if __name__ == '__main__':
    raise SystemExit(main())
