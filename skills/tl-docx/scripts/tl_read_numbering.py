"""Bounded stored list-label reconstruction, independent of Word rendering."""

import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
SUPPORTED = {'decimal', 'decimalZero', 'upperLetter', 'lowerLetter', 'upperRoman', 'lowerRoman', 'bullet', 'none'}


def value(node, tag, default=None):
    """Read a unique Word value; ambiguous definitions cannot yield labels."""
    items = node.findall(W + tag)
    if len(items) > 1:
        raise ValueError('numbering_duplicate_property')
    return items[0].get(W + 'val', default) if items else default


def integer(text, low, high):
    """Reject malformed or unbounded integer fields without coercing them."""
    if not isinstance(text, str) or not re.fullmatch(r'-?[0-9]{1,10}', text) or not low <= int(text) <= high:
        raise ValueError('numbering_invalid_value')
    return int(text)


def indexed(root, tag, attribute):
    """Index definitions, rejecting duplicate IDs instead of choosing one."""
    result = {}
    if root is not None:
        for node in root.findall(W + tag):
            key = node.get(W + attribute)
            if key is None or key in result:
                raise ValueError('numbering_duplicate_or_missing_id')
            result[key] = node
    return result


def level_definition(node):
    """Parse supported level settings while retaining explicit unsupported flags."""
    level = integer(node.get(W + 'ilvl'), 0, 8)
    restart = value(node, 'lvlRestart')
    restart = level if restart is None else integer(restart, 0, level)
    fmt = value(node, 'numFmt', 'decimal')
    if fmt not in SUPPORTED or node.find(W + 'lvlPicBulletId') is not None:
        raise ValueError('numbering_format_unsupported')
    pattern = value(node, 'lvlText', '')
    if len(pattern) > 4096:
        raise ValueError('numbering_label_limit')
    return level, {'start': integer(value(node, 'start', '0'), 0, 2147483647),
                   'restart': restart, 'format': fmt, 'text': pattern,
                   'style': value(node, 'pStyle'),
                   'legal': node.find(W + 'isLgl') is not None and value(node, 'isLgl', '1') not in ('0', 'false', 'off')}


