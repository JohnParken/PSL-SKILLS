"""Frozen real-value comparison guards; synthetic counterexamples, no downloads."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_et_samples import check_fixed_values


class FixedValueTests(unittest.TestCase):
    def result(self):
        return {'sheets': [{'name': 'Data', 'cells': {
            'A1': {'type': 'text', 'value': '0017'},
            'B1': {'type': 'number', 'value': Decimal('1.25')},
            'C1': {'type': 'number', 'value': Decimal('999'),
                   'formula': {'tokens': 'opaque'}, 'cache_status': 'unverified'},
        }}]}

    def expectation(self):
        return {'sheets': {'Data': {
            'A1': {'type': 'text', 'value': '0017'},
            'B1': {'type': 'number', 'value': '1.25'},
            'C1': {'type': 'number', 'value': '999', 'formula': True},
        }}}

    def test_typed_values_and_unverified_cache(self):
        self.assertEqual(check_fixed_values(self.result(), self.expectation()),
                         {'semantic_values_checked': 3, 'formula_caches_checked': 1})

    def test_text_is_not_coerced_to_number(self):
        result = self.result()
        result['sheets'][0]['cells']['A1'] = {'type': 'number', 'value': Decimal(17)}
        with self.assertRaisesRegex(ValueError, 'type differs'):
            check_fixed_values(result, self.expectation())

    def test_wrong_value_or_missing_address_is_not_a_count_success(self):
        for mutation in ('wrong', 'missing'):
            result = self.result()
            if mutation == 'wrong':
                result['sheets'][0]['cells']['B1']['value'] = Decimal('125')
            else:
                del result['sheets'][0]['cells']['B1']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check_fixed_values(result, self.expectation())

    def test_missing_cache_or_lost_formula_provenance_is_rejected(self):
        for mutation in ('missing', 'formula'):
            result = self.result()
            if mutation == 'missing':
                result['sheets'][0]['cells']['C1']['cache_status'] = 'missing'
            else:
                del result['sheets'][0]['cells']['C1']['formula']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check_fixed_values(result, self.expectation())


if __name__ == '__main__':
    unittest.main()
