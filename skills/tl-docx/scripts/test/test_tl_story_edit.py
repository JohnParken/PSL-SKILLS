"""Synthetic preservation and refusal tests for narrow header/footer run edits."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.opc.oxml import serialize_part_xml
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_edit
from tl_package import OFFICE_REL, Package, REL, TYPES, parse, rels_member

SCRIPTS = Path(__file__).resolve().parents[1]


def fixture(multiple_sections=False):
    """Create independent pure-text default header/footer parts without private input."""
    document = Document()
    document.add_paragraph('Body unchanged')
    for story in (document.sections[0].header, document.sections[0].footer):
        paragraph = story.paragraphs[0]
        paragraph.add_run('Before').italic = True
        paragraph.add_run('Target').bold = True
        paragraph.add_run('After')
        story.add_paragraph('Other paragraph')
    if multiple_sections:
        document.add_section(WD_SECTION_START.NEW_PAGE)
    stream = BytesIO()
    document.save(stream)
    package = Package(stream.getvalue())
    return Package(package.overlay({'custom/opaque.bin': bytes(range(128))}))


def story_name(package, kind='header'):
    """Discover the fixture part using declared relationships, not conventional paths."""
    return next(value['target'] for value in package.relations(package.main).values() if value['type'] == OFFICE_REL + kind)


def operation(package, kind='replace_story_run_text', story='header', **changes):
    """Construct exactly the new public part/paragraph/run schema."""
    result = {'type': kind, 'part': story_name(package, story), 'paragraph_index': 0, 'run_index': 1,
              'expected_text': 'BeforeTargetAfter', 'expected_run_text': 'Target'}
    result.update({'format': {'font_east_asia': '黑体', 'size_pt': 18}} if kind == 'format_story_run' else {'text': 'New'})
    result.update(changes)
    return result


def edited(package, requests):
    """Run the production package validation and overlay pipeline."""
    data, receipt = tl_edit.edit_document(package, {'operations': requests}, None)
    return Package(data), receipt


def mutated(package, part, change):
    """Apply one synthetic XML variation while retaining original package members."""
    root = deepcopy(package.xml(part))
    change(root)
    return Package(package.overlay({part: serialize_part_xml(root)}))


class StoryRunEditTests(unittest.TestCase):
    """First-version story support never creates parts or widens shared/linked scope."""

    def test_header_and_footer_text_and_format_preserve_all_other_members(self):
        """Only the explicitly selected member may differ; original package stays unchanged."""
        for story in ('header', 'footer'):
            for kind in ('replace_story_run_text', 'format_story_run'):
                with self.subTest(story=story, kind=kind):
                    package = fixture()
                    name = story_name(package, story)
                    before = package.read(name)
                    result, receipt = edited(package, [operation(package, kind, story)])
                    self.assertEqual([name], receipt['changed_parts'])
                    self.assertEqual(package.names, result.names)
                    for member in package.names - {name}:
                        self.assertEqual(package.read(member), result.read(member))
                    self.assertEqual(before, package.read(name))
                    self.assertEqual(before, serialize_part_xml(package.xml(name)))
                    old, new = deepcopy(package.xml(name)), deepcopy(result.xml(name))
                    old_run = old.findall(qn('w:p'))[0].findall(qn('w:r'))[1]
                    new_run = new.findall(qn('w:p'))[0].findall(qn('w:r'))[1]
                    if kind == 'replace_story_run_text':
                        self.assertEqual('New', new_run.find(qn('w:t')).text)
                        self.assertEqual(serialize_part_xml(old_run.find(qn('w:rPr'))), serialize_part_xml(new_run.find(qn('w:rPr'))))
                    else:
                        self.assertEqual('Target', new_run.find(qn('w:t')).text)
                        self.assertEqual('黑体', new_run.find('./' + qn('w:rPr') + '/' + qn('w:rFonts')).get(qn('w:eastAsia')))
                        self.assertIsNotNone(new_run.find('./' + qn('w:rPr') + '/' + qn('w:b')))
                    old_run.getparent().remove(old_run)
                    new_run.getparent().remove(new_run)
                    self.assertEqual(serialize_part_xml(old), serialize_part_xml(new))

    def test_nonstandard_part_name_is_supported(self):
        """A header is located by actual OPC evidence, not header1.xml conventions."""
        package = fixture()
        original, renamed = story_name(package), 'word/stories/brand.xml'
        members = {name: package.read(name) for name in package.names}
        members[renamed] = members.pop(original)
        root = parse(members[TYPES])
        for child in root:
            if child.get('PartName') == '/' + original:
                child.set('PartName', '/' + renamed)
        members[TYPES] = serialize_part_xml(root)
        root = parse(members[rels_member(package.main)])
        for child in root:
            if child.get('Type') == OFFICE_REL + 'header':
                child.set('Target', 'stories/brand.xml')
        members[rels_member(package.main)] = serialize_part_xml(root)
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        package = Package(stream.getvalue())
        result, receipt = edited(package, [operation(package)])
        self.assertEqual([renamed], receipt['changed_parts'])
        self.assertIn(b'New', result.read(renamed))

    def test_sequential_multipart_empty_and_whitespace_operations(self):
        """Multiple explicit story targets work without ever serializing the main part."""
        package = fixture()
        for value in ('', ' New ', '\t甲\n乙\t'):
            requests = [operation(package, text=value),
                        operation(package, 'format_story_run', expected_text='Before' + value + 'After', expected_run_text=value),
                        operation(package, story='footer')]
            result, receipt = edited(package, requests)
            self.assertEqual(sorted([story_name(package), story_name(package, 'footer')]), receipt['changed_parts'])
            self.assertEqual(package.read(package.main), result.read(package.main))

    def test_invalid_fields_anchors_and_mixed_batches_rejected(self):
        """No silent widening to body edits, variant switches or stale locations."""
        package = fixture()
        changes = [{'part': package.main}, {'part': '../header.xml'}, {'part': 'word/missing.xml'}, {'variant': 'default'},
                   {'expected_text': 'stale'}, {'expected_run_text': 'stale'}, {'text': 'bad\rtext'}]
        changes += [{key: value} for key in ('paragraph_index', 'run_index') for value in (-1, True, 99)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                edited(package, [operation(package, **change)])
        body_request = {'type': 'replace_run_text', 'paragraph_index': 0, 'expected_text': 'Body unchanged',
                        'run_index': 0, 'expected_run_text': 'Body unchanged', 'text': 'Changed'}
        with self.assertRaisesRegex(ValueError, 'separate batch'):
            edited(package, [operation(package), body_request])

    def test_signed_packages_remain_rejected(self):
        """The existing signed-document safety gate also precedes story-only editing."""
        package = fixture()
        signature = b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/>'
        variants = [Package(package.overlay({'_xmlsignatures/sig1.xml': signature}))]
        types = deepcopy(package.xml(TYPES))
        entry = parse(b'<Override xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        entry.set('PartName', '/custom/signature.xml')
        entry.set('ContentType', 'application/vnd.openxmlformats-package.digital-signature-xmlsignature+xml')
        types.append(entry)
        variants.append(Package(package.overlay({'custom/signature.xml': signature, TYPES: serialize_part_xml(types)})))
        for number, variant in enumerate(variants):
            with self.subTest(number=number), self.assertRaisesRegex(ValueError, 'Digitally signed'):
                edited(variant, [operation(package)])

    def test_multi_section_and_inheritance_rejected(self):
        """A later section inheriting the part must not be changed as a side effect."""
        package = fixture(multiple_sections=True)
        with self.assertRaisesRegex(ValueError, 'one explicit final section'):
            edited(package, [operation(package)])

    def test_missing_or_nondefault_reference_and_duplicate_variant_rejected(self):
        """Unreferenced and first/even variants remain outside this first-version contract."""
        package = fixture()
        request = operation(package)
        def change(root, code):
            section = root.find('.//' + qn('w:sectPr'))
            reference = section.find(qn('w:headerReference'))
            if code == 'orphan':
                section.remove(reference)
            elif code == 'duplicate':
                section.append(deepcopy(reference))
            else:
                reference.set(qn('w:type'), code)
        for code in ('orphan', 'first', 'even', 'unknown', 'duplicate'):
            variant = mutated(package, package.main, lambda root, code=code: change(root, code))
            with self.subTest(code=code), self.assertRaises(ValueError):
                edited(variant, [request])

    def test_sharing_alias_relationships_and_duplicate_use_rejected(self):
        """Both direct variant sharing and extra OPC incoming edges are unsafe."""
        package = fixture()
        request = operation(package)
        def share_variant(root):
            section = root.find('.//' + qn('w:sectPr'))
            reference = deepcopy(section.find(qn('w:headerReference')))
            reference.set(qn('w:type'), 'even')
            section.append(reference)
        variants = [mutated(package, package.main, share_variant)]
        def alias_relation(root):
            relation = deepcopy(next(node for node in root if node.get('Type') == OFFICE_REL + 'header'))
            relation.set('Id', 'ExtraAlias')
            root.append(relation)
        variants.append(mutated(package, rels_member(package.main), alias_relation))
        footer = story_name(package, 'footer')
        relation = f'<Relationships xmlns="{REL[1:-1]}"><Relationship Id="Extra" Type="{OFFICE_REL}header" Target="header1.xml"/></Relationships>'
        variants.append(Package(package.overlay({rels_member(footer): relation.encode()})))
        def use_elsewhere(root):
            reference = root.find('.//' + qn('w:headerReference'))
            root.find('./' + qn('w:body') + '/' + qn('w:p')).set(qn('r:id'), reference.get(qn('r:id')))
        variants.append(mutated(package, package.main, use_elsewhere))
        for number, variant in enumerate(variants):
            with self.subTest(number=number), self.assertRaises(ValueError):
                edited(variant, [request])

    def test_relation_content_type_and_root_mismatches_rejected(self):
        """Part name alone is not authorization or proof of a header/footer role."""
        package = fixture()
        request = operation(package)
        name = request['part']
        def wrong_relation(root, code):
            relation = next(node for node in root if node.get('Type') == OFFICE_REL + 'header')
            if code == 'external':
                relation.set('TargetMode', 'External')
                relation.set('Target', 'https://example.invalid/header.xml')
            else:
                relation.set('Type', OFFICE_REL + 'footer')
        variants = [mutated(package, rels_member(package.main), lambda root, code=code: wrong_relation(root, code))
                    for code in ('external', 'wrong-type')]
        def wrong_content_type(root):
            for node in root:
                if node.get('PartName') == '/' + name:
                    node.set('ContentType', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml')
        variants.append(mutated(package, TYPES, wrong_content_type))
        variants.append(mutated(package, name, lambda root: setattr(root, 'tag', qn('w:ftr'))))
        for number, variant in enumerate(variants):
            with self.subTest(number=number), self.assertRaises(ValueError):
                edited(variant, [request])

    def test_complex_story_content_and_revisions_rejected(self):
        """The entire selected part must be plain text, even outside the selected paragraph."""
        package = fixture()
        name = story_name(package)
        def change(root, code):
            paragraph = root.findall(qn('w:p'))[-1]
            if code in ('tbl', 'sdt'):
                root.append(OxmlElement('w:' + code))
            elif code in ('bookmarkStart', 'hyperlink', 'commentRangeStart', 'ins'):
                paragraph.append(OxmlElement('w:' + code))
            elif code in ('fldChar', 'instrText', 'drawing'):
                paragraph.find(qn('w:r')).append(OxmlElement('w:' + code))
            else:
                props = OxmlElement('w:rPr')
                props.append(OxmlElement('w:' + code))
                paragraph.find(qn('w:r')).insert(0, props)
        for code in ('tbl', 'sdt', 'bookmarkStart', 'hyperlink', 'commentRangeStart', 'ins', 'fldChar', 'instrText', 'drawing', 'rPrChange', 'del'):
            variant = mutated(package, name, lambda root, code=code: change(root, code))
            for kind in ('format_story_run', 'replace_story_run_text'):
                with self.subTest(code=code, kind=kind), self.assertRaises(ValueError):
                    edited(variant, [operation(package, kind)])

    def test_editor_request_and_serializer_faults_rejected(self):
        """Independent snapshots reject mutations that valid-looking DOCX packaging would hide."""
        package = fixture()
        original = tl_edit.edit_story_run
        def change(root, code):
            paragraph = root.findall(qn('w:p'))[0]
            run = paragraph.findall(qn('w:r'))[1]
            if code == 'wrong-text':
                run.find(qn('w:t')).text = 'Wrong'
            elif code == 'format':
                run.find(qn('w:rPr')).set(qn('w:rsidRPr'), '1234')
            elif code == 'other-run':
                paragraph.findall(qn('w:r'))[0].find(qn('w:t')).text = 'Wrong'
            elif code == 'other-paragraph':
                root.findall(qn('w:p'))[-1].find('.//' + qn('w:t')).text = 'Wrong'
            elif code == 'new-paragraph':
                root.append(OxmlElement('w:p'))
            elif code == 'run-attribute':
                run.set(qn('w:rsidR'), '1234')
        for kind in ('format_story_run', 'replace_story_run_text'):
            for code in ('wrong-text', 'format', 'other-run', 'other-paragraph', 'new-paragraph', 'run-attribute'):
                def broken(root, request, code=code):
                    original(root, request)
                    change(root, code)
                with self.subTest(kind=kind, code=code), patch.object(tl_edit, 'edit_story_run', broken):
                    with self.assertRaisesRegex(ValueError, 'preservation guard'):
                        edited(package, [operation(package, kind)])
        def changed_request(root, request):
            request['text'] = 'Wrong'
            original(root, request)
        with patch.object(tl_edit, 'edit_story_run', changed_request), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation(package)])
        serializer = tl_edit.serialize_part_xml
        def broken_serializer(root):
            root.find('.//' + qn('w:t')).text = 'Wrong'
            return serializer(root)
        with patch.object(tl_edit, 'serialize_part_xml', broken_serializer), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation(package)])

    def test_overlay_fault_in_unrelated_member_rejected(self):
        """A faulty packaging step cannot silently alter the unchanged main part."""
        package = fixture()
        original = Package.overlay
        def broken_overlay(instance, replacements):
            altered = dict(replacements)
            altered[instance.main] = instance.read(instance.main).replace(b'Body unchanged', b'Wrong')
            return original(instance, altered)
        with patch.object(Package, 'overlay', broken_overlay), self.assertRaisesRegex(ValueError, 'part preservation'):
            edited(package, [operation(package)])

    def test_cli_fault_and_stale_batch_never_publish(self):
        """Exercise failure and success through the actual edit CLI and protect source bytes."""
        script = """import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import tl_edit
