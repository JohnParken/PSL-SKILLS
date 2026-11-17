"""Upstream template registration: registration responsibilities."""
from __future__ import annotations

from .common import (
    KIND_CONFIG,
    OrderedDict,
    Path,
    SpecParseError,
    argparse,
    json,
    sys,
)

def _load_index(path: Path) -> "OrderedDict[str, dict]":
    if not path.exists():
        return OrderedDict()
    raw_text = path.read_text(encoding="utf-8").strip() or "{}"
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise SpecParseError(f"template index must be a JSON object: {path}")
    return OrderedDict(sorted(raw.items()))


def _write_index(path: Path, data: "OrderedDict[str, dict]", *, dry_run: bool) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if dry_run:
        print(f"--- {path.name} (dry-run) ---")
        print(payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _enumerate_ids(kind: str, scope: str = "builtin") -> list[str]:
    from .library import (
        get_library_paths,
    )
    base, _index = get_library_paths(kind, scope)
    if not base.exists():
        return []
    return sorted(
        p.name for p in base.iterdir()
        if p.is_dir()
        and (p / "templates" / "design_spec.md").is_file()
    )


def _print_completion_card(
    kind: str,
    template_id: str,
    entry: dict,
    extras: dict,
    *,
    scope: str = "builtin",
) -> None:
    pretty_kind = {
        "layout": "Layout",
        "deck": "Deck",
        "brand": "Brand",
        "style": "Style",
    }[kind]
    dir_name = {
        "layout": "layouts",
        "deck": "decks",
        "brand": "brands",
        "style": "styles",
    }[kind]
    print()
    print(f"## {pretty_kind} Registration Complete")
    print()
    print(f"**{pretty_kind} ID**: {template_id}")
    prefix = "templates/local" if scope == "local" else "templates"
    print(f"**Path**: `{prefix}/{dir_name}/{template_id}/`")
    if kind in ("brand", "deck"):
        primary = entry.get("primary_color") or "—"
        print(f"**Primary Color**: {primary}")
    if kind in ("layout", "deck"):
        canvas = entry.get("canvas_format") or "—"
        pc = entry.get("page_count") or "—"
        print(f"**Canvas**: {canvas}")
        print(f"**Pages**: {pc}")
    print(f"**Summary**: {entry.get('summary') or '—'}")
    print("**Index Registration**: Done")
    print()
    if KIND_CONFIG[kind]["needs_svg_roster"]:
        pages = extras.get("pages") or []
        page_prefix = extras.get("page_prefix") or ""
        preview = extras.get("preview") or ""
        if preview:
            print(f"**Review PPTX**: `{preview}`")
            print()
        if pages:
            print("### Files Included")
            print()
            print("| File | Status |")
            print("|------|--------|")
            for page in pages:
                print(f"| `{page_prefix}{page}.svg` | Done |")
            if preview:
                print(f"| `{preview}` | Verified |")
            print()


def main(argv: list[str] | None = None) -> int:
    from .entries import (
        _extract_entry,
    )
    from .library import (
        get_library_paths,
        is_safe_template_id,
    )
    parser = argparse.ArgumentParser(
        description=(
            "Register / refresh templates (brand / style / layout / deck) "
            "in the index."
        )
    )
    parser.add_argument(
        "template_id", nargs="?",
        help="Template directory id (under templates/local/<kind_dir>/ by default). Omit with --rebuild-all.",
    )
    parser.add_argument(
        "--kind", choices=list(KIND_CONFIG.keys()), default="deck",
        help="Template kind (default: deck).",
    )
    parser.add_argument("--rebuild-all", action="store_true",
                        help="Rebuild every index entry within the chosen kind.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be written without modifying any files.")
    parser.add_argument("--builtin", action="store_true",
                        help="Maintain the distributed library instead of templates/local/.")
    args = parser.parse_args(argv)

    if not args.template_id and not args.rebuild_all:
        parser.error("provide a template_id or use --rebuild-all")

    if args.template_id is not None and not is_safe_template_id(args.template_id):
        parser.error(
            "template_id must start with a Unicode letter, digit, or underscore, "
            "contain only those characters, dots, or hyphens, and not end with a dot"
        )

    scope = "builtin" if args.builtin else "local"
    base, index_path = get_library_paths(args.kind, scope)
    other_scope = "local" if args.builtin else "builtin"
    other_base, other_index_path = get_library_paths(args.kind, other_scope)

    if args.rebuild_all:
        ids = _enumerate_ids(args.kind, scope)
    else:
        ids = [args.template_id]
        spec_dir = base / args.template_id
        if not spec_dir.is_dir():
            print(f"Error: {args.kind} directory not found: {spec_dir}", file=sys.stderr)
            if scope == "local" and (other_base / args.template_id).is_dir():
                print(
                    "For an official template, pass --builtin. For a personal template, "
                    f"place it in skills/ppt-master/templates/local/{base.name}/{args.template_id}/.",
                    file=sys.stderr,
                )
            return 1

    try:
        other_index = _load_index(other_index_path)
        index = OrderedDict() if args.rebuild_all else _load_index(index_path)
    except (OSError, ValueError, SpecParseError) as exc:
        print(f"Error: cannot read template index: {exc}", file=sys.stderr)
        return 1
    collisions = sorted(set(ids) & other_index.keys())
    if collisions:
        guidance = (
            "Choose non-conflicting IDs, or pass --builtin to maintain official templates."
            if scope == "local" else
            "Choose non-conflicting IDs or rename the conflicting local templates first."
        )
        print(
            f"Error: {args.kind} IDs already exist in the {other_scope} library: "
            f"{', '.join(collisions)}. {guidance}",
            file=sys.stderr,
        )
        return 1

    extracted: dict[str, dict] = {}
    for tid in ids:
        try:
            if not is_safe_template_id(tid):
                raise SpecParseError("unsafe template id; rename the workspace before registering")
            extracted[tid] = _extract_entry(args.kind, tid, base / tid)
        except SpecParseError as exc:
            print(f"Error: {tid}: {exc}", file=sys.stderr)
            return 1

    if args.rebuild_all:
        index = OrderedDict((tid, extracted[tid]["entry"]) for tid in sorted(extracted))
    else:
        for tid, payload in extracted.items():
            index[tid] = payload["entry"]
        index = OrderedDict(sorted(index.items()))

    _write_index(index_path, index, dry_run=args.dry_run)

    if not args.dry_run and not args.rebuild_all:
        tid = args.template_id
        _print_completion_card(
            args.kind, tid, extracted[tid]["entry"], extracted[tid]["extras"], scope=scope,
        )
        return 0

    print()
    print(
        f"[OK] {'Dry-run preview' if args.dry_run else 'Updated'}: "
        f"{len(extracted)} {args.kind}(s) processed; index now lists {len(index)} entries."
    )
    return 0

