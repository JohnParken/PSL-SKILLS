"""Compare planned normalized RTF content with parsed candidate before publishing."""
from wps_rtf_builder import RtfParagraph, RtfTable


def paragraph_text(paragraph):
    return ''.join(run.text for run in paragraph.runs).strip()


def merge_flags(rows, rectangles):
    flags = [[(None, None) for _ in row] for row in rows]
    for top, left, bottom, right in rectangles:
        if not (0 <= top <= bottom < len(rows)):
            raise RuntimeError('Candidate merge row outside content ledger')
        for row in range(top, bottom + 1):
            if not 0 <= left <= right < len(rows[row]):
                raise RuntimeError('Candidate merge column outside content ledger')
            for col in range(left, right + 1):
                flags[row][col] = (('first' if col == left else 'continue') if right > left else None,
                                   ('first' if row == top else 'continue') if bottom > top else None)
    return flags


def validate_content(document, candidate):
    """Content scope excludes styling, related stories, fields and visual pagination."""
    expected = []
    unverified = ['formatting', 'related_content', 'field_freshness', 'visual_pagination']
    for element in document.elements:
        if isinstance(element, RtfParagraph):
            text = paragraph_text(element)
            if text:
                expected.append(('paragraph', text))
            else:
                unverified.append('blank_paragraph_presence')
        elif isinstance(element, RtfTable):
            rows = [['\n'.join(paragraph_text(p) for p in cell.paragraphs).strip() for cell in row.cells]
                    for row in element.rows]
            flags = [[(cell.hmerge, cell.vmerge) for cell in row.cells] for row in element.rows]
            expected.append(('table', rows, flags))
        else:
            unverified.append('nontext_page_breaks')
    actual = []
    try:
        seen = {'paragraph': [], 'table': []}
        for block in candidate['blocks']:
            if block['type'] in seen:
                seen[block['type']].append(block['index'])
            if block['type'] == 'paragraph':
                paragraph = candidate['paragraphs'][block['index']]
                text = paragraph['text'] if isinstance(paragraph, dict) else paragraph
                actual.append(('paragraph', text))
            elif block['type'] == 'table':
                table = candidate['tables'][block['index']]
                rows = [[cell['text'] for cell in row] for row in table['rows']]
                actual.append(('table', rows, merge_flags(rows, table.get('merges', []))))
            else:
                raise RuntimeError('Unknown candidate block in content ledger')
        if (sorted(seen['paragraph']) != list(range(len(candidate['paragraphs'])))
                or sorted(seen['table']) != list(range(len(candidate['tables'])))):
            raise RuntimeError('Candidate block coverage cannot be verified')
        if candidate['text'] != '\n'.join(block[1] for block in actual if block[0] == 'paragraph'):
            raise RuntimeError('Candidate text and paragraph ledger disagree')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError('Candidate content schema cannot be verified') from exc

    def coalesce(blocks):
        result = []
        for block in blocks:
            if result and block[0] == result[-1][0] == 'table':
                unverified.append('adjacent_table_boundaries')
                prior = result.pop()
                result.append(('table', prior[1] + block[1], prior[2] + block[2]))
            else:
                result.append(block)
        return result

    if coalesce(expected) != coalesce(actual):
        raise RuntimeError('Candidate content differs from planned text, block order, cells or merge geometry; publication stopped')
    return {'status': 'verified_within_scope', 'basis': 'normalized_document_tree',
            'verified': ['main_text', 'block_order', 'physical_cell_text', 'supported_merge_flags'],
            'normalization': 'outer paragraph/cell whitespace stripped; adjacent tables compared as consecutive physical rows',
            'unverified': sorted(set(unverified))}
