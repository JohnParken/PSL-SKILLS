"""Offline release-pack verification; no network, installation or publication."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tl_assetpacks import preserve_asset_attributions, query_asset_pack, select_asset_packs
from tl_guard import ContractError
from tl_roundtrip import inspect_workspace_zip
from tl_templates import _template_views


@contextmanager
def task_directory():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as temporary:
        os.chdir(temporary)
        try:
            yield Path(temporary)
        finally:
            os.chdir(previous)


class AssetPackTests(unittest.TestCase):
    """Use the separately distributed pinned packs when explicitly available."""

    @classmethod
    def setUpClass(cls):
        cls.pack_root = Path(os.environ.get('TL_PPTX_ASSET_PACK_DIR', Path(__file__).resolve().parents[4] / 'dist/runtime'))
        catalog = json.loads((Path(__file__).resolve().parents[2] / 'references/offline-assets.json').read_text())
        cls.packs = {row['pack_id']: row for row in catalog['packs']}
        if not all((cls.pack_root / row['filename']).is_file() for row in cls.packs.values()):
            raise unittest.SkipTest('Separate offline release packs are not available in this test deployment')

    def copy_pack(self, pack_id):
        name = self.packs[pack_id]['filename']
        shutil.copyfile(self.pack_root / name, name)
        return name

    def test_all_ten_known_packs_pass_full_hash_crc_and_roster(self):
        with task_directory():
            for ident, expected in self.packs.items():
                with self.subTest(pack=ident):
                    name = self.copy_pack(ident)
                    result = query_asset_pack(name, limit=1)
                    self.assertEqual(result['pack_sha256'], expected['sha256'])
                    self.assertEqual(result['total_matches'], expected['assets'])
                    self.assertEqual(len(result['matches']), 1)

    def test_query_is_bounded_and_stable(self):
        with task_directory():
            name = self.copy_pack('styles')
            first = query_asset_pack(name, limit=2)
            second = query_asset_pack(name, limit=2, offset=first['next_offset'])
            self.assertTrue(first['truncated'])
            self.assertFalse({row['id'] for row in first['matches']} & {row['id'] for row in second['matches']})
            match = query_asset_pack(name, query='incident')
            self.assertEqual([row['id'] for row in match['matches']], ['incident-postmortem'])

    def test_unknown_pack_and_traversing_filename_rejected(self):
        with task_directory():
            name = self.copy_pack('styles')
            with self.assertRaises(ContractError):
                query_asset_pack('./' + name)
            content = bytearray(Path(name).read_bytes())
            content[-1] ^= 1
            Path('tampered.zip').write_bytes(content)
            with self.assertRaisesRegex(ContractError, 'known catalog'):
                query_asset_pack('tampered.zip')

    def test_multiple_template_roots_keep_complete_rosters(self):
        with task_directory() as root:
            brands, styles = self.copy_pack('brands'), self.copy_pack('styles')
            payload, report = select_asset_packs([
                {'pack': brands, 'ids': ['中国电信']}, {'pack': styles, 'ids': ['incident-postmortem']},
            ])
            bundle = inspect_workspace_zip(payload)
            self.assertIn('template-workspaces/brands/中国电信/templates/design_spec.md', bundle['entries'])
            self.assertIn('template-workspaces/styles/incident-postmortem/templates/design_spec.md', bundle['entries'])
            self.assertTrue(any('images/' in path for path in bundle['entries']))
            self.assertEqual(len(report['template_roots']), 2)
            views = _template_views(bundle)
            self.assertEqual(len(views), 2)
            self.assertTrue(all('templates/design_spec.md' in view['entries'] for view in views))
            self.assertEqual(sorted(path.name for path in root.iterdir()), sorted([brands, styles]))

    def test_icon_sound_ids_and_notices_survive_selection(self):
        with task_directory():
            icons, sounds = self.copy_pack('icons-chunk-filled'), self.copy_pack('sounds')
            payload, report = select_asset_packs([
                {'pack': icons, 'ids': ['chunk-filled/home']}, {'pack': sounds, 'ids': ['bigsoundbank/0572']},
            ])
            bundle = inspect_workspace_zip(payload)
            self.assertIn('icons/chunk-filled/home.svg', bundle['entries'])
            self.assertIn('sounds/bigsoundbank/0572.wav', bundle['entries'])
            provenance = bundle['metadata']['asset_selection']
            self.assertTrue(report['attribution_preservation_required'])
            self.assertTrue(any('Noah Jacobus' in row['utf8_text'] for row in provenance['notices']))
            self.assertEqual(provenance['source_packs'][0]['rights']['license'], 'CC-BY-4.0')
            carried = inspect_workspace_zip(preserve_asset_attributions(payload, [bundle['metadata']]))
            self.assertEqual(carried['metadata']['asset_provenance'], [provenance])

    def test_unknown_id_does_not_create_partial_artifact(self):
        with task_directory() as root:
            name = self.copy_pack('styles')
            with self.assertRaisesRegex(ContractError, 'Unknown canonical'):
                select_asset_packs([{'pack': name, 'ids': ['incident-postmortem', 'not-an-id']}])
            self.assertEqual([path.name for path in root.iterdir()], [name])

    def test_mixed_primary_libraries_preserved_with_advisory(self):
        with task_directory():
            chunk = self.copy_pack('icons-chunk-filled')
            tabler = self.copy_pack('icons-tabler-outline')
            payload, report = select_asset_packs([
                {'pack': chunk, 'ids': ['chunk-filled/home']},
                {'pack': tabler, 'ids': ['tabler-outline/home']},
            ])
            entries = inspect_workspace_zip(payload)['entries']
            self.assertIn('icons/chunk-filled/home.svg', entries)
            self.assertIn('icons/tabler-outline/home.svg', entries)
            self.assertEqual(len(report['warnings']), 1)


if __name__ == '__main__':
    unittest.main()
