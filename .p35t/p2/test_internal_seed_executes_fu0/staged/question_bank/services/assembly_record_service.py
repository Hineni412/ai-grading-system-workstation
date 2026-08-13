from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from question_bank.database.paths import project_data_root


@dataclass(frozen=True)
class AssemblyRecord:
    id: str
    title: str
    question_ids: list[int]
    output_path: str
    include_answer: bool
    created_at: str
    question_count: int = 0
    question_type_summary: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AssemblyRecordCreate:
    title: str
    question_ids: list[int]
    output_path: str
    include_answer: bool
    question_count: int = 0
    question_type_summary: dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def create_assembly_record(record: AssemblyRecordCreate, root: str | Path | None = None) -> AssemblyRecord:
    output = AssemblyRecord(
        id=datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8],
        title=str(record.title or "").strip() or "未命名组卷",
        question_ids=_dedupe_ints(record.question_ids),
        output_path=str(record.output_path or ""),
        include_answer=bool(record.include_answer),
        created_at=record.created_at,
        question_count=int(record.question_count or len(record.question_ids)),
        question_type_summary=dict(record.question_type_summary or {}),
    )
    _record_path(output.id, root).write_text(
        json.dumps(output.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def list_assembly_records(root: str | Path | None = None) -> list[AssemblyRecord]:
    records: list[AssemblyRecord] = []
    for path in sorted(_records_root(root).glob("*.json"), reverse=True):
        record = get_assembly_record(path.stem, root)
        if record is not None:
            records.append(record)
    return records


def get_assembly_record(record_id: str, root: str | Path | None = None) -> AssemblyRecord | None:
    path = _record_path(record_id, root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    
    qids = _dedupe_ints(payload.get("question_ids") or [])
    qcount = int(payload.get("question_count") or len(qids))
    qsummary = dict(payload.get("question_type_summary") or {})
    
    return AssemblyRecord(
        id=str(payload.get("id") or path.stem),
        title=str(payload.get("title") or "未命名组卷"),
        question_ids=qids,
        output_path=str(payload.get("output_path") or ""),
        include_answer=bool(payload.get("include_answer")),
        created_at=str(payload.get("created_at") or ""),
        question_count=qcount,
        question_type_summary=qsummary,
    )


def delete_assembly_record(record_id: str, root: str | Path | None = None) -> bool:
    path = _record_path(record_id, root)
    if not path.exists():
        return False
    path.unlink()
    return True


def _records_root(root: str | Path | None = None) -> Path:
    path = Path(root) if root is not None else project_data_root() / "question_bank" / "assembly_records"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(record_id: str, root: str | Path | None = None) -> Path:
    safe_id = "".join(ch for ch in str(record_id or "") if ch.isalnum() or ch in "-_")
    return _records_root(root) / f"{safe_id}.json"


def _dedupe_ints(values: object) -> list[int]:
    output: list[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in output:
            output.append(item)
    return output


__all__ = [
    "AssemblyRecord",
    "AssemblyRecordCreate",
    "create_assembly_record",
    "delete_assembly_record",
    "get_assembly_record",
    "list_assembly_records",
]
