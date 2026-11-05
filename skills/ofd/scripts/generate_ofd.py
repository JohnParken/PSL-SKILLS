"""Generate a deliberately small fixed-layout OFD profile from explicit JSON; no editing."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import struct
import sys
import uuid
import zipfile
from xml.etree import ElementTree as ET


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSETS = sibling('ofd_generation_assets')
READER = sibling('read_ofd')
GenerationError = ASSETS.GenerationError
require = ASSETS.require
number = ASSETS.number
NS = 'http://www.ofdspec.org/2016'
MAX_OBJECTS = 1500
MAX_CHARACTERS = 4000
ZHUQUE_REFERENCE = 'zhuque-fangsong-v0.212'
ZHUQUE_FILES = {
    'ZhuqueFangsong-Regular.ttf': (ASSETS.MAX_FONT_BYTES, '558c62730844fe54ba220146ed62f859d4e2880188d92d985f8921c6e3743bc4'),
    'LICENSE.txt': (65536, '66cc01ad4df62fca6936431a426104cb32a52ce8da49de9a7d643e02d7659043'),
    'LICENSE_Alegreya.txt': (65536, 'f6f60d5d4cf4f4b1fc4e41353c897a2f5a16e6396c0cd8fa8bdfd2f4586a9a68'),
    'NOTICE.txt': (8192, '40ff310b33f4f52ad32c07b9b30161c090e85a6e1a71f1363c83ebb1d8b10ccc'),
}
ET.register_namespace('ofd', NS)


def element(name, parent=None, text=None, **attributes):
    node = ET.Element('{' + NS + '}' + name, {key: str(value) for key, value in attributes.items()})
    if parent is not None:
        parent.append(node)
    node.text = text
    return node


def serialized(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def numeric(value):
    return format(value, '.8f').rstrip('0').rstrip('.') or '0'


def values(numbers):
    return ' '.join(numeric(value) for value in numbers)


def fields(value, allowed, required=()):
    require(isinstance(value, dict) and set(value) <= set(allowed) and set(required) <= set(value),
            'missing/unknown object fields; expected: ' + ','.join(sorted(allowed)))


def xml_string(value, limit):
    require(isinstance(value, str) and len(value) <= limit and all(
        char in '\t\n\r' or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF for char in value), 'invalid/oversized XML string')
    return value


def read_asset(path, limit):
    require(isinstance(path, str) and bool(path), 'asset path must be an explicit relative string')
    data = READER.read_input(Path(path), task_root='.')
    require(len(data) <= limit, 'asset size limit')
    return data


def bundled_font(reference):
    """Read one reviewed immutable font bundle; never reinterpret user paths as skill paths."""
    require(reference == ZHUQUE_REFERENCE, 'unknown bundled font reference')
    root = Path(__file__).resolve().parent.parent
    prefix = Path('assets/fonts/zhuque_fangsong')
    assets = {}
    for filename, (limit, digest) in ZHUQUE_FILES.items():
        try:
            data = READER.read_input(prefix / filename, task_root=root)
        except (OSError, READER.ReadError) as exc:
            raise GenerationError('bundled font asset missing/inaccessible: ' + filename) from exc
        require(len(data) <= limit and hashlib.sha256(data).hexdigest() == digest,
                'bundled font asset integrity check failed: ' + filename)
        assets[filename] = data
    # Preserve both complete upstream licenses; never rewrite the font binary.
    license_data = assets['LICENSE.txt'] + b'\n\n----- Upstream Alegreya copyright and license -----\n\n' + assets['LICENSE_Alegreya.txt']
    source = dict(kind='bundled', reference=reference, version='0.212', prerelease=True,
                  release_url='https://github.com/TrionesType/zhuque/releases/tag/v0.212',
                  author_preview_notice='Note that this version is not production-ready.',
                  notice_text=assets['NOTICE.txt'].decode('utf-8'),
                  checked_assets={name: digest for name, (_limit, digest) in ZHUQUE_FILES.items()},
                  coverage='Requested text checked individually; no all-symbol or all-Han guarantee')
    return assets['ZhuqueFangsong-Regular.ttf'], license_data, source


def private_font(name):
    """Read one explicitly named user font from this installation's private fonts directory."""
    require(isinstance(name, str) and 0 < len(name) <= 128
            and name not in ('.', '..') and '/' not in name and '\\' not in name
            and all(char.isascii() and (char.isalnum() or char in '_.- ') for char in name)
            and name.lower().endswith('.ttf'), 'skill_font must be one ASCII .ttf filename, not a path')
    root = Path(__file__).resolve().parent.parent
    data = READER.read_input(Path('assets/user_fonts') / name, task_root=root)
    require(len(data) <= ASSETS.MAX_FONT_BYTES, 'font size limit')
    return data


