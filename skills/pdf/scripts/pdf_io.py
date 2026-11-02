"""Task-directory file access and exclusive PDF extraction exports.

No user input is followed through a symlink. All artifacts are assembled in
memory before writing a new directory; only files owned by this export can be
removed if writing fails. No PDF source is ever opened for writing.
"""

import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath


class PdfTaskError(Exception):
    """An expected failure with a stable machine-readable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class AssetBudgetExceeded(PdfTaskError):
    """An optional image or preview does not fit the output budget."""

    def __init__(self):
        super().__init__("output_budget_exceeded", "图片或预览超过输出字节预算；该资源未导出。")


def relative_parts(value):
    """Validate a portable task-relative path, without filesystem effects."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise PdfTaskError("invalid_path", "文件路径必须是任务目录内的非空相对路径。")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or not path.parts:
        raise PdfTaskError("invalid_path", "禁止绝对数据路径、父目录路径及磁盘前缀。")
    return path.parts


class Workspace:
    """A descriptor-anchored task root; requires the documented Linux APIs."""

    def __init__(self, skill_root):
        self.root = Path.cwd().resolve()
        install_root = Path(skill_root).resolve()
        if self.root == install_root or install_root in self.root.parents:
            raise PdfTaskError("invalid_workspace", "当前目录是 Skill 安装目录；请由宿主确认独立任务目录。")
        try:
            self.install_parts = install_root.relative_to(self.root).parts
        except ValueError:
            self.install_parts = None
        needed = (os.open, os.mkdir, os.stat, os.unlink, os.rmdir)
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or any(func not in os.supports_dir_fd for func in needed):
            raise PdfTaskError("unsupported_platform", "当前平台缺少目录描述符及不跟随链接能力；停止文件操作。")
        self.dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.fd = os.open(".", self.dir_flags)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _data_parts(self, name):
        parts = relative_parts(name)
        if self.install_parts is not None and parts[:len(self.install_parts)] == self.install_parts:
            raise PdfTaskError("invalid_data_path", "任务数据路径进入了 Skill 安装目录；请在安装目录外使用输入和输出。")
        return parts

    def _parent(self, parts):
        current = os.dup(self.fd)
        try:
            for name in parts[:-1]:
                next_fd = os.open(name, self.dir_flags, dir_fd=current)
                os.close(current)
                current = next_fd
            return current
        except OSError as exc:
            os.close(current)
            raise PdfTaskError("unsafe_or_missing_path", "路径不存在、不可访问或包含符号链接。") from exc

    def read_bytes(self, name, limit):
        parts = self._data_parts(name)
        parent = self._parent(parts)
        try:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except OSError as exc:
                raise PdfTaskError("input_unavailable", "输入文件不存在、不可读取或是符号链接。") from exc
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise PdfTaskError("invalid_input_type", "输入必须是普通文件。")
                if info.st_size > limit:
                    raise PdfTaskError("input_budget_exceeded", "输入文件超过允许的字节预算。")
                data = stream.read(limit + 1)
                if len(data) > limit:
                    raise PdfTaskError("input_budget_exceeded", "输入文件超过允许的字节预算。")
                after = os.fstat(stream.fileno())
                if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise PdfTaskError("input_changed", "读取期间输入文件发生变化，请重新确认输入。")
                return data
        finally:
            os.close(parent)

    def check_pdf_input(self, name, limit):
        data = self.read_bytes(name, limit)
        if not data or b"%PDF-" not in data[:1024]:
            raise PdfTaskError("not_pdf", "输入不含有效 PDF 文件头。")
        return data

    def write_probe(self):
        import secrets

        name = "pdf_probe_" + secrets.token_hex(12)
        marker = b"pdf-task-write-check\n"
        fd = None
        identity = None
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            if os.write(fd, marker) != len(marker):
                raise PdfTaskError("write_check_failed", "写入探针未完整写入。")
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(marker) + 1) != marker:
                raise PdfTaskError("write_check_failed", "写入探针回读不一致。")
        finally:
            if fd is not None:
                os.close(fd)
            if identity is not None:
                current = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != identity:
                    raise PdfTaskError("cleanup_incomplete", "探针归属发生变化，未删除未知文件。")
                os.unlink(name, dir_fd=self.fd)

    def export(self, directory, files):
        """Write a new, exclusively owned output directory. Never overwrite."""
        parts = self._data_parts(directory)
        parent = self._parent(parts)
        output_fd = None
        root_identity = None
        root_created = False
        directory_fds = {}
        created_dirs = []
        created_files = []
        try:
            try:
                os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
            except FileExistsError as exc:
                raise PdfTaskError("output_exists", "输出目录已存在；请选择新的目录，禁止覆盖。") from exc
            root_created = True
            root_info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            root_identity = (root_info.st_dev, root_info.st_ino)
            output_fd = os.open(parts[-1], self.dir_flags, dir_fd=parent)
            opened_info = os.fstat(output_fd)
            if (opened_info.st_dev, opened_info.st_ino) != root_identity:
                raise PdfTaskError("output_changed", "导出目录打开时归属发生变化。")
            directory_fds = {(): output_fd}
            for name, data in files.items():
                child_parts = relative_parts(name)
                for index in range(1, len(child_parts)):
                    key = child_parts[:index]
                    if key not in directory_fds:
                        parent_fd = directory_fds[key[:-1]]
                        os.mkdir(key[-1], mode=0o700, dir_fd=parent_fd)
                        info = os.stat(key[-1], dir_fd=parent_fd, follow_symlinks=False)
                        created_dirs.append((parent_fd, key[-1], info.st_dev, info.st_ino))
                        child_fd = os.open(key[-1], self.dir_flags, dir_fd=parent_fd)
                        directory_fds[key] = child_fd
                        opened_child = os.fstat(child_fd)
                        if (opened_child.st_dev, opened_child.st_ino) != (info.st_dev, info.st_ino):
                            raise PdfTaskError("output_changed", "导出子目录归属发生变化。")
                file_parent = directory_fds[child_parts[:-1]]
                fd = os.open(child_parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=file_parent)
                with os.fdopen(fd, "wb") as stream:
                    info = os.fstat(stream.fileno())
                    created_files.append((file_parent, child_parts[-1], info.st_dev, info.st_ino))
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                check_fd = os.open(child_parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=file_parent)
                with os.fdopen(check_fd, "rb") as stream:
                    if stream.read() != data:
                        raise PdfTaskError("export_verification_failed", "交付文件回读不一致。")
            final_info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if (final_info.st_dev, final_info.st_ino) != root_identity:
                raise PdfTaskError("output_changed", "导出过程中输出目录归属变化。")
        except Exception as exc:
            if root_created:
                cleanup_ok = self._rollback(created_files, created_dirs, parent, parts[-1], root_identity)
                if not cleanup_ok:
                    raise PdfTaskError("cleanup_incomplete", "导出失败且部分本轮文件未能清理；保留现场并报告，禁止扫描删除。") from exc
            raise
        finally:
            for key, dir_fd in directory_fds.items():
                if key:
                    os.close(dir_fd)
            if output_fd is not None:
                os.close(output_fd)
            os.close(parent)

    @staticmethod
    def _rollback(created_files, created_dirs, parent, name, root_identity):
        cleanup_ok = True
        for entries, remove in ((created_files, os.unlink), (created_dirs, os.rmdir)):
            for dir_fd, child_name, device, inode in reversed(entries):
                try:
                    current = os.stat(child_name, dir_fd=dir_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != (device, inode):
                        cleanup_ok = False
                        continue
                    remove(child_name, dir_fd=dir_fd)
                except OSError:
                    cleanup_ok = False
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != root_identity:
                cleanup_ok = False
            else:
                os.rmdir(name, dir_fd=parent)
        except OSError:
            cleanup_ok = False
        return cleanup_ok


class AssetCollector:
    """An in-memory sink, separate from filesystem delivery."""

    def __init__(self, limit):
        self.limit = limit
        self.size = 0
        self.files = {}

    def __call__(self, name, data):
        parts = relative_parts(name)
        if parts[0] not in ("images", "previews") or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", item) for item in parts):
            raise PdfTaskError("invalid_asset_path", "引擎返回了不支持的资源名称。")
        if not isinstance(data, bytes):
            raise PdfTaskError("invalid_asset", "资源必须是二进制字节。")
        if name in self.files:
            if self.files[name] != data:
                raise PdfTaskError("asset_conflict", "资源名称重复且内容不同。")
            return name
        if self.size + len(data) > self.limit:
            raise AssetBudgetExceeded()
        self.files[name] = data
        self.size += len(data)
        return name
