"""Additive CLI delivery metadata; never change primary success or calculation status."""
from pathlib import Path
from _artifact_runtime.archive_deliverables import archive_outputs, archive_user_message


def finish_delivery(result, output, protected_inputs=()):
    """Archive only the just-written final file; existing inputs are never owned temps."""
    try:
        delivery = archive_outputs([Path(output)], [Path(p) for p in protected_inputs if p is not None])
    except Exception as exc:
        delivery = {'archive_status': 'failed', 'files': [], 'warnings': [str(exc)]}
        delivery['archive_message'] = archive_user_message(delivery)
    result['delivery'] = delivery
    return result
