"""Source-bound part roundtrip contracts and malicious-package regressions."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_package import Package
from tl_roundtrip import repack_package, unpack_package


class RoundtripTests(unittest.TestCase):
    """Keep originals, binary parts and non-target XML outside the edit surface."""

    def setUp(self):
        doc = Document()
        doc.add_paragraph('Original text')
        doc.sections[0].header.paragraphs[0].text = 'Untouched header'
        stream = BytesIO()
        doc.save(stream)
        self.original = stream.getvalue()
        self.package = Package(self.original)
        self.bundle = unpack_package(self.package)

    def main_part(self, bundle=None):
        """Find a part by identity, not its incidental bundle position."""
        return next(item for item in (bundle or self.bundle)['parts'] if item['member'] == self.package.main)

    def test_unchanged_is_identical_zip(self):
        """Return the original archive for an unchanged bundle."""
        result, receipt = repack_package(self.package, self.bundle)
        self.assertEqual(self.original, result)
        self.assertTrue(receipt['unchanged_archive'])

    def test_changed_text_preserves_other_payloads(self):
        """Retain every non-target part and the original input."""
        item = self.main_part()
        item['content'] = item['content'].replace('Original text', 'Edited text')
        result, receipt = repack_package(self.package, self.bundle)
        self.assertEqual(['word/document.xml'], receipt['changed_parts'])
        self.assertEqual('Edited text', Document(BytesIO(result)).paragraphs[0].text)
        after = Package(result)
        for name in self.package.names - {self.package.main}:
            self.assertEqual(self.package.read(name), after.read(name))
        self.assertEqual(self.original, self.package.data)

    def test_source_and_digest_guards(self):
        """Reject stale source and member identities."""
        for field in ('source_sha256', 'extension'):
            bundle = deepcopy(self.bundle)
            bundle[field] = 'wrong'
            with self.assertRaises(ValueError):
                repack_package(self.package, bundle)
        self.main_part()['sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            repack_package(self.package, self.bundle)

    def test_missing_duplicate_unknown_parts(self):
        """Require a complete and unambiguous source member inventory."""
        variants = []
        missing = deepcopy(self.bundle)
        missing['parts'].pop()
        variants.append(missing)
        duplicate = deepcopy(self.bundle)
        duplicate['parts'][-1] = duplicate['parts'][0]
        variants.append(duplicate)
        for name in ('../../escape', '/tmp/escape', 'new.xml'):
            unknown = deepcopy(self.bundle)
            unknown['parts'][0]['member'] = name
            variants.append(unknown)
        for bundle in variants:
            with self.subTest(bundle=bundle['parts'][0]['member']), self.assertRaises(ValueError):
                repack_package(self.package, bundle)

    def test_bad_xml_and_base64(self):
        """Reject malformed replacement payloads before publication."""
        for encoding, content in (('utf-8', '<broken'), ('base64', '!not base64'), ('unknown', 'x'), ('utf-8', '\ud800')):
            bundle = deepcopy(self.bundle)
            item = self.main_part(bundle)
            item.update(encoding=encoding, content=content)
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                repack_package(self.package, bundle)

    def test_missing_relationship_is_rejected(self):
        """Validate changed relationship targets without fetching them."""
        rel = next(item for item in self.bundle['parts'] if item['member'] == 'word/_rels/document.xml.rels')
        rel['content'] = rel['content'].replace('Target="styles.xml"', 'Target="missing.xml"')
        with self.assertRaisesRegex(ValueError, 'missing relationship'):
            repack_package(self.package, self.bundle)

    def test_signed_changes_rejected_but_exact_copy_allowed(self):
        """Do not invalidate digital signatures through edits."""
        signed = Package(self.package.overlay({'_xmlsignatures/sig.xml': b'<signature/>'}))
        bundle = unpack_package(signed)
        self.assertEqual(signed.data, repack_package(signed, bundle)[0])
        self.main_part(bundle)['content'] = self.main_part(bundle)['content'].replace('Original text', 'Edit')
        with self.assertRaisesRegex(ValueError, 'signed'):
            repack_package(signed, bundle)

    def test_dotx_and_binary_roundtrip(self):
        """Preserve the template type and arbitrary opaque binary bytes."""
        types = self.package.read('[Content_Types].xml').replace(
            b'wordprocessingml.document.main+xml', b'wordprocessingml.template.main+xml')
        template = Package(self.package.overlay({'[Content_Types].xml': types, 'custom/opaque.bin': bytes(range(256))}))
        bundle = unpack_package(template)
        result, receipt = repack_package(template, bundle)
        self.assertEqual(template.data, result)
        self.assertEqual('.dotx', Package(result).extension)
        self.assertTrue(receipt['unchanged_archive'])

    def test_unknown_fields_and_type_switch(self):
        """Reject unsupported instructions and document type changes."""
        for target in ('root', 'part'):
            bundle = deepcopy(self.bundle)
            (bundle if target == 'root' else bundle['parts'][0])['unknown'] = True
            with self.assertRaises(ValueError):
                repack_package(self.package, bundle)
        types = next(item for item in self.bundle['parts'] if item['member'] == '[Content_Types].xml')
        types['content'] = types['content'].replace('wordprocessingml.document.main+xml', 'wordprocessingml.template.main+xml')
        with self.assertRaisesRegex(ValueError, 'document type'):
            repack_package(self.package, self.bundle)

    def test_bundle_budget(self):
        """Refuse exports exceeding the public JSON stdin budget."""
        with patch('tl_roundtrip.MAX_BUNDLE_BYTES', 100), self.assertRaisesRegex(ValueError, '16 MiB'):
            unpack_package(self.package)

    def test_zip_path_and_symlink_rejected_before_unpack(self):
        """Reject malicious ZIP members without extraction."""
        for name, mode in (('../escape', stat.S_IFREG), ('link', stat.S_IFLNK)):
            stream = BytesIO(self.original)
            with ZipFile(stream, 'a') as archive:
                info = ZipInfo(name)
                info.external_attr = (mode | 0o644) << 16
                archive.writestr(info, b'target')
            with self.assertRaises(ValueError):
                unpack_package(Package(stream.getvalue()))


if __name__ == '__main__':
    unittest.main()
