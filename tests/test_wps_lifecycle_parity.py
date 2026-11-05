"""Public lifecycle implementation and WPS distribution must not silently diverge."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WpsLifecycleParityTests(unittest.TestCase):
    def test_pinned_public_copy(self):
        metadata = json.loads((ROOT / 'skills/wps/references/lifecycle-upstream.json').read_text(encoding='utf-8'))
        self.assertEqual({item['file'] for item in metadata['files']},
                         {'router_config.py', 'output_location.py', 'artifact_files.py', 'controlled_cleanup.py',
                          'cleanup_task.py', 'archive_deliverables.py'})
        for item in metadata['files']:
            with self.subTest(file=item['file']):
                source = (ROOT / 'skills/artifact-lifecycle/scripts' / item['file']).read_bytes()
                expected = 'from __future__ import annotations\n' + source.decode('utf-8')
                for module in ('router_config', 'output_location', 'artifact_files', 'controlled_cleanup'):
                    expected = expected.replace('from ' + module + ' import ', 'from .' + module + ' import ')
                installed = (ROOT / 'skills/wps/scripts/_wps_artifact_runtime' / item['file']).read_bytes()
                self.assertEqual(installed, expected.encode('utf-8'))
                self.assertEqual(hashlib.sha256(source).hexdigest(), item['source_sha256'])
                self.assertEqual(hashlib.sha256(installed).hexdigest(), item['installed_sha256'])
