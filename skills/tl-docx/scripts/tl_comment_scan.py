"""Read-side scanning and invariant checks for classic and threaded Word comments."""

import posixpath
import re

from docx.oxml.ns import qn

from tl_package import OFFICE_REL


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
W16CEX = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML = "http://www.w3.org/XML/1998/namespace"
MAX_ID = 2**31 - 1
COMMENT_REL = OFFICE_REL + "comments"
COMMENT_SPECS = (
    ("comments.xml", W, "comments", "comment", COMMENT_REL,
     "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),
    ("commentsExtended.xml", W15, "commentsEx", "commentEx",
     "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"),
    ("commentsIds.xml", W16CID, "commentsIds", "commentId",
     "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml"),
    ("commentsExtensible.xml", W16CEX, "commentsExtensible", "commentExtensible",
     "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml"),
)
CONTENT_TYPE_ALIASES = {
    "commentsExtended.xml": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
        "application/vnd.ms-word.commentsExt+xml",
    },
    "commentsIds.xml": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml",
        "application/vnd.ms-word.commentsId+xml",
    },
    "commentsExtensible.xml": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml",
        "application/vnd.ms-word.commentsExtensible+xml",
    },
}
MARKER_TAGS = {
    qn("w:commentRangeStart"): "start",
    qn("w:commentRangeEnd"): "end",
    qn("w:commentReference"): "reference",
}
XML_SPACE = "{%s}space" % XML


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= value.keys()
            or value.keys() - set(required) - set(optional)):
        raise ValueError("Missing or unsupported comment action fields")


def _text(value, label, *, empty=False):
    if (not isinstance(value, str) or (not value and not empty)
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                   or ord(char) in (0xFFFE, 0xFFFF) for char in value)):
        raise ValueError(f"{label} must be supported single-line Word text")
    return value


def _identifier(value, label="id"):
    if type(value) is not int or not 0 <= value <= MAX_ID:
        raise ValueError(f"{label} must be an integer from 0 to {MAX_ID}")
    return value


def _xml_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value):
        return None
    result = int(value)
    return result if result <= MAX_ID else None


