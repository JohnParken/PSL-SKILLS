"""Focused regression tests for the tl-docx read report API."""

import pathlib
from hashlib import sha256
import sys
import unittest

from docx.oxml import parse_xml


SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from tl_read import report


class MemoryPackage:
    """Small in-memory implementation of the public package reader contract."""

    def __init__(self, parts, relationships=None):
        self.main = "word/document.xml"
        self.names = set(parts)
        self.types = {name: "application/xml" for name in self.names}
        self._parts = {name: value.encode("utf-8") for name, value in parts.items()}
        self._relationships = relationships or {}

    def xml(self, name):
        return parse_xml(self.read(name))

    def read(self, name):
        """Return the stored bytes of a member."""
        return self._parts[name]

    def related(self, source, relationship_id):
        return self._relationships.get((source, relationship_id))


def report_fixture():
    document = """
    <w:document
        xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
        xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
        xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
        xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"
        xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
        xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/><w:numPr>
            <w:ilvl w:val="0"/><w:numId w:val="1"/>
          </w:numPr></w:pPr>
          <w:r><w:t>Overview</w:t></w:r>
          <w:hyperlink r:id="rIdLink"><w:r><w:t>Reference</w:t></w:r></w:hyperlink>
          <w:r><w:fldChar w:fldCharType="begin"/></w:r>
          <w:r><w:instrText> PAGE </w:instrText></w:r>
          <w:r><w:fldChar w:fldCharType="separate"/></w:r>
          <w:r><w:t>1</w:t></w:r>
          <w:r><w:fldChar w:fldCharType="end"/></w:r>
          <w:del><w:r><w:delText>Removed</w:delText></w:r></w:del>
          <m:oMath><m:r><m:t>x+y</m:t></m:r></m:oMath>
        </w:p>
        <w:p><w:pPr><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr>
          <w:r><w:t>Unnumbered</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>
              <w:p><w:r><w:t>Origin</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
            <w:tc><w:p><w:r><w:t>Next</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
        <w:p>
          <w:r><w:drawing><mc:AlternateContent>
            <mc:Choice Requires="wps"><wps:wsp><wps:txbx>
              <w:txbxContent><w:p><w:r><w:t>Stored box</w:t></w:r></w:p></w:txbxContent>
            </wps:txbx></wps:wsp></mc:Choice>
            <mc:Fallback><w:txbxContent><w:p><w:r><w:t>Fallback box</w:t></w:r></w:p>
            </w:txbxContent></mc:Fallback>
          </mc:AlternateContent><wp:inline><wp:docPr name="Picture" descr="Chart icon"/>
            <a:graphic><a:graphicData><pic:pic><a:blip r:embed="rIdImage"/></pic:pic>
            </a:graphicData></a:graphic>
          </wp:inline></w:drawing></w:r>
        </w:p>
        <w:p><w:r><w:drawing><a:graphic><a:graphicData>
          <c:chart r:id="rIdChart"/>
        </a:graphicData></a:graphic></w:drawing></w:r></w:p>
        <w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>
      </w:body>
    </w:document>
    """
    chart = """
    <c:chartSpace
        xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
        xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <c:externalData r:id="rIdWorkbook"/>
      <c:chart><c:plotArea><c:pieChart><c:ser>
        <c:idx val="0"/><c:tx><c:v>Revenue</c:v></c:tx>
        <c:cat><c:strRef><c:f>Sheet1!$A$1:$A$2</c:f><c:strCache>
          <c:ptCount val="2"/><c:pt idx="0"><c:v>North</c:v></c:pt>
          <c:pt idx="1"><c:v>South</c:v></c:pt>
        </c:strCache></c:strRef></c:cat>
        <c:val><c:numRef><c:f>Sheet1!$B$1:$B$2</c:f><c:numCache>
          <c:formatCode>0.00</c:formatCode><c:ptCount val="2"/>
          <c:pt idx="0"><c:v>10.00</c:v></c:pt>
          <c:pt idx="1"><c:v>20.0</c:v></c:pt>
        </c:numCache></c:numRef></c:val>
      </c:ser></c:pieChart></c:plotArea></c:chart>
    </c:chartSpace>
    """
    parts = {
        "word/document.xml": document,
        "word/charts/chart1.xml": chart,
        "word/media/image1.png": "synthetic-image-resource",
        "word/header1.xml": (
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:r><w:t>Header text</w:t></w:r></w:p></w:hdr>'
        ),
        "word/styles.xml": (
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/>'
            '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style></w:styles>'
        ),
        "word/numbering.xml": (
            '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
            '<w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
            '</w:lvl></w:abstractNum><w:num w:numId="1">'
            '<w:abstractNumId w:val="0"/></w:num></w:numbering>'
        ),
        "docProps/core.xml": (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">'
            '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Sample</dc:title>'
            '</cp:coreProperties>'
        ),
    }
    relationships = {
        ("word/document.xml", "rIdLink"): {
            "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            "target": "https://example.test/report", "external": True,
        },
        ("word/document.xml", "rIdImage"): {
            "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
            "target": "word/media/image1.png", "external": False,
        },
        ("word/document.xml", "rIdChart"): {
            "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart",
            "target": "word/charts/chart1.xml", "external": False,
        },
        ("word/charts/chart1.xml", "rIdWorkbook"): {
            "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject",
            "target": "https://example.test/data.xlsx", "external": True,
        },
    }
    return MemoryPackage(parts, relationships)


