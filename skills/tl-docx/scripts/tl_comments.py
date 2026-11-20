"""Read and mutate a guarded subset of classic and threaded Word comments."""

from copy import deepcopy
from datetime import datetime, timezone
import secrets

from docx.opc.oxml import serialize_part_xml
from docx.oxml.ns import qn

from tl_comment_scan import (
    COMMENT_SPECS,
    MARKER_TAGS,
    W,
    W14,
    W15,
    W16CEX,
    W16CID,
    XML_SPACE,
    _fields,
    _hex_id,
    _identifier,
    _ordinary_run,
    _public_result,
    _run_text,
    _scan,
    _text,
    _xml_identifier,
)
from tl_package import CT, REL, TYPES, Package, parse, rels_member


def _specification(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("action"), str):
        raise ValueError("Expected one comment action")
    action = spec["action"]
    if action == "list":
        _fields(spec, ("action",))
    elif action == "add":
        _fields(spec, ("action", "paragraph_index", "expected_text", "anchor_text", "text", "author"), ("initials",))
        if type(spec["paragraph_index"]) is not int or spec["paragraph_index"] < 0:
            raise ValueError("paragraph_index must be a nonnegative integer")
        _text(spec["expected_text"], "expected_text", empty=True)
        _text(spec["anchor_text"], "anchor_text")
        _text(spec["text"], "text")
        _text(spec["author"], "author")
        _text(spec.get("initials", ""), "initials", empty=True)
    elif action == "reply":
        _fields(spec, ("action", "id", "expected_text", "text", "author"), ("initials",))
        _identifier(spec["id"])
        _text(spec["expected_text"], "expected_text", empty=True)
        _text(spec["text"], "text")
        _text(spec["author"], "author")
        _text(spec.get("initials", ""), "initials", empty=True)
    elif action == "update":
        _fields(spec, ("action", "id", "expected_text", "text"))
        _identifier(spec["id"])
        _text(spec["expected_text"], "expected_text", empty=True)
        _text(spec["text"], "text")
    elif action in ("delete", "resolve", "reopen"):
        _fields(spec, ("action", "id", "expected_text"))
        _identifier(spec["id"])
        _text(spec["expected_text"], "expected_text", empty=True)
    else:
        raise ValueError("Expected list, add, reply, update, delete, resolve or reopen")
    return action


def _find_comment(root, identifier):
    matches = [node for node in root.findall(qn("w:comment"))
               if _xml_identifier(node.get(qn("w:id"))) == identifier]
    if len(matches) != 1:
        raise ValueError("Comment identifier does not select exactly one comment")
    return matches[0]


def _paragraph_text(paragraph):
    if any(child.tag not in (qn("w:pPr"), qn("w:r")) for child in paragraph):
        raise ValueError("Anchor paragraph has active or unknown content")
    runs = [child for child in paragraph if child.tag == qn("w:r")]
    if not runs or any(not _ordinary_run(run) for run in runs):
        raise ValueError("Anchor paragraph must contain ordinary text runs")
    values = [_run_text(run) for run in runs]
    if any(not value for value in values):
        raise ValueError("Empty anchor runs are unsupported")
    return runs, "".join(values)


def _active_range_intersects(document, paragraph):
    body = document.find(qn("w:body"))
    if body is None:
        return True
    order = list(body.iter())
    target_nodes = list(paragraph.iter())
    start_index, end_index = order.index(target_nodes[0]), order.index(target_nodes[-1])
    ranges = {}
    for index, node in enumerate(order):
        if not isinstance(node.tag, str) or not node.tag.startswith("{" + W + "}"):
            continue
        local = node.tag.rsplit("}", 1)[-1]
        if local in ("commentRangeStart", "commentRangeEnd"):
            continue
        if local.endswith("RangeStart"):
            pair_name, kind = local[:-5], "start"
        elif local.endswith("RangeEnd"):
            pair_name, kind = local[:-3], "end"
        elif local in ("bookmarkStart", "permStart"):
            pair_name, kind = local[:-5], "start"
        elif local in ("bookmarkEnd", "permEnd"):
            pair_name, kind = local[:-3], "end"
        else:
            continue
        key = node.get(qn("w:id")) or node.get(qn("w:name"))
        ranges.setdefault((pair_name, key), {"start": [], "end": []})[kind].append(index)
    for (_, key), pair in ranges.items():
        starts, ends = pair["start"], pair["end"]
        if len(starts) > 1 or len(ends) > 1:
            if any(start <= end_index and end >= start_index for start in starts for end in ends):
                return True
            if any(start <= end_index for start in starts) or any(end >= start_index for end in ends):
                return True
        if key is None and any(start <= end_index for start in starts):
            return True
        for start in starts:
            after = [end for end in ends if end > start]
            if not after:
                if start <= end_index:
                    return True
                continue
            end = min(after)
            if start <= end_index and end >= start_index:
                return True
        if not starts and any(end >= start_index for end in ends):
            return True
    field_starts = []
    for index, node in enumerate(order):
        if node.tag != qn("w:fldChar"):
            continue
        kind = node.get(qn("w:fldCharType"))
        if kind == "begin":
            field_starts.append(index)
        elif kind == "end":
            if field_starts:
                start = field_starts.pop()
                if start <= end_index and index >= start_index:
                    return True
            elif index >= start_index:
                return True
    return any(start <= end_index for start in field_starts)


