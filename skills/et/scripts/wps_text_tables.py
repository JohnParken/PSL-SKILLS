"""Bounded text-table import; no markup, formulas or external links execute."""
import csv
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import io
import re

from wps_access import ParseError
from wps_biff8_headers import column_name

MAX_BYTES = 8 * 1024 * 1024
MAX_CELLS = 100000
MAX_ROWS = 100000
MAX_TEXT = 4 * 1024 * 1024
NS = '{urn:schemas-microsoft-com:office:spreadsheet}'


def need(condition, message, status='invalid_or_unsupported'):
    if not condition:
        raise ParseError(status, {'stage': 'text_table'}, message)


class Tables:
    """Shared allocation budget and sparse grid, preserving source text."""

    def __init__(self):
        self.sheets = []
        self.cells = 0
        self.text = 0
        self.issues = []

    def issue(self, code):
        """Bound repeated diagnostics independently of input markup size."""
        if not any(item['code'] == code for item in self.issues):
            self.issues.append({'code': code})

    def sheet(self, name):
        need(len(self.sheets) < 256, 'Text table sheet budget exceeded', 'resource_limit')
        sheet = {'name': name, 'cells': {}, 'merges': [], 'hidden_state': 0,
                 'read_status': 'read', 'hyperlinks': [], 'comments': []}
        self.sheets.append(sheet)
        return sheet

    def add(self, sheet, row, col, value, **metadata):
        need(1 <= row <= 1048576 and 1 <= col <= 16384, 'Text table index outside grid')
        self.cells += 1
        self.text += len(value)
        need(self.cells <= MAX_CELLS and self.text <= MAX_TEXT,
             'Text table cell/text budget exceeded', 'resource_limit')
        key = column_name(col) + str(row)
        need(key not in sheet['cells'], 'Overlapping text table cells')
        sheet['cells'][key] = {'type': 'text', 'value': value, **metadata}
        return sheet['cells'][key]

    def result(self, kind, encoding):
        need(self.cells > 0, 'No table cell data found', 'unsupported')
        coordinates_verified = not any(item['code'] in {
            'html_span_layout_not_reconstructed', 'xml_merge_layout_not_reconstructed',
            'html_unclosed_table', 'nested_html_table_separate_sheet',
            'html_row_group_order_not_reconstructed'} for item in self.issues)
        if not coordinates_verified:
            for sheet in self.sheets:
                sheet['coordinates_verified'] = False
                sheet['coordinate_basis'] = 'source_order_only_not_visual_grid'
                for cell in sheet['cells'].values():
                    cell['coordinate_verified'] = False
        return {'coordinates_verified': coordinates_verified,
                'coordinate_basis': 'source_grid' if coordinates_verified else 'source_order_only_not_visual_grid',
                'status': 'partial' if self.issues else 'ok', 'partial': bool(self.issues),
                'format': kind, 'schema_version': 'text_spreadsheet_readonly_v1',
                'spreadsheet_parsed': True, 'sheets': self.sheets, 'date_system': 'unknown',
                'cell_count': self.cells, 'cell_value_coverage': 'text_table_subset',
                'omissions_affect_cell_values': bool(self.issues), 'issues': self.issues,
                'producer_verified': False, 'formulas_executed': False,
                'external_links_followed': False, 'encoding': encoding,
                'formatting_read': False, 'source_values_preserved': True}


class HtmlTables(HTMLParser):
    """Extract separate nested tables; skip active content and track lost layout."""

    def __init__(self, tables):
        super().__init__(convert_charrefs=True)
        self.tables = tables
        self.stack = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.suppressed += 1
        if self.suppressed:
            return
        if tag == 'table':
            need(len(self.stack) < 16, 'HTML table nesting budget exceeded', 'resource_limit')
            if self.stack:
                self.tables.issue('nested_html_table_separate_sheet')
            self.stack.append({'sheet': self.tables.sheet('Table' + str(len(self.tables.sheets) + 1)),
                               'row': 0, 'col': 0, 'parts': None, 'footer_seen': False, 'in_footer': False})
        elif self.stack:
            state = self.stack[-1]
            if tag == 'tfoot':
                state['footer_seen'] = True
                state['in_footer'] = True
            elif tag == 'thead' and state['row']:
                self.tables.issue('html_row_group_order_not_reconstructed')
            elif tag == 'tr':
                if state['footer_seen'] and not state['in_footer']:
                    self.tables.issue('html_row_group_order_not_reconstructed')
                self.finish_cell()
                state['row'] += 1
                need(state['row'] <= MAX_ROWS, 'HTML row budget exceeded', 'resource_limit')
                state['col'] = 0
            elif tag in ('td', 'th'):
                self.finish_cell()
                state['row'] = max(1, state['row'])
                state['col'] += 1
                state['parts'] = []
                if any(key in ('rowspan', 'colspan') and value != '1' for key, value in attrs):
                    self.tables.issue('html_span_layout_not_reconstructed')
            elif tag == 'br' and state['parts'] is not None:
                state['parts'].append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.suppressed:
            self.suppressed -= 1
            return
        if self.suppressed or not self.stack:
            return
        if tag == 'tfoot':
            self.stack[-1]['in_footer'] = False
        if tag in ('td', 'th', 'tr', 'table'):
            self.finish_cell()
        if tag == 'table':
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and not self.suppressed and self.stack[-1]['parts'] is not None:
            self.stack[-1]['parts'].append(data)

    def finish_cell(self):
        state = self.stack[-1]
        if state['parts'] is not None:
            self.tables.add(state['sheet'], state['row'], state['col'], ''.join(state['parts']))
            state['parts'] = None


