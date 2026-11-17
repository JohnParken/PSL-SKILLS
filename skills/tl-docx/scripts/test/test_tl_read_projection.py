"""Projection remains isolated, complete and equivalent while reducing copying."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tl_read_content import content_records
from benchmark_read_projection import projection_fixture


# Measured from the pre-optimization implementation on independent synthetic data.
FINGERPRINTS = [
    ('paragraph', 250, False, '257b58b7aaddc1a5b3cbf0199b52929033bbef274c0713e58af23b4a38c3bf8f'),
    ('paragraph', 250, True, '2e8d9115c8cd453b3ec064997f91c8b0dec44efc7045ecddbef793ec202ebc5e'),
    ('paragraph', 2500, False, 'e29be0ff8b6d43ae2db695f8e3b7e94bc869b432133acefc596b659d7f15996c'),
    ('paragraph', 2500, True, 'a0782d2fc911d25d9c9795b3fde5ef115ebd9e1fe4596be05b46dfc02cf739d2'),
    ('paragraph', 10000, False, 'cf0fd0b773b7797eea795607254252832894e6cc11652080fa7e2725d51ad3ed'),
    ('paragraph', 10000, True, '79422a6348aaf7a857903ed8eeedd9d7f248cee73345a423cbdf07bb474c8d9f'),
    ('table', 100, False, 'db7e564379431175615178c9f3ebb0fb8b105ec2b672a75148de6e41afe6237d'),
    ('table', 100, True, 'caa5c0c478199ee6acbbf0b7cb81f2b629be473144819bbbd534d152292ad1f5'),
    ('table', 1000, False, 'bd07b5baa7ed95c365a53dd6b26756da97de9277473b7872ef3a3e6cf45d2042'),
    ('table', 1000, True, '0c3caa1406ede4bd6f79fdfb31a96da1e919c71e9f747f2ad8dfa4954ebd0bf8'),
    ('nested', 50, False, 'a3030874f8d2966c4a08c8187f207513b8bd623b13dc069b900b04fbc7a0040a'),
    ('nested', 50, True, '7f0160ab64086cf79032d7beed48a302049f45d5778cdf8dd4450d3833a0d7fd'),
    ('nested', 500, False, '2dc932c3ba55c76cfc28076a814ac04400182338f035672cd7a2482a6ef69fe3'),
    ('nested', 500, True, '939da14572f6dff7cc4d3d7ae924657558d2ca9fc3e64fb3230718180116bcb9'),
    ('nested', 2000, False, '939d89f46c7fe1dd438cb4f8be6073ed204f45690b35eab9cc50a0cfe8dc2ae9'),
    ('nested', 2000, True, '053e732c5ea5ae65ba9d7585e4c23ca7ca24d378171ca8e725be1b1e1d87d7f5'),
]


class CountingRecords(list):
    """Count full input passes independently of timing and machine speed."""

    def __init__(self, values):
        super().__init__(values)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return super().__iter__()


class ProjectionTests(unittest.TestCase):
    """Check old fingerprints and mutable output isolation."""

    def test_public_raw_report_stays_immutable(self):
        """Repeated views and caller mutation do not alter a shared raw report."""
        from tl_docx import arguments, read_result
        from tl_read import report
        from test_tl_read_content import ContentTests
        package = ContentTests().sample()
        raw = report(package)
        snapshot = deepcopy(raw)
        for options in ([], ['--details'], ['--table-header', '1:0'], []):
            result = read_result(package, arguments(['read', 'sample.docx', *options]), raw_report=raw)
            expected = read_result(package, arguments(['read', 'sample.docx', *options]), raw_report=deepcopy(snapshot))
            self.assertEqual(expected, result)
            for item in result['records']:
                item['caller_changed'] = True
                if item.get('cells'):
                    item['cells'][0]['text'] = 'caller changed'
            self.assertEqual(snapshot, raw)

    def test_preoptimization_fingerprints(self):
        """Both views retain every byte of the canonical projected record data."""
        for kind, count, details, expected in FINGERPRINTS:
            with self.subTest(kind=kind, count=count, details=details):
                source = projection_fixture(kind, count)
                output = content_records(source, details=details)
                actual = sha256(json.dumps(output, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                self.assertEqual(expected, actual)

    def test_no_nested_cell_full_rescan(self):
        """More nested cells do not cause more complete passes through records."""
        for count in (50, 500, 2000):
            records = CountingRecords(projection_fixture('nested', count))
            output = content_records(records)
            self.assertEqual(4 * count, len(output))
            self.assertLessEqual(records.passes, 6)

    def test_outputs_never_mutate_source(self):
        """Unknown nested values, tuples and previously shared fields stay owned."""
        shared = {'text': 'kept', 'path': '/hidden'}
        source = [{'type': 'future_object', 'part': 'a', 'path': '/future',
                   'first': shared, 'second': shared, 'tuple_value': ({'items': ['original']},)},
                  {'type': 'paragraph', 'part': 'a', 'path': '/p', 'text': 'content',
                   'revisions': [{'text': 'removed', 'path': '/p/del'}], 'cross_record_alias': shared}]
        snapshot = deepcopy(source)
        for details in (False, True, False):
            output = content_records(source, details=details)
            output[0]['first']['text'] = 'changed'
            output[0]['tuple_value'][0]['items'].append('changed')
            output[1]['revisions'][0]['text'] = 'changed'
            self.assertEqual(snapshot, source)

    def test_nested_override_is_only_in_output(self):
        """A table aggregate can be suppressed without editing cached raw cells."""
        source = projection_fixture('nested', 2)
        snapshot = deepcopy(source)
        for details in (False, True, False):
            output = content_records(source, details=details)
            self.assertEqual('', output[0]['cells'][0]['text'])
            self.assertTrue(output[0]['cells'][0]['content_in_following_records'])
            output[0]['cells'][0]['text'] = 'changed'
            self.assertEqual(snapshot, source)

    def test_record_rules_precede_recursive_internal_filter(self):
        """Empty display maps retain their prior semantics after internal pruning."""
        source = [{'type': 'chart_point', 'part': 'a', 'path': '/c',
                   'display': {'path': {'text': 'opaque', 'status': 'formatted'}}}]
        self.assertEqual([{'type': 'chart_point', 'display': {}}], content_records(source))

    def test_matching_is_part_and_exact_prefix_scoped(self):
        """Interleaved stories and near-prefix paths do not join unrelated prose."""
        source = projection_fixture('nested', 1)
        source.insert(2, {'type': 'paragraph', 'part': 'other.xml',
                          'path': source[1]['path'], 'text': 'Other story'})
        source.append({'type': 'paragraph', 'part': 'word/document.xml',
                       'path': '/w:document[1]/w:body[1]/w:tbl[10]/w:tr[1]/w:tc[1]/w:p[1]', 'text': 'Near prefix'})
        output = content_records(source)
        self.assertEqual('', output[0]['cells'][0]['text'])
        texts = [item['text'] for item in output if item['type'] == 'paragraph']
        self.assertEqual(['Cell 0 column 1', 'Other story', 'Nested 0', 'Near prefix'], texts)


if __name__ == '__main__':
    unittest.main()
