"""Paragraph layout, tab-stop, literal-tab, and runner contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest("DOCX requires Python >=3.10")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from office.validators.generation import verify_generation
from read_docx import read_docx

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SCRIPT = Path(__file__).resolve().parent / "run_docx_task.py"


@unittest.skipUnless(expat.version_info >= (2, 6, 0), "DOCX requires Expat >=2.6")
class ParagraphLayoutTests(unittest.TestCase):
    def setUp(self):
        self.original_directory = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(dir=".")
        self.work = Path(self.temporary.name).resolve()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.original_directory)
        self.temporary.cleanup()

    def document_parts(self, path):
        with zipfile.ZipFile(path) as archive:
            return {
                name: ET.fromstring(archive.read(name))
                for name in ("word/document.xml", "word/styles.xml", "word/numbering.xml")
            }

    def paragraph(self, root, index=0):
        return root.find(W + "body").findall(W + "p")[index]

    def style(self, root, name):
        return next(node for node in root.findall(W + "style")
                    if node.get(W + "styleId") == name)

    def test_literal_tabs_roundtrip_and_tab_stops_do_not_add_text(self):
        spec = {
            "styles": {"body": {"tab_stops": [
                {"position_pt": 72, "align": "decimal", "leader": "dot"},
                {"position_pt": 36, "align": "center"},
                {"position_pt": 144, "align": "right", "leader": "underscore"},
                {"position_pt": 0, "align": "left"},
                {"position_pt": 1584, "align": "left"},
            ]}},
            "blocks": [
                {"type": "paragraph", "text": "No text tab", "format": {"tab_stops": [
                    {"position_pt": 18, "align": "right"},
                ]}},
                {"type": "paragraph", "text": "\talpha\t\t middle \t"},
                {"type": "paragraph", "text": ""},
                {"type": "paragraph", "runs": [
                    {"text": "lead\t", "bold": True},
                    {"text": "\t body "},
                    {"text": "\t"},
                    {"text": ""},
                ]},
            ],
        }
        create_docx.create_document(spec, "tabs.docx", verify=True)
        self.assertEqual(
            read_docx("tabs.docx"),
            "No text tab\n\talpha\t\t middle \t\n\nlead\t\t body \t",
        )

        parts = self.document_parts("tabs.docx")
        style_tabs = self.style(parts["word/styles.xml"], "BodyText").find(W + "pPr/" + W + "tabs")
        self.assertEqual(
            [(node.get(W + "pos"), node.get(W + "val"), node.get(W + "leader")) for node in style_tabs],
            [("0", "left", None), ("720", "center", None), ("1440", "decimal", "dot"),
             ("2880", "right", "underscore"), ("31680", "left", None)],
        )
        body_paragraphs = parts["word/document.xml"].find(W + "body").findall(W + "p")
        local_tabs = body_paragraphs[0].find(W + "pPr/" + W + "tabs")
        self.assertEqual([(node.get(W + "pos"), node.get(W + "val")) for node in local_tabs],
                         [("360", "right")])
        self.assertIsNone(body_paragraphs[1].find(W + "pPr/" + W + "tabs"))
        self.assertEqual(
            [child.tag for child in body_paragraphs[1].findall(".//" + W + "r")[0]],
            [W + "tab", W + "t", W + "tab", W + "tab", W + "t", W + "tab"],
        )
        run_nodes = body_paragraphs[3].findall(W + "r")
        self.assertEqual(
            [[child.tag for child in run if child.tag != W + "rPr"] for run in run_nodes],
            [[W + "t", W + "tab"], [W + "tab", W + "t"], [W + "tab"], [W + "t"]],
        )

    def test_paragraph_flags_indent_switching_and_fixed_property_order(self):
        default_spec = {"blocks": [
            {"type": "title", "text": "Default title"},
            {"type": "heading", "level": 1, "text": "Default heading"},
        ]}
        create_docx.create_document(default_spec, "default-keep-next.docx", verify=True)
        default_styles = self.document_parts("default-keep-next.docx")["word/styles.xml"]
        for name in ("Title", "Heading1"):
            implicit = self.style(default_styles, name).find(W + "pPr/" + W + "keepNext")
            self.assertIsNotNone(implicit)
            self.assertEqual(implicit.attrib, {})

        spec = {
            "styles": {
                "body": {
                    "keep_with_next": False,
                    "keep_together": True,
                    "page_break_before": False,
                    "widow_control": True,
                    "left_indent_pt": 9,
                    "right_indent_pt": 18,
                    "tab_stops": [
                        {"position_pt": 72, "align": "left"},
                        {"position_pt": 36, "align": "right", "leader": "hyphen"},
                    ],
                },
                "title": {"keep_with_next": False},
                "heading1": {"keep_with_next": False},
            },
            "blocks": [
                {"type": "title", "text": "Explicit title false",
                 "format": {"keep_with_next": False}},
                {"type": "heading", "level": 1, "text": "Explicit heading false",
                 "format": {"keep_with_next": False}},
                {"type": "paragraph", "text": "Local hanging overrides inherited first line", "format": {
                    "keep_with_next": False, "keep_together": True,
                    "page_break_before": False, "widow_control": True,
                    "hanging_indent_pt": 6, "space_before_pt": 2,
                    "tab_stops": [
                        {"position_pt": 72, "align": "clear"},
                        {"position_pt": 18, "align": "decimal", "leader": "dot"},
                    ],
                }},
                {"type": "paragraph", "text": "First line overrides inherited hanging",
                 "format": {"first_line_chars": 1}},
            ],
        }
        create_docx.create_document(spec, "layout.docx", verify=True)
        parts = self.document_parts("layout.docx")
        styles = parts["word/styles.xml"]
        document = parts["word/document.xml"]

        for name in ("Title", "Heading1"):
            style_flag = self.style(styles, name).find(W + "pPr/" + W + "keepNext")
            self.assertEqual(style_flag.attrib, {W + "val": "0"})
        for index in (0, 1):
            direct_flag = self.paragraph(document, index).find(W + "pPr/" + W + "keepNext")
            self.assertEqual(direct_flag.attrib, {W + "val": "0"})

        body_style = self.style(styles, "BodyText")
        body_ppr = body_style.find(W + "pPr")
        self.assertEqual([child.tag for child in body_ppr],
                         [W + "keepNext", W + "keepLines", W + "pageBreakBefore", W + "widowControl",
                          W + "tabs", W + "spacing", W + "ind", W + "jc"])
        self.assertEqual([child.attrib for child in list(body_ppr)[:4]], [
            {W + "val": "0"}, {W + "val": "1"}, {W + "val": "0"}, {W + "val": "1"},
        ])
        self.assertEqual(body_ppr.find(W + "ind").attrib, {
            W + "firstLineChars": "200", W + "left": "180", W + "right": "360",
        })
        style_tabs = body_ppr.find(W + "tabs")
        self.assertEqual([(item.get(W + "pos"), item.get(W + "val"), item.get(W + "leader"))
                          for item in style_tabs], [("720", "right", "hyphen"), ("1440", "left", None)])

        local = self.paragraph(document, 2).find(W + "pPr")
        self.assertEqual([child.tag for child in local], [
            W + "pStyle", W + "keepNext", W + "keepLines", W + "pageBreakBefore", W + "widowControl",
            W + "tabs", W + "spacing", W + "ind",
        ])
        self.assertEqual(local.find(W + "tabs").find(W + "tab").attrib,
                         {W + "val": "decimal", W + "pos": "360", W + "leader": "dot"})
        self.assertEqual(local.find(W + "tabs").findall(W + "tab")[1].attrib,
                         {W + "val": "clear", W + "pos": "1440"})
        self.assertEqual(local.find(W + "ind").attrib,
                         {W + "firstLineChars": "0", W + "hanging": "120"})
        first_line = self.paragraph(document, 3).find(W + "pPr/" + W + "ind")
        self.assertEqual(first_line.attrib, {W + "firstLineChars": "100"})

    def test_style_indent_mode_merge_and_explicit_local_reset(self):
        spec = {
            "styles": {"body": {"hanging_indent_pt": 12}},
            "blocks": [{"type": "paragraph", "text": "reset hanging", "format": {
                "first_line_chars": 2,
            }}],
        }
        create_docx.create_document(spec, "indent.docx", verify=True)
        parts = self.document_parts("indent.docx")
        style_ind = self.style(parts["word/styles.xml"], "BodyText").find(W + "pPr/" + W + "ind")
        self.assertEqual(style_ind.attrib, {W + "hanging": "240"})
        paragraph_ind = self.paragraph(parts["word/document.xml"]).find(W + "pPr/" + W + "ind")
        self.assertEqual(paragraph_ind.attrib, {W + "hanging": "0", W + "firstLineChars": "200"})

        zero_pairs = [
            {"first_line_chars": 0, "hanging_indent_pt": 12},
            {"first_line_chars": 2, "hanging_indent_pt": 0},
            {"first_line_chars": 0, "hanging_indent_pt": 0},
        ]
        for index, fields in enumerate(zero_pairs):
            with self.subTest(fields=fields):
                create_docx.create_document({"blocks": [{"type": "paragraph", "text": "x",
                                                          "format": fields}]},
                                            f"zero-{index}.docx", verify=True)
                paragraph = self.paragraph(self.document_parts(f"zero-{index}.docx")["word/document.xml"])
                indent = paragraph.find(W + "pPr/" + W + "ind")
                expected = (
                    {W + "firstLineChars": "0", W + "hanging": "240"},
                    {W + "firstLineChars": "200", W + "hanging": "0"},
                    {W + "firstLineChars": "0", W + "hanging": "0"},
                )[index]
                self.assertEqual(indent.attrib, expected)

        create_docx.create_document({"blocks": [{"type": "paragraph", "text": "bounds", "format": {
            "left_indent_pt": 1584, "right_indent_pt": 0,
        }}]}, "indent-bounds.docx", verify=True)
        bounds = self.paragraph(self.document_parts("indent-bounds.docx")["word/document.xml"])
        self.assertEqual(bounds.find(W + "pPr/" + W + "ind").attrib,
                         {W + "left": "31680", W + "right": "0"})

    def test_table_body_and_header_accept_layout_styles_and_local_overrides(self):
        spec = {
            "styles": {
                "table_body": {"keep_together": True, "tab_stops": [
                    {"position_pt": 54, "align": "center"},
                ]},
                "table_header": {"widow_control": False, "left_indent_pt": 3},
            },
            "blocks": [{"type": "table", "header": True,
                        "header_format": {"page_break_before": True},
                        "body_format": {"keep_together": False, "tab_stops": [
                            {"position_pt": 18, "align": "clear"},
                        ], "hanging_indent_pt": 3},
                        "rows": [["Header"], ["Body"]]}],
        }
        create_docx.create_document(spec, "table.docx", verify=True)
        parts = self.document_parts("table.docx")
        table = parts["word/document.xml"].find(".//" + W + "tbl")
        header_ppr = table.findall(".//" + W + "pPr")[0]
        body_ppr = table.findall(".//" + W + "pPr")[1]
        self.assertEqual([child.tag for child in header_ppr], [W + "pStyle", W + "pageBreakBefore"])
        self.assertEqual([child.tag for child in body_ppr],
                         [W + "pStyle", W + "keepLines", W + "tabs", W + "ind"])
        self.assertEqual(body_ppr.find(W + "tabs/" + W + "tab").attrib,
                         {W + "val": "clear", W + "pos": "360"})
        self.assertEqual(body_ppr.find(W + "ind").attrib,
                         {W + "firstLineChars": "0", W + "hanging": "60"})

    def test_invalid_layout_fields_preserve_old_output_and_create_no_new_files(self):
        invalid = [
            {"keep_with_next": "false"},
            {"keep_together": 1},
            {"widow_control": None},
            {"page_break_before": "true"},
            {"left_indent_pt": True},
            {"right_indent_pt": float("inf")},
            {"hanging_indent_pt": 1584.01},
            {"first_line_chars": 1, "hanging_indent_pt": 1},
            {"tab_stops": []},
            {"tab_stops": [{"position_pt": 1, "align": "clear", "leader": "none"}]},
            {"tab_stops": [{"position_pt": 1, "align": "left", "unknown": 2}]},
            {"tab_stops": [{"position_pt": True, "align": "left"}]},
            {"tab_stops": [{"position_pt": float("nan"), "align": "left"}]},
            {"tab_stops": [{"position_pt": 1584.01, "align": "left"}]},
            {"tab_stops": [{"position_pt": 1, "align": "middle"}]},
            {"tab_stops": [{"position_pt": 1, "align": "left", "leader": "slash"}]},
            {"tab_stops": [{"position_pt": index, "align": "left"} for index in range(65)]},
            {"tab_stops": [{"position_pt": 1.01, "align": "left"},
                           {"position_pt": 1.02, "align": "right"}]},
        ]
        old = Path("existing.docx")
        old.write_bytes(b"protected old output")
        for index, fields in enumerate(invalid):
            spec = {"blocks": [{"type": "paragraph", "text": "x", "format": fields}]}
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    create_docx.create_document(spec, f"invalid-{index}.docx", verify=True)
                self.assertFalse(Path(f"invalid-{index}.docx").exists())
        self.assertEqual(old.read_bytes(), b"protected old output")
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), [
            "existing.docx",
        ])

    def test_only_paragraph_text_and_run_text_allow_literal_tabs(self):
        invalid_specs = [
            {"blocks": [{"type": "title", "text": "title\ttab"}]},
            {"blocks": [{"type": "subtitle", "text": "subtitle\ttab"}]},
            {"blocks": [{"type": "heading", "level": 1, "text": "heading\ttab"}]},
            {"blocks": [{"type": "list", "ordered": False, "items": ["item\ttab"]}]},
            {"blocks": [{"type": "table", "rows": [["cell\ttab"]]}]},
            {"header": "header\ttab", "blocks": []},
            {"footer": "footer\ttab", "blocks": []},
        ]
        for index, spec in enumerate(invalid_specs):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    create_docx.create_document(spec, f"unsupported-tabs-{index}.docx", verify=True)
                self.assertFalse(Path(f"unsupported-tabs-{index}.docx").exists())

    def test_generator_verifier_rejects_flag_tab_and_property_order_mutations(self):
        spec = {"blocks": [{"type": "paragraph", "text": "verify", "format": {
            "keep_with_next": False, "keep_together": True,
            "tab_stops": [{"position_pt": 18, "align": "left"}],
        }}]}

        def prepared():
            validated = create_docx._validate_spec(spec, self.work)
            parts = create_docx._package_parts(validated)
            roots = {name: ET.fromstring(parts[name]) for name in (
                "word/document.xml", "word/styles.xml", "word/numbering.xml",
            )}
            checker = SimpleNamespace(roots=roots, relations={}, archive=None)
            verify_generation(validated, checker)
            return validated, checker

        validated, checker = prepared()
        ppr = self.paragraph(checker.roots["word/document.xml"]).find(W + "pPr")
        ppr.find(W + "keepNext").set(W + "val", "1")
        with self.assertRaisesRegex(ValueError, "Generated layout mismatch"):
            verify_generation(validated, checker)

        validated, checker = prepared()
        ppr = self.paragraph(checker.roots["word/document.xml"]).find(W + "pPr")
        ppr.find(W + "tabs/" + W + "tab").set(W + "pos", "361")
        with self.assertRaisesRegex(ValueError, "Generated layout mismatch"):
            verify_generation(validated, checker)

        validated, checker = prepared()
        ppr = self.paragraph(checker.roots["word/document.xml"]).find(W + "pPr")
        ppr[:] = list(reversed(ppr[:]))
        with self.assertRaisesRegex(ValueError, "Generated layout mismatch"):
            verify_generation(validated, checker)

        validated, checker = prepared()
        ppr = self.paragraph(checker.roots["word/document.xml"]).find(W + "pPr")
        ppr.append(ET.Element(W + "keepNext", {W + "val": "0"}))
        with self.assertRaisesRegex(ValueError, "Generated layout mismatch"):
            verify_generation(validated, checker)

    def test_runner_create_verifies_layout_and_cleans_its_owned_files(self):
        spec = {
            "blocks": [{"type": "paragraph", "text": "runner\tverified", "format": {
                "keep_with_next": False,
                "tab_stops": [{"position_pt": 36, "align": "decimal", "leader": "dot"}],
            }}],
        }
        environment = dict(os.environ)
        environment.update({
            "DOCX_DEBUG_ROUTER_CONFIG": "0",
            "ROUTER_CONFIG_VARIABLES": json.dumps({"commonParam": [
                {"name": "questionId", "value": "paragraph-layout-test"},
            ]}),
            "PATH": "",
            "LC_ALL": "C",
        })
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT), "create", "runner.docx"],
            input=json.dumps(spec, ensure_ascii=False), capture_output=True, encoding="utf-8",
            cwd=self.work, env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["task_status"], "completed")
        self.assertTrue(result["cleanup"]["cleanup_complete"])
        self.assertTrue(result["cleanup"]["manifest_removed"])
        self.assertTrue(result["layout_intent"])
        self.assertEqual(read_docx(result["output"]), "runner\tverified")
        self.assertEqual(Path(result["output"]).read_bytes(),
                         Path(result["archive_output"]).read_bytes())
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), [
            "output", "runner.docx",
        ])
        self.assertEqual(sorted(path.name for path in Path("output/paragraph-layout-test").iterdir()),
                         ["runner.docx"])


if __name__ == "__main__":
    unittest.main()
