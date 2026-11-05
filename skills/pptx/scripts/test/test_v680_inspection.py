"""MCE inspection and resource boundaries required by the v6.8 formula exporter."""

from __future__ import annotations

import sys
import re
import unittest
from pathlib import Path
from xml.sax.saxutils import escape


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "_engine"))

from pptx_core.package import ConvertedSlide, build_presentation
from pptx_core.runtime import TaskError, prepare_svg
from pptx_core.validation import ValidationError, inspect_presentation, safe_xml, validate_presentation
from test_package import SHAPE, read_parts, slide_xml
from test_validation import zipped


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SVG_NS = "http://www.w3.org/2000/svg"


def text_shape(identifier: int, text: str) -> str:
    return SHAPE.replace('id="2"', f'id="{identifier}"').replace("季度收入 &amp; Growth", text)


def alternate(choices: str, fallback: str | None = None) -> str:
    final = f"<mc:Fallback>{fallback}</mc:Fallback>" if fallback is not None else ""
    return f'<mc:AlternateContent xmlns:mc="{MC_NS}">{choices}{final}</mc:AlternateContent>'


def choice(contents: str, namespace: str = A14_NS, *, prefix: str = "extension", requires: str | None = None) -> str:
    return f'<mc:Choice xmlns:{prefix}="{namespace}" Requires="{requires or prefix}">{contents}</mc:Choice>'


def presentation(shapes: str, *, notes: str = "") -> bytes:
    return build_presentation([ConvertedSlide(slide_xml(shapes), notes=notes)])


class AlternateContentInspectionTests(unittest.TestCase):
    def test_first_supported_choice_is_counted_and_read_once(self) -> None:
        branches = choice(text_shape(2, "unknown"), "urn:future")
        branches += choice(text_shape(3, "selected"), prefix="alias")
        branches += choice(text_shape(4, "later supported"))
        data = presentation(alternate(branches, text_shape(5, "fallback")))
        slide = inspect_presentation(data)["slides"][0]
        self.assertEqual(slide["text"], ["selected"])
        self.assertEqual(slide["objects"], {"shapes": 1, "images": 0, "tables": 0, "charts": 0})

    def test_unknown_namespace_even_with_familiar_prefix_uses_fallback(self) -> None:
        branches = choice(text_shape(2, "unknown"), "urn:future", prefix="a14")
        data = presentation(alternate(branches, text_shape(3, "fallback")))
        self.assertEqual(inspect_presentation(data)["slides"][0]["text"], ["fallback"])

    def test_nested_choice_rebinding_respects_each_namespace_scope(self) -> None:
        nested = alternate(choice(text_shape(2, "wrong"), "urn:future", prefix="shared"), text_shape(3, "nested fallback"))
        outer = alternate(choice(nested, prefix="shared"), text_shape(4, "outer fallback"))
        slide = inspect_presentation(presentation(outer))["slides"][0]
        self.assertEqual(slide["text"], ["nested fallback"])
        self.assertEqual(slide["objects"]["shapes"], 1)

    def test_all_required_namespaces_must_be_supported(self) -> None:
        branches = (
            f'<mc:Choice xmlns:known="{A14_NS}" xmlns:future="urn:future" Requires="known future">'
            f'{text_shape(2, "wrong")}</mc:Choice>'
        )
        data = presentation(alternate(branches, text_shape(3, "fallback")))
        self.assertEqual(inspect_presentation(data)["slides"][0]["text"], ["fallback"])

    def test_inline_math_and_notes_do_not_read_fallback_text(self) -> None:
        math = f'<m:oMath xmlns:m="{MATH_NS}"><m:r><m:t>x</m:t></m:r></m:oMath>'
        body = '<a:t>ratio </a:t>' + alternate(choice(math), '<a:t>preview x</a:t>') + '<a:t> holds</a:t>'
        shape = text_shape(2, "body").replace("<a:t>body</a:t>", body)
        parts = read_parts(presentation(shape, notes="note"))
        notes = parts["ppt/notesSlides/notesSlide1.xml"].decode("utf-8")
        notes, count = re.subn(
            r'<a:t(?: [^>]*)?>note</a:t>', alternate(choice('<a:t>selected note</a:t>'), '<a:t>duplicate note</a:t>'), notes,
        )
        self.assertEqual(count, 1)
        parts["ppt/notesSlides/notesSlide1.xml"] = notes.encode("utf-8")
        slide = inspect_presentation(zipped(parts))["slides"][0]
        self.assertEqual(slide["text"], ["ratio x holds"])
        self.assertEqual(slide["notes"], ["selected note"])

    def test_unselected_branches_are_still_structurally_validated(self) -> None:
        invalid = text_shape(3, "fallback").replace('<a:rPr lang="zh-CN" sz="2400"/>', '<a:rPr><a:hlinkClick r:id="missing"/></a:rPr>')
        parts = read_parts(presentation(text_shape(2, "selected")))
        parts["ppt/slides/slide1.xml"] = slide_xml(alternate(choice(text_shape(2, "selected")), invalid)).encode("utf-8")
        with self.assertRaisesRegex(ValidationError, "unresolved relationship"):
            validate_presentation(zipped(parts))
        invalid_id = alternate(choice(text_shape(2, "selected")), text_shape(0, "invalid ID"))
        parts["ppt/slides/slide1.xml"] = slide_xml(invalid_id).encode("utf-8")
        with self.assertRaisesRegex(ValidationError, "integer is outside the supported range"):
            validate_presentation(zipped(parts))

    def test_ambiguous_branches_and_unreadable_choices_fail_explicitly(self) -> None:
        bad_children = (
            "<mc:Fallback/>",
            choice("") + "<mc:Fallback/><mc:Fallback/>",
            "<mc:Fallback/>" + choice(""),
            choice("") + "<unexpected/>",
        )
        for contents in bad_children:
            with self.subTest(contents=contents), self.assertRaises(ValidationError):
                safe_xml(alternate(contents).encode("utf-8"))
        data = presentation(alternate(choice(text_shape(2, "future"), "urn:future")))
        with self.assertRaisesRegex(ValidationError, "no supported Choice or Fallback"):
            inspect_presentation(data)


