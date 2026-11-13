"""Additive CLI delivery metadata; never change primary success or calculation status."""
from pathlib import Path
from _artifact_runtime.archive_delivery import archive_delivery, archive_user_message, archive_user_notice, validate_input_path, validate_output_path, validate_delivery_output


def validate_cli_paths(arguments):
    """Validate all file arguments of supported write CLIs before reading or writing."""
    inputs = [getattr(arguments, field, None) for field in
              ('input', 'left', 'right', 'spec', 'appearance', 'query', 'daily')]
    inputs = [validate_input_path(Path(value)) for value in inputs if value is not None]
    for field in ('output', 'xlsx'):
        value = getattr(arguments, field, None)
        if value is not None:
            output = validate_delivery_output(value, protected_inputs=inputs)
            if output.exists():
                raise ValueError('Output already exists; choose a new filename')


def finish_delivery(result, output, protected_inputs=()):
    """Archive only the just-written final file; existing inputs are never owned temps."""
    try:
        delivery = archive_delivery([output], [Path(p) for p in protected_inputs if p is not None])
    except Exception as exc:
        delivery = {'archive_contract': 'archive-delivery-v2', 'archive_status': 'failed',
                    'requested_count': 1, 'completed_count': 0, 'failed_count': 1,
                    'reason_code': 'archive_exception', 'files': [], 'warnings': [str(exc)]}
        delivery['archive_message'] = archive_user_message(delivery)
        delivery['archive_user_notice'] = archive_user_notice(delivery)
    result['delivery'] = delivery
    return result
