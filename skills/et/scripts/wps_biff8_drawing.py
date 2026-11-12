# SPDX-License-Identifier: MIT
"""Tolerant BIFF8 drawing, object and embedded-chart handling.

Drawings, objects and charts are retained as bounded opaque evidence so later
worksheet cells are still read. Nothing is decoded semantically, rendered,
evaluated or executed; macro/link/executable object subrecords are explicitly
detected and reported as not executed.
"""
from wps_biff8_charts import (OBJ_CHART_TYPE, OBJ_NOTE_TYPE, chart_records_end,
                              drawing_frame_report, obj_subrecords,
                              officeart_fragments, officeart_root)
from wps_biff8_drawing_notes import DrawingResourceLimit, note_drawing_group
from wps_biff8_retain import (NOTE_RECORD_NAMES, OBJ_NOTE_PREFIX, retain,
                              salvage)


def _is_note_object(records, index):
    """True when the record at ``index`` is an Obj with object type 25 (note)."""
    if index >= len(records) or records[index][1] != 0x005D:
        return False
    payload = records[index][2]
    if not payload.startswith(OBJ_NOTE_PREFIX):
        return False
    return obj_subrecords(payload)['object_type_raw'] == OBJ_NOTE_TYPE


def _handle_note_drawing(reader, state, records, start):
    """Try the exact note-drawing chain; return next index or ``None``."""
    location = reader.current_location
    try:
        stop, details, identities, report = note_drawing_group(
            records, start,
            byte_budget=max(0, reader.limits['max_metadata_bytes']
                            - reader.metadata_bytes),
            record_budget=max(0, reader.limits['max_metadata_entries']
                              - len(reader.metadata)),
            existing_ids=state.object_ids)
    except DrawingResourceLimit as exc:
        reader.issue({'code': 'note_drawing_metadata_budget',
                      'sheet': state.bound['name'],
                      'record_offset': location.get('record_offset'),
                      'message': str(exc)})
        return None
    except ValueError as exc:
        reader.issue({'code': 'note_drawing_validation_failed',
                      'sheet': state.bound['name'],
                      'record_offset': location.get('record_offset'),
                      'message': str(exc)})
        return None
    for component in records[start:stop]:
        index = reader.metadata_record(component, NOTE_RECORD_NAMES,
                                       state.bound['name'])
        if index is not None and component[0] in details:
            reader.metadata[index]['structure'] = details[component[0]]
    state.object_ids.update(identities)
    comments = report.get('comments') or []
    accepted = []
    for comment in comments:
        if not reader.reserve_display_text(comment.get('text', ''),
                                           comment.get('author', '')):
            comment['text'] = ''
            comment['author'] = ''
            comment['display_text_omitted'] = True
        accepted.append(comment)
    state.comments.extend(accepted)
    reported = {key: value for key, value in report.items()
                if key != 'comments'}
    reader.note_drawings.append({**reported, 'sheet': state.bound['name']})
    if report.get('unknown_officeart_records'):
        reader.salvage_data_uncertain = True
    reader.issue({'code': 'note_drawing_retained_opaque',
                  'sheet': state.bound['name'],
                  'record_offset': location.get('record_offset'),
                  'object_count': report['object_count'],
                  'comment_count': len(accepted),
                  'message': 'Framed note objects retained raw; unknown '
                             'OfficeArt records, when present, are listed in '
                             'note_drawings. Decoded comment text and author '
                             'are returned in sheets[].comments and are not '
                             'written into cell values. No formulas or '
                             'external content executed.'})
    return stop


