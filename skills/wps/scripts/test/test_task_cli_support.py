"""Run legacy CLI assertions from their explicitly owned temporary task directory."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import os
import subprocess
from unittest.mock import patch


def task_arguments(arguments):
    paths = [Path(value) for value in arguments if isinstance(value, str) and Path(value).is_absolute()
             and not value.endswith('.py')]
    if not paths:
        return None, arguments
    root = Path(os.path.commonpath([str(path.parent) for path in paths]))
    return root, [str(Path(value).relative_to(root)) if isinstance(value, str) and Path(value) in paths else value for value in arguments]


def run_task_cli(arguments, **kwargs):
    script_index = next((index for index, value in enumerate(arguments)
                         if str(value).endswith(('read_wps.py', 'run_wps_task.py', 'run_wps_edit.py', 'export_wps.py', 'wps_cfb.py'))), None)
    # Legacy CLI fixture contract is explicitly standard; default-mode tests bypass this adapter.
    if script_index is not None and str(arguments[script_index]).endswith('read_wps.py') and '--reading-mode' not in arguments:
        arguments = [*arguments, '--reading-mode', 'standard']
    if script_index is None or kwargs.get('cwd') is not None:
        return subprocess.run(arguments, **kwargs)
    root, task = task_arguments(arguments[script_index + 1:])
    return subprocess.run([*arguments[:script_index + 1], *task], cwd=root, **kwargs)


def run_main(function, arguments=None):
    values = sys.argv[1:] if arguments is None else arguments
    root, task = task_arguments(values)
    previous = Path.cwd()
    try:
        if root is not None:
            os.chdir(root)
        with patch.object(sys, 'argv', [sys.argv[0], *task]):
            return function() if arguments is None else function(task)
    finally:
        os.chdir(previous)


def task_api(function, *args, **kwargs):
    """Run an existing artifact fixture API call with explicit cwd/basename inputs."""
    name = function.__name__
    index = 0 if name == 'save' else 1
    values = list(args)
    output = values[index] if len(values) > index else kwargs.get('output_path')
    source = None if name in ('save', 'create_document') else Path(values[0]).absolute()
    target = Path(output) if output is not None else source.with_suffix('.wps')
    previous = Path.cwd()
    root = target.absolute().parent
    # Invalid-directory fixtures remain invalid instead of creating a parent.
    if not root.is_dir():
        return function(*args, **kwargs)
    if source is not None:
        values[0] = source
    if len(values) > index:
        values[index] = target.name
    elif output is not None:
        kwargs['output_path'] = target.name
    try:
        os.chdir(root)
        return function(*values, **kwargs)
    finally:
        os.chdir(previous)