def _add_position(paragraph, spec):
    runs, actual = _paragraph_text(paragraph)
    if actual != spec["expected_text"]:
        raise ValueError("Anchor paragraph text mismatch")
    anchor = spec["anchor_text"]
    start = actual.find(anchor)
    if start < 0 or actual.find(anchor, start + 1) >= 0:
        raise ValueError("Anchor text must match exactly once in the selected paragraph")
    boundaries = {0: 0}
    offset = 0
    for index, run in enumerate(runs):
        offset += len(_run_text(run))
        boundaries[offset] = index + 1
    end = start + len(anchor)
    if start not in boundaries or end not in boundaries:
        raise ValueError("Anchor must cover complete consecutive runs")
    return runs[boundaries[start]], runs[boundaries[end] - 1]


def _new_element(namespace, local):
    node = parse(f'<x:item xmlns:x="{namespace}"/>'.encode("utf-8"))
    node.tag = f"{{{namespace}}}{local}"
    return node


def _append(parent, namespace, local, attributes=()):
    node = _new_element(namespace, local)
    for key, value in attributes:
        node.set(key, str(value))
    parent.append(node)
    return node


def _next_hex(used):
    for _ in range(128):
        value = f"{secrets.randbelow(0xFFFFFFFE) + 1:08X}"
        if value not in used:
            return value
    raise ValueError("Could not allocate comment metadata identifiers")


def _allocate_comment(state, roots, text_value, author, initials, parent_id=None):
    records = state["records"]
    identifier = max(records, default=-1) + 1
    _identifier(identifier)
    used_para = {record.get("para_id") for record in records.values()}
    used_durable = {record.get("durable_id") for record in records.values()}
    modern = state["mode"] != "classic"
    para_id = _next_hex(used_para) if modern else None
    durable_id = _next_hex(used_durable) if modern else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    comments = roots[state["names"]["comments.xml"]]
    comment = _append(comments, W, "comment", (
        (qn("w:id"), identifier), (qn("w:author"), author), (qn("w:initials"), initials),
        (qn("w:date"), now),
    ))
    paragraph = _append(comment, W, "p", (("{%s}paraId" % W14, para_id),) if modern else ())
    run = _append(paragraph, W, "r")
    text_node = _append(run, W, "t", ((XML_SPACE, "preserve"),))
    text_node.text = text_value
    if not modern:
        return identifier
    extension_attrs = [("{%s}paraId" % W15, para_id), ("{%s}done" % W15, "0")]
    if parent_id is not None:
        extension_attrs.append(("{%s}paraIdParent" % W15, records[parent_id]["para_id"]))
    _append(roots[state["names"]["commentsExtended.xml"]], W15, "commentEx", extension_attrs)
    _append(roots[state["names"]["commentsIds.xml"]], W16CID, "commentId", (
        ("{%s}paraId" % W16CID, para_id), ("{%s}durableId" % W16CID, durable_id),
    ))
    _append(roots[state["names"]["commentsExtensible.xml"]], W16CEX, "commentExtensible", (
        ("{%s}durableId" % W16CEX, durable_id), ("{%s}dateUtc" % W16CEX, now),
    ))
    return identifier


def _marker(parent, name, identifier):
    return _append(parent, W, name, ((qn("w:id"), identifier),))


def _reference_run(paragraph, identifier):
    run = _append(paragraph, W, "r")
    _append(run, W, "commentReference", ((qn("w:id"), identifier),))
    return run


def _insert_add_anchor(paragraph, first, last, identifier):
    start = _marker(first.getparent(), "commentRangeStart", identifier)
    first.addprevious(start)
    end = _marker(last.getparent(), "commentRangeEnd", identifier)
    last.addnext(end)
    end.addnext(_reference_run(paragraph, identifier))


