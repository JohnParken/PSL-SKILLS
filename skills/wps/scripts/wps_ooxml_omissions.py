"""Report unhandled textual structures without inventing their reading order."""
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def content_omissions(root, part, wrappers, revisions):
    """Expose formula and ignored block-container gaps, not private document text."""
    issues = []
    math_count = sum(node.tag == "{" + M + "}oMath" for node in root.iter())
    if math_count:
        issues.append({'code': 'math_content_not_decoded', 'stream': part, 'location': part,
                       'offset': None, 'count': math_count,
                       'message': 'Mathematical formulas are present but not decoded; surrounding text is retained'})
    excluded = {'accepted': {'del', 'moveFrom'}, 'original': {'ins', 'moveTo'}, 'all': set()}[revisions]
    containers = {'body', 'tc', 'hdr', 'ftr', 'footnote', 'endnote', 'comment', 'txbxContent'}
    unknown = set()
    def inspect_blocks(parent):
        for child in parent:
            local = child.tag.rsplit('}', 1)[-1]
            if not child.tag.startswith('{' + W + '}') or local in excluded:
                continue
            if local in wrappers:
                inspect_blocks(child)
            elif local not in {'p', 'tbl'}:
                if any(node.tag in ('{' + W + '}t', '{' + W + '}delText') for node in child.iter()):
                    unknown.add(local)
    for parent in root.iter():
        if parent.tag in {'{' + W + '}' + name for name in containers}:
            inspect_blocks(parent)
    if unknown:
        issues.append({'code': 'unknown_block_content_not_decoded', 'stream': part, 'location': part,
                       'offset': None, 'elements': sorted(unknown)[:32], 'distinct_elements': len(unknown),
                       'message': 'Text in unrecognized block containers was not decoded; no reading order inferred'})
    return issues
