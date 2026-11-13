"""Create a small, standards-based DOCX package from a JSON block specification."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# Compatibility exports retain the existing Python API, including private helpers.
from docx_create_common import (
    argparse, difflib, hashlib, json,
    math, os, re, shutil,
    stat, struct, sys, tempfile,
    unicodedata, zipfile, zlib, Path,
    quoteattr, Any, ET, _check_delivery_path,
    _prepare_delivery_directory, _paths_alias, _archive_output_path, _copy_main_to_archive,
    xmlio, check_runtime, apply_profile, parse_section,
    SECTION_KEYS, BREAK_TYPES, normalize_merged_grid, PARAGRAPH_FORMAT_FIELDS,
    append_paragraph_properties, OutputLocation, resolve_output_location, read_router_config_from_environment,
    W, R, REL, CT,
    WP, A, PIC, XML,
    _CONTENT_TYPES, _REL_TYPES, _MAX_PAGE_MM, _MAX_MARGIN_MM,
    _MAX_IMAGE_BYTES, _MAX_XML_BYTES, _MAX_PACKAGE_BYTES, _MAX_PACKAGE_PARTS,
    _COLOR_RE, _ALIGNMENTS, _STYLE_FIELDS, _STYLE_DEFAULTS,
    _TABLE_STYLE_DEFAULTS, _TABLE_LAYOUT_DEFAULTS, _TABLE_BORDER_EDGES, _q,
    _object, _number, _single_line_text, _validate_style_fields,
    _merge_table_text_format, _validate_table_text_fields, _validate_table_layout, _validate_borders,
    _merge_table_layout, _resolved_border, _border_attributes, _cell_borders,
    _theme_base_format, _validated_styles, _normalize_run_fonts, _text,
    _content_widths, _twips, _emu,
)

from docx_create_validation import (
    _image_size, ImageResources, _image, _prepare_image,
    _validate_run, _validate_spec,
)

from docx_create_xml import (
    _half_points, _points_to_twips, _characters_to_hundredths, _append_character_properties,
    _add_text_run, _append_paragraph_properties, _paragraph, _styles_xml,
    _numbering_xml, _table, _add_picture, _related_text_xml,
    _CROSSREF_PLACEHOLDER, _append_cross_reference, _section_xml, _document_xml,
    _relationship_xml, _content_types_xml, _package_parts,
)

def _check_generated_budget(parts: dict[str, bytes]) -> None:
    budget = xmlio.XmlBudget()
    for name, data in parts.items():
        if name.endswith((".xml", ".rels")):
            try:
                xmlio.fromstring(data, budget=budget)
            except ValueError as exc:
                raise ValueError(f"generated package {name}: {exc}") from exc


def preflight_document(spec: dict, base_dir: str | Path | None = None) -> dict:
    """Check the full specification and generated XML budgets without writing."""
    validated = _validate_spec(spec, Path(base_dir) if base_dir is not None else Path.cwd())
    parts = _package_parts(validated)
    _check_generated_budget(parts)
    return {"status": "preflight-ok", "blocks": len(validated["blocks"]),
            "parts": len(parts), "uncompressed_bytes": sum(map(len, parts.values())),
            "visual_validation": False, "xsd_validation": False}


def _verify_content(spec: dict, roots: dict) -> None:
    expected = []
    images = tables = 0
    for block in spec["blocks"]:
        kind = block["type"]
        if kind in {"title", "subtitle", "heading"}:
            expected.append(block["text"])
        elif kind == "toc":
            expected.extend([block["title"], "（请在 Word/WPS 中更新目录域）"])
        elif kind == "paragraph":
            expected.extend([block["content"]] if isinstance(block["content"], str)
                            else [run["text"] for run in block["content"] if "text" in run])
        elif kind == "list":
            expected.extend(block["items"])
        elif kind == "table":
            tables += 1
            if block["caption"] is not None:
                expected.append(block["caption"])
            for row in block["rows"]:
                for cell in row:
                    if cell is None or isinstance(cell, dict) and cell.get("v_merge") == "continue":
                        continue
                    expected.append(cell if isinstance(cell, str) else cell["text"])
                    images += int(isinstance(cell, dict) and cell["image"] is not None)
        elif kind == "image":
            images += 1
        elif kind == "reference":
            expected.append(block["prefix"] if block["prefix"] else None)
            expected.append(_CROSSREF_PLACEHOLDER)
            expected.append(block["suffix"] if block["suffix"] else None)
    expected = [t for t in expected if t is not None]
    document = roots["word/document.xml"]
    actual = [node.text or "" for node in document.iter(_q(W, "t"))]
    if actual != expected:
        raise ValueError("Generated body text does not match input content coverage")
    if len(list(document.iter(_q(W, "tbl")))) != tables or len(list(document.iter(_q(W, "drawing")))) != images:
        raise ValueError("Generated table/image counts do not match input content coverage")
    for part, (kind, text, page_field) in spec["related_parts"].items():
        actual_text = "".join(node.text or "" for node in roots[part].iter(_q(W, "t")))
        expected_text = text + (" 1" if page_field else "")
        if actual_text != expected_text:
            raise ValueError(f"Generated {kind} text does not match input content coverage at {part}")


def create_document(spec: dict, output: str | Path, base_dir: str | Path | None = None, *,
                    verify: bool = False, require_no_placeholders: bool = False) -> Path:
    """Create a DOCX at output, resolving relative image paths under base_dir."""
    from archive_delivery import validate_delivery_output
    output_path = validate_delivery_output(output)
    if not isinstance(verify, bool) or not isinstance(require_no_placeholders, bool):
        raise ValueError("verify and require_no_placeholders must be booleans")
    if output_path.suffix.lower() != ".docx":
        raise ValueError("output path must use the .docx extension")
    image_base = Path(base_dir) if base_dir is not None else Path.cwd()
    validated = _validate_spec(spec, image_base)
    return _create_validated_document(validated, output_path, verify=verify,
                                      require_no_placeholders=require_no_placeholders)


def validate_task_resources(spec, base_dir=None):
    """Check public task image paths before validation opens any resource bytes."""
    from artifact_files import _check_delivery_path
    from archive_delivery import validate_input_path
    root = Path.cwd()
    base = Path(base_dir) if base_dir is not None else root
    if base.is_absolute():
        try:
            base = base.relative_to(root)
        except ValueError as exc:
            raise ValueError('resource base must stay within the task directory') from exc
    _check_delivery_path(root, base, directory=True)
    pending, seen = [spec], set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                if 'path' in value:
                    path = Path(_text(value['path'], 'resource.path', allow_empty=False))
                    validate_input_path(base / path)
                pending.extend(value.values())
            else:
                pending.extend(value)


def create_document_delivery(spec: dict, output: str | Path, base_dir=None, *,
                             verify: bool = True, require_no_placeholders: bool = False,
                             protected_inputs=()) -> dict:
    """Publish a final task-root DOCX and automatically attempt shared archival."""
    from archive_delivery import finalize_delivery, validate_output_path

    if not isinstance(verify, bool) or not isinstance(require_no_placeholders, bool):
        raise ValueError("verify and require_no_placeholders must be booleans")
    output_path = _delivery_output_path(Path.cwd(), str(output))
    protected = [Path(path) for path in protected_inputs]
    validate_output_path(output_path, protected_inputs=protected)
    validate_task_resources(spec, base_dir)
    validated = _validate_spec(spec, Path(base_dir) if base_dir is not None else Path.cwd())
    protected.extend(validated["image_paths"])
    verified = verify or require_no_placeholders
    layout_warnings = []
    _create_validated_document(validated, output_path, verify=verified,
                               require_no_placeholders=require_no_placeholders,
                               layout_warnings=layout_warnings)
    status = "verified" if verified else "created"
    summary = finalize_delivery(output_path, protected, status=status)
    summary["warnings"].extend(layout_warnings)
    files = summary.get("files", [])
    summary.update(generation_status=status, output_dir=".",
                   archive_output=files[0]["archive_output"] if files else None)
    if verified:
        summary.update(blocks=len(spec["blocks"]), zip_crc=True, structure=True,
                       content_coverage=True, layout_intent=True,
                       visual_validation=False, xsd_validation=False)
    return summary


_MAX_LAYOUT_WARNINGS = 100


def _layout_warning_messages(report: dict) -> list[str]:
    """Bound receipt diagnostics explicitly, without changing pagination intent."""
    warnings = [item for item in report['issues'] if item['level'] == 'warning']
    messages = [f"layout-warning: {item['code']} at {item['location']}: {item['message']}"
                for item in warnings[:_MAX_LAYOUT_WARNINGS]]
    remaining = len(warnings) - _MAX_LAYOUT_WARNINGS
    if remaining > 0:
        messages.append(f'layout-warning: {remaining} additional layout warnings omitted; '
                        'run validate_layout.py for the complete report')
    return messages


def _create_validated_document(validated: dict, output_path: Path, *, verify: bool,
                               require_no_placeholders: bool, layout_warnings: list[str] | None = None) -> Path:
    """Publish once from validated input, retaining image identities for CLI archival."""
    if any(_paths_alias(output_path, path) for path in validated["image_paths"]):
        raise ValueError("output must not overwrite an image used by the document")
    parts = _package_parts(validated)
    _check_generated_budget(parts)

    parent = output_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".docx-", suffix=".tmp", dir=".")
    try:
        with os.fdopen(descriptor, "w+b") as file:
            with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED) as package:
                for name, content in parts.items():
                    package.writestr(name, content)
            if os.fstat(file.fileno()).st_size > _MAX_PACKAGE_BYTES:
                raise ValueError("generated DOCX exceeds the 128 MiB package limit")
            file.flush()
            os.fsync(file.fileno())
        if output_path.exists():
            mode = stat.S_IMODE(output_path.stat().st_mode)
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(temporary_name, mode)
        if verify or require_no_placeholders:
            from office.archiveio import open_archive
            from office.validators.structure import StructureValidator
            from office.validators.generation import verify_generation

            with open_archive(Path(temporary_name)) as archive:
                validator = StructureValidator.from_archive(
                    archive, extension=".docx", require_no_placeholders=require_no_placeholders,
                )
                if not validator.validate(quiet=True) or validator.warnings:
                    raise ValueError("Generated document failed verification: "
                                     + "; ".join((validator.errors + validator.warnings)[:5]))
                _verify_content(validated, validator.roots)
                verify_generation(validated, validator)
                from office.formatting.audit import audit_roots
                layout_report = audit_roots(validator.roots)
                errors = [item['message'] for item in layout_report['issues'] if item['level'] == 'error']
                if errors:
                    raise ValueError('Generated static layout validation failed: ' + '; '.join(errors[:5]))
                if layout_warnings is not None:
                    layout_warnings.extend(_layout_warning_messages(layout_report))
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return output_path


def _delivery_location() -> OutputLocation:
    location = resolve_output_location(read_router_config_from_environment())
    if location.used_fallback:
        print("archive-location: skipped; reason=" + location.fallback_reason,
              file=sys.stderr)
    return location



def _delivery_output_path(task_dir: Path, file_name: str) -> Path:
    """Validate a filename-only output in the task directory."""
    if (not file_name or file_name in {".", ".."}
            or file_name.endswith((" ", "."))
            or any(character in '/\\<>:"|?*' or unicodedata.category(character).startswith("C")
                   for character in file_name)):
        raise ValueError("output must be a single DOCX filename without a directory or control characters")
    if len(file_name.encode("utf-8")) > 255:
        raise ValueError("output filename exceeds 255 UTF-8 bytes")
    if Path(file_name).suffix.lower() != ".docx":
        raise ValueError("output filename must use the .docx extension")
    device_name = file_name.split(".", 1)[0].upper()
    if (device_name in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", device_name)):
        raise ValueError("output filename must not use a reserved device name")
    output_path = Path(file_name)
    _check_delivery_path(task_dir, output_path)
    return output_path






def _runtime_check(task_dir: Path, location: OutputLocation) -> bool:
    checks_passed = check_runtime()
    payload = b"docx-runtime-check"
    probe_directories = [(Path("."), "output_dir")]
    if not location.used_fallback:
        probe_directories.append((location.output_dir, "archive_dir"))
    writable_by_name = {}
    if checks_passed:
        for output_dir, label in probe_directories:
            writable = False
            try:
                _prepare_delivery_directory(task_dir, output_dir)
                with tempfile.TemporaryFile(mode="w+b", dir=task_dir) as probe:
                    probe.write(payload)
                    probe.seek(0)
                    writable = probe.read() == payload and os.access(task_dir / output_dir, os.W_OK)
            except Exception as exc:
                writable = False
                label_error = "archive-warning" if label == "archive_dir" else "output-check-failed"
                print(f"{label_error}: {exc}", file=sys.stderr)
            writable_by_name[label] = writable
    if "archive_dir" in writable_by_name:
        print("archive_dir_check=access-only; actual archive write is checked during copy")
    for output_dir, label in probe_directories:
        print(f"{label}={output_dir.as_posix()} writable={writable_by_name.get(label, False)}")
    if checks_passed and "archive_dir" in writable_by_name and not writable_by_name["archive_dir"]:
        print("archive-warning: archive directory is unavailable; main output may proceed", file=sys.stderr)
    if not checks_passed or not writable_by_name.get("output_dir", False):
        print("check_kind=environment artifact_validation=not_run", file=sys.stderr, flush=True)
        print("check-failed: runtime environment check failed", file=sys.stderr, flush=True)
        return False
    print("check_kind=environment artifact_validation=not_run")
    print("check-ok", flush=True)
    return True


def _check_cli_runtime() -> bool:
    try:
        location = _delivery_location()
    except Exception as exc:
        print(f"archive-warning: configuration check failed: {exc}", file=sys.stderr)
        location = OutputLocation(Path("."), True, "archive_config_error")
    return _runtime_check(Path.cwd(), location)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field {key!r}; keep exactly one value")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="?", help="JSON document specification")
    parser.add_argument("output", nargs="?", metavar="filename.docx",
                        help="DOCX filename only; save in the task directory and automatically attempt archival")
    parser.add_argument("--check", action="store_true", help="check runtime support and task/archive-directory write access")
    parser.add_argument("--preflight", action="store_true", help="validate input and resource budgets without writing")
    parser.add_argument("--verify", action="store_true", help="check ZIP CRC, structure and input coverage before publishing")
    parser.add_argument("--require-no-placeholders", action="store_true", help="also verify and reject remaining {{...}} markers")
    args = parser.parse_args(argv)
    if args.check:
        if args.input is not None or args.output is not None or args.preflight or args.verify or args.require_no_placeholders:
            parser.error("--check does not accept positional arguments or generation options")
        return 0 if _check_cli_runtime() else 1
    if args.preflight and (args.output is not None or args.verify or args.require_no_placeholders):
        parser.error("--preflight accepts input only, without output or verification options")
    if args.input is None or (args.output is None and not args.preflight):
        parser.error("input is required; output is required unless --check or --preflight is used")
    try:
        task_dir = Path.cwd()
        from archive_delivery import validate_input_path
        validate_input_path(args.input)
        output_path = None
        if not args.preflight:
            print("generation-deprecated: create_docx.py generation CLI is deprecated; "
                  "use run_docx_task.py create for standard new-document tasks. "
                  "Library APIs, --check and --preflight remain supported.", file=sys.stderr)
            output_path = _delivery_output_path(task_dir, args.output)
            input_path = args.input.resolve()
            if _paths_alias(input_path, task_dir / output_path):
                parser.error("output must not refer to the input JSON file")
        spec = json.loads(args.input.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
        validate_task_resources(spec, args.input.parent)
        if args.preflight:
            print(json.dumps(preflight_document(spec, args.input.resolve().parent), ensure_ascii=False))
        else:
            summary = create_document_delivery(
                spec, args.output, args.input.resolve().parent, verify=args.verify,
                require_no_placeholders=args.require_no_placeholders,
                protected_inputs=[args.input])
            if summary["archive_status"] == "skipped":
                print("archive-location: skipped; reason=" + summary["fallback_reason"],
                      file=sys.stderr)
            for warning in summary["warnings"]:
                print(warning if warning.startswith('layout-warning: ') else "archive-warning: " + warning,
                      file=sys.stderr)
            print(json.dumps(summary, ensure_ascii=True))
    except (OSError, UnicodeError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


