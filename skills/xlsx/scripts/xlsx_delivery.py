"""Additive CLI delivery metadata; never change primary success or calculation status."""
from pathlib import Path
from _artifact_runtime.archive_deliverables import archive_outputs, archive_user_message, validate_input_path, validate_output_path


def validate_cli_paths(arguments):
    """Validate all file arguments of supported write CLIs before reading or writing."""
    inputs = [getattr(arguments, field, None) for field in
              ('input', 'left', 'right', 'spec', 'appearance', 'query', 'daily')]
    inputs = [validate_input_path(Path(value)) for value in inputs if value is not None]
    for field in ('output', 'xlsx'):
        value = getattr(arguments, field, None)
        if value is not None:
            output = validate_output_path(Path(value), protected_inputs=inputs)
            if output.exists():
                raise ValueError('Output already exists; choose a new filename')


def finish_delivery(result, output, protected_inputs=()):
    """Archive only the just-written final file; existing inputs are never owned temps."""
    try:
        delivery = archive_outputs([Path(output)], [Path(p) for p in protected_inputs if p is not None])
    except Exception as exc:
        delivery = {'archive_status': 'failed', 'files': [], 'warnings': [str(exc)]}
        delivery['archive_message'] = archive_user_message(delivery)
    result['delivery'] = delivery
    return result
