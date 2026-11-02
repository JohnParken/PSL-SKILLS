"""Reproducible planned-reference sizes; not model routing or token telemetry.

Run from the repository root with Python >=3.10 and -I -S -B. Profiles are
explicit task assumptions; reading a link does not imply loading its target.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {
    'docx-simple': ['docx/SKILL.md'],
    'docx-archive-failure': ['docx/SKILL.md', 'docx/references/delivery.md'],
    'docx-cleanup-recovery': ['docx/SKILL.md', 'docx/references/task-cleanup.md'],
    'xlsx-summary': ['xlsx/SKILL.md', 'xlsx/references/bounded-reading.md'],
    'xlsx-wps-analysis': ['xlsx/SKILL.md', 'xlsx/references/data-analysis.md'],
    'wps-default-et': ['wps/SKILL.md'],
    'wps-selected-cells': ['wps/SKILL.md', 'wps/references/bounded-reading.md'],
    'ofd-summary': ['ofd/SKILL.md', 'ofd/references/output-views.md'],
    'bank-writing': ['bank-writing/SKILL.md'],
    'artifact-independent': ['artifact-lifecycle/SKILL.md', 'artifact-lifecycle/references/contract.md'],
    'maintenance-only': ['skill-development-standards/SKILL.md'],
}


def measure(paths):
    """Only existing regular local reference files participate in planned totals."""
    files = []
    for name in paths:
        path = ROOT / 'skills' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'missing or linked profile resource: {name}')
        data = path.read_bytes()
        files.append(dict(path=name, chars=len(data.decode('utf-8')), bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    return dict(files=files, chars=sum(item['chars'] for item in files), bytes=sum(item['bytes'] for item in files))


if __name__ == '__main__':
    print(json.dumps(dict(method='planned UTF-8 reference sizes, not host usage or tokens',
                          profiles={name: measure(paths) for name, paths in PROFILES.items()},
                          repeated_task_assumption='Reuse is valid only if unchanged content remains in context; host behavior unverified'),
                     ensure_ascii=False, indent=2))
