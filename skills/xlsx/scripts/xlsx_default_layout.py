"""Conservative readability defaults for newly created workbooks."""
import math
import unicodedata
from xml.etree import ElementTree as ET

from stdlib_xlsx import NS, address, encoded_xml, xml
from xlsx_appearance import HEADER_COLOR, ROW_COLORS, StyleRegistry


def text_width(text):
    """Approximate character width without relying on installed fonts."""
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1 for char in text)


def apply_default_layout(book, spec):
    """Style populated cells only; never change explicitly supplied cell styles."""
    book.flush()
    book.documents.clear()
    styles = xml(book.parts['xl/styles.xml'])
    registry = StyleRegistry(styles)
    for sheet in spec['sheets']:
        name = sheet['name']
        cells = book.cells[name]
        explicit = {ref.upper() for ref, entry in sheet['cells'].items() if 'style' in entry}
        populated = [(ref, item) for ref, item in cells.items() if item['value'] is not None or item['formula'] is not None]
        first = [item for ref, item in populated if address(ref)[0] == 1]
        header = len(first) >= 2 and all(isinstance(item['value'], str) and item['value'] and item['formula'] is None for item in first)
        widths = {}
        for ref, item in populated:
            if item['formula'] is not None or item['value'] is None:
                continue
            col = address(ref)[1]
            lines = str(item['value']).split('\n')
            widths[col] = max(widths.get(col, 0), max(text_width(line) for line in lines))
        widths = {col: min(40, max(10, width + 2)) for col, width in widths.items()}
        tree = xml(book.parts[book.sheets[name]])
        data = tree.find(NS + 'sheetData')
        if widths:
            cols = ET.Element(NS + 'cols')
            for col, width in sorted(widths.items()):
                ET.SubElement(cols, NS + 'col', {'min': str(col), 'max': str(col), 'width': str(width), 'customWidth': '1'})
            tree.insert(list(tree).index(data), cols)
        for row in data:
            height_lines = 1
            for cell in row:
                ref = cell.get('r')
                item = cells[ref]
                if ref in explicit or (item['value'] is None and item['formula'] is None):
                    continue
                row_number, col = address(ref)
                is_header = header and row_number == 1
                value = item['value']
                lines = str(value).split('\n') if isinstance(value, str) and item['formula'] is None else []
                wrap = bool(lines and (len(lines) > 1 or any(text_width(line) > widths.get(col, 10) - 2 for line in lines)))
                config = {'wrap_headers': wrap, 'wrap_text': wrap, 'header_alignment': 'center'}
                cell.set('s', str(registry.style(HEADER_COLOR if is_header else ROW_COLORS[0], is_header, config)))
                if wrap:
                    capacity = max(1, widths.get(col, 10) - 2)
                    height_lines = max(height_lines, sum(max(1, math.ceil(text_width(line) / capacity)) for line in lines))
            if height_lines > 1:
                row.set('ht', str(min(120, height_lines * 15 + 4)))
                row.set('customHeight', '1')
        book.parts[book.sheets[name]] = encoded_xml(tree)
    book.parts['xl/styles.xml'] = encoded_xml(styles)
    book._index()
