"""Catalogue stored XML evidence separately from page text occurrences."""
from ofd_common import NS, ReadError, decode_text, is_ofd, local, q
from ofd_package import declare_payloads


def catalogue_structure(package, documents, issue):
    """Read normalized XML structure without activating actions or opaque payloads.

    The catalogue describes storage once per node. Page text_runs describe actual
    occurrences and remain a separate view. Structural coverage is not XSD or
    rendering/semantic certification.
    """
    original_parts = set(package.cache)

    for source, root in list(package.cache.items()):
        declare_payloads(package, source, root)
    if set(package.opaque_members) & set(package.cache):
        raise ReadError('conflicting XML part and opaque payload roles')

    for source, info in package.members.items():
        if info.is_dir() or source in package.cache or source in package.opaque_members:
            continue
        xml_named = source.lower().endswith(('.xml', '.xsd'))
        if not xml_named:
            # Only a bounded header of unclassified members is examined. Known
            # attachments, fonts, media and signature payloads never reach here.
            with package.archive.open(source) as handle:
                header = handle.read(64)
            if header.startswith((b'\xff\xfe', b'\xfe\xff')):
                prefix = header.decode('utf-16', errors='ignore').lstrip()
            else:
                prefix = header.decode('utf-8-sig', errors='ignore').lstrip()
            if not (prefix.startswith('<?xml') or NS in prefix):
                continue
        package.xml(source, require_ofd=False)

    # A payload may not simultaneously be interpreted as an OFD XML part.
    if set(package.opaque_members) & set(package.cache):
        raise ReadError('conflicting XML part and opaque payload roles')

    occurrence_index = {}
    for document_index, document in enumerate(documents):
        for page_index, page in enumerate(document['pages']):
            for run_index, run in enumerate(page['text_runs']):
                key = (run['source'], run['storage_node'])
                occurrence_index.setdefault(key, []).append(
                    dict(document=document_index, page=page_index, run=run_index))

    parts = []
    text_sources = []
    character_field_count = 0
    identifiers = []
    reference_fields = []
    validated_files = {}
    for reference in package.evidence:
        key = (reference['source'], reference['value'])
        validated_files.setdefault(key, set()).add(reference['target'])
    for source, root in package.cache.items():
        indexes = package.node_indexes[source]
        parents = {child: parent for parent in root.iter() for child in parent}
        nodes = []
        for node in root.iter():
            parent = parents.get(node)
            index = indexes[node]
            record = dict(index=index, parent=indexes[parent] if parent is not None else None,
                          tag=node.tag, attributes=dict(node.attrib), text=node.text or '', tail=node.tail or '')
            character_field_count += bool(record['text']) + bool(record['tail'])
            package.account(record)
            nodes.append(record)
            if 'ID' in node.attrib:
                ident = dict(source=source, storage_node=index, tag=node.tag, value=node.get('ID'))
                package.account(ident)
                identifiers.append(ident)
            fields = [(name, value, 'attribute') for name, value in node.attrib.items()
                      if name in ('BaseLoc', 'FileRef', 'ResourceID', 'Font', 'DrawParam', 'ColorSpace', 'TemplateID', 'URI')]
            if local(node.tag) in ('DocRoot', 'PublicRes', 'DocumentRes', 'PageRes', 'FileLoc', 'FontFile',
                                   'MediaFile', 'Annotations', 'Attachments', 'CustomTags', 'Extensions', 'Signatures', 'SchemaLoc'):
                fields.append((node.tag, node.text or '', 'text'))
            for field, value, kind in fields:
                targets = sorted(validated_files.get((source, value.strip()), ()))
                reference = dict(source=source, storage_node=index, field=field, value=value, kind=kind,
                                 matching_file_targets=targets,
                                 interpretation='matching_file_value_only' if targets else 'raw_context_required')
                package.account(reference)
                reference_fields.append(reference)
            if node.tag != q('TextCode'):
                continue
            ancestors = []
            ancestor = parent
            while ancestor is not None:
                ancestors.insert(0, indexes[ancestor])
                ancestor = parents.get(ancestor)
            raw = ''.join(node.itertext())
            decoded, problems = decode_text(raw)
            trusted = (parent is not None and parent.tag == q('TextObject')
                       and all(is_ofd(item.tag)
                               for item in parents_chain(node, parents)))
            if not trusted:
                problems.append('uninterpreted_textcode_context')
            if len(node):
                problems.append('nested_textcode')
            for problem in problems:
                issue(problem, source, 'storage_node=' + str(index))
            text_record = dict(source=source, storage_node=index, root_tag=root.tag,
                               object_id=parent.get('ID') if parent is not None else None,
                               raw_text=raw, text=decoded if trusted else None,
                               code_attributes=dict(node.attrib), ancestors=ancestors,
                               occurrences=occurrence_index.get((source, index), []),
                               interpretation='complete' if not problems else 'partial')
            package.account(text_record)
            text_sources.append(text_record)
        part = dict(source=source, sha256=package.xml_digests[source], nodes=nodes,
                    events=package.xml_events[source], active_route=source in original_parts,
                    namespace_mode=package.xml_modes[source])
        # Node records are already charged; charge all event strings and wrapper.
        package.account(dict(source=source, sha256=part['sha256'], events=part['events'],
                             active_route=part['active_route'], namespace_mode=part['namespace_mode']))
        parts.append(part)
        if source not in original_parts:
            issue('additional_xml_part_catalogued', source, root.tag)
        if part['namespace_mode'] == 'raw_names':
            issue('xml_namespace_unresolved', source, 'Raw XML names retained; not interpreted as OFD objects')

    payloads = [dict(source=source, role=role, bytes=package.members[source].file_size)
                for source, role in package.opaque_members.items()]
    package.account(payloads)
    structure = dict(status='complete', scope='normalized XML storage structure, not schema/semantic certification',
                     xml_parts=parts, opaque_payloads=payloads,
                     character_field_count=character_field_count,
                     identifiers=identifiers, reference_fields=reference_fields,
                     reference_validation='Supported file routes only; raw candidate fields do not imply resolved links',
                     namespace_resolution='partial' if 'raw_names' in package.xml_modes.values() else 'complete',
                     member_inventory='all ZIP members; non-XML payloads are opaque')
    unclassified = [name for name, info in package.members.items()
                    if not info.is_dir() and name not in package.cache and name not in package.opaque_members]
    structure['unclassified_members'] = unclassified
    package.account(unclassified)
    missing_indexes = sum(len(document['resource_gaps']) for document in documents)
    structure['missing_resource_index_count'] = missing_indexes
    package.account(dict(missing_resource_index_count=missing_indexes))
    if missing_indexes:
        structure['status'] = 'partial'
    if unclassified:
        structure['status'] = 'partial'
        for name in unclassified:
            issue('unclassified_payload', name, 'Member inventoried; payload format not interpreted')
    return structure, text_sources


def parents_chain(node, parents):
    """Yield the node and its actual ancestors without inferring context from local names."""
    while node is not None:
        yield node
        node = parents.get(node)
