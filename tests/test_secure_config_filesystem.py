from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

import backend.config_workspace.secure_fs as secure_fs_module
from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureFilesystemUnsupportedError,
    SecureRootFilesystem,
)


def test_secure_filesystem_reads_atomically_replaces_and_exactly_unlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    parent = root / "config_sources" / "session-7"
    parent.mkdir(parents=True)
    path = parent / "value.bin"
    sentinel = parent / "sentinel.bin"
    sentinel.write_bytes(b"keep")
    filesystem = SecureRootFilesystem(root)

    filesystem.atomic_write_bytes(path, b"first")
    assert filesystem.read_bytes(path) == b"first"
    filesystem.atomic_write_bytes(path, b"second")
    assert filesystem.read_bytes(path) == b"second"
    filesystem.unlink_many([path])

    assert not path.exists()
    assert sentinel.read_bytes() == b"keep"


def test_secure_filesystem_replaces_created_upload_by_handle(tmp_path: Path) -> None:
    root = tmp_path / "root"
    parent = root / "config_sources" / "session-7"
    parent.mkdir(parents=True)
    temporary = parent / ".upload.tmp"
    destination = parent / "source.pdf"
    filesystem = SecureRootFilesystem(root)

    with filesystem.create_exclusive(temporary) as stream:
        stream.write(b"synthetic-pdf")
    filesystem.replace(temporary, destination)

    assert filesystem.read_bytes(destination) == b"synthetic-pdf"
    assert not temporary.exists()


def test_secure_filesystem_safely_creates_a_missing_physical_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing-parent" / "missing-root"

    filesystem = SecureRootFilesystem(root)
    filesystem.atomic_write_bytes(root / "value.bin", b"created")

    assert filesystem.read_bytes(root / "value.bin") == b"created"


def test_secure_filesystem_rejects_parent_identity_change_before_handle_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    parent = root / "config_sources" / "session-7"
    replacement = root / "replacement-session"
    parked = root / "parked-session"
    parent.mkdir(parents=True)
    replacement.mkdir(parents=True)
    path = parent / "value.bin"
    path.write_bytes(b"original")
    replacement_value = replacement / "value.bin"
    replacement_value.write_bytes(b"replacement")
    filesystem = SecureRootFilesystem(root)
    swapped = False

    def swap_parent(_operation: str, _path: Path) -> None:
        nonlocal swapped
        if swapped:
            return
        parent.rename(parked)
        replacement.rename(parent)
        swapped = True

    monkeypatch.setattr(filesystem, "_before_handle_use", swap_parent)

    with pytest.raises(SecureFilesystemError):
        filesystem.read_bytes(path)

    assert (parent / "value.bin").read_bytes() == b"replacement"
    assert (parked / "value.bin").read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction no-follow regression")
def test_secure_filesystem_rejects_in_root_junction_for_read_and_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    target = root / "target"
    junction = root / "junction"
    target.mkdir(parents=True)
    target_file = target / "value.bin"
    target_file.write_bytes(b"keep")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    filesystem = SecureRootFilesystem(root)
    try:
        with pytest.raises(SecureFilesystemError):
            filesystem.read_bytes(junction / "value.bin")
        with pytest.raises(SecureFilesystemError):
            filesystem.atomic_write_bytes(junction / "value.bin", b"changed")
    finally:
        os.rmdir(junction)
    assert target_file.read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-share race regression")
def test_unlink_many_locks_validated_leaf_against_move_before_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    filesystem = SecureRootFilesystem(root)
    real_delete = secure_fs_module._win_delete_handle
    move_result: list[str] = []
    attempted = False

    def delete_after_external_move_attempt(handle: int) -> None:
        nonlocal attempted
        if not attempted:
            attempted = True

            def move_leaf() -> None:
                try:
                    first.rename(outside / first.name)
                except PermissionError:
                    move_result.append("blocked")
                else:
                    move_result.append("moved")

            worker = threading.Thread(target=move_leaf)
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive()
        real_delete(handle)

    monkeypatch.setattr(
        secure_fs_module,
        "_win_delete_handle",
        delete_after_external_move_attempt,
    )

    filesystem.unlink_many([first, second])

    assert move_result == ["blocked"]
    assert not first.exists()
    assert not second.exists()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows delete preflight regression")
