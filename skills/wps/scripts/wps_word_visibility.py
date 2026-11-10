"""Bounded stored Word run visibility; never an authorization control."""
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MAX_STYLES = 4096
MAX_CHAIN = 64
MAX_RUNS = 100000


def tag(name):
    return '{' + W + '}' + name


class VisibilityMetadataError(ValueError):
    """Unresolved visibility semantics, never a resource or XML structure error."""


class Visibility:
    def __init__(self, policy, styles=None, revisions="accepted", style_part=None):
        if policy not in ('include', 'exclude', 'annotate'):
            raise ValueError('unsupported hidden text policy')
        self.revisions = revisions
        self.policy = policy
        self.styles = {}
        self.records = []
        self.issues = []
        self.count = 0
        self.style_metadata_unknown = False
        style_nodes = [] if styles is None else styles.findall(tag('style'))
        # Count every definition before recovering invalid/duplicate identities.
        if len(style_nodes) > MAX_STYLES:
            raise ValueError('Style count limit exceeded')
        for index, node in enumerate(style_nodes):
            identity = node.get(tag('styleId'))
            if not identity or identity in self.styles:
                self._style_problem('Invalid/duplicate style ID', style_part, index)
                continue
            self.styles[identity] = node
        self.default_styles = {}
        for index, node in enumerate(style_nodes):
            identity = node.get(tag('styleId'))
            if self.styles.get(identity) is not node:
                continue
            if node.get(tag('default')) in ('1', 'true', 'on'):
                kind = node.get(tag('type'))
                if kind in self.default_styles:
                    self._style_problem('Duplicate default style', style_part, index)
                    continue
                self.default_styles[kind] = identity
        self.defaults = styles.find(tag('docDefaults') + '/' + tag('rPrDefault') + '/' + tag('rPr')) if styles is not None else None

    def _style_problem(self, reason, part, index):
        if self.policy != 'include':
            raise VisibilityMetadataError(reason)
        self.style_metadata_unknown = True
        self.issues.append({'stream': part, 'location': '/styles/style[' + str(index) + ']',
                            'code': 'visibility_metadata_unresolved', 'reason': reason,
                            'message': 'Invalid style visibility metadata; stored text retained without a visibility claim'})

    def properties(self, props):
        result = {}
        if props is not None:
            for name in ('vanish', 'webHidden'):
                items = props.findall(tag(name))
                if len(items) > 1:
                    raise VisibilityMetadataError('Duplicate run visibility property')
                if items:
                    value = items[0].get(tag('val'), 'true')
                    if value not in ('true', 'false', '1', '0', 'on', 'off'):
                        raise VisibilityMetadataError('Invalid run visibility value')
                    result[name] = value in ('true', '1', 'on')
        return result

    def chain(self, identity):
        seen = set()
        nodes = []
        while identity:
            if identity in seen:
                raise VisibilityMetadataError('Style inheritance cycle')
            if len(seen) >= MAX_CHAIN:
                raise ValueError('Style chain limit exceeded')
            seen.add(identity)
            if identity not in self.styles:
                return {}, False
            node = self.styles[identity]
            nodes.append(node)
            parent = node.find(tag('basedOn'))
            identity = parent.get(tag('val')) if parent is not None else None
        result = {}
        # Toggle semantics in inherited styles require a full cascade. Treat true
        # style toggles as unknown rather than applying direct-property semantics.
        reliable = True
        for node in reversed(nodes):
            values = self.properties(node.find(tag('rPr')))
            if any(values.values()):
                reliable = False
            result.update(values)
        return result, reliable

    def run_state(self, props, paragraph_style):
        """Resolve one run; callers may recover only VisibilityMetadataError."""
        values = self.properties(self.defaults)
        reliable = not self.style_metadata_unknown
        basis = ['document-defaults']
        for identity in (paragraph_style or self.default_styles.get('paragraph'), self.default_styles.get('character')):
            if identity:
                inherited, known = self.chain(identity)
                values.update(inherited)
                reliable &= known
                basis.append('paragraph-style:' + identity)
        style = props.find(tag('rStyle')) if props is not None else None
        if style is not None:
            identity = style.get(tag('val'))
            inherited, known = self.chain(identity)
            values.update(inherited)
            reliable &= known
            basis.append('run-style:' + str(identity))
        direct = self.properties(props)
        values.update(direct)
        if direct:
            basis.append('direct')
        # vanish is general hidden text; webHidden is view-dependent.
        vanish_known = reliable or 'vanish' in direct
        vanish = direct.get('vanish', values.get('vanish', False))
        web_hidden = values.get('webHidden', False)
        if self.style_metadata_unknown:
            basis.append('invalid-style-metadata')
            state = 'unknown'
        elif vanish_known and vanish:
            state = 'hidden'
        elif web_hidden:
            state = 'unknown'
            basis.append('web-view-only')
        else:
            state = 'visible' if reliable else 'unknown'
        return state, basis

    def apply(self, root, part, base='/'):
        def walk(node, path, paragraph_style=None, revision_excluded=False):
            excluded_tags = {'accepted': ('del', 'moveFrom'), 'original': ('ins', 'moveTo'), 'all': ()}[self.revisions]
            revision_excluded = revision_excluded or node.tag in tuple(tag(x) for x in excluded_tags)
            if node.tag == tag('p'):
                style = node.find(tag('pPr') + '/' + tag('pStyle'))
                paragraph_style = style.get(tag('val')) if style is not None else None
            if node.tag == tag('r'):
                self.count += 1
                if self.count > MAX_RUNS:
                    raise ValueError('Visibility run limit exceeded')
                reason = None
                try:
                    state, basis = self.run_state(node.find(tag('rPr')), paragraph_style)
                except VisibilityMetadataError as exc:
                    # Include never filters text. A semantic metadata failure must
                    # not discard safe text; explicit filtering/annotation stays
                    # strict and resource/structure failures never enter here.
                    if self.policy != 'include':
                        raise
                    state, basis, reason = 'unknown', ['invalid-visibility-metadata'], str(exc)
                excluded = self.policy == 'exclude' and state == 'hidden'
                chars = sum(len(x.text or '') for x in node.iter() if x.tag in (tag('t'), tag('delText')))
                if state != 'visible' or self.policy == 'annotate':
                    self.records.append({'source': {'part': part, 'path': path}, 'state': state,
                                         'action': 'revision_excluded' if revision_excluded else ('excluded' if excluded else 'retained'),
                                         'character_count': chars, 'basis': basis})
                if state == 'unknown':
                    issue = {'stream': part, 'location': path, 'code': 'visibility_unknown',
                             'message': 'Style visibility cascade unresolved; stored text retained subject to the requested revision view'}
                    if reason is not None:
                        issue['reason'] = reason
                    self.issues.append(issue)
                if excluded:
                    for child in node.iter():
                        if child.tag in (tag('t'), tag('delText'), tag('instrText')):
                            child.text = None
                        if child.tag in (tag('tab'), tag('br'), tag('cr'), tag('noBreakHyphen'), tag('softHyphen')):
                            child.tag = tag('excludedControl')
            for index, child in enumerate(node):
                walk(child, path + '/' + child.tag.rsplit('}', 1)[-1] + '[' + str(index) + ']', paragraph_style, revision_excluded)
        walk(root, base)
