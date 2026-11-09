"""Compatibility entry for reviewed OFD runtime packaging; repository only."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_skills import build_package


def package(root, output):
    return build_package(root, output)["files"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New ZIP outside the source directory")
    args = parser.parse_args()
    print("packaged files:", package(Path(__file__).resolve().parents[1] / "skills/ofd", args.output))


if __name__ == "__main__":
    main()
