"""Plan and verify exact template substitutions within ordinary Word text nodes."""

from collections import defaultdict
import re
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError, version_info

from tl_package import OFFICE_REL, Package

WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML = 'http://www.w3.org/XML/1998/namespace'
XMLNS = 'http://www.w3.org/2000/xmlns/'
CONTAINERS = {'document', 'body', 'hdr', 'ftr', 'tbl', 'tr', 'tc', 'p', 'r'}
RUN_CHILDREN = {'rPr', 't', 'tab', 'br', 'cr', 'noBreakHyphen', 'softHyphen'}
BLOCKED = {'fldSimple', 'fldChar', 'instrText', 'ins', 'del', 'moveFrom', 'moveTo', 'pPrChange', 'rPrChange', 'sectPr', 'commentReference'}
PLACEHOLDER = re.compile(r'\{\{[^{}\r\n]+\}\}')
MAX_RULES = 1000
MAX_MATCHES = 100_000


def xml_text(value, *, empty=False):
    """Accept one line of valid XML text."""
    if (not isinstance(value, str) or not empty and not value
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or char in ('\ufffe', '\uffff') for char in value)):
        raise ValueError('Replacement values must be single-line XML text')
    return value


def read_rules(spec):
    """Normalize guarded replacement rules and reject duplicate search strings."""
    if not isinstance(spec, dict) or 'replacements' not in spec or set(spec) - {'replacements', 'parts', 'require_no_placeholders'}:
        raise ValueError('Expected replacements with optional parts and require_no_placeholders')
    rules = spec['replacements']
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        raise ValueError(f'Expected 1–{MAX_RULES} replacement rules')
    result = {}
    for rule in rules:
        if not isinstance(rule, dict) or not {'old', 'new'} <= rule.keys() or rule.keys() - {'old', 'new', 'count'}:
            raise ValueError('Each replacement requires old and new, with optional count')
        old, new = xml_text(rule['old']), xml_text(rule['new'], empty=True)
        count = rule.get('count', 1)
        if old in result or type(count) is not int or not 1 <= count <= MAX_MATCHES:
            raise ValueError('Replacement searches must be distinct and counts positive integers')
        result[old] = (new, count)
    if type(spec.get('require_no_placeholders', False)) is not bool:
        raise ValueError('require_no_placeholders must be boolean')
    return result


def word_parts(package, selected):
    """Restrict replacements to the main story and directly related headers and footers."""
    allowed = {package.main: 'document'}
    for relation in package.relations(package.main).values():
        kind = relation['type']
        if not relation['external'] and kind in (OFFICE_REL + 'header', OFFICE_REL + 'footer'):
            allowed[relation['target']] = 'hdr' if kind.endswith('/header') else 'ftr'
    if (not isinstance(selected, list) or not selected or any(not isinstance(name, str) for name in selected)
            or len(set(selected)) != len(selected) or any(name not in allowed or name not in package.names for name in selected)):
        raise ValueError('Select distinct existing main, header or footer XML parts')
    return [(name, allowed[name]) for name in selected]


def text_value(node):
    """Read text and CDATA under one w:t node."""
    return ''.join(child.data for child in node.childNodes if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


def inherits_space(node):
    """Resolve the nearest explicit xml:space value."""
    while node is not None and node.nodeType == Node.ELEMENT_NODE:
        if node.hasAttributeNS(XML, 'space'):
            return node.getAttributeNS(XML, 'space') == 'preserve'
        node = node.parentNode
    return False


def eligible(node, blocked):
    """Check that substitution would touch only supported, unprotected run text."""
    run = node.parentNode
    if run.namespaceURI != WORD or run.localName != 'r' or node in blocked:
        return False
    if any(child.nodeType not in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE) for child in node.childNodes):
        return False
    if any(attr.namespaceURI != XMLNS and (attr.namespaceURI, attr.localName) != (XML, 'space') for attr in node.attributes.values()):
        return False
    if any(child.nodeType == Node.ELEMENT_NODE and (child.namespaceURI != WORD or child.localName not in RUN_CHILDREN) for child in run.childNodes):
        return False
    if any(child.namespaceURI == WORD and child.localName == 'rPrChange' for child in run.getElementsByTagName('*')):
        return False
    for ancestor in walk_parents(run):
        if ancestor.namespaceURI != WORD or ancestor.localName not in CONTAINERS:
            return False
        if ancestor.localName == 'p' and any(child.namespaceURI == WORD and child.localName in BLOCKED
                                             for child in ancestor.getElementsByTagName('*')):
            return False
    return True


def walk_parents(element):
    """Yield an element and its element ancestors."""
    while element is not None and element.nodeType == Node.ELEMENT_NODE:
        yield element
        element = element.parentNode


def protected_text(root):
    """Track cross-paragraph fields, comment, bookmark, move and permission ranges."""
    blocked = set()
    active, seen = set(), set()
    fields = []
    for node in root.getElementsByTagNameNS(WORD, '*'):
        kind = node.localName
        if kind == 't' and (active or fields):
            blocked.add(node)
        elif kind == 'fldChar':
            phase = node.getAttributeNS(WORD, 'fldCharType')
            if phase == 'begin':
                fields.append(False)
            elif phase == 'separate' and fields and not fields[-1]:
                fields[-1] = True
            elif phase == 'end' and fields:
                fields.pop()
            else:
                raise ValueError('Malformed complex field range in selected part')
        elif kind in {'bookmarkStart', 'bookmarkEnd', 'permStart', 'permEnd'} or kind.endswith(('RangeStart', 'RangeEnd')):
            start = kind.endswith('Start')
            key = (kind[:-5] if start else kind[:-3], node.getAttributeNS(WORD, 'id'))
            if not key[1] or (key in seen if start else key not in active):
                raise ValueError('Malformed or duplicated range marker in selected part')
            if start:
                active.add(key)
                seen.add(key)
            else:
                active.remove(key)
    if fields or active:
        raise ValueError('Unclosed field or range in selected part')
    return blocked


