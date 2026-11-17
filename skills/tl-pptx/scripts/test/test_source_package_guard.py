"""Restored original chart parts are checked before any upstream XML parse."""
import base64
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_to_pptx.native_objects.payload_limits import validate_payload_limits


def blob(raw):
    """Generate the upstream checksum-protected transport."""
    return {'encoding': 'base64', 'payload': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest()}


def fixture(raw=b'<chart/>', name='ppt/charts/chart1.xml'):
    """Minimal transport for preflight, not a claim of valid final chart."""
    return {'source_package': {'chart_part': name, 'frame': blob(b'<frame/>'),
                               'parts': [{'name': name, **blob(raw)}]}}


class SourcePackageGuardTests(unittest.TestCase):
    """Reject payload smuggling without blanket rejection of safe transport."""

    def test_bounded_source_transport_accepted(self):
        validate_payload_limits(fixture())

    def test_xml_dtd_and_active_objects_rejected(self):
        for raw in (b'<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>',
                    '<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>'.encode('utf-16'),
                    b'<oleObj/>', b'<shape action="ppaction://macro"/>'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, RuntimeError)):
                validate_payload_limits(fixture(raw))

    def test_path_and_checksum_rejected(self):
        for name in ('ppt/charts/../../evil.xml', 'ppt/charts/%2fsecret.xml', '/ppt/charts/chart.xml'):
            with self.subTest(name=name), self.assertRaises((ValueError, RuntimeError)):
                validate_payload_limits(fixture(name=name))
        value = fixture()
        value['source_package']['parts'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            validate_payload_limits(value)

    def test_dangling_relationship_rejected(self):
        raw = (b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               b'<Relationship Id="rId1" Type="package" Target="../embeddings/missing.xlsx"/></Relationships>')
        with self.assertRaisesRegex(ValueError, 'Dangling'):
            validate_payload_limits(fixture(raw, 'ppt/charts/_rels/chart1.xml.rels'))


if __name__ == '__main__':
    unittest.main()
