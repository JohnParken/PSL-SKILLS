"""Task-local lifecycle for exclusive private directories owned by one producer.

Registration is in-memory, before creation. The private tree is one aggregate:
only trusted producer code may write inside it. This is not a process sandbox,
crash recovery, or a guarantee against another writer running as the same user.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import secrets
import stat

from tl_guard import ContractError

_ACTIVE = ContextVar("tl_workspaces", default=None)
_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def check_task_root():
    """The host's existing ./ is the only task root, never the installation."""
    root = Path.cwd().resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ContractError("The task directory cannot be inside the skill installation")
    return root


@contextmanager
def lifecycle():
    """Collect only reservations made by this invocation; do not search ./."""
    journal = []
    token = _ACTIVE.set(journal)
    try:
        yield journal
    finally:
        _ACTIVE.reset(token)


def receipt(journal):
    """Report uncertainty explicitly and keep OS paths out of the receipt."""
    retained = [entry["name"] for entry in journal if entry["state"] == "retained"]
    return {"status": "incomplete" if retained else "completed",
            "cleanup_complete": not retained, "retained": retained,
            "planned_workspaces": len(journal)}


def _same(parent, name, expected):
    """Use a pinned handle identity, never a name alone."""
    return os.path.samestat(os.stat(name, dir_fd=parent, follow_symlinks=False), expected)


def _empty(fd, live, device):
    """Delete only inside the pinned private tree, without following links."""
    for name in os.listdir(fd):
        live()
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_dev != device:
            raise OSError("Unexpected mounted filesystem; retain workspace")
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIR, dir_fd=fd)
            try:
                def child_live():
                    live()
                    if not _same(fd, name, os.fstat(child)):
                        raise OSError("Private subdirectory changed")
                if not os.path.samestat(info, os.fstat(child)):
                    raise OSError("Private subdirectory changed")
                _empty(child, child_live, device)
                child_live()
                os.rmdir(name, dir_fd=fd)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            live()
            if not _same(fd, name, info):
                raise OSError("Private file changed")
            os.unlink(name, dir_fd=fd)
        else:
            raise OSError("Unexpected link or special entry; retain workspace")


@contextmanager
def private_workspace(*, prefix):
    """Plan, exclusively create, pin, and retire a private task-root aggregate."""
    check_task_root()
    if not re.fullmatch(r"tl-[a-z0-9-]+-", prefix):
        raise ContractError("Invalid private workspace prefix")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW") or os.listdir not in os.supports_fd:
        raise ContractError("Descriptor-based workspace cleanup unavailable")
    journal = _ACTIVE.get()
    name = prefix + secrets.token_hex(16)
    entry = {"name": name, "state": "planned"}
    if journal is not None:
        journal.append(entry)
    root = os.open(".", _DIR)
    child = None
    try:
        os.mkdir(name, 0o700, dir_fd=root)
        entry["state"] = "retained"
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        child = os.open(name, _DIR, dir_fd=root)
        def live():
            if not os.path.samestat(info, os.fstat(child)) or not _same(root, name, info):
                raise OSError("Private workspace identity changed")
        live()
        try:
            yield Path(name)
        finally:
            try:
                live()
                _empty(child, live, info.st_dev)
                live()
                os.rmdir(name, dir_fd=root)
                entry["state"] = "removed"
            except (OSError, RecursionError):
                if journal is None:
                    raise ContractError("Private workspace cleanup incomplete") from None
    finally:
        if child is not None:
            os.close(child)
        os.close(root)
