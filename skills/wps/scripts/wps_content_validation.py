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


def validate_content(document, candidate, *, verify_merges=True):
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
            if not verify_merges:
                flags = None
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
                actual.append(('table', rows, merge_flags(rows, table.get('merges', [])) if verify_merges else None))
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
                flags = prior[2] + block[2] if verify_merges else None
                result.append(('table', prior[1] + block[1], flags))
            else:
                result.append(block)
        return result

    if coalesce(expected) != coalesce(actual):
        raise RuntimeError('Candidate content differs from planned text, block order, cells or merge geometry; publication stopped')
    if not verify_merges:
        unverified.append('merge_geometry')
    return {'status': 'verified_within_scope', 'basis': 'normalized_document_tree',
            'verified': ['main_text', 'block_order', 'physical_cell_text'] + (['supported_merge_flags'] if verify_merges else []),
            'normalization': 'outer paragraph/cell whitespace stripped; adjacent tables compared as consecutive physical rows',
            'unverified': sorted(set(unverified))}


def validate_format(document, candidate):
    """Compare explicit raw page/paragraph controls, never rendered page layout."""
    expected_page = {key: getattr(document, attribute) for key, attribute in (
        ('paperw', 'page_width_twips'), ('paperh', 'page_height_twips'), ('margl', 'margin_left_twips'),
        ('margr', 'margin_right_twips'), ('margt', 'margin_top_twips'), ('margb', 'margin_bottom_twips'))}
    if any(candidate.get('page_properties', {}).get(key) != value for key, value in expected_page.items()):
        raise RuntimeError('Candidate page controls differ from plan; publication stopped')
    paragraphs = [item for item in document.elements if isinstance(item, RtfParagraph) and paragraph_text(item)]
    actual = candidate.get('paragraphs', [])
    if len(paragraphs) != len(actual):
        raise RuntimeError('Candidate paragraph formats cannot be aligned')
    for paragraph, item in zip(paragraphs, actual):
        controls = item.get('format', {})
        expected = {'alignment': {'left': 'ql', 'right': 'qr', 'center': 'qc'}.get(paragraph.align, 'qj'),
                    'sb': int(paragraph.space_before_pt * 20) if paragraph.space_before_pt > 0 else 0,
                    'sa': int(paragraph.space_after_pt * 20) if paragraph.space_after_pt > 0 else 0,
                    'li': int(paragraph.left_indent_pt * 20) if paragraph.left_indent_pt > 0 else 0,
                    'ri': int(paragraph.right_indent_pt * 20) if paragraph.right_indent_pt > 0 else 0}
        base = paragraph.runs[0].size_pt if paragraph.runs else 14.0
        expected['fi'] = int(paragraph.first_line_indent_pt * 20) if paragraph.first_line_indent_pt is not None else int(paragraph.first_line_indent_chars * base * 20)
        if paragraph.line_spacing_exact_pt is not None:
            expected.update(sl=-int(paragraph.line_spacing_exact_pt * 20), slmult=0)
        elif paragraph.line_spacing_mult is not None:
            expected.update(sl=int(paragraph.line_spacing_mult * 240), slmult=1)
        if paragraph.outline_level is not None:
            expected['outlinelevel'] = paragraph.outline_level
        for attribute, key in (('keep_with_next', 'keepn'), ('keep_together', 'keep'), ('page_break_before', 'pagebb')):
            expected[key] = int(bool(getattr(paragraph, attribute)))
        if any(controls.get(key, 0) != value for key, value in expected.items()):
            raise RuntimeError('Candidate paragraph controls differ from plan; publication stopped')
    return {'status': 'verified_raw_controls', 'verified': ['page_dimensions', 'page_margins', 'main_paragraph_alignment_spacing_indent_keep_outline'],
            'unverified': ['font_glyphs', 'table_formatting', 'related_content_formatting', 'actual_page_breaks', 'client_rendering']}
