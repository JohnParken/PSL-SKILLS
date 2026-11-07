"""Current human-authored selection expectations, not model accuracy tests.

Repository maintenance only; original selection cases/results stay historical.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / 'tests' / 'skill_selection_current_cases.json'


def validate_cases(data, entries):
    """Fail when entry text/catalog changes; review semantics BEFORE refreshing."""
    errors = []
    if data.get('schema_version') != 1:
        errors.append('unsupported schema')
    if data.get('evidence_kind') != 'human_authored_expectations_not_model_or_host_measurement':
        errors.append('evidence kind must not claim model measurement')
    if data.get('entry_sha256') != entries:
        errors.append('catalog or entry hashes changed: manually review expectations before refreshing')
    cases = data.get('cases', [])
    ids = [case.get('id') for case in cases]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        errors.append('case IDs must be nonempty and unique')
    positives, negatives = set(), set()
    mixed = False
    for case in cases:
        include = case.get('expected_include', [])
        exclude = case.get('expected_exclude', [])
        if not isinstance(include, list) or not isinstance(exclude, list):
            errors.append('expected selections must be lists')
            continue
        if any(not isinstance(value, str) for value in include + exclude):
            errors.append('expected selections must name skills')
            continue
        if (set(include) | set(exclude)) - entries.keys():
            errors.append('unknown skill')
        if set(include) & set(exclude):
            errors.append('contradictory expectation')
        if len(include) != len(set(include)) or len(exclude) != len(set(exclude)):
            errors.append('duplicate selection')
        if not case.get('prompt') or not case.get('rationale'):
            errors.append('prompt and rationale required')
        if case.get('kind') == 'positive':
            positives.update(include)
        elif case.get('kind') == 'negative':
            negatives.update(exclude)
        elif case.get('kind') == 'mixed':
            mixed |= len(set(include)) >= 2
        elif case.get('kind') != 'boundary':
            errors.append('unknown case kind')
    if positives != entries.keys() or negatives != entries.keys():
        errors.append('each current skill needs explicit positive and negative cases')
    if not mixed:
        errors.append('at least one multi-skill mixed case required')
    return errors


class CurrentSelectionCasesTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(CASES.read_text(encoding='utf-8'))
        self.entries = {path.parent.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted((ROOT / 'skills').glob('*/SKILL.md'))}

    def test_current_catalog_hashes_and_coverage(self):
        self.assertEqual(validate_cases(self.data, self.entries), [])

    def test_every_entry_change_requires_manual_review(self):
        for name in self.entries:
            entries = dict(self.entries)
            entries[name] = 'changed'
            self.assertTrue(validate_cases(self.data, entries), name)

    def test_new_and_removed_skills_require_review(self):
        entries = dict(self.entries)
        entries['new-skill'] = 'hash'
        self.assertTrue(validate_cases(self.data, entries))
        entries.pop('docx')
        self.assertTrue(validate_cases(self.data, entries))
        entries = dict(self.entries)
        entries.pop('docx')
        self.assertTrue(validate_cases(self.data, entries))

    def test_missing_positive_negative_and_mixed_cases(self):
        for kind in ('positive', 'negative', 'mixed'):
            data = copy.deepcopy(self.data)
            data['cases'] = [case for case in data['cases'] if case['kind'] != kind]
            self.assertTrue(validate_cases(data, self.entries), kind)

    def test_unknown_contradictory_and_duplicate_cases_fail(self):
        for value in ('unknown', self.data['cases'][0]['expected_include'][0]):
            data = copy.deepcopy(self.data)
            data['cases'][0]['expected_exclude'].append(value)
            self.assertTrue(validate_cases(data, self.entries))
        data = copy.deepcopy(self.data)
        data['cases'].append(data['cases'][0])
        self.assertTrue(validate_cases(data, self.entries))

    def test_brand_format_and_runner_boundaries_present(self):
        cases = {case['id']: case for case in self.data['cases']}
        for name in ('wps-app-native-xlsx', 'native-docx-reading', 'legacy-doc-reading', 'bank-short-polish',
                     'wps-existing-receipt', 'table-existing-receipt', 'single-xlsx-analysis', 'single-et-analysis', 'mixed-ofd-pptx-read'):
            self.assertIn(name, cases)
            self.assertTrue(cases[name]['expected_exclude'])


if __name__ == '__main__':
    unittest.main()
