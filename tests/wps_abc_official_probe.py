"""Maintenance-only bounded inspection of public Agricultural Bank attachments.

These are DIRECT official abchina.com attachments, not user files. No content is
executed or written, and no document text is printed. The test is a format
compatibility observation, NOT native .wps producer validation.
"""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen, Request

MAX_BYTES = 32 * 1024 * 1024
OFFICIAL_HOSTS = frozenset(("www.abchina.com", "www.abchina.com.cn"))
SAMPLES = [
    {
        "id": "gd_authorization",
        "page": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/t20240407_2412719.htm",
        "url": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/W020240407540325949009.doc",
        "kind": "doc", "anchors": ["授权"],
    },
    {
        "id": "gd_registration",
        "page": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/t20240407_2412719.htm",
        "url": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/W020240407540326257571.doc",
        "kind": "doc", "anchors": ["报名"],
    },
    {
        "id": "gd_supplier_guide",
        "page": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/t20240407_2412719.htm",
        "url": "https://www.abchina.com/cn/branch/gd/CG/BM/202404/W020240407540326417400.doc",
        "kind": "doc", "anchors": ["供应商"],
    },
    {
        "id": "gx_nda_commitment",
        "page": "https://www.abchina.com/cn/branch/gx/cg/bm/202404/t20240425_2415781.htm",
        "url": "https://www.abchina.com/cn/branch/gx/cg/bm/202404/W020240425607944480483.doc",
        "kind": "doc", "anchors": ["授权", "承诺"],
    },
    {
        "id": "gx_smart_park",
        "page": "https://www.abchina.com/cn/branch/gx/cg/bm/202402/t20240218_2396189.htm",
        "url": "https://www.abchina.com/cn/branch/gx/cg/bm/202402/W020240218572546885319.doc",
        "kind": "doc", "anchors": ["授权", "承诺"],
    },
    {
        "id": "headquarters_2026_requirement",
        "page": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/t20261023_2689689.htm",
        "url": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/W020261023680086170794.pdf",
        "kind": "pdf", "anchors": [],
    },
    {
        "id": "headquarters_2026_registration",
        "page": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/t20261023_2689689.htm",
        "url": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/W020261023680086634584.pdf",
        "kind": "pdf", "anchors": [],
    },
    {
        "id": "headquarters_2026_response",
        "page": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/t20261023_2689689.htm",
        "url": "https://www.abchina.com/cn/aboutabc/cg/BM/202610/W020261023680086881485.pdf",
        "kind": "pdf", "anchors": [],
    },
]


def _safe_host(url):
    x = urlsplit(url)
    return x.scheme == "https" and x.hostname in OFFICIAL_HOSTS


def _download(url):
    if not _safe_host(url):
        raise ValueError("non-official HTTPS target")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (read-only format compatibility check)"})
    with urlopen(request, timeout=16) as response:
        if not _safe_host(response.geturl()):
            raise ValueError("redirect outside abchina official hosts")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_BYTES:
            raise ValueError("declared size exceeds budget")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("body exceeds budget")
    return data


def _format(data):
    if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return "CFB_OLE"
    if data.startswith(b"%PDF-"):
        return "PDF"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "ZIP"
    if data.lstrip()[:16].lower().startswith((b"<html", b"<!doctype html", b"<?xml")):
        return "HTML_XML"
    return "UNKNOWN"


def _inspect(item, read_bytes):
    entry = {
        "id": item["id"], "official_page": item["page"],
        "official_url": item["url"], "advertised_extension": item["kind"],
        "origin": "official_website_published",
        "wps_native_producer_verified": False,
        "wps_visual_compatibility_verified": False,
    }
    try:
        data = _download(item["url"])
        entry.update({"download": "success", "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "magic": _format(data)})
        if entry["magic"] == "PDF":
            entry["reader_scope"] = "not_applicable_pdf"
            return entry
        if entry["magic"] not in ("CFB_OLE", "ZIP"):
            entry["reader_scope"] = "not_a_supported_document_container"
            return entry
        try:
            result = read_bytes(data, compatibility="data", object_policy="placeholder")
            entry.update({"reader_status": result.get("status"),
                          "reader_format": result.get("format"),
                          "paragraph_count": len(result.get("paragraphs", [])),
                          "anchors": {a: a in result.get("text", "") for a in item["anchors"]},
                          "issue_codes": [x.get("code") for x in result.get("issues", [])[:12] if isinstance(x, dict)]})
        except Exception as error:
            entry.update({"reader_status": "rejected",
                          "reader_error_class": type(error).__name__,
                          "reader_error_status": getattr(error, "status", None),
                          "reader_error_message": str(error)[:130],
                          "reader_error_location": {
                              key: value for key,value in getattr(error, "location", {}).items()
                              if key in ("stream", "offset", "stage") and type(value) in (int,str)
                          }})
    except Exception as error:
        entry.update({"download": "failed", "download_error": type(error).__name__,
                      "download_detail": str(error)[:110]})
    return entry


def main():
    scripts = Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts"
    sys.path.insert(0, str(scripts))
    from read_wps import read_bytes
    results = [_inspect(item, read_bytes) for item in SAMPLES]
    report = {
        "schema_version": "wps_abc_official_attachment_probe_v1",
        "purpose": "ABC official .doc probes of shared MS-DOC/CFB subset, not native .wps acceptance",
        "total": len(results),
        "downloaded": sum(x.get("download") == "success" for x in results),
        "cfb_count": sum(x.get("magic") == "CFB_OLE" for x in results),
        "pdf_controls": sum(x.get("magic") == "PDF" for x in results),
        "supported_reads": sum(x.get("reader_status") in ("ok","partial") for x in results),
        "producer_verified": False,
        "full_wps_format_verified": False,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["downloaded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
