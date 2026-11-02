"""Bounded DOCX/OPC stability audit using only the Python standard library.

This is a package-level structural audit, not full ISO XSD validation and not
Word/WPS rendering. Warnings about known consumer quirks are not XML errors.

Usage: python -I -S -B scripts/audit_package.py report.docx --json
"""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from office.archiveio import open_archive
from office.validators.structure import StructureValidator


def audit_package(path, *, require_no_placeholders=False):
    """Return a serializable report. No extraction or mutation is performed."""
    path = Path(path)
    report = {
        'structure_valid': False,
        'errors': [],
        'warnings': [],
        'xsd_validated': False,
        'visual_validated': False,
    }
    try:
        if path.is_symlink():
            raise ValueError('Symbolic-link inputs are unsupported')
        if path.is_file():
            if path.suffix.lower() not in ('.docx', '.dotx'):
                raise ValueError('Expected a DOCX/DOTX file')
            with open_archive(path) as archive:
                validator = StructureValidator.from_archive(
                    archive, extension=path.suffix.lower(),
                    require_no_placeholders=require_no_placeholders,
                )
                report['structure_valid'] = validator.validate(quiet=True)
                report['errors'].extend(validator.errors)
                report['warnings'].extend(validator.warnings)
        elif path.is_dir():
            validator = StructureValidator(
                path, require_no_placeholders=require_no_placeholders,
            )
            report['structure_valid'] = validator.validate(quiet=True)
            report['errors'].extend(validator.errors)
            report['warnings'].extend(validator.warnings)
        else:
            raise ValueError('Input does not exist or is not a regular file/directory')
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--json', action='store_true', help='Machine-readable result')
    parser.add_argument('--require-no-placeholders', action='store_true')
    args = parser.parse_args(argv)
    report = audit_package(args.source, require_no_placeholders=args.require_no_placeholders)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('PASSED' if report['structure_valid'] else 'FAILED')
        for issue in report['errors']:
            print('ERROR - ' + issue)
        for issue in report['warnings']:
            print('WARNING - ' + issue)
        print('Scope: bounded OPC/OOXML structure; no XSD, Word/WPS or visual rendering')
    return 0 if report['structure_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
