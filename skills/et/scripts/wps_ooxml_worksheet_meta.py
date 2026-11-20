"""Metadata, style inheritance, and relationship helpers for OOXML worksheets."""

from bisect import bisect_right
import re

from wps_numeric import interpret_number
from wps_ooxml import relationships, rels_path, split_tag
from wps_ooxml_text import is_main
from wps_sheet_views import ooxml_sheet_views


def read_sheet_relations(context, ws_root):
    """Load worksheet relationships; only comments/VML are read, never followed."""
    report = context.report
    relations = relationships(context.package, context.sheet_part, context.budget,
                              report=report, scope='worksheet', sheet=context.sheet_name)
    context.used_parts.add(rels_path(context.sheet_part))
    for relationship, relation in relations.items():
        kind = relation.get('kind')
        if relation.get('external') or kind is None:
            continue
        if kind in ('comments', 'vmlDrawing'):
            if relation.get('path'):
                context.used_parts.add(relation['path'])
            continue
        report.add('worksheet_relationship_not_followed', sheet=context.sheet_name,
                   relationship_kind=str(kind)[:60], relationship_id=relationship)
    return relations, ws_root


def read_sheet_views(context, ws_root, main_children_fn, coordinate_fn):
    """Parse saved worksheet view selections and freeze/split pane state."""
    report = context.report
    sheet = context.sheet_name
    worksheet_tag = context.tag
    containers = main_children_fn(ws_root, 'sheetViews')
    if len(containers) > 1:
        report.add('duplicate_sheet_views_ignored', sheet=sheet)
    if not containers:
        return []
    views, other_settings = ooxml_sheet_views(containers[0], worksheet_tag, coordinate_fn)
    if not views or any(view.get('issues') for view in views):
        report.add('saved_sheet_view_partially_read', sheet=sheet,
                   element=worksheet_tag('sheetViews'))
    if other_settings:
        report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                   element=worksheet_tag('sheetViews'))
    return views


def read_columns(context, ws_root, main_children_fn, integer_fn, saved_dimension_fn, max_column_ranges):
    """Read bounded column range width, visibility, and style indices."""
    report = context.report
    sheet = context.sheet_name
    columns = context.columns
    for container in main_children_fn(ws_root, 'cols'):
        if len(columns) >= max_column_ranges:
            report.add('column_metadata_limit_reached', sheet=sheet, kept=max_column_ranges)
            break
        for col in container:
            if len(columns) >= max_column_ranges:
                report.add('column_metadata_limit_reached', sheet=sheet,
                           kept=max_column_ranges)
                break
            if not is_main(col, 'col'):
                report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                           element=str(col.tag)[:120])
                continue
            try:
                first = integer_fn(col.get('min'), 16384)
                last = integer_fn(col.get('max'), 16384)
            except ValueError:
                report.add('column_reference_not_interpreted', sheet=sheet,
                           raw=str(col.get('min'))[:20])
                continue
            if first < 1 or last < first:
                report.add('column_range_not_interpreted', sheet=sheet,
                           raw=str(col.get('min'))[:20])
                continue
            raw_hidden = col.get('hidden')
            if raw_hidden is None:
                hidden_saved = False
            elif raw_hidden in ('0', '1', 'false', 'true'):
                hidden_saved = raw_hidden in ('1', 'true')
            else:
                hidden_saved = False
                report.add('saved_visibility_flag_not_interpreted', sheet=sheet,
                           column=first, raw=raw_hidden[:20])
            columns.append({'first_zero_based': first - 1, 'last_zero_based': last - 1,
                            'style_index_raw': col.get('style'),
                            'hidden_saved': hidden_saved, 'hidden_cause': 'unknown',
                            **saved_dimension_fn(col.get('width'), 'width_chars')})
    previous_end = -1
    for entry in sorted(columns, key=lambda item: item['first_zero_based']):
        if entry['first_zero_based'] <= previous_end:
            report.add('overlapping_column_ranges_not_applied', sheet=sheet,
                       first=entry['first_zero_based'] + 1)
        previous_end = max(previous_end, entry['last_zero_based'])


