"""Import-kind session identity and score-import storage layout.

Import kind is stored as ``templates/session_<id>/import_context.json`` so no
database migration is needed; a missing file means a normal scan session.
Uploaded workbooks, the import receipt, and the originals receipt all live
under ``exams/session_<id>/`` like other per-session exam data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

KIND_SCAN = "scan"
KIND_ZHIXUE_IMPORT = "zhixue_import"

IMPORT_CONTEXT_FILENAME = "import_context.json"


def session_template_dir(templates_dir: Path, session_id: int) -> Path:
    return Path(templates_dir) / f"session_{int(session_id)}"


def import_context_path(templates_dir: Path, session_id: int) -> Path:
    return session_template_dir(templates_dir, session_id) / IMPORT_CONTEXT_FILENAME


def read_import_context(templates_dir: Path, session_id: int) -> dict[str, Any] | None:
    path = import_context_path(templates_dir, session_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("kind") or "") != KIND_ZHIXUE_IMPORT:
        return None
    return payload


def session_kind(templates_dir: Path, session_id: int) -> str:
    context = read_import_context(templates_dir, session_id)
    return KIND_ZHIXUE_IMPORT if context is not None else KIND_SCAN


def session_exam_date(templates_dir: Path, session_id: int) -> str | None:
    context = read_import_context(templates_dir, session_id)
    if context is None:
        return None
    value = str(context.get("exam_date") or "").strip()
    return value or None


def is_import_session_data_root(data_root: Path | None, session_id: int) -> bool:
    """Job-side check: derive the templates dir from the data root."""
    if data_root is None:
        return False
    return (
        session_kind(Path(data_root) / "templates", int(session_id))
        == KIND_ZHIXUE_IMPORT
    )


def write_import_context(templates_dir: Path, session_id: int, *, exam_date: str) -> Path:
    path = import_context_path(templates_dir, session_id)
    _atomic_write_json(
        path,
        {
            "kind": KIND_ZHIXUE_IMPORT,
            "exam_date": str(exam_date),
        },
    )
    return path


def score_import_dir(data_root: Path, session_id: int) -> Path:
    return Path(data_root) / "exams" / f"session_{int(session_id)}" / "score_import"


def uploads_dir(data_root: Path, session_id: int) -> Path:
    return score_import_dir(data_root, session_id) / "uploads"


def receipt_path(data_root: Path, session_id: int) -> Path:
    return score_import_dir(data_root, session_id) / "receipt.json"


def read_receipt(data_root: Path, session_id: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(receipt_path(data_root, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def upload_path(data_root: Path, session_id: int, upload_id: str) -> Path:
    return uploads_dir(data_root, session_id) / f"{upload_id}.xlsx"


def upload_meta_path(data_root: Path, session_id: int, upload_id: str) -> Path:
    return uploads_dir(data_root, session_id) / f"{upload_id}.json"


def read_upload_meta(data_root: Path, session_id: int, upload_id: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(upload_meta_path(data_root, session_id, upload_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def list_upload_metas(data_root: Path, session_id: int) -> list[dict[str, Any]]:
    directory = uploads_dir(data_root, session_id)
    try:
        names = sorted(directory.iterdir())
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for entry in names:
        if entry.suffix != ".json":
            continue
        meta = read_upload_meta(data_root, session_id, entry.stem)
        if meta is not None:
            result.append(meta)
    return result


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".score-import-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
