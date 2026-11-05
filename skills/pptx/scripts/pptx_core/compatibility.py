"""Scoped MCE decisions stored beside, never in place of, the source XML.

This reader implements Ignorable, ProcessContent and MustUnderstand. Other
compatibility attributes are rejected explicitly; this is not a full MCE
validator. Contexts belong to one bounded package or parse, never a global cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MC = "{" + MC_NS + "}"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
SUPPORTED_ATTRIBUTES = frozenset({MC + "Ignorable", MC + "ProcessContent", MC + "MustUnderstand"})
SUPPORTED_ELEMENTS = frozenset({MC + "AlternateContent", MC + "Choice", MC + "Fallback"})


class CompatibilityError(ValueError):
    """Compatibility markup is unsupported or cannot be interpreted safely."""


def namespace_name(name: str) -> str:
    return name[1:].split("}", 1)[0] if name.startswith("{") else ""


def namespace_tokens(value: str, namespaces: dict[str, str], label: str, attribute: str) -> frozenset[str]:
    resolved = set()
    for prefix in value.split():
        if not NAME_RE.fullmatch(prefix) or not namespaces.get(prefix):
            raise CompatibilityError(f"{label}: {attribute} names an undeclared namespace prefix")
        resolved.add(namespaces[prefix])
    return frozenset(resolved)


def content_tokens(value: str, namespaces: dict[str, str], ignorable: frozenset[str], label: str) -> frozenset[str]:
    resolved = set()
    for token in value.split():
        prefix, separator, local = token.partition(":")
        if not separator or not NAME_RE.fullmatch(prefix) or (local != "*" and not NAME_RE.fullmatch(local)):
            raise CompatibilityError(f"{label}: mc:ProcessContent requires namespace-qualified element names")
        namespace = namespaces.get(prefix)
        if not namespace:
            raise CompatibilityError(f"{label}: mc:ProcessContent names an undeclared namespace prefix")
        if namespace not in ignorable or namespace == MC_NS:
            raise CompatibilityError(f"{label}: mc:ProcessContent must name an Ignorable namespace")
        resolved.add("{" + namespace + "}" + local)
    return frozenset(resolved)


@dataclass(frozen=True)
class CompatibilityScope:
    """Expanded names resolve prefixes at their declaration, before rebinding."""

    ignorable: frozenset[str] = frozenset()
    process_content: frozenset[str] = frozenset()


class CompatibilityContext(set[ET.Element]):
    """Choice membership plus sparse, node-bounded semantic decisions.

    Keeping set behavior preserves the existing private Choice-reader API.
    Ancestor scopes are temporary parser state rather than copies on every node.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ignored: set[ET.Element] = set()
        self.process_content: set[ET.Element] = set()
        self.ignored_attributes: dict[ET.Element, frozenset[str]] = {}
        self.unsupported_requirements: set[ET.Element] = set()

    def enter(
        self, element: ET.Element, namespaces: dict[str, str], parent: CompatibilityScope,
        understood: frozenset[str], label: str,
    ) -> CompatibilityScope:
        if namespace_name(element.tag) == MC_NS and element.tag not in SUPPORTED_ELEMENTS:
            raise CompatibilityError(f"{label}: unsupported MCE element {element.tag.removeprefix(MC)}")
        for attribute in element.attrib:
            if attribute.startswith(MC) and attribute not in SUPPORTED_ATTRIBUTES:
                raise CompatibilityError(f"{label}: unsupported MCE attribute mc:{attribute.removeprefix(MC)}")
        ignorable = parent.ignorable
        if MC + "Ignorable" in element.attrib:
            declared = namespace_tokens(element.attrib[MC + "Ignorable"], namespaces, label, "mc:Ignorable")
            if MC_NS in declared:
                raise CompatibilityError(f"{label}: the MCE namespace cannot be Ignorable")
            ignorable = ignorable | declared
        process_content = parent.process_content
        if MC + "ProcessContent" in element.attrib:
            process_content = process_content | content_tokens(element.attrib[MC + "ProcessContent"], namespaces, ignorable, label)
        if MC + "MustUnderstand" in element.attrib:
            required = namespace_tokens(element.attrib[MC + "MustUnderstand"], namespaces, label, "mc:MustUnderstand")
            if MC_NS in required:
                raise CompatibilityError(f"{label}: mc:MustUnderstand cannot name the MCE namespace")
            if required - understood:
                self.unsupported_requirements.add(element)
        namespace = namespace_name(element.tag)
        if namespace in ignorable and namespace not in understood:
            self.ignored.add(element)
            if element.tag in process_content or "{" + namespace + "}*" in process_content:
                if any("{" + XML_NS + "}" + name in element.attrib for name in ("base", "lang", "space")):
                    raise CompatibilityError(f"{label}: an mc:ProcessContent wrapper cannot carry xml:base, xml:lang or xml:space")
                self.process_content.add(element)
        attributes = frozenset(
            attribute for attribute in element.attrib
            if namespace_name(attribute) in ignorable and namespace_name(attribute) not in understood
        )
        if attributes:
            self.ignored_attributes[element] = attributes
        return CompatibilityScope(ignorable, process_content)