def read_merges(context, ws_root, main_children_fn, cell_range_fn, max_merges, max_overlap_checks):
    """Read bounded merged cell ranges with overlap diagnostics."""
    report = context.report
    sheet = context.sheet_name
    merges = context.merges
    checked = 0
    for container in main_children_fn(ws_root, 'mergeCells'):
        declared = container.get('count')
        if declared is not None and re.fullmatch(r'[0-9]{1,10}', declared):
            if int(declared) != len(container):
                report.add('merge_count_mismatch', sheet=sheet)
        for merge in container:
            if not is_main(merge, 'mergeCell'):
                report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                           element=str(merge.tag)[:120])
                continue
            if len(merges) >= max_merges:
                report.add('merge_count_limit_reached', sheet=sheet,
                           kept=max_merges, cell_value_impact='metadata_only')
                continue
            try:
                bounds = cell_range_fn(merge.get('ref', ''))
            except ValueError:
                report.add('merge_range_not_interpreted', sheet=sheet,
                           raw=str(merge.get('ref'))[:40])
                continue
            if (bounds[0], bounds[2]) == (bounds[1], bounds[3]):
                report.add('single_cell_merge_ignored', sheet=sheet,
                           cell=str(merge.get('ref'))[:20])
                continue
            if checked < max_overlap_checks:
                checked += 1
                overlaps = any(
                    not (bounds[1] < other['bounds_zero_based'][0]
                         or bounds[0] > other['bounds_zero_based'][1]
                         or bounds[3] < other['bounds_zero_based'][2]
                         or bounds[2] > other['bounds_zero_based'][3])
                    for other in merges)
                if overlaps:
                    report.add('overlapping_merges_retained', sheet=sheet,
                               cell=str(merge.get('ref'))[:20])
            merges.append({'range': merge.get('ref'), 'bounds_zero_based': bounds})
    if len(merges) > max_overlap_checks:
        report.add('merge_overlap_check_bounded', sheet=sheet,
                   checked=max_overlap_checks)


def read_hyperlinks(context, ws_root, relations, main_children_fn, cell_range_fn, coordinate_fn, relationship_id_fn, max_hyperlinks):
    """Read bounded worksheet hyperlinks without resolving external targets."""
    report = context.report
    sheet = context.sheet_name
    hyperlinks = context.hyperlinks
    seen = {item['cell'] for item in hyperlinks}
    for container in main_children_fn(ws_root, 'hyperlinks'):
        for link in container:
            if not is_main(link, 'hyperlink'):
                report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                           element=str(link.tag)[:120])
                continue
            if len(hyperlinks) >= max_hyperlinks:
                report.add('hyperlink_count_limit_reached', sheet=sheet, kept=max_hyperlinks)
                return
            ref = link.get('ref')
            try:
                if ':' in (ref or ''):
                    cell_range_fn(ref)
                else:
                    coordinate_fn(ref)
            except ValueError:
                report.add('hyperlink_reference_not_interpreted', sheet=sheet,
                           raw=str(ref)[:40])
                continue
            if ref in seen:
                report.add('duplicate_hyperlink_ignored', sheet=sheet, cell=ref)
                continue
            seen.add(ref)
            location = link.get('location')
            raw_relationship = relationship_id_fn(link)
            relation = relations.get(raw_relationship) if raw_relationship else None
            hyperlink = {'cell': ref, 'location_raw': location,
                         'display_raw': link.get('display'),
                         'tooltip_raw': link.get('tooltip'),
                         'relationship_id_raw': raw_relationship,
                         'target_resolved': False, 'followed': False,
                         'source': {'part': context.sheet_part, 'cell': ref}}
            if relation is not None:
                hyperlink['relationship_external'] = bool(relation.get('external'))
                if relation.get('external') and relation.get('target_raw'):
                    hyperlink['target_raw'] = relation['target_raw']
            hyperlinks.append(hyperlink)
            if location is not None:
                report.add('internal_hyperlink_target_not_resolved', sheet=sheet, cell=ref)
            else:
                report.add('hyperlink_target_not_followed', sheet=sheet, cell=ref)


