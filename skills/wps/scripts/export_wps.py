#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export DOCX documents to WPS-compatible formats (RTF stream or CFB binary) and validate output."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

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


def check_environment(custom_soffice=None, backend='auto'):
    """Verify runtime environment, Python version, and backend availability."""
    python_ok = sys.version_info >= (3, 10)
    from wps_task_paths import write_capabilities
    import wps_delivery
    write_capabilities()
    soffice_path = None if backend == 'rtf' else find_soffice(custom_soffice)
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


def _inspect_file(path, expected_format=None):
    from wps_access import read_input
    from wps_output_contract import inspect_output_bytes
    try:
        return inspect_output_bytes(read_input(path), path, expected_format)
    except (OSError, ValueError) as exc:
        return {"valid": False, "error": str(exc), "full_format_verified": False}


def inspect_rtf_wps(path: Path, size=None, header=None):
    """Legacy helper; caller-supplied size/header no longer bypass full validation."""
    return _inspect_file(path, 'rtf')


def inspect_cfb_wps(path: Path, size=None, header=None):
    """Legacy helper backed by the bounded CFB and MS-DOC reader."""
    return _inspect_file(path, 'ms_doc_subset')


def inspect_wps_structure(file_path):
    """Inspect a write-side candidate; read_wps remains suffix-independent."""
    return _inspect_file(Path(file_path))


def _prepare_conversion(docx_path, output_path, expected_format):
    from read_wps import read_bytes
    from wps_access import read_input
    from wps_output_contract import require_output_path
    source = Path(docx_path).resolve()
    from wps_task_paths import delivery_filename
    target = delivery_filename(output_path if output_path is not None else source.with_suffix('.wps').name).absolute()
    require_output_path(target, expected_format)
    if source == target or (target.exists() and source.samefile(target)):
        raise ValueError('Output must not overwrite the source document')
    source_bytes = read_input(source)
    payload = read_bytes(source_bytes)
    if payload.get('format') != 'ooxml-word' or payload.get('status') not in ('ok', 'partial'):
        raise ValueError('DOCX conversion requires a readable OOXML Word package, regardless of its suffix')
    return source, target, source_bytes, payload


def _publish_conversion(candidate, target, source, source_bytes, source_payload, expected_format, backend):
    from wps_access import read_input
    from wps_output_contract import inspect_output_bytes, readback_summary
    if candidate.is_symlink() or not candidate.is_file():
        return {'status': 'output-missing', 'error': 'Converter did not create a regular private candidate'}
    data = read_input(candidate)
    inspection = inspect_output_bytes(data, target, expected_format)
    if not inspection['valid']:
        return {'status': 'structure-invalid', 'error': inspection['error'], 'structure_check': inspection}
    if read_input(source) != source_bytes:
        raise ValueError('Source changed during conversion; publication stopped')
    from read_wps import read_bytes
    payload = read_bytes(data)
    def main_text(value):
        if value.get('format') == 'rtf':
            parts = []
            for block in value.get('blocks', []):
                if block['type'] == 'paragraph':
                    parts.append(value['paragraphs'][block['index']]['text'])
                else:
                    parts.extend('\t'.join(cell['text'] for cell in row) for row in value['tables'][block['index']]['rows'])
            return '\n'.join(parts)
        return value.get('text', '')
    if ''.join(main_text(source_payload).split()) != ''.join(main_text(payload).split()):
        return {'status': 'content-mismatch', 'error': 'Ordered readable main text differs; target preserved',
                'comparison_scope': 'main-text-with-whitespace-normalized-not-layout'}
    if read_input(source) != source_bytes:
        raise ValueError('Source changed during conversion; publication stopped')
    partial = inspection['partial'] or source_payload['status'] == 'partial'
    from wps_task_paths import output_candidate, write_candidate
    with output_candidate(target, prefix='.tmp_wps_publish_') as (temporary, publish):
        write_candidate(temporary, data)
        publish()
    return {'status': 'partial' if partial else 'success', **publish.receipt, 'partial': partial, 'backend': backend,
            'output_file': str(target), 'file_size': len(data), 'output_format': expected_format,
            'binary_doc': expected_format == 'ms_doc_subset', 'native_wps': False,
            'structure_check': inspection, 'readback': inspection['readback'],
            'source_readback': readback_summary(source_payload), 'layout_verification': 'not_run',
            'source_content_comparison': 'ordered-main-text-matched-whitespace-normalized',
            'full_format_verified': False}


def _conversion_failure(exc, target):
    publication = getattr(exc, 'publication', {})
    if publication and target is not None and publication.get('publication_target') != str(target):
        return {'status': 'error', 'error': str(exc), 'output_written': False,
                'publication_target': str(target), 'publication_outcome': 'not_attempted',
                'cleanup_complete': None, 'cleanup_status': 'unconfirmed_after_staged_failure',
                'intermediate_publication': publication}
    return {'status': 'error', 'error': str(exc), **publication}


