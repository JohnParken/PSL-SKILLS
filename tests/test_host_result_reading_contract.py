"""Repository-only regression for the host-result reading instructions.

This checks entry-point text, not actual host tool behavior or model routing.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def contract_issues(text, *, navigation=False):
    """Check the independently loaded common result-reference instruction."""
    marker = '宿主返回 `/large_tool_results/`' if navigation else '**宿主大结果读取：**'
    paragraphs = [part for part in text.split('\n\n') if marker in part]
    if len(paragraphs) != 1:
        return ['expected one host result paragraph']
    value = paragraphs[0]
    required = (
        '/large_tool_results/', '宿主结果路径', '仅用宿主提供的 `read_file`', '真实 schema',
        '准确引用', '覆盖', '截断', '`execute`', '`cat`', 'Shell', 'Python', '不猜测映射',
        '路径掩码', '缺少 `read_file`', '报告具体缺口', '停止依赖', '普通业务文件', '原有的读取及路径边界',
    )
    if navigation:
        # The guidance-only navigator uses its own concise handoff paragraph.
        required = (
            '/large_tool_results/', '工具结果引用', '只用宿主 `read_file`', '真实 schema',
            '准确引用', '续读', '覆盖', '截断', '执行器', 'Shell', 'Python', '不猜映射',
            '缺少读取能力', '报告缺口', '停止依赖',
        )
    return [fragment for fragment in required if fragment not in value]


class HostResultReadingContractTests(unittest.TestCase):
    def test_all_current_entries_have_independent_contract(self):
        entries = sorted((ROOT / 'skills').glob('*/SKILL.md'))
        self.assertEqual(len(entries), 11)
        for entry in entries:
            with self.subTest(skill=entry.parent.name):
                self.assertEqual(contract_issues(entry.read_text(encoding='utf-8'),
                                                 navigation=entry.parent.name == 'document-navigation'), [])

    def test_missing_safety_or_scope_requirements_are_detected(self):
        source = (ROOT / 'skills/docx/SKILL.md').read_text(encoding='utf-8')
        for fragment in ('仅用宿主提供的 `read_file`', '`execute`', '不猜测映射',
                         '缺少 `read_file`', '停止依赖', '普通业务文件'):
            with self.subTest(fragment=fragment):
                self.assertTrue(contract_issues(source.replace(fragment, '')))
        navigation = (ROOT / 'skills/document-navigation/SKILL.md').read_text(encoding='utf-8')
        for fragment in ('只用宿主 `read_file`', '执行器', '不猜映射', '缺少读取能力', '停止依赖'):
            with self.subTest(skill='document-navigation', fragment=fragment):
                self.assertTrue(contract_issues(navigation.replace(fragment, ''), navigation=True))


if __name__ == '__main__':
    unittest.main()
