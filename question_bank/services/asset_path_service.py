from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from question_bank.database.paths import project_data_root


DEFAULT_SEARCH_SUBDIRS = (
    "question_bank/raw_papers",
    "question_bank/extracted_images",
    "question_bank/previews",
)


class AmbiguousQuestionBankAssetPathError(ValueError):
    pass


def resolve_question_bank_asset_path(
    path_value: object,
    *,
    data_root: str | Path | None = None,
    search_subdirs: Iterable[str] | None = None,
) -> Path:
    root = (
        Path(data_root).expanduser().resolve()
        if data_root is not None
        else project_data_root().resolve()
    )
    subdirs = tuple(search_subdirs or DEFAULT_SEARCH_SUBDIRS)
    text = str(path_value or "").strip()
    stored = Path(text)
    if text and stored.is_absolute() and stored.is_file():
        return stored.resolve()

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

    exact = _unique_existing_files(candidates)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousQuestionBankAssetPathError(text)

    filename = stored.name
    fallback: list[Path] = []
    if filename:
        for subdir in subdirs:
            search_root = root / subdir
            if search_root.exists():
                fallback.extend(search_root.rglob(filename))
    matches = _unique_existing_files(fallback)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousQuestionBankAssetPathError(text)
    return stored


def _unique_existing_files(values: Iterable[Path]) -> list[Path]:
    unique = {value.resolve() for value in values if value.is_file()}
    return sorted(unique, key=lambda item: str(item).lower())


__all__ = [
    "AmbiguousQuestionBankAssetPathError",
    "resolve_question_bank_asset_path",
]
