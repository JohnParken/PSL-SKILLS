"""Guard offline ET task routes; these are not observations of a real host."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class EntryRoutes(unittest.TestCase):
    def test_default_read_is_self_contained(self):
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        common = entry.split('## 仅在任务需要时打开', 1)[0]
        for marker in ('run --check', 'run ./input.et', '--summary', '不先测试写入',
                       'next_arguments', '逐元素', 'next_cursor=null', 'et_content_page_v1',
                       'omissions_affect_cell_values=false', 'cache_status', '不重算',
                       '停止依赖操作', '未存储/未读', 'read_file'):
            self.assertIn(marker, common)

    def test_specialist_routes_and_delivery_are_explicit(self):
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        routing = entry.split('## 仅在任务需要时打开', 1)[1]
        for ref in ('table-preview', 'basic-statistics', 'table-analysis', 'bounded-reading',
                    'spreadsheet-consumption', 'advanced-spreadsheet-routing', 'result-delivery'):
            self.assertIn(f'<skill-root>/references/{ref}.md', routing)
            self.assertTrue((ROOT / 'references' / (ref + '.md')).is_file())
        delivery = (ROOT / 'references/result-delivery.md').read_text(encoding='utf-8')
        for marker in ('--delivery', '--protect-input', 'archive_user_notice'):
            self.assertIn(marker, delivery)
