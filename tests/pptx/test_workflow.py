"""Real isolated-CLI tests for editable output and task-file protection."""

from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "pptx"
SCRIPT = SKILL_ROOT / "scripts" / "pptx.py"
SVG_NS = "http://www.w3.org/2000/svg"
NAMESPACES = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
SIMPLE_SVG = (
    f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
    '<rect x="40" y="40" width="240" height="100" fill="#114477"/>'
    '<text x="60" y="102" font-size="30" fill="#FFFFFF">你好 &amp; Hello</text></svg>'
)


def config(svg: str = SIMPLE_SVG) -> dict:
    return {"schema": "psl.pptx.v1", "title": "检查 <中文> & English", "slides": [{"svg": svg, "notes": "第一行\nSecond line"}]}


def image_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\xff\x00")) + chunk(b"IEND", b""))


def parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def snapshot(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="psl-pptx-test-")
        self.root = Path(self.temporary.name)
        self.environment = {**os.environ, "PATH": "", "PYTHONPATH": str(self.root), "PYTHONNOUSERSITE": "1"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(
        self, *arguments: str, payload: dict | str | None = None, script: Path = SCRIPT,
        guard_installation: bool = False,
    ) -> tuple[subprocess.CompletedProcess, dict]:
        data = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
        launch = [sys.executable, "-I", "-S", "-B", str(script), *arguments]
        if guard_installation:
            bootstrap = (
                "import os, pathlib, runpy, sys\n"
                "root = pathlib.Path(sys.argv[1]).resolve()\n"
                "def guard(event, args):\n"
                "    if event == 'open' and not isinstance(args[0], int):\n"
                "        path = pathlib.Path(args[0]).resolve()\n"
                "        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0\n"
                "        writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)\n"
                "        if writing and path.is_relative_to(root):\n"
                "            raise PermissionError('readonly installation: write attempted')\n"
                "    if event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename'}:\n"
                "        if pathlib.Path(args[0]).resolve().is_relative_to(root):\n"
                "            raise PermissionError('readonly installation: mutation attempted')\n"
                "sys.addaudithook(guard)\n"
                "sys.argv = sys.argv[2:]\n"
                "runpy.run_path(sys.argv[0], run_name='__main__')\n"
            )
            launch = [sys.executable, "-I", "-S", "-B", "-c", bootstrap, str(script.parents[1]), str(script), *arguments]
        process = subprocess.run(
            launch, cwd=self.root, input=data, text=True, encoding="utf-8", capture_output=True,
            env=self.environment, timeout=45, check=False,
        )
        self.assertTrue(process.stdout.strip(), process.stderr)
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout was not one JSON document: {process.stdout!r}; stderr={process.stderr!r}")
        self.assertEqual(result["ok"], process.returncode == 0, (process.stdout, process.stderr, process.returncode))
        return process, result

    def assert_failure(self, *arguments: str, payload: dict | str | None = None) -> dict:
        process, result = self.invoke(*arguments, payload=payload)
        self.assertEqual(process.returncode, 2, result)
        self.assertTrue(process.stderr.strip())
        return result

    def test_environment_and_write_probe_leave_no_files(self) -> None:
        process, result = self.invoke("check", "--write-probe")
        self.assertEqual(process.returncode, 0, result)
        self.assertEqual(result["status"], "environment-ok")
        self.assertEqual(result["write_probe"], "passed")
        self.assertFalse(result["site_packages"])
        self.assertEqual(result["third_party_python_dependencies"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_editable_text_notes_and_roundtrip_inspection(self) -> None:
        process, result = self.invoke("create", "-", "deck.pptx", payload=config())
        self.assertEqual(process.returncode, 0, result)
        archive_parts = parts((self.root / "deck.pptx").read_bytes())
        slide = ET.fromstring(archive_parts["ppt/slides/slide1.xml"])
        self.assertEqual([item.text for item in slide.findall(".//a:t", NAMESPACES)], ["你好 & Hello"])
        self.assertEqual(len(slide.findall(".//p:sp", NAMESPACES)), 2)
        self.assertNotIn("ppt/media/", archive_parts)
        self.assertEqual(result["temporary_files"], 0)
        self.assertEqual(result["visual_validation"], "not_run")
        process, inspected = self.invoke("inspect", "deck.pptx")
        self.assertEqual(process.returncode, 0, inspected)
        self.assertEqual(inspected["slide_count"], 1)
        self.assertIn("你好 & Hello", inspected["slides"][0]["text"])
        self.assertEqual(inspected["slides"][0]["notes"], ["第一行", "Second line"])
        self.assertEqual(inspected["title"], "检查 <中文> & English")
        self.assertEqual({path.name for path in self.root.iterdir()}, {"deck.pptx"})

    def test_distribution_example_keeps_native_chart_table_and_math(self) -> None:
        example = json.loads((SKILL_ROOT / "assets" / "example.json").read_text(encoding="utf-8"))
        process, result = self.invoke("create", "-", "example.pptx", payload=example)
        self.assertEqual(process.returncode, 0, result)
        self.assertEqual(result["slide_count"], 4)
        archive_parts = parts((self.root / "example.pptx").read_bytes())
        table = ET.fromstring(archive_parts["ppt/slides/slide3.xml"])
        self.assertEqual(len(table.findall(".//a:tbl", NAMESPACES)), 1)
        self.assertIn("原生对象", [node.text for node in table.findall(".//a:t", NAMESPACES)])
        formula = ET.fromstring(archive_parts["ppt/slides/slide4.xml"])
        self.assertGreater(len(formula.findall(".//m:oMath", NAMESPACES)), 0)
        chart_names = [name for name in archive_parts if name.startswith("ppt/charts/chart") and name.endswith(".xml")]
        self.assertEqual(len(chart_names), 1)
        chart = ET.fromstring(archive_parts[chart_names[0]])
        values = [node.text for node in chart.findall(".//c:numCache/c:pt/c:v", NAMESPACES)]
        self.assertEqual(values, ["18", "24", "29", "36", "12", "17", "21", "26"])
        workbook = next(data for name, data in archive_parts.items() if name.endswith(".xlsx"))
        worksheet = ET.fromstring(parts(workbook)["xl/worksheets/sheet1.xml"])
        self.assertEqual([node.text for node in worksheet.findall(".//s:v", NAMESPACES)], values[0:1] + values[4:5]
                         + values[1:2] + values[5:6] + values[2:3] + values[6:7] + values[3:4] + values[7:8])

    def test_declared_chart_types_have_editable_series_and_workbooks(self) -> None:
        slides = []
        for kind in ("column", "bar", "line", "area", "pie", "doughnut", "scatter"):
            payload = {"type": kind, "x": 80, "y": 100, "width": 1000, "height": 500,
                       "categories": ["A", "B"], "series": [{"name": "Series", "values": [3, 8]}]}
            if kind == "scatter":
                payload = {"type": kind, "x": 80, "y": 100, "width": 1000, "height": 500,
                           "series": [{"name": "Series", "x": [1, 2], "y": [3, 8]}]}
            svg = (f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><g data-pptx-replace-with="chart" '
                   f'data-pptx-native-authority="json"><metadata type="application/json">{escape(json.dumps(payload))}</metadata></g></svg>')
            slides.append({"svg": svg})
        process, result = self.invoke("create", "-", "charts.pptx", payload={"schema": "psl.pptx.v1", "slides": slides})
        self.assertEqual(process.returncode, 0, result)
        archive_parts = parts((self.root / "charts.pptx").read_bytes())
        self.assertEqual(len([name for name in archive_parts if name.endswith(".xlsx")]), 7)
        tags = set()
        for name, data in archive_parts.items():
            if name.startswith("ppt/charts/chart") and name.endswith(".xml"):
                chart = ET.fromstring(data)
                tags.update(child.tag.rsplit("}", 1)[-1] for child in chart.findall(".//c:plotArea/*", NAMESPACES))
        self.assertTrue({"barChart", "lineChart", "areaChart", "pieChart", "doughnutChart", "scatterChart"}.issubset(tags))

    def test_image_resolution_uses_task_cwd_and_preserves_original_bytes(self) -> None:
        (self.root / "slides").mkdir()
        image = image_png()
        (self.root / "picture.png").write_bytes(image)
        svg = (f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
               '<image x="20" y="30" width="100" height="100" preserveAspectRatio="xMidYMid slice" href="picture.png"/></svg>')
        (self.root / "slides" / "input.svg").write_text(svg, encoding="utf-8")
        before = snapshot(self.root)
        payload = {"schema": "psl.pptx.v1", "slides": [{"svg_path": "slides/input.svg"}]}
        process, result = self.invoke("create", "-", "images.pptx", payload=payload)
        self.assertEqual(process.returncode, 0, result)
        archive_parts = parts((self.root / "images.pptx").read_bytes())
        self.assertEqual([data for name, data in archive_parts.items() if name.startswith("ppt/media/")], [image])
        slide = ET.fromstring(archive_parts["ppt/slides/slide1.xml"])
        crop = slide.find(".//a:srcRect", NAMESPACES)
        self.assertEqual((crop.get("l"), crop.get("r")), ("25000", "25000"))
        for name, digest in before.items():
            self.assertEqual(snapshot(self.root)[name], digest)

    def test_image_data_uri_and_same_document_use(self) -> None:
        encoded = base64.b64encode(image_png()).decode("ascii")
        svg = (f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><defs>'
               '<g id="item"><circle cx="40" cy="40" r="25" fill="#FF0000"/></g></defs>'
               '<use href="#item" x="100" y="100"/>'
               f'<image href="data:image/png;base64,{encoded}" x="300" y="100" width="200" height="100"/></svg>')
        process, result = self.invoke("create", "-", "reuse.pptx", payload=config(svg))
        self.assertEqual(process.returncode, 0, result)

    def test_existing_output_and_inputs_are_unchanged(self) -> None:
        original = b"user file must survive\x00"
        (self.root / "keep.pptx").write_bytes(original)
        result = self.assert_failure("create", "-", "keep.pptx", payload=config())
        self.assertEqual(result["error"]["code"], "output_exists")
        self.assertEqual((self.root / "keep.pptx").read_bytes(), original)

    def test_path_escape_absolute_and_symlinks_are_refused(self) -> None:
        for output in ("../escape.pptx", str(self.root / "absolute.pptx"), "C:\\escape.pptx"):
            with self.subTest(output=output):
                self.assert_failure("create", "-", output, payload=config())
        (self.root / "input.json").write_text(json.dumps(config()), encoding="utf-8")
        (self.root / "alias.json").symlink_to(self.root / "input.json")
        result = self.assert_failure("create", "alias.json", "out.pptx")
        self.assertEqual(result["error"]["code"], "task_boundary")
        self.assertFalse((self.root / "out.pptx").exists())

    def test_unsupported_input_fails_without_partial_artifact(self) -> None:
        cases = [
            f'<?xml-stylesheet type="text/css" href="https://example.com/style.css"?><svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"/>',
            f'<!DOCTYPE svg [<!ENTITY x "wrong">]><svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><text>&x;</text></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><script>alert(1)</script></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><image href="https://example.com/a.png"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><use href="other.svg#shape"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><use data-icon="library/icon"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><rect width="100" height="100" filter="url(#blur)"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 640 480"/>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><g data-pptx-native-ref="r0"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><rect width="100" height="100" style="mix-blend-mode:multiply"/></svg>',
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><rect width="100" height="100" paint-order="stroke fill"/></svg>',
        ]
        for svg in cases:
            with self.subTest(svg=svg):
                self.assert_failure("create", "-", "failed.pptx", payload=config(svg))
                self.assertFalse((self.root / "failed.pptx").exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_json_errors_and_missing_arguments_keep_json_and_exit_code(self) -> None:
        for payload in ('{"schema":"psl.pptx.v1","schema":"duplicate"}', '{"width":NaN}', '{"width":1e999}', '[]', 'bad json'):
            with self.subTest(payload=payload):
                self.assert_failure("create", "-", "failed.pptx", payload=payload)
        invalid = config()
        invalid["text_flow"] = []
        self.assert_failure("create", "-", "failed.pptx", payload=invalid)
        self.assert_failure("create")
        self.assertFalse((self.root / "failed.pptx").exists())

    def test_native_data_ambiguity_and_expansion_are_rejected_before_conversion(self) -> None:
        table = {"schema": "ppt-master.semantic-table.v2", "x": 10, "y": 10, "width": 800, "height": 500,
                 "rows": [["Chosen first"]]}
        huge_grid = {**table, "rows": [["cell"] * 1000] + [[] for _ in range(999)]}
        chart = {"chart_type": "waterfall", "categories": ["A"], "series": [{"name": "Series", "values": [1]}]}
        chart_points = {"type": "column", "categories": ["A"] * 2001, "series": [{"name": "Series", "values": [1] * 2001}]}
        bodies = [
            '<g data-pptx-replace-with="table" data-pptx-native-authority="json" '
            f'data-pptx-json="{escape(json.dumps(table), {chr(34): "&quot;"})}">'
            f'<metadata type="application/json">{escape(json.dumps(table))}</metadata></g>',
            f'<g data-pptx-replace-with="table"><metadata type="application/json">{escape(json.dumps(table))}</metadata></g>',
            '<g data-pptx-replace-with="table" data-pptx-native-authority="json">'
            f'<metadata type="application/json">{escape(json.dumps(huge_grid))}</metadata></g>',
        ]
        for payload in (chart, chart_points):
            bodies.append('<g data-pptx-replace-with="chart" data-pptx-native-authority="json">'
                          f'<metadata type="application/json">{escape(json.dumps(payload))}</metadata></g>')
        for body in bodies:
            with self.subTest(body=body[:100]):
                svg = f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">{body}</svg>'
                self.assert_failure("create", "-", "ambiguous.pptx", payload=config(svg))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_readonly_installation_and_environment_shadowing(self) -> None:
        installation = self.root / "installed"
        shutil.copytree(SKILL_ROOT, installation)
        before = snapshot(installation)
        # An empty PATH and -I -S exclude executable fallbacks, PYTHONPATH and
        # task-directory module shadowing; readonly mode also checks invariance.
        (self.root / "sitecustomize.py").write_text('raise RuntimeError("must not load")', encoding="utf-8")
        (self.root / "json.py").write_text('raise RuntimeError("must not load")', encoding="utf-8")
        for path in installation.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        installation.chmod(0o555)
        try:
            process, result = self.invoke(
                "create", "-", "readonly.pptx", payload=config(), script=installation / "scripts" / "pptx.py",
                guard_installation=True,
            )
            self.assertEqual(process.returncode, 0, result)
            self.assertEqual(snapshot(installation), before)
            self.assertFalse(list(installation.rglob("__pycache__")))
        finally:
            installation.chmod(0o755)
            for path in installation.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)

    def test_concurrent_unique_outputs_and_collision(self) -> None:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(self.invoke, "create", "-", f"parallel-{index}.pptx", payload=config()) for index in range(3)]
            outputs = [future.result() for future in futures]
        self.assertTrue(all(process.returncode == 0 for process, _result in outputs), outputs)
        self.assertEqual(len({result["sha256"] for _process, result in outputs}), 1)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.invoke, "create", "-", "collision.pptx", payload=config()) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(process.returncode for process, _result in results), [0, 2])
        process, result = self.invoke("validate", "collision.pptx")
        self.assertEqual(process.returncode, 0, result)

    def test_runtime_source_imports_only_stdlib_or_distributed_modules(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        roots = {path.stem for path in scripts.iterdir()} | {path.stem for path in (scripts / "_engine").iterdir()}
        violations = []
        for path in scripts.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [(node.module or "").split(".")[0]]
                else:
                    continue
                violations.extend(f"{path.name}:{name}" for name in modules if name not in sys.stdlib_module_names and name not in roots)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
