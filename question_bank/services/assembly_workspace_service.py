from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping, TypeVar
from uuid import uuid4

from question_bank.services.assembly_basket_state import normalize_question_ids


_T = TypeVar("_T")

MAX_ASSEMBLY_QUESTIONS = 500
_LAYOUT_MODES = frozenset({"sequential", "grouped_by_type", "sections"})
_PREVIEW_MODES = frozenset({"student", "teacher"})
_SECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# AI 组卷会话：解答题子类的封闭取值，与 question_bank.parsers.type_detector 一致。
_ESSAY_SUBTYPES = frozenset({"画图", "计算", "证明"})
_DIFFICULTY_RATIO_KEYS = ("easy", "medium", "hard")


class AssemblyDraftConflict(RuntimeError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("Assembly draft has changed")
        self.current_revision = current_revision


class AiAssemblySessionConflict(RuntimeError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("AI assembly session has changed")
        self.current_revision = current_revision


class AssemblyRecordFileExpired(FileNotFoundError):
    pass


class AssemblyRecordFileForbidden(PermissionError):
    pass


class AssemblyRecordFileTypeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssemblySection:
    id: str
    title: str
    question_ids: tuple[int, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "question_ids": list(self.question_ids),
        }


@dataclass(frozen=True, slots=True)
class AssemblyDraft:
    basket_ids: tuple[int, ...]
    order_ids: tuple[int, ...]
    sections: tuple[AssemblySection, ...]
    title: str
    header_text: str
    include_answer: bool
    layout_mode: str
    preview_mode: str
    revision: str
    practice_rules: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "basket_ids": list(self.basket_ids),
            "order_ids": list(self.order_ids),
            "sections": [section.to_payload() for section in self.sections],
            "title": self.title,
            "header_text": self.header_text,
            "include_answer": self.include_answer,
            "layout_mode": self.layout_mode,
            "preview_mode": self.preview_mode,
            **({"practice_rules": True} if self.practice_rules else {}),
        }


@dataclass(frozen=True, slots=True)
class AssemblyRecord:
    id: str
    title: str
    question_ids: tuple[int, ...]
    order_ids: tuple[int, ...]
    sections: tuple[AssemblySection, ...]
    output_path: str
    export_format: str
    filename: str
    include_answer: bool
    created_at: str
    question_count: int
    question_type_summary: dict[str, int]
    # 组卷来源：None=人工组卷，"ai"=AI 组卷；旧记录缺字段按 None 处理。
    source: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "question_ids": list(self.question_ids),
            "order_ids": list(self.order_ids),
            "sections": [section.to_payload() for section in self.sections],
            "output_path": self.output_path,
            "export_format": self.export_format,
            "filename": self.filename,
            "include_answer": self.include_answer,
            "created_at": self.created_at,
            "question_count": self.question_count,
            "question_type_summary": dict(self.question_type_summary),
            "source": self.source,
        }

    def to_public_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "question_ids": list(self.question_ids),
            "order_ids": list(self.order_ids),
            "sections": [section.to_payload() for section in self.sections],
            "export_format": self.export_format,
            "filename": self.filename,
            "include_answer": self.include_answer,
            "created_at": self.created_at,
            "question_count": self.question_count,
            "question_type_summary": dict(self.question_type_summary),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class AssemblyRecordCreate:
    title: str
    draft: AssemblyDraft
    output_path: str | Path
    export_format: str
    question_type_summary: Mapping[str, int] | None = None
    created_at: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAssemblyFile:
    path: Path
    media_type: str


@dataclass(frozen=True, slots=True)
class AiAssemblySession:
    # payload 为落盘的完整规范化内容（含 schema_version 与 updated_at）；
    # revision 只由内容字段（不含 updated_at）派生，相同内容得到相同版本。
    payload: Mapping[str, object]
    revision: str


