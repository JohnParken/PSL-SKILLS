"""Bounded, location-preserving XML part views for the DOCX package reader."""

from xml.parsers import expat
import json


MAX_NODES = 200000
MAX_DEPTH = 256
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_PART_BYTES = 32 * 1024 * 1024
XML_NS = "http://www.w3.org/XML/1998/namespace"
STORY_RELATIONS = {
    "header": "header",
    "footer": "footer",
    "footnotes": "footnotes",
    "endnotes": "endnotes",
    "comments": "comments",
    "styles": "styles",
    "numbering": "numbering",
}
RELATED_KINDS = {value: value for value in STORY_RELATIONS.values()}


class _Budget:
    """Share explicit node and encoded-output limits across requested parts."""

    def __init__(self):
        self.nodes = 0
        self.output = 0
        self.text_slots = {}

    def reserve(self, amount):
        self.output += amount
        if self.output > MAX_OUTPUT_BYTES:
            raise ValueError("XML structure output exceeds the size limit")

    @staticmethod
    def string_size(value):
        size = 2
        for character in value:
            codepoint = ord(character)
            if character in ('"', "\\"):
                size += 2
            elif codepoint < 0x20:
                size += 6
            elif codepoint <= 0x7F:
                size += 1
            elif codepoint <= 0xFFFF:
                size += 6
            else:
                size += 12
        return size

    def value(self, value):
        self.reserve(self.string_size(value))

    def node(self, depth):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ValueError("XML structure exceeds the node limit")
        if depth > MAX_DEPTH:
            raise ValueError("XML structure exceeds the depth limit")
        self.reserve(128)

    def text(self, node, field, value):
        sizes = self.text_slots.setdefault(id(node), {})
        size = sizes.get(field, 0)
        if size == 0:
            self.reserve(2)
        for character in value:
            codepoint = ord(character)
            if character in ('"', "\\"):
                self.reserve(2)
            elif codepoint < 0x20:
                self.reserve(6)
            elif codepoint <= 0x7F:
                self.reserve(1)
            elif codepoint <= 0xFFFF:
                self.reserve(6)
            else:
                self.reserve(12)
        sizes[field] = size + len(value)


def _expanded_name(qualified_name, namespaces, is_attribute=False):
    if ":" in qualified_name:
        prefix, local = qualified_name.split(":", 1)
        namespace = namespaces.get(prefix, "")
    else:
        prefix = None
        local = qualified_name
        namespace = "" if is_attribute else namespaces.get("", "")
    expanded = "{" + namespace + "}" + local if namespace else local
    return expanded, namespace or None, prefix, local


def _append_child(parent, child):
    parent["children"].append(child)


def _new_element(name, attributes, namespaces, declarations):
    tag, namespace, prefix, local = _expanded_name(name, namespaces)
    values = {}
    prefixes = {}
    for raw_name, value in attributes.items():
        if raw_name == "xmlns" or raw_name.startswith("xmlns:"):
            continue
        expanded, _, attr_prefix, _ = _expanded_name(
            raw_name, namespaces, is_attribute=True
        )
        values[expanded] = value
        prefixes[expanded] = attr_prefix
    return {
        "type": "element",
        "tag": tag,
        "name": local,
        "namespace": namespace,
        "prefix": prefix,
        "qualified_name": name,
        "attributes": values,
        "attribute_prefixes": prefixes,
        "namespace_declarations": declarations,
        "children": [],
        "text": "",
        "tail": "",
    }


def _parse_part(package, part, budget, revisions):
    """Use Expat without namespace expansion so source prefixes stay visible."""
    verified = package.xml(part)
    if getattr(verified, "getroottree", None) is None:
        raise ValueError("Package XML verification did not return a parsed root")
    data = package.read(part)
    if len(data) > MAX_PART_BYTES:
        raise ValueError("XML part exceeds the size limit")
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    frames = []
    root = None
    cdata_node = None

    def reject_doctype(*_args):
        raise ValueError("DTD is not supported in document parts")

    def reject_entity(*_args):
        raise ValueError("Entity declarations are not supported in document parts")

    def reject_external(*_args):
        raise ValueError("External XML entities are not supported")

    def append_text(value):
        if not value or not frames:
            return
        parent = frames[-1][0]
        if parent is None:
            return
        if cdata_node is not None:
            budget.text(cdata_node, "text", value)
            cdata_node["text"] += value
            return
        if parent["children"]:
            target = parent["children"][-1]
            budget.text(target, "tail", value)
            target["tail"] += value
        else:
            budget.text(parent, "text", value)
            parent["text"] += value

    def start(name, attributes):
        nonlocal root
        parent_namespaces = frames[-1][1] if frames else {"xml": XML_NS}
        namespaces = dict(parent_namespaces)
        declarations = []
        for key, value in attributes.items():
            if key == "xmlns":
                prefix = None
            elif key.startswith("xmlns:"):
                prefix = key.split(":", 1)[1]
            else:
                continue
            namespaces[prefix or ""] = value
            declarations.append({"prefix": prefix, "namespace": value})
            budget.reserve(96)
            budget.value(key)
            budget.value(value)
        depth = len(frames) + 1
        if depth > MAX_DEPTH:
            raise ValueError("XML structure exceeds the depth limit")
        if frames and frames[-1][0] is None:
            frames.append((None, namespaces))
            return
        node = _new_element(name, attributes, namespaces, declarations)
        revision_group = None
        if node["namespace"] == (
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        ):
            revision_group = {
                "ins": "accepted", "moveTo": "accepted",
                "del": "original", "moveFrom": "original",
            }.get(node["name"])
        included = revision_group in (None, revisions, "all")
        if not included:
            node["included"] = False
            node["omitted"] = "revision-view"
        budget.node(depth)
        budget.value(name)
        for expanded, value in node["attributes"].items():
            budget.value(expanded)
            budget.value(value)
            budget.reserve(64)
        if frames:
            _append_child(frames[-1][0], node)
        elif root is None:
            root = node
        else:
            raise ValueError("XML part has multiple root elements")
        frames.append((node if included else None, namespaces))

    def end(_name):
        if not frames:
            raise ValueError("Unexpected XML closing element")
        frames.pop()

    def comment(value):
        if not frames or frames[-1][0] is None:
            return
        node = {"type": "comment", "text": value, "tail": ""}
        budget.node(len(frames) + 1)
        budget.text(node, "text", value)
        _append_child(frames[-1][0], node)

    def processing_instruction(target, value):
        if not frames or frames[-1][0] is None:
            return
        node = {
            "type": "processing_instruction",
            "target": target,
            "text": value,
            "tail": "",
        }
        budget.node(len(frames) + 1)
        budget.value(target)
        budget.text(node, "text", value)
        _append_child(frames[-1][0], node)

    def start_cdata():
        nonlocal cdata_node
        if frames and frames[-1][0] is not None:
            cdata_node = {"type": "cdata", "text": "", "tail": ""}
            budget.node(len(frames) + 1)
            _append_child(frames[-1][0], cdata_node)

    def end_cdata():
        nonlocal cdata_node
        cdata_node = None

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = append_text
    parser.CommentHandler = comment
    parser.ProcessingInstructionHandler = processing_instruction
    parser.StartCdataSectionHandler = start_cdata
    parser.EndCdataSectionHandler = end_cdata
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.EntityDeclHandler = reject_entity
    parser.ExternalEntityRefHandler = reject_external
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, UnicodeError) as exc:
        raise ValueError("Malformed XML part") from exc
    if root is None or frames:
        raise ValueError("XML part has no complete root element")
    return root