def _xml(tables, data):
    from wps_ooxml import fromstring
    try:
        root = fromstring(data)
    except ValueError as exc:
        raise ParseError('invalid_or_unsupported', {'stage': 'text_table_xml'}, str(exc)) from exc
    need(root.tag == NS + 'Workbook', 'Not SpreadsheetML 2003', 'unsupported')
    for worksheet in root.findall(NS + 'Worksheet'):
        sheet = tables.sheet(worksheet.get(NS + 'Name', 'Sheet' + str(len(tables.sheets) + 1)))
        for table in worksheet.findall(NS + 'Table'):
            # SpreadsheetML row/cell Index values are relative to this origin.
            top = _index(table.get(NS + 'TopCell'), 1)
            left = _index(table.get(NS + 'LeftCell'), 1)
            need(left <= 16384, 'SpreadsheetML table origin outside grid')
            row_index = 0
            rows = table.findall(NS + 'Row')
            need(len(rows) <= MAX_ROWS, 'XML row budget exceeded', 'resource_limit')
            for row in rows:
                row_index = _index(row.get(NS + 'Index'), row_index + 1)
                col = 0
                for cell in row.findall(NS + 'Cell'):
                    col = _index(cell.get(NS + 'Index'), col + 1)
                    datum = cell.find(NS + 'Data')
                    raw = ''.join(datum.itertext()) if datum is not None else ''
                    entry = tables.add(sheet, top + row_index - 1, left + col - 1, raw,
                                       source_type=datum.get(NS + 'Type') if datum is not None else None)
                    if cell.get(NS + 'Formula') is not None:
                        entry.update(formula=cell.get(NS + 'Formula'), cache_status='unverified' if datum is not None else 'missing')
                        if datum is None:
                            tables.issue('missing_formula_cache')
                    if cell.get(NS + 'MergeAcross') or cell.get(NS + 'MergeDown'):
                        tables.issue('xml_merge_layout_not_reconstructed')
                        col += _index(cell.get(NS + 'MergeAcross'), 0, allow_zero=True)
                    if datum is not None and datum.get(NS + 'Type') == 'Number':
                        try:
                            value = Decimal(raw)
                            need(value.is_finite() and len(value.as_tuple().digits) <= 1024
                                 and abs(value.adjusted()) <= 1024, 'Numeric value exceeds budget', 'resource_limit')
                            entry.update(type='number', value=value, raw_value=raw)
                        except InvalidOperation:
                            tables.issue('xml_invalid_number_preserved_as_text')


def _index(raw, default, allow_zero=False):
    if raw is None:
        return default
    need(re.fullmatch(r'[0-9]{1,7}', raw) is not None, 'Invalid SpreadsheetML index')
    result = int(raw)
    need((0 if allow_zero else 1) <= result <= 1048576, 'SpreadsheetML index outside budget')
    return result


