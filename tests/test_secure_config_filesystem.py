from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
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
