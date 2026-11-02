#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export DOCX documents to WPS-compatible formats (RTF stream or CFB binary) and validate output."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

# Ensure local imports work when script is executed directly or via symlink
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from docx_to_wps_rtf import convert_docx_to_rtf_wps
except ImportError:
    convert_docx_to_rtf_wps = None

CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_SIGNATURE = b"PK\x03\x04"
RTF_SIGNATURE = b"{\\rtf1"
WORD_IDENT = 0xA5EC
SUPPORTED_FIB_VERSIONS = {
    0x00C1: "Word 97",
    0x00D9: "Word 2000",
    0x0101: "Word 2002",
    0x010C: "Word 2003",
    0x0112: "Word 2007",
}


def find_soffice(custom_path=None):
    """Locate the soffice / libreoffice executable across environment, PATH, and standard directories."""
    if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
        return str(Path(custom_path).resolve())

    env_paths = [os.environ.get("SOFFICE_PATH"), os.environ.get("LIBREOFFICE_PATH")]
    for candidate in env_paths:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())

    which_candidate = shutil.which("soffice") or shutil.which("libreoffice")
    if which_candidate:
        return str(Path(which_candidate).resolve())

    system_candidates = [
        # Linux locations
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/opt/libreoffice/program/soffice",
        "/snap/bin/libreoffice",
        # macOS locations
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
        # Windows locations
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for candidate in system_candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())

    return None


