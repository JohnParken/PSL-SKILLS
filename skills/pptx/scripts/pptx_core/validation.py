"""Read and structurally validate bounded, transitional PPTX packages.

This is an independent package reader, not an OOXML schema validator or a
PowerPoint renderer. It never extracts files, follows links, or rewrites input.
"""

from __future__ import annotations

import io
import posixpath
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .images import ImageValidationError, inspect_image


MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_NODES = 100000
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_R_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CHARTEX_NS = "http://schemas.microsoft.com/office/drawing/2014/chartex"
A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"
INSPECT_MCE_NAMESPACES = frozenset({P_NS, A_NS, A14_NS, MATH_NS, CHART_NS, CHARTEX_NS})
REL_BASE = R_NS + "/"
TYPE_BASE = "application/vnd.openxmlformats-officedocument."
PRESENTATION_TYPE = TYPE_BASE + "presentationml.presentation.main+xml"
WORKBOOK_TYPE = TYPE_BASE + "spreadsheetml.sheet.main+xml"
XLSX_TYPE = TYPE_BASE + "spreadsheetml.sheet"
REL_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
ENCODING_RE = re.compile(r"\bencoding\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
XML_REFERENCE_ATTRIBUTES = frozenset("{" + R_NS + "}" + name for name in ("id", "embed", "link"))
IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpg"}
UNSUPPORTED_RELATIONSHIPS = frozenset({"vbaProject", "oleObject", "control", "activeXControl", "activeXControlBinary"})
KNOWN_RELATIONSHIP_TARGETS = {
    REL_BASE + "slide": ("{" + P_NS + "}sld", TYPE_BASE + "presentationml.slide+xml"),
    REL_BASE + "slideLayout": ("{" + P_NS + "}sldLayout", TYPE_BASE + "presentationml.slideLayout+xml"),
    REL_BASE + "slideMaster": ("{" + P_NS + "}sldMaster", TYPE_BASE + "presentationml.slideMaster+xml"),
    REL_BASE + "notesSlide": ("{" + P_NS + "}notes", TYPE_BASE + "presentationml.notesSlide+xml"),
    REL_BASE + "notesMaster": ("{" + P_NS + "}notesMaster", TYPE_BASE + "presentationml.notesMaster+xml"),
    REL_BASE + "theme": ("{" + A_NS + "}theme", TYPE_BASE + "theme+xml"),
    REL_BASE + "tableStyles": ("{" + A_NS + "}tblStyleLst", TYPE_BASE + "presentationml.tableStyles+xml"),
    REL_BASE + "chart": ("{" + CHART_NS + "}chartSpace", TYPE_BASE + "drawingml.chart+xml"),
    "http://schemas.microsoft.com/office/2014/relationships/chartEx": (
        "{" + CHARTEX_NS + "}chartSpace", "application/vnd.ms-office.chartex+xml",
    ),
}


class ValidationError(ValueError):
    """Input is corrupt, unsafe to inspect, or outside the supported subset."""


def safe_xml(data: bytes, label: str = "XML", *, understood_choices: set[ET.Element] | None = None) -> ET.Element:
    """Parse bounded UTF-8 XML and check namespace-dependent MCE attributes."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_XML_BYTES:
        raise ValidationError(f"{label}: XML must contain 1–{MAX_XML_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValidationError(f"{label}: only UTF-8 XML is supported") from error
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValidationError(f"{label}: DTD, entity declarations and NUL characters are forbidden")
    if re.search(r"<\?(?!xml(?:\s|\?>))", text, flags=re.IGNORECASE):
        raise ValidationError(f"{label}: processing instructions and external stylesheets are unsupported")
    declaration = re.match(r"<\?xml\b.*?\?>", text, flags=re.DOTALL)
    if declaration is not None:
        encoding = ENCODING_RE.search(declaration.group())
        if encoding is not None and encoding.group(2).casefold() not in ("utf-8", "utf8"):
            raise ValidationError(f"{label}: only UTF-8 XML is supported")
    pending_namespaces: dict[str, str] = {}
    scopes: list[dict[str, str]] = []
    choices = understood_choices if understood_choices is not None else set()
    node_count = 0
    root = None
    try:
        events = ET.iterparse(io.BytesIO(data), events=("start", "end", "start-ns"))
        for event, item in events:
            if event == "start-ns":
                prefix, namespace = item
                pending_namespaces[prefix] = namespace
                continue
            if event == "end":
                if item.tag == "{" + MC_NS + "}AlternateContent":
                    _alternate_content_branch(item, choices, label)
                scopes.pop()
                continue
            node_count += 1
            if node_count > MAX_XML_NODES or len(scopes) >= MAX_XML_DEPTH:
                raise ValidationError(f"{label}: XML exceeds the node or depth limit")
            scope = dict(scopes[-1]) if scopes else {"xml": "http://www.w3.org/XML/1998/namespace"}
            scope.update(pending_namespaces)
            pending_namespaces.clear()
            scopes.append(scope)
            if root is None:
                root = item
            ignorable = item.get("{" + MC_NS + "}Ignorable")
            if ignorable is not None:
                _namespace_tokens(ignorable, scope, label, "mc:Ignorable")
            if item.tag == "{" + MC_NS + "}Choice":
                requires = item.get("Requires", "")
                if not requires.strip():
                    raise ValidationError(f"{label}: mc:Choice requires a nonempty Requires attribute")
                _namespace_tokens(requires, scope, label, "mc:Choice Requires")
                if all(scope[prefix] in INSPECT_MCE_NAMESPACES for prefix in requires.split()):
                    choices.add(item)
    except (ET.ParseError, UnicodeError, LookupError) as error:
        raise ValidationError(f"{label}: malformed XML") from error
    if root is None or scopes:
        raise ValidationError(f"{label}: XML has no complete root")
    return root


def _namespace_tokens(value: str, scope: dict[str, str], label: str, attribute: str) -> None:
    for prefix in value.split():
        if not ID_RE.fullmatch(prefix) or not scope.get(prefix):
            raise ValidationError(f"{label}: {attribute} names an undeclared namespace prefix")


def _alternate_content_branch(root: ET.Element, understood_choices: set[ET.Element], label: str) -> ET.Element | None:
    """Select the first supported Choice, preserving prefix scope from parsing."""
    selected = None
    fallback = None
    choice_count = 0
    for child in root:
        if child.tag == "{" + MC_NS + "}Choice" and fallback is None:
            choice_count += 1
            if selected is None and child in understood_choices:
                selected = child
        elif child.tag == "{" + MC_NS + "}Fallback" and fallback is None:
            fallback = child
        else:
            raise ValidationError(f"{label}: invalid mc:AlternateContent branch order or child")
    if not choice_count:
        raise ValidationError(f"{label}: mc:AlternateContent must contain at least one Choice")
    return selected if selected is not None else fallback


def _semantic_elements(root: ET.Element, understood_choices: set[ET.Element]) -> Iterator[ET.Element]:
    """Traverse one MCE branch for text/counts; safety checks still inspect all."""
    if root.tag == "{" + MC_NS + "}AlternateContent":
        branch = _alternate_content_branch(root, understood_choices, "PPTX")
        if branch is None:
            raise ValidationError("PPTX: AlternateContent has no supported Choice or Fallback for inspection")
        for child in branch:
            yield from _semantic_elements(child, understood_choices)
        return
    yield root
    for child in root:
        yield from _semantic_elements(child, understood_choices)


@dataclass
class _Budget:
    entries: int = 0
    uncompressed: int = 0

    def consume(self, entry_count: int, byte_count: int) -> None:
        self.entries += entry_count
        self.uncompressed += byte_count
        if self.entries > MAX_ENTRIES or self.uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ValidationError("Package and embedded workbooks exceed the shared resource budget")


@dataclass(frozen=True)
class _Relationship:
    kind: str
    target: str
    external: bool


@dataclass
class _Package:
    label: str
    parts: dict[str, bytes]
    types: dict[str, str] = field(default_factory=dict)
    xml: dict[str, ET.Element] = field(default_factory=dict)
    relationships: dict[str, dict[str, _Relationship]] = field(default_factory=dict)
    understood_choices: set[ET.Element] = field(default_factory=set)


def _member_name(name: str, *, directory: bool = False) -> str:
    candidate = name[:-1] if directory and name.endswith("/") else name
    if (
        not candidate
        or candidate.startswith("/")
        or any(character in candidate for character in ("\\", ":", "?", "#", "%"))
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or any(segment in ("", ".", "..") for segment in candidate.split("/"))
    ):
        raise ValidationError("Package contains an unsafe or ambiguous member name")
    return candidate


def _read_zip(data: bytes, label: str, budget: _Budget) -> dict[str, bytes]:
    if not isinstance(data, bytes) or not data.startswith(b"PK\x03\x04") or len(data) > MAX_ZIP_BYTES:
        raise ValidationError(f"{label}: expected a ZIP package no larger than 128 MiB")
    parts: dict[str, bytes] = {}
    names: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            budget.consume(len(members), sum(item.file_size for item in members))
            for member in members:
                is_directory = member.is_dir()
                name = _member_name(member.filename, directory=is_directory)
                if member.filename != member.orig_filename or name.casefold() in names:
                    raise ValidationError(f"{label}: duplicate or noncanonical ZIP member")
                names.add(name.casefold())
                if member.flag_bits & (1 | 64):
                    raise ValidationError(f"{label}: encrypted ZIP members are unsupported")
                if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValidationError(f"{label}: only stored and deflated ZIP members are supported")
                mode = stat.S_IFMT(member.external_attr >> 16)
                expected_mode = stat.S_IFDIR if is_directory else stat.S_IFREG
                if mode not in (0, expected_mode):
                    raise ValidationError(f"{label}: links and special ZIP members are unsupported")
                if member.file_size > MAX_MEMBER_BYTES or (is_directory and member.file_size):
                    raise ValidationError(f"{label}: oversized or nonempty directory member")
                if not 0 <= member.header_offset < len(data):
                    raise ValidationError(f"{label}: invalid ZIP member offset")
                payload = archive.read(member)
                if len(payload) != member.file_size:
                    raise ValidationError(f"{label}: ZIP member size mismatch")
                if not is_directory:
                    parts[name] = payload
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError, NotImplementedError, zlib.error) as error:
        raise ValidationError(f"{label}: corrupt or unsupported ZIP data") from error
    if "[Content_Types].xml" not in parts or "_rels/.rels" not in parts:
        raise ValidationError(f"{label}: package content types or root relationships are missing")
    return parts


def _uri_path(value: str, label: str) -> str:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise ValidationError(f"{label}: invalid internal part URI")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValidationError(f"{label}: invalid internal part URI") from error
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or re.search(r"%(?:2f|5c)", value, flags=re.IGNORECASE):
        raise ValidationError(f"{label}: internal part URI must stay within the package")
    try:
        return unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValidationError(f"{label}: invalid URI encoding") from error


def _load_content_types(package: _Package) -> None:
    root = safe_xml(package.parts["[Content_Types].xml"], package.label + " content types")
    package.xml["[Content_Types].xml"] = root
    if root.tag != "{" + CT_NS + "}Types":
        raise ValidationError(f"{package.label}: invalid content types root")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for entry in root:
        content_type = entry.get("ContentType", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]+", content_type) or "/" not in content_type:
            raise ValidationError(f"{package.label}: invalid content type")
        if "macroenabled" in content_type.casefold() or "vba" in content_type.casefold() or "activex" in content_type.casefold():
            raise ValidationError(f"{package.label}: macro and ActiveX content is unsupported")
        if entry.tag == "{" + CT_NS + "}Default":
            extension = entry.get("Extension", "").casefold()
            if not re.fullmatch(r"[a-z0-9]+", extension) or extension in defaults:
                raise ValidationError(f"{package.label}: duplicate or invalid default content type")
            defaults[extension] = content_type
        elif entry.tag == "{" + CT_NS + "}Override":
            raw_name = entry.get("PartName", "")
            if not raw_name.startswith("/"):
                raise ValidationError(f"{package.label}: content type override needs an absolute part name")
            name = _member_name(_uri_path(raw_name, package.label)[1:])
            if name in overrides or name not in package.parts or name == "[Content_Types].xml":
                raise ValidationError(f"{package.label}: duplicate or dangling content type override")
            overrides[name] = content_type
        else:
            raise ValidationError(f"{package.label}: unsupported content types element")
        if list(entry):
            raise ValidationError(f"{package.label}: content type declarations cannot contain children")
    for name in package.parts:
        if name == "[Content_Types].xml":
            continue
        extension = name.rsplit(".", 1)[-1].casefold()
        content_type = overrides.get(name, defaults.get(extension))
        if content_type is None:
            raise ValidationError(f"{package.label}: a package member has no content type")
        package.types[name] = content_type
        if name.endswith(".rels") and content_type != REL_TYPE:
            raise ValidationError(f"{package.label}: relationships part has an incorrect content type")
        if content_type == REL_TYPE and not name.endswith(".rels"):
            raise ValidationError(f"{package.label}: relationship data has an invalid part name")


def _relationships_owner(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(name)
    if posixpath.basename(directory) != "_rels" or not filename.endswith(".rels"):
        raise ValidationError("Relationships part has no valid owner path")
    return posixpath.join(posixpath.dirname(directory), filename[:-5])


def _external_link(target: str, kind: str, label: str) -> None:
    if kind != REL_BASE + "hyperlink" or any(character.isspace() for character in target) or "\\" in target:
        raise ValidationError(f"{label}: only passive HTTP, HTTPS and mailto hyperlinks may be external")
    try:
        parsed = urlsplit(target)
    except ValueError as error:
        raise ValidationError(f"{label}: malformed external hyperlink") from error
    if parsed.scheme not in ("http", "https", "mailto"):
        raise ValidationError(f"{label}: unsupported external hyperlink scheme")
    if parsed.scheme in ("http", "https") and not parsed.netloc:
        raise ValidationError(f"{label}: HTTP hyperlink has no host")
    if parsed.scheme == "mailto" and not parsed.path:
        raise ValidationError(f"{label}: mail hyperlink has no address")


def _load_relationships(package: _Package) -> None:
    for name, root in package.xml.items():
        if not name.endswith(".rels"):
            continue
        if root.tag != "{" + PACKAGE_R_NS + "}Relationships":
            raise ValidationError(f"{package.label}: invalid relationships root")
        owner = _relationships_owner(name)
        if owner and owner not in package.parts:
            raise ValidationError(f"{package.label}: relationships owner is missing")
        entries: dict[str, _Relationship] = {}
        package.relationships[owner] = entries
        for entry in root:
            if entry.tag != "{" + PACKAGE_R_NS + "}Relationship" or list(entry):
                raise ValidationError(f"{package.label}: invalid relationship element")
            if set(entry.attrib) - {"Id", "Type", "Target", "TargetMode"}:
                raise ValidationError(f"{package.label}: unsupported relationship attributes")
            identifier = entry.get("Id", "")
            kind = entry.get("Type", "")
            target = entry.get("Target", "")
            mode = entry.get("TargetMode", "Internal")
            if not ID_RE.fullmatch(identifier) or identifier in entries:
                raise ValidationError(f"{package.label}: duplicate or invalid relationship ID")
            if not kind.startswith(("http://", "https://")) or kind.rsplit("/", 1)[-1] in UNSUPPORTED_RELATIONSHIPS:
                raise ValidationError(f"{package.label}: unsupported relationship type")
            if not target or any(ord(character) < 32 or ord(character) == 127 for character in target):
                raise ValidationError(f"{package.label}: invalid relationship target")
            if mode == "External":
                _external_link(target, kind, package.label)
                entries[identifier] = _Relationship(kind, target, True)
                continue
            if mode != "Internal":
                raise ValidationError(f"{package.label}: invalid relationship target mode")
            uri_path = _uri_path(target, package.label)
            if uri_path.startswith("/"):
                resolved = posixpath.normpath(uri_path[1:])
            else:
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(owner), uri_path))
            _member_name(resolved)
            if resolved not in package.parts or resolved.endswith(".rels") or resolved == "[Content_Types].xml":
                raise ValidationError(f"{package.label}: relationship target is missing or invalid")
            expected = KNOWN_RELATIONSHIP_TARGETS.get(kind)
            if expected is not None:
                expected_root, expected_type = expected
                target_root = package.xml.get(resolved)
                if target_root is None or target_root.tag != expected_root or package.types[resolved] != expected_type:
                    raise ValidationError(f"{package.label}: relationship target root or content type mismatch")
            if kind == REL_BASE + "image" and package.types[resolved] not in IMAGE_FORMATS:
                raise ValidationError(f"{package.label}: image relationship does not target supported image data")
            if kind == REL_BASE + "package" and package.types[resolved] != XLSX_TYPE:
                raise ValidationError(f"{package.label}: only XLSX packages may be embedded")
            if kind == REL_BASE + "hyperlink":
                raise ValidationError(f"{package.label}: internal hyperlinks must use slide relationships")
            entries[identifier] = _Relationship(kind, resolved, False)
    for name, root in package.xml.items():
        if name.endswith(".rels"):
            continue
        entries = package.relationships.get(name, {})
        for element in root.iter():
            for attribute, identifier in element.attrib.items():
                if attribute in XML_REFERENCE_ATTRIBUTES and identifier not in entries:
                    raise ValidationError(f"{package.label}: XML references an unresolved relationship")


def _load_package(data: bytes, label: str, budget: _Budget, *, workbook: bool = False,
                  allow_png_zero_padding: bool = False, diagnostics: list | None = None) -> _Package:
    package = _Package(label, _read_zip(data, label, budget))
    _load_content_types(package)
    for name, content_type in package.types.items():
        payload = package.parts[name]
        if "/embeddings/" in name and (content_type != XLSX_TYPE or not name.casefold().endswith(".xlsx")):
            raise ValidationError(f"{label}: only XLSX chart workbooks may be embedded")
        if content_type == XLSX_TYPE:
            if workbook or not name.casefold().endswith(".xlsx"):
                raise ValidationError(f"{label}: nested or non-XLSX embedded packages are unsupported")
        elif content_type in IMAGE_FORMATS:
            try:
                inspect_image(payload, IMAGE_FORMATS[content_type])
            except ImageValidationError as error:
                # Non-standard IEND padding is accepted only in explicit inspection mode.
                # Find IEND through bounded chunk framing, then fully validate the prefix.
                if not allow_png_zero_padding or IMAGE_FORMATS[content_type] != 'png':
                    raise ValidationError(f"{label}: invalid PNG/JPEG image") from error
                offset = 8
                while offset + 12 <= len(payload):
                    size = int.from_bytes(payload[offset:offset + 4], 'big')
                    end = offset + 12 + size
                    if end > len(payload):
                        break
                    if payload[offset + 4:offset + 8] == b'IEND':
                        tail = payload[end:]
                        if not tail or len(tail) > 1024 or any(tail):
                            break
                        try:
                            inspect_image(payload[:end], 'png')
                        except ImageValidationError:
                            break
                        if diagnostics is not None:
                            diagnostics.append({'code': 'png_trailing_zero_padding', 'part': name,
                                                'bytes': len(tail)})
                        break
                    offset = end
                else:
                    end = 0
                if not (offset + 12 <= len(payload) and payload[offset + 4:offset + 8] == b'IEND'
                        and diagnostics and diagnostics[-1].get('part') == name):
                    raise ValidationError(f"{label}: invalid PNG/JPEG image") from error
        elif content_type.startswith("image/"):
            raise ValidationError(f"{label}: only PNG and JPEG media are supported")
        elif content_type.endswith("+xml") or content_type in ("application/xml", "text/xml"):
            package.xml[name] = safe_xml(payload, f"{label} part {name}", understood_choices=package.understood_choices)
        else:
            raise ValidationError(f"{label}: unsupported binary package content")
    _load_relationships(package)
    return package


def _find_relationship(
    package: _Package,
    owner: str,
    kind: str,
    *,
    required: bool = True,
) -> _Relationship | None:
    candidates = [item for item in package.relationships.get(owner, {}).values() if item.kind == kind]
    if len(candidates) > 1 or (required and not candidates) or any(item.external for item in candidates):
        raise ValidationError(f"{package.label}: missing, duplicate or external required relationship")
    return candidates[0] if candidates else None


def _root_part(package: _Package, expected_type: str, expected_tag: str) -> str:
    relation = _find_relationship(package, "", REL_BASE + "officeDocument")
    assert relation is not None
    name = relation.target
    if package.types.get(name) != expected_type or name not in package.xml or package.xml[name].tag != expected_tag:
        raise ValidationError(f"{package.label}: unsupported document type or main XML root")
    return name


def _integer(value: str, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValidationError(f"{label}: expected an integer")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValidationError(f"{label}: integer is outside the supported range")
    return result


def _validate_embedded_workbook(data: bytes, label: str, budget: _Budget) -> None:
    package = _load_package(data, label, budget, workbook=True)
    main_name = _root_part(package, WORKBOOK_TYPE, "{" + SHEET_NS + "}workbook")
    root = package.xml[main_name]
    sheets = root.findall("{" + SHEET_NS + "}sheets/{" + SHEET_NS + "}sheet")
    if not sheets:
        raise ValidationError(f"{label}: workbook has no worksheets")
    identifiers: set[int] = set()
    names: set[str] = set()
    targets: set[str] = set()
    relationships = package.relationships.get(main_name, {})
    for sheet in sheets:
        identifier = _integer(sheet.get("sheetId", ""), 1, 4294967295, label)
        name = sheet.get("name", "")
        relation = relationships.get(sheet.get("{" + R_NS + "}id", ""))
        if identifier in identifiers or not name or name.casefold() in names:
            raise ValidationError(f"{label}: duplicate or invalid worksheet identity")
        identifiers.add(identifier)
        names.add(name.casefold())
        if relation is None or relation.external or relation.kind != REL_BASE + "worksheet" or relation.target in targets:
            raise ValidationError(f"{label}: invalid worksheet relationship")
        targets.add(relation.target)
        worksheet = package.xml.get(relation.target)
        if worksheet is None or worksheet.tag != "{" + SHEET_NS + "}worksheet":
            raise ValidationError(f"{label}: unsupported worksheet root")
        if package.types[relation.target] != TYPE_BASE + "spreadsheetml.worksheet+xml":
            raise ValidationError(f"{label}: worksheet content type mismatch")
        if len(worksheet.findall("{" + SHEET_NS + "}sheetData")) != 1:
            raise ValidationError(f"{label}: worksheet must contain one sheetData element")


def _shape_tree(root: ET.Element, label: str) -> ET.Element:
    common = root.findall("{" + P_NS + "}cSld")
    if len(common) != 1:
        raise ValidationError(f"{label}: expected one common slide element")
    trees = common[0].findall("{" + P_NS + "}spTree")
    if len(trees) != 1:
        raise ValidationError(f"{label}: expected one shape tree")
    identifiers: set[int] = set()
    for element in root.iter("{" + P_NS + "}cNvPr"):
        identifier = _integer(element.get("id", ""), 1, 4294967295, label)
        if identifier in identifiers:
            raise ValidationError(f"{label}: duplicate drawing object IDs")
        identifiers.add(identifier)
    return trees[0]


def _typed_root(package: _Package, name: str, tag: str, type_suffix: str) -> ET.Element:
    root = package.xml.get(name)
    if root is None or root.tag != "{" + P_NS + "}" + tag or package.types.get(name) != TYPE_BASE + type_suffix:
        raise ValidationError(f"{package.label}: incorrect {tag} root or content type")
    _shape_tree(root, package.label + " " + name)
    return root


def _paragraph_text(root: ET.Element, understood_choices: set[ET.Element], *, keep_empty: bool) -> list[str]:
    paragraphs = []
    for paragraph in _semantic_elements(root, understood_choices):
        if paragraph.tag != "{" + A_NS + "}p":
            continue
        pieces = []
        for item in _semantic_elements(paragraph, understood_choices):
            if item.tag in ("{" + A_NS + "}t", "{" + MATH_NS + "}t"):
                pieces.append(item.text or "")
            elif item.tag == "{" + A_NS + "}br":
                pieces.append("\n")
        text = "".join(pieces)
        if text or keep_empty:
            paragraphs.append(text)
    return paragraphs


def _notes_text(root: ET.Element, understood_choices: set[ET.Element]) -> list[str]:
    paragraphs = []
    for shape in _semantic_elements(root, understood_choices):
        if shape.tag != "{" + P_NS + "}sp":
            continue
        placeholder = shape.find("{" + P_NS + "}nvSpPr/{" + P_NS + "}nvPr/{" + P_NS + "}ph")
        if placeholder is not None and placeholder.get("type") in ("sldImg", "sldNum", "dt", "ftr", "hdr"):
            continue
        paragraphs.extend(_paragraph_text(shape, understood_choices, keep_empty=True))
    return paragraphs


def _master_roster(package: _Package, main_name: str) -> tuple[set[str], set[str]]:
    root = package.xml[main_name]
    relationships = package.relationships.get(main_name, {})
    rosters = root.findall("{" + P_NS + "}sldMasterIdLst")
    if len(rosters) != 1 or not list(rosters[0]):
        raise ValidationError("PPTX: presentation must contain a nonempty slide master roster")
    identifiers: set[int] = set()
    master_targets: set[str] = set()
    for entry in rosters[0]:
        if entry.tag != "{" + P_NS + "}sldMasterId":
            raise ValidationError("PPTX: invalid slide master roster entry")
        identifier = _integer(entry.get("id", ""), 2147483648, 4294967295, "slide master ID")
        relation = relationships.get(entry.get("{" + R_NS + "}id", ""))
        if identifier in identifiers or relation is None or relation.external or relation.kind != REL_BASE + "slideMaster":
            raise ValidationError("PPTX: duplicate master ID or invalid master relationship")
        identifiers.add(identifier)
        if relation.target in master_targets:
            raise ValidationError("PPTX: slide master roster reuses the same part")
        master_targets.add(relation.target)
        _typed_root(package, relation.target, "sldMaster", "presentationml.slideMaster+xml")
        _find_relationship(package, relation.target, REL_BASE + "theme")
    note_rosters = root.findall("{" + P_NS + "}notesMasterIdLst")
    if len(note_rosters) > 1:
        raise ValidationError("PPTX: duplicate notes master roster")
    note_targets: set[str] = set()
    for entry in list(note_rosters[0]) if note_rosters else []:
        if entry.tag != "{" + P_NS + "}notesMasterId":
            raise ValidationError("PPTX: invalid notes master roster entry")
        relation = relationships.get(entry.get("{" + R_NS + "}id", ""))
        if relation is None or relation.external or relation.kind != REL_BASE + "notesMaster" or relation.target in note_targets:
            raise ValidationError("PPTX: invalid or duplicate notes master relationship")
        note_targets.add(relation.target)
        _typed_root(package, relation.target, "notesMaster", "presentationml.notesMaster+xml")
        _find_relationship(package, relation.target, REL_BASE + "theme")
    return master_targets, note_targets


def _inspect(data: bytes, *, allow_png_zero_padding: bool = False) -> dict:
    budget = _Budget()
    diagnostics: list = []
    package = _load_package(data, "PPTX", budget, allow_png_zero_padding=allow_png_zero_padding,
                            diagnostics=diagnostics)
    for name, content_type in package.types.items():
        if content_type == XLSX_TYPE:
            _validate_embedded_workbook(package.parts[name], f"Embedded workbook {name}", budget)
    main_name = _root_part(package, PRESENTATION_TYPE, "{" + P_NS + "}presentation")
    presentation = package.xml[main_name]
    master_targets, note_master_targets = _master_roster(package, main_name)
    sizes = presentation.findall("{" + P_NS + "}sldSz")
    if len(sizes) != 1:
        raise ValidationError("PPTX: presentation must contain one slide size")
    width = _integer(sizes[0].get("cx", ""), 914400, 51206400, "slide width")
    height = _integer(sizes[0].get("cy", ""), 914400, 51206400, "slide height")
    rosters = presentation.findall("{" + P_NS + "}sldIdLst")
    if len(rosters) != 1 or not list(rosters[0]):
        raise ValidationError("PPTX: presentation must contain a nonempty slide roster")
    slide_ids: set[int] = set()
    slide_targets: set[str] = set()
    relationships = package.relationships.get(main_name, {})
    inspected_slides = []
    for number, entry in enumerate(rosters[0], 1):
        if entry.tag != "{" + P_NS + "}sldId":
            raise ValidationError("PPTX: unsupported slide roster entry")
        identifier = _integer(entry.get("id", ""), 256, 2147483647, "slide ID")
        relation = relationships.get(entry.get("{" + R_NS + "}id", ""))
        if identifier in slide_ids or relation is None or relation.external or relation.kind != REL_BASE + "slide":
            raise ValidationError("PPTX: duplicate slide ID or invalid slide relationship")
        slide_ids.add(identifier)
        if relation.target in slide_targets:
            raise ValidationError("PPTX: slide roster reuses the same part")
        slide_targets.add(relation.target)
        root = _typed_root(package, relation.target, "sld", "presentationml.slide+xml")
        layout = _find_relationship(package, relation.target, REL_BASE + "slideLayout")
        assert layout is not None
        _typed_root(package, layout.target, "sldLayout", "presentationml.slideLayout+xml")
        master = _find_relationship(package, layout.target, REL_BASE + "slideMaster")
        assert master is not None
        if master.target not in master_targets:
            raise ValidationError("PPTX: slide layout uses an unlisted slide master")
        _typed_root(package, master.target, "sldMaster", "presentationml.slideMaster+xml")
        note_relation = _find_relationship(package, relation.target, REL_BASE + "notesSlide", required=False)
        notes = []
        if note_relation is not None:
            note_root = _typed_root(package, note_relation.target, "notes", "presentationml.notesSlide+xml")
            note_slide = _find_relationship(package, note_relation.target, REL_BASE + "slide")
            note_master = _find_relationship(package, note_relation.target, REL_BASE + "notesMaster")
            if note_slide is None or note_slide.target != relation.target or note_master is None:
                raise ValidationError("PPTX: notes do not point back to their slide and notes master")
            if note_master.target not in note_master_targets:
                raise ValidationError("PPTX: notes use an unlisted notes master")
            _typed_root(package, note_master.target, "notesMaster", "presentationml.notesMaster+xml")
            notes = _notes_text(note_root, package.understood_choices)
        counts = {"shapes": 0, "images": 0, "tables": 0, "charts": 0}
        for element in _semantic_elements(root, package.understood_choices):
            if element.tag in ("{" + P_NS + "}sp", "{" + P_NS + "}cxnSp"):
                counts["shapes"] += 1
            elif element.tag == "{" + P_NS + "}pic":
                counts["images"] += 1
            elif element.tag == "{" + A_NS + "}tbl":
                counts["tables"] += 1
            elif element.tag in ("{" + CHART_NS + "}chart", "{" + CHARTEX_NS + "}chart"):
                counts["charts"] += 1
        inspected_slides.append({
            "number": number,
            "text": _paragraph_text(root, package.understood_choices, keep_empty=False),
            "notes": notes,
            "objects": counts,
        })
    title = ""
    core = _find_relationship(package, "", CORE_REL, required=False)
    if core is not None:
        properties = package.xml.get(core.target)
        if properties is None or properties.tag != "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties":
            raise ValidationError("PPTX: invalid core document properties")
        titles = properties.findall("{http://purl.org/dc/elements/1.1/}title")
        if len(titles) > 1:
            raise ValidationError("PPTX: duplicate document title")
        if titles:
            title = titles[0].text or ""
    result = {
        "slide_count": len(inspected_slides),
        "parts": len(package.parts),
        "slide_size": {"width_emu": width, "height_emu": height},
        "structural_validation": "passed",
        "visual_validation": "not_run",
        "title": title,
        "slides": inspected_slides,
    }
    if diagnostics:
        result['structural_validation'] = 'partial'
        result['diagnostics'] = diagnostics
    return result


def validate_presentation(data: bytes) -> dict:
    """Validate the supported structural contract without returning deck text."""
    result = _inspect(data)
    result.pop("title")
    result.pop("slides")
    return result


def inspect_presentation(data: bytes, *, allow_png_zero_padding: bool = False) -> dict:
    """Return slide order, native object counts, text, notes and document title."""
    return _inspect(data, allow_png_zero_padding=allow_png_zero_padding)
