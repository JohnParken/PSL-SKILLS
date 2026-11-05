"""Explicit period coverage, exceptional-value and reconciliation policies."""
from decimal import Decimal
import re

from workflow_runtime import fields, require, text_field


class QualityError(ValueError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def number(value, label, nonnegative=False):
    require(type(value) in (str, int, Decimal), label + ' must be a decimal number, not a boolean/float')
    result = Decimal(value)
    require(result.is_finite() and (result == 0 or abs(result.adjusted()) <= 100), label + ' must be finite and bounded')
    require(not nonnegative or result >= 0, label + ' must be nonnegative')
    return result


def period_coverage(table, keys, config, api):
    fields(config, ['column', 'expected', 'missing'])
    require(config['missing'] in ('reject', 'partial'), 'Period missing policy must be reject or partial')
    expected = config['expected']
    require(isinstance(expected, list) and 1 <= len(expected) <= 120
            and all(isinstance(item, str) and re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', item) and not item.startswith('0000') for item in expected)
            and len(expected) == len(set(expected)), 'Expected periods must be 1..120 distinct YYYY-MM text values')
    cols, _, rows, metadata = table
    column = api.headers.resolve_column(config['column'], cols, metadata['header_schema'])
    require(column not in keys, 'Period coverage keys must identify entities, excluding the period column')
    position = cols.index(column)
    indices = [cols.index(key) for key in keys]
    observed = {}
    for row_number, row in rows:
        kind, value = row[position]
        require(kind == 'text' and value in expected, 'Missing, unexpected or non-YYYY-MM period at row ' + str(row_number))
        key = tuple(row[index] for index in indices)
        observed.setdefault(key, set()).add(value)
    missing = [{'key': [{'type': kind, 'value': value} for kind, value in key], 'periods': sorted(set(expected) - values)}
               for key, values in observed.items() if set(expected) - values]
    report = {'status': 'partial' if missing or not rows else 'passed', 'expected': expected, 'column': column,
              'entity_count': len(observed), 'missing_entity_periods': missing, 'empty_selection': not rows,
              'scope': 'selected rows, per observed entity; entirely absent target entities use missing_target policy'}
    if report['status'] != 'passed' and config['missing'] == 'reject':
        raise QualityError('Required periods are missing', report)
    return report


def ratio_metric(item, metrics, target_keys, api, warnings):
    fields(item, ['id', 'operation', 'numerator', 'denominator', 'unit', 'period', 'basis'], ['scale', 'zero_denominator', 'missing'])
    require(item['operation'] == 'ratio', 'Unknown derived operation')
    for label in ('unit', 'period', 'basis'):
        text_field(item[label], label)
    require(item['numerator'] in metrics and item['denominator'] in metrics, 'Ratio operands must be earlier metrics')
    scale = number(item.get('scale', '1'), 'ratio scale', True)
    require(scale > 0, 'Ratio scale must be positive')
    for label in ('zero_denominator', 'missing'):
        require(item.get(label, 'reject') in ('reject', 'blank'), label + ' must be reject or blank')
    values, exceptions = {}, []
    requests = []
    for key in dict.fromkeys(target_keys.values()):
        top = metrics[item['numerator']]['values'][key]
        bottom = metrics[item['denominator']]['values'][key]
        reason = 'missing' if top is None or bottom is None else 'zero_denominator' if bottom == 0 else None
        if reason:
            require(item.get(reason, 'reject') == 'blank', 'Ratio ' + reason + ': ' + item['id'])
            values[key] = None
            exceptions.append({'key': [{'type': kind, 'value': value} for kind, value in key], 'reason': reason})
        else:
            requests.append((key, {'A1': top, 'B1': bottom, 'C1': scale}))
    evaluated = api.evaluate_many('A1/B1*C1', 'Metrics', [bindings for _, bindings in requests])
    for (key, _), value in zip(requests, evaluated):
        values[key] = value
    if exceptions:
        warnings.append({'metric': item['id'], 'blank_ratio_results': exceptions})
    return {'values': values, 'unit': item['unit'], 'period': item['period'], 'domain': 'target_keys'}


def business_checks(configs, metrics, target_keys, api, warnings):
    require(isinstance(configs, list) and len(configs) <= 64, 'At most 64 business checks')
    reports, ids, count = [], set(), 0
    for config in configs:
        common = ['id', 'kind', 'left', 'scope', 'basis']
        optional = ['absolute_tolerance', 'relative_tolerance', 'on_failure', 'missing']
        if config.get('kind') == 'equal':
            fields(config, common + ['right'], optional)
        else:
            fields(config, common + ['minimum', 'maximum'], optional)
            require(config['kind'] == 'range', 'Business check kind must be equal or range')
        identity = text_field(config['id'], 'check id')
        text_field(config['basis'], 'check basis')
        require(identity not in ids, 'Duplicate business check id')
        ids.add(identity)
        require(config['scope'] in ('each_target', 'target_total', 'source_total'), 'Unknown business check scope')
        for policy in ('on_failure', 'missing'):
            require(config.get(policy, 'reject') in ('reject', 'partial'), policy + ' must be reject or partial')
        absolute = number(config.get('absolute_tolerance', '0'), 'absolute tolerance', True)
        relative = number(config.get('relative_tolerance', '0'), 'relative tolerance', True)
        require(relative <= 1, 'Relative tolerance must be at most 1')
        names = [config['left']] + ([config['right']] if config['kind'] == 'equal' else [])
        require(all(name in metrics for name in names), 'Unknown business check metric')
        if len(names) == 2:
            require((metrics[names[0]]['unit'], metrics[names[0]]['period']) == (metrics[names[1]]['unit'], metrics[names[1]]['period']),
                    'Business check operands require identical unit and period declarations')
        else:
            require(relative == 0, 'Range checks use absolute tolerance only')
            lower = number(config['minimum'], 'minimum')
            upper = number(config['maximum'], 'maximum')
            require(lower <= upper, 'Reversed business check range')
        if config['scope'] == 'source_total':
            require(all(metrics[name]['domain'] == 'source_groups' for name in names), 'source_total requires aggregate metrics, not target-only derived values')
        keys = list(dict.fromkeys(target_keys.values())) if config['scope'] == 'each_target' else [None]
        count += len(keys)
        require(count <= 20000, 'Business checks exceed 20000 observations')
        observations = []
        for key in keys:
            values = []
            for name in names:
                mapping = metrics[name]['values']
                if key is not None:
                    value = mapping[key]
                else:
                    selected = metrics[name]['source_keys'] if config['scope'] == 'source_total' else dict.fromkeys(target_keys.values())
                    members = [mapping[item] for item in selected]
                    value = None if not members or any(item is None for item in members) else api.math.exact(lambda: sum(members, Decimal(0)))
                values.append(value)
            observation = {'key': None if key is None else [{'type': kind, 'value': value} for kind, value in key], 'left': values[0]}
            if any(value is None for value in values):
                observation['status'] = 'unassessed'
            elif config['kind'] == 'equal':
                difference = api.math.exact(lambda: abs(values[0] - values[1]))
                tolerance = api.math.exact(lambda: max(absolute, relative * abs(values[1])))
                observation.update(right=values[1], difference=difference, tolerance=tolerance,
                                   status='passed' if difference <= tolerance else 'failed')
            else:
                passed = api.math.exact(lambda: lower - absolute <= values[0] <= upper + absolute)
                observation.update(minimum=lower, maximum=upper, tolerance=absolute, status='passed' if passed else 'failed')
            observations.append(observation)
        failed = sum(item['status'] == 'failed' for item in observations)
        unknown = sum(item['status'] == 'unassessed' for item in observations)
        report = {'id': identity, 'definition': config, 'status': 'partial' if failed or unknown else 'passed',
                  'value_stage': 'unrounded_metrics', 'observation_count': len(observations), 'failed_count': failed,
                  'unassessed_count': unknown, 'observations': observations}
        reports.append(report)
        if (failed and config.get('on_failure', 'reject') == 'reject') or (unknown and config.get('missing', 'reject') == 'reject'):
            raise QualityError('Business reconciliation failed or could not be assessed: ' + identity, {'checks': reports})
        if failed or unknown:
            warnings.append({'check': identity, 'failed_count': failed, 'unassessed_count': unknown})
    return reports
