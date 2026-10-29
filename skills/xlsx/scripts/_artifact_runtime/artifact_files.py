from __future__ import annotations
"""Format-independent task paths and atomic archive copies; no DOCX imports."""
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import tempfile
from .output_location import OutputLocation

class ArchiveCleanupError(RuntimeError):
    """The archive failed and its exact task-owned temporary path remains unresolved."""
    def __init__(self, message, path):
        super().__init__(message)
        self.temporary_residuals = [path]

def _check_delivery_path(task_dir: Path, relative_path: Path, *, directory: bool = False) -> None:
    """Check existing components without following delivery-path symlinks."""
    raw = str(relative_path)
    if (relative_path.is_absolute() or ".." in relative_path.parts
            or PureWindowsPath(raw).drive or "\\" in raw
            or any(ord(char) < 32 or ord(char) == 127 for char in raw)):
        raise ValueError("delivery paths must stay within the task directory")
    current = task_dir
    for index, part in enumerate(relative_path.parts):
        current = current / part
        if current.is_symlink():
            raise ValueError("symbolic links are unsupported in delivery paths")
        if current.exists():
            is_directory = directory or index < len(relative_path.parts) - 1
            if is_directory and not current.is_dir():
                raise ValueError("delivery output directory is blocked by a non-directory")
            if not is_directory and not current.is_file():
                raise ValueError("delivery output must be a regular file")

def _prepare_delivery_directory(task_dir: Path, output_dir: Path) -> None:
    _check_delivery_path(task_dir, output_dir, directory=True)
    (task_dir / output_dir).mkdir(parents=True, exist_ok=True)
    _check_delivery_path(task_dir, output_dir, directory=True)

def _paths_alias(left: Path, right: Path) -> bool:
    """Detect both resolved path aliases and existing hard links."""
    try:
        if left.resolve() == right.resolve():
            return True
        return os.path.samefile(left, right)
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError) as exc:
        raise ValueError("could not safely compare output and input paths") from exc

def _archive_output_path(task_dir: Path, location: OutputLocation, file_name: str,
                         input_path: Path, image_paths: list[Path]) -> Path:
    """Validate the optional archive target after the main output is published."""
    archive_path = location.output_dir / file_name
    _check_delivery_path(task_dir, archive_path)
    if _paths_alias(task_dir / archive_path, input_path):
        raise ValueError("archive output must not refer to the input JSON file")
    if any(_paths_alias(task_dir / archive_path, path) for path in image_paths):
        raise ValueError("archive output must not overwrite an input image")
    _prepare_delivery_directory(task_dir, location.output_dir)
    _check_delivery_path(task_dir, archive_path)
    if _paths_alias(task_dir / archive_path, input_path):
        raise ValueError("archive output must not refer to the input JSON file")
    return archive_path

def _copy_main_to_archive(task_dir: Path, main_output: Path, location: OutputLocation,
                          file_name: str, input_path: Path, image_paths: list[Path]) -> Path:
    """Atomically copy a published main DOCX into the configured archive."""
    archive_output = _archive_output_path(task_dir, location, file_name, input_path, image_paths)
    main_path = task_dir / main_output
    archive_path = task_dir / archive_output
    if archive_path.exists():
        archive_mode = stat.S_IMODE(archive_path.stat().st_mode)
    else:
        archive_mode = stat.S_IMODE(main_path.stat().st_mode)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".docx-", suffix=".tmp", dir=task_dir,
    )
    try:
        with os.fdopen(descriptor, "wb") as archive_file:
            descriptor = -1
            with main_path.open("rb") as main_file:
                shutil.copyfileobj(main_file, archive_file)
            archive_file.flush()
            os.fsync(archive_file.fileno())
        os.chmod(temporary_name, archive_mode)
        os.replace(temporary_name, archive_path)
    except BaseException as exc:
        cleanup_errors = []
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as close_error:
                cleanup_errors.append(f"descriptor close failed: {close_error}")
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            cleanup_errors.append(f"temporary cleanup failed: {cleanup_error}")
        if cleanup_errors:
            temporary_path = Path(temporary_name).relative_to(task_dir).as_posix()
            raise ArchiveCleanupError(f"{exc}; archive cleanup at {temporary_path}: "
                                      + "; ".join(cleanup_errors), temporary_path) from exc
        raise
    return archive_output
