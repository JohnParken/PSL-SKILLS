"""Inspect sources, validate an explicit multi-source plan, and fill an XLSX copy."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_runtime import Dependencies, fields, probe, read_request, require, snapshot, task_path, text_field

SCHEMA = 'table_workflow_plan_v1'
MAX_WRITES = 10000
MAX_REPORT_BYTES = 16 * 1024 * 1024


class WorkflowError(ValueError):
    """A refusal with actionable, bounded diagnostics."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def encode_plan(value):
    """Keep JSON-number filter/path selectors replayable without binary floats."""
    if isinstance(value, Decimal):
        require(value.is_finite(), 'Nonfinite plan number')
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(encode_plan(item) for item in value) + ']'
    if isinstance(value, dict):
        require(all(isinstance(key, str) for key in value), 'Plan object keys must be strings')
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + encode_plan(item) for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def encode(value):
    if isinstance(value, dict) and value.get('schema_version') == SCHEMA:
        return encode_plan(value)
    if isinstance(value, dict) and 'plan' in value:
        # Audit data retains the existing Decimal-as-string convention. The plan
        # is executable JSON and must retain numeric versus text literals exactly.
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':'
                              + (encode_plan(item) if key == 'plan' else encode(item)) for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def selection(bounds, api):
    require(isinstance(bounds, str), 'Explicit range required')
    ends = bounds.split(':')
    require(len(ends) in (1, 2), 'Range must be A1 or A1:C10')
    first, last = api.address(ends[0]), api.address(ends[-1])
    require(first[0] <= last[0] and first[1] <= last[1]
            and (last[0] - first[0] + 1) * (last[1] - first[1] + 1) <= 200000, 'Reversed or oversized range')
    return (*first, *last)


def exclusions(value, first, last):
    require(isinstance(value, list) and all(type(item) is int and first <= item <= last for item in value)
            and len(value) == len(set(value)), 'exclude_rows must contain unique physical data row numbers within range')
    return set(value)


def columns_for(selectors, columns, schema, api):
    require(isinstance(selectors, list) and 1 <= len(selectors) <= 8, 'Provide 1..8 key selectors')
    resolved = [api.headers.resolve_column(item, columns, schema) for item in selectors]
    require(len(resolved) == len(set(resolved)), 'Duplicate key selector')
    return resolved


