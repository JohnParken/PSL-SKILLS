"""Validate or publish a format-only DOCX/DOTX candidate without changing inputs."""

import argparse
import copy
import json
from pathlib import Path
import sys
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_deliverables import (
    finalize_delivery, validate_delivery_output, validate_input_path,
)
from office.helpers import reject_signed_package
from office.memory_package import MemoryPath, read_package
from office.package import pack_document
from office.validators.structure import StructureValidator, W
from office.xmlio import parse_dom, read_xml

XMLNS_NS = "http://www.w3.org/2000/xmlns/"
MAX_SPEC_BYTES = 2 * 1024 * 1024
FORMAT_TARGETS = {
    "font_east_asia": {"rFonts": ("eastAsia", "eastAsiaTheme")},
    "font_latin": {"rFonts": ("ascii", "hAnsi", "asciiTheme", "hAnsiTheme")},
    "size_pt": {"sz": ("val",), "szCs": ("val",)},
    "bold": {"b": ("val",), "bCs": ("val",)},
    "italic": {"i": ("val",), "iCs": ("val",)},
    "color": {"color": ("val", "themeColor", "themeTint", "themeShade")},
}


def _safe_input(path, label):
    path = Path(path)
    if path.suffix.lower() not in {".docx", ".dotx"}:
        raise ValueError(f"{label} must be a .docx or .dotx file")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file, not a symbolic link")
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        if part in {"", ".", ".."}:
            if part == "..":
                raise ValueError(f"{label} path must not contain parent traversal")
            continue
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} path must not contain symbolic links")
    return path


