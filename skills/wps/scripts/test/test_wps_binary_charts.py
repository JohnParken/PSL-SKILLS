"""Exercise both binary Word chart anchors with independent synthetic packages."""

from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZipFile

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document
from test_wps_binary_images import install_chpx
from test_wps_binary_officeart import fopt, record, sp
from test_wps_binary_png import picf, png_blip, png_bytes
from test_wps_msdoc_maximum import cfb_payload, set_pair, shape_plc


R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CHART = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
DRAWING = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PERSISTENCE = 'http://schemas.microsoft.com/office/2006/relationships/graphicFrameDoc'
FRAME = 'parts/wrapper.xml'
TARGET = 'store/values.xml'
FRAME_RELS = 'parts/_rels/wrapper.xml.rels'
CHART_RELS = 'store/_rels/values.xml.rels'
EXTERNAL_LINK = 'https://example.invalid/synthetic-workbook.xlsx'
CATEGORIES = (
    '<c:cat><c:strRef><c:f>Sheet!$A$1:$A$2</c:f><c:strCache><c:ptCount val="2"/>'
    '<c:pt idx="0"><c:v>北区</c:v></c:pt><c:pt idx="1"><c:v>南区</c:v></c:pt>'
    '</c:strCache></c:strRef></c:cat>'
)
NUMBERS = (
    '<c:numCache><c:formatCode>0.000</c:formatCode><c:ptCount val="2"/>'
    '<c:pt idx="1"><c:v>2.50E-2</c:v></c:pt><c:pt idx="0"><c:v>9.1250</c:v></c:pt></c:numCache>'
)


def _relationships(*items):
    return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(items) + '</Relationships>'


def _chart_xml(values):
    values = '<c:numRef><c:f>Sheet!$B$1:$B$2</c:f>' + values + '</c:numRef>'
    return (
        f'<c:chartSpace xmlns:c="{CHART}" xmlns:a="{DRAWING}" xmlns:r="{R}"><c:chart>'
        '<c:title><c:tx><c:rich><a:p><a:r><a:t>Synthetic chart</a:t></a:r></a:p></c:rich></c:tx></c:title>'
        '<c:plotArea><c:pieChart><c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>Requests</c:v></c:tx>'
        f'{CATEGORIES}<c:val>{values}</c:val></c:ser></c:pieChart></c:plotArea>'
        '</c:chart><c:externalData r:id="book"/></c:chartSpace>'
    )


def chart_package(*, values=NUMBERS, target='../store/values.xml', mode='Internal', replace_parts=None):
    """Use arbitrary part names to require relationship traversal and typing."""
    frame_xml = (
        '<wp:e2oholder xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'xmlns:a="{DRAWING}" xmlns:c="{CHART}" xmlns:r="{R}"><a:graphic>'
        f'<a:graphicData uri="{CHART}"><c:chart r:id="chart"/></a:graphicData></a:graphic></wp:e2oholder>'
    )
    types_xml = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Override PartName="/{FRAME}" ContentType="application/vnd.ms-office.DrsE2oDoc+xml"/>'
        f'<Override PartName="/{TARGET}" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        '</Types>'
    )
    parts = {
        '[Content_Types].xml': types_xml,
        '_rels/.rels': _relationships(f'<Relationship Id="frame" Type="{PERSISTENCE}" Target="{FRAME}"/>'),
        FRAME: frame_xml,
        FRAME_RELS: _relationships(f'<Relationship Id="chart" Type="{R}/chart" Target="{target}" TargetMode="{mode}"/>'),
        TARGET: _chart_xml(values),
        CHART_RELS: _relationships(f'<Relationship Id="book" Type="{R}/oleObject" Target="{EXTERNAL_LINK}" TargetMode="External"/>'),
    }
    parts.update(replace_parts or {})
    handle = BytesIO()
    with ZipFile(handle, 'w', ZIP_DEFLATED) as package:
        for name, content in parts.items():
            package.writestr(name, content)
    return handle.getvalue()


def _metro(package, *, malformed=False, duplicate=False):
    options = fopt(((0x8380, 'test\x00'.encode('utf-16le')), (0xC3A9, package),
                    (0x03BF, 0)), tail_override=b'x' if malformed else None)
    payload = options[8:]
    tertiary = record(0xF122, 3, 3, payload)
    return (tertiary, tertiary) if duplicate else (tertiary,)


