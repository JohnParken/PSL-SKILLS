"""Synthetic merged-origin geometry proofs and independently guarded editing."""

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_edit
from tl_package import Package

SCRIPTS = Path(__file__).resolve().parents[1]


def fixture(colspan=2, rowspan=2):
    """Use ordinary producer-generated gridSpan/vMerge, never private documents."""
    document = Document()
    document.add_paragraph('Body unchanged')
    table = document.add_table(rows=4, cols=4)
    cell = table.cell(1, 1).merge(table.cell(rowspan, colspan))
    cell.text = 'First paragraph'
    paragraph = cell.add_paragraph()
    paragraph.add_run('Before').italic = True
    paragraph.add_run('Target').bold = True
    paragraph.add_run('After')
    cell.add_paragraph()
    table.cell(0, 0).text = 'Other cell'
    document.sections[0].header.paragraphs[0].text = 'Header unchanged'
    output = BytesIO()
    document.save(output)
    package = Package(output.getvalue())
    return Package(package.overlay({'custom/opaque.bin': bytes(range(64))}))


def request(colspan=2, rowspan=2, kind='replace_cell_run_text', **changes):
    """Opt in only with independently expected grid coordinate and rectangle size."""
    value = {'type': kind, 'table_index': 0, 'row_index': 1, 'cell_index': 1, 'paragraph_index': 1, 'run_index': 1,
             'expected_text': 'BeforeTargetAfter', 'expected_run_text': 'Target',
             'expected_merge': {'grid_column': 1, 'colspan': colspan, 'rowspan': rowspan}}
    value.update({'format': {'font_east_asia': '黑体', 'size_pt': 18}} if kind == 'format_cell_run' else {'text': 'New'})
    value.update(changes)
    return value


def edited(package, operations):
    """Exercise the production serialized-package pipeline."""
    data, receipt = tl_edit.edit_document(package, {'operations': operations}, None)
    return Package(data), receipt


def physical(root, row=1, cell=1):
    """Locate actual XML cells, deliberately avoiding python-docx merge aliases."""
    table = root.find('./' + qn('w:body') + '/' + qn('w:tbl'))
    return table, table.findall(qn('w:tr'))[row].findall(qn('w:tc'))[cell]


def mutated(change, colspan=2, rowspan=2):
    """Make a bounded adversarial XML fixture in memory."""
    package = fixture(colspan, rowspan)
    root = deepcopy(package.xml(package.main))
    table, cell = physical(root)
    change(root, table, cell)
    return Package(package.overlay({package.main: serialize_part_xml(root)}))