def convert_docx_to_wps_libreoffice(docx_path, output_path=None, custom_soffice=None, timeout=120):
    """Request actual MS-DOC bytes; validate privately before publishing .doc/.wps."""
    from wps_task_paths import delivery_filename
    try:
        delivery_filename(output_path if output_path is not None else Path(docx_path).with_suffix('.wps').name)
    except (ValueError, OSError) as exc:
        return _conversion_failure(exc, None)
    soffice_path = find_soffice(custom_soffice)
    if not soffice_path:
        return {'status': 'missing-dependency', 'error': 'LibreOffice executable (soffice) not found'}
    try:
        source, target, source_bytes, source_payload = _prepare_conversion(docx_path, output_path, 'ms_doc_subset')
        from wps_task_paths import ACTIVE
        if ACTIVE.get() is None:
            target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.tmp_wps_export_', dir='.' if ACTIVE.get() is not None else target.parent) as directory:
            private = Path(directory).absolute()
            candidate = private / (source.stem + '.doc')
            private_source = private / (source.stem + '.docx')
            private_source.write_bytes(source_bytes)
            command = [soffice_path, '-env:UserInstallation=' + (private / 'profile').as_uri(), '--headless',
                       '--convert-to', 'doc:MS Word 97', '--outdir', str(private), str(private_source)]
            process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, timeout=timeout, check=False)
            execution = {'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
            if process.returncode != 0:
                return {'status': 'conversion-failed', 'error': 'LibreOffice conversion failed', **execution}
            result = _publish_conversion(candidate, target, source, source_bytes, source_payload, 'ms_doc_subset', 'libreoffice')
            result.update(execution)
            return result
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'error': f'Conversion timed out after {timeout} seconds', 'returncode': None}
    except (OSError, ValueError, RuntimeError) as exc:
        return _conversion_failure(exc, locals().get('target'))


def convert_docx_to_wps_rtf(docx_path, output_path=None):
    """Create RTF, never publish it with a binary-DOC suffix."""
    if convert_docx_to_rtf_wps is None:
        return {'status': 'error', 'error': 'docx_to_wps_rtf module failed to import'}
    try:
        source, target, source_bytes, source_payload = _prepare_conversion(docx_path, output_path, 'rtf')
        from wps_task_paths import ACTIVE
        if ACTIVE.get() is not None:
            # The converter already validates text, raw controls and source
            # identity, then publishes through a confined private candidate.
            result = convert_docx_to_rtf_wps(str(source), target.name)
            result['backend'] = 'rtf'
            return result
        if ACTIVE.get() is None:
            target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.tmp_wps_export_', dir='.' if ACTIVE.get() is not None else target.parent) as directory:
            candidate = Path(directory) / 'candidate.rtf'
            from docx_to_wps_rtf import _convert_docx_to_rtf_candidate
            conversion = _convert_docx_to_rtf_candidate(str(source), str(candidate))
            result = _publish_conversion(candidate, target, source, source_bytes, source_payload, 'rtf', 'rtf')
            if result.get('status') in ('success', 'partial'):
                for key in ('content_validation', 'conversion_issues', 'format_validation', 'source_format_mapping'):
                    if key in conversion:
                        result[key] = conversion[key]
                if conversion.get('partial'):
                    result.update(status='partial', partial=True)
            return result
    except (OSError, ValueError, RuntimeError) as exc:
        return _conversion_failure(exc, locals().get('target'))


def convert_docx_to_wps(docx_path, output_path=None, backend="auto", custom_soffice=None, timeout=120):
    """Use suffix-constrained backends; a binary request cannot fall back to RTF."""
    if backend not in ('auto', 'rtf', 'libreoffice'):
        return {'status': 'error', 'error': 'Unknown conversion backend'}
    source = Path(docx_path)
    if not source.is_file():
        return {'status': 'error', 'error': f'Input file not found: {docx_path}'}
    from wps_task_paths import delivery_filename
    try:
        target = delivery_filename(output_path if output_path is not None else source.with_suffix('.wps').name)
    except (ValueError, OSError) as exc:
        return _conversion_failure(exc, None)
    suffix = target.suffix.lower()
    if suffix not in ('.wps', '.rtf', '.doc'):
        return {'status': 'error', 'error': 'Export output must use .wps, .rtf or binary-only .doc'}
    if backend == 'rtf' or (backend == 'auto' and suffix == '.rtf'):
        return convert_docx_to_wps_rtf(source, target)
    if backend == 'libreoffice' or suffix == '.doc':
        result = convert_docx_to_wps_libreoffice(source, target, custom_soffice, timeout)
        result.update(backend_requested=backend, fallback_allowed=False)
        return result
    binary = convert_docx_to_wps_libreoffice(source, target, custom_soffice, timeout)
    if binary.get('status') in ('success', 'partial'):
        binary.update(backend_requested='auto', fallback_used=False)
        return binary
    result = convert_docx_to_wps_rtf(source, target)
    result.update(backend_requested='auto', fallback_used=True,
                  fallback={'from': 'libreoffice', 'to': 'rtf', 'reason': binary})
    if result.get('status') in ('success', 'partial'):
        result.update(status='partial', partial=True)
    return result


