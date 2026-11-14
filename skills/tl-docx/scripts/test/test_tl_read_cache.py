"""Task-local cache integrity, reuse and safe release regressions."""

import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from tl_package import Package
from tl_read import report
from tl_read_cache import cached_report, load, release


class ReadCacheTests(unittest.TestCase):
    """Cache files remain independent of source and published documents."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.root)
        doc = Document()
        doc.add_heading('Header', 1)
        doc.add_paragraph('Read me')
        raw = io.BytesIO()
        doc.save(raw)
        self.package = Package(raw.getvalue())
        self.options = {'revisions': None, 'include_related': True}

    def cache_path(self, token):
        """Locate only the fixture token's random cache."""
        return Path(self.temp.name, '.tl-docx-read-' + token.split('.')[1] + '.cache')

    def test_roundtrip_and_release(self):
        """Fresh JSON loads identically and releases idempotently."""
        data, meta = cached_report(self.root, self.package)
        self.assertEqual(meta['status'], 'stored')
        self.assertEqual(data, load(self.root, meta['token'], self.package.sha256, self.options))
        self.assertEqual(release(self.root, meta['token'])['status'], 'released')
        self.assertEqual(release(self.root, meta['token'])['status'], 'already_absent')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_reuse_skips_reader_and_returns_fresh_data(self):
        """A subsequent process-equivalent call does not traverse XML again."""
        expected, meta = cached_report(self.root, self.package)
        with patch('tl_read.ReportReader', side_effect=AssertionError('parsed again')):
            data, reused = cached_report(self.root, self.package, token=meta['token'])
            self.assertEqual(expected, data)
            self.assertEqual(reused['status'], 'reused')
            data[0].clear()
            self.assertEqual(expected, report(self.package, cache_root=self.root, cache_token=meta['token']))

    def test_source_options_version_binding(self):
        """Source bytes, revisions, related-story policy and parser version bind."""
        _, meta = cached_report(self.root, self.package)
        for source, options in [('0' * 64, self.options),
                                (self.package.sha256, dict(self.options, revisions='original')),
                                (self.package.sha256, dict(self.options, include_related=False))]:
            with self.assertRaises(ValueError):
                load(self.root, meta['token'], source, options)
        with patch('tl_read_cache.PARSER_VERSION', 'next'):
            with self.assertRaises(ValueError):
                load(self.root, meta['token'], self.package.sha256, self.options)
            with self.assertRaises(ValueError):
                release(self.root, meta['token'])

    def test_missing_cache_rebuild_is_explicit(self):
        """Missing cache is rebuilt from verified source, not empty success."""
        expected, meta = cached_report(self.root, self.package)
        release(self.root, meta['token'])
        data, rebuilt = cached_report(self.root, self.package, token=meta['token'])
        self.assertEqual(data, expected)
        self.assertEqual(rebuilt['status'], 'rebuilt_missing')
        self.assertNotEqual(rebuilt['token'], meta['token'])

    def test_corruption_retained(self):
        """Unverified cache contents are neither used nor deleted."""
        _, meta = cached_report(self.root, self.package)
        path = self.cache_path(meta['token'])
        path.write_text('{}', encoding='utf-8')
        for action in (lambda: load(self.root, meta['token'], self.package.sha256, self.options),
                       lambda: release(self.root, meta['token'])):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def test_other_directory_symlink_and_hardlink_rejected(self):
        """Identity checks prevent adopting copies and following symlinks."""
        _, meta = cached_report(self.root, self.package)
        path = self.cache_path(meta['token'])
        with tempfile.TemporaryDirectory() as other:
            root = os.open(other, os.O_RDONLY | os.O_DIRECTORY)
            try:
                other_path = Path(other, path.name)
                other_path.write_bytes(path.read_bytes())
                other_path.chmod(0o600)
                with self.assertRaises(ValueError):
                    release(root, meta['token'])
                other_path.unlink()
                other_path.symlink_to(path)
                with self.assertRaises(OSError):
                    release(root, meta['token'])
                other_path.unlink()
                os.link(path, other_path)
                with self.assertRaises(ValueError):
                    release(self.root, meta['token'])
                self.assertTrue(path.exists())
            finally:
                os.close(root)

    def test_invalid_tokens_permissions_and_limit(self):
        """No path tokens, public cache files or oversized writes are accepted."""
        for token in ('../source.docx', 'tlc1.' + '0' * 1000, None):
            with self.assertRaises(ValueError):
                release(self.root, token)
        with patch('tl_read_cache.MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                cached_report(self.root, self.package)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        _, meta = cached_report(self.root, self.package)
        self.cache_path(meta['token']).chmod(0o644)
        with self.assertRaises(ValueError):
            release(self.root, meta['token'])

    def test_store_failure_cleans_owned_partial(self):
        """Serialization/sync failure removes only its registered new file."""
        source = Path(self.temp.name, 'source.docx')
        source.write_bytes(self.package.data)
        with patch('tl_read_cache.os.fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                cached_report(self.root, self.package)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [source])
        self.assertEqual(source.read_bytes(), self.package.data)

    def test_cross_process_reuse(self):
        """A clean interpreter reuses serialized data without reader construction."""
        expected, meta = cached_report(self.root, self.package)
        code = (
            "import json,os,sys; sys.path.insert(0,sys.argv[1]); "
            "from tl_read_cache import load; root=os.open('.',os.O_RDONLY|os.O_DIRECTORY); "
            "data=load(root,sys.argv[2],sys.argv[3],{'revisions':None,'include_related':True}); "
            "print(json.dumps(data)); os.close(root)"
        )
        completed = subprocess.run(
            [sys.executable, '-I', '-B', '-c', code, str(Path(__file__).resolve().parents[1]),
             meta['token'], self.package.sha256], cwd=self.temp.name,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(completed.stdout), list(expected))

    def test_default_report_does_not_write_cache(self):
        """Uncached reading remains an in-memory operation."""
        with patch('tl_read_cache.store', side_effect=AssertionError('cache write')):
            report(self.package)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
