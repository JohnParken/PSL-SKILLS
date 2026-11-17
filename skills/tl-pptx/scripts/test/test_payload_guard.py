"""Security regressions for inline native XML hidden inside SVG transport."""
import base64
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import tl_payload_guard as guard


def encoded(raw):
    """Return canonical transport encoding for one test XML document."""
    return base64.b64encode(raw.encode('utf-8')).decode('ascii')


def svg(attribute=None, payload=None, part=None):
    """Create a minimal SVG with an attribute or metadata payload."""
    root = etree.Element(f'{{{guard.SVG_NS}}}svg')
    if attribute:
        root.set(attribute, encoded(payload))
    if part:
        item = etree.SubElement(root, f'{{{guard.SVG_NS}}}metadata')
        item.set('data-pptx-part', part)
        item.set('data-pptx-encoding', 'base64')
        item.text = encoded(payload)
    return root


class PayloadGuardTests(unittest.TestCase):
    """Check hidden parser boundaries without executing malicious payloads."""

    def test_all_seven_legitimate_channels(self):
        """The safety guard retains every supported inline XML channel."""
        for attribute, tags in guard.ATTRIBUTE_ROOTS.items():
            tag = sorted(tags)[0]
            raw = etree.tostring(etree.Element(tag), encoding='unicode')
            with self.subTest(attribute=attribute):
                self.assertEqual(guard.validate_native_payloads(svg(attribute, raw))['payloads'], 1)
        for part, tags in guard.METADATA_ROOTS.items():
            raw = etree.tostring(etree.Element(sorted(tags)[0]), encoding='unicode')
            with self.subTest(part=part):
                self.assertEqual(guard.validate_native_payloads(svg(payload=raw, part=part))['payloads'], 1)

    def test_internal_entity_in_base64_rejected(self):
        """Safe outer XML must not disguise a DTD in its native fragment."""
        raw = ('<!DOCTYPE style [<!ENTITY x "hidden">]>'
               f'<p:style xmlns:p="{guard.PRESENTATION_NS}">&x;</p:style>')
        with self.assertRaisesRegex(guard.NativePayloadError, 'DTD'):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw))

    def test_external_entity_rejected_without_access(self):
        """No native payload may declare a file-backed external entity."""
        raw = ('<!DOCTYPE style [<!ENTITY x SYSTEM "file:///nonexistent-sentinel">]>'
               f'<p:style xmlns:p="{guard.PRESENTATION_NS}">&x;</p:style>')
        with self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw))

    def test_relationship_rejected(self):
        """A hash-valid native fragment still cannot reference external parts."""
        raw = (f'<p:style xmlns:p="{guard.PRESENTATION_NS}" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:link="rId2"/>')
        with self.assertRaisesRegex(guard.NativePayloadError, 'relationships'):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw))

    def test_depth_and_cumulative_nodes(self):
        """Decoded fragments share a budget instead of resetting per payload."""
        raw = f'<p:style xmlns:p="{guard.PRESENTATION_NS}"><p:x><p:y/></p:x></p:style>'
        with patch.object(guard, 'MAX_XML_DEPTH', 2), self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw))
        budget = [0, 0]
        with patch.object(guard, 'MAX_XML_NODES', 5):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw), budget)
            with self.assertRaises(guard.NativePayloadError):
                guard.validate_native_payloads(svg('data-pptx-shape-style', raw), budget)

    def test_cumulative_bytes(self):
        """The caller's image/source budget remains part of the same ceiling."""
        raw = f'<p:style xmlns:p="{guard.PRESENTATION_NS}"/>'
        with self.assertRaisesRegex(guard.NativePayloadError, 'cumulative'):
            guard.validate_native_payloads(svg('data-pptx-shape-style', raw), [guard.MAX_TOTAL_BYTES])

    def test_wrong_root_and_invalid_encoding(self):
        """Do not reinterpret malformed transport as an absent optional payload."""
        with self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(svg('data-pptx-shape-style', '<wrong/>'))
        root = svg('data-pptx-shape-style', '<wrong/>')
        root.set('data-pptx-shape-style', 'not base64!')
        with self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(root)

    def test_unnamespaced_metadata_is_still_checked(self):
        """Upstream accepts unnamespaced metadata; it must not bypass preflight."""
        root = etree.Element(f'{{{guard.SVG_NS}}}svg')
        item = etree.SubElement(root, 'metadata')
        item.set('data-pptx-part', 'txbody')
        item.set('data-pptx-encoding', 'base64')
        item.text = encoded('<!DOCTYPE x><x/>')
        with self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(root)

    def test_utf16_fragment_rejected(self):
        """Raw UTF-16 cannot be reinserted as a UTF-8 decoded XML string."""
        root = svg('data-pptx-shape-style', '<wrong/>')
        raw = f'<p:style xmlns:p="{guard.PRESENTATION_NS}"/>'.encode('utf-16')
        root.set('data-pptx-shape-style', base64.b64encode(raw).decode('ascii'))
        with self.assertRaises(guard.NativePayloadError):
            guard.validate_native_payloads(root)

    def test_processing_instruction_and_action(self):
        """Opaque fragments cannot smuggle active action nodes or stylesheet PIs."""
        for body in ('<?xml-stylesheet href="https://example.invalid/x"?>', '<p:hlinkClick action="ppaction://program"/>'):
            raw = f'<p:style xmlns:p="{guard.PRESENTATION_NS}">{body}</p:style>'
            with self.subTest(body=body), self.assertRaises(guard.NativePayloadError):
                guard.validate_native_payloads(svg('data-pptx-shape-style', raw))


if __name__ == '__main__':
    unittest.main()
