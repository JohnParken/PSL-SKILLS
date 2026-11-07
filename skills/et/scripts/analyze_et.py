"""Analyze or preview one explicit ET/ETT stored-value region, to JSON stdout."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from et_table_analysis import analyze
from et_table_data import need, MAX_QUERY_BYTES
from et_table_preview import preview
from read_et import read_bytes, check_environment, encode_result, failure
from wps_access import ParseError, read_input

def query_object(pairs):
    result = {}
    for name, value in pairs:
        need(name not in result, 'Duplicate JSON query key: ' + name)
        result[name] = value
    return result


def load_query(path):
    data = read_input(path)
    need(len(data) <= MAX_QUERY_BYTES, 'Query exceeds 64 KiB', 'resource_limit')
    def invalid_constant(_):
        need(False, 'Nonfinite JSON query values are forbidden')
    try:
        result = json.loads(data.decode('utf-8'), parse_float=Decimal,
                            parse_constant=invalid_constant, object_pairs_hook=query_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ParseError):
            raise
        raise ParseError('analysis_error', {'stage': 'query'}, 'Malformed UTF-8 JSON query') from error
    need(isinstance(result, dict), 'Query must be a JSON object')
    need(not set(result) & {'sheet', 'range', 'header_rows'}, 'Set sheet/range/header_rows with CLI options, not the query file')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--sheet')
    parser.add_argument('--range', dest='cell_range')
    parser.add_argument('--header-rows')
    parser.add_argument('--query', help='Bounded JSON processing options; --preview accepts only offset/limit')
    parser.add_argument('--preview', action='store_true', help='Preview the explicit rectangle before choosing header rows; query accepts only offset/limit')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--max-output-bytes', type=int)
    parser.add_argument('--cursor')
    args = parser.parse_args(argv)
    if args.check:
        if any((args.input, args.sheet, args.cell_range, args.header_rows, args.query, args.preview, args.max_output_bytes is not None, args.cursor is not None)):
            parser.error('--check does not accept input or table options')
        return check_environment()
    if sys.version_info < (3, 9):
        parser.error('Python >=3.9 required')
    if not args.input or args.sheet is None or args.cell_range is None:
        parser.error('input, --sheet and --range are required')
    if args.preview and args.header_rows is not None:
        parser.error('--preview inspects undeclared rows; do not combine it with --header-rows')
    try:
        query = load_query(args.query) if args.query is not None else {}
        query.update(sheet=args.sheet, range=args.cell_range)
        if args.header_rows is not None:
            query['header_rows'] = args.header_rows
        data = read_input(args.input)
        source = read_bytes(data, source=str(args.input))
        if args.max_output_bytes is not None or args.cursor is not None:
            from et_output_page import page
            from wps_read_view import encoded as encode_page
            result = page(source, data, query, is_preview=args.preview,
                          max_bytes=12000 if args.max_output_bytes is None else args.max_output_bytes, cursor=args.cursor)
            encoded = encode_page(result)
        else:
            result = preview(source, query) if args.preview else analyze(source, query)
            result['source_sha256'] = hashlib.sha256(data).hexdigest()
            encoded = encode_result(result)
    except (ParseError, OSError) as error:
        result = failure(getattr(error, 'status', 'io_error'), getattr(error, 'location', {'stage': 'input'}), str(error))
        encoded = json.dumps(result, ensure_ascii=False)
    budget = 12000 if args.cursor is not None and args.max_output_bytes is None else args.max_output_bytes
    if budget is not None and budget >= 512:
        from wps_read_view import check_serialized_bytes
        try:
            check_serialized_bytes(encoded, budget)
        except ParseError as error:
            result = failure(error.status, error.location, str(error))
            encoded = json.dumps(result, ensure_ascii=False)
    from wps_read_view import print_json_record
    print_json_record(encoded, max_bytes=budget)
    return 0 if result.get('status') == 'ok' else 3 if result.get('status') == 'partial' else 1


if __name__ == '__main__':
    sys.exit(main())
