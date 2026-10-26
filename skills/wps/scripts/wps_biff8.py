#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fail-closed MS-XLS BIFF8 record subset, independent of ET/ETT compatibility claims."""
import argparse
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_cfb import CompoundFile,MAX_STREAM_BYTES,COMPATIBILITY_MODES
from wps_visibility import SavedVisibility
from wps_biff8_headers import column_name, address, reconstruct
from wps_biff8_charts import CHART_POLICIES, drawing_frame, chart_end
from wps_numeric import interpret_cell_number

MAX_RECORDS=100000
MAX_CELLS=200000
MAX_MERGES=256
MAX_METADATA_BYTES=1024*1024
MAX_OUTPUT_TEXT_UNITS=8*1024*1024
ERRORS={0:'#NULL!',7:'#DIV/0!',15:'#VALUE!',23:'#REF!',29:'#NAME?',36:'#NUM!',42:'#N/A',43:'#GETTING_DATA'}
GLOBAL_METADATA={0x0042:'CodePage',0x003D:'Window1',0x0031:'Font',0x0293:'Style'}
SHEET_METADATA={0x0200:'Dimensions',0x0208:'Row',0x020B:'Index',0x00D7:'DBCell',0x023E:'Window2'}
# MS-XLS 2.3.2 record IDs. Opt-in raw retention, not semantic decoding or execution.
LEGACY_GLOBAL_METADATA={
    0x00E1:'InterfaceHdr',0x00C1:'Mms',0x00E2:'InterfaceEnd',0x005C:'WriteAccess',
    0x0161:'DSF',0x01C0:'Excel9File',0x013D:'RRTabId',0x0019:'WinProtect',
    0x0012:'Protect',0x0013:'Password',0x01AF:'Prot4Rev',0x01BC:'Prot4RevPass',
    0x0040:'Backup',0x008D:'HideObj',0x000E:'CalcPrecision',0x01B7:'RefreshAll',
    0x00DA:'BookBool',0x087C:'XFCRC',0x087D:'XFExt',0x0892:'StyleExt',
    0x0863:'BookExt',0x0896:'Theme',0x088C:'Compat12',0x088E:'TableStyles',
    0x0160:'UsesELFs',0x089A:'MTRSettings',0x008C:'Country',0x01C1:'RecalcId',
    0x00FF:'ExtSST'}
LEGACY_SHEET_METADATA={
    0x000D:'CalcMode',0x000C:'CalcCount',0x000F:'CalcRefMode',0x0011:'CalcIter',
    0x0010:'CalcDelta',0x005F:'CalcSaveRecalc',0x002A:'PrintRowCol',0x002B:'PrintGrid',
    0x0082:'GridSet',0x0080:'Guts',0x0225:'DefaultRowHeight',0x0014:'Header',
    0x0015:'Footer',0x0083:'HCenter',0x0084:'VCenter',0x0026:'LeftMargin',
    0x0027:'RightMargin',0x0028:'TopMargin',0x0029:'BottomMargin',0x00A1:'Setup',
    0x089C:'HeaderFooter',0x0055:'DefColWidth',0x007D:'ColInfo',0x088B:'PLV',
    0x001D:'Selection',0x0099:'DxGCol',0x0867:'FeatHdr',
    0x0012:'Protect',0x0013:'Password',0x00DD:'ScenarioProtect',
    0x0063:'ObjProtect',0x00A0:'Scl',0x0862:'SheetExt'}



class EncryptedError(ValueError):
    """An encrypted stream was identified; no decryption attempted."""
    status = "encrypted"

class ResourceLimitError(ValueError):
    """A bounded resource policy prevented extraction."""
    status = "resource_limit"

class UnsupportedError(ValueError):
    """A recognized record feature is outside the implemented subset."""
    status = "unsupported"

