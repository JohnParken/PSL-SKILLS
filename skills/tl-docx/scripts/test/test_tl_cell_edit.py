"""Synthetic table-run edit contracts, preservation invariants and fault gates."""

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
from docx.opc.oxml import serialize_part_xml
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.parts.document import DocumentPart

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_edit
from tl_package import Package

SCRIPTS = Path(__file__).resolve().parents[1]


def fixture():
    """Keep several differently formatted runs and paragraphs in a physical cell."""
    document = Document()
    document.add_paragraph('Body unchanged')
    table = document.add_table(rows=2, cols=2)
    cell = table.cell(0, 0)
    cell.paragraphs[0].text = 'First paragraph'
    paragraph = cell.add_paragraph()
    paragraph.add_run('Before').italic = True
    paragraph.add_run('Target').bold = True
    paragraph.add_run('After')
    cell.add_paragraph()  # A trailing empty paragraph must not be discarded.
    table.cell(0, 1).text = 'Neighbour'
    table.cell(1, 0).text = 'Other row'
    document.sections[0].header.paragraphs[0].text = 'Header unchanged'
    document.sections[0].footer.paragraphs[0].text = 'Footer unchanged'
    output = BytesIO()
    document.save(output)
    package = Package(output.getvalue())
    return Package(package.overlay({'custom/opaque.bin': bytes(range(128))}))


def operation(kind='replace_cell_run_text', **changes):
    """Use only the public physical-cell and paragraph/run anchors."""
    result = {'type': kind, 'table_index': 0, 'row_index': 0, 'cell_index': 0,
              'paragraph_index': 1, 'run_index': 1, 'expected_text': 'BeforeTargetAfter', 'expected_run_text': 'Target'}
    result.update({'format': {'font_east_asia': '黑体', 'size_pt': 18}} if kind == 'format_cell_run' else {'text': '新文字'})
    result.update(changes)
    return result


def edited(package, requests):
    """Exercise the production pipeline, including serialize, overlay and reload."""
    data, receipt = tl_edit.edit_document(package, {'operations': requests}, None)
    return Package(data), receipt


def target(root):
    """Locate the fixture independently of the runtime's target selector."""
    cell = root.find('./' + qn('w:body') + '/' + qn('w:tbl') + '/' + qn('w:tr') + '/' + qn('w:tc'))
    paragraph = cell.findall(qn('w:p'))[1]
    return cell, paragraph, paragraph.findall(qn('w:r'))[1]


def changed_source(mutate):
    """Construct an adversarial synthetic OOXML variant without private samples."""
    package = fixture()
    root = deepcopy(package.xml(package.main))
    mutate(root, *target(root))
    return Package(package.overlay({package.main: serialize_part_xml(root)}))


