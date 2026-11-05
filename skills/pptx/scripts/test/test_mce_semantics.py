"""Scoped MCE readback, independent semantic copies and raw-tree safety."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(SCRIPTS)]

from pptx_core.compatibility import CompatibilityContext
from pptx_core.validation import (
    A14_NS, A_NS, MC_NS, MATH_NS, SHEET_NS, ValidationError,
    _semantic_children, _semantic_elements, _semantic_tree,
    inspect_presentation, safe_xml, validate_presentation,
)
from test_package import read_parts, slide_xml
from test_v680_inspection import alternate, choice, presentation, text_shape
from test_validation import zipped


def parse(contents: str, attributes: str = "", *, context: CompatibilityContext | None = None) -> tuple[ET.Element, CompatibilityContext]:
    if context is None:
        context = CompatibilityContext()
    document = (
        f'<a:root xmlns:a="{A_NS}" xmlns:mc="{MC_NS}" xmlns:a14="{A14_NS}" '
        f'xmlns:m="{MATH_NS}" xmlns:x="urn:future" xmlns:y="urn:other" {attributes}>{contents}</a:root>'
    )
    return safe_xml(document.encode("utf-8"), understood_choices=context), context


def text_values(root: ET.Element, context: CompatibilityContext) -> list[str]:
    return [element.text or "" for element in _semantic_elements(root, context) if element.tag in ("{" + A_NS + "}t", "{" + MATH_NS + "}t")]


class ScopedCompatibilityTests(unittest.TestCase):
    def test_ignorable_element_drops_its_whole_subtree_without_mutating_raw_xml(self) -> None:
        root, context = parse('<a:t>visible</a:t><x:wrapper><a:t>hidden</a:t></x:wrapper>', 'mc:Ignorable="x"')
        original = ET.tostring(root)
        self.assertEqual(text_values(root, context), ["visible"])
        self.assertEqual([element.text for element in root.iter("{" + A_NS + "}t")], ["visible", "hidden"])
        projected = _semantic_tree(root, context)
        self.assertEqual([element.tag for element in projected], ["{" + A_NS + "}t"])
        self.assertEqual(ET.tostring(root), original)

    def test_process_content_promotes_only_named_wrappers_and_keeps_order(self) -> None:
        contents = '<a:t>before</a:t><x:keep><a:t>inside</a:t><x:drop><a:t>hidden</a:t></x:drop></x:keep><a:t>after</a:t>'
        root, context = parse(contents, 'mc:Ignorable="x" mc:ProcessContent="x:keep"')
        self.assertEqual(text_values(root, context), ["before", "inside", "after"])
        self.assertEqual([node.text for node in _semantic_children(root, context)], ["before", "inside", "after"])
        self.assertEqual([node.text for node in _semantic_tree(root, context)], ["before", "inside", "after"])

    def test_wildcard_and_nested_process_content_rules_are_inherited(self) -> None:
        contents = (
            '<a:g mc:ProcessContent="y:keep"><x:first><a:t>first</a:t><y:keep><a:t>second</a:t></y:keep></x:first>'
            '<y:drop><a:t>hidden</a:t></y:drop><x:last><a:t>last</a:t></x:last></a:g>'
        )
        root, context = parse(contents, 'mc:Ignorable="x y" mc:ProcessContent="x:*"')
        self.assertEqual(text_values(root, context), ["first", "second", "last"])

    def test_process_content_adds_to_ancestor_names_in_the_same_namespace(self) -> None:
        contents = (
            '<a:g mc:ProcessContent="x:second"><x:first><a:t>inherited</a:t></x:first>'
            '<x:second><a:t>local</a:t></x:second></a:g><x:second><a:t>outside scope</a:t></x:second>'
        )
        root, context = parse(contents, 'mc:Ignorable="x" mc:ProcessContent="x:first"')
        self.assertEqual(text_values(root, context), ["inherited", "local"])

    def test_prefix_rebinding_does_not_retarget_ancestor_rules(self) -> None:
        contents = (
            '<a:g xmlns:x="urn:other" xmlns:old="urn:future" mc:Ignorable="x">'
            '<x:keep><a:t>new namespace hidden</a:t></x:keep><old:keep><a:t>old namespace promoted</a:t></old:keep></a:g>'
            '<x:keep><a:t>sibling restored</a:t></x:keep>'
        )
        root, context = parse(contents, 'mc:Ignorable="x" mc:ProcessContent="x:keep"')
        self.assertEqual(text_values(root, context), ["old namespace promoted", "sibling restored"])

    def test_local_ignorable_declarations_do_not_leak_to_siblings_or_other_parts(self) -> None:
        contents = (
            '<a:g mc:Ignorable="x"><x:wrapper><a:t>hidden</a:t></x:wrapper></a:g>'
            '<x:wrapper><a:t>sibling without Ignorable</a:t></x:wrapper>'
        )
        root, context = parse(contents)
        self.assertEqual(text_values(root, context), ["sibling without Ignorable"])
        other, _context = parse('<x:wrapper><a:t>next part</a:t></x:wrapper>', context=context)
        self.assertEqual(text_values(other, context), ["next part"])

    def test_understood_namespaces_remain_visible_when_declared_ignorable(self) -> None:
        contents = '<a14:m><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></a14:m><a:t>text</a:t>'
        root, context = parse(contents, 'mc:Ignorable="a14 a m" mc:MustUnderstand="a14 a m"')
        self.assertEqual(text_values(root, context), ["x", "text"])
        self.assertIn("{" + A14_NS + "}m", [element.tag for element in _semantic_elements(root, context)])

    def test_must_understand_resolves_current_prefix_bindings_and_fails_explicitly(self) -> None:
        documents = (
            ('<a:t>body</a:t>', 'mc:MustUnderstand="x"'),
            ('<a:g xmlns:a14="urn:future" mc:MustUnderstand="a14"/>', 'mc:MustUnderstand="a14"'),
            ('<a:g mc:MustUnderstand="missing"/>', ""),
        )
        for contents, attributes in documents:
            with self.subTest(contents=contents, attributes=attributes), self.assertRaisesRegex(ValidationError, "mc:MustUnderstand"):
                parse(contents, attributes)
        root, context = parse('<a:t>body</a:t>', 'mc:MustUnderstand="  "')
        self.assertEqual(text_values(root, context), ["body"])

    def test_must_understand_is_checked_only_in_the_active_semantic_content(self) -> None:
        contents = (
            '<x:drop mc:MustUnderstand="x"><a:g mc:MustUnderstand="y"><a:t>hidden</a:t></a:g></x:drop>'
            '<mc:AlternateContent><mc:Choice Requires="x" mc:MustUnderstand="x"><a:t>unknown</a:t></mc:Choice>'
            '<mc:Choice Requires="a14"><a:t>selected</a:t></mc:Choice>'
            '<mc:Fallback mc:MustUnderstand="y"><a:t>fallback</a:t></mc:Fallback></mc:AlternateContent>'
        )
        root, context = parse(contents, 'mc:Ignorable="x"')
        self.assertEqual(text_values(root, context), ["selected"])
        active_requirements = (
            '<x:keep mc:MustUnderstand="x"><a:t>body</a:t></x:keep>',
            '<x:keep><a:g mc:MustUnderstand="y"/></x:keep>',
            '<mc:AlternateContent mc:MustUnderstand="x"><mc:Choice Requires="a14"/></mc:AlternateContent>',
            '<mc:AlternateContent><mc:Choice Requires="a14" mc:MustUnderstand="x"/></mc:AlternateContent>',
            '<mc:AlternateContent><mc:Choice Requires="x"/><mc:Fallback mc:MustUnderstand="y"/></mc:AlternateContent>',
        )
        for contents in active_requirements:
            with self.subTest(contents=contents), self.assertRaisesRegex(ValidationError, "MustUnderstand.*unsupported namespace"):
                parse(contents, 'mc:Ignorable="x" mc:ProcessContent="x:keep"')

    def test_semantic_copy_strips_controls_and_ignorable_attributes_with_text_tails_intact(self) -> None:
        contents = 'before<x:drop>hidden</x:drop>between<x:keep>start<a:t x:hint="drop" a14:hint="keep">inner</a:t>end</x:keep>after'
        root, context = parse(contents, 'mc:Ignorable="x a14" mc:ProcessContent="x:keep"')
        original = ET.tostring(root)
        projected = _semantic_tree(root, context)
        self.assertEqual(projected.attrib, {})
        self.assertEqual(projected.text, "beforebetweenstart")
        self.assertEqual(projected[0].text, "inner")
        self.assertEqual(projected[0].tail, "endafter")
        self.assertEqual(projected[0].attrib, {"{" + A14_NS + "}hint": "keep"})
        self.assertEqual(text_values(projected, context), ["inner"])
        projected[0].text = "changed copy"
        self.assertEqual(ET.tostring(root), original)

    def test_semantic_copy_flattens_nested_alternate_content_inside_process_content(self) -> None:
        nested = alternate(choice('<a:t>chosen</a:t>'), '<a:t>fallback</a:t>')
        root, context = parse(f'<x:keep>{nested}</x:keep>', 'mc:Ignorable="x" mc:ProcessContent="x:keep"')
        projected = _semantic_tree(root, context)
        self.assertEqual([element.tag for element in projected.iter()], ["{" + A_NS + "}root", "{" + A_NS + "}t"])
        self.assertEqual(text_values(projected, context), ["chosen"])

    def test_invalid_or_unimplemented_mce_controls_fail_clearly(self) -> None:
        invalid_attributes = (
            ('mc:ProcessContent="x:keep"', "Ignorable namespace"),
            ('mc:Ignorable="x" mc:ProcessContent="missing:keep"', "undeclared namespace"),
            ('mc:Ignorable="x" mc:ProcessContent="keep"', "namespace-qualified"),
            ('mc:Ignorable="x" mc:ProcessContent="x:bad:name"', "namespace-qualified"),
            ('mc:Ignorable="mc"', "MCE namespace"),
            ('mc:MustUnderstand="mc"', "MCE namespace"),
            ('mc:PreserveElements="x:keep"', "unsupported MCE attribute mc:PreserveElements"),
            ('mc:PreserveAttributes="x:*"', "unsupported MCE attribute mc:PreserveAttributes"),
            ('mc:FutureControl="x"', "unsupported MCE attribute"),
        )
        for attributes, message in invalid_attributes:
            with self.subTest(attributes=attributes), self.assertRaisesRegex(ValidationError, message):
                parse("", attributes)
        for name in ("base", "lang", "space"):
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, "ProcessContent wrapper"):
                parse(f'<x:keep xml:{name}="value"/>', 'mc:Ignorable="x" mc:ProcessContent="x:keep"')

    def test_svg_and_workbook_base_namespaces_are_accepted(self) -> None:
        for namespace, tag in (("http://www.w3.org/2000/svg", "svg"), (SHEET_NS, "workbook")):
            document = f'<{tag} xmlns="{namespace}" xmlns:k="{namespace}" xmlns:mc="{MC_NS}" mc:Ignorable="k" mc:MustUnderstand="k"/>'
            context = CompatibilityContext()
            root = safe_xml(document.encode(), understood_choices=context)
            self.assertEqual(list(_semantic_elements(root, context)), [root])

    def test_legacy_choice_set_remains_usable(self) -> None:
        choices: set[ET.Element] = set()
        document = alternate(choice('<item value="selected"/>'), '<item value="fallback"/>')
        root = safe_xml(document.encode(), understood_choices=choices)
        self.assertEqual(len(choices), 1)
        self.assertEqual([node.get("value") for node in _semantic_elements(root, choices)], ["selected"])

    def test_ignored_content_still_consumes_xml_node_and_depth_limits(self) -> None:
        contents = '<x:drop><a:g><a:t>hidden</a:t></a:g></x:drop>'
        for limit in ("MAX_XML_NODES", "MAX_XML_DEPTH"):
            with self.subTest(limit=limit), patch("pptx_core.validation." + limit, 3), self.assertRaisesRegex(ValidationError, "node or depth limit"):
                parse(contents, 'mc:Ignorable="x"')


class CompatibilityPackageSafetyTests(unittest.TestCase):
    def test_discarded_and_alternative_drawing_ids_do_not_collide_with_retained_ids(self) -> None:
        parts = read_parts(presentation(text_shape(2, "selected")))
        cases = (
            alternate(choice(text_shape(2, "selected")), text_shape(2, "fallback")),
            text_shape(2, "selected") + f'<x:drop xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x">{text_shape(2, "hidden")}</x:drop>',
        )
        for shapes in cases:
            with self.subTest(shapes=shapes[:70]):
                parts["ppt/slides/slide1.xml"] = slide_xml(shapes).encode()
                slide = inspect_presentation(zipped(parts))["slides"][0]
                self.assertEqual(slide["text"], ["selected"])
                self.assertEqual(slide["objects"]["shapes"], 1)

    def test_retained_drawing_ids_still_must_be_unique(self) -> None:
        parts = read_parts(presentation(text_shape(2, "first")))
        duplicate = text_shape(2, "first") + (
            f'<x:keep xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x" mc:ProcessContent="x:keep">'
            f'{text_shape(2, "second")}</x:keep>'
        )
        parts["ppt/slides/slide1.xml"] = slide_xml(duplicate).encode()
        with self.assertRaisesRegex(ValidationError, "duplicate drawing object IDs"):
            validate_presentation(zipped(parts))

    def test_process_content_mixed_text_is_preserved_in_slide_math_and_notes(self) -> None:
        wrapper = f'<x:keep xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x" mc:ProcessContent="x:keep">'
        text = f'before{wrapper}inside</x:keep>after'
        math = f'<m:oMath xmlns:m="{MATH_NS}"><m:r><m:t>x{wrapper}+</x:keep>1</m:t></m:r></m:oMath>'
        body = f'<a:t>{text}</a:t>' + alternate(choice(math), '<a:t>preview</a:t>')
        shape = text_shape(2, "body").replace("<a:t>body</a:t>", body)
        parts = read_parts(presentation(shape, notes="note"))
        notes = parts["ppt/notesSlides/notesSlide1.xml"].decode()
        self.assertIn("note</a:t>", notes)
        parts["ppt/notesSlides/notesSlide1.xml"] = notes.replace("note</a:t>", text + "</a:t>").encode()
        slide = inspect_presentation(zipped(parts))["slides"][0]
        self.assertEqual(slide["text"], ["beforeinsideafterx+1"])
        self.assertEqual(slide["notes"], ["beforeinsideafter"])

    def test_inspection_text_and_object_counts_use_scoped_ignorable_rules(self) -> None:
        contents = (
            f'<x:outer xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x" mc:ProcessContent="x:outer">'
            f'{text_shape(2, "visible")}<x:drop>{text_shape(3, "hidden")}</x:drop></x:outer>'
        )
        slide = inspect_presentation(presentation(contents))["slides"][0]
        self.assertEqual(slide["text"], ["visible"])
        self.assertEqual(slide["objects"]["shapes"], 1)

    def test_every_inactive_location_still_rejects_unresolved_relationships(self) -> None:
        parts = read_parts(presentation(text_shape(2, "base")))
        for unsafe_id in (3, 4, 5):
            shapes = {identifier: text_shape(identifier, "safe") for identifier in (2, 3, 4, 5)}
            shapes[unsafe_id] = shapes[unsafe_id].replace(
                '<a:rPr lang="zh-CN" sz="2400"/>', '<a:rPr><a:hlinkClick r:id="missing"/></a:rPr>',
            )
            branches = choice(shapes[2]) + choice(shapes[3], "urn:future")
            contents = alternate(branches, shapes[4])
            contents += f'<x:drop xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x">{shapes[5]}</x:drop>'
            parts["ppt/slides/slide1.xml"] = slide_xml(contents).encode()
            with self.subTest(unsafe_id=unsafe_id), self.assertRaisesRegex(ValidationError, "unresolved relationship"):
                validate_presentation(zipped(parts))

    def test_ignored_content_still_rejects_dangerous_relationship_targets(self) -> None:
        shape = text_shape(3, "hidden").replace(
            '<a:rPr lang="zh-CN" sz="2400"/>', '<a:rPr><a:hlinkClick r:id="blocked"/></a:rPr>',
        )
        parts = read_parts(presentation(text_shape(2, "visible")))
        contents = text_shape(2, "visible") + f'<x:drop xmlns:x="urn:future" xmlns:mc="{MC_NS}" mc:Ignorable="x">{shape}</x:drop>'
        parts["ppt/slides/slide1.xml"] = slide_xml(contents).encode()
        relationships = parts["ppt/slides/_rels/slide1.xml.rels"].decode()
        dangerous = (
            '<Relationship Id="blocked" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="file:///tmp/dangerous" TargetMode="External"/>'
        )
        parts["ppt/slides/_rels/slide1.xml.rels"] = relationships.replace("</Relationships>", dangerous + "</Relationships>").encode()
        with self.assertRaisesRegex(ValidationError, "external hyperlink scheme"):
            validate_presentation(zipped(parts))


if __name__ == "__main__":
    unittest.main()