class AssemblyWorkspaceService:
    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self.workspace_root = self.data_root / "question_bank"
        self.draft_path = self.workspace_root / "assembly_draft.json"
        self.lock_path = self.workspace_root / ".assembly-workspace.lock"
        self.records_root = self.workspace_root / "assembly_records"
        self.exports_root = self.workspace_root / "assembly_exports"

    def load_draft(self) -> AssemblyDraft:
        return _normalize_draft(self._read_draft_payload())

    def normalize_draft(self, draft: Mapping[str, object]) -> AssemblyDraft:
        return _normalize_draft(draft)

    def save_draft(
        self,
        *,
        expected_revision: str,
        draft: Mapping[str, object],
    ) -> AssemblyDraft:
        normalized = _normalize_draft(draft)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            current = self.load_draft()
            if str(expected_revision) != current.revision:
                raise AssemblyDraftConflict(current.revision)
            _atomic_write_json(self.draft_path, normalized.to_payload())
        return normalized

    def add_questions(
        self,
        *,
        expected_revision: str,
        question_ids: object,
    ) -> AssemblyDraft:
        additions = normalize_question_ids(question_ids)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            current = self.load_draft()
            if str(expected_revision) != current.revision:
                raise AssemblyDraftConflict(current.revision)
            basket = list(current.basket_ids)
            for question_id in additions:
                if question_id not in basket:
                    basket.append(question_id)
            if len(basket) > MAX_ASSEMBLY_QUESTIONS:
                raise ValueError(
                    f"Assembly basket supports at most {MAX_ASSEMBLY_QUESTIONS} questions"
                )
            next_payload = current.to_payload()
            next_payload["basket_ids"] = basket
            next_payload["order_ids"] = [*current.order_ids, *additions]
            normalized = _normalize_draft(next_payload)
            _atomic_write_json(self.draft_path, normalized.to_payload())
        return normalized

    def clear_if_revision(self, expected_revision: str) -> bool:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            current = self.load_draft()
            if current.revision != str(expected_revision):
                return False
            _atomic_write_json(self.draft_path, _normalize_draft({}).to_payload())
        return True

    def create_record(self, create: AssemblyRecordCreate) -> AssemblyRecord:
        export_format = str(create.export_format or "").strip().casefold()
        if export_format not in {"docx", "markdown"}:
            raise ValueError("Assembly export format is not supported")
        output_path = Path(create.output_path)
        filename = output_path.name
        expected_suffix = ".docx" if export_format == "docx" else ".md"
        if output_path.suffix.casefold() != expected_suffix:
            raise ValueError("Assembly record file type does not match export format")
        record = AssemblyRecord(
            id=datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8],
            title=_clean_text(create.title, limit=120) or "未命名组卷",
            question_ids=create.draft.basket_ids,
            order_ids=create.draft.order_ids,
            sections=create.draft.sections,
            output_path=str(output_path),
            export_format=export_format,
            filename=filename,
            include_answer=create.draft.include_answer,
            created_at=(
                str(create.created_at).strip()
                if create.created_at
                else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ),
            question_count=len(create.draft.order_ids),
            question_type_summary=_clean_summary(create.question_type_summary),
            source=_clean_source(create.source),
        )
        self.records_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            _atomic_write_json(self._record_path(record.id), record.to_payload())
        return record

    def list_records(self, *, limit: int = 100) -> list[AssemblyRecord]:
        bounded_limit = max(1, min(int(limit), 100))
        try:
            paths = sorted(self.records_root.glob("*.json"), reverse=True)
        except OSError:
            return []
        records: list[AssemblyRecord] = []
        for path in paths:
            record = self._load_record(path)
            if record is not None:
                records.append(record)
            if len(records) >= bounded_limit:
                break
        return records

    def get_record(self, record_id: str) -> AssemblyRecord | None:
        path = self._record_path(record_id)
        return self._load_record(path)

    def delete_record(self, record_id: str) -> bool:
        path = self._record_path(record_id)
        with _exclusive_file_lock(self.lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                return False
        return True

    def resolve_record_file(self, record_id: str) -> ResolvedAssemblyFile:
        record = self.get_record(record_id)
        if record is None:
            raise AssemblyRecordFileExpired("Assembly record is unavailable")
        candidate = Path(record.output_path)
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise AssemblyRecordFileExpired("Assembly export file is unavailable") from exc
        root = self.exports_root.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AssemblyRecordFileForbidden(
                "Assembly export file is outside the allowed storage boundary"
            ) from exc
        media_types = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown",
        }
        media_type = media_types.get(resolved.suffix.casefold())
        if media_type is None:
            raise AssemblyRecordFileTypeError("Assembly export file type is not supported")
        if not resolved.is_file():
            raise AssemblyRecordFileExpired("Assembly export file is unavailable")
        return ResolvedAssemblyFile(path=resolved, media_type=media_type)

    def _read_draft_payload(self) -> Mapping[str, object]:
        try:
            payload = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _record_path(self, record_id: str) -> Path:
        safe_id = "".join(
            character
            for character in str(record_id or "")
            if character.isalnum() or character in "-_"
        )
        if not safe_id or safe_id != str(record_id or ""):
            return self.records_root / "__invalid__.json"
        return self.records_root / f"{safe_id}.json"

    def _load_record(self, path: Path) -> AssemblyRecord | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        question_ids = normalize_question_ids(payload.get("question_ids"))
        order_ids = [
            question_id
            for question_id in normalize_question_ids(
                payload.get("order_ids") or question_ids
            )
            if question_id in question_ids
        ]
        order_ids.extend(
            question_id for question_id in question_ids if question_id not in order_ids
        )
        sections = _normalize_sections(payload.get("sections"), order_ids)
        if sections:
            order_ids = [
                question_id
                for section in sections
                for question_id in section.question_ids
            ]
        output_path = str(payload.get("output_path") or "")
        export_format = str(payload.get("export_format") or "").strip().casefold()
        if export_format not in {"docx", "markdown"}:
            export_format = (
                "markdown" if Path(output_path).suffix.casefold() == ".md" else "docx"
            )
        filename = _clean_text(payload.get("filename"), limit=255) or Path(output_path).name
        return AssemblyRecord(
            id=str(payload.get("id") or path.stem),
            title=_clean_text(payload.get("title"), limit=120) or "未命名组卷",
            question_ids=tuple(question_ids),
            order_ids=tuple(order_ids),
            sections=tuple(sections),
            output_path=output_path,
            export_format=export_format,
            filename=filename,
            include_answer=bool(payload.get("include_answer")),
            created_at=_clean_text(payload.get("created_at"), limit=40),
            question_count=int(payload.get("question_count") or len(order_ids)),
            question_type_summary=_clean_summary(
                payload.get("question_type_summary")
                if isinstance(payload.get("question_type_summary"), Mapping)
                else None
            ),
            source=_clean_source(payload.get("source")),
        )


