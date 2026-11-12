"""Bounded number-format ancestry, not a full spreadsheet style engine."""
import re

MAX_STYLE_DEPTH = 32
MAX_XFS = 65536
STYLE_NAMESPACES = ('http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                    'http://purl.oclc.org/ooxml/spreadsheetml/main')


def _local(tag):
    if isinstance(tag, str) and tag.startswith('{'):
        end = tag.find('}')
        return tag[end + 1:] if end >= 0 else tag
    return tag


def _style_tag(node, name):
    tag = node.tag if hasattr(node, 'tag') else node
    return _local(tag) == name and (not isinstance(tag, str) or not tag.startswith('{')
                                    or tag[1:tag.find('}')] in STYLE_NAMESPACES)


def parse_xfs(node, tag, report=None):
    """Parse saved XF entries.

    Reading passes an IssueLog so malformed entries are degraded to issues; the
    direct validation API keeps raising ValueError when no report is supplied.
    """
    def issue(code, message=None, **detail):
        if report is None:
            raise ValueError(message or code)
        report.add(code, message, **detail)

    declared = node.get('count')
    if declared is not None:
        if re.fullmatch(r'[0-9]{1,10}', declared) is None or int(declared) != len(node):
            issue('style_xf_count_mismatch', 'XF collection count does not match its entries')
    if len(node) > MAX_XFS:
        issue('style_xf_limit_reached', 'Only the bounded XF prefix is interpreted',
              kept=MAX_XFS, declared=len(node))
    result = []
    for xf in list(node)[:MAX_XFS]:
        if not _style_tag(xf, 'xf'):
            issue('style_xf_child_not_interpreted', element=str(xf.tag))
            continue
        raw = xf.get('numFmtId', '0')
        if re.fullmatch(r'[0-9]{1,5}', raw or '') is None or int(raw) > 65535:
            issue('style_xf_number_format_invalid', reason='numFmtId is not a bounded integer',
                  raw=raw)
            raw = '0'
        apply = xf.get('applyNumberFormat')
        if apply not in (None, '0', '1', 'false', 'true'):
            issue('style_xf_number_format_invalid', reason='applyNumberFormat is not a boolean',
                  raw=apply)
            apply = None
        result.append({'number_format_id_raw': int(raw),
                       'apply_number_format': apply in ('1', 'true'),
                       'attributes_raw': dict(xf.attrib)})
    return result


class NumberFormats:
    def __init__(self, cell_xfs, style_xfs):
        self.cell_xfs = cell_xfs
        self.style_xfs = style_xfs
        self.cache = {}

    def resolve(self, index):
        if index not in self.cache:
            self.cache[index] = self._resolve(index)
        return dict(self.cache[index])

    def _resolve(self, index):
        def unknown(reason, **details):
            return {'status': 'unknown', 'reason': reason, 'source_value_unchanged': True, **details}
        if not isinstance(index, int) or not 0 <= index < len(self.cell_xfs):
            return unknown('cell_xf_outside_collection')
        cell = self.cell_xfs[index]
        attrs = cell['attributes_raw']
        own = cell['number_format_id_raw']
        raw_parent = attrs.get('xfId')
        source = {'cell_xf_index': index, 'cell_num_fmt_id': own,
                  'apply_number_format_raw': attrs.get('applyNumberFormat')}
        if raw_parent is None:
            if cell['apply_number_format']:
                return {'status': 'resolved', 'number_format_id': own, 'source': source}
            return unknown('style_inheritance_unresolved', source=source)
        if not re.fullmatch(r'[0-9]{1,10}', raw_parent) or int(raw_parent) >= len(self.style_xfs):
            return unknown('style_parent_outside_collection', source=source)
        parent_index = int(raw_parent)
        source['style_xf_index'] = parent_index
        parent = self.style_xfs[parent_index]
        # cellStyleXfs xfId is forbidden by the schema. Inspect boundedly only
        # to classify malformed cycles; never treat it as valid inheritance.
        visited = set()
        probe = parent_index
        for _ in range(MAX_STYLE_DEPTH):
            if probe in visited:
                return unknown('style_parent_cycle', source=source)
            visited.add(probe)
            raw = self.style_xfs[probe]['attributes_raw'].get('xfId')
            if raw is None:
                break
            if not re.fullmatch(r'[0-9]{1,10}', raw) or int(raw) >= len(self.style_xfs):
                return unknown('style_parent_outside_collection', source=source)
            probe = int(raw)
        else:
            return unknown('style_parent_depth_limit', source=source)
        if parent['attributes_raw'].get('xfId') is not None:
            return unknown('style_parent_chain_not_allowed', source=source)
        source['parent_num_fmt_id'] = parent['number_format_id_raw']
        if cell['apply_number_format']:
            return {'status': 'resolved', 'number_format_id': own, 'source': source}
        parent_apply = parent['attributes_raw'].get('applyNumberFormat', 'true')
        if parent_apply in ('0', 'false'):
            return unknown('parent_number_format_not_applied', source=source)
        if own != parent['number_format_id_raw']:
            return unknown('cell_parent_number_format_conflict', source=source)
        # MS-OE376: apply=false is a linked stored copy, not a request to
        # substitute a different parent format. Agreement proves this subset.
        return {'status': 'resolved', 'number_format_id': own, 'source': source}


def read_style_sheet(package, part, namespace, budget, report, reader=None):
    """Read a saved styles part; every fault becomes an issue, never a read abort."""
    from wps_ooxml import (MAX_FORMATS, fromstring, read_part, is_security_error,
                           iter_main_children, split_tag)
    reader = read_part if reader is None else reader
    tag = lambda name: '{' + namespace + '}' + name
    styles = []
    style_xfs = []
    formats = {}
    seen_collections = set()
    try:
        root = fromstring(reader(package, part), budget=budget)
    except ValueError as exc:
        if is_security_error(exc):
            raise
        report.add('styles_part_not_read', 'Saved styles were ignored after a bounded read '
                   'failure; cell values are unaffected', part=part, reason=str(exc)[:120])
        return styles, style_xfs, formats
    if root.tag != tag('styleSheet'):
        report.add('styles_root_not_interpreted', part=part, element=str(root.tag)[:120])
        return styles, style_xfs, formats
    for child in iter_main_children(root, (namespace,)):
        local = split_tag(child.tag)[1]
        if local == 'numFmts':
            for fmt in iter_main_children(child, (namespace,)):
                if split_tag(fmt.tag)[1] != 'numFmt':
                    report.add('style_metadata_not_interpreted', part=part,
                               element=str(fmt.tag)[:120])
                    continue
                raw_id = fmt.get('numFmtId')
                if not isinstance(raw_id, str) or not raw_id.isdigit() or int(raw_id) > 65535:
                    report.add('number_format_not_interpreted', part=part,
                               reason='numFmtId is not a bounded integer', raw=raw_id)
                    continue
                key = int(raw_id)
                code = fmt.get('formatCode')
                if not isinstance(code, str):
                    report.add('number_format_not_interpreted', part=part,
                               reason='formatCode is missing')
                    continue
                if len(code) > 4096:
                    report.add('number_format_not_interpreted', part=part,
                               reason='formatCode exceeds the bounded length', num_fmt_id=key)
                    continue
                if key in formats:
                    report.add('duplicate_number_format_ignored', part=part, num_fmt_id=key)
                    continue
                if len(formats) >= MAX_FORMATS:
                    report.add('number_format_limit_reached', part=part, kept=MAX_FORMATS)
                    continue
                formats[key] = code
        elif local in ('cellXfs', 'cellStyleXfs'):
            if local in seen_collections:
                report.add('style_metadata_not_interpreted', part=part,
                           element='duplicate ' + local)
                continue
            seen_collections.add(local)
            parsed = parse_xfs(child, tag, report)
            if local == 'cellXfs':
                styles = parsed
            else:
                style_xfs = parsed
        else:
            report.add('style_metadata_not_interpreted', part=part, element=local)
    return styles, style_xfs, formats
