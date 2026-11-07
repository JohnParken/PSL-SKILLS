"""CLI-only final-file archival; never infer ownership of caller input files."""
from pathlib import Path
import hashlib
from _wps_artifact_runtime.archive_deliverables import archive_outputs, archive_user_message, archive_user_notice


def finish_delivery(result, output, protected_inputs=()):
    """Preserve business/partial/exit contracts while adding explicit archive state."""
    if (result.get('status') not in ('success', 'partial') or not result.get('output_written')
            or result.get('publication_verification') != 'identity_and_bytes_verified' or result.get('cleanup_complete') is not True):
        result['delivery'] = {'archive_status': 'not_attempted', 'archive_message': '归档未执行',
                              'archive_user_notice': archive_user_notice({'archive_status': 'not_attempted'}),
                              'reason': 'business_publication_or_internal_cleanup_not_accepted', 'files': [], 'warnings': []}
        return result
    delivery = None
    try:
        task = Path.cwd()
        path = Path(output)
        path = path.relative_to(task) if path.is_absolute() else path
        from wps_access import read_input
        expected_hash = result.get('published_sha256')
        if not expected_hash or hashlib.sha256(read_input(path)).hexdigest() != expected_hash:
            raise ValueError('Final file changed since publication verification; archive not attempted')
        protected = []
        for value in protected_inputs:
            if value is not None:
                item = Path(value)
                protected.append(item.relative_to(task) if item.is_absolute() else item)
        delivery = archive_outputs([path], protected)
        if hashlib.sha256(read_input(path)).hexdigest() != expected_hash:
            delivery['archive_status'] = 'failed'
            delivery.setdefault('warnings', []).append('Final file changed during delivery; inspect existing copies')
            delivery['archive_message'] = archive_user_message(delivery)
            delivery['archive_user_notice'] = archive_user_notice(delivery)
    except Exception as exc:
        if delivery is None:
            delivery = {'archive_status': 'failed', 'files': [], 'warnings': []}
        delivery['archive_status'] = 'failed'
        delivery.setdefault('warnings', []).append(str(exc))
        delivery['archive_message'] = archive_user_message(delivery)
        delivery['archive_user_notice'] = archive_user_notice(delivery)
    result['delivery'] = delivery
    return result
