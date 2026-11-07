"""Repository-level loading measurement tests; not shipped as Skill runtime."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('loading_benchmark', Path(__file__).with_name('benchmark_skill_loading.py'))
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


class LoadingBenchmarkTests(unittest.TestCase):
    def test_et_profiles_do_not_read_word_skill(self):
        for name in ('wps-default-et', 'wps-selected-cells'):
            for paths in BENCH.PROFILES[name]:
                self.assertEqual(paths[0], 'et/SKILL.md')
                self.assertFalse(any(path.startswith('wps/') for path in paths))

    def test_static_and_complex_routes_differ(self):
        before, after = BENCH.PROFILES['xlsx-static-create']
        self.assertEqual(after, ['xlsx/SKILL.md'])
        self.assertIn('xlsx/references/create.md', before)
        for paths in BENCH.PROFILES['xlsx-formula-partial']:
            self.assertIn('xlsx/references/formula-calculation.md', paths)
            self.assertIn('xlsx/references/task-delivery.md', paths)

    def test_basic_wps_baseline_does_not_count_optional_reference(self):
        before, after = BENCH.PROFILES['wps-static-create']
        self.assertEqual(before, ['wps/SKILL.md'])
        self.assertEqual(after, before)
        baseline = BENCH.read_resource('wps/SKILL.md', BENCH.BASELINE).decode('utf-8')
        for fragment in ('"type": "title"', '"type": "paragraph"', '"type": "table"', 'run_wps_task.py'):
            self.assertIn(fragment, baseline)
        for paths in BENCH.PROFILES['wps-complex-create']:
            self.assertIn('wps/references/generation.md', paths)

    def test_changed_read_sets_are_limited_to_documented_xlsx_shortcut(self):
        allowed = {'xlsx-static-create', 'repeated-create-cold',
                   'repeated-create-retained', 'repeated-create-compacted'}
        for name, (before, after) in BENCH.PROFILES.items():
            if name not in allowed:
                self.assertEqual(before, after, name)

    def test_all_planned_resources_exist_on_both_sides(self):
        for before, after in BENCH.PROFILES.values():
            for name in before:
                self.assertTrue(BENCH.read_resource(name, BENCH.BASELINE))
            for name in after:
                self.assertTrue(BENCH.read_resource(name))

    def test_no_tokenizer_never_estimates_tokens(self):
        result = BENCH.measure(['xlsx/SKILL.md'])
        self.assertIsNone(result['tokens'])
        self.assertIsNone(result['files'][0]['tokens'])
        self.assertGreater(result['bytes'], result['chars'])

    def test_repeated_reads_are_not_silently_deduplicated(self):
        one = BENCH.measure(['xlsx/SKILL.md'])
        two = BENCH.measure(['xlsx/SKILL.md'] * 2)
        self.assertEqual(two['chars'], one['chars'] * 2)
        self.assertEqual(two['read_events'], 2)
        self.assertEqual(two['unique_files'], 1)

    def test_tokenizer_api_counts_events_not_characters(self):
        class FakeEncoder:
            def encode(self, text, disallowed_special):
                self.assertion = disallowed_special == ()
                return [11, 22, 33]
        encoder = FakeEncoder()
        result = BENCH.measure(['xlsx/SKILL.md'] * 2, encoder=encoder)
        self.assertEqual(result['tokens'], 6)
        self.assertTrue(encoder.assertion)
        # This fake proves plumbing only; production reports do not use it.

    def test_paths_cannot_escape_or_follow_links(self):
        for name in ('../secret', '/etc/passwd', 'xlsx/../../secret', 'xlsx\\secret'):
            with self.assertRaises(ValueError):
                BENCH.read_resource(name)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'skills').mkdir()
            (root / 'outside').write_text('private', encoding='utf-8')
            (root / 'skills' / 'link').symlink_to(root / 'outside')
            with self.assertRaises(ValueError):
                BENCH.read_resource('link', root=root)

    def test_report_labels_limits_and_baseline(self):
        report = BENCH.build_report()
        self.assertEqual(report['baseline_commit'], BENCH.BASELINE)
        self.assertEqual(report['host_usage']['status'], 'not_measured')
        self.assertEqual(report['tokenizer']['status'], 'not_measured')
        self.assertIsNone(report['profiles']['xlsx-static-create']['delta_after_minus_before']['tokens'])


if __name__ == '__main__':
    unittest.main()
