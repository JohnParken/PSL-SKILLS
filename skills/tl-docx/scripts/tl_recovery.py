"""Persist enough publication evidence to recover cleanup after interruption."""

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
import re
import stat
from uuid import uuid4

from tl_io import check_platform, output_name, read_task

SCHEMA = 'tl-docx-publication-v1'
MANIFEST_PATTERN = re.compile(r'\.tl-docx-([0-9a-f]{32})\.cleanup\.json')
ARCHIVE_STATES = ('success', 'partial', 'failed', 'not_required')


@contextmanager
def task_lock(root, *, wait):
    """Serialize tool publication and recovery against the same task directory."""
    check_platform(write=True)
    try:
        fcntl.flock(root, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError as exc:
        raise RuntimeError('Another publication is active in this task directory') from exc
    try:
        yield
    finally:
        fcntl.flock(root, fcntl.LOCK_UN)


def identity(info):
    """Return the stable filesystem identity used in the journal."""
    return [info.st_dev, info.st_ino]


def entry(root, name):
    """Inspect an exact filename without following a link."""
    try:
        return os.stat(name, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return None


def matches(info, expected):
    """Confirm a regular file still has the recorded identity."""
    return info is not None and stat.S_ISREG(info.st_mode) and identity(info) == expected


def checkpoint(journal, record):
    """Durably append one complete record before the next side effect."""
    journal.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
    journal.flush()
    os.fsync(journal.fileno())


def stage_name(manifest):
    """Derive the only removable stage name from a valid manifest basename."""
    match = MANIFEST_PATTERN.fullmatch(manifest)
    if match is None:
        raise ValueError('Expected the original tl-docx cleanup manifest basename')
    return '.tl-docx-' + match[1] + '.tmp'


def clean_stage(root, manifest, stage_identity, final_name, *, require_final):
    """Remove only the recorded stage, protecting all other links and the final file."""
    stage = stage_name(manifest)
    info = entry(root, stage)
    if info is None:
        return
    if not matches(info, stage_identity):
        raise ValueError('Registered temporary file changed or its identity is unconfirmed')
    allowed_links = 2 if require_final and matches(entry(root, final_name), stage_identity) else 1
    if info.st_nlink != allowed_links:
        raise ValueError('Registered temporary file has an unexpected number of links')
    os.unlink(stage, dir_fd=root)
    os.fsync(root)


def remove_manifest(root, manifest, journal_identity):
    """Retire the original journal after recovery is complete."""
    info = entry(root, manifest)
    if not matches(info, journal_identity) or info.st_nlink != 1:
        raise ValueError('Original cleanup manifest has changed')
    os.unlink(manifest, dir_fd=root)


def publish(root, name, data, *, status, finalize, task_id=None):
    """Publish validated bytes exclusively and retire only positively owned files."""
    if task_id is not None and not re.fullmatch(r'[0-9a-f]{32}', task_id):
        raise ValueError('Task ID must contain 32 lowercase hexadecimal digits')
    manifest = '.tl-docx-' + (task_id or uuid4().hex) + '.cleanup.json'
    stage = stage_name(manifest)
    digest = sha256(data).hexdigest()
    journal_id = stage_id = delivery = None
    recorded = False
    result = {'published': False, 'cleanup_complete': False, 'stage_identity_verified': False,
              'deliverable_unchanged': False, 'manifest_removed': False, 'task_manifest': manifest}
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        with os.fdopen(fd, 'wb') as journal:
            journal_id = identity(os.fstat(journal.fileno()))
            os.fchmod(journal.fileno(), 0o600)
            checkpoint(journal, {'schema': SCHEMA, 'root': identity(os.fstat(root)), 'manifest': journal_id,
                                 'output': name, 'sha256': digest, 'status': status})
            os.fsync(root)
            stage_fd = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
            with os.fdopen(stage_fd, 'w+b') as stream:
                stage_id = identity(os.fstat(stream.fileno()))
                os.fchmod(stream.fileno(), 0o600)
                checkpoint(journal, {'stage': stage_id})
                recorded = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                if stream.read(len(data) + 1) != data or not matches(entry(root, stage), stage_id):
                    raise OSError('Staged output could not be verified')
                result['stage_identity_verified'] = True
                checkpoint(journal, {'verified': True})
                os.link(stage, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                result['published'] = True
                os.fsync(root)
                if not matches(entry(root, name), stage_id) or sha256(read_task(root, name)).hexdigest() != digest:
                    raise OSError('Published output changed')
                result['deliverable_unchanged'] = True
                checkpoint(journal, {'published': True})
            delivery = finalize(name)
            checkpoint(journal, {'archive_status': delivery['archive_status'],
                                 'archive_cleanup_unresolved': bool(delivery.get('temporary_residuals'))})
    except (OSError, ValueError) as exc:
        field = 'cleanup_error' if result['deliverable_unchanged'] else 'publication_error'
        result[field] = str(exc)
        if isinstance(exc, FileExistsError) and recorded and not result['published']:
            result['output_collision'] = True
    try:
        if result['published'] and not result['deliverable_unchanged']:
            raise ValueError('Published file is unconfirmed; retained original cleanup evidence')
        if 'cleanup_error' in result:
            raise ValueError('Finalization evidence could not be committed; retained original cleanup manifest')
        clean_stage(root, manifest, stage_id, name, require_final=result['published'])
        if result['published'] and (not matches(entry(root, name), stage_id) or sha256(read_task(root, name)).hexdigest() != digest):
            result['deliverable_unchanged'] = False
            raise ValueError('Published file changed during finalization')
        remove_manifest(root, manifest, journal_id)
        result.update(cleanup_complete=True, manifest_removed=True, task_manifest=None)
    except (OSError, ValueError) as exc:
        result['cleanup_error'] = str(exc)
    return result, delivery


def valid_identity(value):
    """Verify the shape of a journaled device/inode pair."""
    return isinstance(value, list) and len(value) == 2 and all(type(item) is int and item >= 0 for item in value)


def unique_object(pairs):
    """Reject duplicate keys in a serialized cleanup record."""
    result = dict(pairs)
    if len(pairs) != len(result):
        raise ValueError('Duplicate cleanup record key')
    return result


def load_manifest(root, manifest):
    """Load the original, bounded, private cleanup journal and validate its phases."""
    stage_name(manifest)
    info = entry(root, manifest)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError('Original private cleanup manifest is unavailable')
    raw = read_task(root, manifest, 4096)
    if not raw.endswith(b'\n'):
        raise ValueError('Cleanup manifest contains an incomplete record')
    records = [json.loads(line, object_pairs_hook=unique_object) for line in raw.split(b'\n')[:-1]]
    if not 1 <= len(records) <= 5 or not all(isinstance(item, dict) for item in records):
        raise ValueError('Invalid cleanup manifest structure')
    plan = records[0]
    if (set(plan) != {'schema', 'root', 'manifest', 'output', 'sha256', 'status'} or plan['schema'] != SCHEMA
            or not valid_identity(plan['root']) or plan['root'] != identity(os.fstat(root))
            or not valid_identity(plan['manifest']) or plan['manifest'] != identity(info)
            or plan['status'] not in ('verified', 'edited', 'exported')
            or not isinstance(plan['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', plan['sha256'])):
        raise ValueError('Cleanup manifest identity or contract does not match this task')
    output_name(plan['output'], extension=None)
    if len(records) >= 2 and (set(records[1]) != {'stage'} or not valid_identity(records[1]['stage'])):
        raise ValueError('Invalid temporary identity checkpoint')
    if len(records) >= 3 and (set(records[2]) != {'verified'} or records[2]['verified'] is not True):
        raise ValueError('Invalid verification checkpoint')
    if len(records) >= 4 and (set(records[3]) != {'published'} or records[3]['published'] is not True):
        raise ValueError('Invalid publication checkpoint')
    archive = records[4] if len(records) == 5 else {'archive_status': 'unknown', 'archive_cleanup_unresolved': False}
    if len(records) == 5 and (set(archive) != {'archive_status', 'archive_cleanup_unresolved'}
                              or archive['archive_status'] not in ARCHIVE_STATES or type(archive['archive_cleanup_unresolved']) is not bool):
        raise ValueError('Invalid archive checkpoint')
    return plan, records[1]['stage'] if len(records) >= 2 else None, len(records) >= 3, len(records) >= 4, archive


def resume(root, manifest):
    """Resume cleanup from a single original journal without rewriting any document."""
    with task_lock(root, wait=False):
        plan, stage_id, verified, was_published, archive = load_manifest(root, manifest)
        output = plan['output']
        actual = entry(root, output)
        published = verified and matches(actual, stage_id)
        if not published and (actual is not None or was_published):
            raise ValueError('Final path does not match the original publication evidence')
        if published and sha256(read_task(root, output)).hexdigest() != plan['sha256']:
            raise ValueError('Published file content changed; retained original cleanup evidence')
        stage = stage_name(manifest)
        stage_entry = entry(root, stage)
        if stage_entry is not None and (not matches(stage_entry, stage_id) or stage_entry.st_uid != os.getuid()
                                        or stat.S_IMODE(stage_entry.st_mode) != 0o600):
            raise ValueError('Temporary file no longer matches the original ownership evidence')
        if stage_entry is not None and verified and not published and sha256(read_task(root, stage)).hexdigest() != plan['sha256']:
            raise ValueError('Verified stage contents changed; retained original cleanup evidence')
        clean_stage(root, manifest, stage_id, output, require_final=published)
        if published and (not matches(entry(root, output), stage_id) or sha256(read_task(root, output)).hexdigest() != plan['sha256']):
            raise ValueError('Published file changed during cleanup')
        remove_manifest(root, manifest, plan['manifest'])
        unresolved = archive['archive_cleanup_unresolved']
        task_status = 'cleanup_incomplete' if unresolved else 'completed' if published else 'failed_cleaned'
        return {'status': 'recovered', 'task_status': task_status,
                'output': output if published else None, 'sha256': plan['sha256'] if published else None,
                'business_status': plan['status'] if published else 'unpublished',
                'archive_status': archive['archive_status'] if published else 'not_required',
                'cleanup': {'cleanup_complete': not unresolved, 'manifest_removed': True, 'deliverables_unchanged': published,
                            'archive_cleanup_unresolved': unresolved}}
