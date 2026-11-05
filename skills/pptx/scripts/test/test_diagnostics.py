"""Public failures identify source pages and fields without publishing output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v680_native import native_marker, svg_page


class DiagnosticTests(unittest.TestCase):
    def failure(self, body: str) -> dict:
        payload = {"schema": "psl.pptx.v1", "slides": [
            {"svg": svg_page('<text x="50" y="90">First page</text>')}, {"svg": svg_page(body)},
        ]}
        with tempfile.TemporaryDirectory(prefix="pptx-diagnostic-") as directory:
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(SCRIPTS / "pptx.py"), "create", "-", "failed.pptx"],
                cwd=directory, input=json.dumps(payload), text=True, capture_output=True, check=False, timeout=45,
                env={**os.environ, "PATH": ""},
            )
            result = json.loads(process.stdout)
            self.assertFalse(result["ok"])
            self.assertEqual(process.returncode, 2)
            self.assertTrue(process.stderr.strip())
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(result["error"]["slide_number"], 2)
        self.assertNotIn(str(SCRIPTS), process.stdout)
        return result["error"]

    def test_foreign_object_identifies_page_element_and_stage(self) -> None:
        error = self.failure('<foreignObject id="bad-widget" width="50" height="50"/>')
        self.assertEqual(error["element_id"], "bad-widget")
        self.assertEqual(error["element_tag"], "foreignObject")
        self.assertEqual(error["stage"], "input_validation")
        self.assertEqual(error["field_path"], "svg.foreignObject")

    def test_attribute_error_identifies_its_field(self) -> None:
        error = self.failure('<rect id="bad-shape" x="1" y="2" width="20" height="30" onload="anything"/>')
        self.assertEqual(error["element_id"], "bad-shape")
        self.assertEqual(error["field_path"], "svg.rect.@onload")

    def test_metadata_error_uses_owning_native_element_id(self) -> None:
        error = self.failure('<g id="bad-chart" data-pptx-replace-with="chart" data-pptx-native-authority="json"><metadata type="application/json">{broken</metadata></g>')
        self.assertEqual(error["element_id"], "bad-chart")
        self.assertEqual(error["field_path"], "svg.metadata")

    def test_converter_failure_identifies_page_and_native_element(self) -> None:
        payload = {"schema": "ppt-master.semantic-table.v2", "x": 80, "y": 100, "width": 800, "height": 200,
                   "rows": [[{"text": "Owner", "col_span": 2}, {"text": "Hidden content"}]]}
        body = native_marker("table", payload).replace('<g ', '<g id="bad-table" ', 1)
        error = self.failure(body)
        self.assertEqual(error["stage"], "conversion")
        self.assertEqual(error["element_id"], "bad-table")

    def test_chart_payload_error_identifies_nested_field(self) -> None:
        payload = {"series": [{"name": "A", "values": "not an array"}]}
        body = native_marker("chart", payload).replace('<g ', '<g id="bad-values" ', 1)
        error = self.failure(body)
        self.assertEqual(error["element_id"], "bad-values")
        self.assertEqual(error["field_path"], "chart.series[0].values")


if __name__ == "__main__":
    unittest.main()
