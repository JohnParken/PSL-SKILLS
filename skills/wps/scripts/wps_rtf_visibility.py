"""Bounded direct-format RTF visibility and text revision views.

This is not a renderer or privacy filter. Unknown cascade, field, historical
property, and structural-marker semantics fail closed when filtering is requested.
"""
from wps_access import ParseError

MAX_VISIBILITY_RUNS = 100000
MAX_STYLE_IDS = 4096
STYLE_CONTROLS = {'s', 'cs', 'ds', 'ts'}
REVISION_METADATA = {'revauth', 'revauthdel', 'revdttm', 'revdttmdel', 'insrsid', 'delrsid', 'revisions', 'revprop', 'revbar', 'revtbl'}
UNRESOLVED_PROPERTIES = {'oldcprops', 'oldpprops', 'oldtprops', 'oldsprops', 'defchp', 'defpap', 'htmlrtf', 'fromhtml', 'htmltag'}
STRUCTURAL_CONTROLS = {'par', 'cell', 'row', 'nestcell', 'nestrow', 'sect', 'page', 'trowd', 'intbl'}
BROKEN_SCOPE_ISSUES = {'rtf_unclosed_groups', 'rtf_unmatched_group_end', 'rtf_truncated_control',
                       'rtf_invalid_control_operand', 'rtf_unicode_incomplete', 'rtf_unicode_fallback_missing', 'rtf_truncated_binary', 'rtf_truncated_hex'}
SAFE_CONTROLS = set("""
rtf ansi ansicpg mac pc pca deff deflang deflangfe deftab uc u pard plain
viewkind viewscale fet cufi dbch hich loch afloch afdbch afcs
fonttbl colortbl stylesheet info pict header footer headerl headerr headerf
footerl footerr footerf footnote annotation object objdata fldinst field fldrslt bin
generator themedata xmlnstbl rsidtbl f fs fcharset cpg fnil froman fswiss fmodern
fscript fdecor ftech fbidi fprq panose fname falt ftruetype red green blue
b i ul ulnone ulw uld uldb uldash uldashd uldashdd ulth ulwave strike striked
caps scaps outl shad emboss imprint nosupersub sub super up dn cf cb chcbpat
highlight lang langfe langnp langfenp kerning expnd expndtw anim rtlch ltrch
rtlpar ltrpar af afs ab ai aul adeff ql qr qc qj sl slmult fi li ri sb sa
outlinelevel keepn keep pagebb widctlpar nowidctlpar tx tb tqr tqc tqdec
paperw paperh margl margr margt margb tab line emdash endash emspace enspace
qmspace bullet lquote rquote ldblquote rdblquote clmgf clmrg clvmgf clvmrg
trleft cellx trhdr trkeepfollow trkeep trgaph trrh trql trqc trqr
clbrdrt clbrdrb clbrdrl clbrdrr brdrs brdrdb brdrdot brdrdash brdrnil brdrw
brdrcf clvertalt clvertalc clvertalb clcbpat clcfpat clshdng itap
snext sbasedon slink sautoupd ssemihidden sunhideused sqformat spriority slocked
spersonal scompose sreply sadditive shidden squickformat sstyrsid
""".split()) | STYLE_CONTROLS | REVISION_METADATA | STRUCTURAL_CONTROLS | {'v', 'webhidden', 'deleted', 'revised'}



