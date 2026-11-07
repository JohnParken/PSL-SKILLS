"""Repository maintenance regressions for bounded name discovery."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/table-workflow/scripts'))
from table_workflow import Workflow, encode


class NameDiscoveryTests(unittest.TestCase):
    def workflow(self, names, partial=False):
        api = SimpleNamespace(metadata=lambda path, raw: {'sheet_names': names, 'native': not partial, 'source_status': 'partial'})
        flow = Workflow(api)
        book = SimpleNamespace(cells=dict.fromkeys(names), wps_source_status='partial')
        flow.snapshots['./book.xlsx'] = (b'', 'digest')
        flow.books['./book.xlsx'] = book
        return flow

    def test_ninth_and_continuation(self):
        names = ['Sheet' + str(i) for i in range(45)]
        flow = self.workflow(names)
        first = flow.names({'path': './book.xlsx'})
        second = flow.names({'path': './book.xlsx', 'offset': first['next_offset'], 'sha256': first['sha256']})
        self.assertEqual(first['names'] + second['names'], names)
        self.assertIsNone(second['next_offset'])

    def test_long_names_budget_partial(self):
        names = [str(i) + 'x' * 3000 for i in range(12)]
        flow = self.workflow(names, True)
        result = flow.names({'path': './book.xlsx', 'limit': 100})
        self.assertLess(len(encode(result)), 24000)
        self.assertGreater(result['next_offset'], 0)
        self.assertLess(result['next_offset'], 12)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['names'], names[:result['next_offset']])

    def test_invalid_continuations(self):
        flow = self.workflow(['One'])
        for extra in ({'offset': 1}, {'offset': -1}, {'offset': True}, {'limit': 0},
                      {'offset': 2, 'sha256': 'digest'}, {'sha256': 'changed'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                flow.names({'path': './book.xlsx', **extra})

    def test_single_and_empty_end(self):
        flow = self.workflow(['One'])
        result = flow.names({'path': './book.xlsx'})
        self.assertEqual(result['names'], ['One'])
        self.assertIsNone(result['next_offset'])
        self.assertEqual(flow.names({'path': './book.xlsx', 'offset': 1, 'sha256': 'digest'})['names'], [])
