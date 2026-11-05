"""Standard-library-only checks for P3 token-lightweight documentation routing."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import hashlib
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
REFS = ROOT / 'references'

ROUTE = {
    '路径解析示例':'generation-runtime.md',
    '预检与集成验收':'generation-runtime.md',
    '交付文件命名':'generation-runtime.md',
    '输出目录与文件名':'generation-runtime.md',
    '配置调试':'generation-runtime.md',
    '主题与文档标题':'generation-styles.md',
    '顶层字段':'generation-blocks.md',
    '默认格式与页面':'generation-styles.md',
    '样式字段与覆盖顺序':'generation-styles.md',
    '内容块':'generation-blocks.md',
    '表格配置与示例':'generation-tables.md',
    '显式覆盖示例':'generation-examples.md',
    '中文 JSON 与引号':'generation-examples.md',
    '生成后格式引导':'generation-runtime.md',
    'P1 原生排版扩展（2026-10-31）':'generation-blocks.md',
    'P2 原生高级版式扩展（2026-10-31）':'generation-blocks.md',
}

class SkillTokenRoutingTests(unittest.TestCase):
    def test_compact_main_entry_and_core(self):
        self.assertLess((ROOT/'SKILL.md').stat().st_size, 16_000)
        self.assertLess((REFS/'generation-core.md').stat().st_size, 7_500)
        self.assertLess((REFS/'generation.md').stat().st_size, 5_000)

    def test_route_all_legacy_sections(self):
        router=(REFS/'generation.md').read_text(encoding='utf-8')
        self.assertEqual(set(re.findall(r'^## (.+)$',router,re.M)), {'目录','历史锚点兼容导航',*ROUTE})
        for heading,target in ROUTE.items():
            sub=(REFS/target).read_text(encoding='utf-8')
            self.assertEqual(len(re.findall(r'^## '+re.escape(heading)+r'$',sub,re.M)),1)

    def test_mandatory_safety_not_hidden(self):
        main=(ROOT/'SKILL.md').read_text(encoding='utf-8')
        for marker in ['<skill-root>','任务工作目录','-I -S -B','标准库','不可信数据',
                       'cleanup_incomplete','archive_status','run_docx_task.py',
                       'run_docx_edit.py','run_docx_task.py','不自动读取归档历史',
                       'stderr','真实退出码','在普通文档任务中修改本 Skill']:
            self.assertIn(marker,main)

    def test_basic_task_does_not_require_full_generation(self):
        main=(ROOT/'SKILL.md').read_text(encoding='utf-8')
        core=(REFS/'generation-core.md').read_text(encoding='utf-8')
        for fragment in ['"theme":"official"','"type":"title"','"type":"paragraph"']:
            self.assertIn(fragment, main+core)
        self.assertIn('按需',main)
        self.assertIn('不要把此索引当作需要继续读取全部文件',
                      (REFS/'generation.md').read_text(encoding='utf-8'))

    def test_router_files_and_anchors_exist(self):
        docs=[ROOT/'SKILL.md',*REFS.glob('*.md')]
        matcher=re.compile(r'<skill-root>/(references/[\w-]+\.md|SKILL\.md)(?:#([^`\s\)]+))?')
        for doc in docs:
            body=doc.read_text(encoding='utf-8')
            for match in matcher.finditer(body):
                dest=ROOT/match.group(1)
                self.assertTrue(dest.is_file(),(doc.name,str(dest)))
                anchor=match.group(2)
                if not anchor:
                    continue
                headings=re.findall(r'^#{1,6} (.+)$',dest.read_text(encoding='utf-8'),re.M)
                slugs={re.sub(r'[^\w\-\u4e00-\u9fff\s]','',h.lower()).strip().replace(' ','-') for h in headings}
                self.assertIn(anchor,slugs,(doc.name,dest.name,anchor))

if __name__ == '__main__':
    unittest.main()
