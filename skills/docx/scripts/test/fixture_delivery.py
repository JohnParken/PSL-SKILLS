"""Create synthetic layout fixtures in their own explicit task working directory."""
import os
from pathlib import Path


def create_fixture(callback, spec, output, base_dir=None, **kwargs):
    destination = Path(output).absolute()
    image_base = Path(base_dir).absolute() if base_dir is not None else Path.cwd()
    previous = Path.cwd()
    try:
        os.chdir(destination.parent)
        callback(spec, destination.name, base_dir=image_base, **kwargs)
    finally:
        os.chdir(previous)
    return destination
