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


# These route comparisons include unsupported baseline requests explicitly.
# Equal read sets do not imply equal capabilities or successful model execution.
ITERATION_PROFILES = {
    'docx-complete-table-read': ['docx/SKILL.md'],
    'pptx-full-inspect': ['pptx/SKILL.md'],
    'pptx-summary': ['pptx/SKILL.md'],
    'pptx-selected-slides': ['pptx/SKILL.md'],
    'pptx-notes-pagination': ['pptx/SKILL.md'],
    'pptx-partial-inspect': ['pptx/SKILL.md'],
    'et-overview': ['et/SKILL.md', 'et/references/bounded-reading.md'],
    'et-header-preview': ['et/SKILL.md', 'et/references/table-analysis.md'],
    'et-direct-statistics': ['et/SKILL.md', 'et/references/table-analysis.md'],
    'et-full-evidence': ['et/SKILL.md', 'et/references/spreadsheet-consumption.md'],
    'et-partial-pagination': ['et/SKILL.md', 'et/references/bounded-reading.md', 'et/references/reading-assessment.md'],
    'table-inspect-many-sheets': ['table-workflow/SKILL.md', 'xlsx/SKILL.md'],
    'table-plan-apply': ['table-workflow/SKILL.md', 'table-workflow/references/plan-contract.md',
                         'xlsx/SKILL.md', 'xlsx/references/edit.md', 'xlsx/references/write-contract.md'],
    'table-partial-et-source': ['table-workflow/SKILL.md', 'table-workflow/references/plan-contract.md',
                                'xlsx/SKILL.md', 'et/SKILL.md', 'et/references/spreadsheet-consumption.md'],
    'ofd-basic-create': ['ofd/SKILL.md', 'ofd/references/generation.md'],
    'ofd-partial-page-read': ['ofd/SKILL.md', 'ofd/references/output-views.md', 'ofd/references/boundaries.md'],
    'mixed-pptx-et-read': ['pptx/SKILL.md', 'et/SKILL.md', 'et/references/table-analysis.md'],
}
for _name, _paths in ITERATION_PROFILES.items():
    PROFILES[_name] = (_paths, _paths)
PROFILES['pptx-notes-pagination'] = (ITERATION_PROFILES['pptx-notes-pagination'],
                                     ['pptx/SKILL.md', 'pptx/references/bounded-reading.md'])

SCENARIOS.update({
    'docx-complete-table-read': 'Read more than two table rows with --tables rows; follow next_block without treating compact preview as complete.',
    'pptx-full-inspect': 'Keep legacy full inspection and complete package safety validation.',
    'pptx-summary': 'Request structure only; omit body/notes while retaining validation and partial diagnostics.',
    'pptx-selected-slides': 'Inspect explicitly selected slides; an unsafe unselected package part must still fail.',
    'pptx-notes-pagination': 'Select notes explicitly, page valid bounded JSON, and reject changed source/options or an oversized single page.',
    'pptx-partial-inspect': 'Explicit PNG zero-padding tolerance retains structural_validation=partial and exit 3 in every selected view.',
    'et-overview': 'Ask for sheet names and size only; choose summary rather than outputting cell values first.',
    'et-header-preview': 'Inspect a declared rectangular header area including blanks and merged anchors; do not infer header rows.',
    'et-direct-statistics': 'Call analyze_et for the complete declared region; output pages do not change statistics or quality denominators.',
    'et-full-evidence': 'Use original extraction schema for complete evidence or external consumers, preserving types and source diagnostics.',
    'et-partial-pagination': 'Follow the cell cursor while preserving source partial/exit 3, omissions and unchanged-source checks.',
    'table-inspect-many-sheets': 'Discover the ninth and later sheet names within the output cap; then inspect a chosen sheet, not all previews.',
    'table-plan-apply': 'Use explicit source and template semantics; public provider capabilities preserve decimal precision and protected cells.',
    'table-partial-et-source': 'Carry ET source coverage through inspection and enforce the existing plan acceptance gate.',
    'ofd-basic-create': 'Create only when explicitly requested; preserve font rights, resource bounds, publication and archive handoff.',
    'ofd-partial-page-read': 'Select a bounded page/text view without hiding source partial, unreturned regions or lack of visual validation.',
    'mixed-pptx-et-read': 'Union only selected-slide inspection and direct ET region analysis instructions; no recursive reference loading.',
})

