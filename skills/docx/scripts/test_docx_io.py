"""Small, synthetic ZIP/XML contract regressions using only the standard library.

These fixtures exercise resource and parsing boundaries; they are not evidence
of Office rendering or complete ZIP/XML format compatibility.
"""
from pathlib import Path
import io
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
from xml.dom import Node
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest("DOCX IO contracts require Python >=3.10")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from office import archiveio, xmlio
from office.helpers import read_part


def zip_bytes(entries, compression=zipfile.ZIP_STORED):
    """Build tiny, deliberately synthetic archives without installing a writer."""
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
    return output.getvalue()


def zip64_end_records():
    """A real ZIP64 EOCD/locator with one tiny, ordinary stored member."""
    ordinary = zip_bytes([("word/document.xml", b"<document>tiny</document>")])
    end = len(ordinary) - 22
    fields = struct.unpack_from("<4s4H2LH", ordinary, end)
    central_size, central_offset = fields[5:7]
    zip64_end = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0,
        1, 1, central_size, central_offset,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    legacy_end = struct.pack(
        "<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF,
        0xFFFFFFFF, 0xFFFFFFFF, 0,
    )
    return ordinary[:end] + zip64_end + locator + legacy_end


class ArchiveContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="docx-io-", dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic.docx"

    def open_payload(self, payload):
        self.path.write_bytes(payload)
        return archiveio.open_archive(self.path)

    def test_stored_and_deflated_members_roundtrip(self):
        contents = {"word/document.xml": "<p>中文 &amp; text</p>".encode("utf-8"),
                    "media/opaque.bin": bytes(range(16))}
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                payload = zip_bytes(contents.items(), compression)
                with self.open_payload(payload) as archive:
                    self.assertEqual(set(archive.namelist()), set(contents))
                    for name, expected in contents.items():
                        self.assertEqual(read_part(archive, name), expected)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_small_valid_zip64_end_records_are_explicitly_unsupported(self):
        payload = zip64_end_records()
        self.assertLess(len(payload), 1024)
        # Establish that the fixture is a readable ZIP64 file independently of
        # the guarded reader, whose declared policy excludes ZIP64.
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("word/document.xml"), b"<document>tiny</document>")
        with self.assertRaisesRegex(ValueError, "ZIP64"):
            with self.open_payload(payload):
                self.fail("ZIP64 is outside the documented archive subset")

    def test_small_force_zip64_local_header_is_unsupported(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            with archive.open("tiny.xml", "w", force_zip64=True) as stream:
                stream.write(b"<tiny/>")
        payload = output.getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("tiny.xml"), b"<tiny/>")
        with self.assertRaisesRegex(ValueError, "ZIP64"):
            with self.open_payload(payload):
                self.fail("ZIP64 extraction versions must not bypass preflight")

    def test_duplicate_paths_and_file_directory_conflicts_are_rejected(self):
        fixtures = [
            [("same.xml", b"A"), ("same.xml", b"B")],
            [("word", b"file"), ("word/document.xml", b"child")],
            [("word/document.xml", b"child"), ("word", b"file")],
            [("word/", b""), ("word", b"file")],
        ]
        for entries in fixtures:
            with self.subTest(names=[name for name, _ in entries]):
                with self.assertRaises(ValueError):
                    with self.open_payload(zip_bytes(entries)):
                        self.fail("Ambiguous member paths were accepted")

    def test_case_and_unicode_path_aliases_are_rejected(self):
        for names in (("Word/a.xml", "word/b.xml"), ("café/a.xml", "cafe\u0301/b.xml")):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    with self.open_payload(zip_bytes([(name, b"x") for name in names])):
                        self.fail("Filesystem aliases were accepted")

    def test_unsafe_paths_are_rejected_without_extracting(self):
        for name in ("../escape.xml", "/absolute.xml", "word/./document.xml",
                     "word\\document.xml", "word//document.xml", "NUL.xml", "word/trailing."):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    with self.open_payload(zip_bytes([(name, b"x")])):
                        self.fail("Unsafe path was accepted")
                self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_symlink_member_is_rejected(self):
        member = zipfile.ZipInfo("word/link.xml")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            with self.open_payload(zip_bytes([(member, b"../outside.xml")])):
                self.fail("Symlink archive member was accepted")

    def test_corrupted_member_crc_is_detected_on_bounded_read(self):
        payload = bytearray(zip_bytes([("word/document.xml", b"UNIQUE_CRC_PAYLOAD")]))
        payload[payload.index(b"UNIQUE_CRC_PAYLOAD")] ^= 1
        with self.open_payload(bytes(payload)) as archive:
            with self.assertRaises(zipfile.BadZipFile):
                read_part(archive, "word/document.xml")

    def test_prefix_and_trailing_bytes_are_rejected(self):
        payload = zip_bytes([("word/document.xml", b"<document/>")])
        for malformed in (b"PREFIX" + payload, payload + b"TRAILER"):
            with self.subTest(kind="prefix" if malformed.startswith(b"PREFIX") else "trailer"):
                with self.assertRaises(ValueError):
                    with self.open_payload(malformed):
                        self.fail("Unbounded surrounding bytes were accepted")

    def test_central_directory_count_mismatch_is_rejected(self):
        payload = bytearray(zip_bytes([("word/document.xml", b"<document/>")]))
        struct.pack_into("<HH", payload, len(payload) - 22 + 8, 2, 2)
        with self.assertRaises(ValueError):
            with self.open_payload(bytes(payload)):
                self.fail("Declared entry count mismatch was accepted")


