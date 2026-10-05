"""Original-paper storage; scores, evidence and templates remain owned by their modules."""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock
from backend.repositories.access import as_grading_repositories
from backend.files.session_cleanup import _collect_other_session_references, _is_under, session_lifecycle_guard

_MEDIA_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class OriginalPagesCleared(RuntimeError):
    def __init__(self) -> None:
        super().__init__("这场考试的原卷已清理，不能再查看原卷、让 AI 批改或导出批注原卷。")


class ScanSourcesReleased(RuntimeError):
    def __init__(self) -> None:
        super().__init__("这场考试的扫描文件已释放，不能再重新扫描归卷。")


def receipt_path(data_root: Path, session_id: int) -> Path:
    path = Path(data_root) / "exams" / f"session_{int(session_id)}" / "originals_receipt.json"
    if not _is_under(path, Path(data_root)):
        raise ValueError("原卷目录不在本机数据目录内。")
    return path


def _receipt(data_root: Path, session_id: int) -> dict[str, Any]:
    path = receipt_path(data_root, session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1
                or value.get("session_id") != int(session_id)
                or value.get("state") not in {"scans_released", "clearing", "cleared"}
                or not isinstance(value.get("freed_bytes"), dict)
                or any(not isinstance(value["freed_bytes"].get(k), int)
                       or value["freed_bytes"][k] < 0 for k in ("scans", "pages", "annotations"))):
            raise ValueError("invalid receipt")
        return value
    except FileNotFoundError:
        state = "complete"
    except (OSError, ValueError, TypeError):
        state = "clearing"
    return {"version": 1, "session_id": int(session_id), "state": state,
            "scans_released_at": None, "cleared_at": None,
            "freed_bytes": {"scans": 0, "pages": 0, "annotations": 0},
            "deleted_files": 0, "kept_unrendered": 0}


def originals_state(data_root: Path | None, session_id: int) -> str:
    return _receipt(data_root, session_id)["state"] if data_root is not None else "complete"


def require_original_pages(data_root: Path | None, session_id: int) -> None:
    if originals_state(data_root, session_id) in {"clearing", "cleared"}:
        raise OriginalPagesCleared()