def _insert_reply_anchor(paragraph, parent_id, identifier):
    starts = [node for node in paragraph.findall(qn("w:commentRangeStart"))
              if node.get(qn("w:id")) == str(parent_id)]
    ends = [node for node in paragraph.findall(qn("w:commentRangeEnd"))
            if node.get(qn("w:id")) == str(parent_id)]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Root comment does not have one complete anchor")
    start = _marker(paragraph, "commentRangeStart", identifier)
    starts[0].addnext(start)
    end = _marker(paragraph, "commentRangeEnd", identifier)
    ends[0].addprevious(end)
    paragraph.append(_reference_run(paragraph, identifier))


def _ensure_roots(package, state, roots, replacements):
    if state["mode"] != "none":
        return
    for filename, namespace, root_name, _, _, _ in COMMENT_SPECS:
        member = state["names"][filename]
        if member in package.names:
            raise ValueError("Comment metadata part name is already occupied")
        roots[member] = _new_element(namespace, root_name)
    rels_name = rels_member(package.main)
    rels_root = (deepcopy(package.xml(rels_name)) if rels_name in package.names
                 else parse(f'<Relationships xmlns="{REL[1:-1]}"/>'.encode("utf-8")))
    content_root = deepcopy(package.xml(TYPES))
    existing_ids = set(package.relations(package.main))
    suffix = 1
    for filename, _, _, _, relation_type, content_type in COMMENT_SPECS:
        while f"rId{suffix}" in existing_ids:
            suffix += 1
        relation = _append(rels_root, REL[1:-1], "Relationship")
        relation.set("Id", f"rId{suffix}")
        relation.set("Type", relation_type)
        relation.set("Target", filename)
        existing_ids.add(f"rId{suffix}")
        suffix += 1
        override = _append(content_root, CT[1:-1], "Override")
        override.set("PartName", "/" + state["names"][filename])
        override.set("ContentType", content_type)
    roots[rels_name] = rels_root
    roots[TYPES] = content_root
    replacements[rels_name] = serialize_part_xml(rels_root)
    replacements[TYPES] = serialize_part_xml(content_root)


def _remove_comment(state, roots, identifier):
    record = state["records"][identifier]
    comment_root = roots[state["names"]["comments.xml"]]
    comment_root.remove(_find_comment(comment_root, identifier))
    main = roots[state["package_main"]]
    for node in list(main.iter()):
        if node.tag not in MARKER_TAGS or node.get(qn("w:id")) != str(identifier):
            continue
        parent = node.getparent()
        if node.tag == qn("w:commentReference") and parent is not None and parent.getparent() is not None:
            parent.getparent().remove(parent)
        elif parent is not None:
            parent.remove(node)
    if state["mode"] == "modern":
        para_id, durable_id = record["para_id"], record["durable_id"]
        for filename, namespace, _, entry_name, _, _ in COMMENT_SPECS[1:]:
            root = roots[state["names"][filename]]
            key_attr = ("{%s}durableId" % namespace if filename == "commentsExtensible.xml"
                        else "{%s}paraId" % namespace)
            target = durable_id if filename == "commentsExtensible.xml" else para_id
            matches = [node for node in root
                       if node.tag == f"{{{namespace}}}{entry_name}" and node.get(key_attr) == target]
            if len(matches) != 1:
                raise ValueError("Comment metadata does not select one entry for deletion")
            root.remove(matches[0])


def _mutation_roots(package, state, action):
    targets = {state["names"]["comments.xml"]}
    if action in ("add", "reply", "delete"):
        targets.add(package.main)
    if action in ("add", "reply", "delete") and state["mode"] == "modern":
        targets.update(state["names"][name] for name, *_ in COMMENT_SPECS[1:])
    if action in ("resolve", "reopen"):
        targets = {state["names"]["commentsExtended.xml"]}
    if action == "add" and state["mode"] == "none":
        targets.update(state["names"].values())
        targets.update((TYPES, rels_member(package.main)))
    roots = {member: deepcopy(package.xml(member)) for member in targets if member in package.names}
    state["package_main"] = package.main
    return roots, targets


def _guard_record(state, spec):
    identifier = spec["id"]
    record = state["records"].get(identifier)
    if record is None or record["text"] != spec["expected_text"]:
        raise ValueError("Comment ID or expected_text guard mismatch")
    return record