class RtfVisibility:
    """Apply only directly evidenced text flags; never infer a style cascade."""

    def __init__(self, hidden_text, revisions, issue):
        if hidden_text not in ('include', 'exclude', 'annotate') or revisions not in (None, 'accepted', 'original', 'all'):
            raise ParseError('unsupported', {'stage': 'options'}, 'Unknown RTF visibility or revision policy')
        self.hidden_text = hidden_text
        self.revisions = revisions
        self.issue = issue
        self.active = hidden_text != 'include' or revisions is not None
        self.filter_hidden = hidden_text == 'exclude'
        self.filter_revisions = revisions in ('accepted', 'original')
        self.state = dict(hidden=False, web_hidden=False, deleted=False, inserted=False)
        self.stack = []
        self.styles = set()
        self.references = set()
        self.base_styles = set()
        self.hidden_unknown = False
        self.revisions_unknown = False
        self.records = []
        self.total_units = 0
        self.excluded_units = 0
        self.hidden_excluded_units = 0
        self.revision_excluded_units = 0

    @staticmethod
    def reject(message, offset, status='unsupported'):
        """Stop before returning text under a policy that cannot be honored."""
        raise ParseError(status, {'stage': 'RTF visibility', 'offset': offset}, message)

    def push(self):
        """Save direct character properties at an RTF group boundary."""
        if self.active:
            self.stack.append(dict(self.state))

    def pop(self):
        """Restore the enclosing direct character properties."""
        if self.active and self.stack:
            self.state = self.stack.pop()

    def unknown(self, kind, offset, message):
        """Refuse affected filtering, otherwise retain explicitly unknown evidence."""
        if kind == 'hidden':
            if self.filter_hidden:
                self.reject(message, offset)
            self.hidden_unknown = True
        else:
            if self.filter_revisions:
                self.reject(message, offset)
            self.revisions_unknown = True

    def broken_scope(self, code, offset):
        """Recovered syntax must not silently redefine an explicit filter scope."""
        if code in BROKEN_SCOPE_ISSUES and (self.filter_hidden or self.filter_revisions):
            self.reject('Malformed RTF scope prevents reliable visibility/revision filtering', offset, 'corrupt')

    def add_style(self, collection, kind, value, offset):
        """Keep style-reference evidence bounded without resolving inheritance."""
        if value is None:
            self.unknown('hidden', offset, 'Incomplete RTF style reference')
            self.unknown('revisions', offset, 'Incomplete RTF style reference')
            return
        identity = (kind, value)
        if identity not in collection and len(collection) >= MAX_STYLE_IDS:
            self.reject('RTF visibility style-ID budget exceeded', offset, 'resource_limit')
        collection.add(identity)

    def skipped_control(self, destination, word, value, offset):
        """Inspect style flags without treating skipped destination text as body."""
        if not self.active or destination != 'stylesheet':
            return
        if word in STYLE_CONTROLS:
            self.add_style(self.styles, word, value, offset)
        elif word == 'sbasedon' and value != 32767:
            self.add_style(self.base_styles, 'base', value, offset)
        elif word in ('v', 'webhidden'):
            self.unknown('hidden', offset, 'RTF stylesheet visibility cascade is not reconstructed')
        elif word in ('deleted', 'revised') or word.startswith(('move', 'revmove')):
            self.unknown('revisions', offset, 'RTF stylesheet revision properties are not reconstructed')
        elif word in UNRESOLVED_PROPERTIES or word not in SAFE_CONTROLS:
            self.unknown('hidden', offset, 'Unknown or historical RTF style properties are not reconstructed')
            self.unknown('revisions', offset, 'Unknown or historical RTF style properties are not reconstructed')

    def destination(self, word, offset):
        """Recognize property histories even when their destination is skipped."""
        if self.active and word in UNRESOLVED_PROPERTIES:
            self.unknown('hidden', offset, 'RTF historical/default properties or alternate view are unsupported')
            self.unknown('revisions', offset, 'RTF historical/default properties or alternate view are unsupported')

    def control(self, word, value, offset):
        """Track direct flags; author/time IDs do not themselves change text."""
        if not self.active:
            return
        self.destination(word, offset)
        keys = {'v': 'hidden', 'webhidden': 'web_hidden', 'deleted': 'deleted', 'revised': 'inserted'}
        if word in keys:
            kind = 'hidden' if word in ('v', 'webhidden') else 'revisions'
            if value not in (None, 0, 1):
                self.unknown(kind, offset, 'RTF visibility/revision toggle operand is not 0 or 1')
            else:
                self.state[keys[word]] = value != 0
        elif word == 'plain':
            self.state['hidden'] = False
            self.state['web_hidden'] = False
            if self.state['deleted'] or self.state['inserted']:
                self.unknown('revisions', offset, 'RTF plain reset inside a revision requires unverified revision-reset semantics')
        elif word in STYLE_CONTROLS:
            self.add_style(self.references, word, value, offset)
        elif word == 'field':
            if self.filter_hidden or self.filter_revisions:
                self.reject('RTF fields cannot be reliably combined with explicit visibility/revision filtering', offset)
            self.hidden_unknown = True
        elif word not in REVISION_METADATA and word.startswith(('move', 'revmove', 'rev', 'ins', 'del', 'pnr')):
            self.unknown('revisions', offset, 'Unrecognized RTF revision semantics: ' + word)
        if word not in SAFE_CONTROLS and word not in UNRESOLVED_PROPERTIES:
            if self.filter_hidden or self.filter_revisions:
                self.reject('Unsupported RTF control prevents verified filtering: ' + word, offset)
            self.hidden_unknown = True
            self.revisions_unknown = True
        if word in STRUCTURAL_CONTROLS:
            hidden = self.state['hidden'] or self.state['web_hidden']
            revised = self.state['deleted'] or self.state['inserted']
            if self.filter_hidden and hidden or self.filter_revisions and revised:
                self.reject('RTF hidden/revision formatting on a structural delimiter is not reconstructed', offset)

    def emit(self, value, start, end, container):
        """Record source spans/counts, without copying text into annotations."""
        if not self.active or not value:
            return True
        hidden = self.state['hidden']
        if self.state['web_hidden'] and not hidden:
            self.unknown('hidden', start, 'RTF webhidden visibility depends on an unspecified document view')
        deleted, inserted = self.state['deleted'], self.state['inserted']
        if deleted and inserted:
            self.unknown('revisions', start, 'Simultaneous deleted and revised RTF text is ambiguous')
        revision = 'conflicting' if deleted and inserted else ('deleted' if deleted else ('inserted' if inserted else 'unchanged'))
        hidden_excluded = self.filter_hidden and hidden
        revision_excluded = self.revisions == 'accepted' and deleted or self.revisions == 'original' and inserted
        excluded = hidden_excluded or revision_excluded
        units = len(value.encode('utf-16le', errors='surrogatepass')) // 2
        self.total_units += units
        self.excluded_units += units if excluded else 0
        self.hidden_excluded_units += units if hidden_excluded else 0
        self.revision_excluded_units += units if revision_excluded else 0
        if self.hidden_text == 'annotate':
            state = 'hidden' if hidden else ('unknown' if self.state['web_hidden'] else 'visible')
            action = 'revision_excluded' if revision_excluded else 'retained'
            record = dict(state=state, revision=revision, action=action, container=container, utf16_units=units,
                          source={'part': 'RTF main content', 'byte_start': start, 'byte_end': end})
            previous = self.records[-1] if self.records else {}
            if previous and all(previous[key] == record[key] for key in ('state', 'revision', 'action', 'container')):
                previous['utf16_units'] += units
                previous['source']['byte_end'] = end
            else:
                if len(self.records) >= MAX_VISIBILITY_RUNS:
                    self.reject('RTF visibility annotation budget exceeded', start, 'resource_limit')
                self.records.append(record)
        return not excluded

    def finish(self, offset):
        """Validate unresolved references before returning any filtered result."""
        if not self.active:
            return None
        missing_bases = {value for _, value in self.base_styles} - {value for _, value in self.styles}
        if self.references - self.styles or missing_bases:
            self.unknown('hidden', offset, 'RTF text references a style without a parsed definition')
            self.unknown('revisions', offset, 'RTF text references a style without a parsed definition')
        if self.hidden_unknown:
            self.issue({'code': 'rtf_visibility_unknown', 'message': 'Effective visibility is unresolved; annotations are evidence, not a privacy filter'})
            for record in self.records:
                record['state'] = 'unknown'
        if self.revisions_unknown:
            self.issue({'code': 'rtf_revision_view_unknown', 'message': 'Some RTF revision semantics are unresolved; stored text is retained'})
            for record in self.records:
                record['revision'] = 'unknown'
        return dict(schema_version='rtf_direct_visibility_v1', policy=self.hidden_text, revisions=self.revisions,
                    scope='main_paragraphs_and_table_cells', evidence='direct_character_flags_with_group_scope',
                    text_utf16_units=self.total_units, excluded_utf16_units=self.excluded_units,
                    hidden_excluded_utf16_units=self.hidden_excluded_units,
                    revision_excluded_utf16_units=self.revision_excluded_units,
                    hidden_state='unknown' if self.hidden_unknown else 'direct_subset_resolved',
                    revision_state='unknown' if self.revisions_unknown else 'direct_subset_resolved',
                    runs=self.records if self.hidden_text == 'annotate' else [])