def _validate_spec_size(specification):
    try:
        raw = json.dumps(specification, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Specification must contain JSON-compatible values") from exc
    if len(raw) > MAX_SPEC_BYTES:
        raise ValueError("Format specification exceeds 2 MiB")


def _validate_package(root, extension, label):
    if not isinstance(root, MemoryPath) or not root.is_dir():
        raise ValueError(f"{label} must be a read-only in-memory package snapshot")
    validator = StructureValidator(root, extension=extension)
    if not validator.validate(quiet=True):
        details = "; ".join(validator.errors[:5]) or "structure checks failed"
        raise ValueError(f"{label} failed implemented structure checks: {details}")
    reject_signed_package(
        validator.files,
        (kind for relationships in validator.relations.values()
         for kind, _ in relationships.values()),
    )
    return validator


def _direct_body(dom):
    children = [node for node in dom.documentElement.childNodes
                if node.nodeType == node.ELEMENT_NODE
                and node.namespaceURI == W and node.localName == "body"]
    if len(children) != 1:
        raise ValueError("Document must contain one direct body element")
    return children[0]


def _target_pairs(original_dom, candidate_dom, specification):
    from edit_structure import (
        _blocked_anchors,
        _elements,
        _text,
        _format_spec,
        _index,
        _plain_paragraph,
        _prepare_run,
        _single_line,
    )

    if not isinstance(specification, dict) or set(specification) != {"operations"}:
        raise ValueError("Expected an object containing only operations")
    operations = specification["operations"]
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a nonempty format_run list")
    original_blocked = _blocked_anchors(original_dom)
    candidate_blocked = _blocked_anchors(candidate_dom)
    original_paragraphs = _elements(_direct_body(original_dom), "p")
    candidate_paragraphs = _elements(_direct_body(candidate_dom), "p")
    targets, seen = [], set()

    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("Each operation must be an object")
        common = {"type", "paragraph_index", "expected_text"}
        allowed = common | {"run_index", "expected_run_text", "format"}
        if operation.get("type") != "format_run" or set(operation) - allowed:
            raise ValueError("Only supported format_run operations are allowed")
        paragraph_index = _index(
            operation.get("paragraph_index"), len(original_paragraphs), "paragraph_index"
        )
        if paragraph_index >= len(candidate_paragraphs):
            raise ValueError("Candidate anchor paragraph is missing")
        source_paragraph = original_paragraphs[paragraph_index]
        candidate_paragraph = candidate_paragraphs[paragraph_index]
        source_runs, source_text = _plain_paragraph(source_paragraph, original_blocked)
        candidate_runs, candidate_text = _plain_paragraph(candidate_paragraph, candidate_blocked)
        expected_text = _single_line(operation.get("expected_text"), "expected_text")
        if source_text != expected_text or candidate_text != expected_text:
            raise ValueError(f"Anchor text mismatch at body paragraph {paragraph_index}")
        run_index = _index(operation.get("run_index"), len(source_runs), "run_index")
        if run_index >= len(candidate_runs):
            raise ValueError("Candidate anchor run is missing")
        source_run, candidate_run = source_runs[run_index], candidate_runs[run_index]
        expected_run_text = _single_line(
            operation.get("expected_run_text"), "expected_run_text"
        )
        source_run_text = "".join(
            _text(node) for node in _elements(source_run, "t")
        )
        candidate_run_text = "".join(
            _text(node) for node in _elements(candidate_run, "t")
        )
        if source_run_text != expected_run_text or candidate_run_text != expected_run_text:
            raise ValueError(f"Run text mismatch at paragraph {paragraph_index}, run {run_index}")
        _prepare_run(source_run)
        _prepare_run(candidate_run)
        formatting = _format_spec(operation.get("format"))
        key = (paragraph_index, run_index)
        if key in seen:
            raise ValueError("Multiple format operations on the same target are unsupported")
        seen.add(key)
        targets.append((paragraph_index, run_index, formatting, source_run, candidate_run))
    return targets


def _property_nodes(run):
    from edit_structure import _elements

    properties = _elements(run, "rPr")
    if len(properties) > 1:
        raise ValueError("Duplicate run properties; editing is unsupported")
    if not properties:
        return None, {}
    nodes = {}
    for child in _elements(properties[0]):
        if child.namespaceURI == W:
            if child.localName in nodes:
                raise ValueError("Duplicate run property; editing is unsupported")
            nodes[child.localName] = child
    return properties[0], nodes


def _target_value(run, element_name, attribute_name):
    _, nodes = _property_nodes(run)
    node = nodes.get(element_name)
    if node is None:
        return None
    attribute = node.getAttributeNodeNS(W, attribute_name)
    return None if attribute is None else attribute.value


def _validate_requested_values(run, formatting):
    for key, value in formatting.items():
        if key == "font_east_asia":
            expected = {"eastAsia": value}
            themes = ("eastAsiaTheme",)
            prop_name = "rFonts"
        elif key == "font_latin":
            expected = {"ascii": value, "hAnsi": value}
            themes = ("asciiTheme", "hAnsiTheme")
            prop_name = "rFonts"
        elif key == "size_pt":
            expected = {"sz": str(int(value * 2)), "szCs": str(int(value * 2))}
            themes = ()
            prop_name = None
        elif key == "bold":
            expected = {"b": "1" if value else "0", "bCs": "1" if value else "0"}
            themes = ()
            prop_name = None
        elif key == "italic":
            expected = {"i": "1" if value else "0", "iCs": "1" if value else "0"}
            themes = ()
            prop_name = None
        else:
            expected = {"color": value}
            themes = ("themeColor", "themeTint", "themeShade")
            prop_name = None
        if prop_name is not None:
            _, nodes = _property_nodes(run)
            font_node = nodes.get(prop_name)
            if font_node is None:
                raise ValueError(f"Candidate did not apply format.{key}")
            for attribute, requested in expected.items():
                if font_node.getAttributeNS(W, attribute) != requested:
                    raise ValueError(f"Candidate has the wrong value for format.{key}")
            if any(font_node.hasAttributeNS(W, theme) for theme in themes):
                raise ValueError(f"Candidate retained a theme attribute for format.{key}")
            continue
        for name, requested in expected.items():
            actual = _target_value(run, name, "val")
            if actual != requested:
                raise ValueError(f"Candidate has the wrong value for format.{key}")
            _, nodes = _property_nodes(run)
            node = nodes.get(name)
            if node is None:
                raise ValueError(f"Candidate did not apply format.{key}")
            if any(node.hasAttributeNS(W, theme) for theme in themes):
                raise ValueError(f"Candidate retained a theme attribute for format.{key}")


def _mask_requested_attributes(run, formatting):
    _, nodes = _property_nodes(run)
    for field in formatting:
        for element_name, attributes in FORMAT_TARGETS[field].items():
            node = nodes.get(element_name)
            if node is None:
                continue
            for name in attributes:
                attribute = node.getAttributeNodeNS(W, name)
                if attribute is not None:
                    node.removeAttributeNode(attribute)


def _empty_added_element(node):
    if node.childNodes:
        return False
    for attribute in node.attributes.values():
        if (attribute.namespaceURI == XMLNS_NS and attribute.localName == "w"
                and attribute.value == W):
            continue
        return False
    return True


def _remove_added_format_nodes(source_run, candidate_run, formatting):
    source_rpr, source_nodes = _property_nodes(source_run)
    candidate_rpr, candidate_nodes = _property_nodes(candidate_run)
    target_names = set()
    for field in formatting:
        target_names.update(FORMAT_TARGETS[field])
    for element_name in target_names:
        if element_name in source_nodes:
            continue
        node = candidate_nodes.get(element_name)
        if node is not None and _empty_added_element(node):
            node.parentNode.removeChild(node)
    if source_rpr is None and candidate_rpr is not None and _empty_added_element(candidate_rpr):
        candidate_rpr.parentNode.removeChild(candidate_rpr)


def _namespace_binding(node, prefix):
    current = node.parentNode
    attribute_name = "xmlns:" + prefix
    while current is not None and current.nodeType == current.ELEMENT_NODE:
        if current.hasAttribute(attribute_name):
            return current.getAttribute(attribute_name)
        current = current.parentNode
    return None


def _normalize_existing_target_namespace(source_run, candidate_run, formatting):
    _, source_nodes = _property_nodes(source_run)
    _, candidate_nodes = _property_nodes(candidate_run)
    target_names = set()
    for field in formatting:
        target_names.update(FORMAT_TARGETS[field])
    for name in target_names:
        source_node = source_nodes.get(name)
        candidate_node = candidate_nodes.get(name)
        if source_node is None or candidate_node is None:
            continue
        source_has = source_node.hasAttributeNS(XMLNS_NS, "w")
        candidate_has = candidate_node.hasAttributeNS(XMLNS_NS, "w")
        if not candidate_has or candidate_node.getAttributeNS(XMLNS_NS, "w") != W:
            continue
        if _namespace_binding(candidate_node, "w") != W:
            continue
        if source_has:
            if source_node.getAttributeNS(XMLNS_NS, "w") != W:
                continue
            if _namespace_binding(source_node, "w") != W:
                continue
            source_node.removeAttributeNS(XMLNS_NS, "w")
        candidate_node.removeAttributeNS(XMLNS_NS, "w")


def _normalize_target_pair(source_run, candidate_run, formatting):
    _mask_requested_attributes(source_run, formatting)
    _mask_requested_attributes(candidate_run, formatting)
    _normalize_existing_target_namespace(source_run, candidate_run, formatting)
    _remove_added_format_nodes(source_run, candidate_run, formatting)


def compare_format_edits(original_package, candidate_package, specification, *, extension):
    """Compare two already-read package snapshots against format_run operations."""
    if extension not in {".docx", ".dotx"}:
        raise ValueError("Expected a DOCX or DOTX extension")
    _validate_spec_size(specification)
    specification = copy.deepcopy(specification)
    _validate_package(original_package, extension, "Original package")
    _validate_package(candidate_package, extension, "Candidate package")
    original_files = original_package.files
    candidate_files = candidate_package.files
    if set(original_files) != set(candidate_files):
        raise ValueError("Candidate package members differ from the original")
    for name in original_files:
        if name != "word/document.xml" and original_files[name] != candidate_files[name]:
            raise ValueError(f"Candidate changed a non-document package part: {name}")
    original_dom = parse_dom(read_xml(original_package / "word/document.xml"))
    candidate_dom = parse_dom(read_xml(candidate_package / "word/document.xml"))
    targets = _target_pairs(original_dom, candidate_dom, specification)
    for _, _, formatting, _, candidate_run in targets:
        _validate_requested_values(candidate_run, formatting)
    for _, _, formatting, source_run, candidate_run in targets:
        _normalize_target_pair(source_run, candidate_run, formatting)
    from edit_docx import _same_xml

    if not _same_xml(original_dom, candidate_dom):
        raise ValueError("Candidate contains changes outside the requested run formatting")
    return {"status": "validated", "targets": len(targets)}


def validate_format_edits(original, candidate, specification):
    """Read and validate a DOCX/DOTX candidate without writing any files."""
    _validate_spec_size(specification)
    source = _safe_input(original, "Original")
    candidate_path = _safe_input(candidate, "Candidate")
    extension = source.suffix.lower()
    if candidate_path.suffix.lower() != extension:
        raise ValueError("Original and candidate must have the same DOCX/DOTX extension")
    source_snapshot = read_package(source)
    candidate_snapshot = read_package(candidate_path)
    try:
        return compare_format_edits(
            source_snapshot, candidate_snapshot, specification, extension=extension
        )
    finally:
        source_snapshot.files.clear()
        candidate_snapshot.files.clear()


def publish_format_candidate_delivery(
    original, candidate, output, specification, *, protected_inputs=()
):
    """Publish the exact validated candidate snapshot, then finalize delivery."""
    _validate_spec_size(specification)
    source = _safe_input(original, "Original")
    candidate_path = _safe_input(candidate, "Candidate")
    extension = source.suffix.lower()
    if candidate_path.suffix.lower() != extension:
        raise ValueError("Original and candidate must have the same DOCX/DOTX extension")
    protected = [source, candidate_path, *map(Path, protected_inputs)]
    destination = validate_delivery_output(output, protected_inputs=protected)
    if destination.suffix.lower() != extension:
        raise ValueError("Output must have the same DOCX/DOTX extension as its inputs")
    source_snapshot = read_package(source)
    candidate_snapshot = read_package(candidate_path)
    try:
        result = compare_format_edits(
            source_snapshot, candidate_snapshot, specification, extension=extension
        )
        pack_document(candidate_snapshot, destination)
        return finalize_delivery(
            destination, protected, status="edited",
            result={"format_guard": result},
        )
    finally:
        source_snapshot.files.clear()
        candidate_snapshot.files.clear()


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_specification(path):
    path = validate_input_path(Path(path))
    with path.open("rb") as stream:
        data = stream.read(MAX_SPEC_BYTES + 1)
    if len(data) > MAX_SPEC_BYTES:
        raise ValueError("Format specification exceeds 2 MiB")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid UTF-8 JSON specification: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original")
    parser.add_argument("candidate")
    parser.add_argument("operations_json")
    args = parser.parse_args(argv)
    try:
        original = validate_input_path(Path(args.original))
        candidate = validate_input_path(Path(args.candidate))
        specification = _read_specification(args.operations_json)
        result = validate_format_edits(original, candidate, specification)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        print(f"format_guard: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
