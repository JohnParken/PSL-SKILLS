"""Revision-view traversal helpers for the DOCX report reader."""

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MARKUP_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REVISION_GROUPS = {
    "ins": "accepted",
    "moveTo": "accepted",
    "del": "original",
    "moveFrom": "original",
}
REVISION_KINDS = {
    "ins": "inserted",
    "del": "deleted",
    "moveFrom": "moved_from",
    "moveTo": "moved_to",
}
PROPERTY_CHANGES = {
    "pPrChange", "rPrChange", "tblPrChange", "trPrChange",
    "tcPrChange", "sectPrChange", "numberingChange",
}


def _name(tag):
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _children(node):
    return [child for child in node if isinstance(child.tag, str)]


def _revision_local(node):
    namespace, local = _name(node.tag)
    return local if namespace == WORD_NS and local in REVISION_GROUPS else None


def _include(reader, node, filter_revisions):
    if not filter_revisions or reader.revisions in (None, "all"):
        return True
    group = REVISION_GROUPS.get(_revision_local(node))
    return group is None or group == reader.revisions


def selected_children(reader, node, part, filter_revisions=True):
    """Choose compatibility branches and optionally exclude one revision view."""
    for child in _children(node):
        namespace, local = _name(child.tag)
        if namespace == MARKUP_NS and local == "AlternateContent":
            branch = reader._alternate_branch(child, part)
            if branch is not None:
                for selected in _children(branch):
                    if _include(reader, selected, filter_revisions):
                        yield selected
            continue
        if _include(reader, child, filter_revisions):
            yield child


def text(reader, node, deleted_only=False):
    pieces = []
    legacy = reader.revisions is None
    inherited_group = None
    if not legacy:
        parent = node.getparent()
        while parent is not None:
            local = _revision_local(parent)
            if local is not None:
                inherited_group = REVISION_GROUPS[local]
                break
            parent = parent.getparent()

    def visit(current, group=None):
        namespace, local = _name(current.tag)
        revision = _revision_local(current)
        if revision is not None:
            group = REVISION_GROUPS[revision]
        deleted = group == "original"
        if namespace == WORD_NS and local == "t":
            if legacy and deleted == deleted_only:
                pieces.append(current.text or "")
            elif deleted_only and deleted:
                pieces.append(current.text or "")
            elif not deleted_only and reader.revisions == "all":
                pieces.append(current.text or "")
            elif not deleted_only and reader.revisions == "accepted" and not deleted:
                pieces.append(current.text or "")
            elif not deleted_only and reader.revisions == "original" and group != "accepted":
                pieces.append(current.text or "")
            return
        if namespace == WORD_NS and local == "delText":
            if legacy and deleted_only:
                pieces.append(current.text or "")
            elif deleted_only and deleted:
                pieces.append(current.text or "")
            elif not deleted_only and reader.revisions == "all" and deleted:
                pieces.append(current.text or "")
            elif not deleted_only and reader.revisions == "original" and deleted:
                pieces.append(current.text or "")
            return
        if namespace == WORD_NS and local in ("tab", "ptab"):
            visible = group != "original" if reader.revisions == "accepted" else group != "accepted"
            if legacy:
                visible = deleted == deleted_only
            elif deleted_only:
                visible = deleted
            if not deleted_only and reader.revisions == "all":
                visible = True
            if visible:
                pieces.append("\t")
            return
        if namespace == WORD_NS and local in ("br", "cr"):
            visible = group != "original" if reader.revisions == "accepted" else group != "accepted"
            if legacy:
                visible = deleted == deleted_only
            elif deleted_only:
                visible = deleted
            if not deleted_only and reader.revisions == "all":
                visible = True
            if visible:
                pieces.append("\n")
            return
        if namespace == WORD_NS and local == "noBreakHyphen":
            visible = group != "original" if reader.revisions == "accepted" else group != "accepted"
            if legacy:
                visible = deleted == deleted_only
            elif deleted_only:
                visible = deleted
            if not deleted_only and reader.revisions == "all":
                visible = True
            if visible:
                pieces.append("\u2011")
            return
        if namespace == WORD_NS and local == "softHyphen":
            visible = group != "original" if reader.revisions == "accepted" else group != "accepted"
            if legacy:
                visible = deleted == deleted_only
            elif deleted_only:
                visible = deleted
            if not deleted_only and reader.revisions == "all":
                visible = True
            if visible:
                pieces.append("\u00ad")
            return
        if namespace == WORD_NS and local in (
            "txbxContent", "drawing", "pict", "object", "pPr", "rPr",
        ):
            return
        if namespace == WORD_NS and local == "instrText":
            return
        if deleted_only and not legacy:
            children = selected_children(reader, current, "", filter_revisions=False)
        else:
            children = reader._selected_children(current, "")
        for child in children:
            visit(child, group)

    visit(node, inherited_group)
    return "".join(pieces)