CAPABILITY_CHANGES = {
    'docx-complete-table-read': 'Baseline implementation supported full table rows and block continuation; entry guidance was incomplete.',
    'pptx-summary': 'Baseline has full inspect only; the requested projection is unavailable before this iteration.',
    'pptx-selected-slides': 'Baseline has full inspect only; explicit selected-slide projection is unavailable before this iteration.',
    'pptx-notes-pagination': 'Baseline has no output budget/cursor or notes selection; equal file counts are not feature parity.',
    'table-inspect-many-sheets': 'Baseline shows at most eight previews and cannot discover later names through this workflow entry.',
}


# Final entry routes: a plan, not evidence of actual host selection or caching.
PROFILES['xlsx-summary'] = (PROFILES['xlsx-summary'][0], ['xlsx/SKILL.md'])
PROFILES['mixed-format-read'] = (PROFILES['mixed-format-read'][0],
                                  ['xlsx/SKILL.md', 'et/SKILL.md', 'et/references/bounded-reading.md', 'wps/SKILL.md'])
SCENARIOS['xlsx-summary'] = 'Structure-only native XLSX summary using the complete entry command; no cell values or pagination.'

for _name, _reference in {
    'et-header-preview': 'table-preview',
    'et-direct-statistics': 'basic-statistics',
}.items():
    PROFILES[_name] = (PROFILES[_name][0], ['et/SKILL.md', f'et/references/{_reference}.md'])
PROFILES['mixed-pptx-et-read'] = (PROFILES['mixed-pptx-et-read'][0],
                                  ['pptx/SKILL.md', 'et/SKILL.md', 'et/references/basic-statistics.md'])
PROFILES['et-complex-query'] = (['et/SKILL.md', 'et/references/table-analysis.md'],
                                ['et/SKILL.md', 'et/references/table-analysis.md'])
SCENARIOS['et-complex-query'] = 'Explicit filtering, grouping and sorting query; retain source partial and query semantics.'
SPECIALIZED_REFERENCES = (
    'maturity-distribution', 'customer-structure', 'deposit-analysis', 'management-policy',
    'management-reconciliation', 'management-dashboard', 'management-wide', 'institution-hierarchy',
    'daily-analysis', 'rank-share', 'advanced-statistics', 'period-comparison', 'table-matching',
    'row-processing', 'header-candidates', 'multi-level-headers', 'result-export',
)
for _reference in SPECIALIZED_REFERENCES:
    _required = [f'xlsx/references/{_reference}.md']
    if _reference == 'institution-hierarchy':
        _required += ['xlsx/references/management-wide.md']
    if _reference == 'result-export':
        _required += ['xlsx/references/task-delivery.md']
    PROFILES['xlsx-specialized-' + _reference] = (
        ['xlsx/SKILL.md', 'xlsx/references/data-analysis.md'] + _required,
        ['xlsx/SKILL.md', 'xlsx/references/analysis-contract.md'] + _required,
    )
    SCENARIOS['xlsx-specialized-' + _reference] = (
        f'Explicit {_reference} task using existing inputs/configuration; no optional export or formula recalculation. '
        'Result-export alone writes the requested XLSX and reads delivery rules; other scenarios return stdout.'
    )