class Workflow:
    def __init__(self, dependencies):
        self.api = dependencies
        self.snapshots = {}
        self.books = {}
        self.warnings = []
        self.publication = {'output_written': False, 'audit_written': False, 'verification': 'not_run'}

    def load(self, spec):
        path = spec['path']
        if path not in self.snapshots:
            require(len(self.snapshots) < 17, 'At most 16 source files plus one template')
            raw, digest = snapshot(path)
            require(sum(len(item[0]) for item in self.snapshots.values()) + len(raw) <= 128 * 1024 * 1024,
                    'Combined source snapshots exceed 128 MiB')
            self.snapshots[path] = (raw, digest)
            try:
                self.books[path] = self.api.load(path, raw)
            except Exception as exc:
                raise WorkflowError('Source parsing failed: ' + path + ': ' + str(exc), {
                    'source_path': path, 'source_error_type': getattr(exc, 'remote_code', None) or type(exc).__name__, 'source_status': getattr(exc, 'status', None),
                    'source_location': getattr(exc, 'location', None)}) from exc
        if 'sha256' in spec:
            require(spec['sha256'] == self.snapshots[path][1], 'Source changed; inspect and rebuild mapping: ' + path)
        return self.books[path]

    def inspect(self, request):
        fields(request, ['files'])
        require(isinstance(request['files'], list) and 1 <= len(request['files']) <= 17, 'Inspect 1..17 explicitly supplied files')
        results = []
        for spec in request['files']:
            fields(spec, ['path'], ['sheet', 'range'])
            book = self.load(spec)
            names = list(book.cells)
            if 'sheet' in spec:
                require(spec['sheet'] in book.cells, 'Unknown worksheet')
                names = [spec['sheet']]
            sheets = []
            for name in names[:8]:
                cells = book.cells[name]
                positions = [self.api.xlsx.address(ref) for ref in cells]
                last_row = max((p[0] for p in positions), default=1)
                last_col = max((p[1] for p in positions), default=1)
                bounds = selection(spec['range'], self.api.xlsx) if 'range' in spec else (1, 1, last_row, last_col)
                first_row, first_col, end_row, end_col = bounds
                preview = []
                for row in range(first_row, min(end_row, first_row + 3) + 1):
                    for col in range(first_col, min(end_col, first_col + 7) + 1):
                        ref = self.api.analysis.column_name(col) + str(row)
                        cell = cells.get(ref, {})
                        value = cell.get('value')
                        rendered = '' if value is None else str(value)
                        preview.append({'cell': ref, 'type': self.api.analysis.typed_value(cell)[0],
                                        'value_preview': rendered[:100], 'value_truncated': len(rendered) > 100,
                                        'formula': cell.get('formula') is not None, 'cache_status': cell.get('cache_status')})
                merges = self.api.merges(book, name)
                sheets.append({'name': name, 'stored_cells': len(cells), 'last_row': last_row, 'last_column': last_col,
                               'formula_count': sum(cell.get('formula') is not None for cell in cells.values()),
                               'merges': merges[:20], 'merges_omitted': max(0, len(merges) - 20), 'preview': preview,
                               'preview_only': True, 'selected_bounds': bounds,
                               'unshown_positions': (end_row - first_row + 1) * (end_col - first_col + 1) - len(preview)})
            issues = getattr(book, 'wps_issues', [])
            results.append({'path': spec['path'], 'sha256': self.snapshots[spec['path']][1],
                            'source_status': 'native_loaded' if self.api.is_native(book) else getattr(book, 'wps_source_status', 'ok'),
                            'source_issues': issues[:5], 'source_issues_omitted': max(0, len(issues) - 5),
                            'source_coverage': getattr(book, 'wps_source_coverage', {}),
                            'sheets': sheets, 'sheets_omitted': max(0, len(names) - 8)})
        partial_count = sum(file['source_status'] not in ('ok', 'native_loaded') for file in results)
        return {'schema_version': 'table_workflow_inspection_v1', 'status': 'partial' if partial_count else 'inspected',
                'operation': 'inspect', 'source_partial_count': partial_count, 'files': results,
                'complete_data_for_computation': False, 'next': 'Resolve roles, ranges, keys, units and periods; build an explicit plan'}

    def source_table(self, name, spec):
        fields(spec, ['path', 'sha256', 'sheet', 'range', 'header_rows', 'keys', 'unit', 'period', 'repeated_keys'],
               ['formulas', 'exclude_rows', 'filters', 'accept_partial', 'period_check'])
        for key in ('unit', 'period'):
            text_field(spec[key], key)
        require(spec['repeated_keys'] in ('reject', 'aggregate'), 'repeated_keys must be reject or aggregate')
        require(type(spec.get('accept_partial', False)) is bool, 'accept_partial must be boolean')
        book = self.load(spec)
        if spec.get('formulas') == 'calculate' and self.api.is_native(book):
            # region_from_book.calculate mutates its argument. Each recomputation
            # starts from the immutable file snapshot; cached regions always see
            # the original saved values, independently of source declaration order.
            book = self.api.open_snapshot(self.snapshots[spec['path']][0], spec['path'])
        if getattr(book, 'wps_source_status', 'ok') != 'ok':
            diagnostic = {'source': name, 'source_status': book.wps_source_status, 'source_issues': book.wps_issues,
                          'source_coverage': book.wps_source_coverage}
            declared_cells = (book.wps_source_coverage.get('cell_value_coverage') == 'complete_within_declared_sheets'
                              and book.wps_source_coverage.get('omissions_affect_cell_values') is False)
            # OOXML's current producer has no cell-coverage assertion. Permit only
            # its exact style-only issue code, explicitly accepted, without adding
            # a fabricated completeness assertion to the original diagnostics.
            style_only = (book.wps_input_schema == 'ooxml_spreadsheet_readonly_v1'
                          and bool(book.wps_issues)
                          and all(isinstance(issue, dict) and issue.get('code') == 'style_metadata_not_interpreted'
                                  for issue in book.wps_issues)
                          and not book.wps_source_coverage.get('omissions_affect_cell_values', False)
                          and not book.wps_source_coverage.get('reading_assessment', {}).get('cell_values', {}).get('omissions_affect_cell_values', False))
            if (not spec.get('accept_partial', False)
                    or not (declared_cells or style_only)):
                raise WorkflowError('Partial ET source requires explicit acceptance and declared cell coverage or allowlisted style-only omissions', diagnostic)
            diagnostic['accepted_scope'] = 'declared_cells' if declared_cells else 'style_only_issues; global_cell_coverage_not_asserted'
            self.warnings.append(diagnostic)
        table = self.api.analysis.region_from_book(book, spec['sheet'], spec['range'], header_rows=spec['header_rows'],
                                                  formulas=spec.get('formulas', 'reject'))
        cols, headers, rows, metadata = table
        excluded = exclusions(spec.get('exclude_rows', []), metadata['header_schema']['data_first_row'], selection(spec['range'], self.api.xlsx)[2])
        _, _, _, filters = self.api.analysis.validate_query({'filters': spec.get('filters', [])}, cols, metadata['header_schema'])
        selected = self.api.select_rows(rows, excluded, filters)
        keys = columns_for(spec['keys'], cols, metadata['header_schema'], self.api)
        side = {'rows': selected, 'indices': [cols.index(key) for key in keys]}
        _, _, diagnostics = self.api.match.index_rows(side, 'never')
        if diagnostics['null_key_rows'] or spec['repeated_keys'] == 'reject' and diagnostics['duplicate_key_groups']:
            raise WorkflowError('Missing source keys or unapproved repeated keys: ' + name, diagnostics)
        if metadata['unverified_formula_caches']:
            self.warnings.append({'source': name, 'unverified_formula_caches': metadata['unverified_formula_caches']})
        metadata = {**metadata, 'workflow_source': name, 'source_path': spec['path'], 'source_sha256': spec['sha256'],
                    'selected_rows': len(selected), 'explicit_excluded_rows': sorted(excluded),
                    'filter_excluded_rows': len(rows) - len(excluded) - len(selected), 'key_diagnostics': diagnostics,
                    'unit': spec['unit'], 'period': spec['period'], 'filters': spec.get('filters', [])}
        if 'period_check' in spec:
            from workflow_quality import period_coverage
            metadata['period_coverage'] = period_coverage((cols, headers, selected, metadata), keys, spec['period_check'], self.api)
            if metadata['period_coverage']['status'] == 'partial':
                self.warnings.append({'source': name, 'period_coverage': metadata['period_coverage']})
        return (cols, headers, selected, metadata), keys

    def enrich(self, specs, tables):
        require(isinstance(specs, list) and len(specs) <= 16, 'At most 16 dimension lookups')
        reports = {}
        raw_names = set(tables)
        for spec in specs:
            fields(spec, ['id', 'left', 'right', 'left_keys', 'right_keys', 'columns', 'relationship', 'max_rows', 'keys', 'repeated_keys', 'basis'])
            name = text_field(spec['id'], 'lookup id')
            text_field(spec['basis'], 'lookup basis')
            require(name not in tables and spec['left'] in tables and spec['right'] in raw_names, 'Lookup requires a new id, earlier left table and raw right dimension')
            require(spec['repeated_keys'] in ('reject', 'aggregate'), 'Lookup grouping repeated_keys must be reject or aggregate')
            left, _ = tables[spec['left']]
            right, _ = tables[spec['right']]
            table, report = self.api.lookup(left, right, {key: spec[key] for key in ('left_keys', 'right_keys', 'columns', 'relationship', 'max_rows')})
            keys = columns_for(spec['keys'], table[0], table[3]['header_schema'], self.api)
            _, _, quality = self.api.match.index_rows({'rows': table[2], 'indices': [table[0].index(key) for key in keys]}, 'never')
            if quality['null_key_rows'] or spec['repeated_keys'] == 'reject' and quality['duplicate_key_groups']:
                raise WorkflowError('Enriched grouping keys are missing or duplicated without aggregate policy', quality)
            table[3].update(lookup_chain=[*left[3].get('lookup_chain', []), name], workflow_source=name, key_diagnostics=quality)
            tables[name] = (table, keys)
            reports[name] = {'definition': spec, 'left': spec['left'], 'right': spec['right'], **report}
        return reports

    def target(self, spec):
        fields(spec, ['path', 'sha256', 'sheet', 'range', 'header_rows', 'keys', 'columns'], ['exclude_rows', 'overwrite'])
        require(Path(spec['path']).suffix.lower() == '.xlsx', 'First-stage target must be an existing XLSX template')
        require(spec.get('overwrite', 'blank_only') in ('blank_only', 'replace_values'), 'Unknown overwrite policy')
        book = self.load(spec)
        require(spec['sheet'] in book.cells, 'Unknown target worksheet')
        bounds = selection(spec['range'], self.api.xlsx)
        cells = book.cells[spec['sheet']]
        def read_cell(ref):
            cell = cells.get(ref, {})
            require(cell.get('formula') is None and cell.get('type') != 'e', 'Formula/error in target header or key: ' + ref)
            return self.api.analysis.typed_value(cell)
        schema = self.api.target_headers(book, spec['sheet'], bounds, spec['header_rows'], read_cell)
        cols = [item['column'] for item in schema['columns']]
        keys = columns_for(spec['keys'], cols, schema, self.api)
        excluded = exclusions(spec.get('exclude_rows', []), schema['data_first_row'], bounds[2])
        rows = [(row, [read_cell(col + str(row)) for col in keys])
                for row in range(schema['data_first_row'], bounds[2] + 1) if row not in excluded]
        require(0 < len(rows) <= MAX_WRITES, 'Target has no data rows or exceeds row budget')
        side = {'rows': rows, 'indices': list(range(len(keys)))}
        _, key_by_row, diagnostics = self.api.match.index_rows(side, 'never')
        if diagnostics['null_key_rows'] or diagnostics['duplicate_key_groups']:
            raise WorkflowError('Target keys must be present and unique', diagnostics)
        require(isinstance(spec['columns'], list) and 1 <= len(spec['columns']) <= 64, 'Target needs 1..64 mapped columns')
        require(len(rows) * len(spec['columns']) <= MAX_WRITES, 'More than 10000 target writes')
        outputs = []
        used = set(keys)
        for item in spec['columns']:
            fields(item, ['column', 'metric', 'unit', 'period', 'decimals'])
            col = self.api.headers.resolve_column(item['column'], cols, schema)
            require(col not in used, 'Repeated target column or attempted key overwrite')
            used.add(col)
            require(type(item['decimals']) is int and 0 <= item['decimals'] <= 12, 'decimals must be 0..12')
            outputs.append({**item, 'column': col})
        return book, schema, key_by_row, outputs

    def prepare(self, plan):
        self.warnings = []
        fields(plan, ['schema_version', 'sources', 'target', 'metrics'], ['derived', 'lookups', 'checks'])
        require(plan['schema_version'] == SCHEMA, 'Unsupported workflow plan schema')
        require(isinstance(plan['sources'], dict) and 1 <= len(plan['sources']) <= 16, 'Provide 1..16 named source regions')
        template, target_schema, target_keys, outputs = self.target(plan['target'])
        require(all(not task_path(item['path']).samefile(task_path(plan['target']['path'])) for item in plan['sources'].values()),
                'Template cannot also be a source, including path aliases')
        tables = {name: self.source_table(name, spec) for name, spec in plan['sources'].items()}
        lookup_reports = self.enrich(plan.get('lookups', []), tables)
        metrics = {}
        metric_reports = {}
        require(isinstance(plan['metrics'], list) and 1 <= len(plan['metrics']) <= 64, 'Provide 1..64 aggregate metrics')
        for metric in plan['metrics']:
            fields(metric, ['id', 'source', 'operation', 'basis'], ['measure', 'missing', 'missing_target', 'scale', 'unit'])
            name = text_field(metric['id'], 'metric id')
            text_field(metric['basis'], 'metric basis')
            require(name not in metrics and metric['source'] in tables, 'Duplicate metric id or unknown source')
            operation = metric['operation']
            require(operation in ('sum', 'count', 'min', 'max', 'mean'), 'Unsupported aggregation')
            require(metric.get('missing', 'reject') in ('reject', 'omit', 'blank'), 'missing must be reject, omit or blank')
            require(metric.get('missing_target', 'reject') in ('reject', 'blank'), 'missing_target must be reject or blank')
            table, keys = tables[metric['source']]
            require(len(keys) == len(next(iter(target_keys.values()))), 'Source and target key arity differs')
            require(operation == 'count' or 'measure' in metric, 'Numeric aggregation requires a measure')
            require(operation != 'count' or 'measure' not in metric, 'count counts rows; omit measure')
            query = {'group_by': keys}
            if operation != 'count':
                query['measure'] = metric['measure']
                measure_column = self.api.headers.resolve_column(metric['measure'], table[0], table[3]['header_schema'])
                require(not measure_column.startswith('lookup:'), 'Dimension columns are attributes; aggregate only original fact measures')
            summary = self.api.analysis.summarize_region(table, query)
            summary['aggregation_input_rows'] = len(table[2])
            summary['excluded_rows'] = summary['source_rows'] - summary['selected_rows']
            source = table[3]
            scale = Decimal(str(metric.get('scale', '1')))
            require(scale.is_finite() and scale > 0 and abs(scale.adjusted()) <= 100, 'scale must be a bounded positive decimal')
            unit = metric.get('unit', 'rows' if operation == 'count' else source['unit'])
            text_field(unit, 'metric unit')
            require(operation != 'count' or scale == 1 and unit == 'rows', 'Row counts cannot be unit-scaled')
            require(scale == 1 or 'unit' in metric, 'Scaling requires explicit output unit')
            require(scale != 1 or unit == ('rows' if operation == 'count' else source['unit']), 'Changed unit requires an explicit non-unit scale')
            index = {}
            for group in summary['groups']:
                if group['blank_measure'] and metric.get('missing', 'reject') == 'reject':
                    raise WorkflowError('Blank measure requires explicit missing=omit: ' + name,
                                        {'blank_measure': group['blank_measure'], 'source': metric['source']})
                value = Decimal(group['rows']) if operation == 'count' else group['numeric'][operation]
                if group['blank_measure'] and metric.get('missing') == 'blank':
                    value = None
                require(value is not None or metric.get('missing') == 'blank', 'No numeric observations for metric: ' + name)
                key = tuple((item['type'], item['value']) for item in group['keys'])
                index[key] = None if value is None else self.api.math.exact(lambda: value * scale)
            require(index or metric.get('missing_target') == 'blank', 'Metric selected no source rows: ' + name)
            if metric.get('missing') == 'omit' and any(g['blank_measure'] for g in summary['groups']):
                self.warnings.append({'metric': name, 'blank_measures_omitted': sum(g['blank_measure'] for g in summary['groups'])})
            target_set = set(target_keys.values())
            missing = target_set - set(index)
            require(not missing or metric.get('missing_target') == 'blank', 'Unmatched target keys for metric ' + name + ': ' + str(len(missing)))
            if missing:
                self.warnings.append({'metric': name, 'unmatched_target_keys': len(missing), 'action': 'blank'})
                index.update((key, None) for key in missing)
            blank_groups = sum(value is None for value in index.values())
            if blank_groups:
                self.warnings.append({'metric': name, 'blank_group_results': blank_groups})
            metrics[name] = {'values': index, 'unit': unit, 'period': source['period'], 'domain': 'source_groups',
                             'source_keys': [key for key in index if key not in missing]}
            metric_reports[name] = {'definition': metric, 'summary': summary, 'unit': unit, 'period': source['period'],
                                    'unmatched_source_groups': len(set(index) - target_set), 'unmatched_target_keys': len(missing),
                                    'row_membership': self.membership(table, keys)}
        derived = plan.get('derived', [])
        require(isinstance(derived, list) and len(derived) <= 64, 'At most 64 derived metrics')
        for item in derived:
            if 'operation' in item:
                from workflow_quality import ratio_metric
                name = text_field(item['id'], 'derived id')
                require(name not in metrics, 'Duplicate derived metric')
                metrics[name] = ratio_metric(item, metrics, target_keys, self.api, self.warnings)
                metric_reports[name] = {'definition': item, 'arithmetic': 'XLSX Decimal ratio; explicit exceptional-value policies'}
                continue
            fields(item, ['id', 'formula', 'bindings', 'unit', 'period', 'basis'], ['missing'])
            name = text_field(item['id'], 'derived id')
            for field in ('formula', 'unit', 'period', 'basis'):
                text_field(item[field], field)
            require(name not in metrics and isinstance(item['bindings'], dict) and 1 <= len(item['bindings']) <= 16,
                    'Duplicate metric or invalid formula bindings')
            require(item.get('missing', 'reject') in ('reject', 'blank'), 'Derived missing must be reject or blank')
            for ref, dependency in item['bindings'].items():
                require(self.api.xlsx.address(ref)[0] == 1 and ref == ref.upper(), 'Formula bindings use uppercase row-1 cell addresses')
                require(dependency in metrics, 'Derived metrics must reference earlier declared metrics')
            values = {}
            requests = []
            for key in set(target_keys.values()):
                if any(metrics[dependency]['values'][key] is None for dependency in item['bindings'].values()):
                    require(item.get('missing', 'reject') == 'blank', 'Missing input for derived metric: ' + name)
                    values[key] = None
                    continue
                requests.append((key, {ref: metrics[dependency]['values'][key] for ref, dependency in item['bindings'].items()}))
            evaluated = self.api.evaluate_many(item['formula'], 'Metrics', [bindings for _, bindings in requests])
            for (key, _), value in zip(requests, evaluated):
                require(isinstance(value, Decimal) and value.is_finite(), 'Derived result must be a finite number')
                values[key] = value
            if any(value is None for value in values.values()):
                self.warnings.append({'metric': name, 'blank_derived_results': sum(value is None for value in values.values())})
            metrics[name] = {'values': values, 'unit': item['unit'], 'period': item['period'], 'domain': 'target_keys'}
            metric_reports[name] = {'definition': item, 'arithmetic': 'XLSX Decimal formula subset; 34 significant digits; not Excel equivalence'}
        from workflow_quality import business_checks
        checks = business_checks(plan.get('checks', []), metrics, target_keys, self.api, self.warnings)
        patch = {}
        writes = []
        for mapping in outputs:
            require(mapping['metric'] in metrics, 'Unknown target metric')
            metric = metrics[mapping['metric']]
            require((mapping['unit'], mapping['period']) == (metric['unit'], metric['period']), 'Target unit/period differs from metric declaration')
            for row, key in target_keys.items():
                ref = mapping['column'] + str(row)
                previous = template.cells[plan['target']['sheet']].get(ref, {})
                require(previous.get('formula') is None, 'Cannot overwrite a target formula: ' + ref)
                require(plan['target'].get('overwrite', 'blank_only') == 'replace_values' or previous.get('value') in (None, ''),
                        'Target is nonblank; requires authorized replace_values: ' + ref)
                value = metric['values'][key]
                rounded = None if value is None else self.api.evaluate_bound('ROUND(A1,' + str(mapping['decimals']) + ')', 'Metrics', {'A1': value})
                if rounded is not None:
                    digits = ''.join(str(digit) for digit in rounded.as_tuple().digits).rstrip('0')
                    require(len(digits) <= 15 and (rounded == 0 or -307 <= rounded.adjusted() <= 307),
                            'Target value exceeds supported Excel numeric precision/range')
                patch[ref] = {'value': rounded}
                writes.append({'sheet': plan['target']['sheet'], 'cell': ref, 'metric': mapping['metric'],
                               'key': [{'type': kind, 'value': val} for kind, val in key],
                               'unrounded_value': value, 'value': rounded, 'unit': mapping['unit'], 'period': mapping['period'],
                               'decimals': mapping['decimals'], 'rounding': 'ROUND_HALF_UP at final write'})
        candidate = self.api.open_snapshot(self.snapshots[plan['target']['path']][0], plan['target']['path'])
        require(not any(entry['value'] is None for entry in patch.values())
                or not any(cell.get('formula') is not None for cells in candidate.cells.values() for cell in cells.values()),
                'Blank metric writes require a formula-free template; do not recalculate missing inputs as zero')
        # Apply and calculate in memory BEFORE any final file or archive is created.
        candidate.patch({'sheets': [{'name': plan['target']['sheet'], 'cells': patch}]})
        calculation = candidate.calculate()
        if calculation['status'] != 'calculated_subset' or calculation['retained_not_refreshed']:
            raise WorkflowError('Template formula/cache preflight refused output', {'calculation': calculation})
        self.verify_preserved(template, candidate, {(plan['target']['sheet'], ref): entry['value'] for ref, entry in patch.items()})
        report = {'schema_version': 'table_workflow_audit_v1', 'status': 'partial' if self.warnings else 'validated',
                  'plan': plan, 'source_fingerprints': {path: digest for path, (_, digest) in self.snapshots.items()},
                  'dependencies': {name: item['skill_sha256'] for name, item in self.api.checks.items()},
                  'dependency_interfaces': {name: item.get('interface', {'transport': 'legacy_in_process'}) for name, item in self.api.checks.items()},
                  'target_headers': target_schema, 'metrics': metric_reports, 'writes': writes, 'warnings': self.warnings,
                  'source_regions': {name: table[3] for name, (table, _) in tables.items()}, 'lookups': lookup_reports, 'checks': checks,
                  'calculation': calculation, 'preservation': 'non-target cell values/styles/formula text checked; formula caches may recalculate',
                  'verification': 'in_memory_passed', 'business_semantics': 'explicit model/user plan; not independently inferred or certified'}
        require(len(encode(report).encode('utf-8')) <= MAX_REPORT_BYTES, 'Audit exceeds 16 MiB; narrow task')
        return candidate, report

    def membership(self, table, keys):
        cols, _, rows, _ = table
        side = {'rows': rows, 'indices': [cols.index(key) for key in keys]}
        index, _, _ = self.api.match.index_rows(side, 'never')
        return [{'key': [{'type': kind, 'value': value} for kind, value in key], 'physical_rows': [row for row, _ in records]}
                for key, records in index.items()]

    def verify_preserved(self, original, candidate, writes):
        return self.api.verify_preserved(original, candidate, writes)

    def apply(self, plan, output, audit):
        output_path, audit_path = task_path(output, output=True), task_path(audit, output=True)
        require(output_path.suffix.lower() == '.xlsx' and audit_path.suffix.lower() == '.json', 'Output must be XLSX and audit must be JSON')
        require(output_path != audit_path, 'Outputs must be distinct')
        self.publication.update(output=str(output_path), audit=str(audit_path))
        candidate, report = self.prepare(plan)
        for directory in {output_path.parent, audit_path.parent}:
            probe(directory)
        self.unchanged_inputs()
        report['output'] = output_path.as_posix()
        report['audit'] = audit_path.as_posix()
        try:
            candidate.save(output_path)
            self.publication['output_written'] = True
            self.api.assert_written(candidate, output_path)
            self.unchanged_inputs()
            self.publication['verification'] = 'workbook_and_inputs_passed'
            report['verification'] = 'written_package_and_inputs_passed'
            report['output_sha256'] = hashlib.sha256(output_path.read_bytes()).hexdigest()
            raw = encode(report).encode('utf-8')
            require(len(raw) <= MAX_REPORT_BYTES, 'Audit exceeds 16 MiB')
            with audit_path.open('xb') as stream:
                self.publication['audit_written'] = 'incomplete'
                stream.write(raw)
            require(audit_path.read_bytes() == raw, 'Audit readback failed')
            self.publication.update(audit_written=True, verification='written_package_and_inputs_passed')
        except Exception as exc:
            if self.publication['output_written'] is False and output_path.exists():
                self.publication['output_written'] = 'unknown'
            raise WorkflowError('Delivery incomplete; retain existing outputs for inspection, do not regenerate blindly',
                                {'reason': str(exc), 'output_exists': output_path.exists(), 'audit_exists': audit_path.exists()}) from exc
        delivery = {}
        for path in (output_path, audit_path):
            result = {}
            self.api.finish_delivery(result, str(path), [*self.snapshots, *(str(other) for other in (output_path, audit_path) if other != path)])
            delivery[path.as_posix()] = result['delivery']
        residuals = any(item.get('temporary_residuals') for item in delivery.values())
        archive_failed = any(item['archive_status'] not in ('copied', 'already_archived', 'skipped') for item in delivery.values())
        return self.receipt(report) | {'delivery': delivery, 'output': str(output_path), 'audit': str(audit_path),
                                      'output_written': True, 'audit_written': True,
                                      'delivery_incomplete': residuals or archive_failed,
                                      'workflow_intermediate_files_created': 0, 'write_probes': 'created_read_back_removed',
                                      'cleanup': 'archive_cleanup_incomplete' if residuals else 'no_workflow_intermediates'}

    def unchanged_inputs(self):
        for path, (_, digest) in self.snapshots.items():
            require(snapshot(path)[1] == digest, 'Input changed during workflow: ' + path)

    @staticmethod
    def receipt(report):
        return {'schema_version': 'table_workflow_receipt_v1', 'status': report['status'], 'verification': report['verification'],
                'write_count': len(report['writes']), 'writes_preview': report['writes'][:10],
                'writes_omitted': max(0, len(report['writes']) - 10), 'warning_count': len(report['warnings']),
                'warnings_preview': report['warnings'][:3], 'warnings_omitted': max(0, len(report['warnings']) - 3),
                'lookup_count': len(report['lookups']), 'check_count': len(report['checks']),
                'checks_failed': sum(item['failed_count'] for item in report['checks']),
                'checks_unassessed': sum(item['unassessed_count'] for item in report['checks']),
                'checks_preview': [{key: item[key] for key in ('id', 'status', 'failed_count', 'unassessed_count')}
                                   for item in report['checks'][:5]], 'checks_omitted': max(0, len(report['checks']) - 5),
                'calculation': report['calculation'], 'provenance': 'full definitions, input fingerprints and group row membership in audit on apply'}


