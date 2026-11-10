"""Repository-only guidance checks, not host behavior or runtime enforcement."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("bank-writing", "docx", "et", "ofd", "pptx", "table-workflow", "wps", "xlsx")


class UserFileDiscoveryTests(unittest.TestCase):
    def test_user_file_discovery_in_business_entries(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                section = text.split("**用户文件定位：**", 1)[1].split("\n", 1)[0]
                for fragment in ("附件映射", "文件名", "交付物", "`./` 顶层", "不递归", "子目录", "歧义", "报告"):
                    self.assertIn(fragment, section)

    def test_other_paths_keep_their_own_contracts(self):
        text = (ROOT / "skills/docx/references/controlled-flow.md").read_text(encoding="utf-8")
        self.assertIn("其他程序输入仍遵循原有任务内相对路径契约", text)
        self.assertIn("归档副本", text)
        self.assertIn("Skill 内部资源", text)


if __name__ == "__main__":
    unittest.main()
