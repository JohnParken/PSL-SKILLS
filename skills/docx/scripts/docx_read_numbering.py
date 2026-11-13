"""Read labels for explicit, single-level decimal Word lists."""

import re

from office.validators.advanced import w


def _value(node, name, default=None):
    children = node.findall(w(name))
    if len(children) > 1:
        raise ValueError(f"duplicate_numbering_{name}")
    return children[0].get(w("val"), default) if children else default


def _definitions(root, name, key):
    if root is None:
        return {}
    values = {}
    for child in root.findall(w(name)):
        identifier = child.get(w(key))
        if not identifier or identifier in values:
            raise ValueError(f"missing_or_duplicate_numbering_{key}")
        values[identifier] = child
    return values


class DecimalNumbering:
    """Resolve direct list references and count paragraphs in document order."""

    def __init__(self, root):
        self.abstracts = _definitions(root, "abstractNum", "abstractNumId")
        self.instances = _definitions(root, "num", "numId")
        self.counters = {}
        self.levels = {}

    def _level(self, number_id):
        instance = self.instances.get(number_id)
        if instance is None:
            return None
        definition = self.abstracts.get(_value(instance, "abstractNumId"))
        if definition is None or definition.find(w("numStyleLink")) is not None or definition.find(w("styleLink")) is not None:
            return None
        levels = definition.findall(w("lvl"))
        if len(levels) != 1 or levels[0].get(w("ilvl")) != "0" or instance.findall(w("lvlOverride")):
            return None
        level = levels[0]
        pattern = _value(level, "lvlText", "")
        if _value(level, "numFmt") != "decimal" or "%1" not in pattern or re.search(r"%[2-9]", pattern):
            return None
        start = _value(level, "start", "0").strip()
        if not re.fullmatch(r"[+-]?[0-9]{1,10}", start) or not -(2 ** 31) <= int(start) < 2 ** 31:
            return None
        return int(start), pattern

    def label(self, paragraph):
        """Return a separate label and optional coverage issue."""
        props = paragraph.find(f"{w('pPr')}/{w('numPr')}")
        if props is None:
            return None, None
        number_id = _value(props, "numId")
        if number_id == "0":
            return None, None
        if _value(props, "ilvl", "0") != "0":
            return None, "paragraph_numbering_unresolved"
        if number_id not in self.levels:
            self.levels[number_id] = self._level(number_id)
        definition = self.levels[number_id]
        if definition is None:
            return None, "paragraph_numbering_unresolved"
        start, pattern = definition
        count = self.counters.get(number_id, start - 1) + 1
        self.counters[number_id] = count
        return pattern.replace("%1", str(count)), None