def _revision_detail_text(reader, node, view):
    pieces = []

    def visit(current, group=None):
        namespace, local = _name(current.tag)
        revision = _revision_local(current)
        if revision is not None:
            group = REVISION_GROUPS[revision]
        deleted = group == "original"
        if namespace == WORD_NS and local in ("t", "delText"):
            visible = view == "all" or (view == "original" and group != "accepted")
            if local == "delText":
                visible = view == "all" or (view == "original" and deleted)
            if visible:
                pieces.append(current.text or "")
            return
        if namespace == WORD_NS and local in ("tab", "ptab"):
            if view == "all" or (view == "original" and group != "accepted"):
                pieces.append("\t")
            return
        if namespace == WORD_NS and local in ("br", "cr"):
            if view == "all" or (view == "original" and group != "accepted"):
                pieces.append("\n")
            return
        for child in selected_children(reader, current, "", filter_revisions=False):
            visit(child, group)

    visit(node)
    return "".join(pieces)


def revision_text(reader, paragraph, legacy=False):
    """Return revision details in XML order without changing the selected text."""
    result = []

    if legacy:
        def visit_legacy(node):
            for child in reader._selected_children(node, ""):
                local = _revision_local(child)
                if local is not None:
                    result.append({
                        "kind": REVISION_KINDS[local],
                        "text": reader._text(
                            child, deleted_only=local in ("del", "moveFrom")
                        ),
                        "path": reader._path(child),
                    })
                else:
                    visit_legacy(child)

        visit_legacy(paragraph)
        return result

    def visit(node):
        for child in selected_children(reader, node, "", filter_revisions=False):
            local = _revision_local(child)
            if local is not None:
                group = REVISION_GROUPS[local]
                view = "original" if group == "original" else "accepted"
                result.append({
                    "kind": REVISION_KINDS[local],
                    "text": _revision_detail_text(reader, child, view),
                    "path": reader._path(child),
                })
            else:
                visit(child)

    visit(paragraph)
    return result


def revision_issues(reader, root, part):
    """Report revision constructs whose effective state cannot be selected."""
    if reader.revisions is None:
        return
    for node in root.iter():
        namespace, local = _name(getattr(node, "tag", None))
        if namespace != WORD_NS:
            continue
        if local in PROPERTY_CHANGES:
            reader._issue(
                "revision_property_change_unresolved", part, reader._path(node),
                element=local, selected_view=reader.revisions,
            )
        if local == "trPr":
            for child in _children(node):
                row_revision = _revision_local(child)
                if row_revision in ("ins", "del"):
                    reader._issue(
                        "revision_row_flag_unresolved", part, reader._path(child),
                        revision=row_revision, selected_view=reader.revisions,
                    )
        inner = _revision_local(node)
        if inner is None:
            continue
        parent = node.getparent()
        while parent is not None:
            outer = _revision_local(parent)
            if outer is not None:
                if REVISION_GROUPS[outer] != REVISION_GROUPS[inner]:
                    reader._issue(
                        "revision_nested_view_conflict", part, reader._path(node),
                        outer_revision=outer, inner_revision=inner,
                        selected_view=reader.revisions,
                    )
                break
            parent = parent.getparent()
