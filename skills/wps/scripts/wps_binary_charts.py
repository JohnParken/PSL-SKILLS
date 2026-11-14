"""Follow bounded DrawingML chart caches in MS-DOC OfficeArt metroBlob data."""

from io import BytesIO
from posixpath import split
import zipfile
import zlib
from xml.parsers import expat

from wps_chart_presentation import add_presentation
from wps_chart_workbook import enrich_embedded
from wps_access import require
from wps_binary_chart_cache import CHART_NS, DRAWING_NS, ChartCacheBudget, ChartCacheLimit, read_chart_cache
from wps_ooxml import (CONTENT_TYPES, R, RELATIONSHIPS, XmlBudget, XmlBudgetExceeded, check_archive,
                       fromstring, preflight_archive, read_part, resolve_internal_target)


WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
FRAME_REL = 'http://schemas.microsoft.com/office/2006/relationships/graphicFrameDoc'
FRAME_TYPE = 'application/vnd.ms-office.DrsE2oDoc+xml'
CHART_TYPE = 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
RELATIONSHIPS_TYPE = 'application/vnd.openxmlformats-package.relationships+xml'
MAX_BLOBS = 64
MAX_BLOB_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BLOB_BYTES = 16 * 1024 * 1024
MAX_BLOB_MEMBERS = 128
MAX_XML_PART_BYTES = 1024 * 1024
MAX_TOTAL_XML_BYTES = 8 * 1024 * 1024
MAX_CHART_PART_NAME_BYTES = 1024
PACKAGE_ERRORS = (KeyError, ValueError, zipfile.BadZipFile, NotImplementedError, OSError, zlib.error)


class PackageBudget:
    """Share ZIP, XML and chart output limits across all anchors in one read."""

    def __init__(self):
        self.blob_bytes = 0
        self.xml_bytes = 0
        self.xml = XmlBudget()
        self.caches = ChartCacheBudget()

    def load_xml(self, package, part):
        """Read and parse a declared part inside both XML budgets."""
        size = package.getinfo(part).file_size
        if size > MAX_XML_PART_BYTES or self.xml_bytes + size > MAX_TOTAL_XML_BYTES:
            raise ChartCacheLimit('Chart package XML byte budget exceeded')
        self.xml_bytes += size
        return fromstring(read_part(package, part), budget=self.xml)


def _tag(namespace, local):
    return f'{{{namespace}}}{local}'


def _one_child(parent, tag):
    items = parent.findall(tag)
    if len(items) != 1:
        raise ValueError('Ambiguous or missing chart drawing element')
    return items[0]


def _content_types(root):
    if root.tag != _tag(CONTENT_TYPES, 'Types'):
        raise ValueError('Invalid chart package content-types root')
    defaults, overrides = {}, {}
    for node in root:
        if node.tag == _tag(CONTENT_TYPES, 'Override'):
            key = node.get('PartName', '')
            if not key.startswith('/'):
                raise ValueError('Invalid chart package part declaration')
            registry = overrides
            part = resolve_internal_target(key, None)
            if part != key[1:]:
                raise ValueError('Noncanonical chart package part declaration')
            key = part
        elif node.tag == _tag(CONTENT_TYPES, 'Default'):
            registry = defaults
            key = node.get('Extension', '').lower()
        else:
            continue
        content_type = node.get('ContentType')
        if not key or not content_type or key in registry:
            raise ValueError('Invalid or repeated chart package content type')
        registry[key] = content_type
    return defaults, overrides


def _typed_xml(package, part, expected_type, types, budget):
    defaults, overrides = types
    name = part.rsplit('/', 1)[-1]
    extension = name.rpartition('.')[2].lower() if '.' in name else ''
    if overrides.get(part, defaults.get(extension)) != expected_type:
        raise ValueError('Invalid content type for chart package part')
    return budget.load_xml(package, part)


def _relationships(package, source, types, budget):
    if source:
        directory, name = split(source)
        member = f'{directory}/_rels/{name}.rels' if directory else f'_rels/{name}.rels'
    else:
        member = '_rels/.rels'
    root = _typed_xml(package, member, RELATIONSHIPS_TYPE, types, budget)
    if root.tag != _tag(RELATIONSHIPS, 'Relationships'):
        raise ValueError('Invalid chart package relationships root')
    relationships = {}
    for node in root.findall(_tag(RELATIONSHIPS, 'Relationship')):
        identifier = node.get('Id')
        if not identifier or identifier in relationships:
            raise ValueError('Invalid or repeated chart package relationship ID')
        relationships[identifier] = node
    return relationships


