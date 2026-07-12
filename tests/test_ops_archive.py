from __future__ import annotations

import asyncio
import stat
import zipfile
from pathlib import Path

import pytest

from backend.ops.archive import (
    OpsArchiveInvalid,
    OpsArchivePolicy,
    OpsArchiveTooLarge,
    extract_validated_zip,
    inspect_zip,
    stage_zip_upload,
)
from data_transfer_service import ExportEntry, write_export_zip


def _zip_with_member(path: Path, name: str, content: bytes = b"x") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "/rooted.txt", "C:/secret.txt", "user_data/../escape.txt"],
)
def test_inspect_zip_rejects_path_escape(tmp_path: Path, name: str) -> None:
    archive = _zip_with_member(tmp_path / "bad.zip", name)

    with pytest.raises(OpsArchiveInvalid):
        inspect_zip(
            archive,
            policy=OpsArchivePolicy(),
            allowed_roots={"user_data", "config"},
        )


def test_inspect_zip_rejects_unknown_root(tmp_path: Path) -> None:
    archive = _zip_with_member(tmp_path / "bad.zip", "phase6/profile.json")

    with pytest.raises(OpsArchiveInvalid, match="unknown_root"):
        inspect_zip(
            archive,
            policy=OpsArchivePolicy(),
            allowed_roots={"user_data", "config"},
        )


def test_inspect_zip_rejects_symlink_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("user_data/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, "../outside")

    with pytest.raises(OpsArchiveInvalid, match="unsupported_member_type"):
        inspect_zip(
            archive_path,
            policy=OpsArchivePolicy(),
            allowed_roots={"user_data"},
        )


def test_inspect_zip_rejects_duplicate_casefold_target(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("user_data/Config/value.json", "one")
        archive.writestr("user_data/config/VALUE.json", "two")

    with pytest.raises(OpsArchiveInvalid, match="duplicate_target"):
        inspect_zip(
            archive_path,
            policy=OpsArchivePolicy(),
            allowed_roots={"user_data"},
        )


def test_inspect_zip_enforces_member_and_total_limits(tmp_path: Path) -> None:
    archive_path = tmp_path / "large.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("user_data/one.bin", b"1" * 6)
        archive.writestr("user_data/two.bin", b"2" * 6)

    with pytest.raises(OpsArchiveTooLarge, match="member_too_large"):
        inspect_zip(
            archive_path,
            policy=OpsArchivePolicy(max_member_bytes=5, max_expanded_bytes=20),
            allowed_roots={"user_data"},
        )
    with pytest.raises(OpsArchiveTooLarge, match="archive_too_large"):
        inspect_zip(
            archive_path,
            policy=OpsArchivePolicy(max_member_bytes=10, max_expanded_bytes=10),
            allowed_roots={"user_data"},
        )


def test_inspect_zip_rejects_excessive_compression_ratio(tmp_path: Path) -> None:
    archive = _zip_with_member(
        tmp_path / "bomb.zip",
        "user_data/zeros.bin",
        b"\0" * 50_000,
    )

    with pytest.raises(OpsArchiveTooLarge, match="compression_ratio"):
        inspect_zip(
            archive,
            policy=OpsArchivePolicy(max_compression_ratio=2.0),
            allowed_roots={"user_data"},
        )


def test_extract_validated_zip_writes_only_regular_members(tmp_path: Path) -> None:
    archive = _zip_with_member(
        tmp_path / "valid.zip",
        "user_data/config/settings.json",
        b'{"ok": true}',
    )
    destination = tmp_path / "staging"

    inspection = extract_validated_zip(
        archive,
        destination,
        policy=OpsArchivePolicy(),
        allowed_roots={"user_data", "config"},
    )

    assert inspection.file_count == 1
    assert (destination / "user_data" / "config" / "settings.json").read_bytes() == b'{"ok": true}'


def test_stage_zip_upload_streams_and_hashes_content(tmp_path: Path) -> None:
    source = _zip_with_member(tmp_path / "source.zip", "user_data/value.txt", b"value")
    payload = source.read_bytes()

    async def chunks():
        yield payload[:7]
        yield payload[7:]

    staged = asyncio.run(
        stage_zip_upload(
            filename="portable.zip",
            chunks=chunks(),
            staging_root=tmp_path / "uploads",
            policy=OpsArchivePolicy(max_upload_bytes=len(payload) + 1),
            upload_id_factory=lambda: "a" * 32,
        )
    )

    assert staged.upload_id == "a" * 32
    assert staged.filename == "portable.zip"
    assert staged.size_bytes == len(payload)
    assert staged.path.read_bytes() == payload
    assert len(staged.sha256) == 64


def test_stage_zip_upload_cleans_partial_file_on_size_failure(tmp_path: Path) -> None:
    async def chunks():
        yield b"1234"
        yield b"5678"

    with pytest.raises(OpsArchiveTooLarge, match="upload_too_large"):
        asyncio.run(
            stage_zip_upload(
                filename="portable.zip",
                chunks=chunks(),
                staging_root=tmp_path / "uploads",
                policy=OpsArchivePolicy(max_upload_bytes=5),
                upload_id_factory=lambda: "b" * 32,
            )
        )

    assert not list((tmp_path / "uploads").glob("*"))


def test_write_export_zip_streams_to_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 1024)
    destination = tmp_path / "output" / "out.zip"

    write_export_zip(
        [ExportEntry(source, "user_data/source.bin", 1024)],
        destination,
    )

    assert zipfile.is_zipfile(destination)
    with zipfile.ZipFile(destination, "r") as archive:
        assert archive.read("user_data/source.bin") == b"x" * 1024