def _handle_object(reader, state, records, index):
    """Retain one Obj record and return the next index."""
    record = records[index]
    name = state.bound['name']
    report = obj_subrecords(record[2])
    for issue in report['issues']:
        reader.issue({'code': issue['code'], 'sheet': name,
                      'record_offset': record[0],
                      'record_id': hex(record[1]),
                      'message': issue['message']})
    for executable in report['executable_subrecords']:
        reader.issue({'code': 'object_macro_or_link_not_executed',
                      'sheet': name, 'record_offset': record[0],
                      'record_id': hex(record[1]),
                      'subrecord_name': executable,
                      'message': 'Executable/link object subrecord retained '
                                 'raw; never loaded, resolved or executed.'})
    object_type = report['object_type_raw']
    identity = report['object_id_raw']
    if object_type == OBJ_CHART_TYPE:
        if not identity or identity in state.object_ids:
            reader.issue({'code': 'duplicate_drawing_object_id',
                          'sheet': name, 'record_offset': record[0],
                          'object_type_raw': object_type,
                          'object_id': identity,
                          'message': 'Duplicate/invalid chart object ID; the '
                                     'following chart is retained raw only.'})
        else:
            state.object_ids.add(identity)
            state.chart_pending = identity
        retain(reader, record, 'ObjChartOpaque', name)
        return index + 1
    retain(reader, record, 'ObjOpaque', name)
    if object_type == OBJ_NOTE_TYPE:
        reader.issue({'code': 'note_object_without_drawing', 'sheet': name,
                      'record_offset': record[0],
                      'object_type_raw': object_type,
                      'cell_value_impact': 'unknown',
                      'message': 'Note object outside a validated note '
                                 'drawing retained raw; not interpreted.'})
    else:
        reader.salvage_data_uncertain = True
        reader.opaque_objects.append({'sheet': name,
                                      'record_offset': record[0],
                                      'object_id': identity,
                                      'object_type_raw': object_type})
        reader.issue({'code': 'unknown_drawing_object', 'sheet': name,
                      'record_offset': record[0],
                      'object_type_raw': object_type,
                      'object_id': identity,
                      'cell_value_impact': 'unknown',
                      'message': 'Object retained raw, not interpreted or '
                                 'executed; cell-value impact is unknown.'})
    if identity is not None and identity not in state.object_ids:
        state.object_ids.add(identity)
    return index + 1


def _handle_drawing(reader, state, records, start):
    """Consume one MsoDrawing group plus its optional Obj record."""
    name = state.bound['name']
    if not reader.drawing_group_seen:
        reader.issue({'code': 'drawing_without_group', 'sheet': name,
                      'record_offset': records[start][0],
                      'message': 'Worksheet drawing reached without a '
                                 'validated MsoDrawingGroup; retained raw.'})
    if reader.drawing_group_seen and _is_note_object(records, start + 1):
        stop = _handle_note_drawing(reader, state, records, start)
        if stop is not None:
            return stop
    next_index, joined, fragments, issues = officeart_fragments(records,
                                                                start)
    for issue in issues:
        reader.issue({**issue, 'sheet': name})
    for ordinal, fragment in enumerate(fragments):
        label = 'MsoDrawingOpaque' if ordinal == 0 \
            else 'MsoDrawingContinuationOpaque'
        retain(reader, fragment, label, name)
    if joined:
        root = officeart_root(joined)
        report = drawing_frame_report(joined, root)
        for issue in report['issues']:
            reader.issue({'code': issue['code'], 'sheet': name,
                          'record_offset': records[start][0],
                          'message': issue['message']})
        if report['unknown']:
            reader.salvage_data_uncertain = True
            reader.issue({'code': 'unknown_officeart_atoms', 'sheet': name,
                          'record_offset': records[start][0],
                          'records': report['unknown'][:256],
                          'cell_value_impact': 'unknown',
                          'message': 'Unknown OfficeArt records retained raw; '
                                     'object semantics and value impact are '
                                     'unverified.'})
    if next_index < len(records) and records[next_index][1] == 0x005D:
        return _handle_object(reader, state, records, next_index)
    return next_index


def _handle_chart(reader, state, records, start):
    """Retain an embedded chart body; never decode caches or execute."""
    name = state.bound['name']
    stop, report, issues = chart_records_end(records, start)
    if any(issue['code'] == 'embedded_chart_invalid' for issue in issues):
        reader.salvage_data_uncertain = True
    if report is None and stop <= start:
        for issue in issues:
            reader.issue({**issue, 'sheet': name})
        state.chart_pending = None
        return None
    for component in records[start:stop]:
        retain(reader, component, 'EmbeddedChartRecordOpaque', name)
    for issue in issues:
        reader.issue({**issue, 'sheet': name})
    if len(reader.embedded_charts) >= reader.limits['max_charts']:
        reader.issue({'code': 'embedded_chart_budget_exceeded', 'sheet': name,
                      'record_offset': records[start][0],
                      'message': 'Embedded chart count budget exhausted; '
                                 'remaining chart bytes retained raw only.'})
    else:
        if report is not None:
            report.update(sheet=name, object_id=state.chart_pending)
            reader.embedded_charts.append(report)
        reader.issue({'code': 'embedded_chart_retained_opaque', 'sheet': name,
                      'record_offset': records[start][0],
                      'payload_issue_count':
                          len(report.get('payload_issues', ()))
                          if report else 0,
                      'message': 'Chart/drawing bytes retained; chart '
                                 'semantics, caches and rendering are not '
                                 'decoded or verified'})
    state.chart_pending = None
    return stop


