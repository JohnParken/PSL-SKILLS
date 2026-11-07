"""Repository-only planned loading comparison, never Deep Agents telemetry.

Python >=3.10. Default uses the standard library. Optional tiktoken is a
maintenance dependency, not a Skill dependency; no installation or downloads.
See tests/skill_loading_benchmark.md for scope and reproducible commands.
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
BASELINE = '4b5a61f51d12d2a9d293898c2f862cbf7271dc8d'
CREATE_BEFORE = ['xlsx/SKILL.md', 'xlsx/references/create.md',
                 'xlsx/references/write-contract.md', 'xlsx/references/task-delivery.md']
FORMULA = CREATE_BEFORE + ['xlsx/references/formula-calculation.md', 'xlsx/references/complex-json.md']
# Lists represent whole-file read events, including intentional repetitions.
# Before routes are corrected against baseline instructions, not the stale JSON.
PROFILES = {
    'docx-simple': (['docx/SKILL.md'], ['docx/SKILL.md']),
    'xlsx-summary': (['xlsx/SKILL.md', 'xlsx/references/bounded-reading.md'],
                     ['xlsx/SKILL.md', 'xlsx/references/bounded-reading.md']),
    'xlsx-static-create': (CREATE_BEFORE, ['xlsx/SKILL.md']),
    'xlsx-formula-partial': (FORMULA, FORMULA),
    'xlsx-tool-failure': (['xlsx/SKILL.md', 'xlsx/references/tool-execution.md', 'xlsx/references/environment-results.md'],
                          ['xlsx/SKILL.md', 'xlsx/references/tool-execution.md', 'xlsx/references/environment-results.md']),
    'xlsx-et-analysis': (['xlsx/SKILL.md', 'xlsx/references/data-analysis.md',
                          'et/SKILL.md', 'et/references/spreadsheet-consumption.md'],
                         ['xlsx/SKILL.md', 'xlsx/references/data-analysis.md',
                          'et/SKILL.md', 'et/references/spreadsheet-consumption.md']),
    # Historical profile names retained for comparisons; ET never routes to WPS text.
    'wps-default-et': (['et/SKILL.md'], ['et/SKILL.md']),
    'wps-selected-cells': (['et/SKILL.md', 'et/references/bounded-reading.md'],
                            ['et/SKILL.md', 'et/references/bounded-reading.md']),
    'wps-text-read': (['wps/SKILL.md'], ['wps/SKILL.md']),
    # Baseline already contains the complete basic stdin example; its link is optional.
    'wps-static-create': (['wps/SKILL.md'], ['wps/SKILL.md']),
    'wps-complex-create': (['wps/SKILL.md', 'wps/references/generation.md'],
                           ['wps/SKILL.md', 'wps/references/generation.md']),
    'mixed-format-read': (['xlsx/SKILL.md', 'xlsx/references/bounded-reading.md',
                           'et/SKILL.md', 'et/references/bounded-reading.md', 'wps/SKILL.md'],
                          ['xlsx/SKILL.md', 'xlsx/references/bounded-reading.md',
                           'et/SKILL.md', 'et/references/bounded-reading.md', 'wps/SKILL.md']),
    'repeated-create-cold': (CREATE_BEFORE * 2, ['xlsx/SKILL.md'] * 2),
    'repeated-create-retained': (CREATE_BEFORE, ['xlsx/SKILL.md']),
    'repeated-create-compacted': (CREATE_BEFORE * 2, ['xlsx/SKILL.md'] * 2),
}
# Preserve unrelated historical scenarios while refreshing their actual files.
for _name, _paths in {
    'docx-archive-failure': ['docx/SKILL.md', 'docx/references/delivery.md'],
    'docx-cleanup-recovery': ['docx/SKILL.md', 'docx/references/task-cleanup.md'],
    'xlsx-wps-analysis': ['xlsx/SKILL.md', 'xlsx/references/data-analysis.md'],
    'ofd-summary': ['ofd/SKILL.md', 'ofd/references/output-views.md'],
    'bank-writing': ['bank-writing/SKILL.md'],
    'artifact-independent': ['artifact-lifecycle/SKILL.md', 'artifact-lifecycle/references/contract.md'],
    'maintenance-only': ['skill-development-standards/SKILL.md'],
}.items():
    PROFILES[_name] = (_paths, _paths)

SCENARIOS = {
    'xlsx-static-create': 'One static table, no formulas or custom appearance; verify values and delivery/cleanup.',
    'xlsx-formula-partial': 'Complex create with unsupported formula; retain incomplete/exit 2 and issues, never claim full calculation.',
    'xlsx-tool-failure': 'Missing or truncated tool result; stop dependent actions and inspect required diagnostics.',
    'xlsx-et-analysis': 'Consume full ET extraction JSON with original partial and cache diagnostics, not a projected view.',
    'wps-static-create': 'Basic title, paragraph and table from stdin; BOTH versions are entry-only, with no advanced layout or saved temporary specification.',
    'wps-complex-create': 'Advanced layout, merged cells or non-default page settings; inspect generation reference.',
    'mixed-format-read': 'Read native XLSX summary, ET selected cells and WPS text independently; union only required references.',
    'repeated-create-cold': 'Two independent tasks: read instructions twice; never reuse task authorization.',
    'repeated-create-retained': 'Two operations in the SAME task, unchanged full instructions still in context: one planned read.',
    'repeated-create-compacted': 'Two operations with missing instructions after compaction: reread required files.',
}


def validate_name(name):
    if not isinstance(name, str):
        raise ValueError('profile paths must be strings')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or not path.parts:
        raise ValueError(f'invalid skill-relative path: {name!r}')
    return path


def read_resource(name, revision=None, root=ROOT):
    path = validate_name(name)
    if revision:
        full = 'skills/' + path.as_posix()
        entry = subprocess.run(['git', 'ls-tree', revision, '--', full], cwd=root, check=True, capture_output=True).stdout
        if not entry.startswith(b'100644 blob ') and not entry.startswith(b'100755 blob '):
            raise ValueError(f'missing or linked baseline resource: {name}')
        return subprocess.run(['git', 'show', f'{revision}:{full}'], cwd=root, check=True, capture_output=True).stdout
    base = root / 'skills'
    current = base
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'linked profile resource: {name}')
    if not current.is_file():
        raise ValueError(f'missing profile resource: {name}')
    return current.read_bytes()


def measure(paths, revision=None, root=ROOT, encoder=None):
    files = []
    for name in paths:
        data = read_resource(name, revision, root)
        text = data.decode('utf-8')
        files.append(dict(path=name, chars=len(text), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          tokens=None if encoder is None else len(encoder.encode(text, disallowed_special=()))))
    return dict(files=files, read_events=len(files), unique_files=len(set(paths)),
                chars=sum(item['chars'] for item in files), bytes=sum(item['bytes'] for item in files),
                tokens=None if encoder is None else sum(item['tokens'] for item in files))


def load_tokenizer(encoding, cache_dir):
    """Read existing cache only. Missing optional dependency/assets fails explicitly."""
    if not encoding:
        return None, dict(status='not_measured', reason='No tokenizer requested; characters and bytes are not tokens.')
    import importlib.metadata
    import tiktoken
    import tiktoken.load
    cache = Path(cache_dir).resolve() if cache_dir else None
    if cache is None or not cache.is_dir():
        raise ValueError('--encoding requires an existing --tokenizer-cache directory; no downloads are allowed')
    original = tiktoken.load.read_file_cached

    def cached_only(blobpath, expected_hash=None):
        candidate = cache / hashlib.sha1(blobpath.encode()).hexdigest()
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError(f'offline tokenizer cache missing: {candidate.name}')
        data = candidate.read_bytes()
        if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError('tokenizer cache hash mismatch')
        return data

    tiktoken.load.read_file_cached = cached_only
    try:
        encoder = tiktoken.get_encoding(encoding)
    finally:
        tiktoken.load.read_file_cached = original
    return encoder, dict(status='measured', library='tiktoken', version=importlib.metadata.version('tiktoken'), encoding=encoding,
                         scope='Sum of independently encoded whole-file read events; not model chat framing, billing or host usage.')


def build_report(baseline=BASELINE, encoder=None, tokenizer=None):
    revision = subprocess.run(['git', 'rev-parse', '--verify', baseline + '^{commit}'], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
    profiles = {}
    for name, (before_paths, after_paths) in PROFILES.items():
        before = measure(before_paths, revision, encoder=encoder)
        after = measure(after_paths, encoder=encoder)
        profiles[name] = dict(scenario=SCENARIOS.get(name, 'Planned whole-file reads for the named operation.'), before=before, after=after,
                              delta_after_minus_before={unit: None if after[unit] is None else after[unit] - before[unit]
                                                        for unit in ('chars', 'bytes', 'tokens', 'read_events')})
    return dict(schema_version=2, method='planned reference read events, not observed model routing', baseline_commit=revision,
                after_source='working tree; per-file SHA256 recorded', platform=platform.system(), python=platform.python_version(),
                tokenizer=tokenizer or dict(status='not_measured'), host_usage=dict(status='not_measured',
                reason='No Deep Agents trace, provider usage or model-visible tool payload captured.'), profiles=profiles,
                limitations=['Characters and bytes are proxies only; no conversion to token estimates.',
                             'No automatic cache, route choice, task correctness or host savings is established by this report.',
                             'Repeated-read scenarios are assumptions, not observed host caching or billing.',
                             'ET profiles correct stale WPS routing in BOTH comparison sides.',
                             'Platform is the measurement platform, not a declaration of Linux runtime validation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default=BASELINE)
    parser.add_argument('--encoding', help='Optional installed tiktoken encoding; exact file token counts only')
    parser.add_argument('--tokenizer-cache', help='Existing offline tiktoken cache; never downloaded or modified')
    args = parser.parse_args()
    try:
        encoder, metadata = load_tokenizer(args.encoding, args.tokenizer_cache)
        report = build_report(args.baseline, encoder, metadata)
    except (ValueError, OSError, ImportError, subprocess.CalledProcessError) as exc:
        print(f'benchmark failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
