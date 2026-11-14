"""Discover Word support parts by OPC relationships and declared content types."""

from tl_package import OFFICE_REL

WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
CONTENT_PREFIX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.'
SPECS = {
    'header': ('header', CONTENT_PREFIX + 'header+xml', 'hdr'),
    'footer': ('footer', CONTENT_PREFIX + 'footer+xml', 'ftr'),
    'footnote': ('footnotes', CONTENT_PREFIX + 'footnotes+xml', 'footnotes'),
    'endnote': ('endnotes', CONTENT_PREFIX + 'endnotes+xml', 'endnotes'),
    'comment': ('comments', CONTENT_PREFIX + 'comments+xml', 'comments'),
    'styles': ('styles', CONTENT_PREFIX + 'styles+xml', 'styles'),
    'numbering': ('numbering', CONTENT_PREFIX + 'numbering+xml', 'numbering'),
    'core': ('http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties',
             'application/vnd.openxmlformats-package.core-properties+xml', 'coreProperties'),
}
STORIES = ('header', 'footer', 'footnote', 'endnote', 'comment')


class PartCatalog:
    """Track discovery and XML failures without inventing conventional filenames."""

    def __init__(self, package, issue):
        self.package = package
        self.issue = issue
        self.parts = {}
        self.failed = set()
        self.orphans = set()

    def locate(self, kind):
        """Return declared parts; orphan content is read with an explicit issue."""
        if kind in self.parts:
            return self.parts[kind]
        relation, content_type, _ = SPECS[kind]
        source = '' if kind == 'core' else self.package.main
        relation_type = relation if kind == 'core' else OFFICE_REL + relation
        found = set()
        for rid, item in self.package.relations(source).items():
            if item['type'] != relation_type:
                continue
            target = item['target']
            if item['external']:
                self.issue('external_support_part_not_read', source, relationship_id=rid, kind=kind)
            elif target not in self.package.names:
                self.issue('support_part_missing', target, relationship_id=rid, kind=kind)
            elif self.package.types.get(target) != content_type:
                self.issue('support_part_content_type_mismatch', target, relationship_id=rid, kind=kind)
            else:
                found.add(target)
        for part in self.package.names:
            if self.package.types.get(part) == content_type and part not in found:
                self.orphans.add(part)
                self.issue('unreferenced_readable_part', part, kind=kind)
                found.add(part)
        result = sorted(found)
        if kind in ('styles', 'numbering', 'core') and len(result) > 1:
            self.issue('multiple_support_parts', source, kind=kind, parts=result)
        self.parts[kind] = result
        return result

    def xml(self, part, kind):
        """Isolate malformed optional XML, not unsafe OPC/DTD/ZIP failures."""
        try:
            root = self.package.xml(part)
        except ValueError as exc:
            if str(exc) != 'Malformed XML part':
                raise
            self.failed.add(part)
            self.issue('support_part_xml_unreadable', part, kind=kind)
            return None
        expected = SPECS[kind][2]
        namespace = ('http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
                     if kind == 'core' else WORD)
        if root.tag != '{' + namespace + '}' + expected:
            self.failed.add(part)
            self.issue('support_part_root_unexpected', part, kind=kind, expected=expected)
            return None
        return root

    def state(self, kind):
        """Report failed parts rather than claiming they were available."""
        parts = self.parts.get(kind, [])
        if not parts:
            return 'absent'
        failed = sum(part in self.failed for part in parts)
        return 'unreadable' if failed == len(parts) else 'partial' if failed else 'available'


def section_references(reader, section, part):
    """Associate explicit section references with their actual header/footer parts."""
    word = '{' + WORD + '}'
    rel_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    references = []
    for kind in ('header', 'footer'):
        for node in section.findall(word + kind + 'Reference'):
            rid = node.get(rel_ns + 'id')
            relation = reader.package.related(part, rid)
            item = {'kind': kind, 'variant': node.get(word + 'type'), 'relationship_id': rid}
            if (relation is None or relation['external'] or relation['type'] != OFFICE_REL + kind
                    or relation['target'] not in reader.package.names):
                reader._issue('section_story_reference_unavailable', part, reader._path(node), kind=kind, relationship_id=rid)
                item['available'] = False
            else:
                item.update(target_part=relation['target'], available=True)
            references.append(item)
    return references
