"""Bounded MS-DOC annotation authors and CP references, never field execution."""
MAX_COMMENTS = 100000
MAX_AUTHORS = 32767
MAX_METADATA_BYTES = 4 * 1024 * 1024
COMMENT_PAIRS = (4, 5, 36)

def comment_references(word, table, story_start, story_count, main_count, read_unit, require):
    def load(pair):
        at = 154 + pair * 8
        offset, length = word.number(at), word.number(at + 4)
        require(length <= MAX_METADATA_BYTES, word.location(at + 4), 'Comment metadata budget exceeded', 'resource_limit')
        return table.region(offset, length) if length else None
    refs, texts, names = [load(pair) for pair in COMMENT_PAIRS]
    if refs is None and texts is None and names is None:
        return None
    require(story_count > 0 and refs is not None and texts is not None,
            word.location(154 + 4 * 8), 'Incomplete comment Ref/Txt/author tables')
    require(len(texts.data) >= 8 and len(texts.data) % 4 == 0, texts.location(), 'Malformed comment Txt PLC')
    count = len(texts.data) // 4 - 2
    require(count <= MAX_COMMENTS, texts.location(), 'Comment count budget exceeded', 'resource_limit')
    require(len(refs.data) == 4 + count * 34, refs.location(), 'Comment Ref/Txt count mismatch')
    if count == 0:
        require(story_count == 1 and texts.number(0) == 0, texts.location(), 'Empty comment table coverage mismatch')
        require(names is None, word.location(154 + 36 * 8), 'Author names without comments')
        return []
    require(names is not None, word.location(154 + 36 * 8), 'Missing comment authors')
    authors = []
    seen = set()
    cursor = 0
    while cursor < len(names.data):
        require(len(authors) < MAX_AUTHORS, names.location(cursor), 'Author count budget exceeded', 'resource_limit')
        length = names.number(cursor, 2)
        require(length < 56, names.location(cursor), 'Comment author exceeds 55 UTF-16 units')
        raw = bytes(names.view(cursor + 2, length * 2))
        try:
            name = raw.decode('utf-16le')
        except UnicodeDecodeError:
            require(False, names.location(cursor + 2), 'Invalid comment author UTF-16')
        require(name not in seen, names.location(cursor), 'Duplicate comment author name')
        seen.add(name)
        authors.append({'name': name, 'source': names.location(cursor)})
        cursor += 2 + length * 2
    cps = [texts.number(i * 4) for i in range(count + 1)]
    require(cps[0] == 0 and cps[-1] == story_count - 1 and all(a < b for a,b in zip(cps,cps[1:])),
            texts.location(), 'Invalid comment text CP coverage')
    anchors = [refs.number(i * 4) for i in range(count)]
    require(all(cp < main_count for cp in anchors) and all(a < b for a,b in zip(anchors,anchors[1:])),
            refs.location(), 'Invalid or duplicate comment anchor CP')
    output = []
    for i, anchor in enumerate(anchors):
        at = 4 * (count + 1) + i * 30
        initials_count = refs.number(at, 2)
        require(initials_count <= 9, refs.location(at), 'Comment initials length exceeds 9')
        try:
            initials = bytes(refs.view(at + 2, initials_count * 2)).decode('utf-16le')
        except UnicodeDecodeError:
            require(False, refs.location(at + 2), 'Invalid comment initials UTF-16')
        author_index = refs.number(at + 20, 2)
        require(author_index < len(authors), refs.location(at + 20), 'Comment author index out of range')
        # Reserved ATRD fields are MUST-ignored. Do not infer creation dates.
        bookmark = refs.number(at + 26)
        require(read_unit(anchor) == 5 and read_unit(story_start + cps[i]) == 5,
                refs.location(i * 4), 'Comment reference/text requires U+0005')
        require(read_unit(story_start + cps[i+1] - 1) == 13,
                texts.location(i * 4), 'Comment text must end with paragraph mark')
        output.append({'index': i, 'anchor': {'stream':'WordDocument','cp':anchor},
            'text_range': {'stream':'WordDocument','cp_start':story_start+cps[i], 'cp_end':story_start+cps[i+1]},
            'author_index':author_index, 'author':authors[author_index], 'initials':initials,
            'bookmark_id_raw':bookmark,
            'annotation_range': {'cp_start':anchor,'cp_end':anchor} if bookmark == 0xFFFFFFFF else None,
            'range_status':'zero_length' if bookmark == 0xFFFFFFFF else 'bookmark_not_decoded',
            'character_properties_verified':False,
            'source':{'stream':table.stream,'ref_offset':refs.base+i*4,'atrd_offset':refs.base+at,'text_offset':texts.base+i*4}})
    return output
