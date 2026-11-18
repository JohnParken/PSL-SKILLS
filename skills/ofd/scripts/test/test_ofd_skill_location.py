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

    def test_prompt_keeps_read_and_cursor_self_contained(self):
        text = ENTRY.read_text(encoding="utf-8")
        self.assertLess(len(text), 5500)
        for fragment in ("--check", "--cursor", "next_cursor", "character_offset",
                         "scope.document_count", "scope.page_count", "source_sha256",
                         "partial", "丢弃该次调用", "不绕过文件级安全",
                         "普通读取和常见页内续读仅用本入口"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_file_delivery_is_explicit_and_packaged(self):
        text = ENTRY.read_text(encoding="utf-8")
        self.assertIn("**保存前**读", text)
        reference = ENTRY.parent / "references" / "task-delivery.md"
        delivery = reference.read_text(encoding="utf-8")
        for fragment in ("尚不存在", "不覆盖已有文件", "questionId", "待交接",
                         "非空清单", "仅重试归档", "--protect-input",
                         'scripts/.venv/bin/python', "archive_output"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, delivery)

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
