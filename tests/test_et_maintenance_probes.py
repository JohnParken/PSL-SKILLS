"""Offline import/routing regressions, not acceptance of public ET originals."""
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EtMaintenanceProbeTests(unittest.TestCase):
    """Keep fixture generation separate so its imports cannot hide stale paths."""

    @classmethod
    def setUpClass(cls):
        code = """
            import struct
            import sys
            from pathlib import Path
            root = Path(sys.argv[1])
            sys.path.insert(0, str(root / 'skills/et/scripts/test'))
            from test_wps_biff8 import cell, rec, workbook
            from test_wps_cfb import synthetic_cfb
            stream = workbook(
                [rec(0x0203, cell(0, 0) + struct.pack('<d', 42))],
                strings=[], extra_global=rec(0x0042, struct.pack('<H', 1200)),
            )
            sys.stdout.buffer.write(synthetic_cfb({'Workbook': stream}))
        """
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', textwrap.dedent(code), str(ROOT)],
            capture_output=True, check=True, cwd=ROOT,
        )
        cls.fixture = result.stdout

    def run_probe(self, filename, code):
        """Run the real entrypoint setup with immutable synthetic input and no network."""
        setup = """
            import hashlib
            import json
            from pathlib import Path
            import runpy
            import sys
            from unittest.mock import patch
            root = Path(sys.argv[1])
            patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')).start()
            module = runpy.run_path(str(root / 'tests' / sys.argv[2]))
            data = sys.stdin.buffer.read()
        """
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', textwrap.dedent(setup) + textwrap.dedent(code), str(ROOT), filename],
            input=self.fixture, capture_output=True, check=False, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        return json.loads(result.stdout)

    def test_codepage_inventory_uses_et_parser(self):
        """Inventory real parser records without preloading ET into the child."""
        result = self.run_probe('wps_biff8_gov_et_discovery.py', """
            print(json.dumps(module['_codepage_inventory'](data)))
        """)
        self.assertEqual(len(result['declared_codepages']), 1)
        self.assertEqual(result['declared_codepages'][0]['declared_codepage'], 1200)

    def test_government_probe_uses_et_reader(self):
        """The public-source adapter must select the spreadsheet reader."""
        result = self.run_probe('wps_biff8_gov_et_discovery.py', """
            probe = module['_probe']
            probe.__globals__['_fetch'] = lambda url: data
            item = dict(id='synthetic', page='https://example.invalid/source',
                        url='https://example.invalid/input.et', sha256=hashlib.sha256(data).hexdigest(),
                        expected_cells=1, expected_table_style_elements=[])
            print(json.dumps(probe(item)))
        """)
        self.assertIn(result['reader_status'], ('ok', 'partial'))
        self.assertEqual(result['format'], 'biff8_subset')
        self.assertEqual(result['cell_count'], 1)
        # Synthetic input cannot satisfy the real attachment's frozen offsets.
        self.assertEqual(result['frozen_subset_acceptance'], 'mismatch')

    def test_builtin_probe_uses_et_parser(self):
        """The Name probe reaches the moved BIFF8 implementation."""
        result = self.run_probe('wps_biff8_builtin_real_source_discovery.py', """
            main = module['main']
            main.__globals__.update(
                SOURCES=(('synthetic', 'https://example.invalid/source', 'https://example.invalid/input.et'),),
                PINNED_SHA256={'synthetic': hashlib.sha256(data).hexdigest()}, get_source=lambda url: data,
            )
            main()
        """)
        self.assertEqual(result['pinned_sha256_matches'], 1)
        self.assertEqual(result['results'][0]['parser']['cells'], 1)
        self.assertNotEqual(result['results'][0]['parser']['status'], 'rejected')

    def test_salvage_probe_uses_et_parser(self):
        """Both parser modes run without relaxing the real-source gate."""
        result = self.run_probe('wps_biff8_salvage_government_probe.py', """
            main = module['main']
            main.__globals__.update(
                SAMPLES=[dict(id='synthetic', official_page='https://example.invalid/source',
                              url='https://example.invalid/input.et', expected_sha256=hashlib.sha256(data).hexdigest())],
                _download=lambda url: data,
            )
            main()
        """)
        sample = result['results'][0]
        self.assertEqual(sample['normal']['cell_count'], 1)
        self.assertEqual(sample['salvage']['cell_count'], 1)
        self.assertTrue(sample['saved_cell_values_equal'])
        self.assertEqual(result['pinned_real_static_name_gate'], 'failed')

    def test_rejection_inventory_uses_current_parser(self):
        """The read-only AST inventory locates the current ET source."""
        result = self.run_probe('wps_biff8_rejection_inventory.py', """
            module['main']()
        """)
        self.assertGreater(result['site_count'], 0)
        self.assertTrue(all(site['safe_to_skip'] is False for site in result['sites']))


if __name__ == '__main__':
    unittest.main()
