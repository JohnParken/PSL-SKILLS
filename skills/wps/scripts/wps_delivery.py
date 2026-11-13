"""CLI-only final-file archival; never infer ownership of caller input files."""
from pathlib import Path
import hashlib
from _wps_artifact_runtime.archive_delivery import archive_delivery, archive_user_message, archive_user_notice


def finish_delivery(result, output, protected_inputs=()):
    """Preserve business/partial/exit contracts while adding explicit archive state."""
    blockers = []
    if result.get('status') not in ('success', 'partial'):
        blockers.append('business_result_not_accepted')
    if result.get('output_written') is not True:
        blockers.append('final_output_not_published')
    if result.get('publication_verification') != 'identity_and_bytes_verified':
        blockers.append('publication_not_verified')
    if result.get('cleanup_complete') is not True:
        blockers.append('internal_cleanup_incomplete')
    if blockers:
        # This final-file boundary declares one requested deliverable.
        # Unready delivery is failure, never a no-delivery/read-only task.
        delivery = {'archive_contract': 'archive-delivery-v2', 'archive_status': 'failed',
                    'requested_count': 1, 'completed_count': 0, 'failed_count': 1,
                    'reason_code': 'delivery_not_ready', 'blocking_reasons': blockers,
                    'files': [{'output': str(output), 'archive_output': None, 'archive_status': 'failed',
                               'archive_verified': False, 'reason_code': 'delivery_not_ready'}], 'warnings': []}
        delivery['archive_message'] = archive_user_message(delivery)
        delivery['archive_user_notice'] = archive_user_notice(delivery)
        result['delivery'] = delivery
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
        delivery = archive_delivery([path], protected)
        if hashlib.sha256(read_input(path)).hexdigest() != expected_hash:
            delivery['archive_status'] = 'failed'
            delivery.setdefault('warnings', []).append('Final file changed during delivery; inspect existing copies')
            delivery['archive_message'] = archive_user_message(delivery)
            delivery['archive_user_notice'] = archive_user_notice(delivery)
    except Exception as exc:
        if delivery is None:
            delivery = {'archive_contract': 'archive-delivery-v2', 'archive_status': 'failed',
                        'requested_count': 1, 'completed_count': 0, 'failed_count': 1,
                        'reason_code': 'delivery_verification_failed',
                        'files': [{'output': str(output), 'archive_output': None, 'archive_status': 'failed',
                                   'archive_verified': False, 'reason_code': 'delivery_verification_failed'}], 'warnings': []}
        delivery['archive_status'] = 'failed'
        delivery.setdefault('warnings', []).append(str(exc))
        delivery['archive_message'] = archive_user_message(delivery)
        delivery['archive_user_notice'] = archive_user_notice(delivery)
    if delivery['archive_status'] == 'failed':
        for item in delivery.get('files', []):
            if item.get('archive_status') in ('copied', 'already_archived'):
                item.update(archive_status='failed', archive_verified=False, reason_code='delivery_verification_failed')
        delivery.update(completed_count=0, failed_count=delivery.get('requested_count', 1))
    result['delivery'] = delivery
    return result