CAPABILITY_CHANGES.update({
    'et-header-preview': 'Baseline supports preview, but not the new bounded output cursor/budget; file-size changes do not prove feature-equivalent savings.',
    'et-direct-statistics': 'Baseline supports regional statistics, but not the new bounded output cursor/budget; statistics correctness is tested separately.',
    'et-complex-query': 'Baseline supports queries, but not the new bounded output cursor/budget.',
})


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
        profiles[name] = dict(scenario=SCENARIOS.get(name, 'Planned whole-file reads for the named operation.'),
                              baseline_capability_note=CAPABILITY_CHANGES.get(name), behavior_validation='not_measured_by_loading_benchmark',
                              before=before, after=after,
                              delta_after_minus_before={unit: None if after[unit] is None else after[unit] - before[unit]
                                                        for unit in ('chars', 'bytes', 'tokens', 'read_events')})
    return dict(schema_version=3, benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                method='planned reference read events, not observed model routing', baseline_commit=revision,
                after_source='working tree; per-file SHA256 recorded', platform=platform.system(), python=platform.python_version(),
                tokenizer=tokenizer or dict(status='not_measured'), host_usage=dict(status='not_measured',
                reason='No Deep Agents trace, provider usage or model-visible tool payload captured.'), profiles=profiles,
                limitations=['Characters and bytes are proxies only; no conversion to token estimates.',
                             'No automatic cache, route choice, task correctness or host savings is established by this report.',
                             'Repeated-read scenarios are assumptions, not observed host caching or billing.',
                             'ET profiles correct stale WPS routing in BOTH comparison sides.',
                             'Platform is the measurement platform, not a declaration of Linux runtime validation.',
                             'New capability scenarios may be unsupported at baseline; see baseline_capability_note.',
                             'Runtime output budgets, RPC counts and correctness require their separate regression reports.'])


def check_snapshot(snapshot, baseline=BASELINE, encoder=None, tokenizer=None):
    """Recompute both sides; snapshot content is evidence to check, not authority."""
    issues = []

    def compare(saved, current, path):
        if type(saved) is not type(current):
            issues.append(dict(path=path, reason='type_changed'))
        elif isinstance(current, dict):
            for key in sorted(saved.keys() - current.keys()):
                issues.append(dict(path=f'{path}.{key}', reason='unexpected_field'))
            for key in sorted(current.keys() - saved.keys()):
                issues.append(dict(path=f'{path}.{key}', reason='missing_field'))
            for key in sorted(current.keys() & saved.keys()):
                compare(saved[key], current[key], f'{path}.{key}')
        elif isinstance(current, list):
            if len(saved) != len(current):
                issues.append(dict(path=path, reason='length_changed'))
            for index, (old, new) in enumerate(zip(saved, current)):
                compare(old, new, f'{path}[{index}]')
        elif saved != current:
            issues.append(dict(path=path, reason='value_changed'))

    try:
        current = build_report(baseline, encoder, tokenizer)
        if not isinstance(snapshot, dict):
            raise ValueError('snapshot must be a JSON object')
        # Environment records describe the original measurement, not file freshness.
        excluded = {'platform', 'python'}
        compare({k: v for k, v in snapshot.items() if k not in excluded},
                {k: v for k, v in current.items() if k not in excluded}, 'snapshot')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        issues.append(dict(path='sources', reason='unavailable', detail=str(exc)))
    return dict(status='stale' if issues else 'fresh', issues=issues,
                scope='Current planned read sets, benchmark implementation and both-side file evidence; not observed host behavior.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default=BASELINE)
    parser.add_argument('--check-snapshot', type=Path, help='Validate saved evidence against current plans and files; stale exits 1')
    parser.add_argument('--encoding', help='Optional installed tiktoken encoding; exact file token counts only')
    parser.add_argument('--tokenizer-cache', help='Existing offline tiktoken cache; never downloaded or modified')
    args = parser.parse_args()
    try:
        encoder, metadata = load_tokenizer(args.encoding, args.tokenizer_cache)
        if args.check_snapshot:
            snapshot = json.loads(args.check_snapshot.read_text(encoding='utf-8'))
            report = check_snapshot(snapshot, args.baseline, encoder, metadata)
        else:
            report = build_report(args.baseline, encoder, metadata)
    except (ValueError, OSError, ImportError, subprocess.CalledProcessError) as exc:
        if args.check_snapshot:
            print(json.dumps(dict(status='stale', issues=[dict(path='snapshot', reason='unreadable', detail=str(exc))])))
        else:
            print(f'benchmark failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get('status') == 'stale' else 0


if __name__ == '__main__':
    raise SystemExit(main())