def verify_readback(wps_path, read_wps_script=None):
    """Run the same real reader for RTF, MS-DOC and OOXML; preserve partial/exit 3."""
    from wps_output_contract import readback_summary
    path = Path(wps_path).resolve()
    if not path.is_file():
        return {'status': 'error', 'error': f'Target file does not exist: {wps_path}'}
    script = Path(read_wps_script) if read_wps_script else Path(__file__).with_name('read_wps.py')
    if not script.is_file():
        return {'status': 'error', 'error': 'Requested read_wps.py script is unavailable'}
    from wps_task_paths import ACTIVE
    access = ACTIVE.get()
    directory = access.root if access is not None else path.parent
    argument = str(path.relative_to(directory)) if access is not None else path.name
    command = [sys.executable, '-I', '-S', '-B', str(script.resolve()), argument]
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False, cwd=directory)
    except subprocess.TimeoutExpired:
        return {'status': 'readback-error', 'error': 'Reader timed out', 'returncode': None}
    except OSError as exc:
        return {'status': 'readback-error', 'error': str(exc), 'returncode': None}
    try:
        payload = json.loads(process.stdout)
    except (json.JSONDecodeError, ValueError):
        return {'status': 'readback-nonjson', 'returncode': process.returncode, 'stderr': process.stderr,
                'stdout_tail': process.stdout[-500:], 'stdout_characters': len(process.stdout),
                'stdout_truncated': len(process.stdout) > 500}
    result = readback_summary(payload, process.returncode)
    result['stderr'] = process.stderr
    return result


from wps_task_paths import cli_boundary, validate_argument


@cli_boundary
def main():
    parser = argparse.ArgumentParser(description="WPS document export and validation tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check execution environment and backend availability.")
    check_parser.add_argument("--soffice", help="Path to custom soffice executable.")
    check_parser.add_argument('--backend', choices=('auto', 'rtf', 'libreoffice'), default='auto',
                              help='rtf checks the standard-library route without probing external software')

    inspect_parser = subparsers.add_parser("inspect", help="Validate an output candidate's signature, supported content and suffix.")
    inspect_parser.add_argument("file", help="RTF .rtf/.wps or MS-DOC .doc/.wps candidate; use read_wps.py for input reading.")

    convert_parser = subparsers.add_parser("convert", help="Convert OOXML Word to RTF or conditional binary MS-DOC.")
    convert_parser.add_argument("input", help="Path to input .docx file.")
    convert_parser.add_argument("--output", "-o", help="Bare filename in task cwd. Default: input basename with .wps; .rtf or binary-only .doc also accepted.")
    convert_parser.add_argument(
        "--backend",
        "-b",
        choices=["auto", "rtf", "libreoffice"],
        default="auto",
        help="auto: .wps allows disclosed RTF fallback; .doc requires LibreOffice; .rtf uses pure Python.",
    )
    convert_parser.add_argument("--soffice", help="Path to custom soffice executable.")
    convert_parser.add_argument("--timeout", type=int, default=120, help="Conversion timeout in seconds.")
    convert_parser.add_argument("--verify-read", action="store_true", help="Perform readback content verification.")
    convert_parser.add_argument("--read-wps-script", help="Path to read_wps.py script.")

    args = parser.parse_args()

    if args.command == "check":
        res = check_environment(args.soffice, args.backend)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if res.get("status") == "environment-ok" else 1)

    if args.command == "inspect":
        validate_argument(args.file)
        res = inspect_wps_structure(args.file)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit((3 if res.get("partial") else 0) if res.get("valid") else 1)

    if args.command == "convert":
        from wps_delivery import finish_delivery
        validate_argument(args.input)
        validate_argument(args.output or Path(args.input).with_suffix(".wps").name, output=True)
        res = convert_docx_to_wps(
            args.input,
            args.output,
            backend=args.backend,
            custom_soffice=args.soffice,
            timeout=args.timeout,
        )
        if args.verify_read and res.get("status") in ("success", "partial"):
            res["readback"] = verify_readback(res["output_file"], args.read_wps_script)
            read_status = res["readback"]["status"]
            if read_status == "readback-partial":
                res.update(status="partial", partial=True)
            elif read_status != "readback-ok":
                res.update(status="readback-failed", error="Output exists, but requested readback did not succeed")
        finish_delivery(res, res.get('output_file') or args.output or Path(args.input).with_suffix('.wps').name, [args.input])
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if res.get("status") == "success" else 3 if res.get("status") == "partial" else 1)


if __name__ == "__main__":
    sys.exit(main())
