"""Independent stdlib structural audit of fixed-layout-1; no writer/reader imports or rendering.

This deliberately recognizes only the generated subset. It is not an OFD XSD
validator or an independent client acceptance test.
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import zipfile
from xml.etree import ElementTree as ET
from xml.parsers import expat

NAMESPACE = '{http://www.ofdspec.org/2016}'


def ensure(condition, message):
    if not condition:
        raise ValueError('generated-profile audit: ' + message)


def floats(value, count):
    parts = value.split()
    ensure(len(parts) == count, 'numeric tuple arity')
    result = [float(part) for part in parts]
    ensure(all(math.isfinite(part) for part in result), 'nonfinite coordinate')
    return result


def audit(data):
    ensure(0 < len(data) <= 32 * 1024 * 1024, 'package size')
    ensure(tuple(map(int, expat.EXPAT_VERSION.removeprefix('expat_').split('.'))) >= (2, 6, 0), 'Expat version')
    archive = zipfile.ZipFile(io.BytesIO(data))
    infos = archive.infolist()
    ensure(1 <= len(infos) <= 64 and len({item.filename for item in infos}) == len(infos), 'member count/duplicates')
    members = {}
    trees = {}
    ids = set()
    total = nodes = 0
    for info in infos:
        name = info.filename
        ensure(info.orig_filename == name and re.fullmatch(r'[A-Za-z0-9_/.-]+', name)
               and not name.startswith('/') and all(part not in ('', '.', '..') for part in name.split('/')),
               'unsafe member name')
        ensure(info.compress_type == zipfile.ZIP_STORED and not info.flag_bits & 1
               and info.file_size == info.compress_size and info.file_size <= 10 * 1024 * 1024, 'compression/member profile')
        total += info.file_size
        ensure(total <= 32 * 1024 * 1024, 'total member bytes')
        payload = archive.read(info)
        ensure(len(payload) == info.file_size, 'member readback')
        members[name] = payload
        if name.endswith('.xml'):
            ensure(len(payload) <= 2 * 1024 * 1024, 'XML bytes')
            parser = expat.ParserCreate()
            depth = 0
            def reject(*_args):
                raise ValueError('generated-profile audit: DTD/entity forbidden')
            def start(_tag, attributes):
                nonlocal depth, nodes
                depth += 1
                nodes += 1
                ensure(depth <= 32 and nodes <= 10000 and len(attributes) <= 32, 'XML structure budget')
            def end(_tag):
                nonlocal depth
                depth -= 1
            parser.StartDoctypeDeclHandler = reject
            parser.EntityDeclHandler = reject
            parser.ExternalEntityRefHandler = reject
            parser.StartElementHandler = start
            parser.EndElementHandler = end
            parser.Parse(payload, True)
            tree = ET.fromstring(payload)
            trees[name] = tree
            for node in tree.iter():
                ensure(node.tag.startswith(NAMESPACE), 'foreign namespace')
                if 'ID' in node.attrib:
                    ident = node.attrib['ID']
                    ensure(re.fullmatch(r'[1-9][0-9]{0,4}', ident) and ident not in ids, 'global ID uniqueness/range')
                    ids.add(ident)
    def one(root, tag):
        matches = root.findall(NAMESPACE + tag)
        ensure(len(matches) == 1, 'required single ' + tag)
        return matches[0]
    def target(source, value):
        ensure(isinstance(value, str) and value and not value.startswith('/')
               and '\\' not in value and ':' not in value, 'invalid reference')
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source), value))
        ensure(not path.startswith('../') and path in members, 'unresolved reference')
        return path
    ensure('OFD.xml' in trees, 'missing root')
    root = trees['OFD.xml']
    ensure(root.tag == NAMESPACE + 'OFD' and root.get('Version') == '1.0' and root.get('DocType') == 'OFD', 'root profile')
    body = one(root, 'DocBody')
    doc_info = one(body, 'DocInfo')
    ensure(re.fullmatch(r'[A-F0-9]{32}', one(doc_info, 'DocID').text or ''), 'DocID')
    doc_path = target('OFD.xml', one(body, 'DocRoot').text)
    ensure(doc_path in trees and trees[doc_path].tag == NAMESPACE + 'Document', 'document root')
    document = trees[doc_path]
    common = one(document, 'CommonData')
    maximum = int(one(common, 'MaxUnitID').text)
    fonts = {}
    images = {}
    referenced = {'OFD.xml', doc_path}
    resource_links = common.findall(NAMESPACE + 'DocumentRes')
    ensure(len(resource_links) <= 1, 'resource index count')
    for link in resource_links:
        resource_path = target(doc_path, link.text)
        referenced.add(resource_path)
        res = trees[resource_path]
        ensure(res.tag == NAMESPACE + 'Res' and res.get('BaseLoc') == 'Res', 'resource root/BaseLoc')
        anchor = posixpath.dirname(resource_path) + '/Res/_anchor'
        for font in res.findall(NAMESPACE + 'Fonts/' + NAMESPACE + 'Font'):
            location = target(anchor, one(font, 'FontFile').text)
            referenced.add(location)
            ensure(members[location][:4] == b'\x00\x01\x00\x00', 'font magic')
            ensure(len(members[location]) <= 10 * 1024 * 1024, 'font member limit')
            fonts[font.get('ID')] = location
            licenses = doc_info.findall(NAMESPACE + 'CustomDatas/' + NAMESPACE + 'CustomData')
            ensure(any((node.get('Name') == 'FontLicense:' + font.get('ID') and (node.text or '').strip())
                       or (node.get('Name') == 'FontRights:' + font.get('ID') and node.text == 'not_assessed') for node in licenses),
                   'missing font license or explicit unassessed-rights metadata')
        for image in res.findall(NAMESPACE + 'MultiMedias/' + NAMESPACE + 'MultiMedia'):
            location = target(anchor, one(image, 'MediaFile').text)
            referenced.add(location)
            ensure(image.get('Type') == 'Image' and image.get('Format') in ('PNG', 'JPEG'), 'image resource type')
            ensure(len(members[location]) <= 8 * 1024 * 1024, 'image member limit')
            ensure(members[location].startswith(b'\x89PNG\r\n\x1a\n') if image.get('Format') == 'PNG'
                   else members[location].startswith(b'\xff\xd8'), 'image magic/format')
            images[image.get('ID')] = location
    records = []
    page_entries = list(one(document, 'Pages'))
    ensure(1 <= len(page_entries) <= 32, 'page count')
    for entry in page_entries:
        ensure(entry.tag == NAMESPACE + 'Page', 'page tree child')
        path = target(doc_path, entry.get('BaseLoc'))
        referenced.add(path)
        page = trees[path]
        ensure(page.tag == NAMESPACE + 'Page', 'page root')
        area = one(page, 'Area')
        physical = floats(one(area, 'PhysicalBox').text, 4)
        content_box = floats(one(area, 'ApplicationBox').text, 4)
        ensure(physical[:2] == [0, 0] and physical[2] > 0 and physical[3] > 0, 'page size')
        cx, cy, cw, ch = content_box
        ensure(cx >= 0 and cy >= 0 and cw > 0 and ch > 0
               and cx + cw <= physical[2] + 1e-7 and cy + ch <= physical[3] + 1e-7, 'page margins')
        layer = one(one(page, 'Content'), 'Layer')
        text = []
        objects = []
        for node in layer:
            kind = node.tag.removeprefix(NAMESPACE)
            ensure(kind in ('TextObject', 'PathObject', 'ImageObject'), 'unsupported generated object')
            x, y, width, height = floats(node.get('Boundary', ''), 4)
            ensure(width > 0 and height > 0 and x >= cx - 1e-7 and y >= cy - 1e-7
                   and x + width <= cx + cw + 1e-7 and y + height <= cy + ch + 1e-7, 'object/cell content bounds')
            if kind == 'TextObject':
                ensure(node.get('Font') in fonts and float(node.get('Size', '0')) > 0, 'text font/size')
                code = one(node, 'TextCode')
                decoded = re.sub(r'\\([0-9A-Fa-f]{4})', lambda match: chr(int(match.group(1), 16)), code.text or '')
                glyphs = one(node, 'CGTransform')
                ensure(glyphs.get('CodePosition') == '0' and int(glyphs.get('CodeCount')) == len(decoded)
                       and int(glyphs.get('GlyphCount')) == len(decoded), 'glyph-code count')
                indices = one(glyphs, 'Glyphs').text.split()
                ensure(len(indices) == len(decoded) and all(0 < int(index) < 65536 for index in indices), 'glyph IDs')
                ensure(len(code.get('DeltaX', '').split()) == max(0, len(decoded) - 1), 'horizontal advances')
                text.append(dict(object_id=node.get('ID'), text=decoded))
            elif kind == 'ImageObject':
                ensure(node.get('ResourceID') in images, 'image resource reference')
                a, b, c, d, e, f = floats(node.get('CTM', ''), 6)
                ensure(abs(a - width) < 1e-7 and abs(d - height) < 1e-7 and b == c == e == f == 0, 'image transform')
            else:
                ensure(float(node.get('LineWidth', '0')) > 0 and node.get('Fill') == 'false'
                       and one(node, 'AbbreviatedData').text, 'path profile')
            objects.append(dict(id=node.get('ID'), kind=kind, boundary=[x, y, width, height]))
        records.append(dict(source=path, text=text, objects=objects))
    ensure(ids and maximum == max(map(int, ids)), 'MaxUnitID')
    ensure(referenced == set(members), 'unreferenced/missing generated part')
    archive.close()
    return dict(tool='check_generated_ofd stdlib independent structural implementation', status='passed',
                scope='fixed-layout-1 ZIP/XML/references/IDs/bounds/literal text only; no XSD or rendering certification',
                sha256=hashlib.sha256(data).hexdigest(), pages=records, max_unit_id=maximum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    args = parser.parse_args(argv)
    try:
        path = Path(args.input)
        ensure(not path.is_absolute() and path.parts and '..' not in path.parts, 'input outside task directory')
        ensure(os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'confined file primitives')
        directory = os.open('.', os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        descriptor = None
        try:
            for part in path.parts[:-1]:
                next_directory = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=directory)
                os.close(directory)
                directory = next_directory
            descriptor = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
            before = os.fstat(descriptor)
            ensure(stat.S_ISREG(before.st_mode) and before.st_size <= 32 * 1024 * 1024, 'regular bounded input')
            with os.fdopen(descriptor, 'rb') as handle:
                descriptor = None
                data = handle.read(before.st_size + 1)
                after = os.fstat(handle.fileno())
            ensure(len(data) == before.st_size and all(getattr(before, key) == getattr(after, key)
                   for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'input changed')
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)
        print(json.dumps(audit(data), ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, expat.ExpatError, ET.ParseError) as exc:
        print(json.dumps(dict(status='failed', diagnostics=[str(exc)]), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
