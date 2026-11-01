"""Independent maintenance-only BIFF8 *known record* cell census.

Does not import wps_biff8 or decode SST/Formula. Counts only exact known cell
record layouts from a separately supplied bounded Workbook stream.
Unknown cell kinds could be present: this is not full value certification.
"""
import struct

KNOWN_SINGLE_CELL = frozenset((0x0203,0x027E,0x0205,0x0201,0x00FD,0x0204,0x0006))


def count_known_cell_records(workbook_bytes):
    if not isinstance(workbook_bytes, bytes) or len(workbook_bytes) > 8 * 1024 * 1024:
        raise ValueError('Independent source stream exceeds bound')
    records = []
    offset = 0
    while offset < len(workbook_bytes):
        if len(records) >= 100000 or len(workbook_bytes)-offset < 4:
            raise ValueError('Independent BIFF record frame missing or over budget')
        kind,length = struct.unpack_from('<HH',workbook_bytes,offset)
        if length > 8224 or length + 4 > len(workbook_bytes)-offset:
            raise ValueError('Independent BIFF frame truncated or oversized')
        records.append((offset,kind,workbook_bytes[offset+4:offset+4+length]))
        offset += 4+length
    if not records or records[0][1] != 0x0809:
        raise ValueError('No BIFF globals BOF in independent input')
    sheet_offsets = []
    global_end = None
    for record_offset, kind, payload in records[1:]:
        if kind == 0x0085:
            if len(payload) < 4: raise ValueError('Truncated BoundSheet offset')
            sheet_offsets.append(struct.unpack_from('<I', payload)[0])
        elif kind == 0x000A:
            global_end = record_offset+4
            break
    if global_end is None or not sheet_offsets or len(sheet_offsets)>128:
        raise ValueError('No bounded globals or sheet pointers')
    by_start={t[0]:i for i,t in enumerate(records)}
    if any(start not in by_start for start in sheet_offsets):
        raise ValueError('Sheet pointer outside independently framed record')
    found_cells = []
    groups = []
    for source_offset in sheet_offsets:
        pos=by_start[source_offset]
        if records[pos][1] != 0x0809:
            raise ValueError('Worksheet does not start at BIFF BOF')
        found=set()
        counts=0
        first_end=False
        for _,kind,payload in records[pos+1:]:
            if kind == 0x000A:
                first_end=True
                break
            if kind == 0x0809:
                raise ValueError('Nested chart/substream outside independent census subset')
            if kind in KNOWN_SINGLE_CELL:
                if len(payload)<6: raise ValueError('Truncated known cell source')
                row,col=struct.unpack_from('<HH',payload,0)
                if col>=256:raise ValueError('Invalid known cell column')
                key=(row,col)
                if key in found:raise ValueError('Duplicate cell coordinate')
                found.add(key)
                counts+=1
            elif kind in (0x00BD,0x00BE):
                if len(payload)<6:raise ValueError('Truncated multi-cell source')
                row,first=struct.unpack_from('<HH',payload)
                last=struct.unpack_from('<H',payload,len(payload)-2)[0]
                width=6 if kind==0x00BD else 2
                if first>=last or last>=256 or len(payload)!=6+(last-first+1)*width:
                    raise ValueError('Multi-cell record length inconsistent')
                for col in range(first,last+1):
                    key=(row,col)
                    if key in found: raise ValueError('Duplicate multi-cell position')
                    found.add(key)
                    counts+=1
        if not first_end:
            raise ValueError('Worksheet no bounded EOF')
        groups.append(counts)
        found_cells.extend(found)
    return {'source_kind':'independent_known_cell_record_census',
            'sheets':len(groups),'known_record_cell_count':sum(groups),
            'per_sheet_known_record_cell_counts':groups,
            'all_possible_other_cell_records_excluded':False,
            'cell_values_and_format_verified':False}