def _hex_id(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{8}", value) else None


def _add_diagnostic(diagnostics, message):
    if message not in diagnostics:
        diagnostics.append(message)


def _comment_names(package):
    folder = posixpath.dirname(package.main)
    return {spec[0]: posixpath.join(folder, spec[0]) for spec in COMMENT_SPECS}


def _relationship_source(name):
    if name == "_rels/.rels":
        return ""
    if not name.endswith(".rels"):
        return None
    without_suffix = name[:-5]
    parent, leaf = posixpath.split(without_suffix)
    if parent == "_rels":
        return leaf
    if parent.endswith("/_rels"):
        return posixpath.join(parent[:-6], leaf)
    return None


def _signature_check(package, diagnostics):
    signed = any(name.lower().startswith("_xmlsignatures/") for name in package.names)
    for name in package.names:
        if not name.endswith(".rels"):
            continue
        source = _relationship_source(name)
        if source is None:
            _add_diagnostic(diagnostics, "Unsupported relationship part location")
            continue
        try:
            relations = package.relations(source)
        except ValueError:
            _add_diagnostic(diagnostics, "A package relationship part is unsupported")
            continue
        if any("digital-signature" in item["type"].lower() for item in relations.values()):
            signed = True
    if signed:
        _add_diagnostic(diagnostics, "Digitally signed packages cannot be mutated")


def _layout(package, diagnostics):
    names = _comment_names(package)
    present = {key for key, member in names.items() if member in package.names}
    try:
        relations = package.relations(package.main)
    except ValueError:
        relations = {}
        _add_diagnostic(diagnostics, "The main document relationships are unsupported")
    by_type = {spec[4]: spec[0] for spec in COMMENT_SPECS}
    known = {}
    for relation in relations.values():
        kind = relation["type"]
        lowered = kind.lower()
        if not any(token in lowered for token in ("/comment", "/people", "/person")):
            continue
        filename = by_type.get(kind)
        if filename is None:
            _add_diagnostic(diagnostics, "Unknown comment or person relationship")
            continue
        if filename in known:
            _add_diagnostic(diagnostics, "Duplicate comment metadata relationship")
            continue
        if relation["external"]:
            _add_diagnostic(diagnostics, "External comment relationships are unsupported")
            continue
        if relation["target"] != names[filename]:
            _add_diagnostic(diagnostics, "Comment relationship target is unsupported")
            continue
        known[filename] = relation
    for filename, member in names.items():
        if filename in present and filename not in known:
            _add_diagnostic(diagnostics, f"Missing relationship for {member}")
        if filename in known and filename not in present:
            _add_diagnostic(diagnostics, f"Missing comment part {member}")
        if filename in present:
            content_type = COMMENT_SPECS[[spec[0] for spec in COMMENT_SPECS].index(filename)][5]
            accepted_types = CONTENT_TYPE_ALIASES.get(filename, {content_type})
            if package.types.get(member) not in accepted_types:
                _add_diagnostic(diagnostics, f"Incorrect content type for {member}")
    for member in package.names:
        if (posixpath.dirname(member) == posixpath.dirname(package.main)
                and posixpath.basename(member).lower().startswith("comments")
                and member not in names.values()):
            _add_diagnostic(diagnostics, "Unknown comment metadata part")
    modern = present & {spec[0] for spec in COMMENT_SPECS[1:]}
    if modern and present != {spec[0] for spec in COMMENT_SPECS}:
        _add_diagnostic(diagnostics, "Modern comment metadata must contain all four parts")
    if present and "comments.xml" not in present:
        _add_diagnostic(diagnostics, "Comment metadata exists without comments.xml")
    mode = "modern" if modern else "classic" if "comments.xml" in present else "none"
    return mode, names, present


def _has_only_attributes(element, allowed):
    return set(element.attrib) <= set(allowed)


def _root_attributes_are_known(root):
    ignorable = root.get("{%s}Ignorable" % MC)
    if set(root.attrib) - {"{%s}Ignorable" % MC}:
        return False
    if ignorable is None:
        return True
    prefixes = ignorable.split()
    return all(prefix in root.nsmap for prefix in prefixes)


def _simple_run_properties(properties):
    if properties.attrib:
        return False
    return all(child.tag == qn("w:rStyle") and len(child) == 0
               and _has_only_attributes(child, {qn("w:val")}) for child in properties)


def _comment_is_simple(comment):
    if not _has_only_attributes(comment, {qn("w:id"), qn("w:author"), qn("w:date"), qn("w:initials")}):
        return False
    if len(comment) != 1 or comment[0].tag != qn("w:p"):
        return False
    paragraph = comment[0]
    if not _has_only_attributes(paragraph, {"{%s}paraId" % W14, "{%s}textId" % W14}):
        return False
    paragraph_properties = [node for node in paragraph if node.tag == qn("w:pPr")]
    if len(paragraph_properties) > 1:
        return False
    if any(node.tag != qn("w:pStyle") or len(node)
           or not _has_only_attributes(node, {qn("w:val")}) for node in
           (() if not paragraph_properties else paragraph_properties[0])):
        return False
    if any(node.tag not in (qn("w:pPr"), qn("w:r")) for node in paragraph):
        return False
    text_runs, reference_runs = [], []
    for run in (node for node in paragraph if node.tag == qn("w:r")):
        if not _has_only_attributes(run, {qn("w:rsidR"), qn("w:rsidRPr"), qn("w:rsidDel")}):
            return False
        text_nodes = [node for node in run if node.tag == qn("w:t")]
        references = [node for node in run if node.tag == qn("w:annotationRef")]
        properties = [node for node in run if node.tag == qn("w:rPr")]
        if len(properties) > 1 or any(not _simple_run_properties(node) for node in properties):
            return False
        if text_nodes and not references:
            if (len(text_nodes) != 1 or len(run) != len(text_nodes) + len(properties)
                    or not _has_only_attributes(text_nodes[0], {XML_SPACE})
                    or text_nodes[0].text is None or not text_nodes[0].text):
                return False
            value = text_nodes[0].text
            if ((value[:1].isspace() or value[-1:].isspace())
                    and text_nodes[0].get(XML_SPACE) != "preserve"):
                return False
            text_runs.append(run)
        elif references and not text_nodes:
            if (len(references) != 1 or len(run) != len(references) + len(properties)
                    or any(not _has_only_attributes(node, set()) or len(node) for node in references)):
                return False
            reference_runs.append(run)
        else:
            return False
    return len(text_runs) == 1 and len(reference_runs) <= 1


def _read_comment_definitions(package, mode, names, present, diagnostics):
    records = {}
    para_ids = {}
    if "comments.xml" not in present:
        return records, para_ids
    try:
        root = package.xml(names["comments.xml"])
    except ValueError:
        _add_diagnostic(diagnostics, "The comments part is malformed")
        return records, para_ids
    if root.tag != qn("w:comments"):
        _add_diagnostic(diagnostics, "The comments part has an unsupported root")
        return records, para_ids
    if not _root_attributes_are_known(root):
        _add_diagnostic(diagnostics, "The comments root has unsupported attributes")
    for child in root:
        if child.tag != qn("w:comment"):
            _add_diagnostic(diagnostics, "The comments part has unsupported content")
            continue
        identifier = _xml_identifier(child.get(qn("w:id")))
        if identifier is None:
            _add_diagnostic(diagnostics, "A comment has an invalid identifier")
            continue
        if identifier in records:
            _add_diagnostic(diagnostics, "Duplicate comment identifiers")
            continue
        text_value = "".join(node.text or "" for node in child.iter(qn("w:t")))
        record = {
            "id": identifier,
            "text": text_value,
            "author": child.get(qn("w:author"), ""),
            "parent_id": None,
            "paragraph_index": None,
            "editable": _comment_is_simple(child),
            "node": child,
        }
        if not record["editable"]:
            _add_diagnostic(diagnostics, f"Comment {identifier} has unsupported content")
        if not record["author"]:
            _add_diagnostic(diagnostics, f"Comment {identifier} has no supported author")
        records[identifier] = record
        if mode == "modern" and len(child) == 1 and child[0].tag == qn("w:p"):
            para_id = _hex_id(child[0].get("{%s}paraId" % W14))
            if para_id is None or para_id in para_ids:
                _add_diagnostic(diagnostics, "Comment paraId values are invalid or duplicated")
            else:
                para_ids[para_id] = identifier
                record["para_id"] = para_id
    return records, para_ids


def _metadata_entries(package, names, present, records, para_ids, diagnostics):
    mappings = {}
    for filename, namespace, root_name, entry_name, _, _ in COMMENT_SPECS[1:]:
        if filename not in present:
            continue
        try:
            root = package.xml(names[filename])
        except ValueError:
            _add_diagnostic(diagnostics, f"The {filename} part is malformed")
            continue
        if root.tag != f"{{{namespace}}}{root_name}" or not _root_attributes_are_known(root):
            _add_diagnostic(diagnostics, f"The {filename} root is unsupported")
            continue
        entries = {}
        allowed = {
            "commentsExtended.xml": {"{%s}paraId" % W15, "{%s}paraIdParent" % W15, "{%s}done" % W15},
            "commentsIds.xml": {"{%s}paraId" % W16CID, "{%s}durableId" % W16CID},
            "commentsExtensible.xml": {"{%s}durableId" % W16CEX, "{%s}dateUtc" % W16CEX},
        }[filename]
        key_attr = {
            "commentsExtended.xml": "{%s}paraId" % W15,
            "commentsIds.xml": "{%s}paraId" % W16CID,
            "commentsExtensible.xml": "{%s}durableId" % W16CEX,
        }[filename]
        for entry in root:
            if entry.tag != f"{{{namespace}}}{entry_name}" or len(entry) > 0:
                _add_diagnostic(diagnostics, f"The {filename} part has unsupported content")
                continue
            key = _hex_id(entry.get(key_attr))
            if key is None or key in entries or not _has_only_attributes(entry, allowed):
                _add_diagnostic(diagnostics, f"The {filename} part has invalid or duplicate metadata")
                continue
            entries[key] = entry
        mappings[filename] = entries
    required_para = set(para_ids)
    if set(mappings.get("commentsExtended.xml", {})) != required_para:
        _add_diagnostic(diagnostics, "Comment parent metadata is not one-to-one")
    if set(mappings.get("commentsIds.xml", {})) != required_para:
        _add_diagnostic(diagnostics, "Comment identity metadata is not one-to-one")
    identity_entries = mappings.get("commentsIds.xml", {})
    durable_owner = {}
    for para_id, entry in identity_entries.items():
        durable = _hex_id(entry.get("{%s}durableId" % W16CID))
        if durable is None or durable in durable_owner:
            _add_diagnostic(diagnostics, "Comment durableId values are invalid or duplicated")
        else:
            durable_owner[durable] = para_ids.get(para_id)
            if para_id in para_ids:
                records[para_ids[para_id]]["durable_id"] = durable
    if set(durable_owner) != set(mappings.get("commentsExtensible.xml", {})):
        _add_diagnostic(diagnostics, "Comment extensible metadata is not one-to-one")
    extension_entries = mappings.get("commentsExtended.xml", {})
    for para_id, entry in extension_entries.items():
        identifier = para_ids.get(para_id)
        if identifier is None:
            continue
        done = entry.get("{%s}done" % W15, "")
        if done not in ("", "0", "1", "true", "false"):
            _add_diagnostic(diagnostics, f"Comment {identifier} has an unsupported resolved state")
        records[identifier]["resolved"] = done in ("1", "true")
        parent = entry.get("{%s}paraIdParent" % W15)
        if parent:
            parent = _hex_id(parent)
            if parent is None or parent not in para_ids or parent == para_id:
                _add_diagnostic(diagnostics, f"Comment {identifier} has an invalid parent")
            else:
                records[identifier]["parent_id"] = para_ids[parent]
    for identifier, record in records.items():
        parent = record["parent_id"]
        if parent is not None and records.get(parent, {}).get("parent_id") is not None:
            _add_diagnostic(diagnostics, "Only one reply level is supported")
        if parent == identifier:
            _add_diagnostic(diagnostics, "Cyclic comment parent metadata")
    return mappings


def _ordinary_run(run):
    if run.tag != qn("w:r"):
        return False
    if not _has_only_attributes(run, {qn("w:rsidR"), qn("w:rsidRPr"), qn("w:rsidDel")}):
        return False
    return bool(list(run)) and all(child.tag in (qn("w:rPr"), qn("w:t")) for child in run)


def _run_text(run):
    value = []
    for node in run:
        if node.tag != qn("w:t"):
            continue
        if not _has_only_attributes(node, {XML_SPACE}):
            raise ValueError("Text run has unsupported attributes")
        text_node = node.text or ""
        if (text_node[:1].isspace() or text_node[-1:].isspace()) and node.get(XML_SPACE) != "preserve":
            raise ValueError("Ambiguous text whitespace")
        value.append(text_node)
    return "".join(value)


def _valid_reference_run(run, reference):
    if (run.tag != qn("w:r")
            or not _has_only_attributes(run, {qn("w:rsidR"), qn("w:rsidRPr"), qn("w:rsidDel")})):
        return False
    children = list(run)
    references = [node for node in children if node.tag == qn("w:commentReference")]
    if len(references) != 1 or references[0] is not reference:
        return False
    if any(node.tag not in (qn("w:rPr"), qn("w:commentReference")) for node in children):
        return False
    for properties in (node for node in children if node.tag == qn("w:rPr")):
        if properties.attrib or any(
                child.tag != qn("w:rStyle") or len(child)
                or not _has_only_attributes(child, {qn("w:val")}) for child in properties):
            return False
    return True


def _anchor_state(package, records, diagnostics):
    try:
        document = package.xml(package.main)
    except ValueError:
        _add_diagnostic(diagnostics, "The main document is malformed")
        return {}
    bodies = document.findall(qn("w:body"))
    if document.tag != qn("w:document") or len(bodies) != 1:
        _add_diagnostic(diagnostics, "Comment anchors require one Word document body")
        return {}
    body = bodies[0]
    paragraphs = body.findall(qn("w:p"))
    paragraph_indexes = {id(paragraph): index for index, paragraph in enumerate(paragraphs)}
    groups = {}
    for node in document.iter():
        kind = MARKER_TAGS.get(node.tag)
        if kind is None:
            continue
        identifier = _xml_identifier(node.get(qn("w:id")))
        if identifier is None or identifier not in records:
            _add_diagnostic(diagnostics, "Unknown or invalid comment anchor identifier")
            continue
        group = groups.setdefault(identifier, {})
        if kind in group:
            _add_diagnostic(diagnostics, "Duplicate comment anchor marker")
            continue
        group[kind] = node
    if set(groups) != set(records):
        _add_diagnostic(diagnostics, "Comment definitions and body anchors do not match")
    for identifier, group in groups.items():
        if set(group) != {"start", "end", "reference"}:
            _add_diagnostic(diagnostics, f"Comment {identifier} has incomplete anchors")
            continue
        start, end, reference = (group[key] for key in ("start", "end", "reference"))
        start_para, end_para = start.getparent(), end.getparent()
        reference_run = reference.getparent()
        paragraph = start_para
        if (paragraph is not end_para or paragraph not in paragraphs
                or paragraph.getparent() is not body or reference_run is None
                or reference_run.tag != qn("w:r") or reference_run.getparent() is not paragraph):
            _add_diagnostic(diagnostics, f"Comment {identifier} is outside a direct body paragraph")
            continue
        if any(not _has_only_attributes(marker, {qn("w:id")}) or len(marker)
               for marker in (start, end, reference)):
            _add_diagnostic(diagnostics, f"Comment {identifier} has unsupported anchor markup")
        local_reference_runs = {
            other["reference"].getparent()
            for other in groups.values()
            if ("reference" in other and other["reference"].getparent() is not None
                and other["reference"].getparent().getparent() is paragraph)
        }
        if (reference_run not in local_reference_runs
                or any(not _valid_reference_run(run, next(
                    node for node in run if node.tag == qn("w:commentReference")))
                    for run in local_reference_runs)):
            _add_diagnostic(diagnostics, f"Comment {identifier} has a mixed reference run")
        children = list(paragraph)
        positions = (children.index(start), children.index(end), children.index(reference_run))
        if not positions[0] < positions[1] < positions[2]:
            _add_diagnostic(diagnostics, f"Comment {identifier} anchors are not ordered")
        for child in children:
            if child.tag == qn("w:pPr") or child.tag in MARKER_TAGS:
                continue
            if child.tag != qn("w:r"):
                _add_diagnostic(diagnostics, f"Comment {identifier} paragraph has active or unknown anchors")
                break
            if child in local_reference_runs:
                continue
            if not _ordinary_run(child):
                _add_diagnostic(diagnostics, f"Comment {identifier} paragraph has non-ordinary text")
                break
            try:
                _run_text(child)
            except ValueError as exc:
                _add_diagnostic(diagnostics, str(exc))
                break
        record = records.get(identifier)
        if record is not None:
            record["paragraph_index"] = paragraph_indexes[id(paragraph)]
        group.update({"paragraph": paragraph, "positions": positions})
    for first_id, first in groups.items():
        if "positions" not in first:
            continue
        for second_id, second in groups.items():
            if second_id <= first_id or "positions" not in second or first["paragraph"] is not second["paragraph"]:
                continue
            first_start, first_end = first["positions"][:2]
            second_start, second_end = second["positions"][:2]
            crossed = (first_start < second_start < first_end < second_end
                       or second_start < first_start < second_end < first_end)
            if crossed:
                _add_diagnostic(diagnostics, "Comment ranges cross or overlap ambiguously")
    for identifier, record in records.items():
        parent_id = record["parent_id"]
        if parent_id is None:
            continue
        child, parent = groups.get(identifier), groups.get(parent_id)
        if (child is None or parent is None or "positions" not in child or "positions" not in parent
                or child["paragraph"] is not parent["paragraph"]
                or not (parent["positions"][0] < child["positions"][0]
                        and child["positions"][1] < parent["positions"][1])):
            _add_diagnostic(diagnostics, "Reply anchors must be nested inside their root comment")
    return groups


def _scan(package):
    """Inspect all comment definitions, metadata parts, and body anchors in a package."""
    diagnostics = []
    _signature_check(package, diagnostics)
    mode, names, present = _layout(package, diagnostics)
    records, para_ids = _read_comment_definitions(package, mode, names, present, diagnostics)
    mappings = {}
    if mode == "modern" and present == {spec[0] for spec in COMMENT_SPECS}:
        mappings = _metadata_entries(package, names, present, records, para_ids, diagnostics)
    groups = _anchor_state(package, records, diagnostics)
    return {"mode": mode, "names": names, "present": present, "records": records,
            "para_ids": para_ids, "mappings": mappings, "groups": groups,
            "diagnostics": diagnostics, "mutable": not diagnostics}


def _public_result(state, action):
    """Format the public diagnostic and comment-list payload for callers."""
    comments = []
    for identifier, record in sorted(state["records"].items()):
        item = {key: record[key] for key in ("id", "text", "author", "parent_id", "paragraph_index")}
        if "resolved" in record:
            item["resolved"] = record["resolved"]
        comments.append(item)
    return {"action": action, "mode": state["mode"], "mutable": state["mutable"],
            "diagnostics": list(state["diagnostics"]), "comments": comments}