def read_filters(context, ws_root, main_children_fn, cell_range_fn):
    """Record autoFilter ranges without evaluating filter conditions."""
    report = context.report
    sheet = context.sheet_name
    for filter_node in main_children_fn(ws_root, 'autoFilter'):
        if context.filters:
            report.add('duplicate_filter_definition_ignored', sheet=sheet)
            continue
        try:
            cell_range_fn(filter_node.get('ref', ''))
        except ValueError:
            report.add('filter_range_not_interpreted', sheet=sheet,
                       raw=str(filter_node.get('ref'))[:40])
            continue
        context.filters.append({'range': filter_node.get('ref'),
                                'conditions_decoded': False})
        report.add('filter_conditions_not_evaluated', sheet=sheet)


def column_style_for(columns, column_index):
    """Find the saved column-format metadata covering one zero-based column."""
    starts = [entry['first_zero_based'] for entry in columns]
    position = bisect_right(starts, column_index) - 1
    while position >= 0:
        entry = columns[position]
        if entry['first_zero_based'] <= column_index <= entry['last_zero_based']:
            return entry
        position -= 1
    return None


def apply_row_column_styles(context, coordinate_fn):
    """Apply saved row/column formats to numeric cells only when unambiguous."""
    report = context.report
    sheet = context.sheet_name
    styles = context.styles
    styled_rows = {entry['row_zero_based']: entry for entry in context.rows
                   if entry['style_index_raw'] is not None}
    styled_columns = [entry for entry in context.columns
                      if entry['style_index_raw'] is not None]
    for ref, item in context.cells.items():
        if item['type'] != 'number' or item['cell_style_explicit']:
            continue
        row_index, column_index = coordinate_fn(ref)
        candidates = []
        if row_index in styled_rows:
            entry = styled_rows[row_index]
            candidates.append(('row', entry['style_index_raw'],
                               entry['custom_format_raw'] in ('1', 'true')))
        column_entry = column_style_for(styled_columns, column_index)
        if column_entry is not None:
            candidates.append(('column', column_entry['style_index_raw'], True))
        if not candidates:
            continue
        valid = []
        for origin, raw_index, active in candidates:
            if (active and re.fullmatch(r'[0-9]{1,4}', raw_index or '')
                    and int(raw_index) < len(styles)):
                inherited_index = int(raw_index)
                resolution = context.number_formats.resolve(inherited_index)
                if resolution['status'] == 'resolved':
                    valid.append((origin, inherited_index, resolution))
        matched = None
        if len(valid) == len(candidates):
            if len(valid) == 1:
                matched = valid[0]
            elif (len(valid) == 2
                  and valid[0][2]['number_format_id'] == valid[1][2]['number_format_id']):
                origin, inherited_index, resolution = valid[0]
                resolution = dict(resolution)
                resolution['source'] = {
                    'equivalent_sources': [entry[2]['source'] for entry in valid]}
                matched = ('row_and_column_equivalent', inherited_index, resolution)
        if matched is None:
            item['numeric_interpretation'] = {
                'status': 'unknown',
                'reason': 'row_or_column_style_inheritance_unresolved',
                'source_value_unchanged': True,
            }
            report.add('number_style_inheritance_not_resolved', sheet=sheet, cell=ref)
            continue
        origin, inherited_index, resolution = matched
        inherited_format = resolution['number_format_id']
        interpreted = interpret_number(item['value'], inherited_format,
                                       context.formats.get(inherited_format),
                                       context.date_system)
        interpreted['number_format_source'] = resolution['source']
        interpreted['basis'] = 'stored_number_and_unambiguous_' + origin + '_cell_xf'
        interpreted['style_source'] = {'kind': origin, 'xf_index': inherited_index,
                                       'part': context.sheet_part}
        item['numeric_interpretation'] = interpreted


def report_unknown_worksheet_elements(context, ws_root, main_namespaces):
    """Record unsupported or unparsed top-level worksheet elements as diagnostics."""
    report = context.report
    sheet = context.sheet_name
    known = {'sheetData', 'sheetViews', 'cols', 'mergeCells', 'hyperlinks',
             'autoFilter', 'legacyDrawing'}
    metadata = {'dimension', 'sheetFormatPr', 'pageMargins'}
    for child in ws_root:
        namespace, local = split_tag(child.tag)
        if namespace in main_namespaces and local in known:
            continue
        if namespace in main_namespaces and local in metadata:
            report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                       element=str(child.tag)[:120])
            continue
        report.add('worksheet_metadata_not_interpreted', sheet=sheet,
                   element=str(child.tag)[:120])
