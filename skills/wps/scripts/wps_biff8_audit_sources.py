"""Conservative Font, calculation-record and XF source associations for BIFF8.

Only interprets independently bounded fixed fields from metadata already retained
by wps_biff8. No effective fonts, styles, formula results or cache freshness.
"""
import struct


MAX_SOURCES = 4096


def font_sources(parsed):
    observed = []
    for item in parsed.get('uninterpreted_metadata', ()):
        if item.get('scope') != 'globals' or item.get('record_id') != '0x31':
            continue
        if len(observed) >= MAX_SOURCES:
            raise ValueError('Font source count exceeded')
        payload = bytes.fromhex(item['payload_hex'])
        record_index = len(observed)
        # The fourth FontIndex (4) is reserved in MS-XLS; physical fifth
        # Font record is addressed by FontIndex 5.
        font_index = record_index if record_index < 4 else record_index + 1
        source = {'font_index_raw': font_index,
                  'font_record_index': record_index,
                  'record_offset': item['offset'],
                  'source_kind': 'observed_biff8_font_record',
                  'effective_font': 'unknown'}
        if len(payload) < 16:
            source['status'] = 'uninterpreted_short_font_record'
        else:
            count, flag = payload[14], payload[15]
            end = 16 + count * (2 if flag == 1 else 1)
            if flag not in (0, 1) or end != len(payload):
                source['status'] = 'uninterpreted_font_name_framing'
            else:
                try:
                    name = payload[16:end].decode('utf-16le' if flag else 'latin1')
                except UnicodeDecodeError:
                    source['status'] = 'uninterpreted_font_name_encoding'
                else:
                    height, flags, color, weight = struct.unpack_from('<4H', payload)
                    source.update({'status': 'direct_font_record_fields',
                                   'name_raw': name,
                                   'height_twips_raw': height,
                                   'flags_raw': flags,
                                   'color_index_raw': color,
                                   'weight_raw': weight,
                                   'superscript_subscript_raw': struct.unpack_from('<H', payload, 8)[0],
                                   'underline_raw': payload[10],
                                   'family_raw': payload[11],
                                   'charset_raw': payload[12]})
        observed.append(source)
    return observed


def rich_font_references(rich_runs, fonts):
    by_index = {item['font_index_raw']: item for item in fonts}
    output = []
    for rich in rich_runs:
        refs = []
        for span in rich.get('spans', ()):
            index = span['font_index_raw']
            observed = by_index.get(index)
            refs.append({'font_index_raw': index,
                         'status': 'direct_font_record_reference'
                                   if observed and observed['status'] == 'direct_font_record_fields'
                                   else 'font_details_unresolved',
                         'font_record_index': observed['font_record_index'] if observed else None,
                         'font_record_offset': observed['record_offset'] if observed else None})
        output.append({'sst_index': rich['sst_index'], 'font_references': refs})
    return output


def calculation_metadata(parsed):
    """Retain calculation mode and recalc-engine IDs; never assert freshness."""
    items = []
    for source in parsed.get('uninterpreted_metadata', ()):
        record_id = source.get('record_id')
        if record_id not in ('0xd', '0x5f', '0x1c1'):
            continue
        if len(items) >= MAX_SOURCES:
            raise ValueError('Calculation record evidence budget exceeded')
        raw = bytes.fromhex(source['payload_hex'])
        entry = {'record_id': record_id, 'record_offset': source['offset'],
                 'scope': source['scope'], 'raw_hex': raw.hex(),
                 'status': 'uninterpreted',
                 'recalculation_executed': False}
        if record_id == '0xd' and len(raw) == 2:
            value = struct.unpack('<h', raw)[0]
            if value in (0, 1, 2):
                entry.update(status='saved_calculation_mode_only',
                             calculation_mode={0:'manual',1:'automatic',
                                               2:'automatic_except_tables'}[value])
        elif record_id == '0x5f' and len(raw) == 2:
            value = struct.unpack('<H', raw)[0]
            if value in (0, 1):
                entry.update(status='saved_recalc_on_save_flag_only',
                             saved_recalculate_before_save=bool(value))
        elif record_id == '0x1c1' and len(raw) == 8:
            tag, reserved, build = struct.unpack('<HHI', raw)
            if tag == 0x01c1 and reserved == 0:
                entry.update(status='last_engine_build_id_only',
                             engine_build_raw=build)
        items.append(entry)
    return {'records': items, 'calculation_settings_present': bool(items),
            'saved_caches_current': 'unknown', 'formulas_recalculated': False}


def xf_ancestry(parsed):
    """Report raw style-vs-cell type and observed direct parent only."""
    raw_records = [bytes.fromhex(item['raw_hex'])
                   for item in parsed.get('styles', ())]
    if len(raw_records) > MAX_SOURCES:
        raise ValueError('XF evidence budget exceeded')
    result = []
    for index, raw in enumerate(raw_records):
        if len(raw) != 20:
            raise ValueError('Invalid BIFF8 XF record width')
        identity = struct.unpack_from('<H', raw, 4)[0]
        style = bool(identity & 4)
        parent = (identity >> 4) & 0x0fff
        if style:
            status = ('style_xf_has_no_parent'
                      if parent == 0x0fff else 'nonstandard_style_parent_unresolved')
        elif parent < len(raw_records) and bool(struct.unpack_from('<H', raw_records[parent], 4)[0] & 4):
            status = 'observed_parent_style_xf_only'
        else:
            status = 'parent_style_xf_unresolved'
        result.append({'xf_index': index,
                       'xf_kind_raw': 'style' if style else 'cell',
                       'parent_xf_index_raw': parent,
                       'parent_reference_status': status,
                       'effective_style': 'unknown',
                       'style_inheritance_applied': False})
    return result
