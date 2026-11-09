"""Common packager safety, explicit membership and reproducibility regressions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("all_skill_packager", REPO / "tools/package_skills.py")
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


class RuntimePackagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-packager-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text('---\nname: sample\nversion: "20261109.1200.0000"\n---\nBody\n', encoding="utf-8")
        (self.source / "scripts").mkdir()
        (self.source / "scripts/main.py").write_text('print("ok")\n', encoding="utf-8")
        self.manifest = self.work / "manifest.json"
        self.data = {"schema_version": 1, "files": ["SKILL.md", "scripts/main.py"],
                     "exclude_prefixes": ["scripts/test/", "assets/user_fonts/"], "exclude_files": {}}
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")

    def build(self, name="package.zip"):
        return PACKAGER.build_package(self.source, self.work / name, self.manifest)

    def test_deterministic_exact_members_and_source_bytes(self):
        first = self.build()
        second = self.build("second.zip")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["skill"], "sample")
        with zipfile.ZipFile(self.work / "package.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"sample/" + name for name in self.data["files"]})
            for name in self.data["files"]:
                self.assertEqual(archive.read("sample/" + name), (self.source / name).read_bytes())

    def test_new_unknown_runtime_file_is_not_silently_dropped(self):
        (self.source / "scripts/new.py").write_text("pass\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unclassified"):
            self.build()
        self.assertFalse((self.work / "package.zip").exists())

    def test_missing_required_file_refuses_publication(self):
        (self.source / "scripts/main.py").unlink()
        with self.assertRaisesRegex(ValueError, "Missing runtime"):
            self.build()
        self.assertFalse((self.work / "package.zip").exists())

    def test_test_and_cache_files_are_excluded(self):
        for name in ("scripts/test/test_sample.py", "scripts/__pycache__/main.pyc", ".DS_Store"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not runtime")
        result = self.build()
        self.assertEqual(result["files"], 2)
        self.assertEqual(len(result["excluded_files"]), 3)

    def test_private_font_bytes_and_symlinks_are_never_read(self):
        fonts = self.source / "assets/user_fonts"
        fonts.mkdir(parents=True)
        (fonts / "NOTICE.txt").write_text("Private fonts are excluded.\n", encoding="utf-8")
        (fonts / "private.ttf").write_bytes(b"private font sentinel")
        (fonts / "linked.ttf").symlink_to(self.work / "nonexistent-private-target")
        self.data["files"].append("assets/user_fonts/NOTICE.txt")
        self.write_manifest()
        self.build()
        with zipfile.ZipFile(self.work / "package.zip") as archive:
            self.assertEqual([n for n in archive.namelist() if "user_fonts" in n], ["sample/assets/user_fonts/NOTICE.txt"])
            self.assertNotIn(b"private font sentinel", b"".join(archive.read(n) for n in archive.namelist()))

    def test_manifest_cannot_include_private_font(self):
        self.data["files"].append("assets/user_fonts/private.ttf")
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Private installation"):
            self.build()

    def test_symlink_source_and_runtime_file_are_rejected(self):
        target = self.source / "scripts/main.py"
        target.unlink()
        target.symlink_to(self.work / "outside.py")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.build()
        link = self.work / "linked-source"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            PACKAGER.build_package(link, self.work / "out.zip", self.manifest)

    def test_existing_output_and_link_are_unchanged(self):
        target = self.work / "package.zip"
        target.write_bytes(b"existing user package")
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaises(FileExistsError):
            self.build()
        link = self.work / "link.zip"
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.build("link.zip")
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.assertTrue(link.is_symlink())

    def test_output_inside_source_is_rejected(self):
        with self.assertRaises(ValueError):
            PACKAGER.build_package(self.source, self.source / "bad.zip", self.manifest)
        self.assertFalse((self.source / "bad.zip").exists())

    def test_unsafe_duplicate_and_conflicting_members_are_rejected(self):
        for member in ("../outside.py", "/absolute.py", "scripts/../main.py", "scripts\\bad.py", "SKILL.md", "scripts/test/test_sample.py"):
            with self.subTest(member=member):
                self.data["files"] = ["SKILL.md", "scripts/main.py", member]
                self.write_manifest()
                with self.assertRaises(ValueError):
                    self.build()

    def test_unknown_manifest_schema_is_rejected(self):
        self.data["unexpected"] = True
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.build()


if __name__ == "__main__":
    unittest.main()
