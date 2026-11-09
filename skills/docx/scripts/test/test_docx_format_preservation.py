"""Regression coverage for formatting-only edits through the public API.

Fixtures are synthetic OOXML packages. These tests do not claim Office rendering
or real-producer acceptance evidence.
"""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from fixture_delivery import create_fixture

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
from create_docx import create_document
from edit_structure import edit_structure

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"


def tag(name):
    return f"{{{W}}}{name}"


def attributes(node):
    return {key.rsplit("}", 1)[-1]: value for key, value in node.attrib.items()}


def set_attributes(node, values):
    for name, value in values.items():
        node.set(tag(name), str(value))


def element(parent, name, values=None, text=None):
    node = ET.SubElement(parent, tag(name))
    if values:
        set_attributes(node, values)
    if text is not None:
        node.text = text
    return node


def semantic_element(node):
    """Compare XML meaning, ignoring attribute order and namespace prefixes."""
    return (
        node.tag,
        tuple(sorted(node.attrib.items())),
        node.text,
        tuple(semantic_element(child) for child in node),
        node.tail,
    )


@unittest.skipUnless(
    sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
    "native DOCX editing requires Python >=3.10 and Expat >=2.6",
)
class FormatPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="docx-format-preservation-", dir="."
        )
        self.work = Path(self.temporary.name).resolve()
        self.previous = Path.cwd()
        os.chdir(self.work)
        self.input_hashes = {}

    def tearDown(self):
        try:
            for path, digest in self.input_hashes.items():
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, digest, f"Input changed: {path.name}")
        finally:
            os.chdir(self.previous)
            self.temporary.cleanup()

    def track_input(self, path, *, replace=False):
        resolved = Path(path).resolve()
        if replace or resolved not in self.input_hashes:
            self.input_hashes[resolved] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return resolved

    @staticmethod
    def members(path):
        with zipfile.ZipFile(path) as package:
            return {name: package.read(name) for name in package.namelist()}

    @staticmethod
    def write_members(path, parts):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name, content in parts.items():
                package.writestr(name, content)

    def generated_document(self, name, blocks=None):
        destination = self.work / name
        create_fixture(
            create_document,
            {"blocks": blocks or [{"type": "paragraph", "text": "fixture"}]},
            destination,
            base_dir=self.work,
            verify=True,
        )
        return Path(name)

    @staticmethod
    def append_style(styles, style_type, style_id, name, based_on):
        style = ET.SubElement(
            styles,
            tag("style"),
            {tag("type"): style_type, tag("styleId"): style_id},
        )
        element(style, "name", {"val": name})
        if based_on:
            element(style, "basedOn", {"val": based_on})
        return style

    def add_known_styles(self, data):
        styles = ET.fromstring(data)
        defaults = styles.find(tag("docDefaults"))
        run_defaults = defaults.find(f"{tag('rPrDefault')}/{tag('rPr')}")
        default_fonts = run_defaults.find(tag("rFonts"))
        set_attributes(default_fonts, {
            "ascii": "Defaults Latin",
            "hAnsi": "Defaults Latin",
            "eastAsia": "Defaults Asia",
            "cs": "Defaults Complex",
        })
        set_attributes(run_defaults.find(tag("sz")), {"val": "22"})
        set_attributes(run_defaults.find(tag("szCs")), {"val": "22"})
        paragraph_defaults = defaults.find(f"{tag('pPrDefault')}/{tag('pPr')}")
        default_spacing = paragraph_defaults.find(tag("spacing"))
        if default_spacing is None:
            default_spacing = ET.Element(tag("spacing"))
            paragraph_defaults.insert(0, default_spacing)
        set_attributes(default_spacing, {
            "before": "90", "after": "70", "line": "276", "lineRule": "auto",
        })

        base = self.append_style(
            styles, "paragraph", "BaseParagraph", "Base Paragraph", "Normal"
        )
        base_ppr = element(base, "pPr")
        base_tabs = element(base_ppr, "tabs")
        element(base_tabs, "tab", {"val": "left", "pos": "480"})
        element(base_ppr, "spacing", {
            "before": "260", "after": "130", "line": "240", "lineRule": "auto",
        })
        element(base_ppr, "ind", {"left": "480", "right": "160"})

        derived = self.append_style(
            styles, "paragraph", "DerivedParagraph", "Derived Paragraph", "BaseParagraph"
        )
        derived_ppr = element(derived, "pPr")
        element(derived_ppr, "spacing", {"after": "150"})

        run_style = self.append_style(
            styles, "character", "CharacterOverlay", "Character Overlay",
            "DefaultParagraphFont",
        )
        run_ppr = element(run_style, "rPr")
        element(run_ppr, "rFonts", {
            "ascii": "Styled Latin", "hAnsi": "Styled Latin", "eastAsia": "Styled Asia",
        })
        element(run_ppr, "sz", {"val": "24"})
        return ET.tostring(styles, encoding="utf-8", xml_declaration=True)

    def add_known_numbering(self, data):
        numbering = ET.fromstring(data)
        abstract = next(
            node for node in numbering.findall(tag("abstractNum"))
            if node.get(tag("abstractNumId")) == "0"
        )
        level = next(
            node for node in abstract.findall(tag("lvl"))
            if node.get(tag("ilvl")) == "0"
        )
        ppr = level.find(tag("pPr"))
        ind = ppr.find(tag("ind"))
        set_attributes(ind, {
            "left": "1440", "right": "120", "hanging": "360", "hangingChars": "180",
        })
        number = ET.SubElement(numbering, tag("num"), {tag("numId"): "91"})
        element(number, "abstractNumId", {"val": "0"})
        return ET.tostring(numbering, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def add_run(paragraph, text, *, target=False):
        run = element(paragraph, "r")
        props = element(run, "rPr")
        if target:
            element(props, "rStyle", {"val": "CharacterOverlay"})
            element(props, "rFonts", {
                "ascii": "Original Latin",
                "hAnsi": "Original Latin",
                "eastAsia": "Original Asia",
                "cs": "Original Complex",
                "asciiTheme": "minorHAnsi",
                "hAnsiTheme": "majorHAnsi",
                "eastAsiaTheme": "majorEastAsia",
                "cstheme": "minorBidi",
                "hint": "eastAsia",
            })
            element(props, "b", {"val": "0"})
            element(props, "i", {"val": "1"})
            element(props, "color", {
                "val": "112233", "themeColor": "accent2", "themeTint": "33",
            })
            element(props, "sz", {"val": "20"})
            element(props, "szCs", {"val": "22"})
            element(props, "lang", {"val": "en-GB", "eastAsia": "zh-CN"})
        else:
            element(props, "rStyle", {"val": "CharacterOverlay"})
            element(props, "rFonts", {
                "ascii": "Untouched Latin", "eastAsia": "Untouched Asia",
            })
            element(props, "b", {"val": "1"})
            element(props, "color", {"val": "445566", "themeColor": "accent4"})
            element(props, "sz", {"val": "18"})
            element(props, "szCs", {"val": "18"})
        element(run, "t", text=text)
        return run

    def add_known_paragraphs(self, data):
        document = ET.fromstring(data)
        body = document.find(f"{tag('body')}")
        section = body.find(tag("sectPr"))
        self.assertIsNotNone(section)
        for child in list(body):
            if child is not section:
                body.remove(child)

        fixed = ET.Element(tag("p"))
        fixed_ppr = element(fixed, "pPr")
        element(fixed_ppr, "pStyle", {"val": "DerivedParagraph"})
        element(fixed_ppr, "keepNext", {"val": "1"})
        fixed_tabs = element(fixed_ppr, "tabs")
        element(fixed_tabs, "tab", {"val": "left", "pos": "720"})
        element(fixed_tabs, "tab", {"val": "right", "pos": "8640"})
        element(fixed_ppr, "spacing", {
            "before": "300", "after": "180", "line": "360", "lineRule": "exact",
        })
        element(fixed_ppr, "ind", {
            "firstLine": "360", "firstLineChars": "125", "left": "720", "right": "240",
        })
        element(fixed_ppr, "jc", {"val": "center"})
        mark_props = element(fixed_ppr, "rPr")
        element(mark_props, "color", {"val": "ABCDEF"})
        self.add_run(fixed, "目标 Latin", target=True)
        self.add_run(fixed, "保留文字")

        numbered = ET.Element(tag("p"))
        numbered_ppr = element(numbered, "pPr")
        element(numbered_ppr, "pStyle", {"val": "BaseParagraph"})
        num_props = element(numbered_ppr, "numPr")
        element(num_props, "ilvl", {"val": "0"})
        element(num_props, "numId", {"val": "91"})
        tabs = element(numbered_ppr, "tabs")
        element(tabs, "tab", {"val": "left", "pos": "1080"})
        element(numbered_ppr, "spacing", {
            "beforeAutospacing": "1", "afterAutospacing": "1",
            "line": "276", "lineRule": "auto",
        })
        element(numbered_ppr, "ind", {
            "left": "1080", "right": "120", "hanging": "360", "hangingChars": "200",
        })
        element(numbered_ppr, "jc", {"val": "left"})
        self.add_run(numbered, "编号内容")
        body.insert(0, fixed)
        body.insert(1, numbered)
        return ET.tostring(document, encoding="utf-8", xml_declaration=True)

    def format_source(self, name="source.docx"):
        path = self.generated_document(name)
        parts = self.members(path)
        self.assertIn("word/numbering.xml", parts)
        parts["word/styles.xml"] = self.add_known_styles(parts["word/styles.xml"])
        parts["word/numbering.xml"] = self.add_known_numbering(parts["word/numbering.xml"])
        parts["word/document.xml"] = self.add_known_paragraphs(parts["word/document.xml"])
        self.write_members(path, parts)
        self.track_input(path)
        return path

    def document_root(self, path):
        return ET.fromstring(self.members(path)["word/document.xml"])

    def paragraphs(self, path):
        body = self.document_root(path).find(tag("body"))
        return body.findall(tag("p"))

    @staticmethod
    def target_run(document, paragraph_index=0):
        paragraphs = document.find(tag("body")).findall(tag("p"))
        return paragraphs[paragraph_index].findall(tag("r"))[0]

    def non_target_run_semantics(self, path):
        runs = self.paragraphs(path)[0].findall(tag("r"))
        return semantic_element(runs[1])

    def target_run_semantics(self, path, paragraph_index=0, ignored_font_attrs=()):
        run = self.target_run(self.document_root(path), paragraph_index)
        props = run.find(tag("rPr"))
        if props is not None:
            fonts = props.find(tag("rFonts"))
            if fonts is not None:
                for name in ignored_font_attrs:
                    fonts.attrib.pop(tag(name), None)
                if not fonts.attrib:
                    props.remove(fonts)
            if not list(props) and not props.attrib:
                run.remove(props)
        return semantic_element(run)

    def paragraph_properties_semantics(self, path, paragraph_index):
        paragraph = self.paragraphs(path)[paragraph_index]
        return semantic_element(paragraph.find(tag("pPr")))

    def document_text(self, path):
        root = self.document_root(path)
        return "".join(node.text or "" for node in root.iter(tag("t")))

    def document_semantics(self, path, ignored_font_attrs=(), ignored_size=False):
        document = self.document_root(path)
        run = self.target_run(document)
        props = run.find(tag("rPr"))
        fonts = props.find(tag("rFonts"))
        for name in ignored_font_attrs:
            fonts.attrib.pop(tag(name), None)
        if ignored_size:
            for name in ("sz", "szCs"):
                props.find(tag(name)).attrib.pop(tag("val"), None)
        return semantic_element(document)

    def assert_known_fixture_values(self, source):
        document = self.document_root(source)
        paragraphs = document.find(tag("body")).findall(tag("p"))
        self.assertEqual(len(paragraphs), 2)
        fixed_ppr = paragraphs[0].find(tag("pPr"))
        numbered_ppr = paragraphs[1].find(tag("pPr"))
        self.assertEqual(attributes(fixed_ppr.find(tag("pStyle"))), {
            "val": "DerivedParagraph",
        })
        self.assertEqual(attributes(fixed_ppr.find(tag("ind"))), {
            "firstLine": "360", "firstLineChars": "125", "left": "720", "right": "240",
        })
        self.assertEqual(attributes(fixed_ppr.find(tag("spacing"))), {
            "before": "300", "after": "180", "line": "360", "lineRule": "exact",
        })
        self.assertEqual(
            [attributes(tab) for tab in fixed_ppr.find(tag("tabs")).findall(tag("tab"))],
            [{"val": "left", "pos": "720"}, {"val": "right", "pos": "8640"}],
        )
        self.assertEqual(attributes(numbered_ppr.find(tag("spacing"))), {
            "beforeAutospacing": "1", "afterAutospacing": "1",
            "line": "276", "lineRule": "auto",
        })
        self.assertEqual(attributes(numbered_ppr.find(tag("ind"))), {
            "left": "1080", "right": "120", "hanging": "360", "hangingChars": "200",
        })
        self.assertEqual(attributes(numbered_ppr.find(f"{tag('numPr')}/{tag('numId')}")), {
            "val": "91",
        })
        target = self.target_run(document)
        self.assertEqual(attributes(target.find(f"{tag('rPr')}/{tag('rStyle')}")), {
            "val": "CharacterOverlay",
        })
        self.assertEqual(attributes(target.find(f"{tag('rPr')}/{tag('rFonts')}")), {
            "ascii": "Original Latin", "hAnsi": "Original Latin", "eastAsia": "Original Asia",
            "cs": "Original Complex", "asciiTheme": "minorHAnsi", "hAnsiTheme": "majorHAnsi",
            "eastAsiaTheme": "majorEastAsia", "cstheme": "minorBidi", "hint": "eastAsia",
        })

        parts = self.members(source)
        styles = ET.fromstring(parts["word/styles.xml"])
        defaults = styles.find(tag("docDefaults"))
        run_defaults = defaults.find(f"{tag('rPrDefault')}/{tag('rPr')}")
        self.assertEqual(attributes(run_defaults.find(tag("rFonts"))), {
            "ascii": "Defaults Latin", "hAnsi": "Defaults Latin",
            "eastAsia": "Defaults Asia", "cs": "Defaults Complex",
        })
        self.assertEqual(attributes(defaults.find(
            f"{tag('pPrDefault')}/{tag('pPr')}/{tag('spacing')}"
        )), {"before": "90", "after": "70", "line": "276", "lineRule": "auto"})
        style_map = {
            node.get(tag("styleId")): node
            for node in styles.findall(tag("style"))
        }
        self.assertEqual(style_map["DerivedParagraph"].find(tag("basedOn")).get(tag("val")),
                         "BaseParagraph")
        self.assertEqual(style_map["BaseParagraph"].find(tag("basedOn")).get(tag("val")), "Normal")
        self.assertEqual(style_map["CharacterOverlay"].find(tag("basedOn")).get(tag("val")),
                         "DefaultParagraphFont")

        numbering = ET.fromstring(parts["word/numbering.xml"])
        abstract = next(
            node for node in numbering.findall(tag("abstractNum"))
            if node.get(tag("abstractNumId")) == "0"
        )
        level = next(node for node in abstract.findall(tag("lvl"))
                     if node.get(tag("ilvl")) == "0")
        self.assertEqual(attributes(level.find(f"{tag('pPr')}/{tag('ind')}")), {
            "left": "1440", "right": "120", "hanging": "360", "hangingChars": "180",
        })
        self.assertIsNotNone(document.find(f"{tag('body')}/{tag('sectPr')}"))

    def assert_non_target_members_identical(self, source, output):
        before = self.members(source)
        after = self.members(output)
        self.assertEqual(set(before), set(after))
        for name in before:
            if name != "word/document.xml":
                self.assertEqual(before[name], after[name], name)

    def assert_rejected(self, source, specification, output_name, *, existing=True):
        self.track_input(source)
        output = Path(output_name)
        sentinel = b"existing output must survive a rejected edit"
        if existing:
            output.write_bytes(sentinel)
        with self.assertRaises(ValueError):
            edit_structure(source, output, specification)
        self.assertEqual(hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                         self.input_hashes[Path(source).resolve()])
        if existing:
            self.assertEqual(output.read_bytes(), sentinel)
        else:
            self.assertFalse(output.exists())

    def test_font_and_size_only_matrix_preserves_document_semantics(self):
        source = self.format_source()
        self.assert_known_fixture_values(source)
        baseline_fonts = attributes(self.target_run(self.document_root(source)).find(
            f"{tag('rPr')}/{tag('rFonts')}"
        ))
        cases = (
            (
                "east-asian",
                {"font_east_asia": "Requested Asia"},
                {"eastAsia": "Requested Asia"},
                ("eastAsia", "eastAsiaTheme"),
                False,
            ),
            (
                "latin",
                {"font_latin": "Requested Latin"},
                {"ascii": "Requested Latin", "hAnsi": "Requested Latin"},
                ("ascii", "hAnsi", "asciiTheme", "hAnsiTheme"),
                False,
            ),
            (
                "both-fonts",
                {"font_east_asia": "Requested Asia", "font_latin": "Requested Latin"},
                {
                    "eastAsia": "Requested Asia", "ascii": "Requested Latin",
                    "hAnsi": "Requested Latin",
                },
                (
                    "eastAsia", "ascii", "hAnsi", "eastAsiaTheme", "asciiTheme", "hAnsiTheme",
                ),
                False,
            ),
            ("size", {"size_pt": 13.5}, {}, (), True),
        )
        for case, formatting, updates, removed, size_only in cases:
            with self.subTest(case=case):
                output = Path(f"{case}.docx")
                specification = {"operations": [{
                    "type": "format_run", "paragraph_index": 0,
                    "expected_text": "目标 Latin保留文字", "run_index": 0,
                    "expected_run_text": "目标 Latin", "format": formatting,
                }]}
                edit_structure(source, output, specification)
                root = self.document_root(output)
                target = self.target_run(root)
                props = target.find(tag("rPr"))
                expected_fonts = dict(baseline_fonts)
                for name in removed:
                    expected_fonts.pop(name)
                expected_fonts.update(updates)
                self.assertEqual(attributes(props.find(tag("rFonts"))), expected_fonts)
                expected_size = "27" if size_only else "20"
                expected_complex_size = "27" if size_only else "22"
                self.assertEqual(attributes(props.find(tag("sz"))), {"val": expected_size})
                self.assertEqual(attributes(props.find(tag("szCs"))), {
                    "val": expected_complex_size,
                })
                ignored_fonts = removed + tuple(updates)
                self.assertEqual(
                    self.document_semantics(source, ignored_fonts, size_only),
                    self.document_semantics(output, ignored_fonts, size_only),
                )
                self.assertEqual(
                    self.non_target_run_semantics(source),
                    self.non_target_run_semantics(output),
                )
                self.assert_non_target_members_identical(source, output)

    def make_variant(self, name, kind):
        path = self.format_source(name)
        parts = self.members(path)
        document = ET.fromstring(parts["word/document.xml"])
        paragraph = document.find(f"{tag('body')}/{tag('p')}")
        if kind == "bookmark":
            paragraph.insert(1, ET.Element(tag("bookmarkStart"), {
                tag("id"): "7", tag("name"): "ProtectedAnchor",
            }))
            paragraph.append(ET.Element(tag("bookmarkEnd"), {tag("id"): "7"}))
        else:
            runs = paragraph.findall(tag("r"))
            for run in runs:
                paragraph.remove(run)
            begin = element(paragraph, "r")
            element(begin, "fldChar", {"fldCharType": "begin"})
            instruction = element(paragraph, "r")
            element(instruction, "instrText", text=" REF fixture ").set(
                f"{{{XML}}}space", "preserve"
            )
            separate = element(paragraph, "r")
            element(separate, "fldChar", {"fldCharType": "separate"})
            paragraph.append(runs[0])
            finish = element(paragraph, "r")
            element(finish, "fldChar", {"fldCharType": "end"})
            paragraph.append(runs[1])
        parts["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        self.write_members(path, parts)
        self.track_input(path, replace=True)
        return path

    def test_unsupported_format_and_unknown_operation_are_atomic(self):
        source = self.format_source()
        base = {
            "paragraph_index": 0, "expected_text": "目标 Latin保留文字",
            "run_index": 0, "expected_run_text": "目标 Latin",
        }
        self.assert_rejected(source, {"operations": [{
            "type": "format_run", **base, "format": {"font_symbol": "Unsupported"},
        }]}, "unsupported-format.docx")
        self.assert_rejected(source, {"operations": [{
            "type": "rewrite_run", **base, "format": {"size_pt": 14},
        }]}, "unknown-operation.docx", existing=False)

    def test_bookmarked_and_field_containing_anchors_are_rejected(self):
        operation = {
            "type": "format_run", "paragraph_index": 0,
            "expected_text": "目标 Latin保留文字", "run_index": 0,
            "expected_run_text": "目标 Latin", "format": {"size_pt": 14},
        }
        for kind in ("bookmark", "field"):
            with self.subTest(kind=kind):
                source = self.make_variant(f"{kind}.docx", kind)
                self.assert_rejected(
                    source, {"operations": [operation]}, f"rejected-{kind}.docx"
                )

    def test_table_only_target_is_rejected_without_publishing(self):
        source = self.generated_document("table-only.docx", [{
            "type": "table", "rows": [["Header"], ["Value"]], "header": True,
        }])
        self.assert_rejected(source, {"operations": [{
            "type": "format_run", "paragraph_index": 0, "expected_text": "Value",
            "run_index": 0, "expected_run_text": "Value", "format": {"size_pt": 14},
        }]}, "table-only-rejected.docx")

    def test_valid_operation_before_bookmarked_target_does_not_publish_partial_output(self):
        source = self.make_variant("batch-source.docx", "bookmark")
        specification = {"operations": [
            {
                "type": "format_run", "paragraph_index": 1,
                "expected_text": "编号内容", "run_index": 0,
                "expected_run_text": "编号内容", "format": {"font_latin": "Would Change"},
            },
            {
                "type": "format_run", "paragraph_index": 0,
                "expected_text": "目标 Latin保留文字", "run_index": 0,
                "expected_run_text": "目标 Latin", "format": {"size_pt": 14},
            },
        ]}
        self.assert_rejected(source, specification, "batch-rejected.docx")

    def test_inherited_runs_and_numbering_indents_survive_targeted_formats(self):
        cases = (
            ("no-rpr", 0, "font_east_asia", "Inherited Asia", "rpr"),
            ("rstyle-no-rfonts", 0, "font_latin", "Inherited Latin", "rfonts"),
            ("numbered-indents", 1, "font_east_asia", "Numbered Asia", "ind"),
        )
        for name, paragraph_index, field, value, removal in cases:
            with self.subTest(case=name):
                source = self.format_source(f"{name}.docx")
                parts = self.members(source)
                document = ET.fromstring(parts["word/document.xml"])
                paragraphs = document.find(tag("body")).findall(tag("p"))
                paragraph = paragraphs[paragraph_index]
                run = paragraph.findall(tag("r"))[0]
                if removal == "rpr":
                    run.remove(run.find(tag("rPr")))
                elif removal == "rfonts":
                    run.find(tag("rPr")).remove(run.find(f"{tag('rPr')}/{tag('rFonts')}"))
                else:
                    paragraph.find(tag("pPr")).remove(
                        paragraph.find(f"{tag('pPr')}/{tag('ind')}")
                    )
                parts["word/document.xml"] = ET.tostring(
                    document, encoding="utf-8", xml_declaration=True
                )
                self.write_members(source, parts)
                self.track_input(source, replace=True)
                before_text = self.document_text(source)
                before_ppr = self.paragraph_properties_semantics(source, paragraph_index)
                format_spec = {field: value}
                operation = {
                    "type": "format_run", "paragraph_index": paragraph_index,
                    "expected_text": "目标 Latin保留文字" if paragraph_index == 0 else "编号内容",
                    "run_index": 0,
                    "expected_run_text": "目标 Latin" if paragraph_index == 0 else "编号内容",
                    "format": format_spec,
                }
                output = Path(f"{name}-edited.docx")
                edit_structure(source, output, {"operations": [operation]})
                self.assertEqual(self.document_text(output), before_text)
                self.assertEqual(
                    self.paragraph_properties_semantics(output, paragraph_index), before_ppr
                )
                ignored = ("eastAsia", "eastAsiaTheme") if field == "font_east_asia" else (
                    "ascii", "hAnsi", "asciiTheme", "hAnsiTheme",
                )
                self.assertEqual(
                    self.target_run_semantics(source, paragraph_index, ignored),
                    self.target_run_semantics(output, paragraph_index, ignored),
                )
                target = self.target_run(self.document_root(output), paragraph_index)
                fonts = target.find(f"{tag('rPr')}/{tag('rFonts')}")
                if field == "font_east_asia":
                    self.assertEqual(attributes(fonts).get("eastAsia"), value)
                    self.assertNotIn("eastAsiaTheme", attributes(fonts))
                else:
                    self.assertEqual(attributes(fonts), {
                        "ascii": value, "hAnsi": value,
                    })
                    self.assertEqual(
                        attributes(target.find(f"{tag('rPr')}/{tag('rStyle')}")),
                        {"val": "CharacterOverlay"},
                    )
                if name == "no-rpr":
                    self.assertEqual(attributes(fonts), {"eastAsia": value})
                if name == "numbered-indents":
                    target_ppr = self.paragraphs(output)[paragraph_index].find(tag("pPr"))
                    self.assertIsNone(target_ppr.find(tag("ind")))
                    self.assertEqual(
                        attributes(target_ppr.find(f"{tag('numPr')}/{tag('numId')}")),
                        {"val": "91"},
                    )
                self.assert_non_target_members_identical(source, output)

    def test_edit_chain_and_repeat_application_keep_prior_formatting(self):
        source = self.format_source()
        east_asia = Path("east-asian-first.docx")
        edit_structure(source, east_asia, {"operations": [{
            "type": "format_run", "paragraph_index": 0,
            "expected_text": "目标 Latin保留文字", "run_index": 0,
            "expected_run_text": "目标 Latin", "format": {"font_east_asia": "Chained Asia"},
        }]})
        self.track_input(east_asia)
        chained = Path("chained-size.docx")
        edit_structure(east_asia, chained, {"operations": [{
            "type": "format_run", "paragraph_index": 0,
            "expected_text": "目标 Latin保留文字", "run_index": 0,
            "expected_run_text": "目标 Latin", "format": {"size_pt": 15},
        }]})
        self.assertEqual(self.document_semantics(east_asia, (), True),
                         self.document_semantics(chained, (), True))
        target = self.target_run(self.document_root(chained))
        fonts = attributes(target.find(f"{tag('rPr')}/{tag('rFonts')}"))
        self.assertEqual(fonts["eastAsia"], "Chained Asia")
        self.assertNotIn("eastAsiaTheme", fonts)
        self.assertEqual(attributes(target.find(f"{tag('rPr')}/{tag('sz')}")), {"val": "30"})
        self.assert_non_target_members_identical(east_asia, chained)

        repeated = Path("repeated-size.docx")
        edit_structure(east_asia, repeated, {"operations": [{
            "type": "format_run", "paragraph_index": 0,
            "expected_text": "目标 Latin保留文字", "run_index": 0,
            "expected_run_text": "目标 Latin", "format": {"size_pt": 15},
        }]})
        self.assertEqual(self.document_semantics(chained), self.document_semantics(repeated))
        self.assert_non_target_members_identical(east_asia, repeated)

    @staticmethod
    def routing_config(record):
        return json.dumps([{"name": "commonParam", "value": json.dumps([
            {"name": "questionId", "value": record},
        ])}])

    def test_runner_cli_publishes_preserved_font_edit_and_complete_receipt(self):
        source = self.format_source("runner-source.docx")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        specification = {"operations": [{
            "type": "format_run", "paragraph_index": 0,
            "expected_text": "目标 Latin保留文字", "run_index": 0,
            "expected_run_text": "目标 Latin", "format": {"font_east_asia": "CLI Asia"},
        }]}
        environment = dict(os.environ, PATH="")
        environment["DOCX_DEBUG_ROUTER_CONFIG"] = "0"
        environment["ROUTER_CONFIG_VARIABLES"] = self.routing_config(
            "format-preservation-cli"
        )
        process = subprocess.run(
            [
                sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "run_docx_edit.py"),
                "structure", str(source), "runner-edited.docx",
            ],
            cwd=self.work,
            env=environment,
            input=json.dumps(specification, ensure_ascii=False),
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "edited", payload)
        self.assertEqual(payload["task_status"], "completed", payload)
        self.assertTrue(payload["cleanup"]["cleanup_complete"], payload)
        self.assertEqual(payload["output"], "runner-edited.docx")
        output = Path(payload["output"])
        self.assertTrue(output.is_file())
        root = self.document_root(output)
        target = self.target_run(root)
        fonts = attributes(target.find(f"{tag('rPr')}/{tag('rFonts')}"))
        self.assertEqual(fonts["eastAsia"], "CLI Asia")
        self.assertNotIn("eastAsiaTheme", fonts)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(
            self.document_semantics(source, ("eastAsia", "eastAsiaTheme")),
            self.document_semantics(output, ("eastAsia", "eastAsiaTheme")),
        )
        self.assert_non_target_members_identical(source, output)


if __name__ == "__main__":
    unittest.main()
