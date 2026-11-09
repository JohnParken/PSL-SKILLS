"""Verify exact release archives, then run isolated package acceptance (no network).

Requires the same tools, tests and manifests shipped in verification/. Does not
mutate release ZIPs. Requires non-root Python >=3.12.11 / Expat >=2.6 for tests.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = json.loads((args.release_dir / "release.json").read_text(encoding="utf-8"))
    names = [item["skill"] for item in report["packages"]]
    if len(names) != 10 or len(set(names)) != 10:
        raise ValueError("Expected exactly ten distinct Skill packages")
    with tempfile.TemporaryDirectory(prefix="verify-skill-release-") as directory:
        project = Path(directory)
        (project / "tools").mkdir()
        (project / "tests").mkdir()
        shutil.copy2(ROOT / "tools/package_skills.py", project / "tools/package_skills.py")
        shutil.copy2(ROOT / "tests/test_skill_runtime_packages.py", project / "tests/test_skill_runtime_packages.py")
        shutil.copytree(ROOT / "packaging/runtime-manifests", project / "packaging/runtime-manifests")
        for item in report["packages"]:
            filename = item["file"]
            if Path(filename).name != filename or not filename.endswith(".zip"):
                raise ValueError("Unsafe release filename")
            archive_path = args.release_dir / filename
            if archive_path.is_symlink():
                raise ValueError("Symlink release archive")
            if hashlib.sha256(archive_path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Release hash differs: " + filename)
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) != item["files"] or len({m.filename for m in members}) != len(members):
                    raise ValueError("Unexpected member count or duplicates")
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (not path.parts or path.parts[0] != item["skill"] or path.is_absolute()
                            or any(part in (".", "..", "") for part in member.filename.split("/"))
                            or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                        raise ValueError("Unsafe release member")
                    target = project / "skills" / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as handle:
                        handle.write(archive.read(member))
            print("Verified archive: " + filename, flush=True)
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-m", "unittest", "discover",
                                 "-s", str(project / "tests"), "-p", "test_skill_runtime_packages.py", "-v"],
                                cwd=project, check=False)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
