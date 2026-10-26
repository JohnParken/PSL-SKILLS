"""Explicitly synthetic MS-DOC/CFB fixtures, never WPS producer evidence.

FIB and CLX fixture constants are independent literals, not imported from the
parser's version table or offsets. Expected text and diagnostic offsets are fixed.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import ParseError, parse_streams, read

SIGNATURE = bytes.fromhex('d0cf11e0a1b11ae1')
FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD

def entry(name,kind,start=END,size=0,right=FREE,child=FREE):
    block=bytearray(128); name_bytes=name.encode('utf-16le')+bytes(2)
    block[:len(name_bytes)]=name_bytes; struct.pack_into('<HBB3I',block,64,len(name_bytes),kind,1,FREE,right,child)
    struct.pack_into('<IQ',block,116,start,size); return block


def synthetic_cfb(streams,major=3):
    # This builder implements only tiny test fixtures and is not a file writer feature.
    size=512 if major==3 else 4096; directory_count=(len(streams)+1+size//128-1)//(size//128)
    sectors=[bytearray(size) for _ in range(1+directory_count)]
    fat=[FREE]*(size//4); fat[0]=FAT_SECTOR
    for index in range(1,1+directory_count): fat[index]=index+1 if index<directory_count else END
    small={name:content for name,content in streams.items() if 0<len(content)<4096}
    mini_fat=[FREE]*(size//4); mini_blob=bytearray(); starts={}
    for name,content in small.items():
        start=len(mini_blob)//64; blocks=(len(content)+63)//64; starts[name]=start
        for index in range(start,start+blocks): mini_fat[index]=index+1 if index+1<start+blocks else END
        mini_blob.extend(content+bytes(blocks*64-len(content)))
    mini_id=END; root_start=END
    if small:
        mini_id=len(sectors); sectors.append(bytearray(struct.pack('<'+'I'*len(mini_fat),*mini_fat))); fat[mini_id]=END
        root_start=len(sectors); blocks=(len(mini_blob)+size-1)//size
        for offset in range(blocks):
            identifier=len(sectors); content=mini_blob[offset*size:(offset+1)*size]; sectors.append(bytearray(content+bytes(size-len(content))))
            fat[identifier]=identifier+1 if offset+1<blocks else END
    for name,content in streams.items():
        if len(content)>=4096:
            starts[name]=len(sectors); blocks=(len(content)+size-1)//size
            for offset in range(blocks):
                identifier=len(sectors); data=content[offset*size:(offset+1)*size]; sectors.append(bytearray(data+bytes(size-len(data))))
                fat[identifier]=identifier+1 if offset+1<blocks else END
        elif not content: starts[name]=END
    directory=entry('Root Entry',5,root_start,len(mini_blob),child=1 if streams else FREE)
    for index,(name,content) in enumerate(streams.items(),1):
        directory.extend(entry(name,2,starts[name],len(content),right=index+1 if index<len(streams) else FREE))
    directory.extend(bytes(directory_count*size-len(directory)))
    for index in range(directory_count): sectors[index+1]=directory[index*size:(index+1)*size]
    sectors[0]=bytearray(struct.pack('<'+'I'*len(fat),*fat))
    header=bytearray(size); header[:8]=SIGNATURE
    struct.pack_into('<5H',header,24,0x3E,major,0xFFFE,9 if major==3 else 12,6)
    struct.pack_into('<9I',header,40,directory_count if major==4 else 0,1,1,0,4096,mini_id,1 if small else 0,END,0)
    struct.pack_into('<109I',header,76,0,*([FREE]*108))
    return bytes(header+b''.join(sectors))


def document(parts=None, other_stories=None, table_one=False, prefix=b''):
    """Literal Word-97 layout. Raw UTF-16 chunks may split a surrogate pair.

    parts: (raw bytes, compressed bool), in logical order but physically reversed.
    other_stories: mapping of FIB offsets (80,84,92,96,100,104) to raw UTF-16.
    An additional separating/final CP is present when related stories exist.
    """
    parts = [(b'Hello ', True), ('中文\r第二段\r'.encode('utf-16le'), False)] if parts is None else parts
    related = other_stories or {}
    word = bytearray(4096)
    for offset, value in ((0, 0xA5EC), (2, 0xC1), (10, 0x1200 if table_one else 0x1000),
                          (12, 0xBF), (32, 14), (62, 22), (152, 93), (898, 0)):
        struct.pack_into('<H', word, offset, value)
    main_count = sum(len(raw) if compressed else len(raw) // 2 for raw, compressed in parts)
    struct.pack_into('<I', word, 76, main_count)
    all_parts = list(parts)
    for offset in (80, 84, 92, 96, 100, 104):
        raw = related.get(offset, b'')
        struct.pack_into('<I', word, offset, len(raw) // 2)
        if raw:
            all_parts.append((raw, False))
    if any(related.values()):
        all_parts.append((b'\r\x00', False))
    physical_offsets = []
    for raw, _ in reversed(all_parts):
        physical_offsets.insert(0, len(word))
        word.extend(raw)
    positions = [0]
    records = []
    for (raw, compressed), offset in zip(all_parts, physical_offsets):
        positions.append(positions[-1] + (len(raw) if compressed else len(raw) // 2))
        encoded = offset * 2 | 0x40000000 if compressed else offset
        records.append(struct.pack('<HIH', 0x0030, encoded, 0))
    plc = struct.pack('<' + 'I' * len(positions), *positions) + b''.join(records)
    clx = b'\x02' + struct.pack('<I', len(plc)) + plc
    struct.pack_into('<II', word, 24, 4096, len(word))
    struct.pack_into('<I', word, 64, len(word))
    struct.pack_into('<II', word, 418, len(prefix), len(clx))
    return {('WordDocument',): bytes(word), ('1Table' if table_one else '0Table',): prefix + clx}


def replace_word(streams, offset, value, width=4):
    changed = dict(streams)
    word = bytearray(changed[('WordDocument',)])
    struct.pack_into({1: '<B', 2: '<H', 4: '<I'}[width], word, offset, value)
    changed[('WordDocument',)] = bytes(word)
    return changed


def replace_clx(streams, clx):
    changed = dict(streams)
    table = '1Table' if struct.unpack_from('<H', changed[('WordDocument',)], 10)[0] & 0x200 else '0Table'
    changed[(table,)] = clx
    changed = replace_word(changed, 418, 0)
    return replace_word(changed, 422, len(clx))


class WpsTests(unittest.TestCase):
    def assert_parse_error(self, streams, status='corrupt', stream=None, offset=None):
        with self.assertRaises(ParseError) as ctx:
            parse_streams(streams)
        exc = ctx.exception
        self.assertEqual(exc.status, status)
        if stream is not None:
            self.assertEqual(exc.location['stream'], stream)
        if offset is not None:
            self.assertEqual(exc.location['offset'], offset)
        return exc

    def test_mixed_fragmented_chinese_and_selected_table(self):
        for one in (False, True):
            streams = document(table_one=one, prefix=b'not-a-clx')
            streams[('0Table' if one else '1Table',)] = b'unselected invalid bytes'
            result = parse_streams(streams)
            self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
            self.assertEqual(result['paragraphs'], ['Hello 中文', '第二段'])
            self.assertEqual(result['main_cp_units'], 13)
            self.assertEqual(result['fib_version'], '0xc1')

    def test_published_clx_example_with_independent_literal_offsets(self):
        # MS-DOC 3.1 Example of a Clx, synthesized from its published tables.
        # FIB scaffolding is synthetic; text, CPs, FCs, CLX location/length,
        # and expected text are independently transcribed specification values.
        word = bytearray(4096)
        for offset, value in ((0, 0xA5EC), (2, 0xC1), (10, 0x1000), (12, 0xC1),
                              (32, 14), (62, 22), (152, 93)):
            struct.pack_into('<H', word, offset, value)
        struct.pack_into('<I', word, 64, 3118)
        struct.pack_into('<I', word, 76, 14)
        struct.pack_into('<II', word, 418, 504, 45)
        word[3106:3118] = bytes.fromhex('480065006c006c006f002000')
        word[1024:1032] = b'World.\r\r'
        clx = bytes.fromhex(
            '02 28000000 00000000 06000000 0d000000 0e000000 '
            '3100 220c0000 0000 3000 00080040 0000 3000 0e080040 0000')
        self.assertEqual(len(clx), 45)
        result = parse_streams({('WordDocument',): word, ('0Table',): bytes(504) + clx})
        self.assertEqual(result['text'], 'Hello World.\n\n')
        self.assertEqual(result['paragraphs'], ['Hello World.', ''])
        self.assertEqual(result['main_cp_units'], 14)

    def test_newer_or_inconsistent_fib_layouts_are_refused(self):
        for version in (0, 0xBF, 0xFFFF):
            self.assert_parse_error(replace_word(document(), 2, version, 2), 'unsupported', 'WordDocument', 2)
        for version in (0xD9, 0x101, 0x10C, 0x112):
            self.assert_parse_error(replace_word(document(), 2, version, 2), 'unsupported', 'WordDocument', 152)
        for offset, value in ((32, 15), (62, 23), (152, 109)):
            self.assert_parse_error(replace_word(document(), offset, value, 2), 'unsupported', 'WordDocument', offset)
        self.assert_parse_error(replace_word(document(), 898, 2, 2), 'unsupported', 'WordDocument', 900)
        # An apparent nFibNew must not resurrect a newer version.
        streams = replace_word(document(), 900, 0x112, 2)
        streams = replace_word(streams, 898, 5, 2)
        self.assert_parse_error(streams, 'unsupported', 'WordDocument', 152)

    def test_fib_encryption_flags_key_and_platform(self):
        self.assertEqual(parse_streams(replace_word(document(), 10, 0x9000, 2))['text'], 'Hello 中文\n第二段\n')
        for flags in (0x1100, 0x9100):
            self.assert_parse_error(replace_word(document(), 10, flags, 2), 'encrypted', 'WordDocument', 10)
        self.assert_parse_error(replace_word(document(), 14, 1), 'corrupt', 'WordDocument', 14)
        for offset, value, width in ((8, 1, 2), (10, 0x1001, 2), (10, 0x1002, 2), (10, 0, 2), (18, 1, 1), (88, 1, 4)):
            self.assert_parse_error(replace_word(document(), offset, value, width), 'unsupported')

    def test_physical_text_bounds_and_ignored_reserved_fields(self):
        # MS-DOC FibBase reserved5/reserved6 MUST be ignored. Fixed expected
        # text is independent of these historical fcMin/fcMac-like values.
        for offset in (24, 28):
            for value in (0, 899, 4096, 0xFFFFFFFF):
                self.assertEqual(parse_streams(replace_word(document(), offset, value))['text'],
                                 'Hello 中文\n第二段\n')
        for value in (899, 0xFFFFFFFF):
            self.assert_parse_error(replace_word(document(), 64, value), 'corrupt', 'WordDocument', 64)
        # cbMac excludes bytes beyond it even when the stream itself is longer.
        self.assert_parse_error(replace_word(document(), 64, 4115), 'corrupt', '0Table', 19)
        # A descriptor into the FIB is still rejected before text extraction.
        streams = document()
        table = bytearray(streams[('0Table',)])
        struct.pack_into('<I', table, 19, 898 * 2 | 0x40000000)
        self.assert_parse_error(replace_clx(streams, table), 'corrupt', '0Table', 19)
        # The CLX pointer is specifically pair 33, not the neighboring pair.
        streams = replace_word(document(), 410, 0xFFFFFFFF)
        self.assertEqual(parse_streams(streams)['text'], 'Hello 中文\n第二段\n')
        self.assert_parse_error(replace_word(streams, 418, 0xFFFFFFFF), 'corrupt', '0Table', 0xFFFFFFFF)

    def test_nfib_back_specified_values(self):
        for value in (0xBF, 0xC1):
            self.assertEqual(parse_streams(replace_word(document(), 12, value, 2))['text'],
                             'Hello 中文\n第二段\n')
        for value in (0, 0xD9, 0xFFFF):
            self.assert_parse_error(replace_word(document(), 12, value, 2), 'corrupt', 'WordDocument', 12)

    def test_empty_success_distinct_from_bad_or_missing_clx(self):
        result = parse_streams(document([]))
        self.assertEqual((result['status'], result['text'], result['paragraphs'], result['main_cp_units']),
                         ('ok', '', [], 0))
        result = parse_streams(document([(b'\r', True)]))
        self.assertEqual((result['text'], result['paragraphs']), ('\n', ['']))
        self.assert_parse_error(replace_clx(document([]), b''))
        self.assert_parse_error(replace_clx(document([]), b'\x02\x04\x00\x00\x00\x01\x00\x00\x00'))
        self.assert_parse_error(replace_word(document([]), 76, 1))
        streams = document([])
        del streams[('0Table',)]
        self.assert_parse_error(streams)

    def test_every_related_story_is_excluded_and_range_is_units(self):
        for offset in (80, 84, 92, 96, 100, 104):
            related = {offset: ('RELATED-' + str(offset) + '\r').encode('utf-16le')}
            result = parse_streams(document([(b'\x3d\xd8\x00\xde\r\x00', False)], related))
            self.assertEqual(result['text'], '😀\n')
            self.assertEqual(result['main_cp_units'], 3)
            self.assertEqual(result['paragraphs'], ['😀'])
        # A single piece straddles the body/header boundary; only the first two CPs belong to body.
        streams = document([('甲\rHEADER\r\r'.encode('utf-16le'), False)])
        streams = replace_word(streams, 76, 2)
        streams = replace_word(streams, 84, 7)
        self.assertEqual(parse_streams(streams)['text'], '甲\n')
        # Main body cannot borrow the header's low surrogate to repair itself.
        streams = document([(b'\x3d\xd8\x00\xde\r\x00', False)])
        streams = replace_word(streams, 76, 1)
        streams = replace_word(streams, 84, 1)
        self.assert_parse_error(streams)

    def test_split_surrogate_pair_and_bad_utf16(self):
        parts = [(b'\x3d\xd8', False), (b'\x00\xde', False), (b'\r', True)]
        result = parse_streams(document(parts))
        self.assertEqual((result['text'], result['main_cp_units']), ('😀\n', 3))
        for raw, offset in ((b'\x00\xd8\r\x00', 4096), (b'a\x00b\x00', 4098)):
            self.assert_parse_error(document([(raw, False)]), 'corrupt', 'WordDocument', offset)

    def test_compressed_mapping_not_cp1252_or_gbk(self):
        raw = bytes.fromhex('808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f') + b'\r'
        expected = '\x80\x81‚ƒ„…†‡ˆ‰Š‹Œ\x8d\x8e\x8f\x90‘’“”•–—˜™š›œ\x9d\x9eŸ\n'
        self.assertEqual(parse_streams(document([(raw, True)]))['text'], expected)

    def test_logical_paragraphs_soft_breaks_and_table_markers(self):
        result = parse_streams(document([('甲\x07乙\x07\x07\x0b丙\x0c\r\r'.encode('utf-16le'), False)]))
        self.assertEqual(result['paragraphs'], ['甲\t乙\t\t\n丙\x0c', ''])
        self.assertEqual(result['text'], '甲\t乙\t\t\n丙\x0c\n\n')
        self.assertNotIn('tables', result)

    def test_fields_objects_and_controls_never_become_successful_partial_text(self):
        for code in (0, 1, 2, 8, 0x13, 0x14, 0x15, 0x1F):
            streams = document([(bytes((65, code, 13)), True)])
            self.assert_parse_error(streams, 'unsupported', 'WordDocument', 4097)
        # Diagnostics after supplementary Unicode use UTF-16 CPs, not Python string indexes.
        exc = self.assert_parse_error(document([('😀\x13\r'.encode('utf-16le'), False)]), 'unsupported', 'WordDocument', 4100)
        self.assertEqual(exc.location['cp'], 2)

    def test_prc_skipping_lengths_and_complex_prm_indexes(self):
        streams = document()
        clx = streams[('0Table',)]
        prefixed = b'\x01\x03\x00abc' + b'\x01\x00\x00' + clx
        self.assertEqual(parse_streams(replace_clx(streams, prefixed))['text'], 'Hello 中文\n第二段\n')
        for bad in (b'\x01', b'\x01\x05', b'\x01\x05\x00x', b'\x01\xff\x7f', b'\x02\x00', clx + b'\x00', b'\x02\x04\x00\x00\x00'):
            self.assert_parse_error(replace_clx(streams, bad))
        table = bytearray(clx)
        struct.pack_into('<H', table, 23, 0xFFFF)
        self.assert_parse_error(replace_clx(streams, bytes(table)), 'corrupt', '0Table', 23)
        # Two empty property groups; valid first/last indexes do not alter stored text.
        for prm in (1, 3):
            struct.pack_into('<H', table, 23, prm)
            candidate = b'\x01\x00\x00' * 2 + table
            self.assertEqual(parse_streams(replace_clx(streams, candidate))['text'], 'Hello 中文\n第二段\n')
        struct.pack_into('<H', table, 23, 5)
        self.assert_parse_error(replace_clx(streams, b'\x01\x00\x00' * 2 + table),
                                'corrupt', '0Table', 29)

    def test_nonzero_clx_base_has_absolute_diagnostics(self):
        streams = document(prefix=b'X' * 37)
        streams[('0Table',)] = b'X' * 37 + b'\x01\x05'
        streams = replace_word(streams, 422, 2)
        self.assert_parse_error(streams, 'corrupt', '0Table', 38)
        streams[('0Table',)] = b'X' * 37 + b'\x01\x05\x00Z'
        streams = replace_word(streams, 422, 4)
        self.assert_parse_error(streams, 'corrupt', '0Table', 40)
        streams[('0Table',)] = b'X' * 37 + b'\x02\x00'
        streams = replace_word(streams, 422, 2)
        self.assert_parse_error(streams, 'corrupt', '0Table', 38)

    def test_invalid_cp_length_fc_and_overlap(self):
        streams = document()
        for offset, value in ((1, 0xFFFFFFFF), (1, 15), (5, 1), (9, 0), (13, 12),
                              (19, 0xFFFFFFFF), (19, 0x40000001), (19, 0x40000000), (19, 0x7FFFFFFE)):
            table = bytearray(streams[('0Table',)])
            struct.pack_into('<I', table, offset, value)
            self.assert_parse_error(replace_clx(streams, bytes(table)))
        table = bytearray(streams[('0Table',)])
        struct.pack_into('<I', table, 27, 4098)  # second piece overlaps the first [4110,4116)
        self.assert_parse_error(replace_clx(streams, bytes(table)), 'corrupt', 'WordDocument', 4110)

    def test_budgets_before_large_allocations(self):
        self.assert_parse_error(replace_word(document(), 76, 0xFFFFFFFF), 'resource_limit', 'WordDocument', 76)
        self.assert_parse_error(replace_word(document(), 422, 0xFFFFFFFF), 'resource_limit', 'WordDocument', 422)
        # Enough CLX bytes for 100001 pieces, but no CP unpack/body allocation is allowed.
        length = 4 + 12 * 100001
        huge = b'\x02' + struct.pack('<I', length) + bytes(length)
        self.assert_parse_error(replace_clx(document([]), huge), 'resource_limit', '0Table', 1)
        too_many_prc = b'\x01\x00\x00' * 4097
        self.assert_parse_error(replace_clx(document([]), too_many_prc), 'resource_limit', '0Table', 12288)
        many_paragraphs = document([(b'\r' * 100001, True)])
        self.assert_parse_error(many_paragraphs, 'resource_limit', 'WordDocument', 76)
        class Oversized:
            def __len__(self):
                return 8 * 1024 * 1024 + 1
        with patch('read_wps.Region', side_effect=AssertionError('must reject before Region allocation')):
            self.assert_parse_error({('WordDocument',): Oversized()}, 'resource_limit')

    def test_cfb_versions_encryption_minor_version_and_unknown_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.wps'
            for major in (3, 4):
                path.write_bytes(synthetic_cfb({key[0]: value for key, value in document().items()}, major))
                self.assertEqual(read(path)['text'], 'Hello 中文\n第二段\n')
            for name in ('EncryptionInfo', 'EncryptedPackage', 'EncryptedSummary', 'DRMContent'):
                path.write_bytes(synthetic_cfb({name: b'x'}))
                self.assertEqual(read(path)['status'], 'encrypted')
            data = bytearray(synthetic_cfb({}))
            struct.pack_into('<H', data, 24, 0xFFFF)
            path.write_bytes(data)
            self.assertEqual(read(path)['status'], 'unsupported')
            self.assertEqual(read(path)['location']['offset'], 24)
            path.write_bytes(b'PK rubbish, neither ZIP nor OOXML')
            result = read(path)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['location']['stage'], 'container')
            path.write_bytes(SIGNATURE)
            self.assertEqual(read(path)['status'], 'corrupt')

    def test_file_budget_precedes_read_and_fifo_does_not_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'oversized.wps'
            with path.open('wb') as handle:
                handle.truncate(32 * 1024 * 1024 + 1)
            original_fdopen = os.fdopen
            calls = []
            class Guard:
                def __init__(self, fd, mode):
                    self.handle = original_fdopen(fd, mode)
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.handle.close()
                def fileno(self):
                    return self.handle.fileno()
                def read(self, *args):
                    calls.append(args)
                    raise AssertionError('oversized input must not be read')
            with patch('read_wps.os.fdopen', Guard):
                self.assertEqual(read(path)['status'], 'resource_limit')
            self.assertEqual(calls, [])
            if hasattr(os, 'mkfifo'):
                fifo = Path(temp) / 'pipe.wps'
                os.mkfifo(fifo)
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('read_wps.py')), str(fifo)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)['status'], 'unsupported')

    def test_cli_json_text_empty_and_failures(self):
        script = Path(__file__).with_name('read_wps.py')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.wps'
            for parts, expected in ((None, 'Hello 中文\n第二段\n'), ([], '')):
                path.write_bytes(synthetic_cfb({key[0]: value for key, value in document(parts).items()}))
                original = hashlib.sha256(path.read_bytes()).hexdigest()
                for mode in ('json', 'text'):
                    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), './input.wps', '--format', mode],
                                            cwd=temp, capture_output=True, text=True, timeout=5, env={**os.environ, 'PATH': ''})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, '')
                    self.assertEqual(json.loads(result.stdout)['text'] if mode == 'json' else result.stdout, expected)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
            for data in (b'unknown', synthetic_cfb({'EncryptionInfo': b'x'}), SIGNATURE):
                path.write_bytes(data)
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), './input.wps', '--format', 'text'],
                                        cwd=temp, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                report = json.loads(result.stderr)
                self.assertIsNone(report['text'])
                self.assertFalse(report['partial'])
            missing = read(Path(temp) / 'absent.wps')
            self.assertEqual(missing['status'], 'io_error')

    def test_unexpected_programming_exception_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.wps'
            path.write_bytes(synthetic_cfb({key[0]: value for key, value in document().items()}))
            with patch('read_wps.CompoundFile', side_effect=RuntimeError('sentinel programming defect')):
                with self.assertRaisesRegex(RuntimeError, 'sentinel programming defect'):
                    read(path)


class DeterministicMutationTests(unittest.TestCase):
    """Bounded fixed corpus; not an exhaustive fuzzer or format certification."""

    def test_clx_and_fib_structural_mutations(self):
        seed = document(prefix=b'BASE' * 9)
        randomizer = random.Random(20261025)
        allowed = {'corrupt', 'unsupported', 'encrypted', 'resource_limit', 'invalid_or_unsupported'}
        tested = 0
        # Explicit high-risk integer boundaries and 256 seeded structural changes.
        for offset in (2, 8, 10, 12, 14, 18, 24, 28, 32, 62, 64, 76, 80, 84, 88, 92, 96, 100, 104, 152, 418, 422, 898):
            width = 2 if offset in (2, 8, 10, 12, 32, 62, 152, 898) else 1 if offset == 18 else 4
            for value in (0, 1, (1 << (8 * width - 1)) - 1, (1 << (8 * width)) - 1):
                mutated = replace_word(seed, offset, value, width)
                try:
                    result = parse_streams(mutated)
                    self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
                except ParseError as exc:
                    self.assertIn(exc.status, allowed)
                    self.assertTrue(exc.location)
                tested += 1
        table = seed[('0Table',)]
        for _ in range(256):
            changed = bytearray(table)
            if randomizer.randrange(3) == 0:
                changed = changed[:randomizer.randrange(len(changed) + 1)]
            else:
                offset = randomizer.randrange(36, len(changed))
                changed[offset] ^= randomizer.randrange(1, 256)
            mutated = dict(seed)
            mutated[('0Table',)] = bytes(changed)
            try:
                result = parse_streams(mutated)
                self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
            except ParseError as exc:
                self.assertIn(exc.status, allowed)
                self.assertTrue(exc.location)
            tested += 1
        self.assertEqual(tested, 348)

    def test_cfb_bounded_integer_mutations_cycles_and_truncation(self):
        # CFB structural fields only; never mutate payload into unspecified text.
        seed = synthetic_cfb({key[0]: value for key, value in document().items()})
        mutations = []
        for offset, width in ((24, 2), (26, 2), (28, 2), (30, 2), (32, 2), (40, 4), (44, 4), (48, 4),
                              (56, 4), (60, 4), (64, 4), (68, 4), (72, 4), (76, 4),
                              (512 + 4, 4), (1024 + 76, 4), (1024 + 128 + 68, 4),
                              (1024 + 128 + 72, 4), (1024 + 128 + 116, 4), (1024 + 128 + 120, 4)):
            for value in (0, 1, 0xFF, (1 << (8 * width)) - 1):
                data = bytearray(seed)
                struct.pack_into('<H' if width == 2 else '<I', data, offset, value)
                mutations.append(bytes(data))
        mutations.extend(seed[:cut] for cut in (0, 8, 25, 511, 512, 1024, len(seed) - 1))
        # Directory self-cycle, mini FAT self-cycle, physical cross-link, CLSID.
        for offset, value in ((1024 + 128 + 72, 1), (1536, 0), (1024 + 256 + 116, 0)):
            data = bytearray(seed)
            struct.pack_into('<I', data, offset, value)
            mutations.append(bytes(data))
        data = bytearray(seed)
        data[8] = 1
        mutations.append(bytes(data))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'mutated.wps'
            for data in mutations:
                path.write_bytes(data)
                result = read(path)
                if result['status'] == 'ok':
                    self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
                else:
                    self.assertIn(result['status'], {'unsupported', 'corrupt', 'invalid_or_unsupported', 'encrypted', 'resource_limit'})
                    self.assertIsNone(result['text'])
                    self.assertFalse(result['partial'])
                    self.assertTrue(result['location'])
        self.assertEqual(len(mutations), 91)


if __name__ == '__main__':
    unittest.main()
