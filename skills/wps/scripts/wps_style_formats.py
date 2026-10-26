"""Bounded number-format ancestry, not a full spreadsheet style engine."""
import re

MAX_STYLE_DEPTH = 32
MAX_XFS = 4096


def parse_xfs(node, tag):
    if len(node) > MAX_XFS:
        raise ValueError('XF collection limit exceeded')
    declared = node.get('count')
    if declared is not None and (not re.fullmatch(r'[0-9]{1,10}', declared) or int(declared) != len(node)):
        raise ValueError('XF collection count mismatch')
    result = []
    for xf in node:
        if xf.tag != tag('xf'):
            raise ValueError('Unknown XF collection child')
        raw = xf.get('numFmtId', '0')
        if not re.fullmatch(r'[0-9]{1,5}', raw) or int(raw) > 65535:
            raise ValueError('Invalid XF number format ID')
        apply = xf.get('applyNumberFormat')
        if apply not in (None, '0', '1', 'false', 'true'):
            raise ValueError('Invalid XF applyNumberFormat')
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
        if not 0 <= index < len(self.cell_xfs):
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
