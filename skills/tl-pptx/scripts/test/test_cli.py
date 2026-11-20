"""Independent regressions for the SVG boundary and runner failure receipts."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

import tl_assemble
import tl_pptx
from tl_guard import ContractError, publish_exclusive, validate_svg

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"/>'


@contextlib.contextmanager
def task_directory():
    """Create and clean a test-owned workspace without changing source files."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(previous)


class CliRegressionTests(unittest.TestCase):
    """Check observable failure behavior, without persistent module stubs."""

    def test_project_icon_reference_is_rejected(self):
        """data-icon must not resolve an unvalidated task-local icons file."""
        svg = SVG.replace('/>', '><use data-icon="local-secret" width="50" height="50"/></svg>')
        with self.assertRaises(ContractError):
            validate_svg(svg.encode('utf-8'))

    def test_argument_error_is_one_json_result(self):
        """An invalid command still returns machine-readable stdout and exit 2."""
        completed = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT_ROOT / 'tl_pptx.py'), 'nonsense'],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)

    def test_archive_exception_preserves_verified_publication(self):
        """Archive failure is partial delivery, not an unreported output file."""
        archive_module = types.ModuleType('archive_delivery')
        def fail_archive(*_args, **_kwargs):
            raise OSError('injected archive failure')
        archive_module.archive_delivery = fail_archive
        with task_directory() as workspace:
            Path('input.svg').write_text(SVG, encoding='utf-8')
            # Isolated provider replacement: the original import table is restored.
            with patch.dict(sys.modules, {'archive_delivery': archive_module}):
                with patch.object(tl_assemble, 'assemble_pptx', return_value=(b'verified bytes', {'status': 'verified'})):
                    result, code = tl_pptx.convert(['input.svg'], 'result.pptx')
            self.assertEqual(code, 3)
            self.assertEqual(result['status'], 'verified')
            self.assertTrue(result['publication']['published'])
            self.assertTrue(result['cleanup']['cleanup_complete'])
            self.assertEqual(result['archive']['archive_status'], 'failed')
            self.assertEqual(Path('result.pptx').read_bytes(), b'verified bytes')
            self.assertEqual(Path('input.svg').read_text(encoding='utf-8'), SVG)
            self.assertEqual({item.name for item in workspace.iterdir()}, {'input.svg', 'result.pptx'})

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO test')
    def test_fifo_rejected_without_blocking(self):
        """A nonregular named pipe is rejected before any blocking read."""
        with task_directory():
            os.mkfifo('pipe.svg')
            code = (f'import sys; sys.path.insert(0, {str(SCRIPT_ROOT)!r}); '
                    'from tl_guard import read_local; read_local("pipe.svg", ".svg")')
            completed = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                                       capture_output=True, text=True, timeout=3, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('bounded regular file', completed.stderr)

    def test_replaced_output_is_not_claimed_or_deleted(self):
        """A replaced directory entry must not be verified or removed."""
        with task_directory():
            original_fsync = os.fsync
            def replace_after_write(descriptor):
                original_fsync(descriptor)
                Path('result.pptx').unlink()
                Path('result.pptx').write_bytes(b'unknown replacement')
            with patch('os.fsync', side_effect=replace_after_write):
                with self.assertRaises(ContractError):
                    publish_exclusive('result.pptx', b'verified bytes')
            self.assertEqual(Path('result.pptx').read_bytes(), b'unknown replacement')

    def test_conversion_exception_cleans_snapshots_without_output(self):
        """Failed conversion removes only registered snapshots and preserves input."""
        archive_module = types.ModuleType('archive_delivery')
        archive_module.archive_delivery = lambda *_args, **_kwargs: self.fail('archive must not run')
        with task_directory() as workspace:
            Path('input.svg').write_text(SVG, encoding='utf-8')
            with patch.dict(sys.modules, {'archive_delivery': archive_module}):
                with patch.object(tl_assemble, 'assemble_pptx', side_effect=ValueError('injected conversion failure')):
                    result, code = tl_pptx.convert(['input.svg'], 'result.pptx')
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'error')
            self.assertTrue(result['cleanup']['cleanup_complete'])
            self.assertEqual({item.name for item in workspace.iterdir()}, {'input.svg'})

    def test_svg_check_and_text_measure_return_structured_json(self):
        """Offline SVG preflight and text measurement leave no residue in task root."""
        with task_directory() as workspace:
            page = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
                    '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
                    '<text x="80" y="120" font-family="Arial" font-size="36" fill="#172554">标题</text></svg>')
            Path('page.svg').write_text(page, encoding='utf-8')
            result, code = tl_pptx.svg_check_operation('page.svg', quick=True)
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'verified')
            self.assertTrue(result['validation']['passed'])
            self.assertEqual(result['validation']['file'], 'page.svg')
            self.assertNotIn('path', result['validation'])
            self.assertTrue(result['cleanup']['cleanup_complete'])
            self.assertEqual({item.name for item in workspace.iterdir()}, {'page.svg'})

            bad = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><script/></svg>'
            Path('bad.svg').write_text(bad, encoding='utf-8')
            with self.assertRaises(ContractError):
                tl_pptx.svg_check_operation('bad.svg')

            out = io.StringIO()
            with patch.object(tl_pptx, 'environment_check'), contextlib.redirect_stdout(out):
                measure_code = tl_pptx.main([
                    'text-measure', '测试文字溢出估算 Hello World',
                    '--size', '24', '--max-width', '140', '--family', 'Arial',
                    '--x', '80', '--y', '120',
                ])
            parser_result = json.loads(out.getvalue())
            self.assertEqual(measure_code, 0)
            self.assertEqual(parser_result['status'], 'verified')
            self.assertGreater(len(parser_result['measurement']['lines']), 1)
            self.assertIn('bounds', parser_result['measurement'])

            # Batch svg-check: matching and mismatched viewBox across pages
            page2 = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
                     '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/></svg>')
            Path('page2.svg').write_text(page2, encoding='utf-8')
            batch_ok, batch_code = tl_pptx.svg_check_operation(['page.svg', 'page2.svg'], quick=True)
            self.assertEqual(batch_code, 0)
            self.assertTrue(batch_ok['validation']['passed'])
            self.assertEqual(batch_ok['validation']['page_count'], 2)
            self.assertTrue(batch_ok['validation']['cross_page_viewbox_consistent'])

            page_odd = page2.replace('0 0 1280 720', '0 0 1024 768')
            Path('odd.svg').write_text(page_odd, encoding='utf-8')
            batch_bad, _ = tl_pptx.svg_check_operation(['page.svg', 'odd.svg'])
            self.assertFalse(batch_bad['validation']['passed'])
            self.assertFalse(batch_bad['validation']['cross_page_viewbox_consistent'])
            self.assertTrue(batch_bad['validation']['cross_page_errors'])

            # Batch text-measure manifest
            manifest = {
                'defaults': {'family': 'Arial', 'size': 20, 'max_width': 200},
                'items': [
                    {'id': 'title', 'text': '季度总结', 'size': 32},
                    {'id': 'body', 'text': '这是一段较长的离线排版测量文字', 'x': 60, 'y': 160},
                ],
            }
            Path('measure.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            out = io.StringIO()
            with patch.object(tl_pptx, 'environment_check'), contextlib.redirect_stdout(out):
                batch_m_code = tl_pptx.main(['text-measure', '--manifest', 'measure.json'])
            batch_m = json.loads(out.getvalue())
            self.assertEqual(batch_m_code, 0)
            self.assertEqual(batch_m['count'], 2)
            self.assertEqual([item['id'] for item in batch_m['measurements']], ['title', 'body'])
            self.assertIn('bounds', batch_m['measurements'][1])
            with patch.object(tl_pptx, 'environment_check'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tl_pptx.main(['text-measure', 'hi', '--manifest', 'measure.json']), 2)
            self.assertEqual({item.name for item in workspace.iterdir()},
                             {'page.svg', 'bad.svg', 'page2.svg', 'odd.svg', 'measure.json'})

    def test_attribution_guard_supports_isolated_python(self):
        """Integrity gate can be invoked directly with python -I -B."""
        completed = subprocess.run(
            [sys.executable, '-I', '-B', str(SCRIPT_ROOT / 'attribution_guard.py')],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
