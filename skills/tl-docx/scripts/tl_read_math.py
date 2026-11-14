"""Readable OMML expressions, not a complete LaTeX or layout converter."""
MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def math_expression(node):
    """Preserve common mathematical relationships and report unknown semantics."""
    issues = []

    def local(element):
        tag = element.tag if isinstance(element.tag, str) else ''
        return tag.removeprefix('{' + MATH_NS + '}') if tag.startswith('{' + MATH_NS + '}') else ''

    def diagnostic(code, element):
        value = {'code': code, 'element': local(element) or str(element.tag)}
        if value not in issues:
            issues.append(value)

    def children(element):
        return [child for child in element if isinstance(child.tag, str)]

    def part(element, name, optional=False):
        matches = [child for child in children(element) if local(child) == name]
        if len(matches) != 1:
            if not optional or matches:
                diagnostic('math_structure_incomplete', element)
            return '?'
        return render(matches[0])

    def prop(element, group, name, default):
        found = element.find('{' + MATH_NS + '}' + group)
        if found is None:
            return default
        item = found.find('{' + MATH_NS + '}' + name)
        return default if item is None else item.get('{' + MATH_NS + '}val', default)

    def render(element):
        name = local(element)
        expected = {
            'f': {'num', 'den'}, 'sSub': {'e', 'sub'}, 'sSup': {'e', 'sup'},
            'sSubSup': {'e', 'sub', 'sup'}, 'rad': {'deg', 'e'}, 'func': {'fName', 'e'},
            'limLow': {'e', 'lim'}, 'limUpp': {'e', 'lim'}, 'nary': {'sub', 'sup', 'e'},
            'd': {'e'}, 'eqArr': {'e'}, 'acc': {'e'}, 'bar': {'e'}, 'groupChr': {'e'},
        }.get(name)
        if expected is not None:
            for child in children(element):
                child_name = local(child)
                if child_name not in expected and child_name != name + 'Pr':
                    diagnostic('math_structure_unsupported', child)
        if name == 't':
            return element.text or ''
        if name.endswith('Pr') or element.tag == '{' + WORD_NS + '}rPr':
            return ''
        if name in {'oMath', 'oMathPara', 'r', 'e', 'num', 'den', 'sub', 'sup', 'deg', 'fName', 'lim'}:
            return ''.join(render(child) for child in children(element))
        if name == 'f':
            if prop(element, 'fPr', 'type', 'bar') not in ('bar', 'skw', 'lin'):
                diagnostic('math_notation_approximate', element)
            return '(' + part(element, 'num') + ')/(' + part(element, 'den') + ')'
        if name in {'sSub', 'sSup', 'sSubSup'}:
            value = '(' + part(element, 'e') + ')'
            if name in {'sSub', 'sSubSup'}:
                value += '_(' + part(element, 'sub') + ')'
            if name in {'sSup', 'sSubSup'}:
                value += '^(' + part(element, 'sup') + ')'
            return value
        if name == 'rad':
            base = part(element, 'e')
            degree = part(element, 'deg', optional=True)
            hidden = prop(element, 'radPr', 'degHide', '0') in ('1', 'true', 'on')
            return 'sqrt(' + base + ')' if hidden or degree in ('', '?', '2') else 'root(' + degree + ', ' + base + ')'
        if name == 'func':
            return part(element, 'fName') + '(' + part(element, 'e') + ')'
        if name in {'limLow', 'limUpp'}:
            return '(' + part(element, 'e') + ')' + ('_(' if name == 'limLow' else '^(') + part(element, 'lim') + ')'
        if name == 'nary':
            operator = prop(element, 'naryPr', 'chr', '∫')
            lower, upper = part(element, 'sub', optional=True), part(element, 'sup', optional=True)
            return operator + ('_(' + lower + ')' if lower not in ('', '?') else '') + (
                '^(' + upper + ')' if upper not in ('', '?') else '') + '(' + part(element, 'e') + ')'
        if name == 'd':
            start, end = prop(element, 'dPr', 'begChr', '('), prop(element, 'dPr', 'endChr', ')')
            separator = prop(element, 'dPr', 'sepChr', '|')
            return start + separator.join(render(child) for child in children(element) if local(child) == 'e') + end
        if name == 'eqArr':
            return 'equations[' + '; '.join(render(child) for child in children(element) if local(child) == 'e') + ']'
        if name in {'acc', 'bar', 'groupChr'}:
            diagnostic('math_notation_approximate', element)
            mark = prop(element, name + 'Pr', 'chr', name)
            return mark + '(' + part(element, 'e') + ')'
        diagnostic('math_structure_unsupported', element)
        content = ''.join(render(child) for child in children(element))
        return '[' + (name or 'unknown') + ': ' + content + ']'

    return {'text': render(node), 'issues': issues}
