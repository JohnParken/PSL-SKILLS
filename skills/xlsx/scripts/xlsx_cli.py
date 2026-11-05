"""Stable version-one capability CLI, backed by the existing XLSX algorithms."""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_cli_protocol import PROTOCOL, MAX_FRAME, dumps, loads
import stdlib_xlsx as core
import xlsx_analysis as analysis
import xlsx_headers as headers
import xlsx_match as matching
import xlsx_lookup as lookup
import formula_engine as formula
from xlsx_wps_result import load_result_bytes
from xlsx_delivery import finish_delivery, validate_input_path, validate_output_path

OPERATIONS = ('open', 'create', 'extract', 'release', 'region', 'target_headers', 'resolve_column',
              'address', 'column_name', 'typed_value', 'validate_query', 'matches', 'index_rows',
              'summarize', 'lookup', 'evaluate', 'evaluate_many', 'select_rows', 'patch', 'calculate', 'verify', 'publish', 'delivery')


def capabilities():
    return {'protocol': PROTOCOL, 'interface_version': 1, 'skill': 'xlsx', 'operations': list(OPERATIONS),
            'transport': 'bounded_json_lines', 'max_frame_bytes': MAX_FRAME,
            'handles': 'process_local; opaque; release or close stdin', 'max_handles': 64}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(original, candidate, writes):
    """Keep the existing workflow preservation checks inside the format owner."""
    require(original.sheets == candidate.sheets, 'Workbook sheet structure changed')
    require(not set(candidate.parts) - set(original.parts), 'Unexpected workbook part added')
    for (sheet, ref), expected in writes.items():
        actual = candidate.cells[sheet].get(ref, {})
        require(actual.get('formula') is None and (actual.get('value') is None if expected is None
                else isinstance(actual.get('value'), Decimal) and actual['value'] == expected),
                'Target value differs from calculated write: ' + sheet + '!' + ref)
    for sheet, cells in original.cells.items():
        require(all((sheet, ref) in writes for ref in set(candidate.cells[sheet]) - set(cells)), 'Non-target cells added to ' + sheet)
        for ref, cell in cells.items():
            updated = candidate.cells[sheet].get(ref, {})
            if (sheet, ref) in writes:
                require(cell.get('style') == updated.get('style'), 'Target style changed')
            elif cell.get('formula') is not None:
                require(all(cell.get(field) == updated.get(field) for field in ('formula', 'style', 'formula_attributes')),
                        'Non-target formula structure changed')
            else:
                require(cell == updated, 'Non-target cell changed: ' + sheet + '!' + ref)
    for name, data in original.parts.items():
        if name not in candidate.changed:
            require(candidate.parts.get(name) == data, 'Untouched ZIP member changed')


