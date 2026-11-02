"""Check DOCX/DOTX packages with explicit structural rules, without XSD or rendering."""

import argparse
import sys
from contextlib import nullcontext
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .memory_package import package_workspace
    from .archiveio import open_archive
    from .helpers import OOXML_FAMILY
    from .validators import StructureValidator, RedliningValidator
except ImportError:
    from office.memory_package import package_workspace
    from office.archiveio import open_archive
    from office.helpers import OOXML_FAMILY
    from office.validators import StructureValidator, RedliningValidator


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="DOCX/DOTX file or unpacked directory")
    parser.add_argument("--original", type=Path,
                        help="Original DOCX/DOTX; compare body text after structure checks")
    parser.add_argument("--author", help=(
        "Deprecated compatibility option; requires --original. Its value does not filter authors."
    ))
    parser.add_argument("--require-no-placeholders", action="store_true",
                        help="Reject remaining {{...}} placeholders")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.author is not None and not args.original:
        parser.error("--author requires --original")
    if args.author is not None:
        print("Deprecated --author accepted for compatibility; its value does not filter authors.")
    if not args.path.exists():
        parser.error(f"{args.path} does not exist")
    if args.path.is_file() and args.path.suffix.lower() not in OOXML_FAMILY:
        parser.error("Packed input must be a .docx or .dotx file")
    if args.original and (
        not args.original.is_file() or args.original.suffix.lower() not in OOXML_FAMILY
    ):
        parser.error("--original must be an existing .docx or .dotx file")
    try:
        if args.path.is_file() and not args.original:
            with open_archive(args.path) as archive:
                validator = StructureValidator.from_archive(
                    archive, extension=args.path.suffix.lower(),
                    require_no_placeholders=args.require_no_placeholders,
                )
                if not validator.validate():
                    return 1
            print("Basic structure checks PASSED. XSD and visual validation were not performed.")
            return 0
        with (package_workspace(args.path) if args.path.is_file() else nullcontext(args.path)) as unpacked:
            extension = args.path.suffix.lower() if args.path.is_file() else None
            validator = StructureValidator(
                unpacked, extension=extension,
                require_no_placeholders=args.require_no_placeholders,
            )
            success = validator.validate()
            if success and args.original:
                success = RedliningValidator(
                    unpacked, args.original, verbose=args.verbose
                ).validate()
            if not success:
                return 1
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"FAILED - {exc}", file=sys.stderr)
        return 1
    print("Basic structure checks PASSED. XSD and visual validation were not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
