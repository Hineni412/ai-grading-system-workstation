# Question-Bank Source Paper Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive every imported source paper under `user_data/question_bank/raw_papers`, store portable database paths, migrate the 12 existing external DOCX references without changing questions or AI tags, and resolve moved question-bank assets after copying `user_data` to another computer.

**Architecture:** Add a filesystem-only content-addressed archive service and a separate question-bank asset resolver. Route every import through the archive service, keep physical paths for parsing and data-relative paths for persistence, then provide a transactional migration service plus a thin CLI for existing databases. Update source-paper and image consumers to resolve portable and legacy absolute paths at read time.

**Tech Stack:** Python 3.11+, `pathlib`, `hashlib`, `sqlite3`, `shutil`, `tempfile`, pytest, existing question-bank services and PathManager.

## Global Constraints

- Copy source papers; never move or delete the external originals.
- Archive to `user_data/question_bank/raw_papers/` using `<sanitized-stem>_<sha256[:12]><suffix>`.
- Verify the complete SHA256 before reusing a short-hash candidate.
- Persist source paths relative to the data root, for example `question_bank/raw_papers/paper_a1b2c3d4e5f6.docx`.
- Do not modify question text, answers, question IDs, soft-delete state, tags, API profiles, or Git tracking policy.
- Do not issue any AI request during migration or verification.
- Back up `question_bank.db` successfully before applying database path updates.
- Do not stage or commit production files under `user_data/`; code and documentation commits only.
- Preserve compatibility with legacy absolute paths at read time; migrated data must not be opened with pre-feature code.

---

### Task 1: Content-addressed source paper archive

**Files:**
- Create: `question_bank/services/source_paper_archive_service.py`
- Create: `tests/test_source_paper_archive_service.py`

**Interfaces:**
- Consumes: `project_data_root()` from `question_bank.database.paths` when no explicit data root is supplied.
- Produces: `ArchivedSourcePaper`, `archive_source_paper(...)`, and `archive_source_bytes(...)` for later import and migration tasks.

- [ ] **Step 1: Write failing archive tests**

```python
from pathlib import Path

from question_bank.services.source_paper_archive_service import (
    archive_source_bytes,
    archive_source_paper,
)


def test_archive_copies_without_removing_source_and_reuses_identical_content(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    source = tmp_path / "outside" / "期中试卷.docx"
    source.parent.mkdir()
    source.write_bytes(b"same-paper")

    first = archive_source_paper(source, data_root=data_root)
    second_source = tmp_path / "other" / "renamed.docx"
    second_source.parent.mkdir()
    second_source.write_bytes(b"same-paper")
    second = archive_source_paper(second_source, data_root=data_root)

    assert source.read_bytes() == b"same-paper"
    assert second_source.read_bytes() == b"same-paper"
    assert first.physical_path == second.physical_path
    assert first.stored_path.startswith("question_bank/raw_papers/")
    assert first.sha256 == second.sha256
    assert second.reused is True


def test_same_name_with_different_content_creates_distinct_archives(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    left = tmp_path / "a" / "paper.docx"
    right = tmp_path / "b" / "paper.docx"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"left")
    right.write_bytes(b"right")

    archived_left = archive_source_paper(left, data_root=data_root)
    archived_right = archive_source_paper(right, data_root=data_root)

    assert archived_left.physical_path != archived_right.physical_path
    assert archived_left.physical_path.read_bytes() == b"left"
    assert archived_right.physical_path.read_bytes() == b"right"


def test_archive_bytes_uses_the_same_content_addressed_contract(tmp_path: Path) -> None:
    archived = archive_source_bytes(
        filename="upload.docx",
        content=b"uploaded-paper",
        data_root=tmp_path / "user_data",
    )
    assert archived.physical_path.read_bytes() == b"uploaded-paper"
    assert archived.stored_path.endswith(f"_{archived.sha256[:12]}.docx")
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `python -m pytest tests/test_source_paper_archive_service.py -q`

Expected: collection fails with `ModuleNotFoundError: question_bank.services.source_paper_archive_service`.

- [ ] **Step 3: Implement the archive service**

```python
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from question_bank.database.paths import project_data_root


SUPPORTED_SUFFIXES = {".docx", ".pdf"}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