class ContinuedStrings:
    """MS-XLS Unicode characters and raw rich SST runs across Continue records.

    A fixed string header must stay in one record. A continuation of character
    data carries an encoding byte; a new string at a record boundary does not.
    UTF-16 units are assembled before decoding, so surrogate pairs may span records.
    """
    def __init__(self, records, index, position=0):
        self.records = records
        self.index = index
        self.position = position

    def advance(self):
        self.index += 1
        require(self.index < len(self.records) and self.records[self.index][1] == 0x003C,
                'Missing Continue record after string data')
        self.position = 0
        require(bool(self.records[self.index][2]), 'Empty Continue record')

    def string(self, allow_rich=False, metadata_budget=None):
        try:
            return self._string(allow_rich, metadata_budget)
        except ValueError as exc:
            # Add physical context without changing the expected failure class.
            # Programming errors (IndexError, TypeError, etc.) are not swallowed.
            index = min(self.index, len(self.records)-1)
            offset = self.records[index][0]
            raise type(exc)(f'{exc}; Workbook record_offset={offset} '
                            f'payload_offset={self.position}') from exc

    def _string(self, allow_rich=False, metadata_budget=None):
        self.last_rich = None
        payload = self.records[self.index][2]
        if self.position == len(payload):
            self.advance()
            payload = self.records[self.index][2]
        require(len(payload)-self.position >= 3, 'String fixed header crosses record boundary')
        count, flag = struct.unpack_from('<HB', payload, self.position)
        require(count <= 32767, 'String count exceeds 32767')
        if flag not in ((0, 1, 8, 9) if allow_rich else (0, 1)):
            raise UnsupportedError('Phonetic/rich-cache/unknown string flags unsupported')
        start = self.position
        record_offset = self.records[self.index][0]
        self.position += 3
        run_count = 0
        if flag & 8:
            require(len(payload)-self.position >= 2, 'Rich string fixed header crosses record boundary')
            run_count = struct.unpack_from('<H', payload, self.position)[0]
            self.position += 2
            require(run_count > 0, 'Rich string has no format runs')
            budget = MAX_METADATA_BYTES if metadata_budget is None else metadata_budget
            if run_count * 4 > min(MAX_METADATA_BYTES, budget):
                raise ResourceLimitError('Rich string metadata budget exceeded before read')
        flag &= 1
        remaining = count
        units = bytearray()
        while remaining:
            payload = self.records[self.index][2]
            if self.position == len(payload):
                self.advance()
                payload = self.records[self.index][2]
                flag = payload[0]
                require(flag in (0, 1), 'Invalid Continue encoding flag')
                self.position = 1
                require(self.position < len(payload), 'Continue has no character data')
            width = 2 if flag else 1
            available = len(payload)-self.position
            take = min(remaining, available//width)
            require(take > 0, 'UTF-16 character split within a Continue boundary')
            raw = payload[self.position:self.position+take*width]
            if flag:
                units.extend(raw)
            else:
                for byte in raw:
                    units.extend((byte, 0))
            self.position += take*width
            remaining -= take
            if remaining:
                require(self.position == len(payload), 'UTF-16 character split within a Continue boundary')
        try:
            value = units.decode('utf-16le')
        except UnicodeDecodeError as exc:
            raise ValueError('Malformed UTF-16 string across Continue records') from exc
        if run_count:
            # Unlike a continuation of characters, raw FormatRun bytes carry no
            # encoding flag. Never interpret a run byte as a character option.
            needed = run_count * 4
            runs = bytearray()
            while needed:
                payload = self.records[self.index][2]
                if self.position == len(payload):
                    self.advance()
                    payload = self.records[self.index][2]
                take = min(needed, len(payload)-self.position)
                runs.extend(payload[self.position:self.position+take])
                self.position += take
                needed -= take
            previous = -1
            for at in range(0, len(runs), 4):
                character, font = struct.unpack_from('<HH', runs, at)
                require(previous < character <= count, 'Rich FormatRun indexes must increase within string bounds')
                if character != count:
                    require(font != 4 and font <= 1022, 'Invalid rich string FontIndex')
                previous = character
            self.last_rich = {'record_offset': record_offset,
                              'payload_offset': start, 'utf16_units': count,
                              'run_count': run_count, 'runs_bytes': bytes(runs)}
        return value

    def finish(self):
        require(self.position == len(self.records[self.index][2]), 'Unparsed continued string tail')
        return self.index + 1


def require(condition,message):
    if not condition: raise ValueError(message)


def unicode_string(data,offset=0,short=False):
    prefix=2 if short else 3
    require(len(data)>=offset+prefix,'Truncated Unicode string header')
    count=data[offset] if short else struct.unpack_from('<H',data,offset)[0]
    flag=data[offset+prefix-1]
    require(count<=32767 and flag in (0,1),'Rich/phonetic/unknown string flags unsupported')
    start=offset+prefix; end=start+count*(2 if flag else 1)
    require(end<=len(data),'Truncated string; Continue records unsupported')
    try: value=data[start:end].decode('utf-16le' if flag else 'latin1')
    except UnicodeDecodeError as exc: raise ValueError('Malformed UTF-16 string') from exc
    return value,end


def number(raw):
    require(len(raw)==8,'Xnum must contain eight bytes')
    value=struct.unpack('<d',raw)[0]
    require(math.isfinite(value),'Nonfinite Xnum unsupported')
    return Decimal.from_float(value)


def rk_value(raw):
    require(len(raw)==4,'RK value must contain four bytes')
    bits=struct.unpack('<I',raw)[0]
    if bits&2:
        value=Decimal(struct.unpack('<i',raw)[0]>>2)
    else:
        value=number(struct.pack('<Q',(bits&0xFFFFFFFC)<<32))
    if bits&1:
        sign,digits,exponent=value.as_tuple(); value=Decimal((sign,digits,exponent-2))
    return value


def records(data):
    require(isinstance(data,bytes), 'BIFF stream must be bytes')
    if len(data) > MAX_STREAM_BYTES:
        raise ResourceLimitError('BIFF stream exceeds 8 MiB')
    output=[]; offset=0
    while offset<len(data):
        if len(output) >= MAX_RECORDS:
            raise ResourceLimitError('BIFF record count limit exceeded')
        require(len(data)-offset>=4,'Truncated BIFF record header')
        kind,size=struct.unpack_from('<HH',data,offset)
        require(size<=8224 and offset+4+size<=len(data),'Unsupported/oversized/truncated BIFF record payload')
        if kind==0x002F: raise EncryptedError('Encrypted BIFF FILEPASS unsupported; no decryption attempted')
        output.append((offset,kind,data[offset+4:offset+4+size])); offset+=4+size
    require(output and output[0][1]==0x0809,'Stream must start with BIFF8 BOF, not a filename hint')
    return output


def bof(record,expected):
    offset,kind,data=record
    require(kind==0x0809 and len(data)==16,'Expected 16-byte BIFF8 BOF at '+str(offset))
    version,substream=struct.unpack_from('<HH',data)
    require(version==0x0600 and substream==expected,'Unsupported BIFF version/substream; only BIFF8 globals and worksheets')
    return data.hex()


class Reader:
    def __init__(self,data,*,compatibility="strict",chart_policy="reject"):
        require(compatibility in COMPATIBILITY_MODES,"Unknown BIFF compatibility mode")
        require(chart_policy in CHART_POLICIES,"Unknown chart policy")
        require(chart_policy=="reject" or compatibility=="legacy_excel_clsid","Opaque chart retention requires explicit compatibility mode")
        self.chart_policy=chart_policy; self.embedded_charts=[]; self.drawing_group_seen=False
        self.compatibility=compatibility
        self.global_metadata={**GLOBAL_METADATA,**(LEGACY_GLOBAL_METADATA if compatibility=="legacy_excel_clsid" else {})}
        self.sheet_metadata={**SHEET_METADATA,**(LEGACY_SHEET_METADATA if compatibility=="legacy_excel_clsid" else {})}
        self.data=data; self.records=records(data); self.styles=[]; self.formats={}; self.strings=[]; self.rich_strings=[]; self.font_count=0
        self.metadata=[]; self.metadata_bytes=0; self.issues=[]; self.cell_count=0; self.merge_count=0; self.output_text_units=0

    def reserve_output_text(self, *values):
        # Charge every serialized cell occurrence, not merely SST unique strings.
        # Count UTF-16 units without constructing an encoded copy. Repeated custom
        # format strings are also materialized per cell and share this budget.
        units = sum(len(value) + sum(ord(char) > 0xFFFF for char in value)
                    for value in values if isinstance(value, str))
        if units > MAX_OUTPUT_TEXT_UNITS - self.output_text_units:
            raise ResourceLimitError('Repeated cell text exceeds output text budget')
        self.output_text_units += units

    def metadata_record(self,record,names,scope):
        offset,kind,payload=record
        self.metadata_bytes+=len(payload)
        require(self.metadata_bytes<=MAX_METADATA_BYTES and len(self.metadata)<4096,'Uninterpreted metadata budget exceeded')
        self.metadata.append({'scope':scope,'offset':offset,'record_id':hex(kind),'name':names[kind],'payload_hex':payload.hex()})

    def issue(self,issue):
        require(len(self.issues)<1000,'Diagnostic count exceeds 1000')
        self.issues.append(issue)

    def parse(self):
        bof(self.records[0],5)
        if self.compatibility=="legacy_excel_clsid":
            self.issue({"code":"known_metadata_retained_uninterpreted","message":"Explicit compatibility mode retains only allowlisted MS-XLS metadata bytes. Internal metadata semantics, protection, style extensions and calculation settings are not validated or applied; unknown/data extension records still fail."})
        bounds=[]; names=set(); date_system=None; seen_sst=False; global_end=None
        template_seen=False; self_link=False; internal_refs=[]; print_areas=set()
        consumed_until = 0
        for index,record in enumerate(self.records[1:],1):
            if index < consumed_until:
                continue
            offset,kind,payload=record
            if kind==0x000A:
                require(not payload,'EOF payload must be empty'); global_end=index+1; break
            if kind==0x0085:
                require(len(payload)>=8,'Truncated BoundSheet8')
                position=struct.unpack_from('<I',payload)[0]; hidden=payload[4]&3; sheet_type=payload[5]
                require(hidden in (0,1,2) and sheet_type==0,'Macro/chart/VBA BoundSheet8 types unsupported')
                name,end=unicode_string(payload,6,True)
                require(end==len(payload) and 1<=payload[6]<=31 and not any(char in name for char in '\0\x03:\\*?/[]') and not name.startswith("'") and not name.endswith("'"),'Invalid worksheet name')
                require(name.casefold() not in names and len(bounds)<128,'Duplicate sheet names or sheet count exceeds 128')
                names.add(name.casefold()); bounds.append({'name':name,'offset':position,'hidden_state':hidden})
            elif kind==0x0022:
                require(len(payload)==2 and date_system is None,'Duplicate/malformed Date1904')
                flag=struct.unpack('<H',payload)[0]; require(flag in (0,1),'Invalid Date1904 value'); date_system='1904' if flag else '1900'
            elif kind==0x00E0:
                require(len(payload)==20 and len(self.styles)<4096,'Malformed XF or style count exceeds 4096')
                self.styles.append({'xf_index':len(self.styles),'number_format_id_raw':struct.unpack_from('<H',payload,2)[0],'raw_hex':payload.hex()})
            elif kind==0x041E:
                require(len(payload)>=5,'Truncated Format')
                identity=struct.unpack_from('<H',payload)[0]; value,end=unicode_string(payload,2)
                require(end==len(payload) and 1<=len(value)<=255 and identity not in self.formats,'Malformed/duplicate number format')
                self.formats[identity]=value
            elif kind==0x00FC:
                require(not seen_sst and len(payload)>=8,'Duplicate/truncated SST'); seen_sst=True
                total,unique=struct.unpack_from('<ii',payload)
                require(0<=unique<=total and unique<=10000,'Invalid SST reference/unique counts')
                strings = ContinuedStrings(self.records, index, 8)
                for _ in range(unique):
                    self.strings.append(strings.string(allow_rich=True, metadata_budget=(MAX_METADATA_BYTES-self.metadata_bytes if len(self.rich_strings) < 4096 else 0)))
                    if strings.last_rich is not None:
                        rich = strings.last_rich
                        size = len(rich['runs_bytes'])
                        if size > MAX_METADATA_BYTES - self.metadata_bytes or len(self.rich_strings) >= 4096:
                            raise ResourceLimitError('Rich string metadata budget exceeded')
                        self.metadata_bytes += size
                        self.rich_strings.append({**rich, 'sst_index': len(self.strings)-1})
                consumed_until = strings.finish()
            elif self.compatibility=="legacy_excel_clsid" and kind==0x0060:
                require(not template_seen and not payload,'Duplicate/malformed Template marker')
                template_seen=True
                self.metadata_record(record,{kind:'Template'},'globals')
            elif self.compatibility=="legacy_excel_clsid" and kind==0x01AE:
                require(not self_link and len(payload)==4 and payload[2:]==b'\x01\x04',
                        'Only one self-referencing SupBook supported; external/add-in links refused')
                self_link=True
                self.metadata_record(record,{kind:'SupBookSelf'},'globals')
            elif self.compatibility=="legacy_excel_clsid" and kind==0x0017:
                require(self_link and not internal_refs and len(payload)>=2,'Missing self link or duplicate/truncated ExternSheet')
                count=struct.unpack_from('<H',payload)[0]
                require(1<=count<=128 and len(payload)==2+6*count,'Malformed/oversized ExternSheet')
                for start in range(2,len(payload),6):
                    link,first,last=struct.unpack_from('<Hhh',payload,start)
                    require(link==0 and 0<=first<=last<128,'Only valid internal sheet references supported')
                    internal_refs.append((first,last))
                self.metadata_record(record,{kind:'ExternSheetInternal'},'globals')
            elif self.compatibility=="legacy_excel_clsid" and kind==0x0018:
                # Retain only a static built-in Print_Area, never an executable name.
                require(len(payload)==27 and payload[:8]==b'\x20\x00\x00\x01\x0b\x00\x00\x00'
                        and payload[10:16]==b'\x00\x00\x00\x00\x00\x06' and payload[16]==0x3B,
                        'Only static built-in Print_Area names supported; macros/other names refused')
                sheet=struct.unpack_from('<H',payload,8)[0]
                ref,r1,r2,c1,c2=struct.unpack_from('<5H',payload,17)
                require(1<=sheet<=128 and sheet not in print_areas and ref<len(internal_refs)
                        and internal_refs[ref]==(sheet-1,sheet-1) and r1<=r2 and c1<=c2<256,
                        'Invalid/duplicate/nonlocal Print_Area reference')
                print_areas.add(sheet)
                self.metadata_record(record,{kind:'PrintAreaStatic'},'globals')
            elif kind==0x00EB and self.chart_policy=="opaque":
                require(not self.drawing_group_seen,'Duplicate drawing group unsupported')
                drawing_frame(payload,0xF000)
                self.drawing_group_seen=True
                self.metadata_record(record,{kind:'MsoDrawingGroupOpaque'},'globals')
            elif kind in self.global_metadata:
                if kind == 0x0031:
                    self.font_count += 1
                if kind==0x0042: require(len(payload)==2 and struct.unpack('<H',payload)[0]==1200,'Only Unicode CodePage 1200 accepted')
                self.metadata_record(record,self.global_metadata,'globals')
            else: raise UnsupportedError('Unsupported global record '+hex(kind)+' at '+str(offset))
        require(global_end is not None and bounds and self.styles and date_system is not None,'Require complete globals, at least one worksheet/XF and explicit Date1904')
        require(all(last<len(bounds) for first,last in internal_refs) and all(sheet<=len(bounds) for sheet in print_areas),
                'Internal/Print_Area reference exceeds actual worksheet count')
        if self.rich_strings:
            for rich in self.rich_strings:
                for at in range(0, len(rich['runs_bytes']), 4):
                    character, font = struct.unpack_from('<HH', rich['runs_bytes'], at)
                    if character != rich['utf16_units']:
                        require((font if font < 4 else font-1) < self.font_count,
                                'Rich string FontIndex exceeds global font collection')
            self.issue({'code': 'rich_string_formatting_not_applied',
                        'message': 'SST text extracted; FormatRun bytes retained once by SST index. Font rendering and layout are not applied or verified.'})
        offset_index={record[0]:index for index,record in enumerate(self.records)}
        require(len({item['offset'] for item in bounds})==len(bounds),'Aliased worksheet substreams')
        physical=sorted(bounds,key=lambda item:item['offset']); position=global_end; sheets={}
        for bound in physical:
            require(bound['offset'] in offset_index and offset_index[bound['offset']]==position,'Worksheet offset gap/overlap or unbound records')
            sheet,position=self.sheet(position,bound); sheets[bound['name']]=sheet
        require(position==len(self.records),'Trailing/unbound BIFF records are not ignored')
        require(not self.drawing_group_seen or self.embedded_charts,'Drawing group without supported embedded chart')
        for sheet in sheets.values():
            for cell in sheet['cells'].values():
                if cell['type'] == 'number':
                    cell['numeric_interpretation'] = interpret_cell_number(cell['value'], cell['number_format_id_raw'], cell['custom_format_raw'], date_system, self.styles[cell['xf_index']]['raw_hex'])
                    cell['numeric_interpretation']['cache_status'] = cell['cache_status']
        return {'status':'read_biff8_subset_with_diagnostics' if self.issues else 'read_biff8_subset',
                'schema_version':'biff8_readonly_v1','compatibility_mode':self.compatibility,'support_level':'biff8_strict_record_subset','biff_parsed':True,
                'chart_policy':self.chart_policy,'charts_decoded':False,'embedded_charts':self.embedded_charts,
                'native_et_ett_parsed':False,'wps_compatibility_verified':False,'formulas_executed':False,'external_links_followed':False,
                'date_system':date_system,'sheets':[sheets[item['name']] for item in bounds],
                'shared_string_formatting':[dict((key, value) for key, value in rich.items() if key != 'runs_bytes') | {'runs_raw_hex': rich['runs_bytes'].hex()} for rich in self.rich_strings],
                'styles':self.styles,'custom_number_formats':{str(key):value for key,value in self.formats.items()},
                'uninterpreted_metadata':self.metadata,'issues':self.issues,'record_count':len(self.records),'cell_count':self.cell_count,
                'policies':{'numbers':'Exact Decimal representation of stored IEEE754; raw bytes retained; no text coercion',
                            'dates':'All stored numeric serials remain numbers; raw XF/format IDs and custom strings retained; no ISO conversion or effective-style inheritance inference',
                            'formulas':'Opaque tokens/ancillary bytes only, never decompiled or executed; every present cache is unverified and may be stale',
                            'unknown_records':'Fail closed; known metadata is explicitly retained, not silently discarded',
                            'scope':'Record-subset extraction, not full MS-XLS grammar conformance or real ET/ETT acceptance'}}

    def sheet(self,index,bound):
        bof_raw=bof(self.records[index],0x0010); index+=1; cells={}; merges=[]; ws_bool=False; pending=None
        drawing_pending=False; chart_pending=None; object_ids=set()
        formula_groups={}; formula_refs=[]; last_formula=None
        visibility = SavedVisibility()
        def put(row,col,xf,kind,value,record,**extra):
            require(0<=row<65536 and 0<=col<256 and 0<=xf<len(self.styles),'Cell coordinate or XF index outside BIFF8 bounds')
            address=column_name(col+1)+str(row+1)
            require(address not in cells, 'Duplicate cell address')
            if self.cell_count >= MAX_CELLS:
                raise ResourceLimitError('Cell limit exceeded')
            fmt=self.styles[xf]['number_format_id_raw']
            self.reserve_output_text(value, self.formats.get(fmt))
            cells[address]={'type':kind,'value':value,'xf_index':xf,'number_format_id_raw':fmt,'custom_format_raw':self.formats.get(fmt),
                            'record_offset':record[0],'record_id':hex(record[1]),'cache_status':'not_applicable',**extra}
            self.cell_count+=1; return address
        while index<len(self.records):
            record=self.records[index]; offset,kind,payload=record; index+=1
            if pending is not None and kind not in (0x04BC, 0x0221):
                if kind==0x0207:
                    strings = ContinuedStrings(self.records, index-1)
                    value = strings.string()
                    index = strings.finish()
                    self.reserve_output_text(value)
                    cells[pending].update(type='text',value=value,cache_status='unverified',cache_record_offset=offset); pending=None; continue
                cells[pending].update(type='blank',value=None,cache_status='missing')
                self.issue({'sheet':bound['name'],'cell':pending,'code':'missing_formula_string_cache','expected':'String after Formula and optional group definition'}); pending=None
            if drawing_pending:
                require(kind==0x005D and len(payload)==26 and payload[:6]==b'\x15\x00\x12\x00\x05\x00'
                        and payload[10:]==bytes(16),'Only a chart Obj with FtCmo and FtEnd is supported')
                identity=struct.unpack_from('<H',payload,6)[0]
                require(identity and identity not in object_ids,'Duplicate/invalid chart object ID')
                object_ids.add(identity); chart_pending=identity; drawing_pending=False
                self.metadata_record(record,{kind:'ObjChartOpaque'},bound['name'])
                continue
            if chart_pending is not None:
                require(len(self.embedded_charts)<128,'Embedded chart count exceeds 128')
                stop,report=chart_end(self.records,index-1)
                for item in self.records[index-1:stop]:
                    self.metadata_record(item,{item[1]:'EmbeddedChartRecordOpaque'},bound['name'])
                report.update(sheet=bound['name'],object_id=chart_pending)
                self.embedded_charts.append(report)
                self.issue({'sheet':bound['name'],'code':'embedded_chart_retained_opaque',
                            'message':'Chart/drawing bytes retained; chart semantics, caches and rendering are not decoded or verified'})
                chart_pending=None; index=stop
                continue
            if kind==0x00EC and self.chart_policy=="opaque":
                require(self.drawing_group_seen,'Embedded drawing requires a drawing group')
                drawing_frame(payload,0xF002)
                self.metadata_record(record,{kind:'MsoDrawingOpaque'},bound['name'])
                drawing_pending=True
                continue
            if kind==0x000A:
                require(not payload and ws_bool,'Require empty EOF and exactly one non-dialog WsBool')
                counts = {anchor: 0 for anchor in formula_groups}
                for row, col, anchor, shared, address in formula_refs:
                    require(anchor in formula_groups, 'Missing shared/array formula anchor at '+address)
                    group = formula_groups[anchor]
                    first_row, last_row, first_col, last_col = group['bounds_zero_based']
                    require(first_row <= row <= last_row and first_col <= col <= last_col,
                            'Formula outside shared/array range at '+address)
                    require(shared == (group['kind'] == 'shared'), 'Formula shared flag/group mismatch at '+address)
                    counts[anchor] += 1
                    cells[address]['formula']['group_anchor'] = group['anchor']
                    cells[address]['formula']['group_kind'] = group['kind']
                for anchor, group in formula_groups.items():
                    require(counts[anchor] == group['expected_members'],
                            'Missing/conflicting shared/array formula members at '+group['anchor'])
                    group['observed_members'] = counts[anchor]
                return {**bound,'bof_raw_hex':bof_raw,'cells':cells,'merges':merges,
                        'formula_groups':list(formula_groups.values()), 'visibility':visibility.report()},index
            if kind==0x0081:
                require(not ws_bool and len(payload)==2 and not struct.unpack('<H',payload)[0]&0x0010,'Duplicate/malformed/dialog WsBool unsupported')
                ws_bool=True
            elif kind in (0x0203,0x027E,0x0205,0x0201,0x00FD,0x0006):
                require(len(payload)>=6,'Truncated cell record'); row,col,xf=struct.unpack_from('<3H',payload)
                if kind==0x0203:
                    require(len(payload)==14,'Number payload must be 14 bytes'); put(row,col,xf,'number',number(payload[6:]),record,raw_numeric_hex=payload[6:].hex())
                elif kind==0x027E:
                    require(len(payload)==10,'RK payload must be 10 bytes'); put(row,col,xf,'number',rk_value(payload[6:]),record,raw_numeric_hex=payload[6:].hex())
                elif kind==0x0201:
                    require(len(payload)==6,'Blank payload must be 6 bytes'); put(row,col,xf,'blank',None,record)
                elif kind==0x00FD:
                    require(len(payload)==10,'LabelSst payload must be 10 bytes'); string_index=struct.unpack_from('<I',payload,6)[0]
                    require(string_index<len(self.strings),'SST index outside table'); put(row,col,xf,'text',self.strings[string_index],record,sst_index=string_index)
                elif kind==0x0205:
                    require(len(payload)==8 and payload[7] in (0,1),'Malformed BoolErr')
                    if payload[7]:
                        require(payload[6] in ERRORS,'Unknown BErr code'); put(row,col,xf,'error',ERRORS[payload[6]],record,error_code=payload[6])
                    else:
                        require(payload[6] in (0,1),'Invalid boolean cell'); put(row,col,xf,'boolean',bool(payload[6]),record)
                else:
                    require(len(payload)>=23,'Truncated Formula')
                    flags=struct.unpack_from('<H',payload,14)[0]; count=struct.unpack_from('<H',payload,20)[0]
                    require(0<count<=len(payload)-22,'Malformed formula token length')
                    tokens=payload[22:22+count]
                    anchor=None
                    if tokens[0]&0x7f == 1:
                        if count != 5 or len(payload) != 27:
                            raise UnsupportedError('Only a single PtgExp without ancillary data is supported at '+str(offset))
                        anchor=struct.unpack_from('<HH',tokens,1)
                        require(anchor[1]<256,'Invalid PtgExp anchor column at '+str(offset))
                    require(not flags&8 or anchor is not None,'Shared Formula requires PtgExp at '+str(offset))
                    formula={'encoding':'opaque_biff8_ptgs','tokens_hex':payload[22:22+count].hex(),'ancillary_hex':payload[22+count:].hex(),
                             'flags_raw':flags,'needs_recalculation_flag':bool(flags&1),'cache_raw_hex':payload[6:14].hex(),'cache_may_be_stale':True}
                    raw=payload[6:14]; cache_status='unverified'; error_code=None
                    if raw[6:8]!=b'\xff\xff': value=number(raw); cell_type='number'
                    else:
                        cache_type=raw[0]
                        if cache_type==0: value=None; cell_type='blank'; cache_status='pending_string'
                        elif cache_type==1:
                            require(raw[2] in (0,1),'Invalid boolean formula cache'); value=bool(raw[2]); cell_type='boolean'
                        elif cache_type==2:
                            require(raw[2] in ERRORS,'Unknown cached error code'); value=ERRORS[raw[2]]; cell_type='error'; error_code=raw[2]; cache_status='error_unverified'
                        elif cache_type==3: value=''; cell_type='text'; cache_status='unverified_empty_string'
                        else: raise ValueError('Unknown special FormulaValue type')
                    address=put(row,col,xf,cell_type,value,record,formula=formula,error_code=error_code)
                    cells[address]['cache_status']=cache_status
                    if cache_status=='pending_string': pending=address
                    last_formula=(row,col,address,anchor,bool(flags&8),index-1)
                    if anchor is not None: formula_refs.append((row,col,anchor,bool(flags&8),address))
            elif kind in (0x04BC, 0x0221):
                # MS-XLS FORMULA permits a group definition before its String cache.
                require(last_formula is not None and last_formula[5] == index-2,
                        'Shared/array definition must immediately follow Formula at '+str(offset))
                row,col,address,anchor,shared,_=last_formula
                group_kind='shared' if kind == 0x04BC else 'array'
                require(anchor == (row,col) and shared == (group_kind == 'shared'),
                        'Shared/array definition anchor or flag mismatch at '+str(offset))
                prefix=8 if group_kind == 'shared' else 12
                require(len(payload)>=prefix+3, 'Truncated shared/array definition at '+str(offset))
                r1,r2,c1,c2=struct.unpack_from('<HHBB',payload)
                require(r1<=r2 and c1<=c2 and r1<=row<=r2 and c1<=col<=c2,
                        'Invalid shared/array range at '+str(offset))
                if group_kind == 'array':
                    require((row,col)==(r1,c1),'Array anchor must be top-left at '+str(offset))
                area=(r2-r1+1)*(c2-c1+1)
                if area>MAX_CELLS or len(formula_groups)>=1024:
                    raise ResourceLimitError('Shared/array range or group budget exceeded at '+str(offset))
                expected=payload[7] if group_kind == 'shared' else area
                require(0<expected<=area,'Invalid shared formula cUse at '+str(offset))
                require(anchor not in formula_groups,'Duplicate formula group anchor at '+str(offset))
                for other in formula_groups.values():
                    a,b,c,d=other['bounds_zero_based']
                    if not (r2<a or b<r1 or c2<c or d<c1):
                        raise UnsupportedError('Overlapping shared/array bounding ranges unsupported at '+str(offset))
                count=struct.unpack_from('<H',payload,prefix)[0]
                require(0<count<=len(payload)-prefix-2,'Truncated shared/array tokens at '+str(offset))
                if len(payload)>MAX_METADATA_BYTES-self.metadata_bytes:
                    raise ResourceLimitError('Formula group metadata budget exceeded at '+str(offset))
                self.metadata_bytes+=len(payload)
                formula_groups[anchor]={'kind':group_kind,'anchor':address,
                    'bounds_zero_based':[r1,r2,c1,c2], 'expected_members':expected,
                    'record_offset':offset,'record_id':hex(kind),
                    'tokens_hex':payload[prefix+2:prefix+2+count].hex(),
                    'ancillary_hex':payload[prefix+2+count:].hex(),
                    'header_raw_hex':payload[:prefix].hex(), 'expression_decoded':False}
                self.issue({'sheet':bound['name'],'cell':address,'offset':offset,
                            'code':'formula_group_expression_not_decoded',
                            'message':'Saved cell caches and group pointers retained; expression/ancillary semantics and cache freshness are not verified. No recalculation.'})
            elif kind in (0x00BD,0x00BE):
                require(len(payload)>=6,'Truncated multi-cell record'); row,first=struct.unpack_from('<HH',payload); last=struct.unpack_from('<H',payload,len(payload)-2)[0]
                width=6 if kind==0x00BD else 2
                require(first<last<256 and len(payload)==6+(last-first+1)*width,'Malformed MulRk/MulBlank bounds/length')
                for col in range(first,last+1):
                    start=4+(col-first)*width; xf=struct.unpack_from('<H',payload,start)[0]
                    raw=payload[start+2:start+6] if kind==0x00BD else None
                    put(row,col,xf,'number' if raw is not None else 'blank',rk_value(raw) if raw is not None else None,record,raw_numeric_hex=None if raw is None else raw.hex())
            elif kind==0x00E5:
                require(len(payload)>=2,'Truncated MergeCells'); count=struct.unpack_from('<H',payload)[0]
                require(count<=1026 and len(payload)==2+8*count and self.merge_count+count<=MAX_MERGES,'Malformed/oversized merge record')
                for start in range(2,len(payload),8):
                    r1,r2,c1,c2=struct.unpack_from('<4H',payload,start)
                    require(r1<=r2 and c1<=c2<256 and (r1,c1)!=(r2,c2),'Invalid merge bounds')
                    for other in merges:
                        a,b,c,d=other['bounds_zero_based']
                        require(r2<a or b<r1 or c2<c or d<c1,'Overlapping merges unsupported')
                    merges.append({'range':column_name(c1+1)+str(r1+1)+':'+column_name(c2+1)+str(r2+1),
                                   'bounds_zero_based':[r1,r2,c1,c2],'record_offset':offset})
                    self.merge_count+=1
            elif kind in (0x0208, 0x007D, 0x009B, 0x009D, 0x009E):
                visibility.add(kind, payload, offset)
                self.metadata_record(record,{kind:'SavedVisibilityOrFilter'},bound['name'])
                if kind in (0x009B,0x009D,0x009E):
                    self.issue({'sheet':bound['name'],'offset':offset,'code':'saved_filter_not_evaluated','message':'Saved filter metadata retained; conditions not decoded or applied, hidden cause unknown. Default includes every row.'})
            elif kind in self.sheet_metadata: self.metadata_record(record,self.sheet_metadata,bound['name'])
            else: raise UnsupportedError('Unsupported worksheet record '+hex(kind)+' at '+str(offset))
        raise ValueError('Truncated worksheet without EOF')


def parse_stream(data,*,compatibility="strict",chart_policy="reject"):
    return Reader(data,compatibility=compatibility,chart_policy=chart_policy).parse()


def read_file(path,*,compatibility="strict",chart_policy="reject"):
    container=CompoundFile.load(path,compatibility=compatibility)
    return read_container(container, path, compatibility=compatibility, chart_policy=chart_policy)


def read_container(container, path=None, *, compatibility='strict', chart_policy='reject'):
    if container.encrypted:
        raise EncryptedError('Encrypted compound workbook unsupported')
    require(not any(part.casefold() in ('vba','_vba_project_cur','macros') for stream in container.paths for part in stream),'Macro storage unsupported')
    candidates=[stream for stream in container.streams if len(stream)==1 and stream[0].casefold() in ('workbook','book')]
    require(len(candidates)==1,'Require exactly one root Workbook/Book stream; name is only a candidate until BOF validation')
    content=container.read_stream(candidates[0]); result=parse_stream(content,compatibility=compatibility,chart_policy=chart_policy)
    return {**result,'source':{'file':str(path) if path is not None else None,'extension':Path(path).suffix.lower() if path is not None else '','stream_path':list(candidates[0]),
                             'stream_sha256':hashlib.sha256(content).hexdigest(),'container_version':container.major,'container_compatibility':container.compatibility,'container_issues':container.issues},
            'other_container_streams_not_decoded':[item for item in container.report()['streams'] if item['path']!=list(candidates[0])]}



def reconstruct_headers(result,sheet,cell_range,header_rows,formulas='reject'):
    require(result.get('schema_version')=='biff8_readonly_v1','Require native BIFF8 subset result')
    require(formulas in ('reject','cached'),'Header formulas policy must be reject/cached')
    choices=[item for item in result['sheets'] if item['name']==sheet]
    require(len(choices)==1,'Unknown BIFF worksheet')
    require(isinstance(cell_range,str),'Explicit A1 range required for BIFF header adapter')
    ends=cell_range.split(':'); require(len(ends) in (1,2),'Invalid BIFF header range')
    first_row,first_col=address(ends[0]); last_row,last_col=address(ends[-1])
    require(1<=first_row<=last_row<=65536 and 1<=first_col<=last_col<=256 and (last_row-first_row+1)*(last_col-first_col+1)<=MAX_CELLS,'BIFF header range exceeds bounds/cell budget')
    data=choices[0]; cells=data['cells']
    def read_cell(coordinate):
        cell=cells.get(coordinate,{'type':'blank','value':None})
        if cell.get('formula') is not None:
            require(formulas=='cached','Formula header requires explicit cached opt-in; caches unverified')
            require(cell['cache_status'] not in ('missing','error_unverified'),'Missing/error formula header cache cannot form a path')
        require(cell['type']!='error','Error cell cannot form a header path')
        return cell['type'],cell['value']
    schema=reconstruct(SimpleNamespace(cells={sheet:cells}),sheet,(first_row,first_col,last_row,last_col),header_rows,read_cell,
                       merge_nodes=[{'ref':item['range']} for item in data['merges']])
    schema['source_format']='BIFF8 strict subset'; schema['formula_policy']=formulas
    schema['cache_warning']='Any formula cache used is unverified and may be stale' if formulas=='cached' else None
    return schema


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('input')
    parser.add_argument('--sheet'); parser.add_argument('--range',dest='cell_range'); parser.add_argument('--header-rows')
    parser.add_argument('--header-formulas',choices=('reject','cached'),default='reject')
    parser.add_argument('--compatibility',choices=COMPATIBILITY_MODES,default='strict')
    parser.add_argument('--chart-policy',choices=CHART_POLICIES,default='reject'); args=parser.parse_args()
    try:
        result=read_file(args.input,compatibility=args.compatibility,chart_policy=args.chart_policy)
        if args.header_rows is not None:
            require(args.sheet is not None and args.cell_range is not None,'Header reconstruction requires explicit sheet/range/header-rows')
            result['header_schema']=reconstruct_headers(result,args.sheet,args.cell_range,args.header_rows,args.header_formulas)
        elif args.sheet is not None or args.cell_range is not None or args.header_formulas!='reject':
            raise ValueError('Header options require explicit header-rows')
        from read_wps import encode_result
        encoded=encode_result(result)
        print(encoded); return 0
    except (ValueError,OSError,struct.error,OverflowError) as exc:
        print(json.dumps({'status':'invalid_or_unsupported','message':str(exc),'biff_parsed':False,'native_et_ett_parsed':False},ensure_ascii=False)); return 1


if __name__=='__main__': sys.exit(main())