@unittest.skipUnless(sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
                     "XML contracts require Python >=3.10 and Expat >=2.6.0")
class XmlContractTests(unittest.TestCase):
    def test_unicode_namespace_entities_and_dom_nodes(self):
        data = ('<r xmlns:x="urn:test" a="一&amp;二"><x:c><![CDATA[甲&乙]]></x:c>'
                '<!--note--><?probe data?></r>').encode("utf-8")
        root = xmlio.fromstring(data)
        self.assertEqual(root.attrib["a"], "一&二")
        self.assertEqual(root[0].tag, "{urn:test}c")
        self.assertEqual(root[0].text, "甲&乙")
        dom = xmlio.parse_dom(data)
        self.addCleanup(dom.unlink)
        self.assertEqual(dom.documentElement.getAttribute("a"), "一&二")
        self.assertEqual([node.nodeType for node in dom.documentElement.childNodes],
                         [Node.ELEMENT_NODE, Node.COMMENT_NODE, Node.PROCESSING_INSTRUCTION_NODE])
        child = dom.documentElement.firstChild
        self.assertEqual(child.namespaceURI, "urn:test")
        self.assertEqual(child.firstChild.nodeType, Node.CDATA_SECTION_NODE)
        self.assertEqual(child.firstChild.data, "甲&乙")

    def test_utf16_file_parsing_preserves_text(self):
        data = '<?xml version="1.0" encoding="UTF-16"?><r><c>中文</c></r>'.encode("utf-16")
        with tempfile.TemporaryDirectory(prefix="docx-xml-", dir=".") as directory:
            source = Path(directory) / "source.xml"
            source.write_bytes(data)
            self.assertEqual(xmlio.parse(source).getroot()[0].text, "中文")
            self.assertEqual(source.read_bytes(), data)

    def test_doctypes_and_entities_are_rejected_in_utf8_and_utf16(self):
        fixtures = (
            '<!DOCTYPE r><r/>',
            '<!DOCTYPE r [<!ENTITY value "expanded">]><r>&value;</r>',
            '<!DOCTYPE r SYSTEM "https://example.invalid/never-fetch"><r/>',
            '<!DOCTYPE r [<!ENTITY value SYSTEM "file:///never-read">]><r>&value;</r>',
        )
        for text in fixtures:
            for encoding in ("utf-8", "utf-16"):
                for reader in (xmlio.fromstring, xmlio.parse_dom):
                    with self.subTest(text=text, encoding=encoding, reader=reader.__name__):
                        with self.assertRaises(ValueError):
                            reader(text.encode(encoding))

    def test_malformed_xml_and_unbound_prefix_are_rejected(self):
        for data in (b"<r><c></r>", b"<x:r/>", b"<r a='1' a='2'/>"):
            for reader in (xmlio.fromstring, xmlio.parse_dom):
                with self.subTest(data=data, reader=reader.__name__):
                    with self.assertRaises(ValueError):
                        reader(data)

    def test_depth_boundary_uses_small_fixtures(self):
        with patch.object(xmlio, "MAX_XML_DEPTH", 3):
            for reader in (xmlio.fromstring, xmlio.parse_dom):
                with self.subTest(reader=reader.__name__):
                    reader(b"<a><b><c/></b></a>")
                    with self.assertRaises(ValueError):
                        reader(b"<a><b><c><d/></c></b></a>")

    def test_shared_element_budget_rejects_combined_parts(self):
        data = b"<r><c/></r>"
        with patch.object(xmlio, "MAX_XML_ELEMENTS", 3):
            # Each part fits independently; two retained parts do not.
            xmlio.fromstring(data)
            xmlio.parse_dom(data).unlink()
            budget = xmlio.XmlBudget()
            xmlio.fromstring(data, budget=budget)
            with self.assertRaises(ValueError):
                xmlio.parse_dom(data, budget=budget)
            xmlio.fromstring(data, budget=xmlio.XmlBudget())

    def test_shared_attribute_budget_and_per_element_boundary(self):
        with patch.object(xmlio, "MAX_XML_ATTRIBUTES", 3):
            budget = xmlio.XmlBudget()
            xmlio.fromstring(b'<r a="1" b="2"/>', budget=budget)
            with self.assertRaises(ValueError):
                xmlio.fromstring(b'<r c="3" d="4"/>', budget=budget)
        with patch.object(xmlio, "MAX_XML_ATTRIBUTES_PER_ELEMENT", 2):
            xmlio.fromstring(b'<r a="1" b="2"/>')
            with self.assertRaises(ValueError):
                xmlio.fromstring(b'<r a="1" b="2" c="3"/>')

    def test_namespace_uri_and_shared_expanded_name_budgets(self):
        with patch.object(xmlio, "MAX_XML_NAMESPACE_URI_CHARS", 8):
            self.assertEqual(xmlio.fromstring(b'<x:r xmlns:x="urn:tiny"/>').tag, "{urn:tiny}r")
            with self.assertRaises(ValueError):
                xmlio.fromstring(b'<x:r xmlns:x="urn:large"/>')
        with patch.object(xmlio, "MAX_XML_EXPANDED_NAME_CHARS", 25):
            data = b'<x:r xmlns:x="urn:a"/>'
            budget = xmlio.XmlBudget()
            xmlio.fromstring(data, budget=budget)
            with self.assertRaises(ValueError):
                xmlio.fromstring(data, budget=budget)
            xmlio.fromstring(data, budget=xmlio.XmlBudget())

    def test_text_comment_cdata_and_processing_instruction_node_budget(self):
        data = b"<r><!--a--><?p a?><![CDATA[x]]>y</r>"
        with patch.object(xmlio, "MAX_XML_NODES", 5):
            xmlio.parse_dom(data).unlink()
            with self.assertRaises(ValueError):
                xmlio.parse_dom(data.replace(b"</r>", b"<!--extra--></r>"))
            budget = xmlio.XmlBudget()
            xmlio.fromstring(b"<r><!--one--></r>", budget=budget)
            xmlio.parse_dom(b"<r><!--two--></r>", budget=budget).unlink()
            with self.assertRaises(ValueError):
                xmlio.fromstring(b"<r><!--three--></r>", budget=budget)

    def test_byte_budget_and_old_expat_stop_before_tree_construction(self):
        with patch.object(xmlio, "MAX_XML_BYTES", 16):
            self.assertEqual(xmlio.fromstring(b"<r>ok</r>").text, "ok")
            with self.assertRaises(ValueError):
                xmlio.fromstring(b"<r>" + b"x" * 17 + b"</r>")
        with patch.object(xmlio.expat, "version_info", (2, 5, 0)):
            with self.assertRaises(ValueError):
                xmlio.fromstring(b"<r/>")


if __name__ == "__main__":
    unittest.main()
