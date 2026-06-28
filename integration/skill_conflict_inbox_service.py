from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from db_manager import DBManager
from path_manager import resolve_stored_file_path
from question_bank.database.schema import connect, initialize_database
from question_bank.services.skill_catalog_service import SkillCatalogService
from session_manager import iter_effective_rubric_items


@dataclass(frozen=True, slots=True)
class ConflictSourceGroup:
    source_key: str
    source_type: str
    source_label: str
    context: str
    conflicts: tuple[dict[str, Any], ...]
    has_resolved_measured: bool
    is_historical: bool


@dataclass(frozen=True, slots=True)
class ConflictInboxSummary:
    blocking: tuple[ConflictSourceGroup, ...]
    advisory: tuple[ConflictSourceGroup, ...]
    historical: tuple[ConflictSourceGroup, ...]
    coverage: dict[str, dict[str, int]]
    warnings: tuple[str, ...] = ()


class SkillConflictInboxService:
    def __init__(
        self,
        question_bank_db_path: str | Path,
        grading_db_path: str | Path,
    ) -> None:
        self.question_bank_db_path = Path(question_bank_db_path)
        self.grading_db_path = Path(grading_db_path)
        initialize_database(self.question_bank_db_path)
        self.grading_db = DBManager(self.grading_db_path)
        self.grading_db.initialize()
        self.data_root = _infer_data_root(self.grading_db_path)

    def summary(self) -> ConflictInboxSummary:
        conflicts = SkillCatalogService(
            self.question_bank_db_path
        ).list_open_conflicts(limit=1_000_000)
        question_rows, resolved_questions, assessment_rows = self._load_skill_sources()
        sessions = {
            int(item["id"]): item
            for item in self.grading_db.list_grading_sessions(include_deleted=True)
        }
        resolved_assessments = {
            (int(item["grading_session_id"]), str(item["source_question_id"]))
            for item in assessment_rows
            if item["status"] == "resolved" and item["role"] == "measured"
        }

        grouped: dict[str, list[dict[str, Any]]] = {}
        metadata: dict[str, tuple[str, object]] = {}
        for conflict in conflicts:
            source_type = str(conflict.get("source_type") or "")
            source_ref = str(conflict.get("source_ref") or "")
            if source_type == "question_bank_item":
                question_id = _question_source_id(source_ref)
                key = (
                    f"question:{question_id}"
                    if question_id is not None
                    else f"question:unknown:{source_ref}"
                )
                metadata[key] = (source_type, question_id)
            elif source_type == "assessment_item":
                assessment = _assessment_source(source_ref)
                key = (
                    f"assessment:{assessment[0]}:{assessment[1]}"
                    if assessment is not None
                    else f"assessment:unknown:{source_ref}"
                )
                metadata[key] = (source_type, assessment)
            else:
                key = f"legacy:{source_ref}"
                metadata[key] = (source_type or "legacy_term", source_ref)
            grouped.setdefault(key, []).append(conflict)

        blocking: list[ConflictSourceGroup] = []
        advisory: list[ConflictSourceGroup] = []
        historical: list[ConflictSourceGroup] = []
        for source_key, source_conflicts in grouped.items():
            source_type, identity = metadata[source_key]
            group = self._make_group(
                source_key=source_key,
                source_type=source_type,
                identity=identity,
                conflicts=source_conflicts,
                question_rows=question_rows,
                resolved_questions=resolved_questions,
                resolved_assessments=resolved_assessments,
                sessions=sessions,
            )
            if group.is_historical:
                historical.append(group)
            elif group.has_resolved_measured:
                advisory.append(group)
            else:
                blocking.append(group)

        for values in (blocking, advisory, historical):
            values.sort(key=_group_sort_key)

        assessment_keys, warnings = self._active_assessment_keys(
            sessions=sessions,
            assessment_rows=assessment_rows,
            conflicts=conflicts,
        )
        active_question_ids = {
            question_id
            for question_id, row in question_rows.items()
            if not bool(row.get("is_deleted"))
        }
        resolved_active_questions = active_question_ids & resolved_questions
        resolved_active_assessments = assessment_keys & resolved_assessments
        advisory_question_ids = {
            int(group.source_key.partition(":")[2])
            for group in advisory
            if group.source_key.startswith("question:")
            and group.source_key.partition(":")[2].isdigit()
        }
        advisory_assessments = {
            parsed
            for group in advisory
            if (parsed := _assessment_group_key(group.source_key)) is not None
        }
        coverage = {
            "question_bank": {
                "total": len(active_question_ids),
                "resolved": len(resolved_active_questions),
                "blocking": len(active_question_ids - resolved_active_questions),
                "advisory": len(advisory_question_ids & resolved_active_questions),
            },
            "assessment": {
                "total": len(assessment_keys),
                "resolved": len(resolved_active_assessments),
                "blocking": len(assessment_keys - resolved_active_assessments),
                "advisory": len(advisory_assessments & resolved_active_assessments),
            },
        }
        return ConflictInboxSummary(
            blocking=tuple(blocking),
            advisory=tuple(advisory),
            historical=tuple(historical),
            coverage=coverage,
            warnings=tuple(warnings),
        )

    def _load_skill_sources(
        self,
    ) -> tuple[dict[int, dict[str, Any]], set[int], list[dict[str, Any]]]:
        with connect(self.question_bank_db_path) as conn:
            question_rows = {
                int(row["id"]): dict(row)
                for row in conn.execute(
                    """
                    SELECT id, question_number, question_text, source_file, is_deleted
                    FROM questions
                    """
                ).fetchall()
            }
            resolved_questions = {
                int(row["question_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT question_id FROM question_skill_links
                    WHERE role = 'measured' AND status = 'resolved'
                    """
                ).fetchall()
            }
            assessment_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT grading_session_id, source_question_id, role, status
                    FROM assessment_item_skills
                    """
                ).fetchall()
            ]
        return question_rows, resolved_questions, assessment_rows

    def _make_group(
        self,
        *,
        source_key: str,
        source_type: str,
        identity: object,
        conflicts: list[dict[str, Any]],
        question_rows: dict[int, dict[str, Any]],
        resolved_questions: set[int],
        resolved_assessments: set[tuple[int, str]],
        sessions: dict[int, dict[str, Any]],
    ) -> ConflictSourceGroup:
        if source_type == "question_bank_item" and isinstance(identity, int):
            question = question_rows.get(identity)
            historical = question is None or bool(question.get("is_deleted"))
            number = str((question or {}).get("question_number") or identity)
            return ConflictSourceGroup(
                source_key=source_key,
                source_type=source_type,
                source_label=f"题库第 {number} 题",
                context=str((question or {}).get("question_text") or "题目记录已不存在"),
                conflicts=tuple(conflicts),
                has_resolved_measured=identity in resolved_questions,
                is_historical=historical,
            )
        if source_type == "assessment_item" and isinstance(identity, tuple):
            session_id, item_ref = identity
            session = sessions.get(int(session_id))
            historical = session is None or bool(session.get("is_deleted"))
            session_name = str((session or {}).get("session_name") or f"试卷 {session_id}")
            return ConflictSourceGroup(
                source_key=source_key,
                source_type=source_type,
                source_label=f"{session_name} · {item_ref}",
                context="评分规则中的题目或小题",
                conflicts=tuple(conflicts),
                has_resolved_measured=(int(session_id), str(item_ref)) in resolved_assessments,
                is_historical=historical,
            )
        return ConflictSourceGroup(
            source_key=source_key,
            source_type=source_type,
            source_label="历史知识点记录",
            context=str(identity or "来源已无法识别"),
            conflicts=tuple(conflicts),
            has_resolved_measured=False,
            is_historical=True,
        )

    def _active_assessment_keys(
        self,
        *,
        sessions: dict[int, dict[str, Any]],
        assessment_rows: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
    ) -> tuple[set[tuple[int, str]], list[str]]:
        known_by_session: dict[int, set[tuple[int, str]]] = {}
        for item in assessment_rows:
            try:
                session_id = int(item["grading_session_id"])
            except (TypeError, ValueError):
                continue
            known_by_session.setdefault(session_id, set()).add(
                (session_id, str(item["source_question_id"]))
            )
        for conflict in conflicts:
            if conflict.get("source_type") != "assessment_item":
                continue
            parsed = _assessment_source(conflict.get("source_ref"))
            if parsed is not None:
                known_by_session.setdefault(parsed[0], set()).add(parsed)

        result: set[tuple[int, str]] = set()
        warnings: list[str] = []
        for session_id, session in sessions.items():
            if bool(session.get("is_deleted")):
                continue
            rubric_path = resolve_stored_file_path(
                session.get("rubric_path"),
                data_root=self.data_root,
            )
            try:
                payload = json.loads(rubric_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result.update(known_by_session.get(session_id, set()))
                warnings.append(
                    f"{session.get('session_name') or f'试卷 {session_id}'} 的评分规则文件不可读取，"
                    "覆盖统计已按现有技能关联和冲突记录回退。"
                )
                continue
            result.update(
                (session_id, item_ref)
                for item_ref, _question, _item in iter_effective_rubric_items(payload)
            )
        return result, warnings


def _question_source_id(source_ref: object) -> int | None:
    for value in reversed(str(source_ref or "").split(":")):
        try:
            question_id = int(value)
        except ValueError:
            continue
        return question_id if question_id > 0 else None
    return None


def _assessment_source(source_ref: object) -> tuple[int, str] | None:
    left, separator, right = str(source_ref or "").partition(":")
    if not separator or not right:
        return None
    try:
        session_id = int(left)
    except ValueError:
        return None
    return (session_id, right) if session_id > 0 else None


def _assessment_group_key(source_key: str) -> tuple[int, str] | None:
    if not source_key.startswith("assessment:"):
        return None
    return _assessment_source(source_key.removeprefix("assessment:"))


def _group_sort_key(group: ConflictSourceGroup) -> tuple[int, int, str, int]:
    first_conflict_id = min(int(item["id"]) for item in group.conflicts)
    if group.source_key.startswith("assessment:"):
        parsed = _assessment_group_key(group.source_key)
        return (0, parsed[0] if parsed else 0, parsed[1] if parsed else "", first_conflict_id)
    if group.source_key.startswith("question:"):
        question_id = _question_source_id(group.source_key)
        return (1, question_id or 0, "", first_conflict_id)
    return (2, 0, group.source_key, first_conflict_id)


def _infer_data_root(grading_db_path: Path) -> Path:
    resolved = grading_db_path.expanduser().resolve()
    return resolved.parent.parent if resolved.parent.name == "databases" else resolved.parent


__all__ = [
    "ConflictInboxSummary",
    "ConflictSourceGroup",
    "SkillConflictInboxService",
]
