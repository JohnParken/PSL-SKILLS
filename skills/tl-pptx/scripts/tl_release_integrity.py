"""Verify the independent tl-pptx release, not the upstream official distribution.

The hash pins the canonical manifest. The verifier's own manifest entry hashes
its source with only this pin normalized to avoid a circular digest. Authenticity
of a whole replacement release requires checking its separately trusted ZIP hash.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

PIN = "340ec702c3a728ffcd6b992c55be32cf6d4066a4ad3ed527bf4d873ccb742e7a"
INDEX = "references/tl-release-integrity.json"
SELF = "scripts/tl_release_integrity.py"

def digest(data):
    """Return a lowercase SHA-256 fingerprint."""
    return hashlib.sha256(data).hexdigest()

def verify(root):
    """Fail on wrong identity, missing, added, changed or linked runtime files."""
    inventory = set()
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=_raise):
        base = Path(parent)
        for name in dirs[:]:
            path = base / name
            rel = path.relative_to(root).as_posix()
            if rel in {"scripts/test", "scripts/.venv"}:
                dirs.remove(name)
            elif path.is_symlink():
                return False
            elif name == "__pycache__":
                return False
        for name in files:
            path = base / name
            if not stat.S_ISREG(path.lstat().st_mode):
                return False
            if name != ".DS_Store":
                inventory.add(path.relative_to(root).as_posix())
    if INDEX not in inventory:
        return False
    index = (root / INDEX).read_bytes()
    if digest(index) != PIN:
        return False
    expected = json.loads(index)
    if expected["distribution"] != "tl-pptx (unofficial MIT derivative)":
        return False
    hashes = expected["sha256"]
    if set(hashes) != inventory - {INDEX}:
        return False
    for name in sorted(inventory - {INDEX}):
        data = (root / name).read_bytes()
        if name == SELF:
            marker = ('PIN = "' + PIN + '"').encode()
            if data.count(marker) != 1:
                return False
            data = data.replace(marker, ('PIN = "' + '0' * 64 + '"').encode())
        if digest(data) != hashes[name]:
            return False
    return True

def _raise(error):
    """Do not mistake unreadable directories for empty ones."""
    raise error