def _internal_part(relationship, source, expected_relation):
    if (relationship is None or relationship.get('Type') != expected_relation
            or relationship.get('TargetMode', 'Internal') != 'Internal'):
        raise ValueError('Invalid internal chart package relationship')
    target = relationship.get('Target') or ''
    if len(target) > MAX_CHART_PART_NAME_BYTES or len(target.encode('utf-8')) > MAX_CHART_PART_NAME_BYTES:
        raise ChartCacheLimit('Chart package target name limit exceeded')
    return resolve_internal_target(target, source)


def _frame_chart_part(package, frame_part, types, budget):
    frame = _typed_xml(package, frame_part, FRAME_TYPE, types, budget)
    if frame.tag != _tag(WP, 'e2oholder'):
        raise ValueError('Invalid graphic frame root')
    graphic = _one_child(frame, _tag(DRAWING_NS, 'graphic'))
    graphic_data = _one_child(graphic, _tag(DRAWING_NS, 'graphicData'))
    if graphic_data.get('uri') != CHART_NS:
        if ('chart' in graphic_data.get('uri', '').lower()
                or graphic_data.find(_tag(CHART_NS, 'chart')) is not None):
            raise ValueError('Unsupported chart markup in the graphic frame')
        return None
    reference = _one_child(graphic_data, _tag(CHART_NS, 'chart'))
    rels = _relationships(package, frame_part, types, budget)
    return _internal_part(rels.get(reference.get(_tag(R, 'id'))), frame_part, R + '/chart')


def _workbook_status(package, root, chart_part, types, budget):
    sources = root.findall(_tag(CHART_NS, 'externalData'))
    if not sources:
        return 'no_reference'
    if len(sources) != 1:
        return 'unresolved'
    try:
        rels = _relationships(package, chart_part, types, budget)
        relation = rels.get(sources[0].get(_tag(R, 'id')))
        if relation is None or relation.get('Type') not in (R + '/oleObject', R + '/package'):
            return 'unresolved'
        mode = relation.get('TargetMode', 'Internal')
        if mode == 'External' and relation.get('Target'):
            return 'external_not_followed'
        if mode == 'Internal':
            target = resolve_internal_target(relation.get('Target'), chart_part)
            if not package.getinfo(target).is_dir():
                return 'embedded_not_read'
    except (ChartCacheLimit, XmlBudgetExceeded):
        raise
    except PACKAGE_ERRORS:
        return 'unresolved'
    return 'unresolved'


def _chart_package(payload, budget):
    if expat.version_info < (2, 6, 0):
        return {'status': 'not_decoded', 'reason': 'expat_unavailable'}
    source = BytesIO(payload)
    preflight_archive(source, len(payload))
    with zipfile.ZipFile(source) as package:
        if len(package.infolist()) > MAX_BLOB_MEMBERS:
            raise ChartCacheLimit('Chart package member budget exceeded')
        check_archive(package)
        types = _content_types(budget.load_xml(package, '[Content_Types].xml'))
        rels = _relationships(package, None, types, budget)
        frames = [rel for rel in rels.values() if rel.get('Type') == FRAME_REL]
        if not frames:
            return None
        if len(frames) != 1:
            raise ValueError('Ambiguous graphic frame relationship')
        frame = _internal_part(frames[0], None, FRAME_REL)
        chart = _frame_chart_part(package, frame, types, budget)
        if chart is None:
            return None
        root = _typed_xml(package, chart, CHART_TYPE, types, budget)
        parsed = read_chart_cache(root, budget.caches)
        workbook_status = _workbook_status(package, root, chart, types, budget)
        if workbook_status == 'unresolved':
            parsed['status'] = 'partial'
            parsed['issues'].append('workbook_relationship_unresolved')
        result = {**parsed, 'graphic_frame_part': frame, 'chart_part': chart,
                  'workbook_status': workbook_status}
        if workbook_status == 'embedded_not_read':
            enrichment = enrich_embedded(package, root, chart, types, budget, result)
            result['workbook_enrichment'] = enrichment
            state = enrichment['status']
            result['workbook_status'] = state if state.startswith('embedded_') else 'embedded_' + state
            if enrichment['issues']:
                result['status'] = 'partial'
                result['issues'].append('embedded_workbook_incomplete')
        add_presentation(result, budget.caches)
        return result


