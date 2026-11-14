"""Synthetic end-to-end checks for the Skill's shortest read/create entry."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx.oxml import parse_xml
from docx.oxml.ns import qn


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
CLI = SCRIPT_ROOT / 'tl_docx.py'
SKILL = SCRIPT_ROOT.parent / 'SKILL.md'
EXPECTED_SPEC = {'blocks': [
    {'type': 'title', 'text': '工作报告'},
    {'type': 'heading', 'level': 1, 'text': '工作概况'},
    {'type': 'paragraph', 'text': '在这里填写正文。'},
    {'type': 'table', 'header': True, 'rows': [['项目', '结果'], ['示例项目', '已完成']]},
]}
READ_IMPORT_PROBE = '''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]).parent))
from tl_docx import main
code = main(['read', '报告.docx'])
print('LOADED_MODULES=' + json.dumps(sorted(sys.modules)), file=sys.stderr)
raise SystemExit(code)
'''


class ShallowEntryTests(unittest.TestCase):
    """Run only synthetic task data, with independent child-process state."""

    def setUp(self):
        task = tempfile.TemporaryDirectory(prefix='tl-shallow-entry-')
        self.addCleanup(task.cleanup)
        self.task = Path(task.name)
        self.environment = dict(os.environ, PATH='', ROUTER_CONFIG_VARIABLES=json.dumps({
            'commonParam': [{'name': 'questionId', 'value': 'shallow-entry-test'}]}))

    def invoke(self, *arguments, payload=None, expected=0):
        """Use the same isolated public entry and stdin JSON as the documentation."""
        process = subprocess.run([sys.executable, '-I', '-B', str(CLI), *arguments], cwd=self.task,
                                 env=self.environment, input=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
                                 capture_output=True, timeout=30, check=False)
        self.assertEqual(expected, process.returncode, process.stderr.decode())
        return json.loads(process.stdout)

    def create_example(self):
        """Ensure the tested specification is the actual documented short example."""
        text = SKILL.read_text(encoding='utf-8')
        examples = [json.loads(value) for value in re.findall(r"<<'JSON'\s*\n(.*?)\nJSON", text, re.DOTALL)]
        self.assertIn(EXPECTED_SPEC, examples, 'Shortest Skill JSON example changed; update its independent contract test.')
        return self.invoke('create', '报告.docx', payload=EXPECTED_SPEC)

    def test_short_example_creates_archives_and_uses_natural_pagination(self):
        """Check actual publication bytes and absence of forced document breaks."""
        receipt = self.create_example()
        self.assertEqual(receipt['status'], 'verified')
        self.assertTrue(receipt['publication']['published'])
        self.assertTrue(receipt['cleanup']['cleanup_complete'])
        self.assertTrue(receipt['cleanup']['manifest_removed'])
        self.assertEqual(receipt['archive_status'], 'success')
        original = (self.task / '报告.docx').read_bytes()
        self.assertEqual(original, (self.task / 'output/shallow-entry-test/报告.docx').read_bytes())
        self.assertEqual(receipt['sha256'], sha256(original).hexdigest())
        with ZipFile(self.task / '报告.docx') as package:
            document = parse_xml(package.read('word/document.xml'))
        self.assertFalse([node for node in document.iter(qn('w:br')) if node.get(qn('w:type')) == 'page'])
        self.assertFalse([node for node in document.iter(qn('w:pageBreakBefore')) if node.get(qn('w:val'), '1') not in ('0', 'false', 'off')])
        self.assertEqual({'报告.docx', 'output'}, {path.name for path in self.task.iterdir()})

    def test_default_read_returns_content_and_keeps_full_escape_hatch(self):
        """Bare read exposes useful text/table rows rather than infrastructure."""
        self.create_example()
        result = self.invoke('read', '报告.docx')
        self.assertEqual('content', result['view'])
        self.assertFalse(result['has_more'])
        records = result['records']
        texts = [record.get('text') for record in records]
        self.assertIn('工作报告', texts)
        self.assertIn('在这里填写正文。', texts)
        rows = [record for record in records if record['type'] == 'table_row']
        self.assertEqual([['项目', '结果'], ['示例项目', '已完成']], [[cell['text'] for cell in row['cells']] for row in rows])
        self.assertFalse({'style', 'numbering_definition', 'section'} & {record['type'] for record in records})
        self.assertFalse(any('path' in record or 'part' in record for record in records))
        full = self.invoke('read', '报告.docx', '--full')
        self.assertTrue(any('part' in record and 'path' in record for record in full['records']))

    def test_edit_protects_original_and_publishes_separate_copy(self):
        """A precise edit and a rejected overwrite both leave source bytes intact."""
        self.create_example()
        original = (self.task / '报告.docx').read_bytes()
        operation = {'type': 'replace_run_text', 'paragraph_index': 2, 'expected_text': '在这里填写正文。',
                     'run_index': 0, 'expected_run_text': '在这里填写正文。', 'text': '这是更新后的正文。'}
        payload = {'operations': [operation]}
        receipt = self.invoke('edit', '报告.docx', '修改后.docx', payload=payload)
        self.assertEqual(receipt['status'], 'edited')
        self.assertEqual(receipt['archive_status'], 'success')
        self.assertEqual(original, (self.task / '报告.docx').read_bytes())
        result = self.invoke('read', '修改后.docx')
        self.assertIn('这是更新后的正文。', [record.get('text') for record in result['records']])
        self.invoke('edit', '报告.docx', '报告.docx', payload=payload, expected=1)
        self.assertEqual(original, (self.task / '报告.docx').read_bytes())

    def test_read_does_not_load_creation_or_edit_modules(self):
        """A fresh process proves read does not import unrelated business engines."""
        self.create_example()
        process = subprocess.run([sys.executable, '-I', '-B', '-c', READ_IMPORT_PROBE, str(CLI)],
                                 cwd=self.task, env=self.environment, capture_output=True, timeout=30, check=False)
        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertEqual('content', json.loads(process.stdout)['view'])
        marker = next(line for line in process.stderr.decode().splitlines() if line.startswith('LOADED_MODULES='))
        loaded = set(json.loads(marker.partition('=')[2]))
        forbidden = {'tl_create', 'tl_create_layout', 'tl_create_styles', 'tl_create_tables', 'tl_edit', 'tl_replace', 'tl_comments', 'tl_roundtrip'}
        self.assertEqual(set(), forbidden & loaded)


if __name__ == '__main__':
    unittest.main()
