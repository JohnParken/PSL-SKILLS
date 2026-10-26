"""Bounded MS-DOC foot/endnote reference metadata; no formatting execution."""
import struct
MAX_REFERENCES = 100000

def note_references(word, table, story_type, story_start, story_count, main_count, read_unit, require):
    """Return references or None when no supported tables exist (legacy subset)."""
    pair = {'footnotes': 2, 'endnotes': 46}.get(story_type)
    if pair is None:
        return None
    def load(index):
        entry = 154 + index * 8
        offset, length = word.number(entry), word.number(entry + 4)
        return table.region(offset, length) if length else None
    refs, texts = load(pair), load(pair + 1)
    if refs is None and texts is None:
        return None
    location = {'stream': table.stream, 'offset': texts.base if texts else refs.base}
    require(refs is not None and texts is not None, location, 'Incomplete note Ref/Txt table pair')
    require(story_count > 0, location, 'Note tables without story text')
    spans = [(refs.base, refs.base + len(refs.data)), (texts.base, texts.base + len(texts.data))]
    clx_start, clx_size = word.number(418), word.number(422)
    spans.append((clx_start, clx_start + clx_size))
    for index, (start, end) in enumerate(spans):
        for other_start, other_end in spans[index+1:]:
            require(end <= other_start or other_end <= start, location, 'Overlapping note/CLX table ranges')
    require(len(texts.data) >= 12 and len(texts.data) % 4 == 0, texts.location(), 'Malformed note Txt PLC length')
    count = len(texts.data) // 4 - 2
    require(count <= MAX_REFERENCES, texts.location(), 'Note reference budget exceeded', 'resource_limit')
    require(len(refs.data) == 4 + count * 6, refs.location(), 'Note Ref/Txt count mismatch')
    cps = [texts.number(i * 4) for i in range(count + 1)]
    require(cps[0] == 0 and cps[-1] == story_count - 1, texts.location(), 'Note text coverage/terminal CP mismatch')
    require(all(a < b for a, b in zip(cps, cps[1:])), texts.location(), 'Note text CPs must strictly increase')
    # The final PLC CP is undefined and ignored, including its numerical order.
    anchors = [refs.number(i * 4) for i in range(count)]
    require(all(cp < main_count for cp in anchors) and all(a < b for a,b in zip(anchors, anchors[1:])), refs.location(), 'Invalid or duplicate note anchor CP')
    output = []
    for i, anchor in enumerate(anchors):
        flag = refs.number(4 * (count + 1) + i * 2, 2)
        require(read_unit(story_start + cps[i+1] - 1) == 13, texts.location(i * 4), 'Note text must end with paragraph mark')
        if flag:
            require(read_unit(anchor) == 2, refs.location(i * 4), 'Auto note anchor requires U+0002')
        output.append({'index': i, 'anchor': {'stream': 'WordDocument', 'cp': anchor},
                       'text_range': {'stream': 'WordDocument', 'cp_start': story_start + cps[i], 'cp_end': story_start + cps[i+1]},
                       'numbering': 'automatic' if flag else 'custom', 'numbering_raw': flag,
                       'character_properties_verified': False,
                       'source': {'stream': table.stream, 'ref_offset': refs.base + i * 4, 'text_offset': texts.base + i * 4}})
    return output
