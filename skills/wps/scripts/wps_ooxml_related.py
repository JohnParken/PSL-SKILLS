"""Independent OOXML related-story recovery; shared package guards remain strict."""
import re
import wps_ooxml as core


def relationship_name(part):
    """Return the OPC relationship part belonging to a resolved source part."""
    folder, _, filename = part.rpartition('/')
    return (folder + '/' if folder else '') + '_rels/' + filename + '.rels'


def recoverable_content_error(exc):
    """Only recover local content faults, never resource or XML security failures."""
    if isinstance(exc, core.XmlBudgetExceeded):
        return False
    message = str(exc)
    return message.startswith((
        'Invalid XML:', 'Missing related part:', 'Missing/wrong related-part content type:',
        'Wrong related-part XML root:', 'Unexpected related text entry:',
        'Invalid related text definition ID:', 'Duplicate related text definition ID:',
        'Missing/non-normal related text definition:',
    ))


def read_related(package, document, revisions, budget, record_omissions, issues, table_collector=None,
                  visibility=None, textbox_collector=None, main_part="word/document.xml"):
    """Follow main-part relationships and explicit IDs; never enumerate filenames."""
    rel_name = relationship_name(main_part)
    relationships = {}
    if rel_name in package.namelist():
        root = core._word_part(package, rel_name, budget)
        if root.tag != '{' + core.RELATIONSHIPS + '}Relationships':
            raise ValueError('Invalid document relationships root')
        for item in root:
            if item.tag != '{' + core.RELATIONSHIPS + '}Relationship':
                raise ValueError('Invalid document relationship element')
            identity = item.get('Id')
            if not identity or identity in relationships:
                raise ValueError('Missing/duplicate document relationship ID')
            relationships[identity] = item
    types = core.fromstring(core.read_part(package, '[Content_Types].xml'), budget=budget)
    declarations = {}
    for item in types:
        if item.tag == '{' + core.CONTENT_TYPES + '}Override':
            name = item.get('PartName')
            if name in declarations:
                raise ValueError('Duplicate part content-type declaration')
            declarations[name] = item.get('ContentType')
    header_owners = {}
    for section_index, section in enumerate(document.iter(core._tag('sectPr'))):
        seen = set()
        for child in section:
            if child.tag not in (core._tag('headerReference'), core._tag('footerReference')):
                continue
            signature = (child.tag, child.get(core._tag('type')))
            if signature in seen:
                raise ValueError('Duplicate header/footer type within section')
            seen.add(signature)
            header_owners[id(child)] = section_index
    requests = {}
    note_requests = {}
    reference_count = 0
    for index, node in enumerate(core._visible_elements(document, revisions)):
        local = node.tag.rsplit('}', 1)[-1]
        if local not in ('headerReference', 'footerReference', 'footnoteReference', 'endnoteReference', 'commentReference'):
            continue
        reference_count += 1
        if reference_count > core.MAX_RELATED_REFERENCES:
            raise ValueError('Related reference count limit exceeded')
        reference = {'source_part': main_part, 'element_index': index, 'kind': local,
                     'page': None, 'cp': None}
        if local in ('headerReference', 'footerReference'):
            if id(node) not in header_owners:
                raise ValueError('Header/footer reference outside section properties')
            reference['section_index'] = header_owners[id(node)]
            kind = 'header' if local == 'headerReference' else 'footer'
            identity = node.get('{' + core.R + '}id')
            variant = node.get(core._tag('type'))
            if variant not in ('default', 'first', 'even'):
                raise ValueError('Invalid header/footer reference type')
            reference.update(relationship_id=identity, variant=variant)
            key = (kind, identity)
            requests.setdefault(key, []).append(reference)
        else:
            kind = {'footnoteReference': 'footnotes', 'endnoteReference': 'endnotes', 'commentReference': 'comments'}[local]
            value = node.get(core._tag('id'))
            if value is None or not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 2147483647:
                raise ValueError('Invalid related text reference ID')
            identity = str(int(value))
            reference['id'] = identity
            note_requests.setdefault(kind, {}).setdefault(identity, []).append(reference)
    for kind in note_requests:
        matches = [(identity, item) for identity, item in relationships.items() if item.get('Type') == core.R + '/' + kind]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous ' + kind + ' relationship')
        requests[(kind, matches[0][0])] = []
    stories = []
    loaded_parts = set()
    for (kind, identity), references in requests.items():
        if len(stories) >= core.MAX_RELATED_REGIONS:
            raise ValueError('Related region count limit exceeded')
        relationship = relationships.get(identity)
        if relationship is None or relationship.get('Type') != core.R + '/' + kind:
            raise ValueError('Missing/wrong related-part relationship: ' + str(identity))
        if relationship.get('TargetMode', 'Internal') != 'Internal':
            raise ValueError('unsupported external related-part relationship')
        part = core.resolve_internal_target(relationship.get('Target'), main_part)
        try:
            if part not in package.namelist():
                raise ValueError('Missing related part: ' + part)
            if (kind, part) in loaded_parts:
                raise ValueError('unsupported aliases to related part: ' + part)
            loaded_parts.add((kind, part))
            expected_root, expected_type = core.RELATED_TYPES[kind]
            if declarations.get('/' + part) != expected_type:
                raise ValueError('Missing/wrong related-part content type: ' + part)
            root = core._word_part(package, part, budget)
            if root.tag != core._tag(expected_root):
                raise ValueError('Wrong related-part XML root: ' + part)
            entries = [(None, root, references)]
            if kind in note_requests:
                singular = {'footnotes': 'footnote', 'endnotes': 'endnote', 'comments': 'comment'}[kind]
                definitions = {}
                for child in root:
                    if child.tag != core._tag(singular):
                        raise ValueError('Unexpected related text entry: ' + part)
                    value = child.get(core._tag('id'))
                    if value is None or not re.fullmatch(r'-?[0-9]{1,10}', value) or not -2147483648 <= int(value) <= 2147483647:
                        raise ValueError('Invalid related text definition ID: ' + part)
                    key = str(int(value))
                    if key in definitions:
                        raise ValueError('Duplicate related text definition ID: ' + part)
                    if len(definitions) >= core.MAX_RELATED_REGIONS:
                        raise ValueError('Related definition count limit exceeded')
                    definitions[key] = child
                entries = []
                for key, refs in note_requests[kind].items():
                    if key not in definitions or definitions[key].get(core._tag('type'), 'normal') != 'normal':
                        raise ValueError('Missing/non-normal related text definition: ' + part + ' id=' + key)
                    entries.append((key, definitions[key], refs))
            for key, entry, refs in entries:
                if len(stories) >= core.MAX_RELATED_REGIONS:
                    raise ValueError('Related region count limit exceeded')
                first_issue = len(issues)
                visibility_issue_start = len(visibility.issues) if visibility is not None else 0
                entry_path = "/" + kind + "[" + str(key) + "]"
                if visibility is not None:
                    visibility.apply(entry, part, entry_path)
                record_omissions(entry, part)
                if textbox_collector is not None:
                    textbox_collector.collect(entry, part, entry_path)
                nested = sorted({node.tag.rsplit('}', 1)[-1] for node in core._visible_elements(entry, revisions)
                                 if node.tag in (core._tag('footnoteReference'), core._tag('endnoteReference'),
                                                 core._tag('commentReference'), core._tag('headerReference'), core._tag('footerReference'))})
                if nested:
                    issues.append({'stream': part, 'offset': None, 'code': 'related_nested_reference_not_followed',
                                   'elements': nested, 'message': 'Only explicit main-document references are followed; nested references not resolved'})
                story_issues = issues[first_issue:]
                if visibility is not None:
                    story_issues += visibility.issues[visibility_issue_start:]
                paragraphs = core._blocks(entry, revisions)
                if table_collector is not None:
                    table_issue_start = len(table_collector.issues)
                    table_collector.collect(entry, part, '/' + kind + '[' + str(key) + ']')
                    story_issues += table_collector.issues[table_issue_start:]
                stories.append({'type': kind, 'source': {'part': part, 'id': key, 'relationship_id': identity},
                                'text': '\n'.join(paragraphs), 'paragraphs': paragraphs,
                                'references': refs, 'anchor_status': 'relationship_or_id_verified',
                                'page_mapping': 'unknown', 'cp_range': None,
                                'status': 'partial' if story_issues else 'ok', 'issues': story_issues})
        except ValueError as exc:
            if not recoverable_content_error(exc):
                exc.wps_part = part
                raise
            issue = {'stream': part, 'location': part, 'offset': None,
                     'code': 'related_content_unreadable', 'message': str(exc)}
            issues.append(issue)
            stories.append({'type': kind, 'source': {'part': part, 'relationship_id': identity},
                            'text': None, 'paragraphs': [], 'references': references,
                            'anchor_status': 'unverified', 'page_mapping': 'unknown',
                            'cp_range': None, 'status': 'partial', 'issues': [issue]})
    return stories
