"""Integration coverage for XLSX commands run from an isolated task directory."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]


def tree_snapshot(root):
    """Capture all relative names and file bytes, including directory entries."""
    snapshot = {".": None}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = path.read_bytes() if path.is_file() else None
    return snapshot


class XlsxPathIntegrationTests(unittest.TestCase):
    def run_cli(self, script, cwd, *arguments):
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(script), *arguments],
            cwd=cwd,
            env={"PATH": ""},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail("CLI did not return JSON: %s\n%s\n%s" % (exc, result.stdout, result.stderr))

    def assert_standard_package_aliases(self, workbook_path):
        with zipfile.ZipFile(workbook_path) as archive:
            members = set(archive.namelist())
        self.assertIn("[Content_Types].xml", members)
        self.assertIn("_rels/.rels", members)
        self.assertNotIn("content_types.xml", members)
        self.assertNotIn("_rels/root.rels", members)

    def test_cli_works_from_separate_cwd_with_read_only_unicode_install(self):
        with tempfile.TemporaryDirectory(prefix="xlsx path integration 测试 ") as temporary:
            root = Path(temporary)
            installed = root / "installed skill 空间"
            task_cwd = root / "task cwd 空间"
            shutil.copytree(SKILL_ROOT, installed)
            task_cwd.mkdir()

            copied_script = installed / "scripts" / "stdlib_xlsx.py"
            self.assertTrue(copied_script.is_absolute())
            self.assertTrue(copied_script.is_file())
            self.assertFalse((task_cwd / "scripts").exists())
            self.assertIn(" ", str(installed))
            self.assertTrue(any(ord(character) > 127 for character in str(installed)))

            install_paths = [installed, *installed.rglob("*")]
            before = tree_snapshot(installed)
            try:
                for path in sorted(install_paths, key=lambda item: len(item.parts), reverse=True):
                    path.chmod(0o555 if path.is_dir() else 0o444)
                self.assertTrue(all(
                    path.stat().st_mode & 0o222 == 0 for path in install_paths
                ))

                check = self.assert_success(self.run_cli(copied_script, task_cwd, "check"))
                self.assertEqual(check.get("status"), "ready")
                self.assertEqual(check.get("dependencies"), "standard_library_only")

                spec = {
                    "sheets": [{
                        "name": "Sales",
                        "cells": {
                            "A1": {"value": "Item"},
                            "B1": {"value": "Amount"},
                            "A2": {"value": "tea"},
                            "B2": {"value": 2},
                            "A3": {"value": "coffee"},
                            "B3": {"formula": "B2*3"},
                        },
                    }],
                }
                appearance = {
                    "preset": "readable",
                    "sheets": {
                        "Sales": {
                            "header_rows": "1",
                            "alternate_rows": True,
                            "column_widths": {"A": 18, "B": 14},
                            "number_formats": {"B": "0.00"},
                        },
                    },
                }
                (task_cwd / "sales.json").write_text(
                    json.dumps(spec, ensure_ascii=False), encoding="utf-8"
                )
                (task_cwd / "appearance.json").write_text(
                    json.dumps(appearance, ensure_ascii=False), encoding="utf-8"
                )

                for output_name in ("created-one.xlsx", "created-two.xlsx"):
                    created = self.assert_success(self.run_cli(
                        copied_script,
                        task_cwd,
                        "create",
                        "sales.json",
                        output_name,
                        "--appearance",
                        "appearance.json",
                    ))
                    self.assertEqual(created.get("status"), "calculated_subset")
                    self.assertTrue(created.get("output_written"))
                    self.assertEqual(created.get("output"), output_name)
                    self.assertEqual(created.get("appearance", {}).get("preset"), "readable")
                    sheet_report = created["appearance"]["sheets"][0]
                    self.assertEqual(sheet_report.get("status"), "styled")
                    self.assertGreater(sheet_report.get("styled_cells", 0), 0)
                    self.assertTrue((task_cwd / output_name).is_file())
                    self.assert_standard_package_aliases(task_cwd / output_name)

                read = self.assert_success(self.run_cli(
                    copied_script, task_cwd, "read", "created-one.xlsx"
                ))
                original_cells = read["sheets"][0]["cells"]
                self.assertEqual(read["sheets"][0]["name"], "Sales")
                self.assertEqual(original_cells["A2"]["value"], "tea")
                self.assertEqual(original_cells["B2"]["value"], "2")
                self.assertEqual(original_cells["B3"]["value"], "6")
                self.assertEqual(original_cells["B3"]["formula"], "B2*3")
                self.assertGreater(int(original_cells["A1"]["style"]), 0)
                original_workbook = task_cwd / "created-one.xlsx"
                original_bytes = original_workbook.read_bytes()

                patch = {"sheets": [{"name": "Sales", "cells": {"B2": {"value": 4}}}]}
                (task_cwd / "patch.json").write_text(
                    json.dumps(patch, ensure_ascii=False), encoding="utf-8"
                )
                edited = self.assert_success(self.run_cli(
                    copied_script,
                    task_cwd,
                    "edit",
                    "created-one.xlsx",
                    "patch.json",
                    "edited.xlsx",
                ))
                self.assertEqual(edited.get("status"), "calculated_subset")
                self.assertTrue(edited.get("output_written"))
                self.assertEqual(original_workbook.read_bytes(), original_bytes)

                edited_read = self.assert_success(self.run_cli(
                    copied_script, task_cwd, "read", "edited.xlsx"
                ))
                edited_cells = edited_read["sheets"][0]["cells"]
                self.assertEqual(edited_cells["B2"]["value"], "4")
                self.assertEqual(edited_cells["B3"]["value"], "12")
                self.assertEqual(edited_cells["B3"]["formula"], "B2*3")
                self.assertEqual(edited_cells["A2"]["value"], "tea")
                self.assert_standard_package_aliases(task_cwd / "edited.xlsx")

                original_after_edit = self.assert_success(self.run_cli(
                    copied_script, task_cwd, "read", "created-one.xlsx"
                ))
                self.assertEqual(
                    original_after_edit["sheets"][0]["cells"]["B2"]["value"], "2"
                )
                # Every maintained CLI must load its own dependencies outside the install cwd.
                entrypoints = (
                    "stdlib_xlsx", "xlsx_maturity",
                    "xlsx_customers", "xlsx_management_policy", "xlsx_deposits",
                    "xlsx_deposit_structure", "xlsx_reconcile", "xlsx_dashboard",
                    "xlsx_management", "xlsx_daily", "xlsx_result_export",
                    "xlsx_rank_share", "xlsx_advanced", "xlsx_match", "xlsx_rows",
                    "xlsx_header_candidates", "xlsx_headers", "xlsx_analysis",
                    "xlsx_identify", "xlsx_tabular",
                )
                for entrypoint in entrypoints:
                    with self.subTest(entrypoint=entrypoint):
                        help_result = self.run_cli(
                            installed / "scripts" / (entrypoint + ".py"), task_cwd, "--help"
                        )
                        self.assertEqual(help_result.returncode, 0, help_result.stderr)
                        self.assertIn("usage:", help_result.stdout.lower())

                # Relative paths with subdirectories, spaces and Unicode work across CLIs.
                (task_cwd / "数据 files").mkdir()
                (task_cwd / "数据 files" / "source data.csv").write_text(
                    "item,amount\ntea,2\ncoffee,3\n", encoding="utf-8"
                )
                tabular_script = installed / "scripts" / "xlsx_tabular.py"
                self.assert_success(self.run_cli(
                    tabular_script, task_cwd, "import", "./数据 files/source data.csv",
                    "./数据 files/imported report.xlsx", "--sheet", "Data",
                    "--numeric-columns", "B",
                ))
                calculated = self.assert_success(self.run_cli(
                    copied_script, task_cwd, "calculate", "./数据 files/imported report.xlsx",
                    "./数据 files/calculated report.xlsx",
                ))
                self.assertTrue(calculated["output_written"])
                self.assert_success(self.run_cli(
                    tabular_script, task_cwd, "export", "./数据 files/calculated report.xlsx",
                    "./数据 files/exported data.csv", "--sheet", "Data",
                ))
                csv_read = self.assert_success(self.run_cli(
                    copied_script, task_cwd, "read", "./数据 files/exported data.csv"
                ))
                self.assertEqual(csv_read["rows"], [["item", "amount"], ["tea", "2"], ["coffee", "3"]])
                self.assertEqual(tree_snapshot(installed), before)
                self.assertFalse(any(
                    path.name == "__pycache__" or path.suffix == ".pyc"
                    for path in installed.rglob("*")
                ))
            finally:
                for path in sorted(install_paths, key=lambda item: len(item.parts), reverse=True):
                    path.chmod(0o755 if path.is_dir() else 0o644)


if __name__ == "__main__":
    unittest.main()