def _read_blob(payload, source, budget):
    if payload is None:
        return {'status': 'not_decoded', 'reason': 'invalid_metro_blob_property'}
    budget.blob_bytes += len(payload)
    require(len(payload) <= MAX_BLOB_BYTES and budget.blob_bytes <= MAX_TOTAL_BLOB_BYTES,
            source, 'Chart package byte budget exceeded', 'resource_limit')
    try:
        return _chart_package(payload, budget)
    except (ChartCacheLimit, XmlBudgetExceeded) as exc:
        require(False, source, str(exc), 'resource_limit')
    except PACKAGE_ERRORS:
        # Do not include arbitrary package names, XML text, or relationship targets in errors.
        return {'status': 'not_decoded', 'reason': 'invalid_or_unsupported_chart_package'}


def add_binary_charts(result, links):
    """Publish independent cache results tied to verified MS-DOC positions."""
    if not links:
        return
    budget = PackageBudget()
    parsed = {}
    payloads = {}
    skipped = {}
    skipped_references = 0
    entries = []
    for reference, (payload, source) in sorted(links, key=lambda item: item[0]['anchor_cp']):
        key = (source['stream'], source['offset'])
        if key in payloads:
            require(payloads[key] == payload, source,
                    'Conflicting chart payloads at one source position')
        else:
            payloads[key] = payload
        if key in skipped or (key not in parsed and len(parsed) >= MAX_BLOBS):
            skipped[key] = source
            skipped_references += 1
            continue
        if key not in parsed:
            entry = _read_blob(payload, source, budget)
            parsed[key] = entry
            if entry is not None:
                entry.update(source=source, references=[])
                entries.append(entry)
        entry = parsed[key]
        if entry is not None:
            entry['references'].append(reference)
    if not entries and not skipped:
        return
    unread = [entry for entry in entries if entry['status'] == 'not_decoded']
    incomplete = [entry for entry in entries if entry['status'] == 'partial']
    result['binary_charts'] = {
        'scope': 'officeart_metro_blob_cache',
        'status': ('partial' if skipped else
                   'not_decoded' if len(unread) == len(entries) else
                   'partial' if incomplete or unread else 'stored_data'),
        'chart_count': len(entries) - len(unread),
        'unresolved_package_count': len(unread),
        'charts': entries,
    }
    if skipped:
        ranges = {}
        for stream, offset in skipped:
            item = ranges.setdefault(stream, {'stream': stream, 'min_offset': offset,
                                              'max_offset': offset, 'candidate_count': 0})
            item['min_offset'] = min(item['min_offset'], offset)
            item['max_offset'] = max(item['max_offset'], offset)
            item['candidate_count'] += 1
        coverage = {
            'candidate_scan_limit': MAX_BLOBS,
            'scanned_candidate_count': len(parsed),
            'uninspected_candidate_count': len(skipped),
            'uninspected_reference_count': skipped_references,
            'all_candidates_inspected': False,
            'uninspected_source_bounds': list(ranges.values()),
            'source_bounds_are_contiguous': False,
        }
        result['binary_charts']['inspection'] = coverage
        result['issues'].append({
            'code': 'binary_chart_candidate_scan_limit', **coverage,
            'message': 'Optional chart inspection stopped at its candidate limit; '
                       'remaining drawing packages were not inspected or classified as charts',
        })
        result['status'] = 'partial'
        result['partial'] = True
    for entry in unread:
        result['issues'].append({'code': 'binary_chart_package_not_decoded', **entry['source'], 'reason': entry['reason'],
                                 'message': 'Stored OfficeArt chart package could not be verified'})
    for entry in incomplete:
        result['issues'].append({'code': 'binary_chart_cache_incomplete', **entry['source'],
                                 'message': 'Some chart data or source metadata is missing or unsupported'})
