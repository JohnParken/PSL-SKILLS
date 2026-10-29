"""Validate automatic-task records before controllers act on their contents."""
from pathlib import Path
from cleanup_task import _validate, _valid_snapshot, _parts

MAX_ATTEMPTS = 3
RUNNER_SCHEMA = 'docx_auto_task_v1'
STATES = {'initializing', 'ready', 'running', 'retry_pending', 'recovery_pending', 'delivered'}


def file_snapshot(value):
    return (isinstance(value, dict) and value.get('kind') == 'file'
            and _valid_snapshot(value, file_only=True)
            and value['size'] >= 0 and value['links'] == 1)


def validate_manifest(data, manifest, task):
    if not isinstance(data, dict):
        raise ValueError('Automatic manifest must be a JSON object')
    for key in ('entries', 'protected', 'deliverables'):
        if not isinstance(data.get(key), list):
            raise ValueError('Invalid automatic manifest ' + key)
    if any(not isinstance(e, dict) or not isinstance(e.get('path'), str)
           for e in data['entries'] + data['protected']):
        raise ValueError('Automatic manifest entries must be path objects')
    if any(not isinstance(p, str) for p in data['deliverables']):
        raise ValueError('Invalid automatic deliverable paths')
    _validate(data, manifest, task)
    runner = data.get('runner')
    if not isinstance(runner, dict) or runner.get('schema') != RUNNER_SCHEMA:
        raise ValueError('Invalid automatic runner schema')
    workspace, output = runner.get('workspace'), runner.get('output')
    if not isinstance(workspace, str) or len(_parts(workspace)) != 1:
        raise ValueError('Invalid automatic workspace')
    if (not isinstance(output, str) or len(_parts(output)) != 1
            or Path(output).suffix.lower() != '.docx'):
        raise ValueError('Invalid automatic output filename')
    if (type(runner.get('attempts')) is not int or not 0 <= runner['attempts'] <= MAX_ATTEMPTS
            or runner.get('state') not in STATES):
        raise ValueError('Invalid automatic runner phase or attempt count')
    names = {workspace + '/' + n for n in ('worker.py', 'spec.json', 'receipt.json', 'task.lock')}
    stage = runner.get('retry_stage')
    if stage is not None:
        if stage != workspace + '/retry-spec.json':
            raise ValueError('Invalid retry staging path')
        names.add(stage)
    entries = data['entries']
    if ({e['path'] for e in entries if e['kind'] == 'file'} != names
            or [e['path'] for e in entries if e['kind'] == 'directory'] != [workspace]
            or data['deliverables'] != [output]):
        raise ValueError('Automatic task paths differ from the original plan')
    created = runner.get('created')
    expected = {workspace + '/' + n for n in ('worker.py', 'spec.json', 'task.lock')}
    if (not isinstance(created, dict) or not set(created).issubset(expected)
            or any(not file_snapshot(s) for s in created.values())):
        raise ValueError('Invalid helper creation fingerprints')
    if runner['state'] != 'initializing':
        identity = runner.get('workspace_identity')
        if (set(created) != expected or not isinstance(identity, dict)
                or identity.get('kind') != 'directory' or not _valid_snapshot(identity)):
            raise ValueError('Incomplete automatic workspace identity')
    update = runner.get('spec_update')
    if update is not None:
        if (not isinstance(update, dict) or update.get('schema') != 'docx_spec_update_v1'
                or update.get('phase') not in ('preparing', 'prepared') or stage is None
                or update.get('old') != created.get(workspace + '/spec.json')
                or not file_snapshot(update.get('old'))):
            raise ValueError('Invalid specification update intent')
        digest = update.get('sha256')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid specification update digest')
        if update['phase'] == 'preparing' and 'next' in update:
            raise ValueError('Unprepared specification cannot assert a new fingerprint')
        if update['phase'] == 'prepared' and (not file_snapshot(update.get('next'))
                                              or update['next']['sha256'] != digest):
            raise ValueError('Invalid prepared specification fingerprint')
    if 'delivery' in runner:
        validate_result(runner['delivery'], output)
    if runner['state'] == 'delivered' and 'delivery' not in runner:
        raise ValueError('Delivered state requires a verified delivery record')
    if data['phase'] == 'sealed':
        if update is not None:
            raise ValueError('A sealed task cannot have a pending specification update')
        validate_result(runner.get('delivery'), output)
        if (data.get('outcome') != 'success' or runner['state'] != 'delivered'
                or data.get('archive_status') != runner['delivery']['archive_status']):
            raise ValueError('Sealed automatic state differs from its verified delivery')
    return data


def validate_result(result, output):
    if (not isinstance(result, dict) or result.get('status') != 'verified'
            or result.get('output') != output
            or result.get('archive_status') not in ('copied', 'skipped', 'failed')
            or not all(result.get(k) is True for k in ('zip_crc', 'structure', 'content_coverage', 'layout_intent'))
            or not isinstance(result.get('files'), list) or not isinstance(result.get('warnings'), list)
            or any(not isinstance(w, str) for w in result['warnings'])):
        raise ValueError('Invalid verified delivery result')
    if result['archive_status'] == 'copied' and not result['files']:
        raise ValueError('Copied archive result requires file evidence')
    for item in result['files']:
        if (not isinstance(item, dict) or not {'output', 'archive_output', 'archive_status'}.issubset(item)
                or not isinstance(item.get('output'), str)
                or item.get('archive_status') not in ('copied', 'already_archived', 'skipped', 'failed')
                or not (item.get('archive_output') is None or isinstance(item['archive_output'], str))):
            raise ValueError('Invalid archive file result')
    return result


def validate_receipt(receipt, manifest, runner):
    if (not isinstance(receipt, dict) or receipt.get('schema') != 'docx_auto_receipt_v1'
            or receipt.get('manifest') != manifest or receipt.get('created') != runner['created']
            or not file_snapshot(receipt.get('output_fingerprint'))):
        raise ValueError('Invalid worker receipt')
    validate_result(receipt.get('result'), runner['output'])
    return receipt


def validate_config(config, runner, protected):
    if (not isinstance(config, dict) or set(config) != {'spec', 'output', 'protected_inputs'}
            or config['output'] != runner['output'] or not isinstance(config['spec'], dict)
            or config['protected_inputs'] != protected):
        raise ValueError('Invalid owned document configuration')
    return config
