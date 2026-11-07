#!/usr/bin/env python3
"""Repository-only documentation checks (Python >=3.10, standard library).

Checks current inventory/version tables and ordinary inline/reference Markdown
links, ATX heading anchors and explicit HTML ids. Not a CommonMark parser,
semantic/source-claim validator, Mermaid parser or network checker. Fenced code
and inline code are ignored for links. No recursive directory traversal.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = r'[0-9]{8}\.[0-9]{4}\.[0-9]{4}'


def prose(text):
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append('')
        else:
            lines.append('' if fence else line)
    return '\n'.join(lines)


def anchors(text):
    result, counts = set(), {}
    for line in prose(text).splitlines():
        result.update(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', line))
        match = re.match(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$', line)
        if not match:
            continue
        title = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', match[1])
        title = re.sub(r'<[^>]+>', '', title).lower()
        slug = ''.join(c for c in title if c.isalnum() or c in '_- ')
        slug = slug.replace(' ', '-')
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        result.add(slug + (f'-{number}' if number else ''))
    return result


def links(text):
    text = re.sub(r'(`+).*?\1', '', prose(text))
    # Destinations with balanced single-level parentheses (common filenames).
    dest = r'(?:<([^>]+)>|([^\s()]*(?:\([^()]*\)[^\s()]*)*))'
    for match in re.finditer(r'!?\[[^\]\n]*\]\(\s*' + dest + r'(?:\s+["\'][^\n]*?["\'])?\s*\)', text):
        yield match[1] or match[2]
    for match in re.finditer(r'^ {0,3}\[[^]]+\]:\s*<?([^\s>]+)>?', text, re.M):
        yield match[1]


def check_project_docs(root):
    """Return a deterministic JSON-serializable report; never write or network."""
    root = Path(root).resolve()
    issues, cache = [], {}

    def issue(path, code, detail):
        issues.append({'path': str(path.relative_to(root)), 'code': code, 'detail': detail})

    def read(path):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            issue(path, 'outside-repository', 'Refusing to read outside repository')
            return ''
        if path not in cache:
            try:
                cache[path] = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError) as exc:
                issue(path, 'read-error', str(exc))
                cache[path] = ''
        return cache[path]

    inventory = {}
    for path in sorted((root / 'skills').glob('*/SKILL.md')):
        source = read(path)
        frontmatter = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', source, re.S)
        versions = re.findall(
            r'^version:\s*["\'](' + VERSION + r')["\']\s*$',
            frontmatter[1] if frontmatter else '', re.M,
        )
        match = versions if len(versions) == 1 else None
        if not match:
            issue(path, 'skill-version', 'Missing simple quoted version in frontmatter')
        else:
            inventory[path.parent.name] = match[0]
    if not inventory:
        issue(root / 'skills', 'inventory', 'No skills found')
    for name, version in inventory.items():
        for relative, target in [('README.md', f'skills/{name}/SKILL.md'), ('docs/README.md', f'skills/{name}.md')]:
            path = root / relative
            rows = [line for line in read(path).splitlines() if line.startswith('|') and f']({target})' in line]
            if len(rows) != 1:
                issue(path, 'coverage', f'{name}: expected exactly one table row, found {len(rows)}')
            elif re.findall(VERSION, rows[0]) != [version]:
                issue(path, 'version-drift', f'{name}: expected {version}')
        path = root / 'docs/skills' / f'{name}.md'
        introduction = re.split(r'^ {0,3}##\s+', prose(read(path)), maxsplit=1, flags=re.M)[0]
        baseline = [
            paragraph for paragraph in re.split(r'\n\s*\n', introduction)
            if re.search(r'Skill\s*版本|源码基线|基线版本', paragraph)
        ]
        if len(baseline) != 1 or re.findall(VERSION, baseline[0]) != [version]:
            issue(path, 'version-drift', f'{name}: expected baseline version {version}')
    for path in sorted((root / 'docs/skills').glob('*.md')):
        if path.stem not in inventory:
            issue(path, 'coverage', 'Detail page has no current Skill')
    pages = sorted({root / 'README.md', root / 'AGENTS.md', root / 'docs/README.md', *(root / 'docs').glob('*.md'), *(root / 'docs/skills').glob('*.md')})
    checked = 0
    for path in pages:
        for destination in links(read(path)):
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue
            checked += 1
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            if not target.is_relative_to(root):
                issue(path, 'outside-repository', destination)
            elif not target.exists():
                issue(path, 'missing-link', destination)
            elif parsed.fragment and target.suffix.lower() == '.md' and unquote(parsed.fragment) not in anchors(read(target)):
                issue(path, 'missing-anchor', destination)
    return {
        'ok': not issues,
        'skills': inventory,
        'pages_checked': len(pages),
        'local_links_checked': checked,
        'issues': issues,
        'limitations': (
            'Structural checks only; no semantic claims, Mermaid rendering, '
            'external links or complete CommonMark validation.'
        ),
    }



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    report = check_project_docs(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('PASS' if report['ok'] else 'FAIL')
        for item in report['issues']:
            print(f"{item['path']}: {item['code']}: {item['detail']}")
        print(report['limitations'])
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
