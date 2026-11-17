"""Independent formatting invariants and injected editor/serializer faults."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.parts.document import DocumentPart

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_edit
from tl_package import Package


SCRIPTS = Path(__file__).resolve().parents[1]


def operation(formatting, paragraph_index=0):
    """Address the fixture using the same public guards as the CLI."""
    return {'type': 'format_run', 'paragraph_index': paragraph_index, 'expected_text': 'Targetrest',
            'run_index': 0, 'expected_run_text': 'Target', 'format': formatting}


def fixture(rich=False):
    """Generate synthetic content only, including unrelated members and nodes."""
    document = Document()
    paragraph = document.add_paragraph()
    run = paragraph.add_run('Target')
    paragraph.add_run('rest')
    document.add_paragraph('Other paragraph')
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'Other cell'
    document.sections[0].header.paragraphs[0].text = 'Other header'
    paragraph._p.insert(0, OxmlElement('w:bookmarkStart'))  # pylint: disable=protected-access
    if rich:
        props = run._r.get_or_add_rPr()  # pylint: disable=protected-access
        for tag, values in (
                ('rFonts', {'ascii': 'Before', 'asciiTheme': 'minorHAnsi', 'hAnsi': 'Before', 'hAnsiTheme': 'minorHAnsi',
                            'eastAsia': 'Before East', 'eastAsiaTheme': 'minorEastAsia', 'cs': 'Complex', 'cstheme': 'minorBidi', 'hint': 'eastAsia'}),
                ('b', {'val': '0'}), ('i', {'val': '0'}),
                ('u', {'val': 'double', 'color': '123456'}),
                ('color', {'val': '112233', 'themeColor': 'accent1', 'themeTint': '80', 'themeShade': '40'}),
                ('sz', {'val': '24'}), ('szCs', {'val': '24'}), ('lang', {'val': 'en-US', 'eastAsia': 'zh-CN'})):
            node = OxmlElement('w:' + tag)
            for key, value in values.items():
                node.set(qn('w:' + key), value)
            props.append(node)
        # Formatting must not discard fields, drawings or meaningful whitespace.
        run._r.append(OxmlElement('w:fldChar'))  # pylint: disable=protected-access
        run._r.find(qn('w:t')).set(qn('xml:space'), 'preserve')  # pylint: disable=protected-access
    stream = BytesIO()
    document.save(stream)
    package = Package(stream.getvalue())
    return Package(package.overlay({'custom/opaque.bin': bytes(range(128))}))


def edited(package, operations):
    """Execute the actual production edit pipeline, not a guard-only model."""
    result, receipt = tl_edit.edit_document(package, {'operations': operations}, lambda _path: b'')
    return Package(result), receipt


class FormatGuardTests(unittest.TestCase):
    """Check declared changes and reject mutations outside the narrow surface."""

    def test_each_supported_property_and_all_together(self):
        """Both absent and richly populated rPr allow exactly the declared edit."""
        values = {'font_ascii': 'Latin', 'font_east_asia': '黑体', 'size_pt': 18,
                  'bold': True, 'italic': False, 'underline': False, 'color': 'aabbcc'}
        for rich in (False, True):
            package = fixture(rich)
            for formatting in [dict([item]) for item in values.items()] + [values]:
                with self.subTest(rich=rich, formatting=formatting):
                    result, receipt = edited(package, [operation(formatting)])
                    self.assertEqual([] if rich and formatting == {'italic': False} else [package.main], receipt['changed_parts'])
                    self.assertEqual(package.names, result.names)
                    for name in package.names - {package.main}:
                        self.assertEqual(package.read(name), result.read(name))
                    run = result.document().paragraphs[0].runs[0]
                    self.assertEqual('Target', run.text)
                    if 'font_ascii' in formatting:
                        fonts = run._r.rPr.rFonts  # pylint: disable=protected-access
                        self.assertEqual('Latin', fonts.get(qn('w:ascii')))
                        self.assertEqual('Latin', fonts.get(qn('w:hAnsi')))
                        self.assertIsNone(fonts.get(qn('w:asciiTheme')))
                    if rich:
                        props = run._r.rPr  # pylint: disable=protected-access
                        self.assertEqual('Complex', props.rFonts.get(qn('w:cs')))
                        self.assertEqual('123456', props.find(qn('w:u')).get(qn('w:color')))
                        self.assertEqual('en-US', props.find(qn('w:lang')).get(qn('w:val')))
                        self.assertIsNotNone(run._r.find(qn('w:fldChar')))  # pylint: disable=protected-access
                    if 'color' in formatting:
                        color = run._r.rPr.find(qn('w:color'))  # pylint: disable=protected-access
                        self.assertEqual({qn('w:val'): 'AABBCC'}, dict(color.attrib))

    def test_both_boolean_values_and_missing_complex_size(self):
        """Independently assert actual flags, without introducing a complex size."""
        for key in ('bold', 'italic', 'underline'):
            for value in (False, True):
                result, _ = edited(fixture(), [operation({key: value, 'size_pt': 18})])
                run = result.document().paragraphs[0].runs[0]
                self.assertEqual(value, getattr(run.font, key))
                self.assertEqual(18, run.font.size.pt)
                self.assertIsNone(run._r.rPr.find(qn('w:szCs')))  # pylint: disable=protected-access

    def test_existing_size_precision_contract_and_complex_size(self):
        """Retain the existing ordinary truncation / complex rounding contract."""
        for size, ordinary, complex_size in ((12.25, '24', '24'), (12.75, '25', '26'), (18.5, '37', '37')):
            result, _ = edited(fixture(True), [operation({'size_pt': size})])
            props = result.document().paragraphs[0].runs[0]._r.rPr  # pylint: disable=protected-access
            self.assertEqual(ordinary, props.find(qn('w:sz')).get(qn('w:val')))
            self.assertEqual(complex_size, props.find(qn('w:szCs')).get(qn('w:val')))

    def test_repeated_noop_and_mixed_sequential_operations(self):
        """Use each step's actual indexes; other supported edits keep their scope."""
        package = fixture()
        requests = [{'type': 'insert_paragraph', 'paragraph_index': 0, 'expected_text': 'Targetrest', 'text': 'Inserted'},
                    operation({'bold': True}, 1), operation({'bold': True}, 1), operation({'italic': True}, 1)]
        result, receipt = edited(package, requests)
        self.assertEqual(4, receipt['operations'])
        self.assertEqual(['Inserted', 'Targetrest', 'Other paragraph'], [p.text for p in result.document().paragraphs])
        self.assertTrue(result.document().paragraphs[1].runs[0].bold)
        self.assertTrue(result.document().paragraphs[1].runs[0].italic)

    def test_rejects_editor_faults_anywhere_in_main_part(self):
        """Fault injection is independent of the verifier's expected tree logic."""
        original = tl_edit.format_run

        def mutate(run, code):
            node = run._r  # pylint: disable=protected-access
            root = node.getroottree().getroot()
            body = root.find(qn('w:body'))
            if code == 'text':
                node.find(qn('w:t')).text = 'Corrupt'
            elif code == 'other-run':
                node.getnext().find(qn('w:t')).text = 'Corrupt'
            elif code == 'paragraph':
                node.getparent().set(qn('w:rsidR'), '1234')
            elif code == 'section':
                body.find(qn('w:sectPr')).set(qn('w:rsidR'), '1234')
            elif code == 'bookmark':
                node.getparent().find(qn('w:bookmarkStart')).set(qn('w:id'), '99')
            elif code == 'unrequested-font':
                node.rPr.rFonts.set(qn('w:cs'), 'Corrupt')
            elif code == 'unrequested-property':
                node.rPr.find(qn('w:lang')).set(qn('w:val'), 'fr-FR')
            elif code == 'property-order':
                node.rPr.insert(0, node.rPr[-1])
            elif code == 'run-attribute':
                node.set(qn('w:rsidR'), '1234')
            elif code == 'space':
                node.find(qn('w:t')).attrib.pop(qn('xml:space'))
            elif code == 'field':
                node.remove(node.find(qn('w:fldChar')))
            elif code == 'wrong-value':
                node.rPr.rFonts.set(qn('w:eastAsia'), 'Wrong')
            elif code == 'duplicate-property':
                node.rPr.append(deepcopy(node.rPr.rFonts))
            elif code == 'duplicate-rpr':
                node.append(deepcopy(node.rPr))
            elif code == 'new-paragraph':
                body.append(OxmlElement('w:p'))
            elif code == 'removed-paragraph':
                body.remove(body.findall(qn('w:p'))[-1])
            elif code == 'other-cell':
                root.find('.//' + qn('w:tc')).find('.//' + qn('w:t')).text = 'Corrupt'

        faults = ('text', 'other-run', 'paragraph', 'section', 'bookmark', 'unrequested-font', 'unrequested-property',
                  'property-order', 'run-attribute', 'space', 'field', 'wrong-value', 'duplicate-property',
                  'duplicate-rpr', 'new-paragraph', 'removed-paragraph', 'other-cell')
        for fault in faults:
            def broken(run, formatting, code=fault):
                original(run, formatting)
                mutate(run, code)
            with self.subTest(fault=fault), patch.object(tl_edit, 'format_run', broken):
                with self.assertRaisesRegex(ValueError, 'preservation guard'):
                    edited(fixture(True), [operation({'font_east_asia': '黑体'})])

    def test_duplicate_selected_source_properties_rejected(self):
        """Do not guess which duplicate OOXML property represents intent."""
        package = fixture(True)
        root = deepcopy(package.xml(package.main))
        props = root.find('.//' + qn('w:rPr'))
        props.append(deepcopy(props.find(qn('w:rFonts'))))
        from docx.opc.oxml import serialize_part_xml  # pylint: disable=import-outside-toplevel
        package = Package(package.overlay({package.main: serialize_part_xml(root)}))
        with self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation({'font_ascii': 'New'})])

    def test_component_load_fault_rejected(self):
        """Compare the loaded component tree against original package XML."""
        package = fixture()
        original = Package.document

        def broken(instance):
            document = original(instance)
            document.paragraphs[1].text = 'Corrupt load'
            return document

        with patch.object(Package, 'document', broken), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation({'bold': True})])

    def test_serializer_mutation_and_overlay_faults_rejected(self):
        """Compare final bytes to an approved snapshot, not mutable live state."""
        package = fixture()
        getter = DocumentPart.blob.fget

        def broken_blob(part):
            part.element.find('.//' + qn('w:t')).text = 'Corrupt serializer'
            return getter(part)

        with patch.object(DocumentPart, 'blob', property(broken_blob)):
            with self.assertRaisesRegex(ValueError, 'preservation guard'):
                edited(package, [operation({'bold': True})])
        original = Package.overlay

        def broken_overlay(instance, replacements):
            replacements[instance.main] = replacements[instance.main].replace(b'Target', b'Broken')
            return original(instance, replacements)

        with patch.object(Package, 'overlay', broken_overlay), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation({'bold': True})])

    def test_cli_fault_never_publishes_or_changes_source(self):
        """Exercise actual CLI error output and prove no final or temp file exists."""
        code = """import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import tl_edit
import tl_docx
original = tl_edit.format_run
def broken(run, formatting):
    original(run, formatting)
    run.text = 'Corrupt'
with patch.object(tl_edit, 'format_run', broken):
    raise SystemExit(tl_docx.main(['edit', 'source.docx', 'output.docx']))
"""
        with tempfile.TemporaryDirectory(prefix='tl-format-guard-') as task:
            source = Path(task) / 'source.docx'
            data = fixture().data
            source.write_bytes(data)
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(SCRIPTS)], cwd=task,
                                    input=json.dumps({'operations': [operation({'bold': True})]}).encode(),
                                    capture_output=True, timeout=20, check=False)
            self.assertEqual(1, result.returncode, result.stderr.decode())
            self.assertEqual('error', json.loads(result.stdout)['status'])
            self.assertIn(b'preservation guard', result.stderr)
            self.assertEqual(data, source.read_bytes())
            self.assertEqual(['source.docx'], sorted(path.name for path in Path(task).iterdir()))


if __name__ == '__main__':
    unittest.main()
