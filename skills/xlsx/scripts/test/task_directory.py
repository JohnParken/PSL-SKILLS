"""Isolated fixture task cwd for tests of filename-only public write APIs."""
import os
import tempfile
from pathlib import Path

class TaskDirectory:
    def __init__(self, *args, **kwargs):
        self.previous = Path.cwd()
        self.directory = tempfile.TemporaryDirectory(*args, **kwargs)
        os.chdir(self.directory.name)
        self.name = '.'
        self.closed = False

    def __enter__(self):
        return self.name

    def cleanup(self):
        if not self.closed:
            os.chdir(self.previous)
            self.directory.cleanup()
            self.closed = True

    def __exit__(self, *args):
        self.cleanup()