class MergedCellOriginTests(unittest.TestCase):
    """Merged editing changes only an explicit origin's selected ordinary run."""

    def test_horizontal_vertical_and_rectangular_origins(self):
        """Three merge shapes retain their properties, continuations and all other members."""
        for colspan, rowspan in ((2, 1), (1, 2), (2, 2), (2, 3)):
            for kind in ('format_cell_run', 'replace_cell_run_text'):
                with self.subTest(colspan=colspan, rowspan=rowspan, kind=kind):
                    package = fixture(colspan, rowspan)
                    result, receipt = edited(package, [request(colspan, rowspan, kind)])
                    self.assertEqual([package.main], receipt['changed_parts'])
                    for name in package.names - {package.main}:
                        self.assertEqual(package.read(name), result.read(name))
                    before, after = deepcopy(package.xml(package.main)), deepcopy(result.xml(package.main))
                    old_cell, new_cell = physical(before)[1], physical(after)[1]
                    self.assertEqual(serialize_part_xml(old_cell.find(qn('w:tcPr'))), serialize_part_xml(new_cell.find(qn('w:tcPr'))))
                    old_run = old_cell.findall(qn('w:p'))[1].findall(qn('w:r'))[1]
                    new_run = new_cell.findall(qn('w:p'))[1].findall(qn('w:r'))[1]
                    self.assertEqual('New' if kind == 'replace_cell_run_text' else 'Target', new_run.find(qn('w:t')).text)
                    if kind == 'format_cell_run':
                        self.assertEqual('黑体', new_run.find('./' + qn('w:rPr') + '/' + qn('w:rFonts')).get(qn('w:eastAsia')))
                    else:
                        self.assertEqual(serialize_part_xml(old_run.find(qn('w:rPr'))), serialize_part_xml(new_run.find(qn('w:rPr'))))
                    old_run.getparent().remove(old_run)
                    new_run.getparent().remove(new_run)
                    self.assertEqual(serialize_part_xml(before), serialize_part_xml(after))

    def test_physical_index_is_not_visual_grid_column(self):
        """A preceding horizontal merge makes physical cell 1 start at grid column 2."""
        def move_origin(_root, table, _cell):
            for row in table.findall(qn('w:tr'))[1:3]:
                cells = row.findall(qn('w:tc'))
                span = OxmlElement('w:gridSpan')
                span.set(qn('w:val'), '2')
                cells[0].find(qn('w:tcPr')).append(span)
                row.remove(cells[-1])
        package = mutated(move_origin)
        result, _ = edited(package, [request(expected_merge={'grid_column': 2, 'colspan': 2, 'rowspan': 2})])
        self.assertIn(b'New', serialize_part_xml(physical(result.xml(result.main))[1]))
        with self.assertRaisesRegex(ValueError, 'expected_merge'):
            edited(package, [request()])

    def test_explicit_continuation_and_empty_styled_run_preserved(self):
        """The standard explicit continuation form and harmless empty formatting are allowed."""
        def explicit(_root, table, _cell):
            continuation = table.findall(qn('w:tr'))[2].findall(qn('w:tc'))[1]
            continuation.find('./' + qn('w:tcPr') + '/' + qn('w:vMerge')).set(qn('w:val'), 'continue')
            run = OxmlElement('w:r')
            props = OxmlElement('w:rPr')
            props.append(OxmlElement('w:b'))
            run.append(props)
            continuation.find(qn('w:p')).append(run)
        package = mutated(explicit)
        result, _ = edited(package, [request()])
        self.assertEqual(serialize_part_xml(physical(package.xml(package.main), row=2)[1]),
                         serialize_part_xml(physical(result.xml(result.main), row=2)[1]))

    def test_opt_in_required_and_geometry_exact(self):
        """Old calls stay conservative, and declarations cannot guess the merge extent."""
        package = fixture()
        old = request()
        del old['expected_merge']
        with self.assertRaisesRegex(ValueError, 'expected_merge'):
            edited(package, [old])
        invalid = [{'grid_column': 0, 'colspan': 2, 'rowspan': 2}, {'grid_column': 1, 'colspan': 1, 'rowspan': 2},
                   {'grid_column': 1, 'colspan': 2, 'rowspan': 3}, {'grid_column': True, 'colspan': 2, 'rowspan': 2},
                   {'grid_column': 1, 'colspan': 0, 'rowspan': 2}, {'grid_column': 1, 'colspan': 2}, None]
        for expected in invalid:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                edited(package, [request(expected_merge=expected)])
        with self.assertRaises(ValueError):
            edited(fixture(1, 1), [request(1, 1)])

    def test_continuation_is_never_redirected_to_origin(self):
        """Targeting the physical continuation must fail, even if visible APIs alias it."""
        with self.assertRaisesRegex(ValueError, 'continuation cell'):
            edited(fixture(), [request(row_index=2, paragraph_index=0, run_index=0, expected_text='', expected_run_text='')])

    def test_ambiguous_topology_and_nonempty_continuation_refused(self):
        """Reject unsupported old merge syntax, incomplete grids and hidden content."""
        def change(_root, table, cell, code):
            props = cell.find(qn('w:tcPr'))
            continuation = table.findall(qn('w:tr'))[2].findall(qn('w:tc'))[1]
            if code == 'hMerge':
                props.append(OxmlElement('w:hMerge'))
            elif code == 'duplicate-grid':
                table.insert(1, deepcopy(table.find(qn('w:tblGrid'))))
            elif code == 'duplicate-table-props':
                table.insert(0, deepcopy(table.find(qn('w:tblPr'))))
            elif code == 'missing-grid':
                table.remove(table.find(qn('w:tblGrid')))
            elif code == 'duplicate-span':
                props.append(deepcopy(props.find(qn('w:gridSpan'))))
            elif code == 'duplicate-vmerge':
                props.append(deepcopy(props.find(qn('w:vMerge'))))
            elif code in ('zero-span', 'bad-span'):
                props.find(qn('w:gridSpan')).set(qn('w:val'), '0' if code == 'zero-span' else 'bad')
            elif code == 'bad-vmerge':
                props.find(qn('w:vMerge')).set(qn('w:val'), 'unknown')
            elif code == 'row-gap':
                row = table.findall(qn('w:tr'))[2]
                row.remove(row.findall(qn('w:tc'))[-1])
            elif code == 'gridBefore':
                row = table.findall(qn('w:tr'))[2]
                prop = OxmlElement('w:trPr')
                prop.append(OxmlElement('w:gridBefore'))
                row.insert(0, prop)
            elif code == 'width-mismatch':
                continuation.find('./' + qn('w:tcPr') + '/' + qn('w:gridSpan')).set(qn('w:val'), '1')
                row = continuation.getparent()
                row.append(deepcopy(row.findall(qn('w:tc'))[-1]))
            elif code == 'orphan-continuation':
                first = table.findall(qn('w:tr'))[0].findall(qn('w:tc'))[0]
                first.find(qn('w:tcPr')).append(OxmlElement('w:vMerge'))
            elif code == 'revised-continuation':
                continuation.find(qn('w:tcPr')).append(OxmlElement('w:cellDel'))
            elif code in ('hidden-text', 'field', 'numbering'):
                paragraph = continuation.find(qn('w:p'))
                if code == 'numbering':
                    prop = OxmlElement('w:pPr')
                    prop.append(OxmlElement('w:numPr'))
                    paragraph.append(prop)
                else:
                    run = OxmlElement('w:r')
                    node = OxmlElement('w:t' if code == 'hidden-text' else 'w:fldChar')
                    if code == 'hidden-text':
                        node.text = 'Hidden'
                    run.append(node)
                    paragraph.append(run)
        codes = ('hMerge', 'duplicate-grid', 'duplicate-table-props', 'missing-grid', 'duplicate-span', 'duplicate-vmerge', 'zero-span', 'bad-span',
                 'bad-vmerge', 'row-gap', 'gridBefore', 'width-mismatch', 'orphan-continuation', 'revised-continuation', 'hidden-text', 'field', 'numbering')
        for code in codes:
            package = mutated(lambda *args, code=code: change(*args, code))
            for kind in ('format_cell_run', 'replace_cell_run_text'):
                with self.subTest(code=code, kind=kind), self.assertRaises(ValueError):
                    edited(package, [request(kind=kind)])

    def test_sequential_and_mixed_body_edits_keep_merge_geometry(self):
        """Per-step anchors remain valid while unrelated supported operations stay ordered."""
        package = fixture()
        operations = [request(), request(kind='format_cell_run', expected_text='BeforeNewAfter', expected_run_text='New'),
                      {'type': 'format_run', 'paragraph_index': 0, 'expected_text': 'Body unchanged',
                       'run_index': 0, 'expected_run_text': 'Body unchanged', 'format': {'bold': True}}]
        result, _ = edited(package, operations)
        self.assertTrue(result.document().paragraphs[0].runs[0].bold)
        self.assertEqual(serialize_part_xml(physical(package.xml(package.main))[1].find(qn('w:tcPr'))),
                         serialize_part_xml(physical(result.xml(result.main))[1].find(qn('w:tcPr'))))

    def test_independent_guard_rejects_continuation_when_preflight_is_faulty(self):
        """A bug bypassing topology validation must not let a continuation act as an origin."""
        def add_empty_run(_root, table, _cell):
            continuation = table.findall(qn('w:tr'))[2].findall(qn('w:tc'))[1]
            continuation.find(qn('w:p')).append(OxmlElement('w:r'))
        package = mutated(add_empty_run)
        operation = request(row_index=2, paragraph_index=0, run_index=0, expected_text='', expected_run_text='',
                            expected_merge={'grid_column': 1, 'colspan': 2, 'rowspan': 1})
        with patch.object(tl_edit, 'merged_cell_origin', return_value=None), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(package, [operation])
        with patch.object(tl_edit, 'merged_cell_origin', return_value=None), self.assertRaisesRegex(ValueError, 'preservation guard'):
            edited(fixture(), [request(expected_merge={'grid_column': 1, 'colspan': 2, 'rowspan': 3})])

    def test_faults_in_merge_properties_continuations_and_other_cells_rejected(self):
        """Only one run payload/properties may change, not the merge or any physical neighbour."""
        original = tl_edit.edit_cell_run
        for code in ('span', 'continuation', 'other-cell', 'row-order', 'wrong-text'):
            for kind in ('format_cell_run', 'replace_cell_run_text'):
                def broken(document, operation, code=code):
                    original(document, operation)
                    table, cell = physical(document.element)
                    if code == 'span':
                        cell.find('./' + qn('w:tcPr') + '/' + qn('w:gridSpan')).set(qn('w:val'), '3')
                    elif code == 'continuation':
                        table.findall(qn('w:tr'))[2].findall(qn('w:tc'))[1].set('changed', 'yes')
                    elif code == 'other-cell':
                        table.find('.//' + qn('w:t')).text = 'Wrong'
                    elif code == 'row-order':
                        table.append(table.findall(qn('w:tr'))[0])
                    else:
                        cell.findall(qn('w:p'))[1].findall(qn('w:r'))[1].find(qn('w:t')).text = 'Wrong'
                with self.subTest(code=code, kind=kind), patch.object(tl_edit, 'edit_cell_run', broken):
                    with self.assertRaisesRegex(ValueError, 'preservation guard'):
                        edited(fixture(), [request(kind=kind)])

    def test_legacy_whole_cell_and_row_operations_still_reject(self):
        """Adding an origin opt-in does not enable flattening or merged-row mutation."""
        operations = [{'type': 'replace_cell_text', 'table_index': 0, 'row_index': 1, 'cell_index': 1,
                       'expected_text': 'First paragraph\nBeforeTargetAfter\n', 'text': 'Flat'},
                      {'type': 'delete_row', 'table_index': 0, 'row_index': 1, 'expected_rows': 4, 'expected_text': ['', '', '']}]
        for operation in operations:
            with self.subTest(kind=operation['type']), self.assertRaises(ValueError):
                edited(fixture(), [operation])

    def test_cli_rejects_fault_before_publish_and_accepts_valid_origin(self):
        """The final publication gate keeps the input and cleanup state intact on failure."""
        script = """import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import tl_edit
import tl_docx
original = tl_edit.edit_cell_run
def broken(document, operation):
    original(document, operation)
    document.paragraphs[0].text = 'Wrong'
with patch.object(tl_edit, 'edit_cell_run', broken):
    raise SystemExit(tl_docx.main(['edit', 'source.docx', 'output.docx']))
"""
        package = fixture()
        with tempfile.TemporaryDirectory(prefix='tl-merged-origin-') as task:
            source = Path(task) / 'source.docx'
            source.write_bytes(package.data)
            payload = json.dumps({'operations': [request()]}).encode()
            failed = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(SCRIPTS)], cwd=task,
                                    input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(1, failed.returncode, failed.stderr.decode())
            self.assertIn(b'preservation guard', failed.stderr)
            self.assertEqual(['source.docx'], sorted(item.name for item in Path(task).iterdir()))
            command = [sys.executable, '-I', '-B', str(SCRIPTS / 'tl_docx.py'), 'edit', 'source.docx', 'output.docx']
            success = subprocess.run(command, cwd=task, input=payload, capture_output=True, timeout=20, check=False)
            self.assertEqual(0, success.returncode, success.stderr.decode())
            self.assertEqual('edited', json.loads(success.stdout)['status'])
            self.assertEqual(package.data, source.read_bytes())
            self.assertEqual(['output.docx', 'source.docx'], sorted(item.name for item in Path(task).iterdir()))


if __name__ == '__main__':
    unittest.main()