def bounded_result(result, limit=24000):
    """Bound stdout without pretending a projection is full computation evidence."""
    if len(encode(result)) <= limit:
        return result
    original = len(encode(result))
    result = dict(result)
    if len(result.get('message', '')) > 4000:
        result['message_original_characters'] = len(result['message'])
        result['message'] = result['message'][:4000]
        result['message_truncated'] = True
    if 'files' in result:
        result['files'] = [{**file, 'sheets': [dict(sheet) for sheet in file['sheets']]} for file in result['files']]
        for file in result['files']:
            for sheet in file['sheets']:
                sheet['preview_omitted_due_to_budget'] = len(sheet.pop('preview', []))
            file['source_diagnostics_omitted_due_to_budget'] = True
            file.pop('source_coverage', None)
            file.pop('source_issues', None)
    else:
        for key in ('writes_preview', 'warnings_preview', 'checks_preview', 'diagnostics'):
            if key in result:
                result[key + '_omitted_due_to_budget'] = True
                result.pop(key)
    result['projection_truncated'] = True
    result['original_characters'] = original
    if len(encode(result)) <= limit:
        return result
    # Projection must never turn an already published result into a new failure
    # or erase its file identities/partial/cleanup state because diagnostics grew.
    core = ('schema_version', 'status', 'operation', 'verification', 'output', 'audit', 'output_written', 'audit_written',
            'delivery_incomplete', 'cleanup', 'write_count', 'warning_count', 'source_partial_count',
            'lookup_count', 'check_count', 'checks_failed', 'checks_unassessed',
            'complete_data_for_computation', 'error_type', 'check_kind', 'artifact_validation', 'task_writable')
    compact = {key: result[key] for key in core if key in result}
    compact.update(projection_truncated=True, original_characters=original, details_omitted_due_to_budget=True,
                   next='Use the audit for full calculation provenance; inspect individual files/sheets for omitted source details')
    if 'message' in result:
        compact['message'] = result['message'][:1000]
        compact['message_truncated'] = True
    if 'delivery' in result:
        compact['delivery'] = {path: {'archive_status': item.get('archive_status', 'unknown'),
                                     'archive_message': item.get('archive_message'),
                                     'temporary_residual_count': len(item.get('temporary_residuals', [])),
                                     'details_omitted_due_to_budget': True} for path, item in result['delivery'].items()}
    if 'files' in result:
        compact['files'] = [{key: item[key] for key in ('path', 'sha256', 'source_status')} for item in result['files'][:3]]
        compact['files_omitted_due_to_budget'] = max(0, len(result['files']) - 3)
    require(len(encode(compact)) <= limit, 'Core result identities exceed output budget')
    return compact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'inspect', 'plan', 'apply'))
    parser.add_argument('--xlsx-root', required=True)
    parser.add_argument('--et-root')
    parser.add_argument('--write', action='store_true', help='Include an exclusive write probe in check')
    parser.add_argument('--output')
    parser.add_argument('--audit')
    args = parser.parse_args()
    workflow = None
    dependencies = None
    try:
        if args.command != 'apply':
            require(args.output is None and args.audit is None, '--output/--audit only apply to the apply command')
        dependencies = Dependencies(args.xlsx_root, args.et_root)
        check = dependencies.check(args.write)
        if args.command == 'check':
            result = check
        else:
            workflow = Workflow(dependencies)
            request = read_request(sys.stdin.buffer)
            if args.command == 'inspect':
                result = workflow.inspect(request)
            elif args.command == 'plan':
                _, report = workflow.prepare(request)
                result = workflow.receipt(report)
            else:
                require(args.output and args.audit, 'apply requires --output and --audit')
                result = workflow.apply(request, args.output, args.audit)
        print(encode(bounded_result(result)), flush=True)
        return 3 if result.get('status') == 'partial' or result.get('delivery_incomplete') else 0
    except Exception as exc:
        diagnostics = dict(getattr(exc, 'diagnostics', {}))
        if hasattr(exc, 'calculation'):
            diagnostics['calculation'] = exc.calculation
        result = {'status': 'failed', 'error_type': getattr(exc, 'remote_code', None) or type(exc).__name__, 'message': str(exc),
                  'diagnostics': diagnostics, 'output_written': False, 'audit_written': False}
        if workflow is not None:
            result.update(workflow.publication, warning_count=len(workflow.warnings), warnings_preview=workflow.warnings[:3],
                          warnings_omitted=max(0, len(workflow.warnings) - 3))
        print(encode(bounded_result(result)), flush=True)
        return 1
    finally:
        if dependencies is not None:
            dependencies.close()


if __name__ == '__main__':
    raise SystemExit(main())