def _write_receipt(data_root: Path, session_id: int, value: dict[str, Any]) -> None:
    path = receipt_path(data_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".originals-{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _files(directory: Path, data_root: Path, *, on_unreadable: Callable[[], None] | None = None) -> Iterator[Path]:
    for path, _info, _parts in _file_entries(directory, Path(data_root).resolve(), on_unreadable=on_unreadable):
        yield path


def _file_entries(directory: Path, resolved_root: Path, *, parts: tuple[str, ...] = (),
                  on_unreadable: Callable[[], None] | None = None) -> Iterator[tuple[Path, os.stat_result, tuple[str, ...]]]:
    """Resolve directory boundaries and reject links and Windows reparse points."""
    if directory.is_symlink() or directory.is_junction():
        return
    directory = directory.resolve()
    if not directory.is_relative_to(resolved_root):
        return
    try:
        with os.scandir(directory) as listing:
            entries = list(listing)
    except FileNotFoundError:
        return
    except OSError:
        if on_unreadable is None:
            raise
        on_unreadable()
        return
    for entry in entries:
        path = Path(entry.path)
        try:
            info = entry.stat(follow_symlinks=False)
            attributes = getattr(info, "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        except OSError:
            if on_unreadable is None:
                raise
            on_unreadable()
            continue
        if entry.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            continue
        child_parts = (*parts, entry.name)
        if stat.S_ISDIR(info.st_mode):
            yield from _file_entries(path, resolved_root, parts=child_parts, on_unreadable=on_unreadable)
        elif stat.S_ISREG(info.st_mode):
            # The parent was resolved and checked; regular child files cannot
            # escape it. Destructive callers re-resolve each path before unlink.
            yield path, info, child_parts


def _candidates(db: Any, data_root: Path, session_id: int, *, shared_refs: set[Path] | None = None) -> tuple[dict[Path, str], set[Path], int]:
    db = as_grading_repositories(db)
    shared = shared_refs if shared_refs is not None else _collect_other_session_references(db, session_id, data_root)
    exam_dir = data_root / "exams" / f"session_{int(session_id)}"
    candidates: dict[Path, str] = {}
    release: set[Path] = set()
    kept = 0
    resolved_root = data_root.resolve()
    for path, _info, relative_parts in _file_entries(exam_dir, resolved_root):
        if path in shared or path.suffix.lower() not in _MEDIA_SUFFIXES:
            continue
        scan_pdf = (path.suffix.lower() == ".pdf" and len(relative_parts) == 4
                    and relative_parts[0] == "scan_batches" and relative_parts[2] == "files")
        candidates[path] = "scans" if scan_pdf else "pages"
        if scan_pdf:
            pages_dir = path.parent / "_pdf_pages" / path.stem
            try:
                manifest_path = pages_dir / "source_manifest.json"
                if manifest_path.is_symlink() or not _is_under(manifest_path, data_root):
                    raise ValueError("external manifest")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                count = manifest["page_count"]
                pages = [p for p in _files(pages_dir, data_root)
                         if p.parent == pages_dir.resolve() and p.match("page_*.jpg")]
                rendered = isinstance(count, int) and count > 0 and len(pages) == count
            except (OSError, ValueError, KeyError, TypeError):
                rendered = False
            if rendered:
                release.add(path)
            else:
                kept += 1
    template_dir = data_root / "templates" / f"session_{int(session_id)}" / "template-versions"
    for path in _files(template_dir, data_root):
        if path.name == "template_source_full_class.pdf" and path not in shared:
            candidates[path] = "scans"
            release.add(path)
    for directory, legacy in ((data_root / "annotated" / f"session_{int(session_id)}", True),
                              (data_root / "cache" / "annotated_pages" / f"session_{int(session_id)}", False)):
        for path in _files(directory, data_root):
            if path not in shared and (path.name.endswith("_annotated.jpg") if legacy else path.suffix.lower() == ".jpg"):
                candidates[path] = "annotations"
    return candidates, release, kept


def measure_session_originals(db: Any, data_root: Path, session_id: int, *, shared_refs: set[Path] | None = None) -> dict[str, int]:
    candidates, release, kept = _candidates(db, Path(data_root), session_id, shared_refs=shared_refs)
    sizes = {path: path.stat().st_size for path in candidates}
    scans = sum(size for path, size in sizes.items() if candidates[path] == "scans")
    pages = sum(size for path, size in sizes.items() if candidates[path] == "pages")
    annotations = sum(size for path, size in sizes.items() if candidates[path] == "annotations")
    return {"scan_bytes": scans, "page_bytes": pages, "annotation_bytes": annotations,
            "release_bytes": sum(sizes[p] for p in release), "clear_bytes": scans + pages + annotations,
            "kept_unrendered": kept}


def _delete_files(data_root: Path, session_id: int, receipt: dict[str, Any], candidates: dict[Path, str]) -> dict[str, int]:
    freed = count = 0
    for path, category in sorted(candidates.items(), key=lambda item: str(item[0])):
        if not _is_under(path, data_root) or path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        freed += size
        count += 1
        receipt["freed_bytes"][category] += size
        receipt["deleted_files"] += 1
        # Persist progress so an interrupted clear reports cumulative recovered space.
        _write_receipt(data_root, session_id, receipt)
    return {"freed_bytes": freed, "deleted_files": count}


def release_session_scans(db: Any, data_root: Path, session_id: int) -> dict[str, Any]:
    with session_lifecycle_guard(session_id):
        candidates, release, kept = _candidates(db, data_root, session_id)
        receipt = _receipt(data_root, session_id)
        if receipt["state"] == "complete" and release:
            receipt["state"] = "scans_released"
        if release:
            receipt["scans_released_at"] = datetime.now().isoformat()
        receipt["kept_unrendered"] = kept
        result = _delete_files(data_root, session_id, receipt, {p: candidates[p] for p in release})
        if release:
            _write_receipt(data_root, session_id, receipt)
        return {**result, "originals_state": receipt["state"], "kept_unrendered": kept}


def _remove_empty_dirs(directory: Path, data_root: Path) -> None:
    if not _is_under(directory, data_root) or directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        return
    for entry in os.scandir(directory):
        if entry.is_dir(follow_symlinks=False):
            _remove_empty_dirs(Path(entry.path), data_root)
    try:
        directory.rmdir()
    except OSError:
        pass  # JSON and shared references keep their containing directory.


def clear_session_originals(db: Any, data_root: Path, session_id: int, *, clear_crop_cache: Callable[[], int]) -> dict[str, Any]:
    db = as_grading_repositories(db)
    with session_lifecycle_guard(session_id):
        receipt = _receipt(data_root, session_id)
        if receipt["state"] == "cleared":
            return {"originals_state": "cleared", "freed_bytes": 0, "deleted_files": 0, "kept_unrendered": 0}
        lock_dir = data_root / "cache" / "annotated_pages" / f"session_{int(session_id)}"
        with get_answer_region_session_lock(lock_dir):
            receipt["state"] = "clearing"
            _write_receipt(data_root, session_id, receipt)
            db.reviews.clear_session_annotation_paths(session_id)
            candidates, _release, _kept = _candidates(db, data_root, session_id)
            result = _delete_files(data_root, session_id, receipt, candidates)
            _remove_empty_dirs(lock_dir, data_root)
            _remove_empty_dirs(data_root / "exams" / f"session_{int(session_id)}" / "scan_batches", data_root)
            clear_crop_cache()
            receipt["state"] = "cleared"
            receipt["cleared_at"] = datetime.now().isoformat()
            receipt["kept_unrendered"] = 0
            _write_receipt(data_root, session_id, receipt)
        return {**result, "originals_state": "cleared", "kept_unrendered": 0}


def storage_overview(data_root: Path) -> dict[str, Any]:
    categories = [("originals", "学生原卷"), ("annotations", "批注图"), ("question_bank", "题库资料"),
                  ("reports", "报告与导出"), ("backups", "备份"), ("databases", "数据库"), ("other", "其他")]
    totals = dict.fromkeys((key for key, _ in categories), 0)
    legacy_bytes = legacy_files = 0
    unreadable = 0
    def count_unreadable():
        nonlocal unreadable
        unreadable += 1
    for path, info, parts in _file_entries(data_root, Path(data_root).resolve(), on_unreadable=count_unreadable):
        head = parts[0]
        if head == "exams" or (head == "templates" and path.name == "template_source_full_class.pdf"):
            key = "originals"
        elif head == "annotated" or parts[:2] == ("cache", "annotated_pages"):
            key = "annotations"
        elif head in {"reports", "outputs"}:
            key = "reports"
        elif head in {"backups", "archives"}:
            key = "backups"
        else:
            key = head if head in {"question_bank", "databases"} else "other"
        size = info.st_size
        totals[key] += size
        if head == "annotated" and len(parts) == 3 and parts[1].startswith("session_") and path.name.endswith("_annotated.jpg"):
            legacy_bytes += size
            legacy_files += 1
    return {"total_bytes": sum(totals.values()), "unreadable_count": unreadable, "categories": [{"key": k, "label": label, "bytes": totals[k]} for k, label in categories],
            "legacy_annotations": {"bytes": legacy_bytes, "files": legacy_files}}


def clear_legacy_annotations(db: Any, data_root: Path) -> dict[str, int]:
    db = as_grading_repositories(db)
    freed = count = 0
    legacy_root = data_root / "annotated"
    session_ids = {int(session["id"]) for session in db.sessions.list_grading_sessions(include_deleted=True)}
    if _is_under(legacy_root, data_root) and not legacy_root.is_symlink() and not legacy_root.is_junction() and legacy_root.is_dir():
        with os.scandir(legacy_root) as directories:
            for entry in directories:
                match = re.fullmatch(r"session_([1-9][0-9]*)", entry.name)
                if match and entry.is_dir(follow_symlinks=False) and not Path(entry.path).is_junction():
                    session_ids.add(int(match[1]))
    for sid in sorted(session_ids):
        with session_lifecycle_guard(sid), get_answer_region_session_lock(data_root / "cache" / "annotated_pages" / f"session_{sid}"):
            shared = _collect_other_session_references(db, sid, data_root)
            db.reviews.clear_session_annotation_paths(sid, legacy_root=legacy_root)
            directory = legacy_root / f"session_{sid}"
            for path in _files(directory, data_root):
                if (path.parent == directory.resolve() and path.name.endswith("_annotated.jpg")
                        and path not in shared and _is_under(path, data_root) and not path.is_symlink()):
                    try:
                        size = path.stat().st_size
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    freed += size
                    count += 1
    return {"freed_bytes": freed, "deleted_files": count}