import tl_docx
original = tl_edit.edit_story_run
def broken(root, request):
    original(root, request)
    root.set('unexpected', 'yes')
with patch.object(tl_edit, 'edit_story_run', broken):
    raise SystemExit(tl_docx.main(['edit', 'source.docx', 'output.docx']))
"""
        package = fixture()
        with tempfile.TemporaryDirectory(prefix='tl-story-edit-') as task:
            source = Path(task) / 'source.docx'
            source.write_bytes(package.data)
            payload = json.dumps({'operations': [operation(package)]}).encode()
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(SCRIPTS)], cwd=task,
                                    input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(1, result.returncode, result.stderr.decode())
            self.assertIn(b'preservation guard', result.stderr)
            self.assertEqual(['source.docx'], sorted(item.name for item in Path(task).iterdir()))
            command = [sys.executable, '-I', '-B', str(SCRIPTS / 'tl_docx.py'), 'edit', 'source.docx', 'output.docx']
            stale = subprocess.run(command, cwd=task, input=json.dumps({'operations': [operation(package), operation(package)]}).encode(),
                                   capture_output=True, timeout=20, check=False)
            self.assertEqual(1, stale.returncode, stale.stderr.decode())
            self.assertEqual(['source.docx'], sorted(item.name for item in Path(task).iterdir()))
            success = subprocess.run(command, cwd=task, input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(0, success.returncode, success.stderr.decode())
            self.assertEqual('edited', json.loads(success.stdout)['status'])
            self.assertEqual(package.data, source.read_bytes())
            self.assertEqual(['output.docx', 'source.docx'], sorted(item.name for item in Path(task).iterdir()))


if __name__ == '__main__':
    unittest.main()