def _is_xml_part(package, part):
    content_type = package.types.get(part, "").lower()
    return (
        content_type in ("application/xml", "text/xml")
        or content_type.endswith("+xml")
        or content_type.endswith("/xml")
        or part.lower().endswith((".xml", ".rels"))
    )


def _validate_part(package, part):
    from tl_package import safe_member

    if not isinstance(part, str) or not safe_member(part):
        raise ValueError("XML part names must be safe package member names")
    if part not in package.names:
        raise ValueError("Requested XML part is missing from the package")
    if not _is_xml_part(package, part):
        raise ValueError("Requested package part is not declared as XML")


def _resolved_related(package, include_stories):
    relations = getattr(package, "relations", None)
    if not callable(relations):
        raise ValueError("Package reader does not expose relationship resolution")
    selected = {}
    for relationship in relations(package.main).values():
        if relationship.get("external"):
            continue
        target = relationship.get("target")
        relation_type = relationship.get("type", "").rsplit("/", 1)[-1]
        kind = RELATED_KINDS.get(relation_type)
        if (kind is not None and target in package.names
                and (include_stories or kind in ("styles", "numbering"))):
            selected[target] = kind
    return selected


def _explicit_kind(part):
    path = part.lower().split("/")
    name = path[-1]
    if "charts" in path or name.startswith("chart"):
        return "chart"
    if name == "settings.xml":
        return "settings"
    if name == "theme.xml" or ("theme" in path and name.startswith("theme")):
        return "theme"
    return "xml"


def structure(package, *, revisions="accepted", include_related=False, parts=()):
    """Return an untruncated XML tree for selected package parts and text view."""
    if expat.version_info < (2, 6, 0):
        raise RuntimeError("OOXML structure requires Expat 2.6.0 or newer")
    if revisions not in ("accepted", "original", "all"):
        raise ValueError("revisions must be accepted, original or all")
    if not isinstance(include_related, bool):
        raise ValueError("include_related must be a boolean")
    if isinstance(parts, (str, bytes)):
        raise ValueError("parts must be a collection of package part names")
    try:
        explicit_parts = list(parts)
    except TypeError as exc:
        raise ValueError("parts must be a collection of package part names") from exc
    related = (
        _resolved_related(package, include_stories=include_related)
        if not explicit_parts or include_related else {}
    )
    for part in explicit_parts:
        _validate_part(package, part)
    if explicit_parts:
        selected = {part: _explicit_kind(part) for part in explicit_parts}
    else:
        selected = {package.main: "main"}
    for part, kind in related.items():
        selected.setdefault(part, kind)
    budget = _Budget()
    records = []
    encoded_output = 0
    for part in sorted(selected, key=lambda name: (name != package.main, name)):
        _validate_part(package, part)
        root = _parse_part(package, part, budget, revisions)
        encoded_output += len(
            json.dumps(root, ensure_ascii=True, separators=(",", ":"))
        ) + 128
        if encoded_output > MAX_OUTPUT_BYTES:
            raise ValueError("XML structure output exceeds the size limit")
        records.append({
            "type": "xml_part",
            "part": part,
            "path": "/",
            "kind": selected[part],
            "root": root,
        })
    coverage = {
        "status": "ok",
        "parts": len(records),
        "nodes": budget.nodes,
        "output_limit_bytes": MAX_OUTPUT_BYTES,
        "revisions": revisions,
        "related_included": include_related,
    }
    result = (records, [], coverage)
    if len(json.dumps(result, ensure_ascii=True, separators=(",", ":"))) > MAX_OUTPUT_BYTES:
        raise ValueError("XML structure output exceeds the size limit")
    return result
