from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from question_bank.database.paths import project_data_root
from question_bank.services.file_cache import (
    cached_asset_resolution,
    memoized_resolve,
)


DEFAULT_SEARCH_SUBDIRS = (
    "question_bank/raw_papers",
    "question_bank/extracted_images",
    "question_bank/previews",
)


class AmbiguousQuestionBankAssetPathError(ValueError):
    pass


class UncontrolledQuestionBankAssetPathError(ValueError):
    pass


def resolve_question_bank_asset_path(
    path_value: object,
    *,
    data_root: str | Path | None = None,
    search_subdirs: Iterable[str] | None = None,
) -> Path:
    root = (
        memoized_resolve(Path(data_root))
        if data_root is not None
        else memoized_resolve(project_data_root())
    )
    subdirs = tuple(search_subdirs or DEFAULT_SEARCH_SUBDIRS)
    text = str(path_value or "").strip()
    return cached_asset_resolution(
        saved_path=text,
        data_root=root,
        search_subdirs=subdirs,
        resolve=lambda: _resolve_asset_path(text, root=root, subdirs=subdirs),
        check_candidates=lambda cs: _unique_existing_files(cs, root=root),
    )


def _resolve_asset_path(
    text: str, *, root: Path, subdirs: tuple[str, ...]
) -> tuple[Path, str, tuple[Path, ...]]:
    stored = Path(text)
    if ".." in stored.parts:
        raise UncontrolledQuestionBankAssetPathError(
            "question-bank asset path cannot traverse outside the data root"
        )
    resolved_stored = _resolve_existing_file(stored) if text and stored.is_absolute() else None
    if resolved_stored is not None:
        if resolved_stored == root or resolved_stored.is_relative_to(root):
            return resolved_stored, "direct", (stored,)
        raise UncontrolledQuestionBankAssetPathError(
            "question-bank asset path is outside the data root"
        )

    candidates: list[Path] = []
    if text and not stored.is_absolute():
        candidates.append(root / stored)
    parts = stored.parts
    lowered = [part.lower() for part in parts]
    for marker in ("user_data", "data"):
        if marker in lowered:
            index = lowered.index(marker)
            if index + 1 < len(parts):
                candidates.append(root.joinpath(*parts[index + 1 :]))

    exact = _unique_existing_files(candidates, root=root)
    if len(exact) == 1:
        return exact[0], "direct", tuple(candidates)
    if len(exact) > 1:
        raise AmbiguousQuestionBankAssetPathError(text)

    filename = stored.name
    fallback: list[Path] = []
    if filename:
        for subdir in subdirs:
            search_root = (root / subdir).resolve(strict=False)
            if search_root != root and not search_root.is_relative_to(root):
                raise UncontrolledQuestionBankAssetPathError(
                    "question-bank search root is outside the data root"
                )
            if search_root.exists():
                fallback.extend(search_root.rglob(filename))
    matches = _unique_existing_files(fallback, root=root)
    if len(matches) == 1:
        return matches[0], "fallback", tuple(candidates)
    if len(matches) > 1:
        raise AmbiguousQuestionBankAssetPathError(text)
    if stored.is_absolute():
        raise UncontrolledQuestionBankAssetPathError(
            "question-bank asset path could not be remapped inside the data root"
        )
    return (root / stored).resolve(strict=False), "none", tuple(candidates)


def _resolve_existing_file(value: Path) -> Path | None:
    try:
        if not value.is_file():
            return None
        return value.resolve()
    except OSError:
        return None


def _unique_existing_files(values: Iterable[Path], *, root: Path) -> list[Path]:
    unique: set[Path] = set()
    for value in values:
        resolved = _resolve_existing_file(value)
        if resolved is not None and (
            resolved == root or resolved.is_relative_to(root)
        ):
            unique.add(resolved)
    return sorted(unique, key=lambda item: str(item).lower())


__all__ = [
    "AmbiguousQuestionBankAssetPathError",
    "UncontrolledQuestionBankAssetPathError",
    "resolve_question_bank_asset_path",
]
