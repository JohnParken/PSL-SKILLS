"""Static OFD instruction checks; not a real host read or path-mapping test."""
import unittest
from pathlib import Path


ENTRY = Path(__file__).resolve().parents[2] / "SKILL.md"


class OfdSkillLocationTests(unittest.TestCase):
    def test_default_reading_route_is_content_first_and_paginates(self):
        text = ENTRY.read_text(encoding="utf-8")
        route = text.split("## 按任务读取", 1)[1].split("## 解释与交付", 1)[0]
        for fragment in ("默认第 1 文档第 1 页", "--document N --page N",
                         "scope.document_count", "scope.page_count", "done=false",
                         "source_sha256", "仅用户明确要求完整结构时", "--view full"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, route)
        self.assertLess(route.index("默认第 1 文档第 1 页"),
                        route.index("--view full"))

    def test_read_file_prefers_dot_skill_without_guessing(self):
        text = ENTRY.read_text(encoding="utf-8")
        section = text.split("## 执行环境约束与首步自检", 1)[1].split("## 按需加载", 1)[0]
        paragraph = next(p for p in section.split("\n\n") if p.startswith("**Skill 读取优先：**"))
        for fragment in ("`read_file`", "优先从 `.skill` 目录", "宿主实际提示或已确认信息",
                         "不假设目录或层级存在", "不猜测路径", "无法定位或读取", "停止依赖操作"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, paragraph)
        self.assertNotIn(".skill/ofd", paragraph)
        self.assertNotIn("./.skill", paragraph)


if __name__ == "__main__":
    unittest.main()