class AiAssemblySessionService:
    """AI 组卷会话持久化：与草稿同一工作区目录，单文件 + 乐观锁。"""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self.workspace_root = self.data_root / "question_bank"
        self.session_path = self.workspace_root / "ai_assembly_session.json"
        self.lock_path = self.workspace_root / ".ai-assembly-session.lock"

    def load_session(self) -> AiAssemblySession:
        raw = self._read_session_payload()
        content = _normalize_session_content(raw)
        return AiAssemblySession(
            payload={**content, "updated_at": _clean_text(raw.get("updated_at"), limit=40)},
            revision=_payload_revision(content),
        )

    def save_session(
        self,
        *,
        expected_revision: str,
        session: Mapping[str, object],
    ) -> AiAssemblySession:
        content = _normalize_session_content(session)
        normalized = AiAssemblySession(
            payload={**content, "updated_at": _now_text()},
            revision=_payload_revision(content),
        )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            current = self.load_session()
            if str(expected_revision) != current.revision:
                raise AiAssemblySessionConflict(current.revision)
            _atomic_write_json(self.session_path, dict(normalized.payload))
        return normalized

    def clear_session(self) -> AiAssemblySession:
        content = _normalize_session_content({})
        normalized = AiAssemblySession(
            payload={**content, "updated_at": _now_text()},
            revision=_payload_revision(content),
        )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with _exclusive_file_lock(self.lock_path):
            _atomic_write_json(self.session_path, dict(normalized.payload))
        return normalized

    def _read_session_payload(self) -> Mapping[str, object]:
        try:
            payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_session_content(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "params": _normalize_session_params(payload.get("params")),
        "spec": _json_clone_mapping(payload.get("spec")),
        "spec_model_name": _clean_text(payload.get("spec_model_name"), limit=200),
        "selections": _normalize_session_selections(payload.get("selections")),
        "locked_question_ids": _normalize_positive_ids(payload.get("locked_question_ids")),
        "locked_row_by_id": _normalize_session_locked_rows(payload.get("locked_row_by_id")),
        "gaps": _json_clone_list(payload.get("gaps")),
        "dedupe_enabled": bool(payload.get("dedupe_enabled", True)),
        "title": _clean_text(payload.get("title"), limit=120),
        "spec_job_id": _positive_int(payload.get("spec_job_id")),
    }