class Session:
    def __init__(self):
        self.books = {}
        self.next_handle = 0
        self.snapshot_bytes = 0

    def book(self, handle):
        require(type(handle) is int and handle in self.books, 'Unknown or expired workbook handle')
        return self.books[handle]

    def view(self, book):
        merges = {}
        for name in book.cells:
            if hasattr(book, 'wps_merges'):
                merges[name] = [node['ref'] for node in book.wps_merges[name]]
            else:
                tree = core.xml(book.parts[book.sheets[name]])
                merges[name] = [node.get('ref') for node in tree.findall(core.NS + 'mergeCells/' + core.NS + 'mergeCell')]
        # Public cell snapshot, not a serialized Workbook or its ZIP/XML state.
        allowed = ('value', 'type', 'formula', 'cache_status')
        return {'cells': {name: {ref: {key: deepcopy(cell[key]) for key in allowed if key in cell}
                                for ref, cell in cells.items()} for name, cells in book.cells.items()},
                'merges': merges, 'source_status': getattr(book, 'wps_source_status', 'ok'),
                'source_issues': deepcopy(getattr(book, 'wps_issues', [])), 'source_coverage': deepcopy(getattr(book, 'wps_source_coverage', {})),
                'input_schema': getattr(book, 'wps_input_schema', None), 'native': isinstance(book, core.Workbook)}

    def add(self, book):
        require(len(self.books) < 64, 'Release handles before opening more than 64 workbooks')
        handle = self.next_handle
        self.next_handle += 1
        self.books[handle] = book
        return {'handle': handle, **self.view(book)}

    def execute(self, operation, p):
        require(operation in OPERATIONS, 'Unknown capability operation')
        if operation == 'open':
            raw = p['raw']
            require(isinstance(raw, bytes) and len(raw) <= 64 * 1024 * 1024, 'Input exceeds 64 MiB')
            require(self.snapshot_bytes + len(raw) <= 256 * 1024 * 1024, 'Session input budget exceeded; start a new session')
            self.snapshot_bytes += len(raw)
            return self.add(core.Workbook.from_bytes(raw, p['source']))
        if operation == 'extract':
            return self.add(load_result_bytes(p['raw'], core.address))
        if operation in ('create', 'patch'):
            require(len(json.dumps(p['spec'], ensure_ascii=False, allow_nan=False, default=str).encode('utf-8')) <= 2 * 1024 * 1024,
                    'Cell specification exceeds 2 MiB')
        if operation == 'create':
            return self.add(core.Workbook.create(p['spec']))
        if operation == 'release':
            self.book(p['handle'])
            del self.books[p['handle']]
            return True
        if operation == 'region':
            return analysis.region_from_book(self.book(p['handle']), p['sheet'], p['range'], **p.get('options', {}))
        if operation == 'target_headers':
            book = self.book(p['handle'])
            def read_cell(ref):
                cell = book.cells[p['sheet']].get(ref, {})
                require(cell.get('formula') is None and cell.get('type') != 'e', 'Formula/error in target header or key: ' + ref)
                return analysis.typed_value(cell)
            return headers.reconstruct(book, p['sheet'], p['bounds'], p['header_rows'], read_cell)
        if operation == 'resolve_column':
            return headers.resolve_column(p['selector'], p['columns'], p['schema'])
        if operation == 'address':
            return core.address(p['ref'])
        if operation == 'column_name':
            return analysis.column_name(p['number'])
        if operation == 'typed_value':
            return analysis.typed_value(p['cell'])
        if operation == 'validate_query':
            return analysis.validate_query(p['query'], p['columns'], p['schema'])
        if operation == 'matches':
            return analysis.matches(p['row'], p['filters'])
        if operation == 'select_rows':
            return [(number, row) for number, row in p['rows']
                    if number not in p['excluded'] and analysis.matches(row, p['filters'])]
        if operation == 'index_rows':
            return matching.index_rows(p['side'], p['nulls'])
        if operation == 'summarize':
            return analysis.summarize_region(p['table'], p['query'])
        if operation == 'lookup':
            return lookup.lookup_regions(p['left'], p['right'], p['config'])
        if operation in ('evaluate', 'evaluate_many'):
            def evaluate(bindings):
                def resolve(sheet, ref):
                    require(sheet == p['sheet'] and ref in bindings, 'Formula reference outside declared bindings')
                    return bindings[ref]
                return formula.evaluate(p['formula'], p['sheet'], resolve)
            if operation == 'evaluate_many':
                require(isinstance(p['bindings'], list) and len(p['bindings']) <= 10000, 'At most 10000 formula bindings per batch')
                return [evaluate(bindings) for bindings in p['bindings']]
            return evaluate(p['bindings'])
        if operation == 'patch':
            book = self.book(p['handle'])
            book.patch(p['spec'])
            return self.view(book)
        if operation == 'calculate':
            book = self.book(p['handle'])
            return {'calculation': book.calculate(), 'view': self.view(book)}
        if operation == 'verify':
            verify(self.book(p['original']), self.book(p['candidate']), p['writes'])
            return True
        if operation == 'publish':
            book = self.book(p['handle'])
            output = validate_output_path(Path(p['output']), protected_inputs=[])
            book.save(output)
            require(core.Workbook.load(output).parts == book.parts, 'Written package does not match validated package')
            return {'output_written': True, 'verification': 'written_package_passed',
                    'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
        if operation == 'delivery':
            output = validate_input_path(Path(p['output']))
            result = {}
            finish_delivery(result, str(output), p['protected_inputs'])
            return result
        raise ValueError('Capability not implemented')


def serve(single=False):
    session = Session()
    for_count = 0
    while True:
        raw = sys.stdin.buffer.readline(MAX_FRAME + 2)
        if not raw:
            return 0
        for_count += 1
        identity = None
        request = {}
        execution_started = False
        try:
            require(len(raw) <= MAX_FRAME + 1 and raw.endswith(b'\n'), 'Truncated or oversized request frame')
            require(for_count <= 200000, 'Session request limit exceeded')
            request = loads(raw)
            identity = request.get('id')
            require(set(request) == {'protocol', 'id', 'operation', 'parameters'} and request['protocol'] == PROTOCOL,
                    'Unsupported protocol or request fields')
            require(type(identity) is int and identity >= 0, 'Request id must be a nonnegative integer')
            execution_started = True
            result = session.execute(request['operation'], request['parameters'])
            response = {'protocol': PROTOCOL, 'id': identity, 'ok': True, 'result': result}
        except Exception as exc:
            if execution_started and isinstance(request, dict) and request.get('operation') in ('patch', 'calculate', 'publish'):
                parameters = request.get('parameters')
                if isinstance(parameters, dict) and type(parameters.get('handle')) is int:
                    session.books.pop(parameters['handle'], None)
            response = {'protocol': PROTOCOL, 'id': identity, 'ok': False,
                        'error': {'code': type(exc).__name__, 'message': str(exc),
                                  'diagnostics': getattr(exc, 'diagnostics', {}), 'calculation': getattr(exc, 'calculation', None)}}
        try:
            encoded = dumps(response)
        except (ValueError, TypeError, RecursionError) as exc:
            encoded = dumps({'protocol': PROTOCOL, 'id': identity, 'ok': False,
                             'error': {'code': 'OutputLimit', 'message': str(exc)}})
        sys.stdout.buffer.write(encoded + b'\n')
        sys.stdout.buffer.flush()
        if len(raw) > MAX_FRAME + 1 or not raw.endswith(b'\n'):
            return 1
        if single:
            return 0 if response['ok'] else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        sys.argv = [str(Path(__file__).with_name('stdlib_xlsx.py')), *sys.argv[2:]]
        return core.main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('capabilities', 'serve', 'call', 'run'))
    args, remaining = parser.parse_known_args()
    if remaining:
        parser.error('Unexpected arguments')
    if args.command == 'capabilities':
        print(json.dumps(capabilities()), flush=True)
        return 0
    return serve(single=args.command == 'call')


if __name__ == '__main__':
    raise SystemExit(main())
