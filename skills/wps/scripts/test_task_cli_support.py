"""Run legacy CLI assertions from their explicitly owned temporary task directory."""
import os
from pathlib import Path
import subprocess
import sys
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
