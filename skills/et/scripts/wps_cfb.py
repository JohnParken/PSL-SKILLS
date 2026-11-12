#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded MS-CFB container reader; no BIFF or native ET/ETT cell decoding."""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import struct
import sys

SIGNATURE=bytes.fromhex('D0CF11E0A1B11AE1')
FREE=0xFFFFFFFF
END=0xFFFFFFFE
FAT_SECTOR=0xFFFFFFFD
DIFAT_SECTOR=0xFFFFFFFC
MAX_FILE_BYTES=32*1024*1024
# Content-first tolerance: the workbook stream itself may be large. The file
# budget above and the allocation checks below still bound total work.
MAX_STREAM_BYTES=32*1024*1024
MAX_DIRECTORY_ENTRIES=4096
MAX_DEPTH=32
LEGACY_EXCEL_CLSID=bytes.fromhex("2008020000000000C000000000000046")
LEGACY_WORD_CLSID=bytes.fromhex("0609020000000000C000000000000046")
COMPATIBILITY_MODES=("strict","legacy_excel_clsid","legacy_word_clsid")


class CompoundFile:
    """Bounded CFB reader with content-first tolerance.

    Invalid foundational allocation tables remain fatal. Stream-level pointer
    bounds, cycles and cross-links stop that chain before the invalid sector;
    already validated prefixes may be returned with issues. Producer quirks
    (nonstandard headers, orphan allocation, padded tails and oversized stream
    declarations) also produce diagnostics. Prefix recovery does not prove
    complete workbook values; downstream readers must preserve coverage risks.
    Compatibility arguments are accepted for API stability but no longer
    select a stricter reading policy.
    """

    def __init__(self, data, *, compatibility="data", recover_zero_fat_count=True,
                 allow_zero_trailing_padding=True, allow_trailing_remainder=True):
        self.compatibility=compatibility
        self.issues=[]
        if not isinstance(data,bytes) or len(data)>MAX_FILE_BYTES:
            raise ValueError('CFB input exceeds 32 MiB or is not bytes')
        self.data=data
        self.used={}
        self.mini_used=set()
        self.paths={}
        self.streams={}
        if len(data)<512 or data[:8]!=SIGNATURE:
            raise ValueError('Missing/truncated CFB header')
        self.major=self.u16(26)
        shift=self.u16(30)
        if (self.major,shift) not in ((3,9),(4,12)) or self.u16(28)!=0xFFFE or self.u16(32)!=6:
            raise ValueError('Unsupported CFB version/sector shifts/byte order')
        self.minor_version=self.u16(24)
        if self.minor_version!=0x003E:
            self.issues.append({"code":"cfb_nonstandard_minor_version","offset":24,
                                "declared":self.minor_version,"expected":0x003E,
                                "message":"Nonstandard CFB minor version retained as a diagnostic; major version and sector allocation are still validated."})
        self.sector_size=1<<shift
        self.sector_count=len(data)//self.sector_size-1
        self.header_clsid=data[8:24]
        if self.header_clsid!=bytes(16):
            self.issues.append({"code":"nonstandard_header_clsid","raw_hex":self.header_clsid.hex(),
                                "message":"Nonzero header CLSID retained as a producer diagnostic; CFB allocation and streams are still validated."})
        if data[34:40]!=bytes(6):
            self.issues.append({"code":"cfb_nonzero_reserved_header","offset":34,"raw_hex":data[34:40].hex(),
                                "message":"Nonzero reserved header bytes retained as a diagnostic; they do not affect sector allocation."})
        remainder = len(data) % self.sector_size
        if remainder:
            tail=data[-remainder:]
            zero=not any(tail)
            self.issues.append({
                'code':'cfb_zero_trailing_padding_ignored' if zero else 'cfb_trailing_remainder_ignored',
                'offset':len(data)-remainder,'padding_bytes':remainder,
                'message':'Bytes beyond the last complete sector cannot belong to any referenced sector chain; '
                          'retained as a diagnostic and ignored after full-sector allocation checks.'})
        self.physical_tail = data[len(data)-remainder:] if remainder else b''
        if self.sector_count<2:
            raise ValueError('CFB file lacks required header, FAT and directory sectors')
        if self.sector_size>512 and any(data[512:self.sector_size]):
            self.issues.append({"code":"cfb_nonzero_header_padding","offset":512,
                                "message":"Nonzero version-4 header padding retained as a diagnostic."})
        self.mini_cutoff=4096
        if self.u32(56)!=4096:
            self.issues.append({"code":"cfb_nonstandard_mini_cutoff","offset":56,
                                "declared":self.u32(56),"used":4096,
                                "message":"Nonstandard mini-stream cutoff retained as a diagnostic; the specified 4096-byte cutoff is used."})
        directory_count=self.u32(40)
        fat_count=self.u32(44)
        difat_count=self.u32(72)
        if self.major==3 and directory_count:
            self.issues.append({"code":"cfb_unexpected_directory_count","declared":directory_count,
                                "message":"Version-3 header declares a nonzero directory sector count; the directory chain is used instead."})
        elif self.major==4 and not 1<=directory_count<=MAX_DIRECTORY_ENTRIES*128//self.sector_size:
            self.issues.append({"code":"cfb_nonstandard_directory_count","declared":directory_count,
                                "message":"Version-4 directory count is outside the reader bound; the directory chain is used instead."})
        if fat_count == 0 and not recover_zero_fat_count:
            raise ValueError('CFB header declares zero FAT sectors')
        if fat_count>512 or difat_count>16:
            raise ValueError('FAT/DIFAT count exceeds bounded reader limits')
        fat_ids=list(struct.unpack_from('<109I',data,76))
        difat_ids=[]
        current=self.u32(68)
        for _ in range(difat_count):
            self.claim(current,'DIFAT')
            difat_ids.append(current)
            values=struct.unpack('<'+'I'*(self.sector_size//4),self.sector(current))
            fat_ids.extend(values[:-1])
            current=values[-1]
        if current!=END:
            self.issues.append({"code":"cfb_difat_termination_issue","offset":68,"terminal":current,
                                "message":"DIFAT chain does not terminate at ENDOFCHAIN; recovered FAT entries are used with a diagnostic."})
        if fat_count == 0:
            # DIFAT supplies explicit sector IDs. The normal checks below
            # still validate their order, padding, markers and allocation.
            fat_count = sum(identifier != FREE for identifier in fat_ids)
            if not fat_count:
                raise ValueError('CFB header declares zero FAT sectors and DIFAT lists none')
            if fat_count > 512:
                raise ValueError('FAT/DIFAT count exceeds bounded reader limits')
            self.issues.append({
                'code': 'cfb_zero_fat_count_recovered',
                'stream': 'CFB header', 'offset': 44,
                'declared_fat_count': 0, 'inferred_fat_count': fat_count,
                'message': 'CFB header declares zero FAT sectors; count recovered from validated DIFAT entries.',
            })
        if len(fat_ids)<fat_count or any(value==FREE for value in fat_ids[:fat_count]) or any(value!=FREE for value in fat_ids[fat_count:]):
            raise ValueError('DIFAT FAT-location count/padding mismatch')
        fat_ids=fat_ids[:fat_count]
        for identifier in fat_ids:
            self.claim(identifier,'FAT')
        self.fat=[]
        for identifier in fat_ids:
            self.fat.extend(struct.unpack('<'+'I'*(self.sector_size//4),self.sector(identifier)))
        if len(self.fat)<self.sector_count:
            self.issues.append({"code":"cfb_fat_short_cover","declared":len(self.fat),"sectors":self.sector_count,
                                "message":"FAT does not cover all physical sectors; missing entries are treated as unused."})
            self.fat.extend([FREE]*(self.sector_count-len(self.fat)))
        unbacked = next((self.sector_count + i
                         for i, value in enumerate(self.fat[self.sector_count:])
                         if value != FREE), None)
        if unbacked is not None:
            self.issues.append({"code":"cfb_fat_unbacked_allocation","sector":unbacked,
                                "message":"FAT marks a sector beyond physical input; streams that reference it are reported as truncated."})
        if any(self.fat[identifier]!=FAT_SECTOR for identifier in fat_ids) or any(self.fat[identifier]!=DIFAT_SECTOR for identifier in difat_ids):
            self.issues.append({"code":"cfb_sector_marker_mismatch",
                                "message":"FAT/DIFAT sector type marker mismatch retained as a diagnostic."})
        directory=self._tolerant_regular(self.u32(48),None,'directory',MAX_DIRECTORY_ENTRIES*128)
        if self.major==4 and len(directory)!=directory_count*self.sector_size:
            self.issues.append({"code":"cfb_directory_size_mismatch",
                                "message":"Directory sector count differs from the validated chain; available entries are read."})
        self.entries=[]
        for offset in range(0,len(directory),128):
            block=directory[offset:offset+128]
            kind=block[66]
            if kind==0:
                self.entries.append(None)
                continue
            if kind not in (1,2,5) or block[67] not in (0,1):
                self.issues.append({"code":"cfb_unsupported_directory_entry","offset":offset,
                                    "message":"Directory entry type/color outside the subset was ignored."})
                self.entries.append(None)
                continue
            length=struct.unpack_from('<H',block,64)[0]
            if not 2<=length<=64 or length%2 or block[length-2:length]!=bytes(2):
                self.issues.append({"code":"cfb_invalid_directory_name_length","offset":offset,"length":length,
                                    "message":"Directory entry with an invalid name length/terminator was ignored."})
                self.entries.append(None)
                continue
            try:
                name=block[:length-2].decode('utf-16le')
            except UnicodeDecodeError:
                self.issues.append({"code":"cfb_malformed_directory_name","offset":offset,
                                    "message":"Directory entry with malformed UTF-16 name was ignored."})
                self.entries.append(None)
                continue
            if not name or any(char in name for char in ('\\','/',':','!','\0')):
                self.issues.append({"code":"cfb_unsafe_directory_name","offset":offset,"raw_name":name,
                                    "message":"Directory entry name contains characters outside the safe subset; retained as raw bytes and not used for routing."})
            left,right,child=struct.unpack_from('<3I',block,68)
            start=struct.unpack_from('<I',block,116)[0]
            size=struct.unpack_from('<Q',block,120)[0]
            if size>MAX_FILE_BYTES or self.major==3 and size>>32:
                self.issues.append({"code":"cfb_stream_declared_size_clamped","offset":offset,"declared":size,
                                    "message":"Directory entry declares a stream size outside the reader budget; the readable prefix is retained."})
                size=min(size,MAX_FILE_BYTES)
            if kind==1 and (size or start):
                self.issues.append({"code":"cfb_storage_entry_with_stream_fields","offset":offset,
                                    "message":"Storage entry carries nonzero stream fields; retained as a diagnostic."})
            if kind==2 and child!=FREE:
                self.issues.append({"code":"cfb_stream_entry_with_child","offset":offset,
                                    "message":"Stream entry carries child directory links; links are ignored."})
            self.entries.append({'name':name,'kind':kind,'left':left,'right':right,'child':child,'start':start,'size':size})
        if not self.entries or self.entries[0] is None or self.entries[0]['kind']!=5:
            raise ValueError('Directory root must be entry zero Root Entry')
        root=self.entries[0]
        if root['name']!='Root Entry':
            self.issues.append({"code":"cfb_nonstandard_root_name","raw_name":root['name'],
                                "message":"Root directory entry uses a nonstandard name; entry zero is still treated as the root."})
        if root['left']!=FREE or root['right']!=FREE or root['size']%64:
            self.issues.append({"code":"cfb_root_entry_alignment_issue",
                                "message":"Root entry sibling/mini-stream alignment is nonstandard; validated mini chain data is used."})
        self.tree(root['child'])
        mini_count=self.u32(64)
        if mini_count>self.sector_count:
            self.issues.append({"code":"cfb_mini_fat_count_clamped","declared":mini_count,"used":self.sector_count,
                                "message":"MiniFAT count exceeds physical sectors; readable prefix is used."})
            mini_count=self.sector_count
        mini_bytes=self._tolerant_regular(self.u32(60),mini_count*self.sector_size,'mini FAT',MAX_FILE_BYTES)
        self.mini_fat=list(struct.unpack('<'+'I'*(len(mini_bytes)//4),mini_bytes)) if mini_bytes else []
        self.mini_stream=self._tolerant_regular(root['start'],root['size'],'root mini stream',MAX_FILE_BYTES)
        total=0
        for path,entry in self.paths.items():
            if entry['kind']!=2:
                continue
            size=entry['size']
            if size>MAX_STREAM_BYTES:
                self.issues.append({"code":"cfb_user_stream_truncated","stream":"/".join(path),"declared":size,
                                    "used":MAX_STREAM_BYTES,
                                    "message":"User stream exceeds the per-stream budget; only the bounded prefix is read."})
                size=MAX_STREAM_BYTES
            total+=size
            if total>MAX_FILE_BYTES:
                self.issues.append({"code":"cfb_aggregate_stream_budget_exceeded","stream":"/".join(path),
                                    "message":"Aggregate user stream budget exceeded; this and later streams are read only up to the remaining budget."})
            content=self._tolerant_mini(entry['start'],size) if 0<size<4096 else self._tolerant_regular(entry['start'],size,'stream '+repr(path),MAX_STREAM_BYTES)
            self.streams[path]=content
        unreferenced=[index for index,value in enumerate(self.fat[:self.sector_count]) if value!=FREE and index not in self.used]
        if unreferenced:
            self.issues.append({"code":"cfb_unreferenced_allocation","sectors":len(unreferenced),"first_sector":unreferenced[0],
                                "message":"Allocated physical sectors are not referenced by any validated chain; they are ignored."})
        bad_mini=[index for index,value in enumerate(self.mini_fat) if value!=FREE and index not in self.mini_used]
        if bad_mini:
            self.issues.append({"code":"cfb_unreferenced_mini_allocation","sectors":len(bad_mini),"first_sector":bad_mini[0],
                                "message":"Allocated mini sectors are not referenced by any validated chain; they are ignored."})
        self.encrypted=any(path[-1].casefold() in ('encryptioninfo','encryptedpackage') for path in self.paths)

    @classmethod
    def load(cls,path,*,compatibility="data"):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('CFB input must be a regular file')
            if before.st_size > MAX_FILE_BYTES:
                raise ValueError('Input exceeds 32 MiB before reading')
            # A bounded single read cannot follow a growing file indefinitely.
            with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                data = handle.read(before.st_size + 1)
            after = os.fstat(descriptor)
            if (len(data) != before.st_size or
                    (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('CFB input changed during read')
        finally:
            os.close(descriptor)
        return cls(data,compatibility=compatibility)

    def u16(self,offset):
        return struct.unpack_from('<H',self.data,offset)[0]
    def u32(self,offset):
        return struct.unpack_from('<I',self.data,offset)[0]

    def sector(self,identifier):
        if type(identifier) is not int or not 0<=identifier<self.sector_count:
            raise ValueError('Sector pointer outside physical input')
        start=(identifier+1)*self.sector_size
        return self.data[start:start+self.sector_size]

    def claim(self,identifier,owner):
        self.sector(identifier)
        if identifier in self.used:
            raise ValueError('Sector chain cycle/cross-link/duplicate allocation')
        self.used[identifier]=owner

    def regular(self,start,size,owner,limit):
        if size is not None and size>limit:
            raise ValueError(owner+' exceeds byte budget')
        parts=[]
        current=start
        count=0
        expected=None if size is None else (size+self.sector_size-1)//self.sector_size
        while current!=END:
            if count>=limit//self.sector_size or expected is not None and count>=expected:
                raise ValueError(owner+' chain length/declared size exceeds limit')
            self.claim(current,owner)
            parts.append(self.sector(current))
            count+=1
            current=self.fat[current]
        if expected is not None and count!=expected:
            raise ValueError(owner+' chain is shorter than declared size')
        result=b''.join(parts)
        return result if size is None else result[:size]

    def mini(self,start,size):
        expected=(size+63)//64
        parts=[]
        current=start
        for _ in range(expected):
            if not 0<=current<len(self.mini_fat) or (current+1)*64>len(self.mini_stream):
                raise ValueError('Mini sector pointer outside mini stream/FAT')
            if current in self.mini_used:
                raise ValueError('Mini sector chain cycle/cross-link')
            self.mini_used.add(current)
            parts.append(self.mini_stream[current*64:(current+1)*64])
            current=self.mini_fat[current]
        if current!=END:
            raise ValueError('Mini chain length/termination differs from stream size')
        return b''.join(parts)[:size]

    def _tolerant_regular(self,start,size,owner,limit):
        """Read a validated stream prefix, recording allocation defects as issues."""
        if size is not None and size>limit:
            self.issues.append({"code":"cfb_stream_over_budget","owner":owner,"declared":size,"used":limit,
                                "message":"Stream declaration exceeds the reader budget; a bounded prefix is read."})
            size=limit
        parts = []
        current = start
        count = 0
        expected=None if size is None else (size+self.sector_size-1)//self.sector_size
        while current!=END:
            if count>=limit//self.sector_size or expected is not None and count>=expected:
                self.issues.append({"code":"cfb_stream_chain_overrun","owner":owner,"sector":current,
                                    "message":"Stream chain exceeds its declared length; extra sectors were ignored."})
                break
            try:
                self.claim(current,owner)
            except ValueError as exc:
                self.issues.append({"code":"cfb_stream_chain_issue","owner":owner,"sector":current,
                                    "message":str(exc)+"; the readable prefix is retained."})
                break
            parts.append(self.sector(current))
            count += 1
            current=self.fat[current]
        if expected is not None and count!=expected:
            self.issues.append({"code":"cfb_stream_chain_shorter_than_declared","owner":owner,
                                "declared_sectors":expected,"read_sectors":count,
                                "message":"Stream chain is shorter/longer than the declared size; readable bytes are retained."})
        result=b''.join(parts)
        return result if size is None else result[:size]

    def _tolerant_mini(self,start,size):
        """Read a mini-stream prefix, recording mini-chain defects as issues."""
        expected=(size+63)//64
        parts = []
        current = start
        for _ in range(expected):
            if not 0<=current<len(self.mini_fat) or (current+1)*64>len(self.mini_stream):
                self.issues.append({"code":"cfb_mini_chain_issue","sector":current,
                                    "message":"Mini sector pointer is outside the mini stream/FAT; readable prefix retained."})
                break
            if current in self.mini_used:
                self.issues.append({"code":"cfb_mini_chain_issue","sector":current,
                                    "message":"Mini sector chain repeats a sector; readable prefix retained."})
                break
            self.mini_used.add(current)
            parts.append(self.mini_stream[current*64:(current+1)*64])
            current=self.mini_fat[current]
        result=b''.join(parts)
        return result[:size]

    def tree(self,child):
        visited={0}
        names={}
        stack=[(child,(),0)]
        while stack:
            identifier,parent,depth=stack.pop()
            if identifier==FREE:
                continue
            if depth>MAX_DEPTH:
                self.issues.append({"code":"cfb_directory_depth_limit","depth":depth,
                                    "message":"Directory tree exceeds the depth limit; remaining links were ignored."})
                continue
            if not 0<=identifier<len(self.entries) or identifier in visited or self.entries[identifier] is None:
                self.issues.append({"code":"cfb_directory_link_issue","entry":identifier,
                                    "message":"Directory link is out of range, repeated, or points at an ignored entry; ignored."})
                continue
            visited.add(identifier)
            entry=self.entries[identifier]
            if entry['kind']==5:
                self.issues.append({"code":"cfb_multiple_directory_roots","entry":identifier,
                                    "message":"Additional root-like directory entry ignored."})
                continue
            key=(parent,entry['name'].casefold())
            if key in names:
                self.issues.append({"code":"cfb_duplicate_directory_name","entry":identifier,"name":entry['name'],
                                    "message":"Duplicate case-insensitive directory name under the same parent; later entry ignored."})
                continue
            names[key]=identifier
            path=parent+(entry['name'],)
            self.paths[path]=entry
            stack.extend([(entry['right'],parent,depth+1),(entry['left'],parent,depth+1)])
            if entry['kind']==1:
                stack.append((entry['child'],path,depth+1))
        unreachable=[index for index,entry in enumerate(self.entries) if entry is not None and index not in visited]
        if unreachable:
            self.issues.append({"code":"cfb_unreachable_directory_entries","entries":len(unreachable),
                                "first_entry":unreachable[0],
                                "message":"Nonempty directory entries are unreachable from the root; they are ignored."})

    def read_stream(self,path):
        if self.encrypted:
            raise ValueError('Encrypted compound payload unsupported; no decryption attempted')
        if not isinstance(path,(list,tuple)) or any(not isinstance(part,str) for part in path):
            raise ValueError('Stream path must be an explicit component list')
        try:
            return self.streams[tuple(path)]
        except KeyError as exc:
            raise ValueError('Unknown stream path') from exc

    def report(self):
        streams=[{'path':list(path),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()} for path,content in self.streams.items()]
        candidates=[list(path) for path in self.streams if len(path)==1 and path[0].casefold() in ('workbook','book')]
        return {'status':'encrypted_unsupported' if self.encrypted else 'container_readable','container':'compound_binary',
                'support_level':'cfb_container_only','compatibility_mode':self.compatibility,
                'header_clsid_raw_hex':self.header_clsid.hex(),'issues':self.issues,'major_version':self.major,'sector_size':self.sector_size,
                'directory_entries':sum(entry is not None for entry in self.entries),'streams':streams,
                'workbook_stream_candidates':candidates,'biff_parsed':False,'native_et_ett_parsed':False,
                'external_links_followed':False,'cells_decoded':False,
                'message':'CFB allocation and stream structure checked under bounded content-first policy; stream names do not prove BIFF/ET format and no spreadsheet cells are decoded here.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument("--compatibility",choices=COMPATIBILITY_MODES,default="data",help="Deprecated no-op; all modes use the tolerant reader")
    args=parser.parse_args()
    try:
        result=CompoundFile.load(args.input,compatibility=args.compatibility).report()
        result['compatibility_option_ignored']=args.compatibility!='data'
        result['extension']=Path(args.input).suffix.lower()
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ValueError,OSError,struct.error) as exc:
        print(json.dumps({'status':'invalid_or_unsupported','support_level':'none','message':str(exc),
                          'biff_parsed':False,'native_et_ett_parsed':False,'cells_decoded':False},ensure_ascii=False))
        return 1


if __name__=='__main__':
    sys.exit(main())