def formatted(number, fmt):
    """Format a counter for the explicitly supported high-value format subset."""
    if fmt in ('decimal', 'decimalZero'):
        return str(number).zfill(2 if fmt == 'decimalZero' else 1)
    if not 1 <= number <= 3999:
        raise ValueError('numbering_counter_range_unsupported')
    if fmt in ('upperLetter', 'lowerLetter'):
        # Word repeats the same letter after Z: AA, BB, ... rather than Excel AB.
        letter = chr(ord('A') + (number - 1) % 26) * ((number - 1) // 26 + 1)
        return letter.lower() if fmt == 'lowerLetter' else letter
    result = ''
    for amount, text in ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'),
                         (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')):
        count, number = divmod(number, amount)
        result += text * count
    if fmt not in ('upperRoman', 'lowerRoman'):
        raise ValueError('numbering_placeholder_format_unsupported')
    return result.lower() if fmt == 'lowerRoman' else result


class NumberingReader:
    """Resolve native list references in stored document order, per story part."""

    def __init__(self, package, catalog=None):
        self.styles = {}
        self.default_style = None
        self.abstracts = {}
        self.instances = {}
        self.error = None
        self.cache = {}
        self.counters = {}
        roots = {}
        for kind in ('numbering', 'styles'):
            if catalog is None:
                part = 'word/' + kind + '.xml'
                roots[kind] = package.xml(part) if part in package.names else None
            else:
                parts = catalog.locate(kind)
                roots[kind] = catalog.xml(parts[0], kind) if len(parts) == 1 else None
                if len(parts) > 1:
                    self.error = 'numbering_multiple_support_parts'
        try:
            root = roots['numbering']
            self.abstracts = indexed(root, 'abstractNum', 'abstractNumId')
            self.instances = indexed(root, 'num', 'numId')
            root = roots['styles']
            self.styles = indexed(root, 'style', 'styleId')
            for key, style in self.styles.items():
                if style.get(W + 'type') == 'paragraph' and style.get(W + 'default') in ('1', 'true', 'on'):
                    self.default_style = key
        except ValueError as error:
            self.error = str(error)

    def _reference(self, paragraph):
        props = paragraph.find(W + 'pPr')
        style_id = value(props, 'pStyle', self.default_style) if props is not None else self.default_style
        result = {}
        chain = []
        seen = set()
        while style_id:
            if style_id in seen or len(seen) >= 256:
                raise ValueError('numbering_style_inheritance_cycle_or_limit')
            seen.add(style_id)
            style = self.styles.get(style_id)
            if style is None:
                break
            chain.append(style.find(W + 'pPr'))
            style_id = value(style, 'basedOn')
        for layer in list(reversed(chain)) + [props]:
            num = None if layer is None else layer.find(W + 'numPr')
            if num is not None:
                if num.find(W + 'numberingChange') is not None or num.find(W + 'ins') is not None:
                    raise ValueError('numbering_revision_unsupported')
                for key in ('numId', 'ilvl'):
                    item = value(num, key)
                    if item is not None:
                        result[key] = item
        return result, seen

    def _levels(self, num_id):
        if num_id in self.cache:
            return self.cache[num_id]
        instance = self.instances.get(num_id)
        abstract = None if instance is None else self.abstracts.get(value(instance, 'abstractNumId'))
        if abstract is None:
            raise ValueError('numbering_definition_unavailable')
        if any(abstract.find(W + tag) is not None for tag in ('numStyleLink', 'styleLink')):
            raise ValueError('numbering_style_link_unsupported')
        if any(key.endswith('}restartNumberingAfterBreak') and val not in ('0', 'false', 'off') for key, val in abstract.attrib.items()):
            raise ValueError('numbering_section_restart_unsupported')
        levels = {}
        for node in abstract.findall(W + 'lvl'):
            level, definition = level_definition(node)
            if level in levels:
                raise ValueError('numbering_duplicate_level')
            levels[level] = definition
        seen = set()
        for node in instance.findall(W + 'lvlOverride'):
            level = integer(node.get(W + 'ilvl'), 0, 8)
            if level in seen:
                raise ValueError('numbering_duplicate_override')
            seen.add(level)
            children = node.findall(W + 'lvl')
            if len(children) > 1:
                raise ValueError('numbering_duplicate_level')
            if children:
                inner, definition = level_definition(children[0])
                if inner != level:
                    raise ValueError('numbering_override_level_mismatch')
                levels[level] = definition
            if level not in levels:
                raise ValueError('numbering_definition_unavailable')
            start = value(node, 'startOverride')
            if start is not None:
                levels[level] = dict(levels[level], start=integer(start, 0, 2147483647))
        self.cache[num_id] = levels
        return levels

    def paragraph_info(self, paragraph, part):
        """Return numbering metadata and explicit issues; never modify paragraph text."""
        info = None
        try:
            reference, styles = self._reference(paragraph)
            if not reference:
                return None, []
            num_id = reference.get('numId')
            info = {'num_id': num_id, 'level': reference.get('ilvl', '0'), 'format': None, 'text': None, 'suppressed': num_id == '0'}
            if num_id == '0':
                return info, []
            if self.error:
                raise ValueError(self.error)
            levels = self._levels(num_id)
            candidates = [index for index, definition in levels.items() if definition['style'] in styles]
            level = integer(reference.get('ilvl', str(candidates[0]) if len(candidates) == 1 else '0'), 0, 8)
            if level not in levels:
                raise ValueError('numbering_definition_unavailable')
            definition = levels[level]
            info.update(level=str(level), format=definition['format'], text=definition['text'])
            counters = self.counters.setdefault((part, num_id), {})
            counters[level] = counters.get(level, definition['start'] - 1) + 1
            for index, entry in levels.items():
                if index > level and entry['restart'] and level < entry['restart']:
                    counters.pop(index, None)
            def substitute(match):
                index = int(match.group(1)) - 1
                if index > level or index not in levels:
                    raise ValueError('numbering_placeholder_unresolved')
                entry = levels[index]
                return formatted(counters.get(index, entry['start']), 'decimal' if definition['legal'] else entry['format'])
            if definition['format'] == 'none':
                info['label'] = ''
            else:
                info['label'] = re.sub(r'%([1-9])', substitute, definition['text'])
            return info, []
        except ValueError as error:
            return info, [str(error)]
