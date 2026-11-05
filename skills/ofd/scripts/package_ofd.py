"""Maintenance packaging: exclude installation-local user fonts; never overwrite output."""
import argparse
from pathlib import Path
import zipfile


def package(root, output):
    root = Path(root)
    files = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[:2] == ('assets', 'user_fonts') and relative.as_posix() != 'assets/user_fonts/NOTICE.txt':
            continue
        if '__pycache__' in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError('symlink in distribution source')
        if path.is_file():
            files.append((path, relative))
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path, relative in sorted(files):
            archive.write(path, 'ofd/' + relative.as_posix())
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='new ZIP in confirmed maintenance work directory')
    args = parser.parse_args()
    print('packaged files:', package(Path(__file__).resolve().parent.parent, args.output))


if __name__ == '__main__':
    main()
