#!/usr/bin/env python3
"""Opt-in, stdlib-only reference-read observation and deterministic load planning.

This utility never intercepts model/tool reads automatically. Only reads performed
through `read` or events supplied by the host to `audit` can be observed. Usage
counts are reported as measured only when the host supplies them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parent.parent

# Suggested *minimum* document sets. These are not an instruction to read all
# documents, nor evidence of what a model actually loaded.
TASKS = {
    # Normal operations need only SKILL.md. These are suggested sets, not hooks.
    "simple": ("SKILL.md",),
    "simple-list": ("SKILL.md",),
    "simple-table": ("SKILL.md",),
    "read": ("SKILL.md",),
    "read-summary": ("SKILL.md",),
    "read-headings": ("SKILL.md",),
    "read-section": ("SKILL.md",),
    "list": ("SKILL.md", "references/generation-blocks.md"),
    "styles": ("SKILL.md", "references/generation-styles.md"),
    "table": ("SKILL.md", "references/generation-tables.md"),
    "complex-table": ("SKILL.md", "references/generation-tables.md", "references/p1-layout.md"),
    "create-image": ("SKILL.md", "references/generation-blocks.md"),
    "create-numbering": ("SKILL.md", "references/p1-layout.md"),
    "sections": ("SKILL.md", "references/p2-layout.md"),
    "read-advanced": ("SKILL.md", "references/inspection.md"),
    "edit": ("SKILL.md", "references/ooxml-editing.md"),
    "edit-text": ("SKILL.md", "references/ooxml-editing.md"),
    "edit-structure": ("SKILL.md", "references/structure-editing.md"),
    "edit-table": ("SKILL.md", "references/table-media-editing.md"),
    # Broad requests still start with the core contract, then expand by need.
    "full-report": ("SKILL.md", "references/generation-core.md"),
    "delivery-diagnostics": ("SKILL.md", "references/delivery.md"),
    "generation-error": ("SKILL.md", "references/controlled-flow.md"),
    "cleanup-recovery": ("SKILL.md", "references/task-cleanup.md"),
    "legacy-runtime": ("SKILL.md", "references/generation-runtime.md"),
}



def source_path(name):
    """Allow only the Skill entry and flat, existing Markdown references."""
    if not isinstance(name, str) or not name:
        raise ValueError("reference path is empty")
    if name != "SKILL.md" and not (name.startswith("references/") and name.count("/") == 1 and name.endswith(".md")):
        raise ValueError("reference must be SKILL.md or references/<name>.md")
    p = ROOT / name
    if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("reference is missing or unsafe")
    return p


def trace_path(name):
    """An audit trace belongs in the caller's workspace, never under Skill files."""
    if not isinstance(name, str) or not name:
        raise ValueError("missing trace path")
    rel = name[2:] if name.startswith("./") else name
    if rel in ("", ".", "..") or '/' in rel or '\\' in rel or not rel.endswith('.jsonl'):
        raise ValueError("trace must be a flat .jsonl filename in current workspace")
    return Path.cwd() / rel


def lines_and_bytes(name, start=None, end=None):
    p = source_path(name)
    lines = p.read_bytes().splitlines(keepends=True)
    n = len(lines)
    if start is None:
        start = 1
    if end is None:
        end = n
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > n:
        raise ValueError("invalid reference line range")
    return lines, start, end, b''.join(lines[start - 1:end])


