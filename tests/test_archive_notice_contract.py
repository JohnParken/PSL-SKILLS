"""Repository-only contract tests for independently deployed archive distributions.

These checks intentionally copy each production distribution without tests or
neighbor Skills, then exercise identical user notices and real archive outcomes.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUCCESS = '🎉 文件归档成功，请到云空间查看。 ☁️✨'
PARTIAL = '部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。'
FAILURE = '🥺 本次归档未完成，请先核对文件结果。 📄'
DISTRIBUTIONS = (
    ('artifact-lifecycle', '', 'archive_deliverables'),
    ('docx', '', 'archive_deliverables'),
    ('xlsx', '_artifact_runtime', '_artifact_runtime.archive_deliverables'),
    ('wps', '_wps_artifact_runtime', '_wps_artifact_runtime.archive_deliverables'),
)
PROBE = r'''
import importlib, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
archive = importlib.import_module(sys.argv[2])
success = '🎉 文件归档成功，请到云空间查看。 ☁️✨'
partial = '部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。'
failure = '🥺 本次归档未完成，请先核对文件结果。 📄'
config = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'notice-private-id'}]})
os.environ['ROUTER_CONFIG_VARIABLES'] = config
cases = [
    ({'archive_status': 'copied', 'files': [{'archive_status': 'copied'}]}, success),
    ({'archive_status': 'copied', 'files': [{'archive_status': 'already_archived'}]}, success),
    ({'archive_status': 'failed', 'files': [{'archive_status': 'copied'}, {'archive_status': 'failed'}]}, partial),
    ({'archive_status': 'failed', 'files': [{'archive_status': 'already_archived'}, {'archive_status': 'skipped'}]}, partial),
    ({'archive_status': 'failed', 'files': [{'archive_status': 'failed'}]}, failure),
    ({'archive_status': 'failed', 'files': [{'archive_status': 'copied'}]}, failure),
    ({'archive_status': 'skipped', 'files': []}, '本次未执行归档，请先核对文件结果。'),
    ({'archive_status': 'not_attempted', 'files': []}, '本次未执行归档，请先核对文件结果。'),
    ({'archive_status': 'handoff_required', 'files': []}, '文件待交接归档，归档完成后再提示到云空间查看。'),
]
for malformed in (None, {}, 'not-a-list', [], [None], [{}], [{'archive_status': 'failed'}]):
    cases.append(({'archive_status': 'copied', 'files': malformed}, '归档结果尚未确认，请先核对文件结果。'))
    cases.append(({'archive_status': 'failed', 'files': malformed}, failure))
cases.append(({'archive_status': 'unknown', 'files': [{'archive_status': 'copied'}]}, '归档结果尚未确认，请先核对文件结果。'))
for result, expected in cases:
    result.update(warnings=['/private/archive/notice-private-id'], archive_output='/private/archive')
    actual = archive.archive_user_notice(result)
    assert actual == expected, (result, actual, expected)
    assert not any(secret in actual for secret in ('/private', 'notice-private-id', '下载', '已生成', '上传完成'))
Path('first.txt').write_bytes(b'first')
Path('second.txt').write_bytes(b'second')
first = archive.archive_outputs([Path('first.txt')], [])
assert first['archive_user_notice'] == success
assert first['archive_message'] == '归档完成'
assert first['files'][0]['archive_verified'] is True
mixed = archive.archive_outputs([Path('second.txt'), Path('first.txt')], [])
assert mixed['archive_user_notice'] == partial
assert mixed['archive_message'] == '归档未完成'
assert [x['archive_status'] for x in mixed['files']] == ['copied', 'failed']
failed = archive.archive_outputs([Path('first.txt')], [])
assert failed['archive_user_notice'] == failure
assert failed['archive_status'] == 'failed'
for sources in ([], [Path('missing.txt')]):
    invalid = archive.archive_outputs(sources, [])
    assert invalid['archive_status'] == 'failed'
    assert invalid['archive_user_notice'] == failure
    assert not invalid['files']
for setting in (None, '{invalid', '{}'):
    if setting is None:
        os.environ.pop('ROUTER_CONFIG_VARIABLES', None)
    else:
        os.environ['ROUTER_CONFIG_VARIABLES'] = setting
    skipped = archive.archive_outputs([Path('first.txt')], [])
    assert skipped['archive_status'] == 'skipped'
    assert skipped['archive_message'] == '归档未执行'
    assert skipped['archive_user_notice'] == '本次未执行归档，请先核对文件结果。'
assert Path('first.txt').read_bytes() == b'first'
assert Path('second.txt').read_bytes() == b'second'
print(json.dumps({'notice_cases': len(cases), 'archive_scenarios': 8, 'primary_files_preserved': True}))
'''


class IndependentArchiveNoticeTests(unittest.TestCase):
    def test_four_readonly_distributions_match_without_neighbor_skills(self):
        for skill, package, module in DISTRIBUTIONS:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(prefix='notice-contract-') as directory:
                root = Path(directory)
                install = root / 'install'
                install.mkdir()
                source = ROOT / 'skills' / skill / 'scripts'
                if package:
                    shutil.copytree(source / package, install / package, ignore=shutil.ignore_patterns('__pycache__'))
                else:
                    for name in ('archive_deliverables.py', 'artifact_files.py', 'output_location.py',
                                 'router_config.py', 'controlled_cleanup.py', 'cleanup_task.py'):
                        shutil.copy2(source / name, install / name)
                before = {p.relative_to(install): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in install.rglob('*') if p.is_file()}
                for path in install.rglob('*'):
                    path.chmod(0o555 if path.is_dir() else 0o444)
                install.chmod(0o555)
                task = root / 'task'
                task.mkdir()
                try:
                    child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', PROBE, str(install), module],
                                           cwd=task, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
                    result = json.loads(child.stdout)
                    self.assertEqual(result['notice_cases'], 24)
                    self.assertTrue(result['primary_files_preserved'])
                    after = {p.relative_to(install): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in install.rglob('*') if p.is_file()}
                    self.assertEqual(before, after)
                    self.assertEqual(child.stderr, '')
                finally:
                    install.chmod(0o755)
                    for path in install.rglob('*'):
                        path.chmod(0o755 if path.is_dir() else 0o644)

    def test_wps_failed_primary_is_not_archived_or_called_successful(self):
        scripts = ROOT / 'skills/wps/scripts'
        code = ("import sys,json; sys.path.insert(0,sys.argv[1]); from wps_delivery import finish_delivery; "
                "print(json.dumps(finish_delivery({'status':'failed','output_written':False},'missing.wps')))")
        with tempfile.TemporaryDirectory(prefix='notice-business-failed-') as work:
            child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(scripts)],
                                   cwd=work, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(child.returncode, 0, child.stderr)
            result = json.loads(child.stdout)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['delivery']['archive_status'], 'not_attempted')
            self.assertNotIn('归档成功', result['delivery']['archive_user_notice'])
            self.assertFalse(list(Path(work).iterdir()))

    def test_workflow_compact_receipt_keeps_each_notice_and_business_state(self):
        scripts = ROOT / 'skills/table-workflow/scripts'
        code = r'''
import json, sys
sys.path.insert(0,sys.argv[1])
from table_workflow import bounded_result
result = {'status': 'partial', 'output_written': True, 'audit_written': True, 'delivery_incomplete': True,
          'unbounded_detail': 'x' * 50000, 'delivery': {
          'first.xlsx': {'archive_status': 'copied', 'archive_message': '归档完成', 'archive_user_notice': sys.argv[2]},
          'audit.json': {'archive_status': 'failed', 'archive_message': '归档未完成', 'archive_user_notice': sys.argv[3]}}}
print(json.dumps(bounded_result(result)))
'''
        child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(scripts), SUCCESS, FAILURE],
                               capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertTrue(result['details_omitted_due_to_budget'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['delivery_incomplete'])
        self.assertEqual(result['delivery']['first.xlsx']['archive_user_notice'], SUCCESS)
        self.assertEqual(result['delivery']['audit.json']['archive_user_notice'], FAILURE)


if __name__ == '__main__':
    unittest.main()
