"""Harness tests over synthetic OOXML bytes; never real WPS provenance evidence."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from xml.parsers import expat

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import read_wps
from verify_wps_sample import verify


def synthetic_word_package():
    """Build a tiny synthetic package with a fixed expected main-story text."""
    entries = [
        ('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
        ('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
        ('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Harness sample text</w:t></w:r></w:p></w:body></w:document>'),
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'OOXML requires Expat >=2.6')
class WpsSampleAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path.cwd()
        self.temp_root = Path(tempfile.mkdtemp(prefix='wps-acceptance-', dir=self.repository))
        self.sample = self.temp_root / 'sample.wps'
        self.manifest = self.temp_root / 'expectations.json'
        self.sample_data = synthetic_word_package()
        self.sample.write_bytes(self.sample_data)
        self.sample_argument = str(self.sample.relative_to(self.repository))
        self.manifest_argument = str(self.manifest.relative_to(self.repository))
        self.expectations = {
            'schema_version': 'wps_sample_expectations_v1',
            'sha256': hashlib.sha256(self.sample_data).hexdigest(),
            'bytes': len(self.sample_data),
            'provenance': {
                'source': 'synthetic test harness fixture',
                'original_filename': 'sample.wps',
                'producer_evidence': 'synthetic package created by this unittest',
                'independent_expectation_source': 'fixed literal in this unittest',
            },
            'options': {},
            'expected': {
                'status': 'ok',
                'format': 'ooxml-word',
                'text': 'Harness sample text',
                'paragraphs': ['Harness sample text'],
            },
        }
        self.write_manifest()
        self.addCleanup(self._remove_temp_root)

    def _remove_temp_root(self):
        import shutil
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.expectations), encoding='utf-8')

    def test_exact_synthetic_content_passes_without_claiming_format_compatibility(self):
        report = verify(self.sample_argument, self.manifest_argument)
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['provenance_status'], 'declared_requires_human_review')
        self.assertFalse(report['full_format_verified'])
        self.assertEqual(report['evidence_kind'], 'declared_original_exact_content')
        self.assertTrue(report['checks']['input_unchanged'])
        self.assertEqual(self.sample.read_bytes(), self.sample_data)
        self.assertNotIn('Harness sample text', json.dumps(report))

    def test_hash_and_length_mismatch_fail_before_reader(self):
        self.expectations['sha256'] = '0' * 64
        self.expectations['bytes'] += 1
        self.write_manifest()
        with patch.object(read_wps, 'read_bytes', side_effect=AssertionError('must not parse')):
            report = verify(self.sample_argument, self.manifest_argument)
        self.assertFalse(report['passed'])
        self.assertEqual(report['failure_code'], 'sample_identity_mismatch')
        self.assertFalse(report['checks']['sha256'])
        self.assertFalse(report['checks']['bytes'])
        self.assertNotIn('Harness sample text', json.dumps(report))

    def test_expected_status_format_text_and_paragraph_mismatches_are_separate(self):
        mismatches = (
            ('status', 'partial'),
            ('format', 'ms_doc_subset'),
            ('text', 'different text'),
            ('paragraphs', ['different paragraph']),
        )
        for key, value in mismatches:
            with self.subTest(key=key):
                self.expectations['expected'][key] = value
                self.write_manifest()
                report = verify(self.sample_argument, self.manifest_argument)
                self.assertFalse(report['passed'])
                self.assertFalse(report['checks'][key])
                self.expectations['expected'][key] = {
                    'status': 'ok', 'format': 'ooxml-word',
                    'text': 'Harness sample text',
                    'paragraphs': ['Harness sample text'],
                }[key]

    def test_manifest_rejects_wrong_types_and_missing_provenance(self):
        original = self.expectations['bytes']
        self.expectations['bytes'] = True
        self.write_manifest()
        report = verify(self.sample_argument, self.manifest_argument)
        self.assertEqual(report['failure_code'], 'manifest_invalid')

        self.expectations['bytes'] = original
        del self.expectations['provenance']['producer_evidence']
        self.write_manifest()
        report = verify(self.sample_argument, self.manifest_argument)
        self.assertEqual(report['failure_code'], 'manifest_invalid')
        self.assertEqual(report['provenance_status'], 'not_assessed')

    def test_binary_table_row_expectations_compare_exact_cell_texts(self):
        self.expectations['expected']['binary_table_rows'] = [['first', 'second']]
        self.write_manifest()
        actual = read_wps.read_bytes(self.sample_data)
        actual['binary_table_structure'] = {
            'rows': [{'cells': [{'text': 'first'}, {'text': 'second'}]}]
        }
        with patch.object(read_wps, 'read_bytes', return_value=actual):
            report = verify(self.sample_argument, self.manifest_argument)
        self.assertTrue(report['passed'], report)
        self.assertTrue(report['checks']['binary_table_rows'])

        self.expectations['expected']['binary_table_rows'] = [['first', 'changed']]
        self.write_manifest()
        with patch.object(read_wps, 'read_bytes', return_value=actual):
            report = verify(self.sample_argument, self.manifest_argument)
        self.assertFalse(report['checks']['binary_table_rows'])

    def test_binary_row_expectations_through_actual_reader(self):
        from test_wps_binary_tables import fixture
        from test_wps import synthetic_cfb
        streams, _ = fixture()
        payload = synthetic_cfb({path[0]: value for path, value in streams.items()})
        self.sample.write_bytes(payload)
        self.expectations['sha256'] = hashlib.sha256(payload).hexdigest()
        self.expectations['bytes'] = len(payload)
        self.expectations['expected'] = {
            'status': 'partial', 'format': 'ms_doc_subset',
            'text': '甲\t乙\t\t\n', 'paragraphs': ['甲\t乙\t\t'],
            'binary_table_rows': [['甲', '乙']],
        }
        self.write_manifest()
        report = verify(self.sample_argument, self.manifest_argument)
        self.assertTrue(report['passed'], report)
        self.assertTrue(report['checks']['binary_table_rows'])
        self.assertEqual(self.sample.read_bytes(), payload)

    def test_positive_compatibility_claims_in_reader_result_fail_acceptance(self):
        claims = (
            ('producer_verified',),
            ('native_et_ett_parsed',),
            ('wps_compatibility_verified',),
            ('reading_assessment', 'full_format_verified'),
        )
        for claim_path in claims:
            with self.subTest(claim=claim_path):
                actual = read_wps.read_bytes(self.sample_data)
                if len(claim_path) == 1:
                    actual[claim_path[0]] = True
                else:
                    actual['reading_assessment']['full_format_verified'] = True
                with patch.object(read_wps, 'read_bytes', return_value=actual):
                    report = verify(self.sample_argument, self.manifest_argument)
                self.assertFalse(report['passed'])
                self.assertFalse(report['checks']['full_format_claims_absent'])

    def test_input_changed_while_reader_runs_is_detected(self):
        original_reader = read_wps.read_bytes

        def mutate_during_parse(data, **options):
            result = original_reader(data, **options)
            self.sample.write_bytes(data + b'changed')
            return result

        with patch.object(read_wps, 'read_bytes', side_effect=mutate_during_parse):
            report = verify(self.sample_argument, self.manifest_argument)
        self.assertFalse(report['passed'])
        self.assertEqual(report['failure_code'], 'input_changed')
        self.assertFalse(report['checks']['input_unchanged'])

    def test_explicit_default_data_policy_is_accepted(self):
        self.expectations['options'] = {'compatibility': 'data'}
        self.write_manifest()
        report = verify(self.sample_argument, self.manifest_argument)
        self.assertTrue(report['passed'], report)

    def test_absolute_parent_and_symlink_escape_paths_are_rejected(self):
        report = verify(str(self.sample), self.manifest_argument)
        self.assertEqual(report['failure_code'], 'invalid_task_relative_path')

        work = self.temp_root / 'work'
        work.mkdir()
        (work / 'expectations.json').write_text(self.manifest.read_text(encoding='utf-8'), encoding='utf-8')
        (work / 'escaped.wps').symlink_to(self.sample)
        previous = Path.cwd()
        try:
            os.chdir(work)
            report = verify('escaped.wps', 'expectations.json')
            self.assertEqual(report['failure_code'], 'task_path_escape')
            report = verify('../sample.wps', 'expectations.json')
            self.assertEqual(report['failure_code'], 'invalid_task_relative_path')
        finally:
            os.chdir(previous)

    def test_cli_emits_json_and_uses_zero_or_one_exit_status(self):
        script = SCRIPT_DIR / 'verify_wps_sample.py'
        command = [sys.executable, '-I', '-S', '-B', str(script),
                   self.sample_argument, self.manifest_argument]
        passed = subprocess.run(command, cwd=self.repository, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(json.loads(passed.stdout)['passed'])

        self.expectations['expected']['text'] = 'wrong'
        self.write_manifest()
        failed = subprocess.run(command, cwd=self.repository, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertFalse(json.loads(failed.stdout)['passed'])
        self.assertNotIn('Harness sample text', failed.stdout)


if __name__ == '__main__':
    unittest.main()
