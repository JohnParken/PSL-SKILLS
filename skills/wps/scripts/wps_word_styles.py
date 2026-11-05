"""Bounded paragraph/run property inheritance; no numbering or theme evaluation."""
from copy import deepcopy
from xml.etree.ElementTree import Element

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def merge_properties(nodes, tag):
    result = None
    for node in nodes:
        if node is None:
            continue
        if result is None:
            result = deepcopy(node)
            continue
        for child in node:
            prior = result.find(child.tag)
            if prior is None:
                result.append(deepcopy(child))
            else:
                prior.attrib.update(child.attrib)
                for nested in list(prior):
                    prior.remove(nested)
                prior.extend(deepcopy(list(child)))
    if result is not None:
        result.tag = W + tag
    return result


class WordStyles:
    def __init__(self, styles=None):
        self.styles = {}
        self.issues = []
        self.defaults = None if styles is None else styles.find(W + 'docDefaults')
        if styles is not None:
            for node in styles.findall(W + 'style'):
                identity = node.get(W + 'styleId')
                if not identity or identity in self.styles or len(self.styles) >= 4096:
                    if len(self.issues) < 100:
                        self.issues.append({'code': 'word_style_definition_unresolved', 'style': identity})
                    continue
                self.styles[identity] = node

    def chain(self, identity):
        result, visited = [], set()
        while identity:
            if identity in visited or len(visited) >= 64 or identity not in self.styles:
                if len(self.issues) < 100:
                    self.issues.append({'code': 'word_style_inheritance_unresolved', 'style': identity})
                break
            visited.add(identity)
            style = self.styles[identity]
            result.insert(0, style)
            parent = style.find(W + 'basedOn')
            identity = None if parent is None else parent.get(W + 'val')
        return result

    def paragraph_chain(self, paragraph):
        reference = paragraph.find(W + 'pPr/' + W + 'pStyle')
        identity = None if reference is None else reference.get(W + 'val')
        if identity is None:
            identity = next((key for key, value in self.styles.items()
                             if value.get(W + 'type') == 'paragraph' and value.get(W + 'default') in ('1', 'true')), None)
        return self.chain(identity)

    def properties(self, paragraph, run=None):
        chain = self.paragraph_chain(paragraph)
        if run is None:
            default = None if self.defaults is None else self.defaults.find(W + 'pPrDefault/' + W + 'pPr')
            return merge_properties([default] + [node.find(W + 'pPr') for node in chain] + [paragraph.find(W + 'pPr')], 'pPr')
        default = None if self.defaults is None else self.defaults.find(W + 'rPrDefault/' + W + 'rPr')
        reference = run.find(W + 'rPr/' + W + 'rStyle')
        character = [] if reference is None else self.chain(reference.get(W + 'val'))
        direct = run.find(W + 'rPr')
        result = merge_properties([default] + [node.find(W + 'rPr') for node in chain + character] + [direct], 'rPr')
        # ISO 29500 §17.7.3: style toggles invert; direct formatting is absolute.
        for toggle in ('b', 'i'):
            def enabled(node):
                return node is not None and node.get(W + 'val', 'true') not in ('false', '0', 'off')
            initial = None if default is None else default.find(W + toggle)
            state = enabled(initial)
            present = initial is not None
            for style in chain + character:
                value = style.find(W + 'rPr/' + W + toggle)
                present = present or value is not None
                if enabled(value):
                    state = not state
            value = None if direct is None else direct.find(W + toggle)
            if value is not None:
                present, state = True, enabled(value)
            if present:
                if result is None:
                    result = Element(W + 'rPr')
                prior = result.find(W + toggle)
                if prior is not None:
                    result.remove(prior)
                result.append(Element(W + toggle, {W + 'val': 'true' if state else 'false'}))
        return result

    def apply(self, document):
        for paragraph in document.iter(W + 'p'):
            properties = self.properties(paragraph)
            for run in paragraph.iter(W + 'r'):
                run_properties = self.properties(paragraph, run)
                old = run.find(W + 'rPr')
                if old is not None:
                    run.remove(old)
                if run_properties is not None:
                    run.insert(0, run_properties)
            old = paragraph.find(W + 'pPr')
            if old is not None:
                paragraph.remove(old)
            if properties is not None:
                paragraph.insert(0, properties)


