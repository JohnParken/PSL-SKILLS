"""Repository-only guidance checks, not host behavior or runtime enforcement."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("bank-writing", "docx", "et", "ofd", "pptx", "table-workflow", "tl-docx", "tl-pptx", "wps", "xlsx")


class UserFileDiscoveryTests(unittest.TestCase):
    def test_navigation_owns_full_discovery(self):
        text = (ROOT / "skills/document-navigation/SKILL.md").read_text(encoding="utf-8")
        for fragment in (
            "`ls -la`", "附件映射", "指定的文件名", "待查交付物", "顶层", "不递归",
            "不查子目录", "历史文件", "歧义", "报告", "不猜路径", "跳过此步",
            "禁止用 `read_file`", "核实的文件名", "`/large_tool_results/`",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_business_entries_keep_direct_call_boundary(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(any(
                    all(fragment in line for fragment in (
                        "上传文件", "待查交付物", "`./` 顶层", "不递归", "不查子目录", "澄清", "不猜路径",
                    ))
                    for line in text.splitlines()
                ))
                self.assertNotIn("**用户文件定位：**", text)

    def test_other_paths_keep_their_own_contracts(self):
        text = (ROOT / "skills/docx/references/controlled-flow.md").read_text(encoding="utf-8")
        self.assertIn("其他程序输入仍遵循原有任务内相对路径契约", text)
        self.assertIn("归档副本", text)
        self.assertIn("Skill 内部资源", text)


if __name__ == "__main__":
    unittest.main()