def chart_fixture(*, floating=None, inline=None, floating_options=None,
                  inline_image=True, blocked_anchor=False, data_property=False):
    if floating_options is None and floating is not None:
        floating_options = _metro(floating)
    marker = b'\x13\x08\x14value\x15\r' if blocked_anchor else b'\x08\r'
    contents = marker if floating_options else b''
    inline_cp = len(contents)
    if inline is not None:
        contents += b'\x01\r'
    contents += b'end\r'
    streams = document([(contents, True)])
    if floating_options:
        set_pair(streams, 40, shape_plc((1 if blocked_anchor else 0,), (700,)))
        group = record(0xF003, 15, 0, sp(700, 1) + sp(701, 0x802, 75, options=floating_options))
        drawing = record(0xF002, 15, 0, record(0xF003, 15, 0, sp(699, 5) + group))
        set_pair(streams, 50, record(0xF000, 15, 0) + b'\0' + drawing)
    if inline is not None:
        picture_location = b'\x03\x6a\x00\x00\x00\x00\x55\x08\x01'
        if data_property:
            picture_location += b'\x06\x08\x01'
        prefix = [(4096 + inline_cp, b'')] if inline_cp else []
        install_chpx(streams, [*prefix, (4097 + inline_cp, picture_location), (4096 + len(contents), b'')])
        picture = sp(801, 0xA00, 75, options=_metro(inline))
        if inline_image:
            picture += png_blip(png_bytes())
        streams[('Data',)] = picf(picture)
    return cfb_payload(streams)


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'embedded DrawingML requires Expat >=2.6')
class BinaryChartsTests(unittest.TestCase):
    def test_both_anchor_paths_keep_exact_values_and_missing_channels(self):
        fixture = chart_fixture(floating=chart_package(), inline=chart_package(values=''))
        standard = read_bytes(fixture, reading_mode='standard')
        self.assertNotIn('binary_charts', standard)
        maximum = read_bytes(fixture, reading_mode='maximum')
        self.assertEqual(maximum['text'], standard['text'])
        self.assertEqual(maximum['status'], 'partial')
        self.assertEqual(maximum['binary_images']['image_count'], 1)
        report = maximum['binary_charts']
        self.assertEqual((report['status'], report['chart_count'], report['unresolved_package_count']), ('partial', 2, 0))
        floating, inline = report['charts']
        self.assertEqual((floating['source']['stream'], inline['source']['stream']), ('0Table', 'Data'))
        self.assertEqual(floating['references'], [{'kind': 'floating', 'story': 'main', 'anchor_cp': 0,
                                                   'shape_id': 701, 'shape_path': [700, 701]}])
        inline_ref, = inline['references']
        self.assertEqual((inline_ref['kind'], inline_ref['anchor_cp'], inline_ref['shape_id']), ('inline', 2, 801))
        self.assertEqual(inline_ref['character_properties_source']['stream'], 'WordDocument')
        self.assertEqual(inline_ref['picf_source']['offset'], 0)
        self.assertEqual(floating['title'], 'Synthetic chart')
        self.assertEqual((floating['graphic_frame_part'], floating['chart_part']), (FRAME, TARGET))
        self.assertEqual(floating['workbook_status'], 'external_not_followed')
        series, = floating['series']
        self.assertEqual(series['name'], 'Requests')
        self.assertEqual(series['categories']['points'], [{'index': 0, 'value': '北区'}, {'index': 1, 'value': '南区'}])
        self.assertEqual(series['values']['points'], [{'index': 0, 'value': '9.1250'}, {'index': 1, 'value': '2.50E-2'}])
        missing = inline['series'][0]['values']
        self.assertEqual((missing['status'], missing['points'], missing['missing_ranges']), ('unavailable', [], None))
        self.assertIn('cache_missing', missing['issues'])
        self.assertNotIn(EXTERNAL_LINK, json.dumps(maximum))
        self.assertNotIn('Sheet!', json.dumps(maximum))
        coverage = [item for item in maximum['reading_assessment']['coverage'] if item['category'] == 'binary_charts']
        self.assertEqual(coverage[0]['state'], 'partially_extracted')

    def test_inline_cache_is_independent_of_preview_and_requires_picture_chpx(self):
        package = chart_package()
        fixture = chart_fixture(inline=package, inline_image=False)
        maximum = read_bytes(fixture, reading_mode='maximum')
        self.assertEqual(maximum['binary_images']['image_count'], 0)
        self.assertEqual(maximum['binary_charts']['chart_count'], 1)
        fixture = chart_fixture(inline=package, data_property=True)
        self.assertNotIn('binary_charts', read_bytes(fixture, reading_mode='maximum'))
        fixture = chart_fixture(floating=package, blocked_anchor=True)
        maximum = read_bytes(fixture, reading_mode='maximum')
        self.assertEqual(maximum['text'], 'value\nend\n')
        self.assertNotIn('binary_charts', maximum)

    def test_untrusted_packages_do_not_create_chart_data_or_block_other_valid_objects(self):
        valid = chart_package()
        fake = f'<c:chartSpace xmlns:c="{CHART}"/>'
        dtd = '<!DOCTYPE test [<!ENTITY private "words">]>' + _chart_xml(NUMBERS)
        invalid_packages = (
            b'not a ZIP',
            chart_package(mode='External', target=EXTERNAL_LINK),
            chart_package(target='../../escape.xml'),
            chart_package(target='//example.invalid/values.xml'),
            chart_package(target='//store/values.xml'),
            chart_package(replace_parts={TARGET: fake}),
            chart_package(replace_parts={TARGET: dtd}),
            chart_package(replace_parts={'../outside.xml': '<x/>'}),
            chart_package(replace_parts={FRAME_RELS: _relationships(
                f'<Relationship Id="chart" Type="{R}/chart" Target="../{TARGET}"/>',
                f'<Relationship Id="chart" Type="{R}/chart" Target="../{TARGET}"/>',
            )}),
        )
        for package in invalid_packages:
            with self.subTest(index=invalid_packages.index(package)):
                result = read_bytes(chart_fixture(floating=package, inline=valid), reading_mode='maximum')
                charts = result['binary_charts']
                self.assertEqual((charts['chart_count'], charts['unresolved_package_count']), (1, 1))
                self.assertEqual(charts['charts'][0]['status'], 'not_decoded')
                self.assertEqual(charts['charts'][1]['status'], 'stored_data')
                self.assertNotIn(EXTERNAL_LINK, json.dumps(result))

    def test_opaque_non_chart_package_is_not_misattributed_to_an_anchor(self):
        package = chart_package(replace_parts={'_rels/.rels': _relationships()})
        result = read_bytes(chart_fixture(floating=package), reading_mode='maximum')
        self.assertNotIn('binary_charts', result)
        self.assertEqual(result['drawing_objects']['anchor_count'], 1)

    def test_unresolved_workbook_relationship_keeps_saved_chart_data(self):
        package = chart_package(replace_parts={CHART_RELS: '<broken'})
        result = read_bytes(chart_fixture(floating=package), reading_mode='maximum')
        chart, = result['binary_charts']['charts']
        self.assertEqual((chart['status'], chart['workbook_status']), ('partial', 'unresolved'))
        self.assertIn('workbook_relationship_unresolved', chart['issues'])
        self.assertEqual(chart['series'][0]['values']['points'][0]['value'], '9.1250')

    def test_complex_property_bounds_duplicates_and_shared_limits(self):
        package = chart_package()
        for options in (_metro(package, malformed=True), _metro(package, duplicate=True)):
            result = read_bytes(chart_fixture(floating_options=options), reading_mode='maximum')
            report = result['binary_charts']
            self.assertEqual((report['chart_count'], report['unresolved_package_count']), (0, 1))
            self.assertEqual(report['charts'][0]['reason'], 'invalid_metro_blob_property')
        fixture = chart_fixture(floating=package, inline=package)
        with patch('wps_binary_charts.MAX_TOTAL_BLOB_BYTES', len(package)), self.assertRaises(ParseError) as failure:
            read_bytes(fixture, reading_mode='maximum')
        self.assertEqual(failure.exception.status, 'resource_limit')

    def test_overlapping_inline_picture_cannot_supply_chart_data(self):
        package = chart_package()
        # A second pointer lands on a syntactically valid PICF inside an opaque
        # child of the outer shape. The two ranges cannot have separate ownership.
        inner = picf(sp(901, 0xA00, 75, options=_metro(package)) + png_blip(png_bytes()))
        container = sp(900, 0xA00, 75, extras=(record(0xF011, 0, 0, inner),))
        outer = picf(container)
        inner_offset = outer.index(inner)
        streams = document([(b'\x01\x01\r', True)])
        install_chpx(streams, [(4097, b'\x03\x6a' + struct.pack('<I', 0) + b'\x55\x08\x01'),
                               (4098, b'\x03\x6a' + struct.pack('<I', inner_offset) + b'\x55\x08\x01'), (4099, b'')])
        streams[('Data',)] = outer
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertNotIn('binary_charts', result)
        self.assertEqual(result['binary_images']['image_count'], 0)
        self.assertIn('binary_image_records_overlap', [item['code'] for item in result['issues']])

    def test_missing_expat_keeps_text_and_reports_chart_unavailability(self):
        fixture = chart_fixture(floating=chart_package())
        with patch('wps_binary_charts.expat.version_info', (2, 5, 0)):
            result = read_bytes(fixture, reading_mode='maximum')
        self.assertEqual((result['status'], result['text']), ('partial', '\ufffc\nend\n'))
        self.assertEqual(result['binary_charts']['charts'][0]['reason'], 'expat_unavailable')

    def test_cli_does_not_change_source_or_create_task_files(self):
        fixture = chart_fixture(floating=chart_package())
        with TemporaryDirectory() as work:
            path = Path(work, 'charts.wps')
            path.write_bytes(fixture)
            response = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                                       path.name, '--reading-mode', 'maximum'], cwd=work, capture_output=True, check=False, timeout=10)
            self.assertEqual(response.returncode, 3)
            self.assertEqual(json.loads(response.stdout)['binary_charts']['chart_count'], 1)
            self.assertEqual(path.read_bytes(), fixture)
            self.assertEqual(list(Path(work).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