class InlineFormulaBudgetTests(unittest.TestCase):
    def test_formula_and_preview_cost_is_reserved_before_conversion(self) -> None:
        svg = (
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
            '<text x="50" y="70"><tspan data-pptx-inline-formula="x">x</tspan></text></svg>'
        ).encode("utf-8")
        with self.assertRaises(TaskError) as failure:
            prepare_svg(svg, width=1280, height=720, native_objects=True, max_expanded_bytes=65536)
        self.assertEqual(failure.exception.code, "resource_limit")
        _text, cost = prepare_svg(svg, width=1280, height=720, native_objects=True, max_expanded_bytes=131072)
        self.assertGreater(cost, 65536)

    def test_long_and_macro_expanded_formulas_exceed_small_remaining_budget(self) -> None:
        formulas = ("x^2+" * 200 + "1", r"\newcommand{\term}{x^2+1}" + r"\term+" * 100 + "1")
        for latex in formulas:
            with self.subTest(latex_length=len(latex)):
                attribute = escape(latex, {'"': "&quot;"})
                svg = (
                    f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
                    f'<text x="50" y="70"><tspan data-pptx-inline-formula="{attribute}">preview</tspan></text></svg>'
                ).encode("utf-8")
                with self.assertRaises(TaskError) as failure:
                    prepare_svg(svg, width=1280, height=720, native_objects=True, max_expanded_bytes=131072)
                self.assertEqual(failure.exception.code, "resource_limit")
                _text, cost = prepare_svg(svg, width=1280, height=720, native_objects=True, max_expanded_bytes=32 * 1024 * 1024)
                self.assertGreater(cost, 310436)

    def test_inherited_typography_replication_is_budgeted(self) -> None:
        font = "F" * 50000
        svg = (
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
            f'<g font-family="{font}"><text x="50" y="70"><tspan data-pptx-inline-formula="x^2">x²</tspan></text></g></svg>'
        ).encode("utf-8")
        with self.assertRaises(TaskError) as failure:
            prepare_svg(svg, width=1280, height=720, native_objects=True, max_expanded_bytes=1024 * 1024)
        self.assertEqual(failure.exception.code, "resource_limit")


if __name__ == "__main__":
    unittest.main()
