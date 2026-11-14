"""Lossless long-record continuation without changing ordinary output."""

from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read_fragments import fragment_records
from tl_docx import encode, page


class FragmentTests(unittest.TestCase):
    """Preserve strings, nested objects, non-ASCII and escaped characters."""

    def test_lossless_reassembly_and_output_budget(self):
        """Fragments preserve complete source JSON and fit actual response frames."""
        original = {'type': 'table_row', 'cells': [{'text': '中文🙂\\\n\"' * 1000}], 'number': 3}
        metadata = {'status': 'ok', 'fragment_mode': 'json-record-v1', 'source_record_total': 1}
        records, count = fragment_records([original], metadata, 2500)
        self.assertEqual(1, count)
        text = ''.join(item['json_fragment'] for item in records)
        self.assertEqual(original, json.loads(text))
        self.assertEqual(sha256(text.encode()).hexdigest(), records[0]['source_sha256'])
        self.assertTrue(records[-1]['final_fragment'])
        self.assertEqual(0, records[0]['character_start'])
        cursor = None
        returned = []
        while True:
            result = page(metadata, records, 2500, cursor, 'binding')
            self.assertLessEqual(len(encode(result)), 2500)
            returned.extend(result['records'])
            if not result['has_more']:
                break
            cursor = result['next_cursor']
        self.assertEqual(records, returned)

    def test_cache_status_does_not_change_fragment_boundaries(self):
        """A missing-cache rebuild must not shift a continuation cursor."""
        records = [{'type': 'paragraph', 'text': 'long text ' * 1000}]
        plain, _ = fragment_records(records, {}, 2500)
        cached, _ = fragment_records(records, {'cache': {'status': 'rebuilt_missing', 'token': 'x' * 240}}, 2500)
        self.assertEqual(plain, cached)

    def test_small_records_unchanged(self):
        """Ordinary records are not wrapped unnecessarily."""
        source = [{'type': 'paragraph', 'text': 'hello'}]
        self.assertEqual((source, 0), fragment_records(source, {}, 2000))

    def test_metadata_over_budget_refuses(self):
        """Cannot pretend to fit an oversized coverage report."""
        with self.assertRaisesRegex(ValueError, 'metadata'):
            fragment_records([{'type': 'paragraph', 'text': 'x'}], {'issues': ['x' * 1000]}, 512)


if __name__ == '__main__':
    unittest.main()
