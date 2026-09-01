from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

MEDIA_TYPES = {
    ".bmp": "image/bmp",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}


class ControlledFileError(RuntimeError):
    """Base error for files resolved through an explicit storage boundary."""


class ControlledFileForbidden(ControlledFileError):
    """Raised when a stored path resolves outside its allowed root."""


class ControlledFileExpired(ControlledFileError):
    """Raised when a once-addressable file is no longer available."""


class ControlledFileTypeError(ControlledFileError):
    """Raised when a file extension is not allowed for the resource type."""


@dataclass(frozen=True, slots=True)
class ResolvedFile:
    path: Path
    media_type: str


def resolve_controlled_file(
    path_value: object,
    *,
    root: Path,
    data_root: Path,
    allowed_suffixes: Iterable[str],
) -> ResolvedFile:
    allowed = {str(suffix).lower() for suffix in allowed_suffixes}
    controlled_root = Path(root)
    controlled_data_root = Path(data_root)
    try:
        root_resolved = controlled_root.resolve(strict=False)
        data_root_resolved = controlled_data_root.resolve(strict=False)
        candidate_resolved = _controlled_candidate(
            path_value,
            root=root_resolved,
            data_root=data_root_resolved,
        )
        candidate_resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ControlledFileForbidden(
            "Stored file is outside the allowed root."
        ) from exc

    suffix = candidate_resolved.suffix.lower()
    if suffix not in allowed or suffix not in MEDIA_TYPES:
        raise ControlledFileTypeError("Stored file type is not allowed.")

    try:
        available = candidate_resolved.is_file()
    except OSError:
        available = False
    if not available:
        raise ControlledFileExpired("Stored file is no longer available.")

    return ResolvedFile(
        path=candidate_resolved,
        media_type=MEDIA_TYPES[suffix],
    )


def _controlled_candidate(
    path_value: object,
    *,
    root: Path,
    data_root: Path,
) -> Path:
    text = str(path_value or "").strip()
    if not text:
        return root / "__missing__"

    windows_path = PureWindowsPath(text)
    posix_path = PurePosixPath(text)
    native_path = Path(text)
    uses_windows_syntax = bool(windows_path.drive) or "\\" in text
    parsed_parts = (
        tuple(windows_path.parts)
        if uses_windows_syntax
        else tuple(posix_path.parts)
    )
    normalized_parts = tuple(
        str(part).strip("\\/").casefold()
        for part in parsed_parts
    )
    if ".." in normalized_parts:
        raise ControlledFileForbidden(
            "Stored file is outside the allowed root."
        )

    syntax_absolute = (
        native_path.is_absolute()
        or windows_path.is_absolute()
        or posix_path.is_absolute()
    )
    if windows_path.drive and not windows_path.is_absolute():
        raise ControlledFileForbidden(
            "Stored file is outside the allowed root."
        )

    legacy_relative = _legacy_data_relative_parts(
        parsed_parts,
        normalized_parts,
    )
    if syntax_absolute:
        if native_path.is_absolute():
            native_resolved = native_path.resolve(strict=False)
            try:
                native_resolved.relative_to(root)
            except ValueError:
                pass
            else:
                return native_resolved
        if legacy_relative is None:
            raise ControlledFileForbidden(
                "Stored file is outside the allowed root."
            )
        return data_root.joinpath(*legacy_relative).resolve(strict=False)

    if legacy_relative is not None:
        return data_root.joinpath(*legacy_relative).resolve(strict=False)

    clean_parts = tuple(
        part
        for part in parsed_parts
        if str(part).strip("\\/") not in {"", "."}
    )
    if clean_parts and normalized_parts[0] == root.name.casefold():
        return data_root.joinpath(*clean_parts).resolve(strict=False)
    return root.joinpath(*clean_parts).resolve(strict=False)


def _legacy_data_relative_parts(
    parsed_parts: tuple[str, ...],
    normalized_parts: tuple[str, ...],
) -> tuple[str, ...] | None:
    for marker in ("user_data", "data"):
        if marker not in normalized_parts:
            continue
        index = normalized_parts.index(marker)
        relative = tuple(
            str(part).strip("\\/")
            for part in parsed_parts[index + 1 :]
            if str(part).strip("\\/")
        )
        return relative or None
    return None
