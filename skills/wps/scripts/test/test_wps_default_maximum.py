"""Default maximum, explicit legacy mode, and non-current revision boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
import read_wps
from test_wps_related_resilience import header_package
from test_wps_maximum_reading import ooxml_with_textbox
from test_wps import document, synthetic_cfb


class DefaultMaximumTests(unittest.TestCase):
    def test_default_api_keeps_current_text_and_separate_stored_view(self):
        result = read_wps.read_bytes(ooxml_with_textbox())
        self.assertEqual(result['reading_mode'], 'maximum')
        self.assertEqual(result['text'], 'MainNew\n')
        self.assertEqual(result['revision_content']['content']['text'], 'MainNewOld\n')
        self.assertEqual(result['content_view'], 'accepted_supported_revision_view')
        self.assertTrue(result['current_view_available'])
        self.assertEqual(result['status'], 'partial')

    def test_rtf_deleted_text_not_current_text(self):
        result = read_wps.read_bytes(b'{\\rtf1 Amount {\\deleted 100}{\\revised 200}}')
        self.assertEqual(result['text'], 'Amount 200')
        self.assertEqual(result['revision_content']['content']['text'], 'Amount 100200')
        self.assertEqual(result['status'], 'partial')

    def test_unresolved_rtf_revision_styles_preserve_labeled_stored_text(self):
        result = read_wps.read_bytes(br'{\rtf1{\stylesheet{\s0\deleted Deleted;}}\s0 text}')
        self.assertEqual(result['text'], 'text')
        self.assertFalse(result['current_view_available'])
        self.assertEqual(result['content_view'], 'stored_text_not_verified_current')
        self.assertEqual(result['status'], 'partial')

    def test_explicit_all_is_never_labeled_current(self):
        result = read_wps.read_bytes(ooxml_with_textbox(), revisions='all')
        self.assertEqual(result['text'], 'MainNewOld\n')
        self.assertFalse(result['current_view_available'])
        self.assertEqual(result['content_view'], 'all_stored_revisions_not_current')
        self.assertEqual(result['status'], 'partial')

    def test_binary_stored_text_is_retained_but_current_unknown(self):
        payload = synthetic_cfb({path[0]: value for path, value in document().items()})
        result = read_wps.read_bytes(payload)
        self.assertTrue(result['text'])
        self.assertFalse(result['current_view_available'])
        self.assertEqual(result['content_view'], 'stored_text_not_verified_current')
        self.assertEqual(result['status'], 'partial')

    def test_default_preserves_readable_body_and_footer(self):
        result = read_wps.read_bytes(header_package())
        self.assertEqual(result['text'], 'Intact body')
        self.assertEqual(result['related_stories'][1]['text'], 'Intact footer')
        self.assertEqual(result['reading_mode'], 'maximum')

    def test_explicit_standard_does_not_read_related(self):
        result = read_wps.read_bytes(header_package(), reading_mode='standard')
        self.assertNotIn('related_stories', result)
        self.assertNotIn('maximum_reading', result)

    def test_secondary_revision_failure_keeps_current_text(self):
        import wps_ooxml
        original = wps_ooxml.read_bytes
        def reader(data, **options):
            if options.get('revisions') == 'all':
                return {'status': 'resource_limit', 'text': None}
            return original(data, **options)
        with patch.object(wps_ooxml, 'read_bytes', side_effect=reader):
            result = read_wps.read_bytes(ooxml_with_textbox())
        self.assertEqual(result['text'], 'MainNew\n')
        self.assertEqual(result['revision_content']['status'], 'unavailable')
        self.assertEqual(result['status'], 'partial')

    def test_output_budget_raises_before_partial_json(self):
        result = read_wps.read_bytes(header_package())
        with patch.object(read_wps, 'MAX_JSON_OUTPUT_BYTES', 32), self.assertRaises(read_wps.ParseError):
            read_wps.encode_result(result)

    def test_cli_default_and_explicit_standard_and_projection_guard(self):
        script = str(Path(read_wps.__file__).resolve())
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'source.wps').write_bytes(header_package())
            base = [sys.executable, '-I', '-S', '-B', script, 'source.wps']
            result = subprocess.run(base, cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(json.loads(result.stdout)['reading_mode'], 'maximum')
            standard = subprocess.run([*base, '--reading-mode', 'standard'], cwd=directory, capture_output=True, text=True, check=False)
            self.assertNotIn('related_stories', json.loads(standard.stdout))
            rejected = subprocess.run([*base, '--summary'], cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(rejected.stdout, '')
            self.assertIn('require explicit standard', rejected.stderr)


if __name__ == '__main__':
    unittest.main()
