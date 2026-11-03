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
MAX_STREAM_BYTES=8*1024*1024
MAX_DIRECTORY_ENTRIES=4096
MAX_DEPTH=32
LEGACY_EXCEL_CLSID=bytes.fromhex("2008020000000000C000000000000046")
LEGACY_WORD_CLSID=bytes.fromhex("0609020000000000C000000000000046")
COMPATIBILITY_MODES=("strict","legacy_excel_clsid","legacy_word_clsid")


class CompoundFile:
    def __init__(self,data,*,compatibility="strict"):
        if compatibility not in COMPATIBILITY_MODES: raise ValueError("Unknown CFB compatibility mode")
        self.compatibility=compatibility; self.issues=[]
        if not isinstance(data,bytes) or len(data)>MAX_FILE_BYTES:
            raise ValueError('CFB input exceeds 32 MiB or is not bytes')
        self.data=data; self.used={}; self.mini_used=set(); self.paths={}; self.streams={}
        if len(data)<512 or data[:8]!=SIGNATURE: raise ValueError('Missing/truncated CFB header')
        self.major=self.u16(26); shift=self.u16(30)
        if (self.major,shift) not in ((3,9),(4,12)) or self.u16(28)!=0xFFFE or self.u16(32)!=6:
            raise ValueError('Unsupported CFB version/sector shifts/byte order')
        self.sector_size=1<<shift; self.sector_count=len(data)//self.sector_size-1
        self.header_clsid=data[8:24]
        if self.header_clsid!=bytes(16):
            expected_clsid = {"legacy_excel_clsid": LEGACY_EXCEL_CLSID, "legacy_word_clsid": LEGACY_WORD_CLSID}.get(compatibility)
            if self.header_clsid != expected_clsid or self.major != 3:
                raise ValueError("Nonzero CFB header CLSID outside strict subset; only an explicit exact legacy Excel/Word profile accepts its observed GUID in v3")
            self.issues.append({"code":"nonstandard_header_clsid","raw_hex":self.header_clsid.hex(),
                                "message":"Observed exact legacy header GUID; violates MS-CFB zero-header-CLSID rule. Not proof of WPS producer or record format."})
        if data[34:40]!=bytes(6):
            raise ValueError('Nonzero reserved CFB header bytes')
        if len(data)%self.sector_size:
            raise ValueError('CFB physical length is not sector aligned: remainder {} bytes'.format(
                len(data)%self.sector_size))
        if self.sector_count<2:
            raise ValueError('CFB file lacks required header, FAT and directory sectors')
        if any(data[512:self.sector_size]): raise ValueError('Nonzero version-4 header padding')
        if self.u32(56)!=4096: raise ValueError('Unsupported mini-stream cutoff')
        directory_count=self.u32(40); fat_count=self.u32(44); difat_count=self.u32(72)
        if self.major==3 and directory_count or self.major==4 and not 1<=directory_count<=MAX_DIRECTORY_ENTRIES*128//self.sector_size:
            raise ValueError('Invalid/oversized directory sector count')
        if not 1<=fat_count<=512 or difat_count>16: raise ValueError('FAT/DIFAT count exceeds bounded reader limits')
        fat_ids=list(struct.unpack_from('<109I',data,76)); difat_ids=[]; current=self.u32(68)
        for _ in range(difat_count):
            self.claim(current,'DIFAT'); difat_ids.append(current)
            values=struct.unpack('<'+'I'*(self.sector_size//4),self.sector(current))
            fat_ids.extend(values[:-1]); current=values[-1]
        if current!=END: raise ValueError('DIFAT count/termination mismatch or cycle')
        if len(fat_ids)<fat_count or any(value==FREE for value in fat_ids[:fat_count]) or any(value!=FREE for value in fat_ids[fat_count:]):
            raise ValueError('DIFAT FAT-location count/padding mismatch')
        fat_ids=fat_ids[:fat_count]
        for identifier in fat_ids: self.claim(identifier,'FAT')
        self.fat=[]
        for identifier in fat_ids: self.fat.extend(struct.unpack('<'+'I'*(self.sector_size//4),self.sector(identifier)))
        if len(self.fat)<self.sector_count: raise ValueError('FAT does not cover physical sectors')
        unbacked = next((self.sector_count + i
                         for i, value in enumerate(self.fat[self.sector_count:])
                         if value != FREE), None)
        if unbacked is not None:
            raise ValueError('FAT allocates sectors beyond physical input (first unbacked sector {})'.format(
                unbacked))
        if any(self.fat[identifier]!=FAT_SECTOR for identifier in fat_ids) or any(self.fat[identifier]!=DIFAT_SECTOR for identifier in difat_ids):
            raise ValueError('FAT/DIFAT sector type marker mismatch')
        directory=self.regular(self.u32(48),None,'directory',MAX_DIRECTORY_ENTRIES*128)
        if self.major==4 and len(directory)!=directory_count*self.sector_size: raise ValueError('Directory declared size differs from chain')
        self.entries=[]
        for offset in range(0,len(directory),128):
            block=directory[offset:offset+128]; kind=block[66]
            if kind==0: self.entries.append(None); continue
            if kind not in (1,2,5) or block[67] not in (0,1): raise ValueError('Unsupported directory entry type/color')
            length=struct.unpack_from('<H',block,64)[0]
            if not 2<=length<=64 or length%2 or block[length-2:length]!=bytes(2): raise ValueError('Invalid directory name length/terminator')
            try: name=block[:length-2].decode('utf-16le')
            except UnicodeDecodeError as exc: raise ValueError('Malformed UTF-16 directory name') from exc
            if not name or any(char in name for char in ('\\','/',':','!','\0')): raise ValueError('Unsafe/invalid CFB directory name')
            left,right,child=struct.unpack_from('<3I',block,68); start=struct.unpack_from('<I',block,116)[0]; size=struct.unpack_from('<Q',block,120)[0]
            if size>MAX_FILE_BYTES or self.major==3 and size>>32: raise ValueError('Stream size exceeds reader limit')
            if kind==1 and (size or start): raise ValueError('Storage entry has nonzero stream fields')
            if kind==2 and child!=FREE: raise ValueError('Stream entry has child directory links')
            self.entries.append({'name':name,'kind':kind,'left':left,'right':right,'child':child,'start':start,'size':size})
        if not self.entries or self.entries[0] is None or self.entries[0]['kind']!=5 or self.entries[0]['name']!='Root Entry':
            raise ValueError('Directory root must be entry zero Root Entry')
        root=self.entries[0]
        if root['left']!=FREE or root['right']!=FREE or root['size']%64: raise ValueError('Invalid root siblings/mini-stream alignment')
        self.tree(root['child'])
        mini_count=self.u32(64)
        if mini_count>self.sector_count: raise ValueError('MiniFAT count exceeds physical input')
        mini_bytes=self.regular(self.u32(60),mini_count*self.sector_size,'mini FAT',MAX_FILE_BYTES)
        self.mini_fat=list(struct.unpack('<'+'I'*(len(mini_bytes)//4),mini_bytes)) if mini_bytes else []
        self.mini_stream=self.regular(root['start'],root['size'],'root mini stream',MAX_FILE_BYTES)
        total=0
        for path,entry in self.paths.items():
            if entry['kind']!=2: continue
            if entry['size']>MAX_STREAM_BYTES: raise ValueError('User stream exceeds 8 MiB limit')
            total+=entry['size']
            if total>MAX_FILE_BYTES: raise ValueError('Aggregate stream size exceeds 32 MiB')
            content=self.mini(entry['start'],entry['size']) if 0<entry['size']<4096 else self.regular(entry['start'],entry['size'],'stream '+repr(path),MAX_STREAM_BYTES)
            self.streams[path]=content
        if any(value!=FREE and index not in self.used for index,value in enumerate(self.fat[:self.sector_count])):
            raise ValueError('Unreferenced allocated physical sector')
        if any(value!=FREE and index not in self.mini_used for index,value in enumerate(self.mini_fat)):
            raise ValueError('Unreferenced or out-of-range allocated mini sector')
        if self.issues and compatibility == "legacy_excel_clsid" and not any(len(path)==1 and path[0].casefold() in ("workbook","book") for path in self.streams):
            raise ValueError("Legacy Excel CLSID mode requires a root Workbook/Book stream; content encoding remains unverified")
        if compatibility == "legacy_word_clsid":
            word = self.streams.get(('WordDocument',))
            if (self.major != 3 or word is None or len(word) < 2 or word[:2] != b'\xec\xa5'
                    or any(len(path) == 1 and path[0].casefold() in ('workbook', 'book') for path in self.streams)):
                raise ValueError("Legacy Word profile requires v3 MS-DOC WordDocument and no workbook payload")
        self.encrypted=any(path[-1].casefold() in ('encryptioninfo','encryptedpackage') for path in self.paths)

    @classmethod
    def load(cls,path,*,compatibility="strict"):
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

    def u16(self,offset): return struct.unpack_from('<H',self.data,offset)[0]
    def u32(self,offset): return struct.unpack_from('<I',self.data,offset)[0]

    def sector(self,identifier):
        if type(identifier) is not int or not 0<=identifier<self.sector_count: raise ValueError('Sector pointer outside physical input')
        start=(identifier+1)*self.sector_size
        return self.data[start:start+self.sector_size]

    def claim(self,identifier,owner):
        self.sector(identifier)
        if identifier in self.used: raise ValueError('Sector chain cycle/cross-link/duplicate allocation')
        self.used[identifier]=owner

    def regular(self,start,size,owner,limit):
        if size is not None and size>limit: raise ValueError(owner+' exceeds byte budget')
        parts=[]; current=start; count=0
        expected=None if size is None else (size+self.sector_size-1)//self.sector_size
        while current!=END:
            if count>=limit//self.sector_size or expected is not None and count>=expected:
                raise ValueError(owner+' chain length/declared size exceeds limit')
            self.claim(current,owner); parts.append(self.sector(current)); count+=1
            current=self.fat[current]
        if expected is not None and count!=expected: raise ValueError(owner+' chain is shorter than declared size')
        result=b''.join(parts)
        return result if size is None else result[:size]

    def mini(self,start,size):
        expected=(size+63)//64; parts=[]; current=start
        for _ in range(expected):
            if not 0<=current<len(self.mini_fat) or (current+1)*64>len(self.mini_stream): raise ValueError('Mini sector pointer outside mini stream/FAT')
            if current in self.mini_used: raise ValueError('Mini sector chain cycle/cross-link')
            self.mini_used.add(current); parts.append(self.mini_stream[current*64:(current+1)*64]); current=self.mini_fat[current]
        if current!=END: raise ValueError('Mini chain length/termination differs from stream size')
        return b''.join(parts)[:size]

    def tree(self,child):
        visited={0}; names={}; stack=[(child,(),0)]
        while stack:
            identifier,parent,depth=stack.pop()
            if identifier==FREE: continue
            if depth>MAX_DEPTH: raise ValueError('Directory tree exceeds depth limit')
            if not 0<=identifier<len(self.entries) or identifier in visited or self.entries[identifier] is None: raise ValueError('Directory link cycle/shared/invalid entry')
            visited.add(identifier); entry=self.entries[identifier]
            if entry['kind']==5: raise ValueError('Multiple directory roots')
            key=(parent,entry['name'].casefold())
            if key in names: raise ValueError('Duplicate case-insensitive directory names')
            names[key]=identifier; path=parent+(entry['name'],); self.paths[path]=entry
            stack.extend([(entry['right'],parent,depth+1),(entry['left'],parent,depth+1)])
            if entry['kind']==1: stack.append((entry['child'],path,depth+1))
        if any(entry is not None and index not in visited for index,entry in enumerate(self.entries)):
            raise ValueError('Unreachable nonempty directory entries')

    def read_stream(self,path):
        if self.encrypted: raise ValueError('Encrypted compound payload unsupported; no decryption attempted')
        if not isinstance(path,(list,tuple)) or any(not isinstance(part,str) for part in path): raise ValueError('Stream path must be an explicit component list')
        try: return self.streams[tuple(path)]
        except KeyError as exc: raise ValueError('Unknown stream path') from exc

    def report(self):
        streams=[{'path':list(path),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()} for path,content in self.streams.items()]
        candidates=[list(path) for path in self.streams if len(path)==1 and path[0].casefold() in ('workbook','book')]
        return {'status':'encrypted_unsupported' if self.encrypted else 'container_readable','container':'compound_binary',
                'support_level':'cfb_container_only','compatibility_mode':self.compatibility,
                'header_clsid_raw_hex':self.header_clsid.hex(),'issues':self.issues,'major_version':self.major,'sector_size':self.sector_size,
                'directory_entries':sum(entry is not None for entry in self.entries),'streams':streams,
                'workbook_stream_candidates':candidates,'biff_parsed':False,'native_et_ett_parsed':False,
                'external_links_followed':False,'cells_decoded':False,
                'message':'CFB allocation and stream structure checked under bounded strict subset; stream names do not prove BIFF/ET format. No spreadsheet cells decoded.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('input')
    parser.add_argument("--compatibility",choices=COMPATIBILITY_MODES,default="strict")
    args=parser.parse_args()
    try:
        result=CompoundFile.load(args.input,compatibility=args.compatibility).report(); result['extension']=Path(args.input).suffix.lower()
        print(json.dumps(result,ensure_ascii=False)); return 0
    except (ValueError,OSError,struct.error) as exc:
        print(json.dumps({'status':'invalid_or_unsupported','support_level':'none','message':str(exc),
                          'biff_parsed':False,'native_et_ett_parsed':False,'cells_decoded':False},ensure_ascii=False)); return 1


if __name__=='__main__': sys.exit(main())
