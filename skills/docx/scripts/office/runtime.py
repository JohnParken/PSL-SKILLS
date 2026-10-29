"""Shared DOCX runtime checks without task-file or output-directory access."""
import re
import sys
import zlib
from xml.parsers import expat
from xml.etree import ElementTree as ET


def check_runtime() -> bool:
    checks_passed = True
    version = ".".join(map(str, sys.version_info[:3]))
    print(f"python={version} required>=3.10 supported={sys.version_info >= (3, 10)}")
    if sys.version_info < (3, 10):
        checks_passed = False
    try:
        payload = b"docx-runtime-check"
        if zlib.decompress(zlib.compress(payload)) != payload:
            checks_passed = False
            print("zlib=failed")
        else:
            print("zlib=ok")
    except Exception as exc:
        checks_passed = False
        print(f"zlib=failed reason={exc}")
    try:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", expat.EXPAT_VERSION)
        expat_version = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if expat_version < (2, 6, 0):
            checks_passed = False
        print(f"expat={expat.EXPAT_VERSION} required>=2.6.0 supported={expat_version >= (2, 6, 0)}")
        ET.fromstring(b"<runtime-check><ok/></runtime-check>")
    except Exception as exc:
        checks_passed = False
        print(f"expat-xml=failed reason={exc}")

    return checks_passed
