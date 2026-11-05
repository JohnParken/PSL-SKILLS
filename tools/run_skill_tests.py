"""Repository maintenance runner: discover one suite and reject empty discovery.

Run each Skill in a separate process to avoid same-named fixture module collisions.
This tool is not a production Skill dependency or part of an independent Skill ZIP.
"""
import argparse
from pathlib import Path
import sys
import unittest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Test directory relative to the maintenance working directory.')
    parser.add_argument('-p', '--pattern', default='test_*.py')
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        print('Test directory does not exist: ' + str(args.directory), file=sys.stderr)
        return 2
    loader = unittest.TestLoader()
    try:
        suite = loader.discover(str(args.directory), pattern=args.pattern)
    except (ImportError, OSError) as exc:
        print('Test discovery failed: ' + str(exc), file=sys.stderr)
        return 2
    count = suite.countTestCases()
    print(f'Collected {count} tests from {args.directory} ({args.pattern})', flush=True)
    if not count:
        print('No tests collected; this is a failed verification.', file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not loader.errors else 1


if __name__ == '__main__':
    sys.exit(main())