def table_alignment(item, columns):
    horizontal = item.get('align', 'left')
    if isinstance(horizontal, str):
        horizontal = [horizontal] * columns
    require(isinstance(horizontal, list) and len(horizontal) == columns
            and all(isinstance(value, str) and value in ('left', 'center', 'right') for value in horizontal),
            'table align must be left/center/right or one value per column')
    vertical = item.get('valign', 'top')
    require(isinstance(vertical, str) and vertical in ('top', 'middle', 'bottom'), 'table valign must be top/middle/bottom')
    return horizontal, vertical


class Builder:
    def __init__(self, spec, loader=read_asset):
        fields(spec, ('schema_version', 'title', 'fonts', 'images', 'pages'), ('schema_version', 'pages'))
        require(spec['schema_version'] == 'ofd-generate-1', 'unsupported generation schema')
        require(isinstance(spec['pages'], list) and 1 <= len(spec['pages']) <= 32, 'page count must be 1..32')
        self.spec = spec
        self.loader = loader
        self.counter = 0
        self.characters = self.objects = self.asset_bytes = 0
        self.files = {}
        self.fonts = {}
        self.images = {}
        self.resource = element('Res', BaseLoc='Res')
        self.report = dict(schema_version='ofd-generation-result-1', status='validated_candidate',
                           profile='fixed-layout-1', ofd_version='1.0', pages=[], fonts=[], images=[], tables=[],
                           visual_validation='not_performed', independent_client_validation='not_performed',
                           diagnostics=['No shaping, kerning, bidi, wrapping, arbitrary editing or signature support',
                                        'Font metrics and declared bounds do not prove renderer or font-program correctness'])
        self.load_assets()

    def ident(self):
        self.counter += 1
        require(self.counter <= 10000, 'object/resource ID limit')
        return str(self.counter)

    def load_assets(self):
        fonts = self.spec.get('fonts', {})
        images = self.spec.get('images', {})
        require(isinstance(fonts, dict) and len(fonts) <= 4 and isinstance(images, dict) and len(images) <= 8, 'font/image count limit')
        font_group = element('Fonts', self.resource) if fonts else None
        for key, value in fonts.items():
            require(isinstance(key, str) and 0 < len(key) <= 64, 'invalid font reference name')
            require(isinstance(value, dict), 'font source must be an object')
            source = None
            if 'bundled' in value:
                fields(value, ('bundled',), ('bundled',))
                data, license_data, source = bundled_font(value['bundled'])
            elif 'skill_font' in value:
                fields(value, ('skill_font',), ('skill_font',))
                data = private_font(value['skill_font'])
                license_data = b''
            else:
                fields(value, ('path', 'license_path', 'embedding_permission'), ('path',))
                if 'embedding_permission' in value:
                    require(value['embedding_permission'] == 'confirmed', 'invalid optional embedding_permission')
                data = self.loader(value['path'], ASSETS.MAX_FONT_BYTES)
                license_data = self.loader(value['license_path'], 64 * 1024) if 'license_path' in value else b''
            font = ASSETS.TrueType(data)
            try:
                license_text = xml_string(license_data.decode('utf-8'), 65536)
            except UnicodeError as exc:
                raise GenerationError('font license must be UTF-8') from exc
            if license_data:
                require(license_text.strip(), 'provided font license text is empty')
            ident = self.ident()
            filename = 'Font_' + ident + '.ttf'
            self.files['Doc_0/Res/' + filename] = data
            node = element('Font', font_group, ID=ident, FontName=font.name)
            element('FontFile', node, filename)
            self.fonts[key] = (ident, font)
            self.report['fonts'].append(dict(ref=key, id=ident, name=font.name, sha256=font.sha256,
                                             permission=('bundled OFL-1.1 original with complete notices' if source else 'not assessed; supplying a font does not establish authorization'),
                                             fs_type=font.fs_type, embedding_mode='full_font_unmodified',
                                             license_text=license_text, license_sha256=hashlib.sha256(license_data).hexdigest() if license_data else None))
            if source is not None:
                self.report['fonts'][-1]['source'] = source
                self.report['diagnostics'].append('Bundled Zhuque v0.212 is a technical preview; requested character coverage still required')
            self.asset_bytes += len(data)
        image_group = element('MultiMedias', self.resource) if images else None
        for key, value in images.items():
            require(isinstance(key, str) and 0 < len(key) <= 64, 'invalid image reference name')
            fields(value, ('path',), ('path',))
            data = self.loader(value['path'], 8 * 1024 * 1024)
            info = ASSETS.inspect_image(data)
            ident = self.ident()
            filename = 'Image_' + ident + ('.png' if info['format'] == 'PNG' else '.jpg')
            self.files['Doc_0/Res/' + filename] = data
            node = element('MultiMedia', image_group, ID=ident, Type='Image', Format=info['format'])
            element('MediaFile', node, filename)
            self.images[key] = (ident, info)
            self.report['images'].append(dict(ref=key, id=ident, sha256=hashlib.sha256(data).hexdigest(), **info))
            self.asset_bytes += len(data)
        require(self.asset_bytes <= 24 * 1024 * 1024, 'total embedded asset limit: 24 MiB')

    def box(self, rectangle):
        x, y, width, height = rectangle
        left, top, right, bottom = self.margins
        require(width > 0 and height > 0 and x >= left - 1e-7 and y >= top - 1e-7
                and x + width <= self.width - right + 1e-7 and y + height <= self.height - bottom + 1e-7,
                'object exceeds page content bounds; no clipping or truncation allowed')
        return values(rectangle)

    def new_object(self):
        self.objects += 1
        require(self.objects <= MAX_OBJECTS, 'object count limit: 1500')
        return self.ident()

    def text(self, item):
        fields(item, ('kind', 'x', 'y', 'font', 'size_mm', 'text'), ('x', 'y', 'font', 'size_mm', 'text'))
        require(isinstance(item['font'], str) and item['font'] in self.fonts, 'unknown font reference')
        font_id, font = self.fonts[item['font']]
        size = number(item['size_mm'], 'size_mm', 0.5, 50)
        measure = font.measure(item['text'], size)
        self.characters += len(item['text'])
        require(self.characters <= MAX_CHARACTERS, 'character count limit: 4000')
        x = number(item['x'], 'text x')
        y = number(item['y'], 'text baseline y')
        ident = self.new_object()
        boundary = [x + measure['left'], y - measure['top'], measure['width'], measure['height']]
        node = element('TextObject', self.layer, ID=ident, Boundary=self.box(boundary), Font=font_id,
                       Size=numeric(size), Stroke='false', Fill='true')
        transform = element('CGTransform', node, CodePosition='0', CodeCount=len(item['text']), GlyphCount=len(measure['glyphs']))
        element('Glyphs', transform, ' '.join(map(str, measure['glyphs'])))
        raw = ''.join('\\' + format(ord(char), '04X') if char.isspace() or char == '\\' else char for char in item['text'])
        attributes = dict(X=numeric(-measure['left']), Y=numeric(measure['top']))
        if len(measure['advances']) > 1:
            attributes['DeltaX'] = values(measure['advances'][:-1])
        element('TextCode', node, raw, **attributes)
        return dict(id=ident, text=item['text'], boundary=boundary, baseline=[x, y], font=font_id,
                    glyphs=measure['glyphs'], advances=measure['advances'])

    def line(self, x1, y1, x2, y2, stroke):
        require(x1 != x2 or y1 != y2, 'zero-length line')
        radius = stroke / 2
        x = min(x1, x2) - radius
        y = min(y1, y2) - radius
        boundary = [x, y, abs(x2 - x1) + stroke, abs(y2 - y1) + stroke]
        ident = self.new_object()
        node = element('PathObject', self.layer, ID=ident, Boundary=self.box(boundary),
                       LineWidth=numeric(stroke), Stroke='true', Fill='false')
        element('AbbreviatedData', node, 'M ' + values([x1 - x, y1 - y]) + ' L ' + values([x2 - x, y2 - y]))
        return ident

    def table(self, item):
        fields(item, ('kind', 'x', 'y', 'widths', 'row_height', 'rows', 'font', 'size_mm', 'padding_mm', 'stroke_mm', 'align', 'valign'),
               ('x', 'y', 'widths', 'row_height', 'rows', 'font', 'size_mm'))
        require(isinstance(item['widths'], list) and 1 <= len(item['widths']) <= 20, 'table columns: 1..20')
        widths = [number(value, 'column width', 1, 1000) for value in item['widths']]
        horizontal, vertical = table_alignment(item, len(widths))
        rows = item['rows']
        require(isinstance(rows, list) and 1 <= len(rows) <= 50 and len(rows) * len(widths) <= 400, 'table cell limit: 400')
        require(all(isinstance(row, list) and len(row) == len(widths) for row in rows), 'ragged table rows')
        require(isinstance(item['font'], str) and item['font'] in self.fonts, 'unknown table font')
        font = self.fonts[item['font']][1]
        size = number(item['size_mm'], 'table size_mm', 0.5, 50)
        x = number(item['x'], 'table x')
        y = number(item['y'], 'table y')
        height = number(item['row_height'], 'row height', 1, 1000)
        padding = number(item.get('padding_mm', 1), 'cell padding', 0, 100)
        stroke = number(item.get('stroke_mm', 0.2), 'table stroke', 0.01, 5)
        require(padding >= stroke / 2, 'table padding must contain half the grid stroke')
        self.box([x - stroke / 2, y - stroke / 2, sum(widths) + stroke, len(rows) * height + stroke])
        grid = []
        position = x
        for width in [0] + widths:
            position += width
            grid.append(self.line(position, y, position, y + height * len(rows), stroke))
        for index in range(len(rows) + 1):
            grid.append(self.line(x, y + index * height, x + sum(widths), y + index * height, stroke))
        cells = []
        for row_index, row in enumerate(rows):
            position = x
            for column, (width, text) in enumerate(zip(widths, row)):
                ASSETS.plain_text(text)
                record = dict(row=row_index, column=column, text=text,
                              box=[position, y + row_index * height, width, height], object_ids=[],
                              alignment=dict(horizontal=horizontal[column], vertical=vertical))
                if text:
                    measure = font.measure(text, size)
                    require(measure['width'] + padding * 2 <= width + 1e-7
                            and measure['height'] + padding * 2 <= height + 1e-7,
                            'table cell overflow; no wrapping, shrinking or truncation')
                    offset_x = {'left': 0, 'center': 0.5, 'right': 1}[horizontal[column]] * (width - 2 * padding - measure['width'])
                    offset_y = {'top': 0, 'middle': 0.5, 'bottom': 1}[vertical] * (height - 2 * padding - measure['height'])
                    rendered = self.text(dict(x=position + padding + offset_x - measure['left'],
                                              y=y + row_index * height + padding + offset_y + measure['top'],
                                              font=item['font'], size_mm=size, text=text))
                    record['object_ids'].append(rendered['id'])
                cells.append(record)
                position += width
        table = dict(page=len(self.report['pages']) + 1, rows=len(rows), columns=len(widths),
                     grid_object_ids=grid, cells=cells, semantics='explicit input grid; no merged cells or editable table model')
        self.report['tables'].append(table)
        return dict(kind='table', table_index=len(self.report['tables']) - 1)

    def item(self, item):
        require(isinstance(item, dict) and isinstance(item.get('kind'), str), 'item requires kind')
        kind = item['kind']
        if kind == 'text':
            return dict(kind=kind, **self.text(item))
        if kind == 'table':
            return self.table(item)
        if kind == 'line':
            fields(item, ('kind', 'x1', 'y1', 'x2', 'y2', 'stroke_mm'), ('x1', 'y1', 'x2', 'y2'))
            coordinates = [number(item[key], key) for key in ('x1', 'y1', 'x2', 'y2')]
            ident = self.line(*coordinates, number(item.get('stroke_mm', 0.2), 'stroke_mm', 0.01, 5))
            return dict(kind=kind, id=ident)
        if kind == 'rectangle':
            fields(item, ('kind', 'x', 'y', 'width', 'height', 'stroke_mm'), ('x', 'y', 'width', 'height'))
            x, y, width, height = [number(item[key], key, 0.01 if key in ('width', 'height') else 0) for key in ('x', 'y', 'width', 'height')]
            stroke = number(item.get('stroke_mm', 0.2), 'stroke_mm', 0.01, 5)
            radius = stroke / 2
            ident = self.new_object()
            node = element('PathObject', self.layer, ID=ident,
                           Boundary=self.box([x - radius, y - radius, width + stroke, height + stroke]),
                           LineWidth=numeric(stroke), Stroke='true', Fill='false')
            element('AbbreviatedData', node, 'M ' + values([radius, radius]) + ' L ' + values([width + radius, radius])
                    + ' L ' + values([width + radius, height + radius]) + ' L ' + values([radius, height + radius]) + ' C')
            return dict(kind=kind, id=ident)
        if kind == 'image':
            fields(item, ('kind', 'ref', 'x', 'y', 'width', 'height'), ('ref', 'x', 'y', 'width', 'height'))
            require(isinstance(item['ref'], str) and item['ref'] in self.images, 'unknown image reference')
            ident, info = self.images[item['ref']]
            x, y, width, height = [number(item[key], key, 0.01 if key in ('width', 'height') else 0) for key in ('x', 'y', 'width', 'height')]
            self.box([x, y, width, height])
            scale = min(width / info['width'], height / info['height'])
            actual_width, actual_height = info['width'] * scale, info['height'] * scale
            boundary = [x + (width - actual_width) / 2, y + (height - actual_height) / 2, actual_width, actual_height]
            object_id = self.new_object()
            element('ImageObject', self.layer, ID=object_id, ResourceID=ident, Boundary=self.box(boundary),
                    CTM=values([actual_width, 0, 0, actual_height, 0, 0]))
            return dict(kind=kind, id=object_id, boundary=boundary, ref=item['ref'])
        raise GenerationError('unsupported item kind: ' + kind)

    def build(self):
        document = element('Document')
        common = element('CommonData', document)
        maximum = element('MaxUnitID', common)
        first = self.spec['pages'][0]
        fields(first, ('width', 'height', 'margins', 'items'), ('width', 'height', 'items'))
        area = element('PageArea', common)
        element('PhysicalBox', area, values([0, 0, number(first.get('width'), 'page width', 20), number(first.get('height'), 'page height', 20)]))
        if self.fonts or self.images:
            element('DocumentRes', common, 'Res.xml')
            self.files['Doc_0/Res.xml'] = serialized(self.resource)
        pages = element('Pages', document)
        for index, page in enumerate(self.spec['pages']):
            fields(page, ('width', 'height', 'margins', 'items'), ('width', 'height', 'items'))
            self.width = number(page['width'], 'page width', 20)
            self.height = number(page['height'], 'page height', 20)
            margins = page.get('margins', [10, 10, 10, 10])
            require(isinstance(margins, list) and len(margins) == 4, 'margins require left,top,right,bottom')
            self.margins = [number(value, 'margin', 0, 1000) for value in margins]
            require(sum(self.margins[::2]) < self.width and sum(self.margins[1::2]) < self.height, 'margins consume page')
            require(isinstance(page['items'], list) and len(page['items']) <= MAX_OBJECTS, 'invalid page items')
            page_id = self.ident()
            source = 'Pages/Page_' + str(index) + '.xml'
            element('Page', pages, ID=page_id, BaseLoc=source)
            content = element('Page')
            area = element('Area', content)
            element('PhysicalBox', area, values([0, 0, self.width, self.height]))
            left, top, right, bottom = self.margins
            element('ApplicationBox', area, values([left, top, self.width - left - right, self.height - top - bottom]))
            self.layer = element('Layer', element('Content', content), ID=self.ident())
            records = [self.item(item) for item in page['items']]
            self.report['pages'].append(dict(number=index + 1, source='Doc_0/' + source, items=records))
            data = serialized(content)
            require(len(data) <= 2 * 1024 * 1024, 'page XML byte limit')
            self.files['Doc_0/' + source] = data
        maximum.text = str(self.counter)
        self.files['Doc_0/Document.xml'] = serialized(document)
        ofd = element('OFD', Version='1.0', DocType='OFD')
        body = element('DocBody', ofd)
        info = element('DocInfo', body)
        doc_id = uuid.uuid4().hex.upper()
        element('DocID', info, doc_id)
        if 'title' in self.spec:
            element('Title', info, xml_string(self.spec['title'], 256))
        element('Creator', info, 'PSL-SKILLS OFD fixed-layout-1')
        if self.report['fonts']:
            custom = element('CustomDatas', info)
            for font in self.report['fonts']:
                if font['license_text']:
                    element('CustomData', custom, font['license_text'], Name='FontLicense:' + font['id'])
                else:
                    element('CustomData', custom, 'not_assessed', Name='FontRights:' + font['id'])
                if 'source' in font:
                    element('CustomData', custom, font['source']['notice_text'], Name='FontNotice:' + font['id'])
        element('DocRoot', body, 'Doc_0/Document.xml')
        self.files['OFD.xml'] = serialized(ofd)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        data = buffer.getvalue()
        require(len(data) <= 32 * 1024 * 1024, 'output byte limit: 32 MiB')
        self.report.update(doc_id=doc_id, max_unit_id=self.counter, objects=self.objects,
                           characters=self.characters, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        return data, self.report


def write_environment():
    require(sys.version_info >= (3, 12, 11), 'generation requires Python >=3.12.11')
    READER.environment()
    READER.confined_environment()
    require(os.link in os.supports_dir_fd and os.link in os.supports_follow_symlinks and os.unlink in os.supports_dir_fd
            and os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks,
            'safe generation requires descriptor-relative link/unlink/stat')


def publish_new(data, output, validator):
    """Validate a private candidate and link it atomically without replacing any name."""
    outcome = dict(output_written=False, publication_target=str(output), publication_outcome='not_attempted',
                   publication_verification='not_run', cleanup_complete=True, cleanup_issues=[])
    try:
        path = Path(output)
        require(isinstance(output, str) and not path.is_absolute() and len(path.parts) == 1
                and path.name not in ('.', '..') and path.suffix.lower() == '.ofd',
                'output must be a new .ofd filename directly in the confirmed task directory')
        directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, ValueError, TypeError) as exc:
        exc.publication = outcome.copy()
        raise
    temporary = '._ofd_candidate_' + uuid.uuid4().hex + '.ofd'
    descriptor = None
    identity = None
    outcome['cleanup_complete'] = False
    primary = None
    try:
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        identity = os.fstat(descriptor)
        with os.fdopen(descriptor, 'w+b') as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
            actual = handle.read(len(data) + 1)
            require(actual == data, 'candidate readback mismatch')
            validator(actual)
            outcome.update(output_written=None, publication_outcome='publication_uncertain')
            try:
                os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError:
                outcome.update(output_written=False, publication_outcome='not_published_existing_name')
                raise
            outcome.update(output_written=True, publication_outcome='published_unverified')
            try:
                final_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
                with os.fdopen(final_fd, 'rb') as final_stream:
                    before = os.fstat(final_stream.fileno())
                    require(stat.S_ISREG(before.st_mode) and (before.st_dev, before.st_ino) == (identity.st_dev, identity.st_ino)
                            and before.st_size == len(data), 'published identity or size changed; inspect output before retry')
                    require(final_stream.read(len(data) + 1) == data, 'published bytes differ from validated candidate; inspect output before retry')
                    after = os.fstat(final_stream.fileno())
                    named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                            and (named.st_dev, named.st_ino) == (identity.st_dev, identity.st_ino),
                            'published file changed during verification; inspect output before retry')
            except (OSError, GenerationError):
                outcome.update(publication_outcome='published_verification_failed', publication_verification='failed')
                raise
            outcome.update(publication_outcome='published_verified', publication_verification='identity_and_bytes_verified')
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_error = None
        try:
            if descriptor is not None:
                os.close(descriptor)
            if identity is not None:
                current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'candidate identity changed; cleanup incomplete')
                os.unlink(temporary, dir_fd=directory)
            outcome['cleanup_complete'] = True
        except FileNotFoundError:
            outcome['cleanup_complete'] = True
        except (OSError, GenerationError) as exc:
            cleanup_error = exc
            outcome['cleanup_issues'].append(dict(reason=str(exc), temporary=temporary))
        finally:
            os.close(directory)
        if primary is not None:
            primary.publication = outcome.copy()
        elif cleanup_error is not None:
            cleanup_error.publication = outcome.copy()
            raise cleanup_error
    return outcome


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def validate_model(data, spec, report, checker):
    audited = checker.audit(data)
    require(len(audited['pages']) == len(spec['pages']), 'generated page count differs from model')
    tables = iter(report['tables'])
    for page_index, (original, actual) in enumerate(zip(spec['pages'], audited['pages']), 1):
        expected = []
        text_by_id = {record['object_id']: record['text'] for record in actual['text']}
        objects = {record['id']: record for record in actual['objects']}
        for item in original['items']:
            if item['kind'] == 'text':
                expected.append(item['text'])
            if item['kind'] != 'table':
                continue
            table = next(tables, None)
            require(table is not None and table['page'] == page_index, 'table report/page mismatch')
            require(table['rows'] == len(item['rows']) and table['columns'] == len(item['widths'])
                    and len(table['cells']) == len(item['rows']) * len(item['widths']), 'table dimensions mismatch')
            grid = table['grid_object_ids']
            require(len(grid) == len(set(grid)) == len(item['rows']) + len(item['widths']) + 2
                    and all(ident in objects and objects[ident]['kind'] == 'PathObject' for ident in grid), 'table grid mapping mismatch')
            padding = item.get('padding_mm', 1)
            horizontal, vertical = table_alignment(item, len(item['widths']))
            addresses = set()
            for cell in table['cells']:
                row, column = cell['row'], cell['column']
                require(type(row) is int and type(column) is int and 0 <= row < len(item['rows'])
                        and 0 <= column < len(item['widths']), 'table cell address mismatch')
                require((row, column) not in addresses, 'duplicate table cell address')
                addresses.add((row, column))
                text = item['rows'][row][column]
                box = [item['x'] + sum(item['widths'][:column]), item['y'] + row * item['row_height'],
                       item['widths'][column], item['row_height']]
                require(cell['text'] == text and all(abs(left - right) < 1e-7 for left, right in zip(cell['box'], box)), 'table cell model mismatch')
                require(len(cell['object_ids']) == (1 if text else 0), 'table cell object count mismatch')
                if not text:
                    continue
                ident = cell['object_ids'][0]
                require(text_by_id.get(ident) == text, 'table cell text/object binding mismatch')
                x, y, width, height = objects[ident]['boundary']
                require(x >= box[0] + padding - 1e-7 and y >= box[1] + padding - 1e-7
                        and x + width <= box[0] + box[2] - padding + 1e-7
                        and y + height <= box[1] + box[3] - padding + 1e-7, 'table cell content out of bounds')
                aligned_x = box[0] + padding + {'left': 0, 'center': 0.5, 'right': 1}[horizontal[column]] * (box[2] - 2 * padding - width)
                aligned_y = box[1] + padding + {'top': 0, 'middle': 0.5, 'bottom': 1}[vertical] * (box[3] - 2 * padding - height)
                require(abs(x - aligned_x) < 1e-6 and abs(y - aligned_y) < 1e-6, 'table cell requested alignment mismatch')
            expected.extend(text for row in item['rows'] for text in row if text)
        require([record['text'] for record in actual['text']] == expected, 'generated literal text differs from model')
    require(next(tables, None) is None, 'extra table report')
    audited['input_model_comparison'] = 'passed; literal text/page order and explicit table cell bindings'
    return audited


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', nargs='?')
    parser.add_argument('output', nargs='?')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if (args.check and (args.spec or args.output)) or (not args.check and not (args.spec and args.output)):
        parser.error('use --check, or supply a JSON spec and new output filename')
    stage = 'environment'
    try:
        write_environment()
        if args.check:
            print(json.dumps(dict(status='environment-ok', check_kind='environment', artifact_validation='not_run',
                                  python=sys.version.split()[0], profile='fixed-layout-1', write_probe='not_run; checked during publication')))
            return 0
        stage = 'input'
        raw = read_asset(args.spec, 256 * 1024)
        spec = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(GenerationError('nonfinite JSON number')))
        stage = 'build'
        data, report = Builder(spec).build()
        checker = sibling('check_generated_ofd')
        stage = 'validation'
        report['structural_validation'] = validate_model(data, spec, report, checker)
        stage = 'publication'
        publication = publish_new(data, args.output, lambda candidate: validate_model(candidate, spec, report, checker))
        report.update(publication)
        report.update(status='generated', output=args.output, cleanup='private candidate removed',
                      archive='not_requested; host must archive validated deliverables through its confirmed lifecycle')
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0
    except (GenerationError, READER.ReadError, OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        print(json.dumps(dict(schema_version='ofd-generation-result-1', status='failed', stage=stage,
                              publication_outcome=getattr(exc, 'publication', {}).get('publication_outcome') or ('not_published_existing_name' if isinstance(exc, FileExistsError)
                                                   else 'inspect_before_retry' if stage == 'publication' else 'not_attempted'),
                              diagnostics=[dict(code='generation_error', detail=str(exc))],
                              **{key: value for key, value in getattr(exc, 'publication', {}).items() if key != 'publication_outcome'}), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
