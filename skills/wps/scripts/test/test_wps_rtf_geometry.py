"""Independent literal RTF merge grids and conservative fallback expectations."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import task_api

import tempfile
import unittest
from read_wps import read_bytes
from run_wps_edit import execute_edit_task
from wps_rtf_builder import build_document_from_spec

LITERAL = (br'{\rtf1\ansi before\par '
           br'\trowd\clmgf\clvmgf\cellx1000\clmrg\clvmgf\cellx2000\cellx3000 '
           br'\pard\intbl anchor\cell\cell side1\cell\row '
           br'\trowd\clmgf\clvmrg\cellx1000\clmrg\clvmrg\cellx2000\cellx3000 '
           br'\pard\intbl\cell\cell side2\cell\row after\par}')


class GeometryTests(unittest.TestCase):
    def test_independent_literal_rectangle_and_order(self):
        result = read_bytes(LITERAL)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['tables'][0]['merges'], [[0, 0, 1, 1]])
        self.assertEqual(result['tables'][0]['cell_boundaries_twips'], [1000, 2000, 3000])
        self.assertEqual([block['type'] for block in result['blocks']], ['paragraph', 'table', 'paragraph'])
        self.assertEqual(result['tables'][0]['rows'][1][2]['text'], 'side2')

    def test_rectangle_generation_and_readback(self):
        spec = {'page_numbering': False, 'blocks': [{'type': 'table', 'repeat_header': False,
                'rows': [['anchor', '', 'a'], ['', '', 'b']], 'merges': [[0, 0, 1, 1]]}]}
        raw = build_document_from_spec(spec).render().encode('ascii')
        self.assertEqual(raw.count(br'\clmgf'), 2)
        self.assertEqual(raw.count(br'\clvmgf'), 2)
        self.assertEqual(raw.count(br'\clvmrg'), 2)
        self.assertEqual(read_bytes(raw)['tables'][0]['merges'], [[0, 0, 1, 1]])

    def test_damaged_grid_stays_partial_and_keeps_cell_text(self):
        for raw in (LITERAL.replace(br'\row ', br'\trleft100\row ', 1), LITERAL.replace(br'\trowd', br'\trowd\trleft100', 1), LITERAL.replace(br'\cellx2000', br'\cellx900', 1),
                    LITERAL.replace(br'\clmgf\clvmrg', br'\clmgf\clvmgf'),
                    LITERAL.replace(br'\clmgf\clvmgf', br'\clmrg\clvmgf', 1)):
            result = read_bytes(raw)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['tables'][0]['rows'][0][0]['text'], 'anchor')
            self.assertTrue(result['issues'])

    def test_nonempty_covered_text_not_dropped_or_silently_rebuilt(self):
        result = read_bytes(LITERAL.replace(br'anchor\cell\cell', br'anchor\cell covered\cell'))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['tables'][0]['rows'][0][1]['text'], 'covered')
        self.assertEqual(result['issues'][0]['code'], 'rtf_merge_covered_content_retained')

    def test_edit_roundtrip_keeps_geometry_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / 'input.rtf', Path(temporary) / 'output.rtf'
            source.write_bytes(LITERAL)
            task_api(execute_edit_task, source, output, [{'operation': 'replace_text', 'find': 'anchor', 'replace': 'changed'}])
            result = read_bytes(output.read_bytes())
            self.assertEqual(result['tables'][0]['merges'], [[0, 0, 1, 1]])
            self.assertEqual(result['tables'][0]['rows'][0][0]['text'], 'changed')
            self.assertEqual(source.read_bytes(), LITERAL)


if __name__ == '__main__':
    unittest.main()