def _normalize_session_params(raw: object) -> dict[str, object]:
    params = raw if isinstance(raw, Mapping) else {}
    raw_ratio = params.get("difficulty_ratio")
    ratio_source = raw_ratio if isinstance(raw_ratio, Mapping) else {}
    ratio: dict[str, object] = {
        key: _bounded_int(ratio_source.get(key), minimum=0, maximum=100)
        for key in _DIFFICULTY_RATIO_KEYS
    }
    type_counts: dict[str, int] = {}
    raw_counts = params.get("type_counts")
    if isinstance(raw_counts, Mapping):
        for raw_key, raw_count in list(raw_counts.items())[:40]:
            key = _clean_text(raw_key, limit=40)
            count = _bounded_int(raw_count, minimum=1, maximum=500)
            if key and count is not None:
                type_counts[key] = count
    return {
        "template_paper_id": _positive_int(params.get("template_paper_id")),
        "scope_keys": _clean_string_list(params.get("scope_keys"), limit=200, item_limit=200),
        "difficulty_ratio": ratio,
        "type_counts": type_counts,
        "exam_types": _clean_string_list(params.get("exam_types"), limit=20, item_limit=80),
        "years": _normalize_years(params.get("years")),
        "free_text": _clean_text(params.get("free_text"), limit=2000),
        "essay_subtype": _essay_subtype(params.get("essay_subtype")),
    }


def _normalize_session_selections(raw: object) -> dict[str, list[int]]:
    if not isinstance(raw, Mapping):
        return {}
    selections: dict[str, list[int]] = {}
    for raw_row, raw_ids in list(raw.items())[:200]:
        row = _bounded_int(raw_row, minimum=0, maximum=9999)
        if row is None:
            continue
        selections[str(row)] = _normalize_positive_ids(raw_ids)
    return selections


