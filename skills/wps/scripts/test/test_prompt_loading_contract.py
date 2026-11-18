"""Check task-oriented entry contracts, not observed model routing or token usage."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PromptLoadingContractTests(unittest.TestCase):
    """Keep frequent paths self-contained while advanced detail remains discoverable."""

    def test_frequent_paths_and_safety_remain_in_entry(self):
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        self.assertLess(len(entry), 8000)
        for marker in (
            'read_wps.py', 'run_wps_task.py', "<<'JSON'", 'wps_content_v1',
            'payload_fragment', 'next_cursor', 'source_sha256/result_sha256/options_sha256',
            'chunk_sha256/prefix_sha256', 'total_chars/total_utf8_bytes/result_sha256',
            'current_view_available', '不填零', '不打开外链', 'partial',
            'publication_uncertain', '不重跑生成', '纯stdout不新增归档',
            '普通读取、续读及基础创建用本入口',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, entry)

    def test_advanced_contract_is_conditional_not_lost(self):
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        detail = (ROOT / 'references/content-reading.md').read_text(encoding='utf-8')
        self.assertIn('普通读取不预读', entry)
        self.assertIn('<skill-root>/references/content-reading.md', entry)
        for marker in ('MC AlternateContent', 'nested_row_evidence', 'binary_optional_content_not_requested',
                       'all_stored_revisions_not_current', 'API `read()`', 'word-visibility.md'):
            with self.subTest(marker=marker):
                self.assertIn(marker, detail)
        self.assertNotIn('MC AlternateContent', entry)


if __name__ == '__main__':
    unittest.main()
