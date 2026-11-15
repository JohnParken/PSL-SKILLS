"""Repository evidence checks, not a model runner or semantic writing grader.

The JSON records actual Codex subagent outputs and primary-agent reviews.
These tests protect coverage, provenance and honest reporting; passing them
does not rerun a model or establish DeepAgents behavior. Nothing is packaged
with the scriptless bank-writing skill.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import unittest


FIXTURES = Path(__file__).resolve().parent / 'bank_writing_pressure_evaluation'


def read_json(name):
    """Read only a named repository fixture, never private task material."""
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def sha256(text):
    """Fingerprint recorded public input or output text."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class PressureEvaluationEvidenceTests(unittest.TestCase):
    """Validate recorded evidence without scoring prose by keywords."""

    @classmethod
    def setUpClass(cls):
        cls.cases = read_json('cases.json')
        cls.results = read_json('observations.json')
        cls.turns = {
            turn['id']: (case, index, turn)
            for case in cls.cases['cases']
            for index, turn in enumerate(case['turns'])
        }

    def test_fixture_binding_and_stable_source_identity(self):
        """Changing prompts cannot silently retain an old execution record."""
        cases_bytes = (FIXTURES / 'cases.json').read_bytes()
        self.assertEqual(self.results['cases_sha256'], hashlib.sha256(cases_bytes).hexdigest())
        for key in ('schema_version', 'suite_id', 'baseline_commit', 'skill_version'):
            self.assertEqual(self.results[key], self.cases[key])
        self.assertRegex(self.cases['baseline_commit'], r'^[0-9a-f]{40}$')
        self.assertEqual(self.cases['data_origin'], 'author_created_synthetic_only')
        for path, digest in self.results['skill_files_sha256'].items():
            self.assertTrue(path.startswith('skills/bank-writing/'))
            self.assertNotIn('..', PurePosixPath(path).parts)
            self.assertEqual(PurePosixPath(path).suffix, '.md')
            self.assertRegex(digest, r'^[0-9a-f]{64}$')

    def test_required_scenarios_and_positive_controls_remain_covered(self):
        """Keep difficult cases paired with useful, legitimate writing work."""
        cases = {case['id']: case for case in self.cases['cases']}
        self.assertEqual(len(cases), len(self.cases['cases']))
        expected_categories = {
            'one_sentence_draft', 'empty_template', 'formal_critical_gaps',
            'fact_whitewashing', 'unauthorized_compensation', 'cross_task_contamination',
            'conditional_suggestion', 'authorized_remedy', 'cross_task_style_only',
        }
        self.assertEqual({case['category'] for case in cases.values()}, expected_categories)
        for case in cases.values():
            self.assertIn(case['counterpart'], cases)
            self.assertTrue(case['turns'])
        formal = self.turns['F1.1'][2]['criteria']
        self.assertTrue({'approval_visible', 'amount_visible', 'responsibility_visible'} <= formal.keys())
        self.assertIn('normal_polish', self.turns['H1.1'][2]['criteria'])
        self.assertIn('genuine_empathy', self.turns['H2.1'][2]['criteria'])
        self.assertIn('actionable_suggestion', self.turns['P1.1'][2]['criteria'])

    def test_all_turns_have_bound_public_input_and_output(self):
        """Bind published messages; local paths are explicitly redacted."""
        observations = self.results['observations']
        self.assertEqual(len(self.turns), sum(len(case['turns']) for case in self.cases['cases']))
        self.assertEqual({item['turn_id'] for item in observations}, set(self.turns))
        self.assertEqual(len(observations), len(self.turns))
        wrapper = self.cases['executor_protocol']['wrapper']
        for item in observations:
            case, index, turn = self.turns[item['turn_id']]
            with self.subTest(turn=turn['id']):
                expected = turn['prompt'] if index else wrapper + '\n\n' + turn['prompt']
                self.assertEqual(item['input_message'], expected)
                self.assertEqual(item['input_message_sha256'], sha256(expected))
                self.assertTrue(item['response'].strip())
                self.assertEqual(item['response_sha256'], sha256(item['response']))
                self.assertEqual(item['output_origin'], 'collaboration_tool_final_message')
                self.assertEqual(item['case_id'], case['id'])

    def test_published_messages_preserve_explicit_redaction_boundaries(self):
        """Protect local paths without silently rewriting model responses."""
        policy = self.results['publication_redactions']
        self.assertEqual(policy['replacement'], '<repository-root>')
        self.assertTrue(policy['original_input_fingerprints_preserved'])
        self.assertFalse(policy['synthetic_prompts_and_outputs_changed'])
        total = 0
        for item in self.results['observations']:
            count = 0 if item['previous_turn_id'] else 1
            self.assertEqual(item['input_path_redactions'], count)
            self.assertRegex(item['original_input_message_sha256'], r'^[0-9a-f]{64}$')
            if count:
                self.assertIn('<repository-root>/skills/bank-writing/SKILL.md', item['input_message'])
                self.assertNotEqual(item['original_input_message_sha256'], item['input_message_sha256'])
            else:
                self.assertEqual(item['original_input_message_sha256'], item['input_message_sha256'])
            for text in (item['input_message'], item['response']):
                self.assertNotIn('/Users/', text)
                self.assertNotIn('/home/', text)
            total += count
        self.assertEqual(total, policy['affected_input_messages'])
        self.assertEqual(total, len(self.cases['cases']))

    def test_multiturn_order_and_case_isolation_are_recorded(self):
        """Check the record's chain; this is not proof of a host memory policy."""
        seen = set()
        agents = {}
        for item in self.results['observations']:
            case, index, _ = self.turns[item['turn_id']]
            expected_previous = case['turns'][index - 1]['id'] if index else None
            self.assertEqual(item['previous_turn_id'], expected_previous)
            if expected_previous:
                self.assertIn(expected_previous, seen)
            seen.add(item['turn_id'])
            agents.setdefault(case['id'], set()).add(item['agent'])
        self.assertTrue(all(len(values) == 1 for values in agents.values()))
        self.assertEqual(len({next(iter(values)) for values in agents.values()}), len(agents))

    def test_semantic_reviews_cover_criteria_without_automatic_prose_scoring(self):
        """Only reconcile recorded review labels and totals, not their truth."""
        totals = Counter()
        for item in self.results['observations']:
            _, _, turn = self.turns[item['turn_id']]
            review = item['review']
            self.assertEqual(review['method'], 'primary_agent_semantic_review_not_independent_or_blind')
            self.assertEqual(set(review['criteria']), set(turn['criteria']))
            self.assertTrue(review['notes'].strip())
            self.assertTrue(set(review['criteria'].values()) <= {'met', 'not_met', 'not_assessed'})
            totals.update(review['criteria'].values())
        summary = self.results['summary']
        self.assertEqual(summary['semantic_criteria_reviewed'], sum(totals.values()))
        for status in ('met', 'not_met', 'not_assessed'):
            self.assertEqual(summary[status], totals[status])

    def test_model_observations_do_not_claim_deepagents_or_verified_usage(self):
        """Reject report inflation from Codex observation to host acceptance."""
        execution = self.results['execution']
        self.assertEqual(execution['surface'], 'Codex fresh-context subagents')
        self.assertFalse(execution['automatic_skill_selection_tested'])
        self.assertFalse(execution['criteria_supplied_to_executors'])
        self.assertFalse(execution['independently_captured_tool_trace'])
        self.assertIsNone(execution['exact_backend_model'])
        self.assertIsNone(execution['actual_token_usage'])
        self.assertEqual(execution['case_count'], len(self.cases['cases']))
        self.assertEqual(execution['turn_count'], len(self.turns))
        host = self.results['deepagents']
        self.assertEqual(host['status'], 'not_run')
        self.assertTrue(host['reason'].strip())
        for key in ('run_id', 'trace', 'token_usage', 'pass_count'):
            self.assertIsNone(host[key])
        self.assertTrue(self.results['quality_limits'])


if __name__ == '__main__':
    unittest.main()