def format_mapping_diagnostics(document):
    """Describe detected paragraph/run properties missing from the bounded mapper."""
    from wps_rtf_builder import FONT_MAP
    issues = []
    total = 0
    allowed = {
        'pPr': {'pStyle': {'val'}, 'jc': {'val'}, 'spacing': {'before', 'after', 'line', 'lineRule'},
                'ind': {'firstLine', 'firstLineChars', 'left', 'right', 'hanging'}, 'outlineLvl': {'val'}},
        'rPr': {'rStyle': {'val'}, 'rFonts': {'eastAsia', 'ascii'}, 'sz': {'val'}, 'b': {'val'}, 'i': {'val'},
                'u': {'val'}, 'color': {'val'}},
    }

    def record(path, prop, reason, attribute=None, value=None):
        nonlocal total
        total += 1
        if len(issues) < 128:
            issues.append({'code': 'source_format_not_mapped', 'path': path[:500], 'property': prop[:100],
                           'path_truncated': len(path) > 500, 'property_truncated': len(prop) > 100,
                           'reason': reason, 'attribute': attribute[:100] if attribute else None,
                           'attribute_truncated': bool(attribute and len(attribute) > 100),
                           'value': value[:200] if value else value, 'value_truncated': bool(value and len(value) > 200),
                           'basis': 'effective_properties_after_bounded_style_inheritance'})

    def visit(node, path):
        if node.tag in (W + 'del', W + 'moveFrom'):
            return
        if node.tag in (W + 'pPr', W + 'rPr'):
            kind = node.tag[len(W):]
            for prop in node:
                name = prop.tag[len(W):] if prop.tag.startswith(W) else prop.tag
                if name not in allowed[kind]:
                    record(path, name, 'unsupported_property')
                    continue
                if name == 'rFonts':
                    east = prop.get(W + 'eastAsia')
                    ascii_font = prop.get(W + 'ascii')
                    if east and ascii_font and east.strip().lower() != ascii_font.strip().lower():
                        record(path, name, 'separate_script_font_not_mapped', 'ascii', ascii_font)
                for attribute, value in prop.attrib.items():
                    key = attribute[len(W):] if attribute.startswith(W) else attribute
                    if key not in allowed[kind][name]:
                        record(path, name, 'unsupported_attribute', key, value)
                    elif name == 'rFonts' and value.strip().lower() not in FONT_MAP:
                        record(path, name, 'font_name_fallback', key, value)
                    elif name == 'spacing' and key == 'lineRule' and value not in ('auto', 'exact'):
                        record(path, name, 'unsupported_value', key, value)
                    elif name == 'jc' and value not in ('left', 'right', 'center', 'both', 'justify'):
                        record(path, name, 'unsupported_value', key, value)
                    elif name == 'outlineLvl' and (not value.isdigit() or not 0 <= int(value) <= 8):
                        record(path, name, 'unsupported_value', key, value)
                    elif name == 'u' and value not in ('single', 'none', '0', 'false'):
                        record(path, name, 'unsupported_value', key, value)
                    elif name == 'color' and value.upper() not in ('AUTO', '000000', 'FF0000', '0070C0', '0000FF'):
                        record(path, name, 'unsupported_value', key, value)
                    elif (name in ('spacing', 'ind', 'sz') and key != 'lineRule'
                          and (not value.isdigit() or (name == 'sz' and int(value) == 0))):
                        record(path, name, 'unsupported_numeric_value', key, value)
            return
        counts = {}
        for child in node:
            if not child.tag.startswith(W):
                continue
            name = child.tag[len(W):]
            counts[name] = counts.get(name, 0) + 1
            visit(child, path + '/' + name + '[' + str(counts[name]) + ']')

    visit(document, '/document')
    if total > len(issues):
        issues.append({'code': 'source_format_diagnostics_truncated', 'total': total, 'omitted': total - len(issues)})
    return issues