def verify_no_placeholders(root):
    """Find residual double-brace markers, including ones split across runs."""
    for para in root.getElementsByTagNameNS(WORD, 'p'):
        values = [text_value(node) for node in para.getElementsByTagNameNS(WORD, 't')
                  if next((item for item in walk_parents(node) if item.namespaceURI == WORD and item.localName == 'p'), None) is para]
        if PLACEHOLDER.search(''.join(values)):
            raise ValueError('An unresolved double-brace placeholder remains in a selected part')


def plan_replacements(trees, rules):
    """Match original text simultaneously and reject any overlap before mutation."""
    counts = dict.fromkeys(rules, 0)
    unavailable = set()
    mutations = defaultdict(list)
    for tree in trees.values():
        blocked = protected_text(tree.documentElement)
        reject_split_matches(tree.documentElement, rules)
        for node in tree.getElementsByTagNameNS(WORD, 't'):
            value = text_value(node)
            applicable = [old for old in rules if old in value]
            if not applicable:
                continue
            if not eligible(node, blocked) or value.strip(' \t\n\r') != value and not inherits_space(node):
                unavailable.update(applicable)
                continue
            for old in applicable:
                start = value.find(old)
                while start >= 0:
                    new, expected = rules[old]
                    counts[old] += 1
                    if counts[old] > expected:
                        raise ValueError('A replacement matched more than its expected count')
                    mutations[node].append((start, start + len(old), new))
                    start = value.find(old, start + 1)
    for old, (_, expected) in rules.items():
        if counts[old] != expected or old in unavailable:
            raise ValueError('Replacement count mismatch or protected match; check for split runs, fields, ranges or ambiguous whitespace')
    for ranges in mutations.values():
        ranges.sort()
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise ValueError('Overlapping replacements in original text')
    return mutations, counts


def reject_split_matches(root, rules):
    """Reject original search matches spanning adjacent text nodes in a paragraph."""
    for paragraph in root.getElementsByTagNameNS(WORD, 'p'):
        nodes = [node for node in paragraph.getElementsByTagNameNS(WORD, 't')
                 if next((item for item in walk_parents(node) if item.namespaceURI == WORD and item.localName == 'p'), None) is paragraph]
        values = [text_value(node) for node in nodes]
        if len(values) < 2:
            continue
        total = ''.join(values)
        for old in rules:
            if old not in total:
                continue
            whole_count = sum(1 for _ in re.finditer(f'(?={re.escape(old)})', total))
            node_count = sum(sum(1 for _ in re.finditer(f'(?={re.escape(old)})', value)) for value in values)
            if whole_count != node_count:
                raise ValueError('Replacement matches text split across multiple Word nodes')


def mutate(node, ranges):
    """Write planned replacements without applying rules to inserted text."""
    original = text_value(node)
    pieces, offset = [], 0
    for start, end, replacement in ranges:
        pieces.extend((original[offset:start], replacement))
        offset = end
    value = ''.join(pieces) + original[offset:]
    while node.firstChild is not None:
        node.removeChild(node.firstChild)
    if value:
        node.appendChild(node.ownerDocument.createTextNode(value))
    if value.strip(' \t\r\n') != value:
        node.setAttributeNS(XML, 'xml:space', 'preserve')


def parse_part(package, name):
    """Check XML through the bounded package parser before preserving lexical DOM nodes."""
    package.xml(name)
    if version_info < (2, 6, 0):
        raise RuntimeError('Template replacement requires Expat 2.6.0 or newer for DOM readback')
    try:
        return minidom.parseString(package.read(name))
    except ExpatError as exc:
        raise ValueError('Selected Word XML could not be parsed') from exc


def replace_document(package, spec):
    """Return an independently rechecked DOCX/DOTX copy and rule counts."""
    if any(name.lower().startswith('_xmlsignatures/') or 'digital-signature' in kind for name, kind in package.types.items()) or any(
        'digital-signature' in rel['type'] for rel in package.relations('').values()
    ):
        raise ValueError('Digitally signed documents cannot be edited')
    rules = read_rules(spec)
    trees = {}
    for name, kind in word_parts(package, spec.get('parts', [package.main])):
        tree = parse_part(package, name)
        root = tree.documentElement
        if root.namespaceURI != WORD or root.localName != kind:
            raise ValueError('Selected Word part has an unexpected root')
        trees[name] = tree
    changes, counts = plan_replacements(trees, rules)
    for node, ranges in changes.items():
        mutate(node, ranges)
    if spec.get('require_no_placeholders', False):
        for tree in trees.values():
            verify_no_placeholders(tree.documentElement)
    touched = {node.ownerDocument for node in changes}
    updates = {name: tree.toxml(encoding='UTF-8') for name, tree in trees.items() if tree in touched}
    candidate = package.overlay(updates)
    verified = Package(candidate)
    if verified.names != package.names or verified.extension != package.extension:
        raise ValueError('Replacement changed package membership or document type')
    for name in sorted(package.names):
        if name in updates:
            if parse_part(verified, name).documentElement.toxml() != trees[name].documentElement.toxml():
                raise ValueError('Rewritten XML failed readback')
        elif verified.read(name) != package.read(name):
            raise ValueError('A non-target package part changed')
    return candidate, {'rule_counts': list(counts.values()), 'changed_parts': sorted(updates)}
