"""Namespace-aware MC selection for the supported stored-object reading view."""
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
SUPPORTED = frozenset({W, 'http://purl.oclc.org/ooxml/wordprocessingml/main', 'http://schemas.openxmlformats.org/drawingml/2006/chart', 'urn:schemas-microsoft-com:vml',
                       'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
                       'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup'})
EXCLUDED = {'accepted': {'del', 'moveFrom'}, 'original': {'ins', 'moveTo'}, 'all': set()}
MAX_OBJECTS = 4096


class ChoiceElement(ET.Element):
    """A Choice retains only the resolved namespace URIs in its Requires value."""

    required_namespaces = ()


class CompatibilityTreeBuilder(ET.TreeBuilder):
    """Capture scoped namespace bindings during the existing guarded XML parse."""

    def __init__(self):
        super().__init__(element_factory=self._element)
        self.namespaces = {}

    @staticmethod
    def _element(tag, attrs):
        return ChoiceElement(tag, attrs) if tag == '{' + MC + '}Choice' else ET.Element(tag, attrs)

    def start_ns(self, prefix, uri):
        self.namespaces.setdefault(prefix or '', []).append(uri)

    def end_ns(self, prefix):
        values = self.namespaces[prefix or '']
        values.pop()
        if not values:
            del self.namespaces[prefix or '']

    def start(self, tag, attrs):
        node = super().start(tag, attrs)
        if isinstance(node, ChoiceElement):
            node.required_namespaces = tuple(
                self.namespaces.get(prefix, [None])[-1] for prefix in attrs.get('Requires', '').split())
        return node


def select_compatibility(root, part, issues, revisions="accepted"):
    """Select the first supported Choice, else Fallback; never guess unknown URIs."""
    selected = unresolved = 0

    def walk(parent):
        nonlocal selected, unresolved
        children = []
        for node in parent:
            if node.tag in {"{" + W + "}" + name for name in EXCLUDED[revisions]}:
                children.append(node)
                continue
            if node.tag != '{' + MC + '}AlternateContent':
                walk(node)
                children.append(node)
                continue
            choice = None
            fallback = None
            malformed = False
            seen_fallback = False
            for branch in node:
                if branch.tag == '{' + MC + '}Choice' and not seen_fallback:
                    namespaces = getattr(branch, 'required_namespaces', ())
                    if choice is None and namespaces and all(uri in SUPPORTED for uri in namespaces):
                        choice = branch
                elif branch.tag == '{' + MC + '}Fallback' and not seen_fallback:
                    fallback = branch
                    seen_fallback = True
                else:
                    malformed = True
            if malformed:
                choice = None
                fallback = None
            branch = choice if choice is not None else fallback
            if branch is None:
                unresolved += 1
                # Preserve the storage alternatives, never imply a selected current view.
                children.append(node)
            else:
                selected += 1
                walk(branch)
                children.extend(branch)
        parent[:] = children

    walk(root)
    if unresolved:
        issues.append({'stream': part, 'location': part, 'offset': None,
                       'code': 'alternate_content_unresolved', 'count': unresolved,
                       'message': 'Unsupported or malformed AlternateContent has no selected branch; stored alternatives are not a verified current view'})
    return {'part': part, 'selected_count': selected, 'unresolved_count': unresolved,
            'policy': 'first_supported_requires_else_fallback', 'application_rendering_verified': False}


def stored_objects(root, object_tag, revisions):
    """Traverse selected objects, retaining provenance for unresolved alternatives."""
    count = 0

    def walk(node, branches):
        nonlocal count
        if node.tag in {'{' + W + '}' + name for name in EXCLUDED[revisions]}:
            return
        if node.tag == object_tag:
            count += 1
            if count > MAX_OBJECTS:
                raise ValueError('Stored object count limit exceeded')
            yield node, branches
            return
        if node.tag == '{' + MC + '}AlternateContent':
            for index, child in enumerate(node):
                label = {'branch_index': index, 'branch': child.tag.rsplit('}', 1)[-1],
                         'requires': child.get('Requires'), 'selection': 'unresolved'}
                yield from walk(child, branches + [label])
            return
        for child in node:
            yield from walk(child, branches)

    yield from walk(root, [])
