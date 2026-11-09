"""Build a reviewed, self-contained Skill runtime ZIP (repository maintenance only).

No business implementation is transformed. Tests, maintenance-only material and
caches stay in the source checkout and are excluded by a reviewed manifest.
New unclassified files fail closed. Existing output is never overwritten.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
MANIFESTS = REPO / "packaging" / "runtime-manifests"


def _name(source):
    entry = Path(source) / "SKILL.md"
    if entry.is_symlink():
        raise ValueError("Symlink Skill entry")
    text = entry.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    names = re.findall(r"^name: ([a-z][a-z0-9-]*)$", match[1] if match else "", re.M)
    if len(names) != 1:
        raise ValueError("Expected one safe Skill name in frontmatter")
    return names[0]


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Manifest paths must be nonempty POSIX relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Unsafe manifest path: " + value)
    return value


def _manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or set(data) != {"schema_version", "files", "exclude_prefixes", "exclude_files"}):
        raise ValueError("Unsupported runtime manifest")
    if not isinstance(data["files"], list) or not data["files"]:
        raise ValueError("Runtime manifest must contain files")
    files = [_relative(value) for value in data["files"]]
    if len(set(files)) != len(files):
        raise ValueError("Duplicate runtime manifest file")
    if any(name.startswith("assets/user_fonts/") and name != "assets/user_fonts/NOTICE.txt" for name in files):
        raise ValueError("Private installation fonts cannot be distribution members")
    if not isinstance(data["exclude_prefixes"], list) or not isinstance(data["exclude_files"], dict):
        raise ValueError("Invalid manifest exclusions")
    prefixes = []
    for prefix in data["exclude_prefixes"]:
        if not isinstance(prefix, str) or not prefix.endswith("/"):
            raise ValueError("Excluded directory prefixes must end with /")
        prefixes.append(_relative(prefix[:-1]) + "/")
    exclusions = data["exclude_files"]
    for name, reason in exclusions.items():
        _relative(name)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Each exclusion needs a reason")
    if any(name in exclusions or any(name.startswith(prefix) for prefix in prefixes)
           and name != "assets/user_fonts/NOTICE.txt" for name in files):
        raise ValueError("Runtime file also excluded")
    return set(files), tuple(prefixes), exclusions


def _inventory(source, manifest=None):
    root = Path(source)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Source must be a real Skill source directory")
    required, prefixes, exclusions = _manifest(manifest or MANIFESTS / (_name(root) + ".json"))
    selected, excluded = {}, []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        # Explicitly excluded private installation assets are never read. Only
        # reviewed, individually listed members (such as NOTICE.txt) are kept.
        if name.startswith("assets/user_fonts/") and name not in required:
            excluded.append({"path": name, "bytes": 0, "reason": "private installation font; not inspected"})
            continue
        if path.is_symlink():
            raise ValueError("Symlink in distribution source: " + name)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Non-regular source member: " + name)
        cache = ("__pycache__" in path.relative_to(root).parts
                 or path.name == ".DS_Store" or path.suffix in {".pyc", ".pyo"})
        if not cache and name in required:
            selected[name] = path.read_bytes()
        elif cache or name in exclusions or any(name.startswith(p) for p in prefixes):
            excluded.append({"path": name, "bytes": path.stat().st_size,
                             "reason": "cache" if cache else exclusions.get(name, "source-only tests")})
        else:
            raise ValueError("Unclassified source file; review runtime manifest: " + name)
    missing = sorted(required - selected.keys())
    if missing:
        raise ValueError("Missing runtime source files: " + ", ".join(missing))
    return selected, excluded


def collect_files(source, manifest=None):
    """Read one source snapshot; keys are relative paths without the Skill-name prefix."""
    return _inventory(source, manifest)[0]


def _version(files):
    match = re.search(rb'^version: "([0-9]{8}\.[0-9]{4}\.[0-9]{4})"$',
                      files.get("SKILL.md", b""), re.MULTILINE)
    if not match:
        raise ValueError("Missing quoted timestamp version in SKILL.md")
    return match[1].decode("ascii")


def build_package(source, output, manifest=None):
    """Build deterministic ZIP bytes, verify the snapshot and publish a new file."""
    root, target = Path(source), Path(output)
    if target.suffix.lower() != ".zip":
        raise ValueError("Runtime package output must use .zip")
    if target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Runtime package output must be outside the source directory")
    files, excluded = _inventory(root, manifest)
    version = _version(files)
    skill = _name(root)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, contents in sorted(files.items()):
            info = zipfile.ZipInfo(skill + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    payload = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() is not None or len(archive.infolist()) != len(files):
            raise ValueError("Runtime ZIP integrity verification failed")
        if set(archive.namelist()) != {skill + "/" + name for name in files}:
            raise ValueError("Runtime ZIP member list differs from manifest")
        for name, contents in files.items():
            if archive.read(skill + "/" + name) != contents:
                raise ValueError("Runtime ZIP differs from source snapshot: " + name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o644)
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            current = target.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                target.unlink()
        except FileNotFoundError:
            pass
        raise
    return {"status": "packaged", "skill": skill, "version": version, "output": str(target),
            "files": len(files), "uncompressed_bytes": sum(map(len, files.values())),
            "zip_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "excluded_files": excluded, "excluded_bytes": sum(item["bytes"] for item in excluded),
            "verification": "CRC, exact members and source snapshot byte parity",
            "runtime_tests": "not run by packager"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skills", nargs="*", help="Skill names; default: all current skills")
    parser.add_argument("--output-dir", type=Path, default=REPO / "dist" / "runtime")
    args = parser.parse_args(argv)
    try:
        available = {path.parent.name: path.parent for path in (REPO / "skills").glob("*/SKILL.md")}
        names = args.skills or sorted(available)
        if len(names) != len(set(names)) or set(names) - available.keys():
            raise ValueError("Unknown or duplicate Skill name")
        # Preflight every source and destination before publishing any package.
        targets = []
        for name in names:
            source = available[name]
            files = collect_files(source)
            target = args.output_dir / (name + "-runtime-" + _version(files) + ".zip")
            if target.exists() or target.is_symlink():
                raise FileExistsError("Refusing to overwrite " + str(target))
            targets.append((source, target))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        reports = [build_package(source, target) for source, target in targets]
        print(json.dumps({"status": "packaged", "packages": reports}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("Runtime packaging failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
