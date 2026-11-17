"""Runtime source and license tracking checks, not a network freshness claim."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ProvenanceTests(unittest.TestCase):
    """Every recorded runtime file remains the reviewed implementation."""

    def test_runtime_fingerprints(self):
        """Prevent source/adapter changes silently outliving their provenance."""
        record = json.loads((ROOT / 'references/upstream.json').read_text(encoding='utf-8'))
        self.assertEqual(record['commit'], '7e0df6705de0cd24345210d31f07fcb5a32b6900')
        self.assertGreater(len(record['files']), 100)
        for entry in record['files']:
            with self.subTest(path=entry['target']):
                path = ROOT / entry['target']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                self.assertTrue(entry['origin'])

    def test_licenses_present(self):
        """Main MIT and geometry data licenses/notices remain in distribution."""
        main = (ROOT / 'references/LICENSE-PPT-MASTER.txt').read_text(encoding='utf-8')
        self.assertIn('Copyright (c) 2025-2026 Hugo He', main)
        self.assertIn('Permission is hereby granted', main)
        data = ROOT / 'scripts/pptx_shapes/data'
        for name in ['LICENSE-APACHE-2.0.txt', 'LICENSE-OPEN-XML-SDK-MIT.txt', 'NOTICE.txt']:
            self.assertGreater((data / name).stat().st_size, 100)


if __name__ == '__main__':
    unittest.main()
