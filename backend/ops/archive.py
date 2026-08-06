from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class OpsArchiveInvalid(ValueError):
    pass


class OpsArchiveTooLarge(OpsArchiveInvalid):
    pass


@dataclass(frozen=True, slots=True)
class OpsArchivePolicy:
    max_upload_bytes: int = 200 * 1024 * 1024
    max_members: int = 10_000
    max_expanded_bytes: int = 1024 * 1024 * 1024
    max_member_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: float = 100.0


@dataclass(frozen=True, slots=True)
class OpsArchiveMember:
    archive_name: str
    parts: tuple[str, ...]
    size_bytes: int
    is_dir: bool


@dataclass(frozen=True, slots=True)
class OpsArchiveInspection:
    members: tuple[OpsArchiveMember, ...]
    file_count: int
    total_expanded_bytes: int


@dataclass(frozen=True, slots=True)
class StagedZipUpload:
    upload_id: str
    filename: str
    size_bytes: int
    sha256: str
    path: Path


_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def inspect_zip(
    archive_path: Path,
    *,
    policy: OpsArchivePolicy,
    allowed_roots: set[str],
) -> OpsArchiveInspection:
    path = Path(archive_path)
    if not zipfile.is_zipfile(path):
        raise OpsArchiveInvalid("invalid_zip")
    members: list[OpsArchiveMember] = []
    seen_targets: set[str] = set()
    total_expanded = 0
    file_count = 0
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > int(policy.max_members):
                raise OpsArchiveTooLarge("too_many_members")
            for info in infos:
                parts = _normalized_member(info.filename, allowed_roots)
                target_key = "/".join(parts).casefold()
                if target_key in seen_targets:
                    raise OpsArchiveInvalid("duplicate_target")
                seen_targets.add(target_key)
                _validate_member_type(info)
                is_dir = info.is_dir()
                size_bytes = max(0, int(info.file_size))
                if not is_dir:
                    file_count += 1
                    if size_bytes > int(policy.max_member_bytes):
                        raise OpsArchiveTooLarge("member_too_large")
                    total_expanded += size_bytes
                    if total_expanded > int(policy.max_expanded_bytes):
                        raise OpsArchiveTooLarge("archive_too_large")
                    compressed = max(0, int(info.compress_size))
                    ratio = float("inf") if compressed == 0 and size_bytes else (
                        size_bytes / max(1, compressed)
                    )
                    if ratio > float(policy.max_compression_ratio):
                        raise OpsArchiveTooLarge("compression_ratio")
                members.append(
                    OpsArchiveMember(
                        archive_name=info.filename,
                        parts=parts,
                        size_bytes=size_bytes,
                        is_dir=is_dir,
                    )
                )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise OpsArchiveInvalid("crc_failed")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, OpsArchiveInvalid):
            raise
        raise OpsArchiveInvalid("invalid_zip") from exc
    return OpsArchiveInspection(
        members=tuple(members),
        file_count=file_count,
        total_expanded_bytes=total_expanded,
    )


def extract_validated_zip(
    archive_path: Path,
    destination: Path,
    *,
    policy: OpsArchivePolicy,
    allowed_roots: set[str],
) -> OpsArchiveInspection:
    inspection = inspect_zip(
        archive_path,
        policy=policy,
        allowed_roots=allowed_roots,
    )
    root = Path(destination)
    if root.exists() and (_is_reparse_point(root) or not root.is_dir()):
        raise OpsArchiveInvalid("unsafe_destination")
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in inspection.members:
            target = root.joinpath(*member.parts)
            try:
                target.resolve(strict=False).relative_to(resolved_root)
            except ValueError as exc:
                raise OpsArchiveInvalid("unsafe_destination") from exc
            if member.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                _reject_reparse_chain(target, root)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _reject_reparse_chain(target.parent, root)
            try:
                with archive.open(member.archive_name, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except FileExistsError as exc:
                raise OpsArchiveInvalid("duplicate_target") from exc
    return inspection


async def stage_zip_upload(
    *,
    filename: str,
    chunks: AsyncIterable[bytes],
    staging_root: Path,
    policy: OpsArchivePolicy,
    upload_id_factory: Callable[[], str],
) -> StagedZipUpload:
    safe_filename = Path(str(filename or "")).name
    if safe_filename != str(filename or "") or Path(safe_filename).suffix.lower() != ".zip":
        raise OpsArchiveInvalid("unsupported_upload_type")
    upload_id = str(upload_id_factory() or "").lower()
    if not _UPLOAD_ID_RE.fullmatch(upload_id):
        raise OpsArchiveInvalid("invalid_upload_id")
    root = Path(staging_root)
    if root.exists() and (_is_reparse_point(root) or not root.is_dir()):
        raise OpsArchiveInvalid("unsafe_upload_root")
    root.mkdir(parents=True, exist_ok=True)
    final_path = root / f"{upload_id}.zip"
    if final_path.exists():
        raise OpsArchiveInvalid("upload_id_conflict")
    fd, temporary_value = tempfile.mkstemp(dir=root, prefix=f".{upload_id}.", suffix=".tmp")
    temporary = Path(temporary_value)
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as output:
            async for chunk in chunks:
                data = bytes(chunk)
                size_bytes += len(data)
                if size_bytes > int(policy.max_upload_bytes):
                    raise OpsArchiveTooLarge("upload_too_large")
                output.write(data)
                digest.update(data)
            output.flush()
            os.fsync(output.fileno())
        if not zipfile.is_zipfile(temporary):
            raise OpsArchiveInvalid("invalid_zip")
        os.replace(temporary, final_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    return StagedZipUpload(
        upload_id=upload_id,
        filename=safe_filename,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        path=final_path,
    )


def _normalized_member(name: str, allowed_roots: set[str]) -> tuple[str, ...]:
    text = str(name or "").replace("\\", "/")
    pure = PurePosixPath(text)
    parts = pure.parts
    if (
        not text
        or pure.is_absolute()
        or PureWindowsPath(text).drive
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise OpsArchiveInvalid("unsafe_member")
    if parts[0] not in allowed_roots:
        raise OpsArchiveInvalid("unknown_root")
    if (
        parts[0].casefold() == "user_data"
        and len(parts) > 1
        and parts[1].casefold() == "workspaces"
        and (
            len(parts) < 4
            or parts[2].casefold() not in {"class-teacher", "teaching-prep"}
        )
    ):
        raise OpsArchiveInvalid("workspace_data_not_allowed")
    return tuple(parts)


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    mode = int(info.external_attr) >> 16
    file_type = stat.S_IFMT(mode)
    if file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}:
        raise OpsArchiveInvalid("unsupported_member_type")


def _reject_reparse_chain(path: Path, root: Path) -> None:
    root = root.resolve()
    current = Path(path)
    while True:
        if _is_reparse_point(current):
            raise OpsArchiveInvalid("unsafe_destination")
        if current.resolve() == root:
            return
        if current.parent == current:
            raise OpsArchiveInvalid("unsafe_destination")
        current = current.parent


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False


__all__ = [
    "OpsArchiveInspection",
    "OpsArchiveInvalid",
    "OpsArchivePolicy",
    "OpsArchiveTooLarge",
    "StagedZipUpload",
    "extract_validated_zip",
    "inspect_zip",
    "stage_zip_upload",
]