@dataclass(frozen=True, slots=True)
class ArchivedSourcePaper:
    physical_path: Path
    stored_path: str
    sha256: str
    reused: bool


def archive_source_paper(
    source_file: str | Path,
    *,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    source = Path(source_file).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    return _archive_from_path(source, data_root=data_root, raw_papers_dir=raw_papers_dir)


def archive_source_bytes(
    *,
    filename: str,
    content: bytes,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    if not content:
        raise ValueError("source paper content is empty")
    root, destination_dir = _roots(data_root, raw_papers_dir)
    suffix = Path(filename).suffix.lower() or ".docx"
    _validate_suffix(suffix)
    digest = hashlib.sha256(content).hexdigest()
    destination = _destination(destination_dir, Path(filename).stem, suffix, digest)
    reused = _reuse_matching(destination_dir, suffix, digest)
    if reused is not None:
        return _result(reused, root, digest, True)
    _atomic_write_bytes(destination, content)
    if _sha256_file(destination) != digest:
        destination.unlink(missing_ok=True)
        raise OSError("archived source hash verification failed")
    return _result(destination, root, digest, False)


def _archive_from_path(source: Path, *, data_root, raw_papers_dir) -> ArchivedSourcePaper:
    root, destination_dir = _roots(data_root, raw_papers_dir)
    suffix = source.suffix.lower()
    _validate_suffix(suffix)
    digest = _sha256_file(source)
    reused = _reuse_matching(destination_dir, suffix, digest)
    if reused is not None:
        return _result(reused, root, digest, True)
    destination = _available_destination(destination_dir, source.stem, suffix, digest)
    fd, temp_name = tempfile.mkstemp(prefix=".source-paper-", suffix=".tmp", dir=destination_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        if _sha256_file(temp_path) != digest:
            raise OSError("archived source hash verification failed")
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return _result(destination, root, digest, False)


def _roots(data_root, raw_papers_dir) -> tuple[Path, Path]:
    if data_root is not None:
        root = Path(data_root).expanduser().resolve()
    elif raw_papers_dir is not None:
        destination = Path(raw_papers_dir).expanduser().resolve()
        if destination.name != "raw_papers" or destination.parent.name != "question_bank":
            raise ValueError("raw_papers_dir must end with question_bank/raw_papers")
        root = destination.parent.parent
    else:
        root = project_data_root().resolve()
    destination = (
        Path(raw_papers_dir).expanduser().resolve()
        if raw_papers_dir is not None
        else root / "question_bank" / "raw_papers"
    )
    if destination != root and root not in destination.parents:
        raise ValueError("raw_papers_dir must stay inside data_root")
    destination.mkdir(parents=True, exist_ok=True)
    return root, destination


def _validate_suffix(suffix: str) -> None:
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source paper type: {suffix}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_stem(value: str) -> str:
    return _UNSAFE_FILENAME.sub("_", value).strip(" ._") or "source-paper"


def _destination(directory: Path, stem: str, suffix: str, digest: str) -> Path:
    return directory / f"{_safe_stem(stem)}_{digest[:12]}{suffix}"


def _available_destination(directory: Path, stem: str, suffix: str, digest: str) -> Path:
    short = _destination(directory, stem, suffix, digest)
    if not short.exists() or _sha256_file(short) == digest:
        return short
    return directory / f"{_safe_stem(stem)}_{digest}{suffix}"


def _reuse_matching(directory: Path, suffix: str, digest: str) -> Path | None:
    for candidate in directory.glob(f"*_{digest[:12]}*{suffix}"):
        resolved = candidate.resolve()
        if directory.resolve() not in resolved.parents:
            continue
        if candidate.is_file() and _sha256_file(candidate) == digest:
            return candidate
    return None


def _result(path: Path, root: Path, digest: str, reused: bool) -> ArchivedSourcePaper:
    resolved = path.resolve()
    return ArchivedSourcePaper(
        physical_path=resolved,
        stored_path=resolved.relative_to(root).as_posix(),
        sha256=digest,
        reused=reused,
    )


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".source-paper-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
```

Use `_available_destination` in `archive_source_bytes` before `_atomic_write_bytes`; this prevents a theoretical short-prefix collision from overwriting a different archive.

- [ ] **Step 4: Run archive tests and the existing importer tests**

Run: `python -m pytest tests/test_source_paper_archive_service.py tests/test_question_bank_importer.py -q`

Expected: all tests pass; no files appear under the real `user_data` because every new test supplies a temporary `data_root`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add question_bank/services/source_paper_archive_service.py tests/test_source_paper_archive_service.py
git commit -m "feat: add content-addressed source paper archive"
```

### Task 2: Portable question-bank asset resolution

**Files:**
- Create: `question_bank/services/asset_path_service.py`
- Create: `tests/test_question_bank_asset_path_service.py`
- Modify: `tests/test_portable_path_resolution.py`

**Interfaces:**
- Consumes: `resolve_stored_file_path(...)` and `project_data_root()`.
- Produces: `resolve_question_bank_asset_path(path_value, *, data_root=None, search_subdirs=None) -> Path`.

- [ ] **Step 1: Write failing resolver tests**

```python
from pathlib import Path

from question_bank.services.asset_path_service import resolve_question_bank_asset_path


def test_resolves_data_relative_question_bank_path(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    archived = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"paper")

    assert resolve_question_bank_asset_path(
        "question_bank/raw_papers/paper_hash.docx",
        data_root=data_root,
    ) == archived


def test_rebases_legacy_absolute_question_image_path(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    image = data_root / "question_bank" / "extracted_images" / "set" / "rId1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    resolved = resolve_question_bank_asset_path(
        r"D:\old\project\user_data\question_bank\extracted_images\set\rId1.png",
        data_root=data_root,
    )
    assert resolved == image


def test_ambiguous_filename_fallback_raises_instead_of_guessing(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    for folder in ("set-a", "set-b"):
        image = data_root / "question_bank/extracted_images" / folder / "same.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(folder.encode())
    with pytest.raises(AmbiguousQuestionBankAssetPathError):
        resolve_question_bank_asset_path("Z:/missing/same.png", data_root=data_root)
```

- [ ] **Step 2: Run resolver tests and confirm RED**

Run: `python -m pytest tests/test_question_bank_asset_path_service.py -q`

Expected: collection fails because `asset_path_service` does not exist.

- [ ] **Step 3: Implement the resolver wrapper**

```python
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
    root = Path(data_root) if data_root is not None else project_data_root()
    subdirs = tuple(search_subdirs or DEFAULT_SEARCH_SUBDIRS)
    text = str(path_value or "").strip()
    stored = Path(text)
    if text and stored.exists():
        return stored

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
```

- [ ] **Step 4: Run path-resolution tests**

Run: `python -m pytest tests/test_question_bank_asset_path_service.py tests/test_portable_path_resolution.py -q`

Expected: all tests pass, including legacy grading paths and new question-bank paths.

- [ ] **Step 5: Commit Task 2**

```powershell
git add question_bank/services/asset_path_service.py tests/test_question_bank_asset_path_service.py tests/test_portable_path_resolution.py
git commit -m "feat: resolve portable question-bank asset paths"
```

### Task 3: Route batch imports through the archive

**Files:**
- Modify: `question_bank/importers/batch_importer.py:194-371`
- Modify: `tests/test_question_bank_importer.py`

**Interfaces:**
- Consumes: `archive_source_paper(...)` and `ArchivedSourcePaper` from Task 1.
- Produces: `import_scanned_papers(..., data_root=None, raw_papers_dir=None, archive_sources=True)` and `_import_scanned_paper(..., stored_source_file)`.

- [ ] **Step 1: Add a failing importer persistence test**

```python
def test_import_archives_local_source_and_persists_data_relative_path(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    source = tmp_path / "outside" / "paper.docx"
    source.parent.mkdir()
    source.write_bytes(b"paper-content")

    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 这是长度足够的测试题目\n答案：1. 42",
        ),
    )

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="docx")],
        db_path,
        data_root=data_root,
    )

    with connect(db_path) as conn:
        paper_source = conn.execute("SELECT source_file FROM papers").fetchone()[0]
        question_source = conn.execute("SELECT source_file FROM questions").fetchone()[0]
    assert paper_source.startswith("question_bank/raw_papers/")
    assert question_source == paper_source
    assert (data_root / paper_source).exists()
    assert source.exists()
    assert result.files[0].source_file == paper_source
```

- [ ] **Step 2: Run the single test and confirm RED**

Run: `python -m pytest tests/test_question_bank_importer.py::test_import_archives_local_source_and_persists_data_relative_path -q`

Expected: FAIL because `import_scanned_papers` does not accept `data_root` and persists the external path.

- [ ] **Step 3: Implement archive-first import coordination**

Update the public signature and loop:

```python
def import_scanned_papers(
    scanned_papers: list[ScannedPaper],
    db_path: str | Path,
    *,
    default_metadata: PaperMetadata | None = None,
    question_range: str | None = None,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
    archive_sources: bool = True,
) -> BatchImportResult:
    database_path = Path(db_path)
    initialize_database(database_path)
    file_results: list[PaperImportFileResult] = []
    for scanned in scanned_papers:
        original_path = Path(scanned.source_file)
        try:
            if archive_sources:
                archived = archive_source_paper(
                    original_path,
                    data_root=data_root,
                    raw_papers_dir=raw_papers_dir,
                )
                physical_path = archived.physical_path
                stored_source_file = archived.stored_path
            else:
                physical_path = original_path
                stored_source_file = str(original_path)
            file_results.append(
                _import_scanned_paper(
                    physical_path,
                    database_path,
                    stored_source_file=stored_source_file,
                    metadata=_merge_metadata(default_metadata or PaperMetadata(), scanned.metadata),
                    question_range=question_range,
                )
            )
        except Exception as exc:
            LOGGER.exception("Failed to import local paper %s", original_path)
            file_results.append(PaperImportFileResult(str(original_path), "failed", message=str(exc)))
```

Change `_import_scanned_paper` so duplicate checks, `papers.source_file`, parsed questions, rich-content mapping, and `PaperImportFileResult.source_file` use `stored_source_file`, while `_extract_paper` and `_file_fingerprint` continue using the physical archived file.

- [ ] **Step 4: Run all importer and question-bank tests**

Run: `python -m pytest tests/test_question_bank_importer.py tests/test_question_bank_service.py tests/test_question_bank_import_dialog_ui.py -q`

Expected: all pass; duplicate imports reuse the archive and remain duplicate at the database layer.

- [ ] **Step 5: Commit Task 3**

```powershell
git add question_bank/importers/batch_importer.py tests/test_question_bank_importer.py
git commit -m "feat: archive source papers before question import"
```

### Task 4: Consolidate uploaded and grading-paper intake

**Files:**
- Modify: `question_bank/services/grading_paper_intake_service.py:29-181`
- Modify: `tests/test_source_question_link_service.py`
- Create: `tests/test_grading_paper_archive_intake.py`

**Interfaces:**
- Consumes: `archive_source_bytes(...)`, `archive_source_paper(...)`, and the Task 3 import options.
- Produces: existing public functions with unchanged return types, now returning canonical archived paths.

- [ ] **Step 1: Write failing upload and existing-file tests**

```python
def test_uploaded_grading_paper_uses_hash_archive(tmp_path: Path) -> None:
    saved = save_uploaded_grading_paper(
        filename="paper.docx",
        content=b"paper-content",
        raw_papers_dir=tmp_path / "user_data/question_bank/raw_papers",
    )
    assert saved.name.startswith("paper_")
    assert saved.name.endswith(".docx")
    assert saved.read_bytes() == b"paper-content"


def test_copy_existing_grading_paper_reuses_identical_archive(tmp_path: Path) -> None:
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper-content")
    raw = tmp_path / "user_data/question_bank/raw_papers"
    first = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)
    second = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)
    assert first == second
    assert len(list(raw.glob("*.docx"))) == 1
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run: `python -m pytest tests/test_grading_paper_archive_intake.py -q`

Expected: FAIL because current functions use timestamps and create duplicate files.

- [ ] **Step 3: Delegate both compatibility functions to the archive service**

```python
def save_uploaded_grading_paper(*, filename: str, content: bytes, raw_papers_dir=None) -> Path:
    return archive_source_bytes(
        filename=filename,
        content=content,
        raw_papers_dir=raw_papers_dir,
    ).physical_path


def copy_existing_grading_paper_to_raw_dir(source_file, *, raw_papers_dir=None) -> Path:
    return archive_source_paper(
        source_file,
        raw_papers_dir=raw_papers_dir,
    ).physical_path
```

Ensure `intake_grading_paper_to_question_bank` passes the current data root/raw-papers directory to Task 3 and uses the returned stored source path when querying newly imported questions. Avoid a second physical copy when the input is already a canonical archive.

- [ ] **Step 4: Run intake and link tests**

Run: `python -m pytest tests/test_grading_paper_archive_intake.py tests/test_source_question_link_service.py tests/test_question_bank_importer.py -q`

Expected: all pass, including source-question linking after archived path persistence.

- [ ] **Step 5: Commit Task 4**

```powershell
git add question_bank/services/grading_paper_intake_service.py tests/test_grading_paper_archive_intake.py tests/test_source_question_link_service.py
git commit -m "refactor: unify grading paper archive intake"
```

### Task 5: Resolve portable paths in previews, rich content, UI, and exports

**Files:**
- Modify: `question_bank/services/preview_service.py:65-176`
- Modify: `question_bank/services/rich_content_backfill_service.py:45-89`
- Modify: `pages/题库管理.py:2119-2415`
- Modify: `pages/组卷.py:457-581`
- Modify: `question_bank/exporters/paper_docx_exporter.py:29-45,582-620`
- Modify: `tests/test_question_preview_display.py`
- Modify: `tests/test_question_bank_exporter.py`
- Create: `tests/test_question_bank_portable_consumers.py`

**Interfaces:**
- Consumes: `resolve_question_bank_asset_path(...)` from Task 2.
- Produces: all question-bank file consumers accept data-relative or legacy absolute source/image paths.

- [ ] **Step 1: Write failing consumer tests**

```python
def test_preview_resolves_data_relative_source_before_conversion(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "user_data"
    source = data_root / "question_bank/raw_papers/paper_hash.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"docx")
    monkeypatch.setattr(preview_service, "project_data_root", lambda: data_root)
    resolved = preview_service._resolved_source_file(
        {"source_file": "question_bank/raw_papers/paper_hash.docx"}
    )
    assert resolved == source


def test_exporter_resolves_legacy_absolute_image_after_data_move(tmp_path, monkeypatch) -> None:
    import base64

    png_fixture_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4l8AAAAASUVORK5CYII="
    )
    data_root = tmp_path / "user_data"
    image = data_root / "question_bank/extracted_images/set/rId1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png_fixture_bytes)
    monkeypatch.setattr(asset_path_service, "project_data_root", lambda: data_root)
    resolved = resolve_question_bank_asset_path(
        r"D:\old\user_data\question_bank\extracted_images\set\rId1.png",
        data_root=data_root,
    )
    assert resolved == image
```

- [ ] **Step 2: Run portable consumer tests and confirm RED**

Run: `python -m pytest tests/test_question_bank_portable_consumers.py tests/test_question_bank_exporter.py -q`

Expected: FAIL because preview/UI/export consumers still call `Path(stored_value)` directly.

- [ ] **Step 3: Route every file access through the resolver**

Apply this pattern at each source/image boundary:

```python
from question_bank.services.asset_path_service import resolve_question_bank_asset_path


def _resolved_source_file(question: Mapping[str, Any]) -> Path:
    return resolve_question_bank_asset_path(
        question.get("source_file"),
        search_subdirs=("question_bank/raw_papers",),
    )


def _existing_image_paths(values: list[object]) -> list[Path]:
    resolved = [
        resolve_question_bank_asset_path(
            value,
            search_subdirs=("question_bank/extracted_images", "question_bank/previews"),
        )
        for value in values
    ]
    return [path for path in resolved if path.is_file()]
```

Use `_resolved_source_file` in preview generation and rich-content backfill. Replace direct `Path(p).exists()` image checks in both Streamlit pages and the DOCX exporter with `_existing_image_paths` or the resolver. Preserve display order and existing deduplication behavior.

- [ ] **Step 4: Run preview, UI, and exporter tests**

Run: `python -m pytest tests/test_question_bank_portable_consumers.py tests/test_question_preview_display.py tests/test_question_bank_exporter.py tests/test_question_preview_display_ui.py -q`

Expected: all pass and no test accesses the real `user_data`.

- [ ] **Step 5: Commit Task 5**

```powershell
git add question_bank/services/preview_service.py question_bank/services/rich_content_backfill_service.py 'pages/题库管理.py' 'pages/组卷.py' question_bank/exporters/paper_docx_exporter.py tests/test_question_bank_portable_consumers.py tests/test_question_preview_display.py tests/test_question_bank_exporter.py
git commit -m "fix: resolve moved question-bank assets"
```

### Task 6: Transactional source path migration and CLI

**Files:**
- Create: `question_bank/services/source_paper_migration_service.py`
- Create: `update_tools/archive_question_bank_sources.py`
- Create: `tests/test_source_paper_migration_service.py`

**Interfaces:**
- Consumes: archive and resolver services from Tasks 1-2 plus `connect()` from `question_bank.database.schema`.
- Produces: `MigrationReport`, `plan_source_paper_migration(...)`, `apply_source_paper_migration(...)`, and CLI flags `--root`, `--db`, `--dry-run`, `--apply`.

- [ ] **Step 1: Write failing migration tests**

```python
def test_apply_migration_updates_all_source_columns_and_preserves_tags(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases/question_bank.db"
    source = tmp_path / "outside/paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"paper")
    _seed_question_bank(db_path, source_file=str(source), tag_rows=3)

    report = apply_source_paper_migration(db_path=db_path, data_root=data_root)

    with connect(db_path) as conn:
        paper_source = conn.execute("SELECT source_file FROM papers").fetchone()[0]
        question_source = conn.execute("SELECT source_file FROM questions").fetchone()[0]
        preview_source = conn.execute("SELECT source_file FROM question_previews").fetchone()[0]
        tag_count = conn.execute("SELECT COUNT(*) FROM question_tags").fetchone()[0]
    assert paper_source == question_source == preview_source
    assert paper_source.startswith("question_bank/raw_papers/")
    assert tag_count == 3
    assert source.exists()
    assert report.migrated_sources == 1
    assert report.backup_path.is_file()


def test_missing_source_is_reported_without_database_update(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases/question_bank.db"
    missing = tmp_path / "missing.docx"
    _seed_question_bank(db_path, source_file=str(missing), tag_rows=1)

    report = apply_source_paper_migration(db_path=db_path, data_root=data_root)

    with connect(db_path) as conn:
        stored = conn.execute("SELECT source_file FROM questions").fetchone()[0]
    assert stored == str(missing)
    assert report.missing_sources == 1


def test_transaction_rolls_back_when_invariant_check_fails(tmp_path: Path, monkeypatch) -> None:
    data_root, db_path, source = _seed_single_source_fixture(tmp_path)
    monkeypatch.setattr(migration_service, "_invariants_match", lambda *_: False)
    with pytest.raises(RuntimeError, match="invariant"):
        apply_source_paper_migration(db_path=db_path, data_root=data_root)
    with connect(db_path) as conn:
        assert conn.execute("SELECT source_file FROM questions").fetchone()[0] == str(source)
```

The `_seed_question_bank` helper must initialize the real schema and insert one paper, one question, one preview, and deterministic tag rows.

- [ ] **Step 2: Run migration tests and confirm RED**

Run: `python -m pytest tests/test_source_paper_migration_service.py -q`

Expected: collection fails because the migration service does not exist.

- [ ] **Step 3: Implement migration planning, backup, invariants, and transaction**

Define the public result:

```python
@dataclass(frozen=True, slots=True)
class MigrationReport:
    scanned_sources: int
    migrated_sources: int
    reused_archives: int
    missing_sources: int
    failed_sources: int
    updated_rows: int
    backup_path: Path | None
    messages: tuple[str, ...]
```

Implementation requirements:

```python
SOURCE_COLUMNS = (
    ("papers", "source_file"),
    ("questions", "source_file"),
    ("question_previews", "source_file"),
)


def _snapshot_invariants(conn: sqlite3.Connection) -> tuple[int, tuple[int, ...], int, str]:
    question_ids = tuple(row[0] for row in conn.execute("SELECT id FROM questions ORDER BY id"))
    tag_rows = list(conn.execute(
        "SELECT question_id, tag_type, tag_value, confidence, source, model_name "
        "FROM question_tags ORDER BY id"
    ))
    digest = hashlib.sha256(repr(tag_rows).encode("utf-8")).hexdigest()
    return len(question_ids), question_ids, len(tag_rows), digest
```

Collect distinct source paths from all three columns, resolve existing legacy paths, archive each available external source, and build an old-to-new mapping before opening the write transaction. Create a consistent SQLite backup with `sqlite3.Connection.backup()` under `user_data/backups/`. Inside `BEGIN IMMEDIATE`, update exact old path values in all three tables, compare the invariant snapshot, and commit only when unchanged. Roll back on every exception.

- [ ] **Step 4: Implement the thin CLI**

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    data_root = root / "user_data"
    db_path = Path(args.db).resolve() if args.db else data_root / "databases/question_bank.db"
    report = (
        apply_source_paper_migration(db_path=db_path, data_root=data_root)
        if args.apply
        else plan_source_paper_migration(db_path=db_path, data_root=data_root)
    )
    print_report(report)
    return 1 if report.failed_sources else 0
```

Default behavior is dry-run even when neither flag is supplied. Output counts and paths only; never output document content, question content, tags, or API values.

- [ ] **Step 5: Run migration tests and CLI dry-run fixture test**

Run: `python -m pytest tests/test_source_paper_migration_service.py -q`

Expected: all pass, backups are created only during apply tests, and tag invariants are unchanged.

- [ ] **Step 6: Commit Task 6**

```powershell
git add question_bank/services/source_paper_migration_service.py update_tools/archive_question_bank_sources.py tests/test_source_paper_migration_service.py
git commit -m "feat: migrate source papers into portable archive"
```

### Task 7: Regression verification and production migration

**Files:**
- Modify only if verification exposes a defect: files from Tasks 1-6 and their matching tests.
- Do not stage: `user_data/**`.

**Interfaces:**
- Consumes: all feature interfaces and the production `question_bank.db`.
- Produces: migrated production source references, a database backup, and verification evidence.

- [ ] **Step 1: Run the focused regression suite**

Run:

```powershell
python -m pytest tests/test_source_paper_archive_service.py tests/test_question_bank_asset_path_service.py tests/test_question_bank_importer.py tests/test_grading_paper_archive_intake.py tests/test_question_bank_portable_consumers.py tests/test_source_paper_migration_service.py -q
```

Expected: all focused tests pass with zero warnings caused by the feature.

- [ ] **Step 2: Run the complete test suite**

Run: `python -m pytest -q`

Expected: exit code 0 and no failures.

- [ ] **Step 3: Create a full user-data backup including API profiles**

Run: `python update_tools/backup_data.py --reason before_update --include-api-keys`

Expected: exit code 0, a new ZIP path is printed, and the ZIP is copied outside the repository before migration.

- [ ] **Step 4: Dry-run the production source migration**

Run: `python update_tools/archive_question_bank_sources.py --root . --dry-run`

Expected baseline: 12 distinct external DOCX source paths are discovered; no database rows are changed; missing/failed counts are zero unless the external state changed after design approval.

- [ ] **Step 5: Capture production invariants without printing content**

Run a read-only SQLite check that prints only:

```text
questions=477
tags=4581
distinct_question_sources=12
```

If question/tag counts differ because the user has legitimately changed the live data, record the fresh values and use those as the apply baseline; do not force the historical numbers.

- [ ] **Step 6: Apply the production migration**

Run: `python update_tools/archive_question_bank_sources.py --root . --apply`

Expected: a timestamped `question_bank.db` backup is created, all available external sources are archived or deduplicated, and all matching database source columns are updated.

- [ ] **Step 7: Verify production invariants and portability**

Run the migration dry-run again and a read-only database check.

Expected:

- zero remaining existing external source paths;
- question count and exact ID set unchanged;
- tag count and tag digest unchanged;
- every stored source path resolves inside `user_data/question_bank/raw_papers`;
- all external originals still exist and retain their pre-migration hashes;
- `user_data/config/api_profiles.json` is unchanged;
- no AI usage log entries are added by migration.

- [ ] **Step 8: Inspect Git scope and commit only code fixes if any**

Run:

```powershell
git status --short
git diff --check
```

Do not stage `user_data`. If verification required code corrections, commit only source and tests:

```powershell
git add question_bank pages tests update_tools
git commit -m "fix: harden portable source paper migration"
```

Expected: production data changes remain local and unstaged; code commits contain no API profiles, databases, source papers, extracted images, or reports.
