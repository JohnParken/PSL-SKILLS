#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Task runner for WPS document creation, atomic writing, readback validation, and receipts."""

from typing import Dict, Any, Optional
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes, ParseError
from wps_output_contract import require_output_path


MAX_SPEC_BYTES = 2 * 1024 * 1024  # 2 MiB


def check_task_environment() -> int:
    """Validate runtime environment readiness for document generation tasks."""
    try:
        if sys.version_info < (3, 10):
            raise RuntimeError("Python >=3.10 required for run_wps_task")
        import wps_rtf_builder
        import read_wps
        import wps_delivery
        from wps_task_paths import write_capabilities
        write_capabilities()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print("check_kind=environment artifact_validation=not_run", file=sys.stderr)
        print(f"environment-failed: {exc}", file=sys.stderr, flush=True)
        return 2 if sys.version_info < (3, 10) else 1
    print("check_kind=environment artifact_validation=not_run")
    print("environment-ok", flush=True)
    return 0


def validate_spec(spec: Dict[str, Any]):
    """Validate document creation specification schema."""
    if not isinstance(spec, dict):
        raise ValueError("Specification must be a JSON object")
    blocks = spec.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("Specification must contain a 'blocks' array")
    if not blocks:
        raise ValueError("'blocks' array cannot be empty")

    valid_types = {
        "title", "subtitle", "heading", "paragraph",
        "list", "table", "callout", "page_break",
    }
    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError(f"Block at index {idx} must be a JSON object")
        b_type = block.get("type")
        if b_type not in valid_types:
            raise ValueError(f"Block at index {idx} has unsupported type '{b_type}'")
        if b_type == "table":
            rows = block.get("rows")
            if not isinstance(rows, list):
                raise ValueError(f"Table block at index {idx} must contain a 'rows' array")
        elif b_type == "list":
            items = block.get("items")
            if not isinstance(items, list):
                raise ValueError(f"List block at index {idx} must contain an 'items' array")


def create_document(
    spec: Dict[str, Any],
    output_path: Path,
    compact_receipt: bool = False,
    theme_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Build, write atomically, and readback-verify a WPS document."""
    from wps_task_paths import delivery_filename
    output_path = delivery_filename(output_path)
    require_output_path(output_path, 'rtf')
    if theme_override:
        spec["theme"] = theme_override

    validate_spec(spec)
    doc = build_document_from_spec(spec)
    raw_bytes = doc.render().encode("ascii")
    if not raw_bytes.startswith(b"{\\rtf"):
        raise RuntimeError("Generated document does not have a valid RTF stream header")

    from wps_task_paths import output_candidate, write_candidate
    from wps_access import read_input
    with output_candidate(output_path) as (temp_path, publish):
        write_candidate(temp_path, raw_bytes)
        candidate = read_bytes(read_input(temp_path), reading_mode='standard')
        from wps_rtf_read import require_publishable_candidate
        require_publishable_candidate(candidate)
        from wps_content_validation import validate_content, validate_format
        content_validation = validate_content(doc, candidate)
        format_validation = validate_format(doc, candidate)
        publish()

    # Readback validation

    try:
        read_result = candidate
    except ParseError as exc:
        raise RuntimeError(f"Readback validation failed: {exc}") from exc

    if read_result.get("status") not in ("ok", "partial"):
        raise RuntimeError(f"Readback status rejected: {read_result.get('status')}")

    file_size = len(raw_bytes)
    blocks_count = len(spec.get("blocks", []))

    if compact_receipt:
        return {
            "status": "success",
            **publish.receipt,
            "content_validation": content_validation,
            "format_validation": format_validation,
            "target": str(output_path),
            "file_size": file_size,
            "blocks_count": blocks_count,
            "readback_status": read_result.get("status"),
            "layout_verification": "not_run",
            "native_wps": False,
            "output_format": "rtf",
            "binary_doc": False,
            "partial": read_result.get("status") == "partial" or bool(doc.spec_warning_count),
            "spec_warnings": doc.spec_warnings,
            "spec_warning_count": doc.spec_warning_count,
            "issues": read_result.get("issues", []),
            "receipt": {
                "format": "wps_rtf",
                "theme": spec.get("theme", "official"),
            },
        }

    return {
        "status": "success",
        **publish.receipt,
        "content_validation": content_validation,
        "format_validation": format_validation,
        "target": str(output_path),
        "file_size": file_size,
        "blocks_count": blocks_count,
        "readback_status": read_result.get("status"),
            "layout_verification": "not_run",
            "native_wps": False,
            "output_format": "rtf",
            "binary_doc": False,
            "partial": read_result.get("status") == "partial" or bool(doc.spec_warning_count),
            "spec_warnings": doc.spec_warnings,
            "spec_warning_count": doc.spec_warning_count,
            "issues": read_result.get("issues", []),
        "theme": spec.get("theme", "official"),
        "preview_text": read_result.get("text", "")[:300],
    }


from wps_task_paths import cli_boundary, validate_argument


@cli_boundary
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    # check command
    subparsers.add_parser("check", help="check environment readiness")

    # create command
    create_parser = subparsers.add_parser("create", help="create a wps document from specification")
    create_parser.add_argument("output", help="bare filename in task cwd; use .wps by default, .rtf when explicitly requested (both contain RTF)")
    create_parser.add_argument("--spec", help="inline JSON specification or file path")
    create_parser.add_argument("--compact-receipt", action="store_true", help="output concise JSON receipt")
    create_parser.add_argument("--theme", choices=("official", "business", "general"), help="override theme")

    args = parser.parse_args(argv)

    if args.command in (None, "check"):
        return check_task_environment()

    if args.command == "create":
        output_file = Path(args.output)
        if output_file.suffix.lower() not in (".wps", ".rtf"):
            print(
                json.dumps({
                    "status": "failed",
                    "error": "Output filename must have a .wps or .rtf extension",
                }, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2

        validate_argument(args.output, output=True)
        # Read specification from argument or stdin
        if args.spec:
            spec_path = Path(args.spec)
            if spec_path.is_file():
                validate_argument(args.spec)
                from wps_access import read_input
                raw_data = read_input(spec_path).decode("utf-8")
            else:
                raw_data = args.spec
        else:
            if sys.stdin.isatty():
                print(
                    json.dumps({
                        "status": "failed",
                        "error": "JSON specification must be provided via stdin or --spec",
                    }, ensure_ascii=False),
                    file=sys.stderr,
                )
                return 2
            raw_data = sys.stdin.read(MAX_SPEC_BYTES)

        try:
            spec = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            print(
                json.dumps({
                    "status": "failed",
                    "error": f"Invalid JSON specification: {exc}",
                }, ensure_ascii=False),
                file=sys.stderr,
            )
            return 1

        try:
            from wps_delivery import finish_delivery
            result = create_document(
                spec=spec,
                output_path=output_file,
                compact_receipt=args.compact_receipt,
                theme_override=args.theme,
            )
            inputs = [args.spec] if args.spec and Path(args.spec).is_file() else []
            finish_delivery(result, output_file, inputs)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(
                json.dumps({
                    "status": "failed",
                    "error": str(exc),
                    **getattr(exc, "publication", {}),
                }, ensure_ascii=False),
                file=sys.stderr,
            )
            return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
