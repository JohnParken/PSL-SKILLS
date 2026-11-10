"""Repository-level loading measurement tests; not shipped as Skill runtime."""
import copy
import hashlib
import json
import subprocess
import sys
from unittest import mock
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

    def test_changed_read_sets_follow_documented_routes(self):
        allowed = {'xlsx-static-create', 'repeated-create-cold',
                   'repeated-create-retained', 'repeated-create-compacted', 'pptx-notes-pagination',
                   'et-header-preview', 'et-direct-statistics', 'mixed-pptx-et-read', 'xlsx-summary', 'mixed-format-read',
                   'xlsx-et-analysis', 'xlsx-wps-analysis'}
        for name, (before, after) in BENCH.PROFILES.items():
            if name not in allowed and not name.startswith('xlsx-specialized-'):
                self.assertEqual(before, after, name)

    def test_native_region_and_et_source_routes_preserve_required_union(self):
        native = BENCH.PROFILES['xlsx-basic-region-analysis'][1]
        self.assertEqual(native, ['xlsx/SKILL.md', 'xlsx/references/data-analysis.md'])
        for name in ('xlsx-et-analysis', 'xlsx-wps-analysis'):
            paths = BENCH.PROFILES[name][1]
            self.assertIn('xlsx/references/et-result-analysis.md', paths)
            self.assertIn('xlsx/references/data-analysis.md', paths)
            self.assertFalse(any(path.startswith('wps/') for path in paths))

    def test_all_planned_resources_exist_on_both_sides(self):
        for before, after in BENCH.PROFILES.values():
            for name in before:
                self.assertTrue(BENCH.read_resource(name, BENCH.BASELINE))
            for name in after:
                self.assertTrue(BENCH.read_resource(name))

    def test_iteration_routes_cover_formats_and_partial_boundaries(self):
        required = {'docx-complete-table-read', 'pptx-summary', 'pptx-selected-slides', 'pptx-notes-pagination',
                    'pptx-partial-inspect', 'et-overview', 'et-header-preview', 'et-direct-statistics',
                    'et-full-evidence', 'et-partial-pagination', 'table-inspect-many-sheets',
                    'table-partial-et-source', 'ofd-basic-create', 'ofd-partial-page-read', 'mixed-pptx-et-read'}
        self.assertTrue(required <= BENCH.PROFILES.keys())
        self.assertEqual(BENCH.PROFILES['et-direct-statistics'][1],
                         ['et/SKILL.md', 'et/references/basic-statistics.md'])
        for paths in BENCH.PROFILES['table-partial-et-source']:
            self.assertIn('et/SKILL.md', paths)
            self.assertNotIn('wps/SKILL.md', paths)
        for paths in BENCH.PROFILES['ofd-basic-create']:
            self.assertIn('ofd/references/generation.md', paths)

    def test_new_capabilities_are_not_claimed_at_baseline(self):
        report = BENCH.build_report()
        for name in ('pptx-summary', 'pptx-selected-slides', 'pptx-notes-pagination', 'table-inspect-many-sheets'):
            profile = report['profiles'][name]
            self.assertTrue(profile['baseline_capability_note'])
            self.assertEqual(profile['behavior_validation'], 'not_measured_by_loading_benchmark')
            self.assertIsNone(profile['delta_after_minus_before']['tokens'])

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


class SnapshotFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = BENCH.build_report()

    def check(self, snapshot):
        # The independent recomputation is real once per class; mutation tests
        # isolate the comparator rather than invoking Git hundreds of times.
        with mock.patch.object(BENCH, 'build_report', return_value=self.report):
            return BENCH.check_snapshot(snapshot)

    def test_history_is_preserved_byte_for_byte(self):
        history = Path(__file__).with_name('runtime_loading_profiles_history_20261107.json').read_bytes()
        self.assertEqual(hashlib.sha256(history).hexdigest(),
                         'aea64b2970ccd3c46043d8b0656d34080d302e2731c37c45253daf9b67e840cd')

    def test_fresh_report(self):
        self.assertEqual(self.check(self.report)['status'], 'fresh')

    def test_every_after_hash_is_checked(self):
        for name, profile in self.report['profiles'].items():
            for index in range(len(profile['after']['files'])):
                changed = copy.deepcopy(self.report)
                changed['profiles'][name]['after']['files'][index]['sha256'] = 'stale'
                with self.subTest(profile=name, event=index):
                    self.assertEqual(self.check(changed)['status'], 'stale')

    def test_baseline_hash_commit_and_plan_are_checked(self):
        for field in ('sha256', 'path'):
            changed = copy.deepcopy(self.report)
            changed['profiles']['docx-simple']['before']['files'][0][field] = 'stale'
            self.assertEqual(self.check(changed)['status'], 'stale')
        changed = copy.deepcopy(self.report)
        changed['baseline_commit'] = '0' * 40
        self.assertEqual(self.check(changed)['status'], 'stale')

    def test_missing_and_added_profiles(self):
        changed = copy.deepcopy(self.report)
        del changed['profiles']['docx-simple']
        self.assertEqual(self.check(changed)['status'], 'stale')
        changed = copy.deepcopy(self.report)
        changed['profiles']['unexpected'] = changed['profiles']['docx-simple']
        self.assertEqual(self.check(changed)['status'], 'stale')

    def test_old_schema_and_benchmark_change(self):
        for key, value in (('schema_version', 2), ('benchmark_sha256', '0' * 64)):
            changed = copy.deepcopy(self.report)
            changed[key] = value
            self.assertEqual(self.check(changed)['status'], 'stale')

    def test_counts_order_repetition_and_capability_notes(self):
        for side in ('before', 'after'):
            changed = copy.deepcopy(self.report)
            changed['profiles']['repeated-create-cold'][side]['files'].pop()
            self.assertEqual(self.check(changed)['status'], 'stale')
        changed = copy.deepcopy(self.report)
        changed['profiles']['xlsx-formula-partial']['after']['files'].reverse()
        self.assertEqual(self.check(changed)['status'], 'stale')
        changed = copy.deepcopy(self.report)
        changed['profiles']['pptx-summary']['baseline_capability_note'] = None
        self.assertEqual(self.check(changed)['status'], 'stale')
        changed = copy.deepcopy(self.report)
        changed['profiles']['docx-simple']['after']['chars'] += 1
        self.assertEqual(self.check(changed)['status'], 'stale')

    def test_real_file_change_and_missing_resource(self):
        original = BENCH.read_resource
        for missing in (False, True):
            def changed(name, revision=None, root=BENCH.ROOT):
                if name == 'docx/SKILL.md' and revision is None:
                    if missing:
                        raise ValueError('missing profile resource: docx/SKILL.md')
                    return original(name, revision, root) + b'changed'
                return original(name, revision, root)
            with mock.patch.object(BENCH, 'read_resource', side_effect=changed):
                self.assertEqual(BENCH.check_snapshot(self.report)['status'], 'stale')

    def test_actual_profile_addition_and_removal(self):
        for remove in (False, True):
            profiles = dict(BENCH.PROFILES)
            if remove:
                profiles.pop('docx-simple')
            else:
                profiles['new-profile'] = (['docx/SKILL.md'], ['docx/SKILL.md'])
            with mock.patch.object(BENCH, 'PROFILES', profiles):
                self.assertEqual(BENCH.check_snapshot(self.report)['status'], 'stale')

    def test_committed_current_and_history_cli(self):
        for filename, status, code in (('runtime_loading_profiles.json', 'fresh', 0),
                                      ('runtime_loading_profiles_history_20261107.json', 'stale', 1)):
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(BENCH.__file__)),
                                     '--check-snapshot', str(Path(__file__).with_name(filename))], capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], status)

    def test_invalid_snapshot_cli_is_structured_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            for content in ('not JSON', '[]'):
                path.write_text(content, encoding='utf-8')
                result = subprocess.run([sys.executable, '-I', '-S', '-B', BENCH.__file__, '--check-snapshot', str(path)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)['status'], 'stale')

    def test_specialized_routes_keep_required_union(self):
        self.assertEqual(BENCH.PROFILES['xlsx-summary'][1], ['xlsx/SKILL.md'])
        self.assertNotIn('xlsx/references/bounded-reading.md', BENCH.PROFILES['mixed-format-read'][1])
        self.assertEqual(len(BENCH.SPECIALIZED_REFERENCES), 17)
        for reference in BENCH.SPECIALIZED_REFERENCES:
            before, after = BENCH.PROFILES['xlsx-specialized-' + reference]
            self.assertIn('xlsx/references/data-analysis.md', before)
            self.assertNotIn('xlsx/references/data-analysis.md', after)
            self.assertIn('xlsx/references/analysis-contract.md', after)
            self.assertIn('xlsx/references/' + reference + '.md', after)
        for paths in BENCH.PROFILES['xlsx-specialized-institution-hierarchy']:
            self.assertIn('xlsx/references/management-wide.md', paths)
        for paths in BENCH.PROFILES['xlsx-specialized-result-export']:
            self.assertIn('xlsx/references/task-delivery.md', paths)


if __name__ == '__main__':
    unittest.main()