def json_result(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def do_plan(args):
    chosen = TASKS[args.task]
    refs = []
    for name in chosen:
        _, s, e, content = lines_and_bytes(name)
        refs.append({"file": name, "lines": e - s + 1, "bytes": len(content),
                     "source_sha256": hashlib.sha256(content).hexdigest()})
    json_result({"type": "planned_reference_set", "task": args.task,
                 "is_observed": False, "files": refs,
                 "total_bytes": sum(x['bytes'] for x in refs),
                 "warning": "Planning is not runtime telemetry or a tokenizer measurement."})


def append_trace(name, event):
    path = trace_path(name)
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('trace is not a regular file')
        os.write(fd, (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
    finally:
        os.close(fd)


def do_init(args):
    path = trace_path(args.trace)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, b'{"type":"trace_start","version":1}\n')
    finally:
        os.close(fd)
    json_result({"trace_initialized": path.name, "note": "Register this temporary file for task cleanup; do not include its contents in user-facing output."})


def do_read(args):
    lines, start, end, content = lines_and_bytes(args.file, args.start_line, args.end_line)
    # A failed trace write must not claim a read was audited. Write first; then print.
    if args.trace:
        append_trace(args.trace, {"type": "reference_read", "path": args.file,
                                  "start_line": start, "end_line": end,
                                  "source_sha256": hashlib.sha256(b''.join(lines)).hexdigest(),
                                  "range_sha256": hashlib.sha256(content).hexdigest(), "read_bytes": len(content)})
    # stdout is the same range as recorded in trace; no banner/JSON mixed in.
    sys.stdout.buffer.write(content)


def do_audit(args):
    path = trace_path(args.trace)
    if path.is_symlink() or not path.is_file():
        raise ValueError('trace is missing or unsafe')
    # Prevent unbounded reading of untrusted traces.
    if path.stat().st_size > 2_000_000:
        raise ValueError('trace is too large')
    events = []
    with path.open(encoding='utf-8') as fp:
        for idx, line in enumerate(fp, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeError) as e:
                raise ValueError(f'invalid JSON at trace line {idx}') from e
            if not isinstance(item, dict):
                raise ValueError(f'event {idx} is not an object')
            events.append(item)
    totals = {'events': len(events), 'reference_reads': 0, 'read_bytes': 0,
              'unique_bytes': 0, 'repeated_bytes': 0, 'measured_reference_tokens': 0}
    by_file = {}
    usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'events': 0}
    measured_read_events = 0
    verified_read_events = 0
    legacy_read_events = 0
    for event in events:
        kind = event.get('type')
        if kind == 'trace_start':
            continue
        if kind == 'model_usage':
            for key in ('input_tokens','output_tokens','cached_tokens'):
                val = event.get(key, 0)
                if type(val) is not int or val < 0:
                    raise ValueError(f'usage field {key} must be nonnegative integer')
                usage[key] += val
            usage['events'] += 1
            continue
        if kind != 'reference_read':
            raise ValueError('unknown event type')
        name = event.get('path')
        start = event.get('start_line')
        end = event.get('end_line')
        if type(start) is not int or type(end) is not int:
            raise ValueError('reference_read must contain integer start_line and end_line')
        lines, start, end, content = lines_and_bytes(name, start, end)
        fingerprint_keys = ('source_sha256', 'range_sha256', 'read_bytes')
        present = [key in event for key in fingerprint_keys]
        if any(present) and not all(present):
            raise ValueError('incomplete reference fingerprint record')
        if all(present):
            if (event['source_sha256'] != hashlib.sha256(b''.join(lines)).hexdigest()
                    or event['range_sha256'] != hashlib.sha256(content).hexdigest()
                    or type(event['read_bytes']) is not int or event['read_bytes'] != len(content)):
                raise ValueError('reference changed since recorded read or fingerprint mismatch; cannot verify byte totals')
            verified_read_events += 1
        else:
            # Preserve old host event support, but never call reconstructed bytes observed.
            legacy_read_events += 1
        entry = by_file.setdefault(name, {'file': name, 'reads': 0, 'read_bytes': 0,
                                          'unique_bytes': 0, 'repeated_bytes': 0,
                                          'ranges': [], '_seen': set()})
        entry['reads'] += 1
        entry['ranges'].append([start, end])
        entry['read_bytes'] += len(content)
        for line_index in range(start, end + 1):
            b = len(lines[line_index - 1])
            if line_index in entry['_seen']:
                entry['repeated_bytes'] += b
            else:
                entry['_seen'].add(line_index)
                entry['unique_bytes'] += b
        val = event.get('measured_tokens')
        if val is not None:
            if type(val) is not int or val < 0:
                raise ValueError('measured_tokens must be nonnegative integer')
            totals['measured_reference_tokens'] += val
            measured_read_events += 1
        totals['reference_reads'] += 1
        totals['read_bytes'] += len(content)
    for entry in by_file.values():
        for key in ('unique_bytes', 'repeated_bytes'):
            totals[key] += entry[key]
        del entry['_seen']
    planned = TASKS.get(args.task) if args.task else None
    unexpected = sorted(set(by_file) - set(planned)) if planned is not None else []
    missing = sorted(set(planned) - set(by_file)) if planned is not None else []
    json_result({'type': 'observed_reference_trace', 'trace': path.name,
                 'fingerprint_verified_reads': verified_read_events, 'legacy_unverified_reads': legacy_read_events,
                 'byte_measurement': 'verified_source_snapshots' if not legacy_read_events else 'contains_legacy_current_file_estimates',
                 'task': args.task, 'totals': totals,
                 'reference_token_measurement': 'partial_or_full' if measured_read_events else 'not_available',
                 'measured_read_events': measured_read_events,
                 'provider_model_usage': usage if usage['events'] else None,
                 'files': sorted(by_file.values(), key=lambda x: x['file']),
                 'planned_but_not_observed': missing, 'not_in_suggested_plan': unexpected,
                 'caveats': ['Only instrumented reads or events supplied by the host appear here.',
                             'Reference read bytes are not actual model tokens; host usage is task-wide, not per-reference.',
                             'Re-reading cached model context may not incur identical billing.']})


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='show suggested reference set, without reading into a model')
    p.add_argument('--task', choices=sorted(TASKS), required=True)
    p.set_defaults(action=do_plan)
    p = sub.add_parser('init', help='create an exclusive, workspace-local JSONL trace')
    p.add_argument('--trace', required=True)
    p.set_defaults(action=do_init)
    p = sub.add_parser('read', help='read only a requested reference/range; opt-in trace')
    p.add_argument('--file', required=True)
    p.add_argument('--start-line', type=int)
    p.add_argument('--end-line', type=int)
    p.add_argument('--trace')
    p.set_defaults(action=do_read)
    p = sub.add_parser('audit', help='summarize a trace; externally sourced model_usage supported')
    p.add_argument('--trace', required=True)
    p.add_argument('--task', choices=sorted(TASKS))
    p.set_defaults(action=do_audit)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.action(args)
    except (ValueError, OSError, UnicodeError) as e:
        print('reference-loading-error: ' + str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