class TlReadTests(unittest.TestCase):
    def test_report_covers_stories_revisions_tables_and_inline_objects(self):
        package = report_fixture()
        records, issues, coverage = report(package)
        paragraphs = [item for item in records if item["type"] == "paragraph"]
        heading = next(item for item in paragraphs if item["text"] == "OverviewReference1")
        self.assertEqual(heading["style_name"], "Heading 1")
        self.assertEqual(heading["heading_level"], 1)
        self.assertEqual(heading["numbering"]["text"], "%1.")
        self.assertEqual(heading["numbering"]["label"], "1.")
        self.assertEqual(heading["deleted_text"], "Removed")
        self.assertEqual(heading["revisions"][0]["kind"], "deleted")
        self.assertEqual(heading["fields"][0]["instruction"], " PAGE ")
        self.assertEqual([item["text"] for item in records if item["type"] == "math"], ["x+y"])
        unnumbered = next(item for item in paragraphs if item["text"] == "Unnumbered")
        self.assertTrue(unnumbered["numbering"]["suppressed"])

        row_records = [item for item in records if item["type"] == "table_row"]
        self.assertEqual(len(row_records), 2)
        continuation = row_records[1]["cells"][0]
        self.assertEqual(continuation["vmerge"], "continue")
        self.assertEqual(continuation["origin"], {"row": 1, "column": 1})
        self.assertEqual(coverage["tables"]["vertical_merge_continuations"], 1)

        textboxes = [item for item in records if item["type"] == "textbox"]
        self.assertEqual([item["text"] for item in textboxes], ["Stored box"])
        image = next(item for item in records if item["type"] == "image")
        self.assertEqual(image["description"], "Chart icon")
        self.assertEqual(image["resource_part"], "word/media/image1.png")
        self.assertEqual(image["resource_sha256"], sha256(package.read("word/media/image1.png")).hexdigest())
        hyperlink = next(item for item in records if item["type"] == "hyperlink")
        self.assertEqual(hyperlink["target"], "https://example.test/report")

        series = next(item for item in records if item["type"] == "chart_series")
        self.assertEqual(series["value_formula"], "Sheet1!$B$1:$B$2")
        self.assertEqual([point["value"] for point in series["points"]], ["10.00", "20.0"])
        self.assertEqual(coverage["charts"]["cached_numeric_values"], 2)
        self.assertEqual(coverage["images"]["unique_internal_targets"], 1)
        self.assertTrue(any(issue["code"] == "external_workbook_not_followed" for issue in issues))
        self.assertFalse(any(issue["code"] == "relationship_type_unexpected" for issue in issues))
        self.assertFalse(any(
            issue["code"] == "numbering_definition_unavailable"
            and issue.get("num_id") == "0"
            for issue in issues
        ))

    def test_report_emits_record_counts_and_core_metadata(self):
        records, _, coverage = report(report_fixture())
        counts = coverage["records_by_type"]
        self.assertEqual(counts["story"], 2)
        self.assertEqual(counts["table"], 1)
        self.assertEqual(counts["table_row"], 2)
        metadata = next(item for item in records if item["type"] == "metadata")
        self.assertEqual(metadata["values"]["title"], "Sample")
        self.assertEqual(coverage["availability"]["headers"], "available")


if __name__ == "__main__":
    unittest.main()
