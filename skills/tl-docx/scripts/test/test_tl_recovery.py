"""Exercise recovery using checkpoints left by a genuinely interrupted child."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1]
CLI = SCRIPTS / 'tl_docx.py'
INTERRUPT = '''
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import tl_recovery as recovery
from tl_io import task_root
mode = sys.argv[2]
if mode == 'verified-stage':
    original = recovery.checkpoint
    def stop(journal, record):
        original(journal, record)
        if record.get('verified'):
            os._exit(90)
    recovery.checkpoint = stop
elif mode == 'after-archive':
    recovery.clean_stage = lambda *args, **kwargs: os._exit(90)
with task_root() as root, recovery.task_lock(root, wait=False):
    recovery.publish(root, 'report.docx', pathlib.Path('fixture.docx').read_bytes(), status='verified',
                     finalize=lambda _: os._exit(90) if mode == 'published' else {'archive_status': 'failed'})
'''


class RecoveryTests(unittest.TestCase):
    """Verify known outputs survive and changed/unknown entries are protected."""

    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='tl-docx-recovery-')
        self.addCleanup(work.cleanup)
        self.task = Path(work.name)
        self.fixture = self.task / 'fixture.docx'
        doc = Document()
        doc.add_paragraph('恢复样例')
        doc.save(self.fixture)

    def interrupt(self, phase):
        """Stop a child at a real journal checkpoint and return its exact files."""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', INTERRUPT, str(SCRIPTS), phase],
                                cwd=self.task, capture_output=True, check=False, timeout=15)
        self.assertEqual(90, result.returncode, result.stderr.decode())
        manifests = list(self.task.glob('.tl-docx-*.cleanup.json'))
        stages = list(self.task.glob('.tl-docx-*.tmp'))
        self.assertEqual((1, 1), (len(manifests), len(stages)))
        return manifests[0], stages[0]

    def resume(self, manifest, expected=0):
        """Recover through the public CLI."""
        result = subprocess.run([sys.executable, '-I', '-B', str(CLI), 'resume', manifest.name],
                                cwd=self.task, capture_output=True, check=False, timeout=15)
        self.assertEqual(expected, result.returncode, result.stderr.decode())
        return json.loads(result.stdout)

    def test_verified_but_unpublished_stage_is_removed_without_publication(self):
        manifest, stage = self.interrupt('verified-stage')
        result = self.resume(manifest)
        self.assertEqual('failed_cleaned', result['task_status'])
        self.assertIsNone(result['output'])
        self.assertFalse(stage.exists())
        self.assertFalse(manifest.exists())
        self.assertFalse((self.task / 'report.docx').exists())

    def test_published_file_is_recovered_without_repeating_archive(self):
        manifest, stage = self.interrupt('published')
        output = self.task / 'report.docx'
        inode = output.stat().st_ino
        result = self.resume(manifest)
        self.assertEqual('completed', result['task_status'])
        self.assertEqual('unknown', result['archive_status'])
        self.assertTrue(result['cleanup']['deliverables_unchanged'])
        self.assertEqual(inode, output.stat().st_ino)
        self.assertEqual(self.fixture.read_bytes(), output.read_bytes())
        self.assertFalse(stage.exists())
        self.assertFalse(manifest.exists())
        self.resume(manifest, expected=1)

    def test_recovery_retains_recorded_archive_outcome(self):
        manifest, stage = self.interrupt('after-archive')
        self.assertEqual('failed', self.resume(manifest)['archive_status'])
        self.assertFalse(stage.exists())

    def test_recovery_refuses_replaced_stage(self):
        manifest, stage = self.interrupt('verified-stage')
        moved = self.task / 'moved'
        stage.rename(moved)
        stage.write_bytes(b'unknown file')
        result = self.resume(manifest, expected=1)
        self.assertEqual('recovery_pending', result['task_status'])
        self.assertEqual(b'unknown file', stage.read_bytes())
        self.assertTrue(manifest.exists())
        self.assertTrue(moved.exists())

    def test_recovery_refuses_a_changed_published_file(self):
        manifest, stage = self.interrupt('published')
        output = self.task / 'report.docx'
        output.write_bytes(b'changed user data')
        self.assertEqual('recovery_pending', self.resume(manifest, expected=1)['task_status'])
        self.assertEqual(b'changed user data', output.read_bytes())
        self.assertTrue(stage.exists())
        self.assertTrue(manifest.exists())

    def test_recovery_preserves_evidence_if_a_published_file_disappears(self):
        manifest, stage = self.interrupt('published')
        (self.task / 'report.docx').unlink()
        self.assertEqual('recovery_pending', self.resume(manifest, expected=1)['task_status'])
        self.assertTrue(stage.exists())
        self.assertTrue(manifest.exists())

    def test_recovery_refuses_a_copied_or_unrelated_manifest(self):
        manifest, stage = self.interrupt('verified-stage')
        copy = self.task / ('.tl-docx-' + '0' * 32 + '.cleanup.json')
        copy.write_bytes(manifest.read_bytes())
        copy.chmod(0o600)
        self.assertEqual('recovery_pending', self.resume(copy, expected=1)['task_status'])
        self.assertTrue(stage.exists())
        self.assertTrue(manifest.exists())


if __name__ == '__main__':
    unittest.main()
