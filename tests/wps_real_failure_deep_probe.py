"""Maintenance-only evidence probe for two frozen official failed document bytes.

Records structural fields and a single MS-DOC control code, never source text.
No parser safety gate is altered, no conversion, no office executable and no
runtime WPS Skill network dependency. Failure remains a failure.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BYTES = 32 * 1024 * 1024
SAMPLES = (
    {
        "id": "government_supporting_material_wps",
        "url": "https://jsj.zs.gov.cn/attachment/0/579/579836/2656565.wps",
        "sha256": "3ccedb9e26d89410796508531912169559c0a0258303a085075e69829762375c",
        "error_offset_word_stream": 2546,
    },
    {
        "id": "agricultural_bank_nda_doc",
        "url": "https://www.abchina.com/cn/branch/gx/cg/bm/202404/W020240425607944480483.doc",
        "sha256": "a3be79ad74fa0a810215ab477116fdba687aac628b0ab9477fbce7936e20ae1a",
    },
)


def _allowed(url, item):
    hostname = urlsplit(url).hostname or ""
    return (urlsplit(url).scheme == "https" and
            ((item["id"].startswith("government_") and hostname == "jsj.zs.gov.cn")
             or (item["id"].startswith("agricultural_") and hostname == "www.abchina.com")))


def _read(item):
    if not _allowed(item["url"], item):
        raise ValueError("source_not_allowlisted")
    request = Request(item["url"], headers={"User-Agent": "Mozilla/5.0 (maintenance read-only format diagnostics)"})
    with urlopen(request, timeout=20) as response:
        if not _allowed(response.geturl(), item):
            raise ValueError("redirect_not_allowlisted")
        reported = response.headers.get("Content-Length")
        if reported and int(reported) > MAX_BYTES:
            raise ValueError("declared_size_exceeds_limit")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("file_exceeds_limit")
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("source_identity_changed")
    return data


def _cfb_header(data):
    if not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) or len(data) < 512:
        raise ValueError("not_cfb")
    major, minor, shift = (struct.unpack_from("<H", data, offset)[0] for offset in (26, 24, 30))
    sector_size = (1 << shift) if 9 <= shift <= 12 else None
    result = {
        "major_version": major, "minor_version": minor,
        "sector_shift": shift, "sector_size": sector_size,
        "reserved_34_40_zero": data[34:40] == b"\x00" * 6,
        "minimum_header_fat_directory": sector_size is not None and len(data) >= sector_size * 3,
        "header_clsid_zero": data[8:24] == b"\x00"*16,
    }
    if sector_size:
        rem = len(data) % sector_size
        result.update({
            "input_byte_length_remainder": rem,
            "input_is_sector_aligned": rem == 0,
            "non_sector_aligned_suffix_sha256": (
                hashlib.sha256(data[-rem:]).hexdigest() if rem else None),
            "non_sector_aligned_suffix_all_zero": (
                data[-rem:] == b"\x00"*rem if rem else None),
        })
    return result


def _locate_msdoc_code(data, byte_offset):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"))
    from wps_access import load_compound
    compound = load_compound(data, "data")
    word = compound.streams[("WordDocument",)]
    flags = struct.unpack_from("<H", word, 10)[0]
    table = compound.streams[(("1Table" if flags & 0x0200 else "0Table"),)]
    clx_offset, clx_size = struct.unpack_from("<II", word, 418)
    if clx_size > len(table) or clx_offset > len(table)-clx_size:
        raise ValueError("out_of_range_clx")
    clx = memoryview(table)[clx_offset:clx_offset+clx_size]
    cursor = 0
    while cursor+3 <= len(clx) and clx[cursor] == 1:
        count = struct.unpack_from("<H", clx, cursor+1)[0]
        cursor += count+3
    if cursor + 5 > len(clx) or clx[cursor] != 2:
        raise ValueError("out_of_scope_clx")
    length = struct.unpack_from("<I", clx, cursor+1)[0]
    if length < 4 or (length-4)%12:
        raise ValueError("invalid_plcpcd")
    pieces = (length-4)//12
    if pieces > 100000:
        raise ValueError("pieces_budget")
    plc = clx[cursor+5:]
    positions = struct.unpack_from("<"+"I"*(pieces+1), plc)
    for idx in range(pieces):
        desc = 4*(pieces+1) + 8*idx
        encoded = struct.unpack_from("<I", plc, desc+2)[0]
        compressed = bool(encoded & 0x40000000)
        start = (encoded & 0x3fffffff)
        start = start//2 if compressed else start
        stride = 1 if compressed else 2
        end = start + (positions[idx+1]-positions[idx])*stride
        if start <= byte_offset < end:
            if (byte_offset-start)%stride:
                return {"status":"not_aligned_to_text_unit"}
            cp = positions[idx] + (byte_offset-start)//stride
            control = int.from_bytes(word[byte_offset:byte_offset+stride], "little")
            return {
                "status":"mapped", "cp":cp, "piece":idx,
                "compressed":compressed, "char_code_decimal":control,
                "char_code_hex":"U+{:04X}".format(control),
                "kind":"C0_CONTROL" if control < 32 else "not_C0",
                "word_stream_offset":byte_offset,
            }
    return {"status":"not_in_piece_table"}



def _shape_anchor_evidence(data, cp):
    # MS-DOC FibRgFcLcb97 pair index 40 = fcPlcSpaMom/lcbPlcSpaMom.
    # This is metadata inspection only, not object rendering/decoding.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"))
    from wps_access import load_compound
    streams=load_compound(data, "data").streams
    word=streams[("WordDocument",)]
    flags=struct.unpack_from("<H",word,10)[0]
    table=streams[(("1Table" if flags & 0x0200 else "0Table"),)]
    pair_offset=154+40*8
    start,size=struct.unpack_from("<II",word,pair_offset)
    result={"fclcb_pair_index":40,"stored_length":size}
    if not size:
        result["status"]="missing_plcf_spa_mom"
        return result
    if size<34 or (size-4)%30 or start>len(table)-size:
        result["status"]="invalid_or_out_of_subset_plcf_spa_mom"
        return result
    count=(size-4)//30
    if count>100000:
        result["status"]="resource_limited"
        return result
    cps=struct.unpack_from("<"+"I"*(count+1),table,start)
    result.update({"status":"bounded_cp_array_checked","shape_count":count,
                   "cp_list_strictly_increasing":all(a<b for a,b in zip(cps,cps[1:])),
                   "failing_control_cp_in_shape_anchors":cp in cps[:-1]})
    return result


def _cfb_prefix_forensic_only(data):
    # In-memory structural check of the sector-aligned prefix ONLY.
    # NEVER used as a fallback or a production read; no text extracted.
    header=_cfb_header(data)
    remainder=header.get("input_byte_length_remainder")
    if not remainder:
        return {"status":"not_needed"}
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"))
    from wps_cfb import CompoundFile
    prefix=data[:-remainder]
    try:
        cfb=CompoundFile(prefix,compatibility="strict")
        return {"status":"sector_aligned_prefix_structurally_readable",
                "streams_count":len(cfb.streams),
                "worddocument_stream_present":("WordDocument",) in cfb.streams,
                "strictly_forensic_not_accepted":True}
    except ValueError as exc:
        return {"status":"sector_aligned_prefix_still_rejected",
                "error_type":type(exc).__name__,
                "reason":str(exc)[:160],
                "strictly_forensic_not_accepted":True}


def main():
    report = {"schema_version":"wps_real_failure_deep_diagnostics_v1",
              "source_policy":"pinned_sha256_official_https_only",
              "test_scope":"read_only_metadata_and_codepoint_not_office_rendering",
              "cases":[]}
    for item in SAMPLES:
        row={"id":item["id"],"sha256":item["sha256"]}
        try:
            data = _read(item)
            row["download"]="ok"
            row["length"]=len(data)
            row["cfb_header"]=_cfb_header(data)
            if item["id"] == "agricultural_bank_nda_doc":
                row["aligned_prefix_forensics"] = _cfb_prefix_forensic_only(data)
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"))
                from read_wps import read_bytes
                try:
                    read_bytes(data)
                    raise ValueError("unsafe acceptance of malformed Agricultural Bank CFB")
                except ValueError as exc:
                    if getattr(exc, "status", None) != "invalid_or_unsupported":
                        raise
                    reason = getattr(exc, "location", {}).get("reason_code")
                    if reason != "cfb_unaligned_physical_length":
                        raise ValueError("expected precise nonaligned CFB failure")
                    row["actual_reader_default"] = {
                        "status": "rejected", "reason_code": reason,
                        "message": str(exc)[:130]
                    }
            if "error_offset_word_stream" in item:
                row["failing_character"] = _locate_msdoc_code(data,item["error_offset_word_stream"])
                if row["failing_character"].get("status") == "mapped":
                    row["shape_anchor_evidence"] = _shape_anchor_evidence(data, row["failing_character"]["cp"])
                # Exercise production reader in both modes, without executing
                # fields or objects or exporting the source document text.
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"))
                from read_wps import read_bytes
                for policy in ("reject", "placeholder"):
                    try:
                        result = read_bytes(data, object_policy=policy)
                        row["actual_reader_" + policy] = {
                            "status": result.get("status"), "format": result.get("format"),
                            "paragraph_count": len(result.get("paragraphs", [])),
                            "issues": [issue.get("code") for issue in result.get("issues", [])
                                       if isinstance(issue, dict)]}
                    except Exception as exc:
                        row["actual_reader_" + policy] = {
                            "status": "rejected", "error_status": getattr(exc, "status", None),
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:120]}
                if (row["actual_reader_reject"].get("error_status") != "unsupported"
                        or row["actual_reader_placeholder"].get("status") != "partial"
                        or row["actual_reader_placeholder"].get("paragraph_count") != 36
                        or "drawn_shape_anchor_not_rendered"
                        not in row["actual_reader_placeholder"].get("issues", ())):
                    raise ValueError("government shape sample did not achieve validated partial extraction")
        except Exception as exc:
            row.update({"download":"failed","error_type":type(exc).__name__,
                        "error_summary":str(exc)[:120]})
        report["cases"].append(row)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(case.get("download")=="ok" for case in report["cases"]) else 1


if __name__=="__main__":
    raise SystemExit(main())
