"""Bounded MS-DOC stored section/header ranges; no pagination or SEPX execution."""
from bisect import bisect_left

MAX_SECTIONS = 4096
MAX_SECTION_BYTES = 1024 * 1024
HEADER_KINDS = ('even_header', 'odd_header', 'even_footer', 'odd_footer', 'first_header', 'first_footer')
SEPARATOR_KINDS = ('footnote_separator', 'footnote_continuation_separator', 'footnote_continuation_notice', 'endnote_separator', 'endnote_continuation_separator', 'endnote_continuation_notice')


def header_sections(word, table, story_start, story_count, main_count, read_unit, require, *, fib_end, cb_mac, text_spans):
    """Return None if tables absent, otherwise verified stored slots and sections."""
    def load(pair):
        at = 154 + pair * 8
        offset, size = word.number(at), word.number(at + 4)
        return table.region(offset, size) if size else None
    sed, hdd = load(6), load(11)
    if sed is None and hdd is None:
        return None
    require(hdd is None or story_count > 0, table.location(), 'Header table without header story')
    sections = []
    sepx_spans = set()
    ordered_text = sorted(text_spans)
    require(all(a < b for a,b in ordered_text) and
            all(left[1] <= right[0] for left,right in zip(ordered_text,ordered_text[1:])),
            word.location(), 'Invalid text ownership ranges')
    text_starts = [a for a,_ in ordered_text]
    if sed is not None:
        require(len(sed.data) >= 20 and (len(sed.data)-4) % 16 == 0, sed.location(), 'Malformed PlcfSed length')
        count = (len(sed.data)-4)//16
        require(count <= MAX_SECTIONS, sed.location(), 'Section count budget exceeded', 'resource_limit')
        cps = [sed.number(i*4) for i in range(count+1)]
        require(cps[0] == 0 and cps[-1] >= main_count and cps[-1] <= 0x7fffffff, sed.location(), 'Invalid section coverage')
        require(all(a < b for a,b in zip(cps,cps[1:])) and all(cp < main_count for cp in cps[:-1]), sed.location(), 'Invalid section CP order')
        raw_budget = 0
        for i in range(count):
            if i < count-1:
                require(read_unit(cps[i+1]-1) == 12, sed.location((i+1)*4), 'Missing section boundary U+000C')
            descriptor = 4*(count+1) + i*12
            fc_raw = sed.number(descriptor+2)
            fc = fc_raw if fc_raw < 0x80000000 else fc_raw-0x100000000
            require(fc >= 0, sed.location(descriptor+2), 'Negative SEPX offset outside supported subset', 'unsupported')
            require(fib_end <= fc < cb_mac, word.location(fc), 'SEPX offset outside meaningful stream')
            size = word.number(fc, 2)
            end = fc + 2 + size
            # Only the last text interval beginning before end can intersect a
            # SEPX: text ownership is already sorted and non-overlapping.
            previous = bisect_left(text_starts, end) - 1
            require(end <= cb_mac and (previous < 0 or ordered_text[previous][1] <= fc),
                    word.location(fc), 'SEPX overlaps text or meaningful stream boundary')
            require(size <= 0x7fff, word.location(fc), 'Negative SEPX byte count')
            raw_budget += size
            require(raw_budget <= MAX_SECTION_BYTES, word.location(fc), 'SEPX metadata budget exceeded', 'resource_limit')
            word.view(fc+2, size)
            # Exact shared structures collapse; check unique intervals once.
            sepx_spans.add((fc,end))
            sections.append({'index':i,'main_range':{'cp_start':cps[i], 'cp_end':min(cps[i+1],main_count)}, 'terminal_cp_raw':cps[i+1],
                'source':{'stream':table.stream,'offset':sed.base+descriptor},
                'properties':{'status':'uninterpreted','stream':'WordDocument','offset':fc,'size':size,'raw_hex':bytes(word.view(fc+2,size)).hex()}})
    unique_sepx = sorted(sepx_spans)
    for left,right in zip(unique_sepx,unique_sepx[1:]):
        require(left[1] <= right[0], word.location(right[0]), 'Overlapping SEPX structures')
    if hdd is None:
        return {'sections':sections,'slots':[], 'mapping_status':'header_table_absent', 'page_mapping':'unknown', 'word_ranges': [{'stream':'WordDocument', 'offset':a, 'size':b-a} for a,b in sorted(set(sepx_spans))]}
    require(sed is not None, hdd.location(), 'Header slots require a section table in supported subset', 'unsupported')
    require(len(hdd.data) == (6+6*len(sections)+2)*4, hdd.location(), 'Header slot/section count mismatch')
    cps = [hdd.number(i*4) for i in range(len(hdd.data)//4-1)]
    require(cps[0] == 0 and cps[-1] == story_count-1, hdd.location(), 'Invalid header coverage/terminal CP')
    require(all(0 <= cp < story_count for cp in cps) and all(a<=b for a,b in zip(cps,cps[1:])), hdd.location(), 'Invalid header CP range/order')
    slots=[]
    for i,(a,b) in enumerate(zip(cps,cps[1:])):
        if b>a:
            require(read_unit(story_start+b-1)==13,hdd.location(i*4),'Missing header story guard paragraph')
            if i>=6:
                require(b-a>=2 and read_unit(story_start+b-2)==13,hdd.location(i*4),'Header/footer requires content and guard paragraphs')
        section = None if i<6 else (i-6)//6
        kind = SEPARATOR_KINDS[i] if i<6 else HEADER_KINDS[(i-6)%6]
        slots.append({'index':i,'kind':kind,'section_index':section,'empty':a==b,
            'stored_range':{'stream':'WordDocument','cp_start':story_start+a,'cp_end':story_start+b},
            'content_range':{'stream':'WordDocument','cp_start':story_start+a,'cp_end':story_start+b-int(b>a)},
            'inheritance_candidate_section':section-1 if section is not None and section>0 and a==b else None,
            'source':{'stream':table.stream,'offset':hdd.base+i*4}})
    return {'sections':sections,'slots':slots,'mapping_status':'stored_slots_mapped_properties_uninterpreted','page_mapping':'unknown', 'word_ranges': [{'stream':'WordDocument', 'offset':a, 'size':b-a} for a,b in sorted(set(sepx_spans))]}
