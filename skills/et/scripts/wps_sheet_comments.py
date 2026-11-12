"""Bounded best-effort legacy SpreadsheetML comments; never VML layout rendering."""
from wps_ooxml import IssueLog, read_part, split_tag

MAX_COMMENTS = 4096
MAX_COMMENT_TEXT = 1024 * 1024
COMMENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml'
COMMENT_NAMESPACES = ('http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                      'http://purl.oclc.org/ooxml/spreadsheetml/main')


def _comment_text_value(text_value, node, report, sheet):
    try:
        value, _formatting = text_value(node, rich_issue=False,
                                        value_affects_cells=False)
        return value
    except ValueError as exc:
        report.add('comment_text_not_interpreted', 'Comment text retained as empty after a '
                   'bounded parse failure', sheet=sheet, reason=str(exc)[:120])
        return ''


def _split_root(root, report, sheet, part):
    """Return the comments tag builder for either spreadsheet main namespace."""
    namespace, local = split_tag(root.tag)
    if local != 'comments':
        report.add('comments_root_not_interpreted', element=str(root.tag)[:120],
                   sheet=sheet, part=part)
        return None
    if namespace not in COMMENT_NAMESPACES:
        report.add('comments_namespace_not_interpreted', element=str(root.tag)[:120],
                   sheet=sheet, part=part)
        return None
    return lambda value: '{' + namespace + '}' + value


def _read_comments_part(package, path, relationship_id, report, sheet, text_value, read_xml,
                        budget):
    """Return best-effort comments from one part; never raise for metadata faults."""
    try:
        root = read_xml(package, path)
    except (ValueError, KeyError) as exc:
        if 'DTD and entity' in str(exc) or 'Expat' in str(exc):
            raise
        report.add('comments_part_not_read', 'Attached comments were ignored after a bounded '
                   'read failure; cells are unaffected', sheet=sheet, part=path,
                   reason=str(exc)[:120])
        return []
    tag = _split_root(root, report, sheet, path)
    if tag is None:
        return []
    if (root.text or '').strip():
        report.add('comments_structure_not_interpreted', element='unwrapped root text',
                   sheet=sheet, part=path)
    authors_node = comment_list = None
    for child in root:
        if child.tag == tag('authors'):
            if authors_node is not None:
                report.add('comments_structure_not_interpreted', element='duplicate authors',
                           sheet=sheet, part=path)
                continue
            authors_node = child
        elif child.tag == tag('commentList'):
            if comment_list is not None:
                report.add('comments_structure_not_interpreted',
                           element='duplicate commentList', sheet=sheet, part=path)
                continue
            comment_list = child
        else:
            report.add('comments_structure_not_interpreted', element=str(child.tag)[:120],
                       sheet=sheet, part=path)
    if comment_list is None:
        report.add('comments_structure_not_interpreted', element='missing commentList',
                   sheet=sheet, part=path)
        return []
    for collection in (authors_node, comment_list):
        if collection is not None and (collection.text or '').strip():
            report.add('comments_structure_not_interpreted', element='unwrapped collection text',
                       sheet=sheet, part=path)
    authors = []
    for author in ([] if authors_node is None else authors_node):
        if (author.tail or '').strip():
            report.add('comments_structure_not_interpreted',
                       element='unwrapped author collection text', sheet=sheet, part=path)
        if author.tag != tag('author') or len(author):
            report.add('comments_structure_not_interpreted', element=str(author.tag)[:120],
                       sheet=sheet, part=path)
            continue
        if len(authors) < MAX_COMMENTS:
            authors.append(author.text or '')
    result = []
    seen = set()
    size = 0
    for index, node in enumerate(comment_list):
        if len(result) >= MAX_COMMENTS:
            report.add('comment_limit_reached', 'Further comments are not returned',
                       sheet=sheet, part=path, kept=MAX_COMMENTS)
            break
        if node.tag != tag('comment'):
            report.add('comments_structure_not_interpreted', element=str(node.tag)[:120],
                       sheet=sheet, part=path)
            continue
        ref = node.get('ref')
        try:
            from wps_ooxml_worksheet import coordinate
            coordinate(ref)
        except ValueError:
            report.add('comment_reference_not_interpreted', 'Comment without a bounded cell '
                       'reference is omitted', sheet=sheet, part=path, raw=str(ref)[:40])
            continue
        if ref in seen:
            report.add('duplicate_comment_skipped', 'Second saved comment for the same cell is '
                       'not returned', sheet=sheet, cell=ref)
            continue
        seen.add(ref)
        author_id = None
        raw_author_id = node.get('authorId')
        try:
            from wps_ooxml_worksheet import integer
            author_id = integer(raw_author_id, 4_294_967_295)
        except ValueError:
            author_id = None
        if author_id is None or author_id >= len(authors):
            report.add('comment_author_not_interpreted', 'Comment author index is missing or '
                       'outside the saved author list', sheet=sheet, cell=ref,
                       raw=str(raw_author_id)[:20])
            author_id = None
            author = ''
        else:
            author = authors[author_id]
        if (node.text or '').strip():
            report.add('comments_structure_not_interpreted', element='unwrapped comment text',
                       sheet=sheet, cell=ref)
        text_node = None
        for child in node:
            if (child.tail or '').strip():
                report.add('comments_structure_not_interpreted',
                           element='unwrapped comment text', sheet=sheet, cell=ref)
            if child.tag == tag('text'):
                text_node = child
                break
            report.add('comments_structure_not_interpreted', element=str(child.tag)[:120],
                       sheet=sheet, cell=ref)
        if text_node is None:
            report.add('comment_text_not_interpreted', 'Comment without a saved text container',
                       sheet=sheet, cell=ref)
            value = ''
        else:
            value = _comment_text_value(text_value, text_node, report, sheet)
        size += len(value)
        if size > MAX_COMMENT_TEXT:
            report.add('comment_text_limit_reached', 'Further comment text is not returned',
                       sheet=sheet, part=path, kept_bytes=MAX_COMMENT_TEXT)
            break
        result.append({'cell': ref, 'author_id': author_id, 'author': author,
                       'text': value,
                       'source': {'part': path,
                                  'path': '/comments/commentList/comment[' + str(index + 1) + ']',
                                  'relationship_id': relationship_id},
                       'kind': 'legacy_comment', 'layout_decoded': False,
                       'target_followed': False})
    return result


def _read_vml(package, path, budget, comments, report, sheet, reader):
    from wps_ooxml import relationships
    from wps_vml_notes import read_note_anchors
    for relationship, relation in relationships(package, path, budget, report=report,
                                                scope='vmlDrawing', sheet=sheet).items():
        if relation.get('external') or relation.get('kind') is None:
            continue
        report.add('vml_relationship_not_followed', sheet=sheet,
                   relationship_kind=str(relation.get('kind'))[:60],
                   relationship_id=str(relationship)[:120])
    try:
        read_note_anchors(package, path, budget, comments, report=report, reader=reader,
                          sheet=sheet)
    except (ValueError, KeyError) as exc:
        if 'DTD and entity' in str(exc) or 'Expat' in str(exc):
            raise
        report.add('vml_note_anchors_not_read', 'Comment text is retained without VML note '
                   'anchors', sheet=sheet, part=path, reason=str(exc)[:120])


def read_comments(package, sheet_part, conformance, namespace, types, budget, text_value,
                  used_parts, sheet_root=None, report=None, relations=None, reader=None,
                  sheet_name=None):
    """Read legacy note text and, best effort, VML anchors; never execute VML."""
    from wps_ooxml import fromstring
    if report is None:
        report = IssueLog()
    sheet = sheet_name if sheet_name is not None else sheet_part

    def read_xml(package_archive, name):
        return fromstring((read_part if reader is None else reader)(package_archive, name),
                          budget=budget)

    if relations is None:
        from wps_ooxml import relationships
        relations = relationships(package, sheet_part, budget, report=report,
                                  scope='worksheet', sheet=sheet)
    comments_relations = []
    vml_relations = []
    for relationship_id, relation in relations.items():
        kind = relation.get('kind')
        if kind == 'comments':
            comments_relations.append((relationship_id, relation))
        elif kind == 'vmlDrawing':
            vml_relations.append((relationship_id, relation))
    if comments_relations:
        if len(comments_relations) > 1:
            report.add('duplicate_worksheet_comments_relationships',
                       'Only the first saved comments part is read',
                       sheet=sheet, count=len(comments_relations))
        relationship_id, relation = comments_relations[0]
        path = relation.get('path')
        if path is None:
            report.add('comments_part_not_read',
                       'Comment relationship target is unresolved', sheet=sheet,
                       relationship_id=str(relationship_id)[:120])
            return []
        expected = types.get(path) if hasattr(types, 'get') else None
        if expected is not None and expected != COMMENT_TYPE:
            report.add('comments_content_type_mismatch', 'Comments part is read from its saved '
                       'structure despite an unexpected content type',
                       sheet=sheet, part=path, content_type=str(expected)[:120])
        if path in used_parts:
            report.add('aliased_worksheet_comments_part', 'The same comments part is attached '
                       'to more than one worksheet', sheet=sheet, part=path)
        else:
            used_parts.add(path)
        result = _read_comments_part(package, path, relationship_id, report, sheet,
                                     text_value, read_xml, budget)
    else:
        result = []
        if vml_relations:
            report.add('vml_notes_without_comments_not_decoded',
                       'VML note anchors were found without a saved comments part',
                       sheet=sheet, count=len(vml_relations))
    if result and vml_relations:
        relationship_id, relation = vml_relations[0]
        path = relation.get('path')
        if path is None:
            report.add('vml_part_not_read', 'VML relationship target is unresolved',
                       sheet=sheet, relationship_id=str(relationship_id)[:120])
        else:
            if len(vml_relations) > 1:
                report.add('duplicate_worksheet_vml_relationships',
                           'Only the first saved VML drawing part is read',
                           sheet=sheet, count=len(vml_relations))
            if path in used_parts:
                report.add('aliased_worksheet_vml_part', 'The same VML part is attached to '
                           'more than one worksheet', sheet=sheet, part=path)
            else:
                used_parts.add(path)
            _read_vml(package, path, budget, result, report, sheet, reader)
    return result