class CellRunEditTests(unittest.TestCase):
    """New cell-run support must not weaken legacy whole-cell or row restrictions."""

    def test_text_and_format_preserve_other_runs_paragraphs_and_parts(self):
        """Verify the target result and independently compare all non-target XML."""
        for kind in ('replace_cell_run_text', 'format_cell_run'):
            with self.subTest(kind=kind):
                package = fixture()
                result, receipt = edited(package, [operation(kind)])
                self.assertEqual([package.main], receipt['changed_parts'])
                self.assertEqual(package.names, result.names)
                for part in package.names - {package.main}:
                    self.assertEqual(package.read(part), result.read(part))
                old, new = deepcopy(package.xml(package.main)), deepcopy(result.xml(result.main))
                old_cell, old_paragraph, old_run = target(old)
                new_cell, new_paragraph, new_run = target(new)
                self.assertEqual(3, len(new_cell.findall(qn('w:p'))))
                self.assertEqual(serialize_part_xml(old_cell.find(qn('w:tcPr'))), serialize_part_xml(new_cell.find(qn('w:tcPr'))))
                if kind == 'replace_cell_run_text':
                    self.assertEqual('新文字', new_run.find(qn('w:t')).text)
                    self.assertEqual(serialize_part_xml(old_run.find(qn('w:rPr'))), serialize_part_xml(new_run.find(qn('w:rPr'))))
                else:
                    self.assertEqual('Target', new_run.find(qn('w:t')).text)
                    self.assertEqual('黑体', new_run.find('./' + qn('w:rPr') + '/' + qn('w:rFonts')).get(qn('w:eastAsia')))
                    self.assertEqual('36', new_run.find('./' + qn('w:rPr') + '/' + qn('w:sz')).get(qn('w:val')))
                    self.assertIsNotNone(new_run.find('./' + qn('w:rPr') + '/' + qn('w:b')))
                old_paragraph.remove(old_run)
                new_paragraph.remove(new_run)
                self.assertEqual(serialize_part_xml(old), serialize_part_xml(new))

    def test_whitespace_empty_and_sequential_runs(self):
        """Text normalization is explicit; the following operation uses current anchors."""
        for value in ('', ' Target ', '\t甲\n乙\t', '\n\n', 'A\u00a0'):
            with self.subTest(value=value):
                result, _ = edited(fixture(), [operation(text=value),
                                              operation('format_cell_run', expected_text='Before' + value + 'After', expected_run_text=value)])
                paragraph = result.document().tables[0].cell(0, 0).paragraphs[1]
                self.assertEqual(['Before', value, 'After'], [run.text for run in paragraph.runs])
                self.assertTrue(paragraph.runs[1].bold)
        result, _ = edited(fixture(), [operation(text='Target'), operation(text='Target')])
        self.assertEqual('BeforeTargetAfter', result.document().tables[0].cell(0, 0).paragraphs[1].text)

    def test_empty_run_without_properties_and_complex_other_cell(self):
        """An unrelated complex cell does not force flattening or rejection."""
        def mutate(_root, cell, _paragraph, _run):
            empty = OxmlElement('w:r')
            cell.findall(qn('w:p'))[2].append(empty)
            neighbour = cell.getnext()
            neighbour.append(OxmlElement('w:tbl'))
        package = changed_source(mutate)
        request = operation(paragraph_index=2, run_index=0, expected_text='', expected_run_text='', text='Value')
        result, _ = edited(package, [request])
        self.assertEqual('Value', result.document().tables[0].cell(0, 0).paragraphs[2].text)

    def test_bad_anchors_fields_and_invalid_text_rejected(self):
        """Do not accept bool/negative indexing, stale text or unknown input fields."""
        changes = [{key: value} for key in ('table_index', 'row_index', 'cell_index', 'paragraph_index', 'run_index')
                   for value in (-1, True, 99)]
        changes += [{'expected_text': 'stale'}, {'expected_run_text': 'stale'}, {'unknown': 1}, {'text': 'bad\rtext'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                edited(fixture(), [operation(**change)])
        with self.assertRaises(ValueError):
            edited(fixture(), [operation('format_cell_run', format={'bold': 'yes'})])

    def test_unsafe_cell_structures_rejected_for_both_operations(self):
        """Reject merged, nested, revision, field and range structures before editing."""
        def mutate(code, _root, cell, paragraph, run):
            if code in ('row-ins', 'row-del', 'trPrChange'):
                props = OxmlElement('w:trPr')
                props.append(OxmlElement('w:' + code.removeprefix('row-')))
                cell.getparent().insert(0, props)
            elif code == 'tblPrChange':
                cell.getparent().getparent().find(qn('w:tblPr')).append(OxmlElement('w:tblPrChange'))
            elif code in ('gridSpan', 'vMerge', 'hMerge', 'tcPrChange', 'cellIns', 'cellDel', 'cellMerge'):
                cell.find(qn('w:tcPr')).append(OxmlElement('w:' + code))
            elif code in ('tbl', 'sdt'):
                cell.append(OxmlElement('w:' + code))
            elif code in ('bookmarkStart', 'hyperlink', 'ins'):
                paragraph.append(OxmlElement('w:' + code))
            elif code in ('fldChar', 'drawing'):
                run.append(OxmlElement('w:' + code))
            elif code in ('rPrChange', 'property-ins', 'property-del'):
                run.find(qn('w:rPr')).append(OxmlElement('w:' + code.removeprefix('property-')))
            elif code == 'duplicate-rpr':
                run.append(deepcopy(run.find(qn('w:rPr'))))
            elif code == 'page-break':
                node = OxmlElement('w:br')
                node.set(qn('w:type'), 'page')
                run.append(node)
        for code in ('gridSpan', 'vMerge', 'hMerge', 'tcPrChange', 'tbl', 'sdt', 'bookmarkStart', 'hyperlink', 'ins',
                     'fldChar', 'drawing', 'rPrChange', 'duplicate-rpr', 'page-break', 'row-ins', 'row-del', 'trPrChange', 'tblPrChange',
                     'cellIns', 'cellDel', 'cellMerge', 'property-ins', 'property-del'):
            package = changed_source(lambda *args, code=code: mutate(code, *args))
            for kind in ('format_cell_run', 'replace_cell_run_text'):
                with self.subTest(code=code, kind=kind), self.assertRaises(ValueError):
                    edited(package, [operation(kind)])

    def test_old_whole_cell_and_row_edit_still_reject_complex_cells(self):
        """The shared old cell_run helper must retain its stricter contract."""
        requests = [
            {'type': 'replace_cell_text', 'table_index': 0, 'row_index': 0, 'cell_index': 0,
             'expected_text': 'First paragraph\nBeforeTargetAfter\n', 'text': 'Flattened'},
            {'type': 'insert_row', 'table_index': 0, 'row_index': 0, 'expected_rows': 2,
             'expected_text': ['First paragraph\nBeforeTargetAfter\n', 'Neighbour'], 'position': 'after', 'cells': ['A', 'B']},
        ]
        for request in requests:
            with self.subTest(kind=request['type']), self.assertRaises(ValueError):
                edited(fixture(), [request])

    def test_editor_faults_rejected_by_independent_full_tree_guard(self):
        """Mutations in every protected layer must fail before serialization/publication."""
        original = tl_edit.edit_cell_run
        def mutate(document, code):
            cell, paragraph, run = target(document.element)
            if code == 'wrong-text':
                run.find(qn('w:t')).text = 'Wrong'
            elif code == 'format':
                run.find(qn('w:rPr')).set(qn('w:rsidRPr'), '1234')
            elif code == 'other-run':
                paragraph.findall(qn('w:r'))[0].find(qn('w:t')).text = 'Wrong'
            elif code == 'other-paragraph':
                cell.find(qn('w:p')).find('.//' + qn('w:t')).text = 'Wrong'
            elif code == 'other-cell':
                cell.getnext().find('.//' + qn('w:t')).text = 'Wrong'
            elif code == 'cell-property':
                cell.find(qn('w:tcPr')).append(OxmlElement('w:shd'))
            elif code == 'body':
                document.paragraphs[0].text = 'Wrong'
            elif code == 'section':
                document.element.find('.//' + qn('w:sectPr')).set(qn('w:rsidR'), '1234')
            elif code == 'run-attribute':
                run.set(qn('w:rsidR'), '1234')
            elif code == 'new-paragraph':
                cell.append(OxmlElement('w:p'))
        for kind in ('format_cell_run', 'replace_cell_run_text'):
            for code in ('wrong-text', 'format', 'other-run', 'other-paragraph', 'other-cell', 'cell-property',
                         'body', 'section', 'run-attribute', 'new-paragraph'):
                def broken(document, request, code=code):
                    original(document, request)
                    mutate(document, code)
                with self.subTest(kind=kind, code=code), patch.object(tl_edit, 'edit_cell_run', broken):
                    with self.assertRaisesRegex(ValueError, 'preservation guard'):
                        edited(fixture(), [operation(kind)])

    def test_input_request_mutation_cannot_redefine_allowed_change(self):
        """The allowed edit must be captured independently before editor invocation."""
        original = tl_edit.edit_cell_run
        def broken(document, request):
            request['text'] = 'Wrong'
            original(document, request)
        with patch.object(tl_edit, 'edit_cell_run', broken), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(fixture(), [operation()])

    def test_serializer_and_loader_faults_rejected(self):
        """Serialization and component loading cannot silently mutate unrelated content."""
        package = fixture()
        original = Package.document
        def broken_load(instance):
            document = original(instance)
            document.paragraphs[0].text = 'Wrong'
            return document
        with patch.object(Package, 'document', broken_load), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation()])
        getter = DocumentPart.blob.fget
        def broken_blob(part):
            part.element.find('.//' + qn('w:t')).text = 'Wrong'
            return getter(part)
        with patch.object(DocumentPart, 'blob', property(broken_blob)), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation()])

    def test_cli_failure_never_publishes_and_success_preserves_source(self):
        """Actual CLI invocation distinguishes successful output from rejected batches."""
        script = """import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import tl_edit
import tl_docx
original = tl_edit.edit_cell_run
def broken(document, request):
    original(document, request)
    document.paragraphs[0].text = 'Wrong'
with patch.object(tl_edit, 'edit_cell_run', broken):
    raise SystemExit(tl_docx.main(['edit', 'source.docx', 'output.docx']))
"""
        with tempfile.TemporaryDirectory(prefix='tl-cell-edit-') as task:
            source = Path(task) / 'source.docx'
            data = fixture().data
            source.write_bytes(data)
            payload = json.dumps({'operations': [operation()]}).encode()
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(SCRIPTS)], cwd=task,
                                    input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(1, result.returncode, result.stderr.decode())
            self.assertIn(b'preservation guard', result.stderr)
            self.assertEqual(['source.docx'], sorted(item.name for item in Path(task).iterdir()))
            requests = [operation(), operation(expected_text='stale')]
            command = [sys.executable, '-I', '-B', str(SCRIPTS / 'tl_docx.py'), 'edit', 'source.docx', 'output.docx']
            stale = subprocess.run(command, cwd=task, input=json.dumps({'operations': requests}).encode(),
                                   capture_output=True, timeout=20, check=False)
            self.assertEqual(1, stale.returncode, stale.stderr.decode())
            self.assertEqual(['source.docx'], sorted(item.name for item in Path(task).iterdir()))
            success = subprocess.run(command, cwd=task, input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(0, success.returncode, success.stderr.decode())
            self.assertEqual('edited', json.loads(success.stdout)['status'])
            self.assertEqual(data, source.read_bytes())
            self.assertEqual(['output.docx', 'source.docx'], sorted(item.name for item in Path(task).iterdir()))


if __name__ == '__main__':
    unittest.main()