class HtmlEncoding(HTMLParser):
    """Read explicit early HTML meta declarations, not comments or body text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels = []
        self.stopped = False
        self.raw_tag = None

    def handle_endtag(self, tag):
        if tag == self.raw_tag:
            self.raw_tag = None

    def handle_starttag(self, tag, attrs):
        if self.raw_tag is not None:
            return
        if tag in ('script', 'style', 'title', 'textarea', 'xmp', 'iframe', 'noembed', 'noframes'):
            self.raw_tag = tag
            return
        if tag == 'plaintext':
            self.stopped = True
        if tag in ('body', 'table'):
            self.stopped = True
        if self.stopped or tag != 'meta':
            return
        keys = [key for key, _value in attrs]
        need(len(keys) == len(set(keys)), 'Ambiguous HTML meta attributes', 'unsupported')
        attributes = dict(attrs)
        label = attributes.get('charset')
        if label is not None:
            self.labels.append(label.strip().lower())
        if (attributes.get('http-equiv') or '').strip().lower() == 'content-type':
            content = attributes.get('content') or ''
            # Parse complete MIME parameters, not a substring such as xcharset
            # or a charset-looking fragment inside another quoted parameter.
            mime, separator, parameters = content.partition(';')
            if mime.strip().lower() != 'text/html' or not separator:
                return
            pattern = re.compile(r"\s*([A-Za-z0-9_-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^;\s]+))\s*(?:;|$)")
            position = 0
            while position < len(parameters):
                if not parameters[position:].strip():
                    break
                match = pattern.match(parameters, position)
                need(match is not None, 'Malformed HTML Content-Type parameters', 'unsupported')
                if match[1].lower() == 'charset':
                    value = next(value for value in match.groups()[1:] if value is not None)
                    self.labels.append(value.strip().lower())
                position = match.end()



def decode_table_text(data):
    """No encoding guessing: legacy HTML needs an explicit supported declaration."""
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    # BOM UTF-16 keeps its existing path; byte-level meta sniffing is ASCII-only.
    declaration = None
    if encoding != 'utf-16' and data.lstrip().startswith((b'<', b'\xef\xbb\xbf')):
        probe = HtmlEncoding()
        probe.feed(data[:8192].decode('latin-1'))
        aliases = {'utf-8': 'utf-8-sig', 'utf8': 'utf-8-sig',
                   'gb18030': 'gb18030', 'gbk': 'gbk', 'gb2312': 'gb2312'}
        codecs = {aliases.get(label) for label in probe.labels}
        need(not codecs or (len(codecs) == 1 and None not in codecs),
             'Conflicting or unsupported HTML charset declaration', 'unsupported')
        if codecs:
            declaration = next(iter(codecs))
            need(not data.startswith(b'\xef\xbb\xbf') or declaration == 'utf-8-sig',
                 'HTML charset conflicts with UTF-8 BOM', 'unsupported')
            encoding = declaration
    try:
        return data.decode(encoding, errors='strict'), encoding, declaration
    except UnicodeError as exc:
        raise ParseError('unsupported', {'stage': 'text_table'},
                         'Text does not match a supported verified encoding') from exc


def read_text_table(data):
    """Return a table only with positive content evidence, never by extension."""
    need(len(data) <= MAX_BYTES, 'Text table byte budget exceeded', 'resource_limit')
    text, encoding, declaration = decode_table_text(data)
    need('\x00' not in text, 'Binary data is not a text table', 'unsupported')
    tables = Tables()
    stripped = text.lstrip()
    if stripped.startswith('<') and 'urn:schemas-microsoft-com:office:spreadsheet' in text[:8192]:
        _xml(tables, data)
        kind = 'xml_spreadsheet_2003'
    elif re.search(r'<table(?:\s|>)', text[:8192], flags=re.I):
        need(not re.search(r'<!ENTITY', text, flags=re.I), 'HTML entities declarations are unsupported')
        parser = HtmlTables(tables)
        parser.feed(text)
        parser.close()
        if parser.stack:
            while parser.stack:
                parser.finish_cell()
                parser.stack.pop()
            tables.issue('html_unclosed_table')
        kind = 'html_table_disguised'
    else:
        need(not stripped.startswith('<'), 'Unrecognized markup is not delimited data', 'unsupported')
        candidates = []
        for delimiter in ('\t', ','):
            try:
                rows = []
                count = 0
                for row in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True):
                    need(len(rows) < MAX_ROWS, 'Delimited table row budget exceeded', 'resource_limit')
                    count += len(row)
                    need(count <= MAX_CELLS, 'Delimited table cell budget exceeded', 'resource_limit')
                    rows.append(row)
            except csv.Error as exc:
                if 'field larger' in str(exc):
                    raise ParseError('resource_limit', {'stage': 'text_table'}, 'Delimited field length budget exceeded') from exc
                continue
            if len(rows) >= 2 and 2 <= len(rows[0]) <= 256 and all(len(row) == len(rows[0]) for row in rows):
                candidates.append((delimiter, rows))
        need(len(candidates) == 1, 'Delimited table evidence is absent or ambiguous', 'unsupported')
        delimiter, rows = candidates[0]
        sheet = tables.sheet('Table1')
        for row_number, row in enumerate(rows, 1):
            for col, value in enumerate(row, 1):
                tables.add(sheet, row_number, col, value)
        kind = 'tsv_disguised' if delimiter == '\t' else 'csv_disguised'
    if encoding in ('gb18030', 'gbk', 'gb2312'):
        need(kind == 'html_table_disguised', 'Legacy charset supported only for declared HTML tables', 'unsupported')
        tables.issue('html_declared_legacy_encoding')
    result = tables.result(kind, encoding)
    if declaration is not None:
        result['encoding_source'] = 'html_meta_declaration'
        result['encoding_declaration_verified'] = False
    return result
