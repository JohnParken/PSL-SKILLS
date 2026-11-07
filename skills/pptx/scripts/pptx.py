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
    create_parser.add_argument("output", help="New bare .pptx filename in the task root; never overwritten")
    for command in ("inspect", "validate"):
        command_parser = commands.add_parser(command, help=f"{command.title()} a supported PPTX package")
        command_parser.add_argument("input", help="Task-relative .pptx filename")
        if command == "inspect":
            command_parser.add_argument("--summary", action="store_true", help="Return object and paragraph counts instead of text")
            command_parser.add_argument("--slides", help="Ascending 1-based ranges, for example 1-3,5")
            command_parser.add_argument("--notes", choices=("include", "omit"), default="include")
            command_parser.add_argument("--max-output-bytes", type=int, help="Whole JSON UTF-8 budget including newline (512..67108864)")
            command_parser.add_argument("--cursor", help="Opaque continuation; repeat all original options")
            command_parser.add_argument("--allow-png-zero-padding", action="store_true",
                                        help="Inspect with bounded non-standard PNG zero padding; report partial")
    return result


def main(argv: list[str] | None = None) -> int:
    command = None
    args = None
    try:
        args = parser().parse_args(argv)
        command = args.command
        environment()
        if command == "check":
            value = check(write_probe=args.write_probe)
        elif command == "create":
            value = create(args.config, args.output)
        else:
            options = None
            if command == "inspect" and (args.summary or args.slides is not None or args.notes != "include"
                                         or args.max_output_bytes is not None or args.cursor is not None):
                options = {key: getattr(args, key) for key in ("summary", "slides", "notes", "max_output_bytes", "cursor")}
            value = read_presentation(args.input, inspect=command == "inspect",
                                      allow_png_zero_padding=getattr(args, 'allow_png_zero_padding', False),
                                      inspection_options=options)
        print(json.dumps({"schema": RESULT_SCHEMA, "ok": True, "command": command, **value}, ensure_ascii=False, allow_nan=False))
        return 3 if value.get('structural_validation') == 'partial' else 0
    except (ValueError, RuntimeError, OSError, OverflowError, RecursionError, TypeError, KeyError, IndexError) as error:
        if isinstance(error, TaskError):
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
        message = message.replace(str(SCRIPTS_ROOT.parent), "<skill-root>")
        budget = getattr(args, "max_output_bytes", None)
        bounded_error = isinstance(budget, int) and 512 <= budget <= 67108864
        if not bounded_error:
            message = message[:4000]
        failure = {"schema": RESULT_SCHEMA, "ok": False, "command": command, "error": {"code": code, "message": message}}
        if bounded_error:
            while len((json.dumps(failure, ensure_ascii=False) + "\n").encode("utf-8")) > budget:
                failure["error"]["message"] = failure["error"]["message"][:-32]
                failure["error"]["message_truncated"] = True
                failure["error"]["full_diagnostic"] = "stderr"
        print(json.dumps(failure, ensure_ascii=False))
        print(f"pptx: {code}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