def _normalize_session_locked_rows(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    locked: dict[str, int] = {}
    for raw_id, raw_row in list(raw.items())[:500]:
        question_id = _positive_int(raw_id)
        row = _bounded_int(raw_row, minimum=0, maximum=9999)
        if question_id is not None and row is not None:
            locked[str(question_id)] = row
    return locked


def _normalize_positive_ids(raw: object, *, limit: int = 500) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    output: list[int] = []
    for value in raw:
        number = _positive_int(value)
        if number is not None and number not in output:
            output.append(number)
        if len(output) >= limit:
            break
    return output


def _clean_string_list(raw: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    output: list[str] = []
    for value in raw:
        text = _clean_text(value, limit=item_limit)
        if text and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _normalize_years(raw: object) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    years: list[int] = []
    for value in raw:
        year = _bounded_int(value, minimum=1990, maximum=2100)
        if year is not None and year not in years:
            years.append(year)
        if len(years) >= 50:
            break
    return years


def _essay_subtype(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text in _ESSAY_SUBTYPES else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _json_clone_mapping(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("AI assembly session spec must be an object or null")
    return _json_clone(dict(value))


def _json_clone_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("AI assembly session gaps must be a list")
    return _json_clone(list(value))


def _json_clone(value: _T) -> _T:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("AI assembly session payload is not JSON serializable") from exc


def _normalize_draft(payload: Mapping[str, object]) -> AssemblyDraft:
    basket = normalize_question_ids(payload.get("basket_ids"))
    if len(basket) > MAX_ASSEMBLY_QUESTIONS:
        raise ValueError(f"Assembly basket supports at most {MAX_ASSEMBLY_QUESTIONS} questions")

    requested_order = normalize_question_ids(payload.get("order_ids"))
    ordered = [question_id for question_id in requested_order if question_id in basket]
    ordered.extend(question_id for question_id in basket if question_id not in ordered)

    sections = _normalize_sections(payload.get("sections"), ordered)
    if sections:
        ordered = [
            question_id
            for section in sections
            for question_id in section.question_ids
        ]

    layout_mode = str(payload.get("layout_mode") or "").strip()
    if layout_mode not in _LAYOUT_MODES:
        layout_mode = "sections" if sections else "sequential"
    if layout_mode == "sections" and not sections:
        layout_mode = "sequential"

    preview_mode = str(payload.get("preview_mode") or "").strip()
    if preview_mode not in _PREVIEW_MODES:
        preview_mode = "teacher"

    normalized_payload: dict[str, object] = {
        "schema_version": 2,
        "basket_ids": basket,
        "order_ids": ordered,
        "sections": [section.to_payload() for section in sections],
        "title": _clean_text(payload.get("title"), limit=120),
        "header_text": _clean_text(payload.get("header_text"), limit=200),
        "include_answer": bool(payload.get("include_answer", True)),
        "layout_mode": layout_mode,
        "preview_mode": preview_mode,
    }
    if payload.get("practice_rules") and basket:
        normalized_payload["practice_rules"] = True
    revision = _payload_revision(normalized_payload)
    return AssemblyDraft(
        basket_ids=tuple(basket),
        order_ids=tuple(ordered),
        sections=tuple(sections),
        title=str(normalized_payload["title"]),
        header_text=str(normalized_payload["header_text"]),
        include_answer=bool(normalized_payload["include_answer"]),
        layout_mode=layout_mode,
        preview_mode=preview_mode,
        revision=revision,
        practice_rules=bool(normalized_payload.get("practice_rules")),
    )


def _normalize_sections(
    raw_sections: object,
    ordered_ids: list[int],
) -> list[AssemblySection]:
    if not isinstance(raw_sections, (list, tuple)) or not raw_sections:
        return []
    allowed = set(ordered_ids)
    seen_questions: set[int] = set()
    seen_section_ids: set[str] = set()
    sections: list[AssemblySection] = []
    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, Mapping):
            continue
        title = _clean_text(raw.get("title"), limit=80)
        ids = [
            question_id
            for question_id in normalize_question_ids(raw.get("question_ids"))
            if question_id in allowed and question_id not in seen_questions
        ]
        if not title:
            continue
        section_id = _clean_section_id(raw.get("id"), index, seen_section_ids)
        seen_section_ids.add(section_id)
        seen_questions.update(ids)
        sections.append(
            AssemblySection(
                id=section_id,
                title=title,
                question_ids=tuple(ids),
            )
        )

    missing = [question_id for question_id in ordered_ids if question_id not in seen_questions]
    if missing:
        section_id = _clean_section_id("unassigned", len(sections), seen_section_ids)
        sections.append(
            AssemblySection(
                id=section_id,
                title="未分节",
                question_ids=tuple(missing),
            )
        )
    return sections


def _clean_section_id(
    value: object,
    index: int,
    seen: set[str],
) -> str:
    candidate = str(value or "").strip()
    if not _SECTION_ID_PATTERN.fullmatch(candidate) or candidate in seen:
        candidate = f"section-{index + 1}"
    suffix = 2
    unique = candidate
    while unique in seen:
        unique = f"{candidate}-{suffix}"
        suffix += 1
    return unique


def _clean_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _clean_source(value: object) -> str | None:
    """来源标记只接受已定义取值；旧记录缺字段或未知取值按 None 处理。"""

    text = str(value or "").strip().casefold()
    return text if text in {"ai"} else None


def _clean_summary(value: Mapping[str, int] | None) -> dict[str, int]:
    summary: dict[str, int] = {}
    for raw_key, raw_count in (value or {}).items():
        key = _clean_text(raw_key, limit=40)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if key and count > 0:
            summary[key] = count
    return summary


def _payload_revision(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "AiAssemblySession",
    "AiAssemblySessionConflict",
    "AiAssemblySessionService",
    "AssemblyDraft",
    "AssemblyDraftConflict",
    "AssemblyRecord",
    "AssemblyRecordCreate",
    "AssemblyRecordFileExpired",
    "AssemblyRecordFileForbidden",
    "AssemblyRecordFileTypeError",
    "AssemblySection",
    "AssemblyWorkspaceService",
    "MAX_ASSEMBLY_QUESTIONS",
    "ResolvedAssemblyFile",
]