def check_environment(custom_soffice=None):
    """Verify runtime environment, Python version, and backend availability."""
    python_ok = sys.version_info >= (3, 10)
    soffice_path = find_soffice(custom_soffice)
    soffice_version = None

    if soffice_path:
        try:
            proc = subprocess.run(
                [soffice_path, "--headless", "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                soffice_version = proc.stdout.strip() or "LibreOffice (version text empty)"
            else:
                soffice_version = f"Error querying version (code {proc.returncode})"
        except Exception as exc:
            soffice_version = f"Failed to execute: {exc}"

    backends = ["rtf"]
    if soffice_path:
        backends.append("libreoffice")

    # Environment is ok as long as Python is >= 3.10 (RTF backend is always available)
    status = "environment-ok" if python_ok else "unsupported-python"
    return {
        "status": status,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_ok": python_ok,
        "backends_available": backends,
        "default_backend": "libreoffice" if soffice_path else "rtf",
        "soffice_found": bool(soffice_path),
        "soffice_path": soffice_path,
        "soffice_version": soffice_version,
    }


def inspect_rtf_wps(path: Path, size: int, header: bytes):
    """Validate and inspect an RTF-stream .wps document."""
    try:
        with path.open("rb") as fp:
            data = fp.read(min(size, 65536))
    except OSError as exc:
        return {"valid": False, "error": f"Cannot read RTF content: {exc}", "details": {}}

    has_font_tbl = b"\\fonttbl" in data
    has_closing = False
    try:
        with path.open("rb") as fp:
            if size > 64:
                fp.seek(size - 64)
                tail = fp.read(64)
            else:
                tail = data
            has_closing = b"}" in tail
    except OSError:
        pass

    if not has_font_tbl:
        return {
            "valid": False,
            "error": "RTF stream missing \\fonttbl header table",
            "details": {"size": size},
        }

    return {
        "valid": True,
        "format": "RTF-stream WPS Compatible Document",
        "file_size": size,
        "has_font_table": has_font_tbl,
        "has_closing_brace": has_closing,
        "engine_compatibility": "WPS Office / MS Office native silent loading",
    }


def inspect_cfb_wps(path: Path, size: int, header: bytes):
    """Validate and inspect a CFB binary .wps document."""
    byte_order = struct.unpack_from("<H", header, 28)[0]
    if byte_order != 0xFFFE:
        return {"valid": False, "error": f"Unsupported byte order: 0x{byte_order:04X}", "details": {"byte_order": byte_order}}

    sector_shift = struct.unpack_from("<H", header, 30)[0]
    if sector_shift not in (9, 12):
        return {"valid": False, "error": f"Unsupported sector shift: {sector_shift}", "details": {"sector_shift": sector_shift}}
    sector_size = 1 << sector_shift

    dir_start_sector = struct.unpack_from("<I", header, 48)[0]
    fat_ids = [val for val in struct.unpack_from("<109I", header, 76) if val not in (0xFFFFFFFF, 0xFFFFFFFE)]

    fat = {}
    try:
        with path.open("rb") as fp:
            for fat_sec in fat_ids:
                fat_offset = (fat_sec + 1) * sector_size
                if fat_offset + sector_size <= size:
                    fp.seek(fat_offset)
                    fat_bytes = fp.read(sector_size)
                    entry_count = sector_size // 4
                    sec_entries = struct.unpack_from(f"<{entry_count}I", fat_bytes)
                    base_idx = len(fat)
                    for idx, nxt in enumerate(sec_entries):
                        fat[base_idx + idx] = nxt
    except OSError as exc:
        return {"valid": False, "error": f"Cannot read FAT table: {exc}", "details": {}}

    dir_sectors = []
    curr_sec = dir_start_sector
    visited = set()
    while curr_sec not in (0xFFFFFFFE, 0xFFFFFFFF) and curr_sec not in visited:
        visited.add(curr_sec)
        dir_sectors.append(curr_sec)
        curr_sec = fat.get(curr_sec, 0xFFFFFFFE)

    word_doc_entry = None
    root_entry = None
    try:
        with path.open("rb") as fp:
            for d_sec in dir_sectors:
                d_offset = (d_sec + 1) * sector_size
                if d_offset + sector_size <= size:
                    fp.seek(d_offset)
                    d_bytes = fp.read(sector_size)
                    for entry_idx in range(0, sector_size, 128):
                        entry_bytes = d_bytes[entry_idx : entry_idx + 128]
                        if len(entry_bytes) < 128:
                            continue
                        name_len = struct.unpack_from("<H", entry_bytes, 64)[0]
                        obj_type = entry_bytes[66]
                        if name_len > 2:
                            raw_name = entry_bytes[: name_len - 2]
                            entry_name = raw_name.decode("utf-16le", errors="ignore")
                        else:
                            entry_name = ""

                        if obj_type == 5:
                            root_entry = {
                                "name": entry_name,
                                "start_sector": struct.unpack_from("<I", entry_bytes, 116)[0],
                                "stream_size": struct.unpack_from("<Q", entry_bytes, 120)[0],
                            }
                        elif entry_name == "WordDocument":
                            word_doc_entry = {
                                "name": entry_name,
                                "type": obj_type,
                                "start_sector": struct.unpack_from("<I", entry_bytes, 116)[0],
                                "stream_size": struct.unpack_from("<Q", entry_bytes, 120)[0],
                            }
                            break
                    if word_doc_entry:
                        break
    except OSError as exc:
        return {"valid": False, "error": f"Cannot read directory stream: {exc}", "details": {}}

    if not word_doc_entry:
        return {
            "valid": False,
            "error": "WordDocument stream not found in CFB directory entries",
            "details": {"size": size, "has_root_entry": bool(root_entry)},
        }

    w_start = word_doc_entry["start_sector"]
    w_size = word_doc_entry["stream_size"]
    w_offset = (w_start + 1) * sector_size

    fib_info = {}
    try:
        with path.open("rb") as fp:
            if w_size >= 4096 or not root_entry:
                fp.seek(w_offset)
                fib_bytes = fp.read(min(32, w_size))
            else:
                mini_start = root_entry["start_sector"]
                mini_stream_offset = (mini_start + 1) * sector_size
                target_offset = mini_stream_offset + (w_start * 64)
                if target_offset + 4 <= size:
                    fp.seek(target_offset)
                    fib_bytes = fp.read(min(32, w_size))
                else:
                    fib_bytes = b""

        if len(fib_bytes) >= 4:
            w_ident, n_fib = struct.unpack_from("<HH", fib_bytes, 0)
            fib_info["wIdent"] = hex(w_ident)
            fib_info["nFib"] = hex(n_fib)
            fib_info["version_name"] = SUPPORTED_FIB_VERSIONS.get(n_fib, f"Unknown FIB (0x{n_fib:04X})")
            if w_ident != WORD_IDENT:
                return {
                    "valid": False,
                    "error": f"Invalid WordDocument stream signature: 0x{w_ident:04X} (expected 0xA5EC)",
                    "details": fib_info,
                }
    except OSError as exc:
        fib_info["read_error"] = str(exc)

    return {
        "valid": True,
        "format": "MS-DOC / WPS Binary CFB",
        "file_size": size,
        "sector_size": sector_size,
        "word_document": {
            "stream_size": w_size,
            "start_sector": w_start,
            "fib": fib_info,
        },
    }


def inspect_wps_structure(file_path):
    """Perform level-2 structure validation on a .wps file (supports both CFB and RTF formats)."""
    path = Path(file_path)
    if not path.is_file():
        return {"valid": False, "error": f"File not found: {file_path}", "details": {}}

    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"valid": False, "error": f"Cannot stat file: {exc}", "details": {}}

    if size < 16:
        return {"valid": False, "error": "File size smaller than 16 bytes", "details": {"size": size}}

    try:
        with path.open("rb") as fp:
            header = fp.read(min(size, 512))
    except OSError as exc:
        return {"valid": False, "error": f"Cannot read header: {exc}", "details": {}}

    if header.startswith(ZIP_SIGNATURE):
        return {
            "valid": False,
            "error": "File starts with ZIP magic PK\\x03\\x04; likely a renamed OOXML document rather than binary .wps",
            "details": {"size": size, "signature": "ZIP"},
        }

    if header.startswith(RTF_SIGNATURE):
        return inspect_rtf_wps(path, size, header)

    if header.startswith(CFB_SIGNATURE):
        return inspect_cfb_wps(path, size, header)

    return {
        "valid": False,
        "error": "File signature is neither CFB/OLE (0xD0CF11E0) nor RTF stream (\\rtf1)",
        "details": {"size": size, "signature_hex": header[:8].hex()},
    }


def convert_docx_to_wps_libreoffice(docx_path, output_path=None, custom_soffice=None, timeout=120):
    """Convert .docx to .wps using LibreOffice 'wps:MS Word 97' filter."""
    input_file = Path(docx_path).resolve()
    soffice_path = find_soffice(custom_soffice)
    if not soffice_path:
        return {
            "status": "missing-dependency",
            "error": "LibreOffice executable (soffice) not found in system or environment",
        }

    if output_path:
        final_output = Path(output_path).resolve()
        out_dir = final_output.parent
    else:
        out_dir = input_file.parent
        final_output = out_dir / f"{input_file.stem}.wps"

    out_dir.mkdir(parents=True, exist_ok=True)
    temp_expected_output = out_dir / f"{input_file.stem}.wps"

    cmd = [
        soffice_path,
        "--headless",
        "--convert-to",
        "wps:MS Word 97",
        "--outdir",
        str(out_dir),
        str(input_file),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Conversion timed out after {timeout} seconds"}
    except Exception as exc:
        return {"status": "error", "error": f"Subprocess invocation failed: {exc}"}

    if proc.returncode != 0:
        return {
            "status": "conversion-failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "error": f"LibreOffice exited with code {proc.returncode}",
        }

    if final_output != temp_expected_output:
        if temp_expected_output.exists():
            temp_expected_output.replace(final_output)

    if not final_output.is_file():
        return {
            "status": "output-missing",
            "error": f"LibreOffice succeeded but target file {final_output} was not created",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    structure_check = inspect_wps_structure(final_output)
    return {
        "status": "success" if structure_check.get("valid") else "structure-invalid",
        "backend": "libreoffice",
        "output_file": str(final_output),
        "file_size": final_output.stat().st_size,
        "structure_check": structure_check,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def convert_docx_to_wps_rtf(docx_path, output_path=None):
    """Convert .docx to .wps using pure Python RTF stream (zero external dependencies)."""
    if convert_docx_to_rtf_wps is None:
        return {"status": "error", "error": "docx_to_wps_rtf module failed to import"}

    input_file = Path(docx_path).resolve()
    if output_path:
        final_output = Path(output_path).resolve()
    else:
        final_output = input_file.parent / f"{input_file.stem}.wps"

    try:
        res = convert_docx_to_rtf_wps(str(input_file), str(final_output))
        structure_check = inspect_wps_structure(final_output)
        res["structure_check"] = structure_check
        res["backend"] = "rtf"
        return res
    except Exception as exc:
        return {"status": "error", "error": f"RTF conversion failed: {exc}"}


def convert_docx_to_wps(docx_path, output_path=None, backend="auto", custom_soffice=None, timeout=120):
    """Convert an input .docx document to .wps format with automatic or requested backend."""
    input_file = Path(docx_path).resolve()
    if not input_file.is_file():
        return {"status": "error", "error": f"Input file not found: {docx_path}"}

    if backend == "rtf":
        return convert_docx_to_wps_rtf(input_file, output_path)

    if backend == "libreoffice":
        return convert_docx_to_wps_libreoffice(input_file, output_path, custom_soffice, timeout)

    # backend == "auto": prefer LibreOffice if available, fallback smoothly to pure Python RTF
    soffice_path = find_soffice(custom_soffice)
    if soffice_path:
        lo_res = convert_docx_to_wps_libreoffice(input_file, output_path, custom_soffice, timeout)
        if lo_res.get("status") == "success":
            return lo_res

    # Fallback to pure Python RTF backend
    return convert_docx_to_wps_rtf(input_file, output_path)


def verify_readback(wps_path, read_wps_script=None):
    """Verify content extraction from the exported .wps file (supports both CFB and RTF formats)."""
    wps_file = Path(wps_path).resolve()
    if not wps_file.is_file():
        return {"status": "error", "error": f"Target file does not exist: {wps_path}"}

    try:
        with wps_file.open("rb") as fp:
            magic = fp.read(16)
    except OSError as exc:
        return {"status": "error", "error": f"Cannot read file header: {exc}"}

    # If it is an RTF-stream .wps, perform native Unicode text decode
    if magic.startswith(RTF_SIGNATURE):
        try:
            with wps_file.open("r", encoding="ascii", errors="ignore") as fp:
                rtf_content = fp.read()

            def decode_u(m):
                code = int(m.group(1))
                if code < 0:
                    code += 65536
                return chr(code)

            decoded_text = re.sub(r"\\u(-?\d+)\?", decode_u, rtf_content)
            # Remove control commands
            stripped_text = re.sub(r"\\[a-zA-Z0-9]+", " ", decoded_text)
            stripped_text = re.sub(r"[{}]", " ", stripped_text)
            paragraphs = [p.strip() for p in stripped_text.splitlines() if p.strip()]

            return {
                "status": "readback-ok",
                "format": "RTF-stream WPS",
                "paragraph_count": len(paragraphs),
                "preview": paragraphs[:5],
                "diagnostics": [],
            }
        except Exception as exc:
            return {"status": "error", "error": f"RTF readback failed: {exc}"}

    # If it is CFB/MS-DOC binary .wps, invoke read_wps.py
    script_path = None
    if read_wps_script and Path(read_wps_script).is_file():
        script_path = Path(read_wps_script).resolve()
    else:
        candidate = Path(__file__).resolve().parent.parent.parent / "wps" / "scripts" / "read_wps.py"
        if candidate.is_file():
            script_path = candidate

    if not script_path:
        return {"status": "skipped", "message": "read_wps.py script not found; readback check skipped"}

    cmd = [sys.executable, "-I", "-S", "-B", str(script_path), str(wps_file)]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {"status": "error", "error": f"Failed to execute read_wps.py: {exc}"}

    if proc.returncode != 0:
        return {
            "status": "readback-error",
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip(),
            "stdout": proc.stdout.strip(),
        }

    try:
        payload = json.loads(proc.stdout)
        paragraphs = payload.get("paragraphs", [])
        return {
            "status": "readback-ok",
            "format": payload.get("format"),
            "paragraph_count": len(paragraphs),
            "preview": [p.get("text", "")[:100] for p in paragraphs[:5]],
            "diagnostics": payload.get("diagnostics", []),
        }
    except json.JSONDecodeError:
        return {
            "status": "readback-nonjson",
            "stdout_tail": proc.stdout[-500:],
        }


def main():
    parser = argparse.ArgumentParser(description="WPS document export and validation tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check execution environment and backend availability.")
    check_parser.add_argument("--soffice", help="Path to custom soffice executable.")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect structure of a .wps file (CFB or RTF).")
    inspect_parser.add_argument("file", help="Path to .wps file.")

    convert_parser = subparsers.add_parser("convert", help="Convert .docx to .wps format.")
    convert_parser.add_argument("input", help="Path to input .docx file.")
    convert_parser.add_argument("--output", "-o", help="Target .wps output path.")
    convert_parser.add_argument(
        "--backend",
        "-b",
        choices=["auto", "rtf", "libreoffice"],
        default="auto",
        help="Export backend (auto: prefer libreoffice else rtf; rtf: pure Python; libreoffice: headless soffice).",
    )
    convert_parser.add_argument("--soffice", help="Path to custom soffice executable.")
    convert_parser.add_argument("--timeout", type=int, default=120, help="Conversion timeout in seconds.")
    convert_parser.add_argument("--verify-read", action="store_true", help="Perform readback content verification.")
    convert_parser.add_argument("--read-wps-script", help="Path to read_wps.py script.")

    args = parser.parse_args()

    if args.command == "check":
        res = check_environment(args.soffice)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if res.get("status") == "environment-ok" else 1)

    if args.command == "inspect":
        res = inspect_wps_structure(args.file)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if res.get("valid") else 1)

    if args.command == "convert":
        res = convert_docx_to_wps(
            args.input,
            args.output,
            backend=args.backend,
            custom_soffice=args.soffice,
            timeout=args.timeout,
        )
        if args.verify_read and res.get("status") == "success":
            res["readback"] = verify_readback(res["output_file"], args.read_wps_script)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if res.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
