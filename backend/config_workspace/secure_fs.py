from __future__ import annotations

import contextlib
import ctypes
import json
import os
import stat
import uuid
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any


class SecureFilesystemError(RuntimeError):
    pass


def _is_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(flag and attributes & flag)


def _identity(path: Path) -> int:
    return int(os.lstat(path).st_ino)


class _WindowsWriter:
    def __init__(self, handle: int) -> None:
        self._handle = handle

    def write(self, content: bytes) -> int:
        payload = bytes(content)
        _win_write_all(self._handle, payload)
        return len(payload)

    def flush(self) -> None:
        if not _FlushFileBuffers(self._handle):
            _raise_last_windows_error()


class SecureRootFilesystem:
    """Handle-anchored file operations below one trusted, physical root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        if _is_reparse(self.root) or not self.root.is_dir():
            raise SecureFilesystemError("trusted root must be a physical directory")

    def _before_handle_use(self, operation: str, path: Path) -> None:
        """Internal checkpoint used by race regression tests."""

    def ensure_directory(self, path: Path) -> None:
        clean = self._clean_path(path)
        relative = self._relative(clean)
        current = self.root
        for part in relative.parts:
            child = current / part
            if child.exists() or _is_reparse(child):
                self._validate_static_component(child, directory=True)
                current = child
                continue
            if os.name == "nt":
                snapshot = self._snapshot(current, include_leaf=True)
                self._before_handle_use("mkdir", child)
                with self._windows_parent_guards(child, snapshot):
                    if not _CreateDirectoryW(str(child), None):
                        error = ctypes.get_last_error()
                        if error != _ERROR_ALREADY_EXISTS:
                            raise SecureFilesystemError("secure directory creation failed")
            else:
                snapshot = self._snapshot(current, include_leaf=True)
                self._before_handle_use("mkdir", child)
                with self._posix_parent_fd(child, snapshot) as parent_fd:
                    try:
                        os.mkdir(child.name, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
            self._validate_static_component(child, directory=True)
            current = child

    @contextlib.contextmanager
    def create_exclusive(self, path: Path) -> Iterator[Any]:
        clean = self._clean_path(path)
        snapshot = self._snapshot(clean, include_leaf=False)
        self._before_handle_use("create", clean)
        if os.name == "nt":
            with self._windows_parent_guards(clean, snapshot) as guards:
                handle = _win_create_file(
                    clean,
                    _GENERIC_WRITE | _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                    _CREATE_NEW,
                    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                )
                try:
                    self._validate_windows_handle(
                        handle,
                        clean,
                        guards.root_final,
                        expected_identity=None,
                        directory=False,
                    )
                    writer = _WindowsWriter(handle)
                    yield writer
                    writer.flush()
                finally:
                    _CloseHandle(handle)
            return
        with self._posix_parent_fd(clean, snapshot) as parent_fd:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(clean.name, flags, 0o600, dir_fd=parent_fd)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                yield stream
                stream.flush()
                os.fsync(stream.fileno())

    def read_bytes(self, path: Path) -> bytes:
        clean = self._clean_path(path)
        snapshot = self._snapshot(clean, include_leaf=True)
        self._before_handle_use("read", clean)
        if os.name == "nt":
            with self._windows_parent_guards(clean, snapshot) as guards:
                expected = snapshot.get(self._key(clean))
                handle = _win_create_file(
                    clean,
                    _GENERIC_READ | _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                    _OPEN_EXISTING,
                    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                )
                try:
                    self._validate_windows_handle(
                        handle,
                        clean,
                        guards.root_final,
                        expected_identity=expected,
                        directory=False,
                    )
                    return _win_read_all(handle)
                finally:
                    _CloseHandle(handle)
        with self._posix_parent_fd(clean, snapshot) as parent_fd:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(clean.name, flags, dir_fd=parent_fd)
            try:
                expected = snapshot.get(self._key(clean))
                if expected is None or int(os.fstat(fd).st_ino) != expected:
                    raise SecureFilesystemError("file identity changed")
                chunks: list[bytes] = []
                while chunk := os.read(fd, 1024 * 1024):
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                os.close(fd)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        try:
            return self.read_bytes(path).decode(encoding)
        except UnicodeError:
            raise SecureFilesystemError("secure text decode failed") from None

    def atomic_write_bytes(self, path: Path, content: bytes) -> None:
        clean = self._clean_path(path)
        snapshot = self._snapshot(clean, include_leaf=True, allow_missing_leaf=True)
        self._before_handle_use("atomic_write", clean)
        if os.name == "nt":
            with self._windows_parent_guards(clean, snapshot) as guards:
                self._reject_windows_reparse_leaf(clean, guards.root_final)
                temporary = clean.parent / f".{clean.name}.{uuid.uuid4().hex}.tmp"
                handle = _win_create_file(
                    temporary,
                    _GENERIC_WRITE | _DELETE | _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                    _CREATE_NEW,
                    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                )
                renamed = False
                try:
                    self._validate_windows_handle(
                        handle,
                        temporary,
                        guards.root_final,
                        expected_identity=None,
                        directory=False,
                    )
                    _win_write_all(handle, bytes(content))
                    if not _FlushFileBuffers(handle):
                        _raise_last_windows_error()
                    _win_rename_handle(handle, clean)
                    renamed = True
                finally:
                    if not renamed:
                        _win_delete_handle(handle)
                    _CloseHandle(handle)
            return
        with self._posix_parent_fd(clean, snapshot) as parent_fd:
            temporary_name = f".{clean.name}.{uuid.uuid4().hex}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
            try:
                payload = memoryview(bytes(content))
                while payload:
                    written = os.write(fd, payload)
                    payload = payload[written:]
                os.fsync(fd)
                os.replace(
                    temporary_name,
                    clean.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            finally:
                os.close(fd)
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass

    def write_json_atomic(self, path: Path, payload: Mapping[str, Any]) -> None:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self.atomic_write_bytes(path, serialized.encode("utf-8"))

    def replace(self, source: Path, destination: Path) -> None:
        clean_source = self._clean_path(source)
        clean_destination = self._clean_path(destination)
        source_snapshot = self._snapshot(clean_source, include_leaf=True)
        destination_snapshot = self._snapshot(
            clean_destination,
            include_leaf=True,
            allow_missing_leaf=True,
        )
        self._before_handle_use("replace", clean_source)
        if os.name == "nt":
            with contextlib.ExitStack() as stack:
                source_guards = stack.enter_context(
                    self._windows_parent_guards(clean_source, source_snapshot)
                )
                destination_guards = stack.enter_context(
                    self._windows_parent_guards(clean_destination, destination_snapshot)
                )
                if source_guards.root_final != destination_guards.root_final:
                    raise SecureFilesystemError("replace roots differ")
                self._reject_windows_reparse_leaf(
                    clean_destination,
                    destination_guards.root_final,
                )
                handle = _win_create_file(
                    clean_source,
                    _DELETE | _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                    _OPEN_EXISTING,
                    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                )
                try:
                    self._validate_windows_handle(
                        handle,
                        clean_source,
                        source_guards.root_final,
                        expected_identity=source_snapshot.get(self._key(clean_source)),
                        directory=False,
                    )
                    _win_rename_handle(handle, clean_destination)
                finally:
                    _CloseHandle(handle)
            return
        with contextlib.ExitStack() as stack:
            source_fd = stack.enter_context(
                self._posix_parent_fd(clean_source, source_snapshot)
            )
            destination_fd = stack.enter_context(
                self._posix_parent_fd(clean_destination, destination_snapshot)
            )
            os.replace(
                clean_source.name,
                clean_destination.name,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )

    def unlink_many(self, paths: Iterable[Path]) -> None:
        clean_paths = tuple(dict.fromkeys(self._clean_path(path) for path in paths))
        if not clean_paths:
            return
        snapshots = {
            path: self._snapshot(path, include_leaf=True, allow_missing_leaf=True)
            for path in clean_paths
        }
        self._before_handle_use("unlink_many", clean_paths[0])
        if os.name == "nt":
            opened: list[int] = []
            with contextlib.ExitStack() as stack:
                root_final: str | None = None
                try:
                    for path in clean_paths:
                        guards = stack.enter_context(
                            self._windows_parent_guards(path, snapshots[path])
                        )
                        if root_final is None:
                            root_final = guards.root_final
                        elif guards.root_final != root_final:
                            raise SecureFilesystemError("unlink roots differ")
                        handle = _win_try_open_delete(path)
                        if handle is None:
                            continue
                        try:
                            self._validate_windows_handle(
                                handle,
                                path,
                                guards.root_final,
                                expected_identity=snapshots[path].get(self._key(path)),
                                directory=False,
                            )
                        except Exception:
                            _CloseHandle(handle)
                            raise
                        opened.append(handle)
                    for handle in opened:
                        _win_delete_handle(handle)
                finally:
                    for handle in opened:
                        _CloseHandle(handle)
            return
        with contextlib.ExitStack() as stack:
            opened: list[tuple[int, str, int | None]] = []
            for path in clean_paths:
                parent_fd = stack.enter_context(
                    self._posix_parent_fd(path, snapshots[path])
                )
                try:
                    metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    raise SecureFilesystemError("refusing to unlink a link")
                opened.append(
                    (parent_fd, path.name, snapshots[path].get(self._key(path)))
                )
            for parent_fd, name, expected in opened:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if expected is not None and int(current.st_ino) != expected:
                    raise SecureFilesystemError("file identity changed")
                os.unlink(name, dir_fd=parent_fd)

    def _clean_path(self, path: Path) -> Path:
        raw = str(path)
        if os.name == "nt" and raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif os.name == "nt" and raw.startswith("\\\\?\\"):
            raw = raw[4:]
        candidate = Path(os.path.abspath(raw))
        self._relative(candidate)
        return candidate

    def _relative(self, path: Path) -> Path:
        try:
            return path.relative_to(self.root)
        except ValueError:
            raise SecureFilesystemError("path escapes trusted root") from None

    def _key(self, path: Path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def _snapshot(
        self,
        path: Path,
        *,
        include_leaf: bool,
        allow_missing_leaf: bool = False,
    ) -> dict[str, int]:
        relative = self._relative(path)
        parts = relative.parts if include_leaf else relative.parts[:-1]
        current = self.root
        snapshot = {self._key(self.root): _identity(self.root)}
        for index, part in enumerate(parts):
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                if allow_missing_leaf and index == len(parts) - 1:
                    break
                raise SecureFilesystemError("controlled path component is missing") from None
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(current):
                raise SecureFilesystemError("controlled path contains a reparse point")
            if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise SecureFilesystemError("controlled ancestor is not a directory")
            snapshot[self._key(current)] = int(metadata.st_ino)
        return snapshot

    def _validate_static_component(self, path: Path, *, directory: bool) -> None:
        self._relative(Path(os.path.abspath(str(path))))
        if _is_reparse(path):
            raise SecureFilesystemError("controlled path contains a reparse point")
        if directory and not path.is_dir():
            raise SecureFilesystemError("controlled component is not a directory")

    @contextlib.contextmanager
    def _windows_parent_guards(
        self,
        path: Path,
        snapshot: dict[str, int],
    ) -> Iterator[_WindowsGuards]:
        handles: list[int] = []
        root_final: str | None = None
        current = self.root
        directories = [self.root]
        for part in self._relative(path).parts[:-1]:
            current = current / part
            directories.append(current)
        try:
            for directory in directories:
                handle = _win_create_file(
                    directory,
                    _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                    _OPEN_EXISTING,
                    _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
                )
                handles.append(handle)
                final = _win_final_path(handle)
                if root_final is None:
                    root_final = final
                self._validate_windows_handle(
                    handle,
                    directory,
                    root_final,
                    expected_identity=snapshot.get(self._key(directory)),
                    directory=True,
                )
            assert root_final is not None
            yield _WindowsGuards(handles=handles, root_final=root_final)
        finally:
            for handle in reversed(handles):
                _CloseHandle(handle)

    def _validate_windows_handle(
        self,
        handle: int,
        path: Path,
        root_final: str,
        *,
        expected_identity: int | None,
        directory: bool,
    ) -> None:
        information = _win_information(handle)
        if information.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise SecureFilesystemError("reparse handle rejected")
        is_directory = bool(information.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != directory:
            raise SecureFilesystemError("handle type changed")
        identity = (int(information.nFileIndexHigh) << 32) | int(
            information.nFileIndexLow
        )
        if expected_identity is not None and identity != expected_identity:
            raise SecureFilesystemError("handle identity changed")
        final = _win_final_path(handle)
        if not _windows_is_relative_to(final, root_final):
            raise SecureFilesystemError("handle escaped trusted root")

    def _reject_windows_reparse_leaf(self, path: Path, root_final: str) -> None:
        handle = _win_try_open_attributes(path)
        if handle is None:
            return
        try:
            self._validate_windows_handle(
                handle,
                path,
                root_final,
                expected_identity=None,
                directory=False,
            )
        finally:
            _CloseHandle(handle)

    @contextlib.contextmanager
    def _posix_parent_fd(
        self,
        path: Path,
        snapshot: dict[str, int],
    ) -> Iterator[int]:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fds: list[int] = []
        try:
            current_fd = os.open(self.root, flags)
            fds.append(current_fd)
            if int(os.fstat(current_fd).st_ino) != snapshot.get(self._key(self.root)):
                raise SecureFilesystemError("root identity changed")
            current = self.root
            for part in self._relative(path).parts[:-1]:
                current = current / part
                next_fd = os.open(part, flags, dir_fd=current_fd)
                fds.append(next_fd)
                current_fd = next_fd
                expected = snapshot.get(self._key(current))
                if expected is None or int(os.fstat(current_fd).st_ino) != expected:
                    raise SecureFilesystemError("directory identity changed")
            yield current_fd
        finally:
            for fd in reversed(fds):
                os.close(fd)


class _WindowsGuards:
    def __init__(self, *, handles: list[int], root_final: str) -> None:
        self.handles = handles
        self.root_final = root_final


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CreateDirectoryW = _kernel32.CreateDirectoryW
    _CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _CreateDirectoryW.restype = wintypes.BOOL
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _ReadFile = _kernel32.ReadFile
    _ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _ReadFile.restype = wintypes.BOOL
    _WriteFile = _kernel32.WriteFile
    _WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _WriteFile.restype = wintypes.BOOL
    _FlushFileBuffers = _kernel32.FlushFileBuffers
    _FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _FlushFileBuffers.restype = wintypes.BOOL
    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
    _SetFileInformationByHandle = _kernel32.SetFileInformationByHandle

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTimeLow", wintypes.DWORD),
            ("ftCreationTimeHigh", wintypes.DWORD),
            ("ftLastAccessTimeLow", wintypes.DWORD),
            ("ftLastAccessTimeHigh", wintypes.DWORD),
            ("ftLastWriteTimeLow", wintypes.DWORD),
            ("ftLastWriteTimeHigh", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FileRenameInfoHeader(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    _GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _SetFileInformationByHandle.restype = wintypes.BOOL

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_PATH_NOT_FOUND = 3
    _ERROR_ALREADY_EXISTS = 183
    _FILE_RENAME_INFO_CLASS = 3
    _FILE_DISPOSITION_INFO_CLASS = 4


def _raise_last_windows_error() -> None:
    raise SecureFilesystemError(f"secure Windows operation failed ({ctypes.get_last_error()})")


def _win_create_file(
    path: Path,
    access: int,
    sharing: int,
    creation: int,
    flags: int,
) -> int:
    handle = _CreateFileW(str(path), access, sharing, None, creation, flags, None)
    if handle == _INVALID_HANDLE_VALUE:
        _raise_last_windows_error()
    return int(handle)


def _win_try_open_attributes(path: Path) -> int | None:
    handle = _CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        if ctypes.get_last_error() in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return None
        _raise_last_windows_error()
    return int(handle)


def _win_try_open_delete(path: Path) -> int | None:
    handle = _CreateFileW(
        str(path),
        _DELETE | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        if ctypes.get_last_error() in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return None
        _raise_last_windows_error()
    return int(handle)


def _win_information(handle: int) -> Any:
    information = _ByHandleFileInformation()
    if not _GetFileInformationByHandle(handle, ctypes.byref(information)):
        _raise_last_windows_error()
    return information


def _win_final_path(handle: int) -> str:
    size = _GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not size:
        _raise_last_windows_error()
    buffer = ctypes.create_unicode_buffer(size + 1)
    if not _GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
        _raise_last_windows_error()
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _windows_is_relative_to(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _win_read_all(handle: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        if not _ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
            _raise_last_windows_error()
        if read.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: read.value])


def _win_write_all(handle: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        chunk = bytes(view[: 1024 * 1024])
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not _WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            _raise_last_windows_error()
        if written.value <= 0:
            raise SecureFilesystemError("secure Windows write made no progress")
        view = view[written.value :]


def _win_rename_handle(handle: int, destination: Path) -> None:
    encoded = str(destination).encode("utf-16-le")
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    filename_offset = pointer_size * 2 + ctypes.sizeof(ctypes.c_uint32)
    size = ctypes.sizeof(_FileRenameInfoHeader) + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    ctypes.c_ubyte.from_buffer(buffer, 0).value = 1
    ctypes.c_void_p.from_buffer(buffer, pointer_size).value = None
    ctypes.c_uint32.from_buffer(buffer, pointer_size * 2).value = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + filename_offset, encoded, len(encoded))
    if not _SetFileInformationByHandle(
        handle,
        _FILE_RENAME_INFO_CLASS,
        buffer,
        size,
    ):
        _raise_last_windows_error()


def _win_delete_handle(handle: int) -> None:
    delete = ctypes.c_ubyte(1)
    if not _SetFileInformationByHandle(
        handle,
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(delete),
        ctypes.sizeof(delete),
    ):
        _raise_last_windows_error()


__all__ = ["SecureFilesystemError", "SecureRootFilesystem"]
