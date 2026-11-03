"""Bounded legacy SpreadsheetML comment text, never VML/layout or threaded comments."""
MAX_COMMENTS = 4096
MAX_COMMENT_TEXT = 1024 * 1024
COMMENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml'


def read_comments(package, sheet_part, conformance, namespace, types, budget, text_value, used_parts, sheet_root=None):
    # Runtime import avoids a module initialization cycle and reuses one OPC guard.
    from wps_ooxml_sheet import relationships, RELS, need, validate_namespace, coordinate, integer
    from wps_ooxml import read_part, fromstring
    relations = relationships(package, sheet_part, budget)
    paths = []
    vml_paths = []
    for relationship_id, (kind, path) in relations.items():
        if kind == RELS[conformance] + 'vmlDrawing':
            need(conformance == 'transitional', 'Strict VML unsupported')
            vml_paths.append((relationship_id, path))
            continue
        need(kind == RELS[conformance] + 'comments', 'Unsupported worksheet relationship ' + str(kind))
        need(types.get(path) == COMMENT_TYPE, 'Comment relationship content type mismatch')
        paths.append((relationship_id, path))
    need(len(paths) <= 1, 'Duplicate worksheet comments relationships')
    need(len(vml_paths) <= 1, 'Duplicate VML relationships')
    drawings = [] if sheet_root is None else sheet_root.findall('{' + namespace + '}legacyDrawing')
    need(len(drawings) <= 1, 'Duplicate legacyDrawing')
    if not vml_paths:
        need(not drawings, 'Missing VML relationship')
    else:
        need(len(drawings) == 1 and not len(drawings[0]) and drawings[0].attrib == {'{' + RELS[conformance][:-1] + '}id': vml_paths[0][0]}, 'Missing/mismatched legacyDrawing relationship')
    if not paths:
        need(not vml_paths, 'VML without comments unsupported')
        return []
    relationship_id, path = paths[0]
    need(path not in used_parts, 'Aliased worksheet comments part')
    used_parts.add(path)
    need(not relationships(package, path, budget), 'Comment relationships unsupported')
    root = fromstring(read_part(package, path), budget=budget)
    tag = lambda value: '{' + namespace + '}' + value
    need(root.tag == tag('comments'), 'Invalid comments root')
    validate_namespace(root, namespace)
    need(not root.attrib and [node.tag for node in root] == [tag('authors'), tag('commentList')], 'Unsupported comments structure')
    authors_node, comments_node = root
    for collection in (root, authors_node, comments_node):
        need(not (collection.text or '').strip() and all(not (child.tail or '').strip() for child in collection), 'Unexpected comments collection text')
    need(not authors_node.attrib and not comments_node.attrib, 'Unsupported comments collection attributes')
    need(len(authors_node) <= MAX_COMMENTS and len(comments_node) <= MAX_COMMENTS, 'Comment count limit exceeded')
    authors = []
    size = 0
    for author in authors_node:
        need(author.tag == tag('author') and not len(author) and not author.attrib, 'Unsupported comment author')
        value = author.text or ''
        need(len(value) <= 32767, 'Comment author text limit exceeded')
        size += len(value)
        need(size <= MAX_COMMENT_TEXT, 'Comment text limit exceeded')
        authors.append(value)
    result = []
    seen = set()
    for index, node in enumerate(comments_node):
        need(node.tag == tag('comment') and set(node.attrib) <= {'ref', 'authorId'}, 'Unsupported comment element or attribute')
        need(not (node.text or '').strip() and all(not (child.tail or '').strip() for child in node), 'Unexpected comment raw text')
        ref = node.get('ref')
        coordinate(ref)
        need(ref not in seen, 'Duplicate comment cell')
        seen.add(ref)
        author_id = integer(node.get('authorId'), MAX_COMMENTS)
        need(author_id < len(authors), 'Comment author index outside collection')
        need(len(node) == 1 and node[0].tag == tag('text') and not node[0].attrib, 'Missing or unsupported comment text')
        value = text_value(node[0])
        size += len(value)
        need(size <= MAX_COMMENT_TEXT, 'Comment text limit exceeded')
        result.append({'cell': ref, 'author_id': author_id, 'author': authors[author_id],
                       'text': value, 'source': {'part': path, 'path': '/comments/commentList/comment[' + str(index + 1) + ']', 'relationship_id': relationship_id},
                       'kind': 'legacy_comment', 'layout_decoded': False, 'target_followed': False})
    if vml_paths:
        from wps_vml_notes import read_note_anchors, VML_TYPE
        vml_id, vml_path = vml_paths[0]
        need(vml_path not in used_parts, 'Aliased VML part')
        used_parts.add(vml_path)
        actual_type = types.get(vml_path)
        if actual_type is None:
            from wps_ooxml import CONTENT_TYPES
            content_root = fromstring(read_part(package, '[Content_Types].xml'), budget=budget)
            extension = vml_path.rsplit('.', 1)[-1]
            declarations = [item.get('ContentType') for item in content_root if item.tag == '{' + CONTENT_TYPES + '}Default' and item.get('Extension') == extension]
            need(len(declarations) == 1, 'Missing/duplicate VML content type')
            actual_type = declarations[0]
        need(actual_type == VML_TYPE, 'VML content type mismatch')
        need(not relationships(package, vml_path, budget), 'VML relationships unsupported')
        read_note_anchors(package, vml_path, budget, result)
    return result
