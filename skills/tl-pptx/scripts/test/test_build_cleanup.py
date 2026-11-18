"""Failure cleanup is limited to freshly owned native-builder workspaces."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_build import build_package, prepare_build
from tl_guard import ContractError
from svg_to_pptx.pptx_package import builder

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
       '<rect x="10" y="20" width="100" height="80" fill="#ff0000"/></svg>')


class BuildCleanupTests(unittest.TestCase):
    """Third-party input/output siblings must survive every failure phase."""

    def setUp(self):
        self.previous = Path.cwd()
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        os.chdir(self.workspace.name)
        self.addCleanup(os.chdir, self.previous)
        Path('page.svg').write_text(SVG, encoding='utf-8')
        Path('unrelated').mkdir()
        Path('unrelated/keep.txt').write_text('keep me', encoding='utf-8')

    def assert_only_inputs_remain(self):
        """Check protected inputs and unrelated directory, not only temp names."""
        self.assertEqual(Path('page.svg').read_text(encoding='utf-8'), SVG)
        self.assertEqual(Path('unrelated/keep.txt').read_text(encoding='utf-8'), 'keep me')
        self.assertEqual({path.name for path in Path('.').iterdir()}, {'page.svg', 'unrelated'})

    def test_builder_failure_removes_only_owned_workspace(self):
        """A partially written private PPTX is disposable; sibling data is not."""
        prepared = prepare_build({'slides': [{'svg': 'page.svg'}]})

        def fail_after_write(_slides, output, **_options):
            output.write_bytes(b'partial output')
            (output.parent / 'private-sidecar.tmp').write_bytes(b'partial sidecar')
            raise RuntimeError('synthetic conversion failure')

        with patch.object(builder, 'create_pptx_with_native_svg', side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, 'synthetic conversion'):
                build_package(prepared)
        self.assert_only_inputs_remain()

    def test_postbuild_validation_failure_still_cleans_owned_workspace(self):
        """Failed structural acceptance never leaks a temporary deliverable."""
        prepared = prepare_build({'slides': [{'svg': 'page.svg'}]})
        with patch('tl_build.inspect_package', side_effect=ContractError('synthetic validation failure')):
            with self.assertRaisesRegex(ContractError, 'synthetic validation'):
                build_package(prepared)
        self.assert_only_inputs_remain()

    def test_linked_audio_is_rejected_before_private_build(self):
        """Explicit names do not authorize following a filesystem link."""
        Path('voice.wav').symlink_to('unrelated/keep.txt')
        with self.assertRaisesRegex(ContractError, 'audio processing has been removed'):
            prepare_build({'slides': [{'svg': 'page.svg', 'audio': 'voice.wav'}]})
        self.assertEqual(Path('unrelated/keep.txt').read_text(encoding='utf-8'), 'keep me')
        self.assertFalse(list(Path('.').glob('tl-pptx-build-*')))


if __name__ == '__main__':
    unittest.main()