def test_unlink_many_deletes_nothing_when_any_leaf_cannot_be_locked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    filesystem = SecureRootFilesystem(root)
    blocker = secure_fs_module._win_create_file(
        second,
        secure_fs_module._GENERIC_READ,
        secure_fs_module._FILE_SHARE_READ | secure_fs_module._FILE_SHARE_WRITE,
        secure_fs_module._OPEN_EXISTING,
        secure_fs_module._FILE_ATTRIBUTE_NORMAL,
    )
    try:
        with pytest.raises(SecureFilesystemError):
            filesystem.unlink_many([first, second])
        assert first.read_bytes() == b"first"
        assert second.read_bytes() == b"second"
    finally:
        secure_fs_module._CloseHandle(blocker)

    filesystem.unlink_many([first, second])
    assert not first.exists()
    assert not second.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows trusted-root regression")
def test_secure_filesystem_rejects_root_that_is_itself_a_junction(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    root = tmp_path / "root-junction"
    target.mkdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(SecureFilesystemError):
            SecureRootFilesystem(root)
    finally:
        os.rmdir(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows trusted-root regression")
def test_secure_filesystem_rejects_root_below_a_parent_junction(
    tmp_path: Path,
) -> None:
    target_parent = tmp_path / "target-parent"
    target_root = target_parent / "root"
    parent_junction = tmp_path / "parent-junction"
    target_root.mkdir(parents=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(parent_junction), str(target_parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(SecureFilesystemError):
            SecureRootFilesystem(parent_junction / "root")
    finally:
        os.rmdir(parent_junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows trusted-root race regression")
def test_missing_root_creation_rejects_parent_swapped_to_junction(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parked = tmp_path / "parent-parked"
    outside = tmp_path / "outside"
    requested_root = parent / "new-root"
    parent.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    raced = False

    class RacingFilesystem(SecureRootFilesystem):
        def _before_handle_use(self, operation: str, path: Path) -> None:
            nonlocal raced
            if raced or operation != "root_initialize":
                return
            parent.rename(parked)
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                parked.rename(parent)
                pytest.skip("junction creation is unavailable")
            raced = True

    try:
        with pytest.raises(SecureFilesystemError):
            RacingFilesystem(requested_root)
    finally:
        if raced:
            os.rmdir(parent)
            parked.rename(parent)

    assert raced is True
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def test_secure_filesystem_fails_closed_without_windows_before_any_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "must-not-be-created"
    monkeypatch.setattr(secure_fs_module, "_WINDOWS_PLATFORM", False)

    with pytest.raises(
        SecureFilesystemUnsupportedError,
        match="secure filesystem is unsupported on this platform",
    ):
        SecureRootFilesystem(root)

    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle compensation regression")
def test_atomic_write_closes_handle_and_preserves_primary_failure_when_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PrimaryFailure(RuntimeError):
        pass

    class CleanupFailure(RuntimeError):
        pass

    root = tmp_path / "root"
    root.mkdir()
    filesystem = SecureRootFilesystem(root)
    temporary_handle: int | None = None
    closed: list[int] = []
    original_create = secure_fs_module._win_create_file
    original_close = secure_fs_module._CloseHandle

    def tracked_create(path: Path, access: int, sharing: int, creation: int, flags: int) -> int:
        nonlocal temporary_handle
        handle = original_create(path, access, sharing, creation, flags)
        if creation == secure_fs_module._CREATE_NEW:
            temporary_handle = handle
        return handle

    def fail_primary(_handle: int, _content: bytes) -> None:
        raise PrimaryFailure("primary write failed")

    def fail_cleanup(_handle: int) -> None:
        raise CleanupFailure("temporary delete failed")

    def tracked_close(handle: int) -> object:
        closed.append(handle)
        return original_close(handle)

    monkeypatch.setattr(secure_fs_module, "_win_create_file", tracked_create)
    monkeypatch.setattr(secure_fs_module, "_win_write_all", fail_primary)
    monkeypatch.setattr(secure_fs_module, "_win_delete_handle", fail_cleanup)
    monkeypatch.setattr(secure_fs_module, "_CloseHandle", tracked_close)

    with pytest.raises(PrimaryFailure, match="primary write failed"):
        filesystem.atomic_write_bytes(root / "value.bin", b"content")

    assert temporary_handle is not None
    assert temporary_handle in closed