def manage_comments(package: Package, spec: dict) -> tuple[bytes | None, dict]:
    """List comments or return a validated in-memory DOCX/DOTX mutation."""
    action = _specification(spec)
    state = _scan(package)
    if action == "list":
        return None, _public_result(state, action)
    if state["diagnostics"]:
        raise ValueError("Comment mutation is unsupported: " + "; ".join(state["diagnostics"]))
    if action in ("resolve", "reopen") and state["mode"] != "modern":
        raise ValueError("Thread state changes require complete modern comment metadata")
    if action == "reply" and state["mode"] != "modern":
        raise ValueError("Replies require complete modern comment metadata")
    record = None
    if action in ("update", "delete", "reply", "resolve", "reopen"):
        record = _guard_record(state, spec)
        if not record["editable"]:
            raise ValueError("Only simple single-run comments can be changed")
    if action in ("resolve", "reopen") and record["parent_id"] is not None:
        raise ValueError("Thread state changes require a root comment")
    if action == "reply" and (record["parent_id"] is not None or record.get("resolved", False)):
        raise ValueError("Replies require unresolved root comments")
    if (action == "delete"
            and any(item["parent_id"] == spec["id"] for item in state["records"].values())):
        raise ValueError("Delete replies before their parent comment")
    roots, targets = _mutation_roots(package, state, action)
    replacements = {}
    if action == "add":
        main = roots[package.main]
        paragraphs = main.find(qn("w:body")).findall(qn("w:p"))
        index = spec["paragraph_index"]
        if index >= len(paragraphs):
            raise ValueError("paragraph_index is outside direct body paragraphs")
        if _active_range_intersects(main, paragraphs[index]):
            raise ValueError("Active ranges or fields overlap the anchor paragraph")
        first, last = _add_position(paragraphs[index], spec)
        _ensure_roots(package, state, roots, replacements)
        identifier = _allocate_comment(state, roots, spec["text"], spec["author"], spec.get("initials", ""))
        _insert_add_anchor(paragraphs[index], first, last, identifier)
    elif action == "reply":
        main = roots[package.main]
        paragraphs = main.find(qn("w:body")).findall(qn("w:p"))
        paragraph = paragraphs[record["paragraph_index"]]
        if _active_range_intersects(main, paragraph):
            raise ValueError("Active ranges or fields overlap the reply anchor paragraph")
        identifier = _allocate_comment(
            state, roots, spec["text"], spec["author"], spec.get("initials", ""), parent_id=spec["id"])
        _insert_reply_anchor(paragraph, spec["id"], identifier)
    elif action == "update":
        comment = _find_comment(roots[state["names"]["comments.xml"]], spec["id"])
        text_node = comment.find("./%s/%s/%s" % (qn("w:p"), qn("w:r"), qn("w:t")))
        text_node.text = spec["text"]
        text_node.set(XML_SPACE, "preserve")
        identifier = spec["id"]
    elif action in ("resolve", "reopen"):
        identifier = spec["id"]
        member = state["names"]["commentsExtended.xml"]
        para_attr = "{%s}paraId" % W15
        entry = next(node for node in roots[member] if _hex_id(node.get(para_attr)) == record["para_id"])
        entry.set("{%s}done" % W15, "1" if action == "resolve" else "0")
    else:
        identifier = spec["id"]
        _remove_comment(state, roots, identifier)
    for member in targets:
        if member in roots:
            replacements[member] = serialize_part_xml(roots[member])
    candidate = package.overlay(replacements)
    verified = Package(candidate)
    checked_state = _scan(verified)
    if checked_state["diagnostics"]:
        raise ValueError("Edited comment package failed invariants: " + "; ".join(checked_state["diagnostics"]))
    if action in ("add", "reply"):
        added = checked_state["records"].get(identifier)
        if added is None or added["text"] != spec["text"]:
            raise ValueError("Edited comment is missing from the verified package")
        if action == "reply" and added["parent_id"] != spec["id"]:
            raise ValueError("Edited reply parent failed verification")
    elif action == "update":
        if checked_state["records"].get(identifier, {}).get("text") != spec["text"]:
            raise ValueError("Updated comment failed verification")
    elif action in ("resolve", "reopen"):
        verified_record = checked_state["records"].get(identifier, {})
        if verified_record.get("resolved") is not (action == "resolve"):
            raise ValueError("Thread state change failed verification")
    elif identifier in checked_state["records"]:
        raise ValueError("Deleted comment remains in the verified package")
    preserved = Package(candidate)
    for member in package.names - set(replacements):
        if preserved.read(member) != package.read(member):
            raise ValueError("A non-target package part changed")
    changed = sorted(member for member, payload in replacements.items()
                     if member not in package.names or payload != package.read(member))
    result = _public_result(checked_state, action)
    result.update(comment_id=identifier, changed_parts=changed)
    return candidate, result
