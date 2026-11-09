"""Regression tests for non-target formatting protection and checked delivery."""

import sys
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[1]),
]
from fixture_delivery import create_fixture

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import edit_structure as edit_structure_module
import format_guard
import run_docx_edit
from create_docx import create_document

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"


def tag(name):
    return f"{{{W}}}{name}"


def element(parent, name, attributes=None, text=None):
    node = ET.SubElement(parent, tag(name))
    if attributes:
        for key, value in attributes.items():
            node.set(tag(key), str(value))
    if text is not None:
        node.text = text
    return node


def make_run(text, *, properties=False, target=False):
    run = ET.Element(tag("r"))
    if properties:
        props = element(run, "rPr")
        if target:
            element(props, "rStyle", {"val": "CharacterOverlay"})
            element(props, "rFonts", {
                "ascii": "Original Latin", "hAnsi": "Original Latin",
                "eastAsia": "Original Asia", "cs": "Complex Script",
                "asciiTheme": "minorHAnsi", "hAnsiTheme": "majorHAnsi",
                "eastAsiaTheme": "majorEastAsia", "cstheme": "minorBidi",
                "hint": "eastAsia",
            })
            element(props, "b", {"val": "0"})
            element(props, "bCs", {"val": "0"})
            element(props, "i", {"val": "1"})
            element(props, "iCs", {"val": "1"})
            element(props, "color", {
                "val": "112233", "themeColor": "accent2", "themeTint": "33",
            })
            element(props, "sz", {"val": "20"})
            element(props, "szCs", {"val": "22"})
            element(props, "lang", {"val": "en-GB", "eastAsia": "zh-CN"})
        else:
            element(props, "rStyle", {"val": "CharacterOverlay"})
            element(props, "color", {"val": "445566", "themeColor": "accent4"})
            element(props, "b", {"val": "1"})
            element(props, "sz", {"val": "18"})
    node = element(run, "t", text=text)
    if text[:1].isspace() or text[-1:].isspace():
        node.set(f"{{{XML}}}space", "preserve")
    return run


class FormatGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="docx-format-guard-", dir="."
        )
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.previous = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self.previous)
        self.input_hashes = {}

    def track(self, path):
        path = Path(path).resolve()
        self.input_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def tearDown(self):
        for path, digest in self.input_hashes.items():
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                digest,
                f"Input changed: {path.name}",
            )

    @staticmethod
    def members(path):
        with zipfile.ZipFile(path) as package:
            return {name: package.read(name) for name in package.namelist()}

    @staticmethod
    def write_members(path, parts):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name, content in parts.items():
                package.writestr(name, content)

    def make_source(self, name="source.docx"):
        destination = self.work / name
        create_fixture(
            create_document,
            {"blocks": [
                {"type": "paragraph", "text": "目标Latin保留文字"},
                {"type": "paragraph", "text": "继承目标"},
                {"type": "list", "ordered": False, "items": ["编号内容"]},
            ]},
            destination,
            base_dir=self.work,
            verify=True,
        )
        parts = self.members(destination)
        self.assertIn("word/numbering.xml", parts)
        document = ET.fromstring(parts["word/document.xml"])
        body = document.find(tag("body"))
        paragraphs = body.findall(tag("p"))
        first = paragraphs[0]
        ppr = first.find(tag("pPr"))
        if ppr is None:
            ppr = ET.Element(tag("pPr"))
            first.insert(0, ppr)
        element(ppr, "spacing", {"before": "160", "after": "90"})
        for run in list(first.findall(tag("r"))):
            first.remove(run)
        first.append(make_run("目标Latin", properties=True, target=True))
        first.append(make_run("保留文字", properties=True))
        second = paragraphs[1]
        for run in list(second.findall(tag("r"))):
            second.remove(run)
        second.append(make_run("继承目标"))
        styles = ET.fromstring(parts["word/styles.xml"])
        style = ET.SubElement(styles, tag("style"), {
            tag("type"): "character", tag("styleId"): "CharacterOverlay",
        })
        element(style, "name", {"val": "Character Overlay"})
        element(style, "basedOn", {"val": "DefaultParagraphFont"})
        parts["word/styles.xml"] = ET.tostring(
            styles, encoding="utf-8", xml_declaration=True
        )
        parts["word/document.xml"] = ET.tostring(
            document, encoding="utf-8", xml_declaration=True
        )
        self.write_members(destination, parts)
        return self.track(destination)

    @staticmethod
    def operation(paragraph_index, run_index, expected_text, expected_run_text, formatting):
        return {
            "type": "format_run",
            "paragraph_index": paragraph_index,
            "expected_text": expected_text,
            "run_index": run_index,
            "expected_run_text": expected_run_text,
            "format": formatting,
        }

    def make_candidate(self, source, specification, name="candidate.docx"):
        edit_structure_module.edit_structure(source, name, specification)
        return self.work / name

    @staticmethod
    def mutate_document(path, change):
        parts = FormatGuardTests.members(path)
        document = ET.fromstring(parts["word/document.xml"])
        change(document, parts)
        parts["word/document.xml"] = ET.tostring(
            document, encoding="utf-8", xml_declaration=True
        )
        FormatGuardTests.write_members(path, parts)

    @staticmethod
    def paragraph(document, index=0):
        return document.find(tag("body")).findall(tag("p"))[index]

    @staticmethod
    def run_at(paragraph, index=0):
        return paragraph.findall(tag("r"))[index]

    @staticmethod
    def property_node(run, name):
        props = run.find(tag("rPr"))
        return None if props is None else props.find(tag(name))

    def assert_rejected(self, source, candidate, specification):
        digest = hashlib.sha256(Path(candidate).read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            format_guard.validate_format_edits(source, candidate, specification)
        self.assertEqual(
            hashlib.sha256(Path(candidate).read_bytes()).hexdigest(), digest
        )

    def test_valid_all_fields_multiple_targets_and_no_rpr(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Requested Asia",
                "font_latin": "Requested Latin",
                "size_pt": 13.5,
                "bold": True,
                "italic": False,
                "color": "abcdef",
            }),
            self.operation(1, 0, "继承目标", "继承目标", {
                "font_east_asia": "Inherited Asia",
                "font_latin": "Inherited Latin",
                "size_pt": 15,
            }),
        ]}
        original_parts = self.members(source)
        candidate = self.make_candidate(source, specification)
        result = format_guard.validate_format_edits(source, candidate, specification)
        self.assertEqual(result, {"status": "validated", "targets": 2})
        candidate_parts = self.members(candidate)
        self.assertEqual(set(original_parts), set(candidate_parts))
        for name in original_parts:
            if name != "word/document.xml":
                self.assertEqual(candidate_parts[name], original_parts[name], name)
        document = ET.fromstring(candidate_parts["word/document.xml"])
        target = self.run_at(self.paragraph(document))
        fonts = self.property_node(target, "rFonts")
        self.assertEqual(fonts.get(tag("eastAsia")), "Requested Asia")
        self.assertEqual(fonts.get(tag("ascii")), "Requested Latin")
        self.assertEqual(fonts.get(tag("hAnsi")), "Requested Latin")
        self.assertNotIn(tag("eastAsiaTheme"), fonts.attrib)
        self.assertNotIn(tag("asciiTheme"), fonts.attrib)
        self.assertNotIn(tag("hAnsiTheme"), fonts.attrib)
        self.assertEqual(self.property_node(target, "sz").get(tag("val")), "27")
        self.assertEqual(self.property_node(target, "szCs").get(tag("val")), "27")
        self.assertEqual(self.property_node(target, "b").get(tag("val")), "1")
        self.assertEqual(self.property_node(target, "bCs").get(tag("val")), "1")
        self.assertEqual(self.property_node(target, "i").get(tag("val")), "0")
        self.assertEqual(self.property_node(target, "iCs").get(tag("val")), "0")
        color = self.property_node(target, "color")
        self.assertEqual(color.get(tag("val")), "ABCDEF")
        for name in ("themeColor", "themeTint", "themeShade"):
            self.assertNotIn(tag(name), color.attrib)
        inherited = self.run_at(self.paragraph(document, 1))
        self.assertEqual(
            self.property_node(inherited, "rFonts").get(tag("eastAsia")),
            "Inherited Asia",
        )
        self.assertEqual(self.property_node(inherited, "szCs").get(tag("val")), "30")
        self.assertEqual(self.property_node(inherited, "rFonts").get(tag("ascii")), "Inherited Latin")
        self.assertEqual(self.property_node(inherited, "rFonts").get(tag("hAnsi")), "Inherited Latin")

    def test_idempotent_requested_values_are_accepted(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {"bold": False}),
        ]}
        candidate = self.make_candidate(source, specification)
        result = format_guard.validate_format_edits(source, candidate, specification)
        self.assertEqual(result["status"], "validated")

    def test_rejects_non_target_document_or_package_changes(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Guard Asia",
            }),
        ]}
        changes = (
            ("paragraph-properties", lambda p, _: p.remove(p.find(tag("pPr")))),
            ("non-target-rpr", lambda p, _: self.property_node(
                self.run_at(p), "color"
            ).set(tag("val"), "010203")),
            ("rstyle", lambda p, _: self.property_node(
                self.run_at(p), "rStyle"
            ).set(tag("val"), "DefaultParagraphFont")),
            ("text", lambda p, _: self.run_at(p, 1).find(tag("t")).__setattr__("text", "altered")),
        )
        for name, callback in changes:
            with self.subTest(change=name):
                candidate = self.make_candidate(
                    source, specification, f"candidate-{name}.docx"
                )
                self.mutate_document(
                    candidate,
                    lambda document, parts, operation=callback: operation(
                        self.paragraph(document), parts
                    ),
                )
                self.assert_rejected(source, candidate, specification)

        for name, part in (
            ("styles", "word/styles.xml"),
            ("numbering", "word/numbering.xml"),
        ):
            with self.subTest(part=name):
                candidate = self.make_candidate(
                    source, specification, f"candidate-{name}.docx"
                )
                parts = self.members(candidate)
                parts[part] += b"\n"
                self.write_members(candidate, parts)
                self.assert_rejected(source, candidate, specification)

    def test_font_mask_rejects_unrequested_font_attributes_and_namespace_changes(self):
        source = self.make_source()
        specification = {"operations": [self.operation(
            0, 0, "目标Latin保留文字", "目标Latin", {"font_east_asia": "Guard Asia"}
        )]}
        for attribute in ("cs", "hint", "asciiTheme", "cstheme"):
            with self.subTest(attribute=attribute):
                candidate = self.make_candidate(source, specification, attribute + ".docx")
                self.mutate_document(candidate, lambda document, _, name=attribute: self.property_node(
                    self.run_at(self.paragraph(document)), "rFonts"
                ).attrib.pop(tag(name)))
                with self.assertRaisesRegex(ValueError, "outside the requested run formatting"):
                    format_guard.validate_format_edits(source, candidate, specification)
        candidate = self.make_candidate(source, specification, "namespace.docx")
        parts = self.members(candidate)
        from office.xmlio import parse_dom
        dom = parse_dom(parts["word/document.xml"])
        dom.documentElement.setAttributeNS(
            "http://www.w3.org/2000/xmlns/", "xmlns:unrequested", "urn:unrequested"
        )
        parts["word/document.xml"] = dom.toxml(encoding="UTF-8", standalone=dom.standalone)
        self.write_members(candidate, parts)
        with self.assertRaisesRegex(ValueError, "outside the requested run formatting"):
            format_guard.validate_format_edits(source, candidate, specification)

    def test_rejects_member_addition_and_removal(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Guard Asia",
            }),
        ]}
        for label, add in (("added", True), ("removed", False)):
            candidate = self.make_candidate(source, specification, f"{label}.docx")
            parts = self.members(candidate)
            if add:
                parts["extra/part.xml"] = b"<extra/>"
            else:
                parts.pop("word/numbering.xml")
            self.write_members(candidate, parts)
            with self.subTest(members=label):
                self.assert_rejected(source, candidate, specification)

    def test_rejects_wrong_unapplied_and_theme_residual_values(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Guard Asia",
            }),
        ]}
        self.assert_rejected(source, source, specification)
        candidate = self.make_candidate(source, specification, "wrong-value.docx")
        self.mutate_document(candidate, lambda document, _: self.property_node(
            self.run_at(self.paragraph(document)), "rFonts"
        ).set(tag("eastAsia"), "Wrong Asia"))
        self.assert_rejected(source, candidate, specification)
        candidate = self.make_candidate(source, specification, "theme-residual.docx")
        self.mutate_document(candidate, lambda document, _: self.property_node(
            self.run_at(self.paragraph(document)), "rFonts"
        ).set(tag("eastAsiaTheme"), "majorEastAsia"))
        self.assert_rejected(source, candidate, specification)

    def test_rejects_split_merge_and_out_of_scope_targets(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Guard Asia",
            }),
        ]}
        split = self.make_candidate(source, specification, "split.docx")

        def split_target(paragraph, _):
            target = self.run_at(paragraph)
            target.find(tag("t")).text = "目标"
            sibling = make_run("Latin")
            paragraph.insert(paragraph.findall(tag("r")).index(target) + 1, sibling)

        self.mutate_document(split, lambda document, parts: split_target(
            self.paragraph(document), parts
        ))
        self.assert_rejected(source, split, specification)

        merged = self.make_candidate(source, specification, "merged.docx")

        def merge_runs(paragraph, _):
            runs = paragraph.findall(tag("r"))
            runs[0].find(tag("t")).text += runs[1].find(tag("t")).text
            paragraph.remove(runs[1])

        self.mutate_document(merged, lambda document, parts: merge_runs(
            self.paragraph(document), parts
        ))
        self.assert_rejected(source, merged, specification)

        table = self.work / "table-only.docx"
        create_fixture(
            create_document,
            {"blocks": [{"type": "table", "rows": [["Header"], ["Value"]]}]},
            table,
            base_dir=self.work,
            verify=True,
        )
        table_specification = {"operations": [
            self.operation(0, 0, "Value", "Value", {"size_pt": 14}),
        ]}
        with self.assertRaises(ValueError):
            format_guard.validate_format_edits(table, table, table_specification)
        out_of_range = {"operations": [
            self.operation(99, 0, "Missing", "Missing", {"size_pt": 14}),
        ]}
        with self.assertRaises(ValueError):
            format_guard.validate_format_edits(source, source, out_of_range)

    def test_rejects_signed_packages_and_revision_targets(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Guard Asia",
            }),
        ]}
        candidate = self.make_candidate(source, specification)
        signed_snapshot = format_guard.read_package(source)
        candidate_snapshot = format_guard.read_package(candidate)
        signed_snapshot.files["_xmlsignatures/signature.xml"] = b"<Signature/>"
        try:
            with self.assertRaisesRegex(ValueError, "Signed packages are unsupported"):
                format_guard.compare_format_edits(
                    signed_snapshot, candidate_snapshot, specification, extension=".docx"
                )
        finally:
            signed_snapshot.files.clear()
            candidate_snapshot.files.clear()

        revision = self.make_source("revision.docx")
        parts = self.members(revision)
        document = ET.fromstring(parts["word/document.xml"])
        paragraph = self.paragraph(document)
        run = self.run_at(paragraph)
        paragraph.remove(run)
        insertion = ET.Element(tag("ins"), {tag("id"): "7", tag("author"): "Reviewer"})
        insertion.append(run)
        paragraph.insert(1, insertion)
        parts["word/document.xml"] = ET.tostring(
            document, encoding="utf-8", xml_declaration=True
        )
        self.write_members(revision, parts)
        self.track(revision)
        with self.assertRaises(ValueError):
            format_guard.validate_format_edits(revision, revision, specification)

    def test_rejects_insert_unknown_fields_and_duplicate_targets(self):
        source = self.make_source()
        base = self.operation(
            0, 0, "目标Latin保留文字", "目标Latin", {"font_east_asia": "Guard Asia"}
        )
        candidate = self.make_candidate(source, {"operations": [base]})
        invalid = (
            {"operations": [{
                "type": "insert_paragraph", "paragraph_index": 0,
                "expected_text": "目标Latin保留文字", "position": "after", "text": "insert",
            }]},
            {"operations": [self.operation(
                0, 0, "目标Latin保留文字", "目标Latin", {"unknown": "value"}
            )]},
            {"operations": [self.operation(
                0, 0, "目标Latin保留文字", "目标Latin", {}
            )]},
            {"operations": [base, base]},
        )
        for specification in invalid:
            with self.subTest(specification=specification):
                with self.assertRaises(ValueError):
                    format_guard.validate_format_edits(source, candidate, specification)

    def test_edit_structure_and_runner_refuse_injected_non_target_loss(self):
        source = self.make_source()
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Injected Asia",
            }),
        ]}
        sentinel = b"preserve existing output"
        output = self.work / "guarded.docx"
        output.write_bytes(sentinel)
        apply_format = edit_structure_module._apply_format

        def remove_ppr(run, formatting):
            apply_format(run, formatting)
            paragraph = run.parentNode
            ppr = next(
                node for node in paragraph.childNodes
                if node.nodeType == node.ELEMENT_NODE
                and node.namespaceURI == W and node.localName == "pPr"
            )
            paragraph.removeChild(ppr)

        with patch.object(edit_structure_module, "_apply_format", side_effect=remove_ppr):
            with self.assertRaises(ValueError):
                edit_structure_module.edit_structure(source, "guarded.docx", specification)
        self.assertEqual(output.read_bytes(), sentinel)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)

        runner_sentinel = b"keep original runner output"
        runner_output = self.work / "runner-guarded.docx"
        runner_output.write_bytes(runner_sentinel)
        environment = dict(os.environ, PATH="", DOCX_DEBUG_ROUTER_CONFIG="0")
        environment.pop("ROUTER_CONFIG_VARIABLES", None)
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(edit_structure_module, "_apply_format", side_effect=remove_ppr):
                result = run_docx_edit.edit_task(
                    "structure", "source.docx", "runner-guarded.docx", specification
                )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runner_output.read_bytes(), runner_sentinel)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        self.assertFalse((self.work / "output").exists())

    def test_cli_validates_read_only_and_rejects_duplicates_or_oversize(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {"size_pt": 12}),
        ]}
        candidate = self.make_candidate(source, specification)
        spec_path = self.work / "operations.json"
        spec_path.write_text(json.dumps(specification, ensure_ascii=False), encoding="utf-8")
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (source, candidate, spec_path)}
        environment = dict(os.environ, PATH="", DOCX_DEBUG_ROUTER_CONFIG="0")
        environment.pop("ROUTER_CONFIG_VARIABLES", None)
        command = [
            sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "format_guard.py"),
            source.name, candidate.name, spec_path.name,
        ]
        process = subprocess.run(
            command, cwd=self.work, env=environment, capture_output=True, encoding="utf-8"
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "validated")
        self.assertEqual(process.stderr, "")
        absolute_command = [
            sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "format_guard.py"),
            str(source), str(candidate), str(spec_path),
        ]
        outside = subprocess.run(
            absolute_command, cwd=self.work, env=environment,
            capture_output=True, encoding="utf-8"
        )
        self.assertEqual(outside.returncode, 1)
        self.assertIn("delivery paths", outside.stderr)
        for path, digest in hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

        spec_path.write_text('{"operations":[],"operations":[]}', encoding="utf-8")
        duplicate = subprocess.run(
            command, cwd=self.work, env=environment, capture_output=True, encoding="utf-8"
        )
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("Duplicate JSON key", duplicate.stderr)
        spec_path.write_bytes(b" " * (format_guard.MAX_SPEC_BYTES + 1))
        oversized = subprocess.run(
            command, cwd=self.work, env=environment, capture_output=True, encoding="utf-8"
        )
        self.assertEqual(oversized.returncode, 1)
        self.assertIn("exceeds 2 MiB", oversized.stderr)

    def test_publish_uses_the_validated_candidate_snapshot(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Snapshot Asia",
            }),
        ]}
        candidate = self.make_candidate(source, specification)
        expected_members = self.members(candidate)
        original_read = format_guard.read_package

        def mutate_after_snapshot(path):
            snapshot = original_read(path)
            if Path(path).name == candidate.name:
                Path(path).write_bytes(b"external candidate replacement")
            return snapshot

        with patch.object(format_guard, "read_package", side_effect=mutate_after_snapshot):
            receipt = format_guard.publish_format_candidate_delivery(
                source, candidate, "published.docx", specification
            )
        self.assertEqual(receipt["status"], "edited")
        published = self.members(self.work / "published.docx")
        self.assertEqual(set(published), set(expected_members))
        self.assertEqual(published, expected_members)
        self.assertEqual(candidate.read_bytes(), b"external candidate replacement")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         self.input_hashes[source])

    def test_format_checked_runner_candidate_alias_and_failure_are_safe(self):
        source = self.make_source()
        specification = {"operations": [
            self.operation(0, 0, "目标Latin保留文字", "目标Latin", {
                "font_east_asia": "Runner Asia",
            }),
        ]}
        candidate = self.make_candidate(source, specification)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        candidate_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        spec_path = self.work / "runner-spec.json"
        spec_path.write_text(json.dumps(specification, ensure_ascii=False), encoding="utf-8")
        environment = dict(os.environ, PATH="", DOCX_DEBUG_ROUTER_CONFIG="0")
        environment["ROUTER_CONFIG_VARIABLES"] = json.dumps([{
            "name": "commonParam",
            "value": json.dumps([{"name": "questionId", "value": "format-checked"}]),
        }])
        command = [
            sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "run_docx_edit.py"),
            "format-checked", source.name, "runner-published.docx",
            "--candidate", candidate.name,
        ]
        process = subprocess.run(
            command,
            cwd=self.work,
            env=environment,
            input=json.dumps(specification, ensure_ascii=False),
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "edited")
        self.assertEqual(payload["task_status"], "completed")
        self.assertTrue(payload["cleanup"]["cleanup_complete"])
        self.assertEqual(payload["output"], "runner-published.docx")
        self.assertEqual(self.members(self.work / payload["output"]), self.members(candidate))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), candidate_hash)

        wrong = self.work / "wrong-candidate.docx"
        wrong.write_bytes(candidate.read_bytes())
        self.mutate_document(wrong, lambda document, _: self.property_node(
            self.run_at(self.paragraph(document)), "rFonts"
        ).set(tag("eastAsia"), "Wrong Asia"))
        wrong_hash = hashlib.sha256(wrong.read_bytes()).hexdigest()
        sentinel = self.work / "runner-failure.docx"
        sentinel.write_bytes(b"keep existing output")
        bad_command = [
            sys.executable, "-I", "-S", "-B", str(SCRIPT_DIR / "run_docx_edit.py"),
            "format-checked", source.name, sentinel.name, "--candidate", wrong.name,
        ]
        failed = subprocess.run(
            bad_command,
            cwd=self.work,
            env=environment,
            input=json.dumps(specification, ensure_ascii=False),
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual(json.loads(failed.stdout)["status"], "failed")
        self.assertEqual(sentinel.read_bytes(), b"keep existing output")
        self.assertEqual(hashlib.sha256(wrong.read_bytes()).hexdigest(), wrong_hash)
        self.assertFalse((self.work / "runner-failure_第2版.docx").exists())
        self.assertEqual(
            list((self.work / "output" / "format-checked").glob("runner-failure*"))
            if (self.work / "output" / "format-checked").exists() else [],
            [],
        )


if __name__ == "__main__":
    unittest.main()
