"""Exact UTF-8 pagination parity and bounded prefix-serialization regressions."""

import base64
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_docx
from tl_contract import ContractError


def exhaustive_page(meta, records, budget, start, binding, request):
    """Independent oracle: serialize every candidate and choose the last fit."""
    best = None
    for end in range(start + bool(records), len(records) + 1):
        more = end < len(records)
        token = base64.urlsafe_b64encode(tl_docx.encode([binding, end])).decode('ascii') if more else None
        candidate = dict(meta, start=start, end=end, total=len(records), has_more=more,
                         next_cursor=token, records=records[start:end])
        if more and request is not None:
            candidate['continuation'] = dict(request, parameters=dict(request['parameters'], cursor=token))
        if budget is None or len(tl_docx.encode(candidate)) <= budget:
            best = candidate
    return best


class PageBudgetTests(unittest.TestCase):
    """Keep public results exact while avoiding serialization of unused tails."""

    def assert_matches_oracle(self, records, budget, start=0, request=None, meta=None):
        """Compare complete result objects, not only record count or size."""
        meta = meta if meta is not None else {'status': 'partial', 'issues': [{'code': 'example', 'count': 1}]}
        binding = 'source-view-binding'
        cursor = base64.urlsafe_b64encode(tl_docx.encode([binding, start])).decode('ascii') if start else None
        expected = exhaustive_page(meta, records, budget, start, binding, request)
        if expected is None:
            with self.assertRaises(ContractError) as caught:
                tl_docx.page(meta, records, budget, cursor, binding, request)
            self.assertEqual(caught.exception.code, 'output_budget_exceeded')
        else:
            actual = tl_docx.page(meta, records, budget, cursor, binding, request)
            self.assertEqual(expected, actual)
            if budget is not None:
                self.assertLessEqual(len(tl_docx.encode(actual)), budget)

    def test_random_unicode_records_and_cursors(self):
        """Exercise escapes, UTF-8 width, nested arrays and cursor digit changes."""
        generator = random.Random(20261117)
        request = {'action': 'read', 'source': "测试 'x'.docx", 'parameters': {'content': True, 'max_output_bytes': 12000}}
        for case in range(60):
            records = [{'text': ''.join(generator.choices('中😀é\\\n\t"abc', k=generator.randrange(1, 80))),
                        'values': [index, None, True, {'label': 'α'}]} for index in range(generator.randrange(1, 30))]
            start = generator.randrange(len(records))
            for budget in (None, 128, 256, 512, 900, 1500, 3000, 12000):
                with self.subTest(case=case, start=start, budget=budget):
                    self.assert_matches_oracle(records, budget, start, request if case % 2 else None)

    def test_terminal_page_can_fit_when_first_candidate_does_not(self):
        """A large continuation must not force premature failure."""
        request = {'action': 'read', 'source': 'x.docx', 'parameters': {'section': 'x' * 1000}}
        records = [{'text': 'a'}, {'text': 'b'}]
        expected = exhaustive_page({}, records, None, 0, 'source-view-binding', request)
        budget = len(tl_docx.encode(expected))
        self.assert_matches_oracle(records, budget, request=request, meta={})
        self.assert_matches_oracle(records, budget - 1, request=request, meta={})

    def test_empty_and_oversized_metadata(self):
        """Empty documents still require space for their complete metadata."""
        for budget in (None, 1, 128, 512, 2000):
            self.assert_matches_oracle([], budget)
            self.assert_matches_oracle([], budget, meta={'notes': '中' * 1000})

    def test_oversized_record(self):
        """Refuse complete-record overflow, rather than silently truncating it."""
        self.assert_matches_oracle([{'text': '中' * 10000}, {'text': 'small'}], 512)

    def test_unused_tail_is_not_serialized_or_sliced(self):
        """The prefix cost is independent of a large remaining record list."""
        records = [{'text': 'x' * 200}] * 100000
        original_encode = tl_docx.encode
        encoded_records = []

        def observed_encode(value):
            if isinstance(value, dict) and 'text' in value:
                encoded_records.append(value)
            if isinstance(value, dict) and 'records' in value:
                self.assertEqual(value['records'], [])
            return original_encode(value)

        with patch.object(tl_docx, 'encode', side_effect=observed_encode):
            result = tl_docx.page({}, records, 512, None, 'binding')
        self.assertEqual(result['records'], records[:result['end']])
        self.assertLessEqual(len(encoded_records), 3)
        self.assertLessEqual(len(original_encode(result)), 512)

    def test_complete_page_exact_budget(self):
        """Both full and resumed terminal pages honor exact inclusive limits."""
        records = [{'text': '中😀"'}, {'text': 'next'}, {'text': 'final'}]
        for start in (0, 1, 2):
            full = exhaustive_page({}, records, None, start, 'source-view-binding', None)
            size = len(tl_docx.encode(full))
            for budget in (size - 1, size, size + 1):
                self.assert_matches_oracle(records, budget, start, meta={})


if __name__ == '__main__':
    unittest.main()
