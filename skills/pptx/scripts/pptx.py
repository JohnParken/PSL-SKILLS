#!/usr/bin/env python3
"""PSL PPTX: standard-library creation, inspection and structural validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# -I deliberately removes the script directory from sys.path. Add only this
# installed skill's two code roots; never add the task directory or PYTHONPATH.
SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS_ROOT), str(SCRIPTS_ROOT / "_engine")]

from pptx_core.runtime import RESULT_SCHEMA, TaskError, check, create, environment, read_presentation


class CommandParser(argparse.ArgumentParser):
    """Keep argument failures inside the JSON result contract."""

    def error(self, message: str) -> None:
        raise TaskError(message, "invalid_arguments")


def parser() -> argparse.ArgumentParser:
    result = CommandParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True, parser_class=CommandParser)
    check_parser = commands.add_parser("check", help="Check isolated runtime and in-memory export")
    check_parser.add_argument("--write-probe", action="store_true", help="Create, read and remove a unique task-directory probe")
    create_parser = commands.add_parser("create", help="Create a new editable PPTX from a JSON deck")
    create_parser.add_argument("config", help="Task-relative JSON filename, or - for stdin")
    create_parser.add_argument("output", help="New task-relative .pptx filename; never overwritten")
    for command in ("inspect", "validate"):
        command_parser = commands.add_parser(command, help=f"{command.title()} a supported PPTX package")
        command_parser.add_argument("input", help="Task-relative .pptx filename")
        if command == "inspect":
            command_parser.add_argument("--details", action="store_true", help="Read bounded native chart, table and OMML content")
        else:
            command_parser.add_argument("--content", action="store_true", help="Also verify chart caches/workbooks and native structures")
    return result


def main(argv: list[str] | None = None) -> int:
    command = None
    try:
        args = parser().parse_args(argv)
        command = args.command
        environment()
        if command == "check":
            value = check(write_probe=args.write_probe)
        elif command == "create":
            value = create(args.config, args.output)
        else:
            value = read_presentation(args.input, inspect=command == "inspect", details=getattr(args, "details", False), content=getattr(args, "content", False))
        print(json.dumps({"schema": RESULT_SCHEMA, "ok": True, "command": command, **value}, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, RuntimeError, OSError, OverflowError, RecursionError, TypeError, KeyError, IndexError) as error:
        if isinstance(getattr(error, "code", None), str):
            code = error.code
            message = str(error)
        elif isinstance(error, FileExistsError):
            code = "output_exists"
            message = "Output already exists; choose a new filename"
        elif isinstance(error, OSError):
            code = "io_error"
            message = "Task file operation failed; verify access and available storage"
        else:
            code = "conversion_or_validation_failed"
            message = str(error)
        # Avoid exposing machine-specific installation paths from lower layers.
        message = message.replace(str(SCRIPTS_ROOT.parent), "<skill-root>")[:4000]
        detail_keys = {"stage", "slide_number", "element_id", "element_tag", "object_kind", "object_number", "object_id", "object_name", "field_path", "part", "row", "column"}
        details = {}
        for key, value in getattr(error, "details", {}).items():
            if key in detail_keys and isinstance(value, (str, int)) and not isinstance(value, bool):
                details[key] = value.replace(str(SCRIPTS_ROOT.parent), "<skill-root>")[:1024] if isinstance(value, str) else value
        publication = getattr(error, "details", {}).get("publication")
        if isinstance(publication, dict) and len(json.dumps(publication, ensure_ascii=False)) <= 8192:
            details["publication"] = publication
        failure = {"code": code, "message": message, **details}
        print(json.dumps({"schema": RESULT_SCHEMA, "ok": False, "command": command, "error": failure}, ensure_ascii=False))
        print(f"pptx: {code}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
