"""Static layout audit: usage: python -I -S -B validate_layout.py document.docx [-o report.json]."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from office.archiveio import open_archive
from office.validators.structure import StructureValidator
from office.formatting.audit import audit_roots
from archive_deliverables import validate_input_path, validate_delivery_output


def validate_layout(path: str | Path, *, profile: str | None = None) -> dict:
    try:
        with open_archive(Path(path)) as archive:
            validator = StructureValidator.from_archive(archive, extension='.docx')
            valid = validator.validate(quiet=True)
            result = audit_roots(validator.roots, profile=profile)
            for message in validator.errors:
                result['issues'].append({'level': 'error', 'code': 'OPC_STRUCTURE',
                                         'location': 'package', 'message': message})
            for message in validator.warnings:
                result['issues'].append({'level': 'warning', 'code': 'OPC_WARNING',
                                         'location': 'package', 'message': message})
            result['structure_valid'] = valid and result['structure_valid']
            return result
    except (OSError, ValueError, EOFError) as exc:
        return {'structure_valid': False, 'profile': profile, 'layout_validation': 'static_only',
                'visual_validation': 'not_run', 'issues': [
                    {'level': 'error', 'code': 'UNREADABLE_PACKAGE', 'location': 'package',
                     'message': str(exc)},
                ]}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only OOXML static layout audit (not visual rendering)')
    parser.add_argument('filename', type=Path)
    parser.add_argument('-o', '--output', help='save JSON to a new task-relative file; never overwrite')
    parser.add_argument('--strict-pagination', action='store_true',
                        help='exit 2 if advisory pagination risks are found (without changing the document)')
    args = parser.parse_args(argv)
    try:
        validate_input_path(args.filename)
        if args.output is not None:
            args.output = validate_delivery_output(args.output, protected_inputs=[args.filename])
            if args.output.exists():
                raise ValueError('Report output already exists; choose a new filename')
        report = validate_layout(args.filename)
        if args.output is None:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            # Reuse descriptor-relative, no-follow traversal and exclusive atomic
            # publication. A competing destination is never replaced, even if it
            # appears after the checks above. Owned staging is removed by save().
            from controlled_cleanup import TaskFiles
            with TaskFiles() as task:
                task.save(args.output.as_posix(), report, new=True)
    except (OSError, ValueError, RuntimeError) as exc:
        print('layout-audit-error: ' + str(exc), file=sys.stderr)
        return 1
    if not report['structure_valid']:
        return 1
    if args.strict_pagination:
        from office.formatting.pagination import PAGINATION_CODES
        if any(item['code'] in PAGINATION_CODES for item in report['issues']):
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
