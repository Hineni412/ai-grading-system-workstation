from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from db_manager import DBManager
from path_manager import resolve_stored_file_path
from question_bank.database.schema import connect, initialize_database
from question_bank.services.grading_paper_intake_service import (
    archive_uploaded_grading_paper,
    intake_grading_paper_to_question_bank,
)
from question_bank.services.skill_link_service import SkillLinkService
from question_bank.services.source_question_link_service import SourceQuestionLinkService
from session_manager import iter_effective_rubric_items


@dataclass(frozen=True, slots=True)
class GradingPaperWorkflowStatus:
    session_id: int
    session_name: str
    state: str
    source_available: bool
    source_paper_path: str
    source_paper_sha256: str
    source_question_total: int
    confirmed_source_links: int
    suggested_source_links: int
    bank_question_total: int
    bank_questions_resolved: int
    assessment_total: int
    assessment_resolved: int
    error: str = ""

    @property
    def missing_source_links(self) -> int:
        return max(0, self.source_question_total - self.confirmed_source_links)

    @property
    def missing_assessment_items(self) -> int:
        return max(0, self.assessment_total - self.assessment_resolved)

    def details(self) -> dict[str, object]:
        return asdict(self)


class GradingPaperSkillWorkflowService:
    def __init__(
        self,
        *,
        grading_db_path: str | Path,
        question_bank_db_path: str | Path,
        data_root: str | Path,
    ) -> None:
        self.grading_db = DBManager(Path(grading_db_path))
        self.grading_db.initialize()
        self.question_bank_db_path = Path(question_bank_db_path)
        initialize_database(self.question_bank_db_path)
        self.data_root = Path(data_root).expanduser().resolve()

    def status(self, session_id: int) -> GradingPaperWorkflowStatus:
        session = self.grading_db.get_grading_session(int(session_id))
        if session is None:
            raise KeyError(f"grading session not found: {session_id}")
        persisted_state = str(session.get("question_bank_sync_state") or "not_started")
        persisted_error = str(session.get("question_bank_sync_error") or "")
        result = self._compute_status(
            int(session_id),
            persisted_state=persisted_state,
            persisted_error=persisted_error,
        )
        if persisted_state == "running" and result.state == "failed":
            self.grading_db.update_question_bank_sync_state(
                int(session_id),
                state="failed",
                details=result.details(),
                error=result.error,
            )
        return result

    def save_source(
        self,
        session_id: int,
        *,
        filename: str,
        content: bytes,
    ) -> GradingPaperWorkflowStatus:
        if self.grading_db.get_grading_session(int(session_id)) is None:
            raise KeyError(f"grading session not found: {session_id}")
        archived = archive_uploaded_grading_paper(
            filename=filename,
            content=content,
            data_root=self.data_root,
            raw_papers_dir=self.data_root / "question_bank" / "raw_papers",
        )
        self.grading_db.bind_grading_session_source(
            int(session_id),
            source_paper_path=archived.stored_path,
            source_paper_sha256=archived.sha256,
        )
        return self.status(int(session_id))

    def run(
        self,
        session_id: int,
        *,
        ai_service: Any,
        max_workers: int,
        requests_per_minute: int,
        progress_callback: Callable[[int, int, int, Any], None] | None = None,
    ) -> GradingPaperWorkflowStatus:
        _session, rubric, source_path = self._required_inputs(int(session_id))
        self.grading_db.update_question_bank_sync_state(int(session_id), state="running")
        try:
            intake_grading_paper_to_question_bank(
                source_file=source_path,
                db_path=self.question_bank_db_path,
                run_ai_tagging=True,
                ai_service=ai_service,
                tagging_max_workers=int(max_workers),
                tagging_requests_per_minute=int(requests_per_minute),
                tagging_progress_callback=progress_callback,
                grading_session_id=int(session_id),
                grading_source_questions=list(rubric.get("questions") or []),
                data_root=self.data_root,
                raw_papers_dir=self.data_root / "question_bank" / "raw_papers",
            )
            SkillLinkService(self.question_bank_db_path).resolve_rubric(
                str(session_id),
                rubric,
                preserve_existing_measured=True,
            )
        except Exception as exc:  # noqa: BLE001
            actual = self._compute_status(
                int(session_id),
                persisted_state="failed",
                persisted_error=str(exc),
            )
            fallback = (
                "partial"
                if actual.confirmed_source_links
                or actual.bank_question_total
                or actual.bank_questions_resolved
                else "failed"
            )
            self.grading_db.update_question_bank_sync_state(
                int(session_id),
                state=fallback,
                details=actual.details(),
                error=str(exc),
            )
            return self.status(int(session_id))

        actual = self._compute_status(
            int(session_id),
            persisted_state="partial",
            persisted_error="题库处理未形成完整关联。",
        )
        self.grading_db.update_question_bank_sync_state(
            int(session_id),
            state=actual.state,
            details=actual.details(),
            error=actual.error or None,
        )
        return self.status(int(session_id))

    def _required_inputs(
        self,
        session_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        session = self.grading_db.get_grading_session(int(session_id))
        if session is None:
            raise KeyError(f"grading session not found: {session_id}")
        rubric_path = resolve_stored_file_path(
            session.get("rubric_path"),
            data_root=self.data_root,
        )
        if not rubric_path.is_file():
            raise FileNotFoundError(rubric_path)
        source_path = resolve_stored_file_path(
            session.get("source_paper_path"),
            data_root=self.data_root,
        )
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
        rubric = payload.get("rubric") if isinstance(payload.get("rubric"), Mapping) else payload
        if not isinstance(rubric, dict):
            raise ValueError("grading rubric must be an object")
        return session, rubric, source_path

    def _compute_status(
        self,
        session_id: int,
        *,
        persisted_state: str,
        persisted_error: str,
    ) -> GradingPaperWorkflowStatus:
        session = self.grading_db.get_grading_session(int(session_id))
        if session is None:
            raise KeyError(f"grading session not found: {session_id}")
        source_value = str(session.get("source_paper_path") or "").strip()
        source_path = resolve_stored_file_path(source_value, data_root=self.data_root)
        source_available = bool(source_value and source_path.is_file())
        rubric = _read_rubric(session.get("rubric_path"), data_root=self.data_root)
        source_question_ids = _source_question_ids(rubric)
        assessment_refs = {
            item_ref for item_ref, _question, _item in iter_effective_rubric_items(rubric)
        }

        source_links = SourceQuestionLinkService(self.question_bank_db_path).list_links(session_id)
        current_links = [
            item
            for item in source_links
            if str(item.get("source_question_id") or "") in source_question_ids
        ]
        confirmed_links = [item for item in current_links if item.get("status") == "confirmed"]
        suggested_links = [item for item in current_links if item.get("status") == "suggested"]

        bank_question_total, bank_questions_resolved = self._bank_question_coverage(source_value)
        assessment_links = SkillLinkService(self.question_bank_db_path).assessment_links_for_sessions(
            [str(session_id)]
        )
        assessment_resolved = len(
            {
                str(item.get("source_question_id") or "")
                for item in assessment_links
                if str(item.get("role") or "") == "measured"
                and str(item.get("source_question_id") or "") in assessment_refs
            }
        )
        state, error = _derive_state(
            source_available=source_available,
            source_total=len(source_question_ids),
            confirmed_links=len(confirmed_links),
            bank_total=bank_question_total,
            bank_resolved=bank_questions_resolved,
            assessment_total=len(assessment_refs),
            assessment_resolved=assessment_resolved,
            persisted_state=persisted_state,
            persisted_error=persisted_error,
        )
        if source_value and not source_available:
            error = error or "原始试卷文件不存在，请重新上传。"
        return GradingPaperWorkflowStatus(
            session_id=int(session_id),
            session_name=str(session.get("session_name") or ""),
            state=state,
            source_available=source_available,
            source_paper_path=source_value,
            source_paper_sha256=str(session.get("source_paper_sha256") or ""),
            source_question_total=len(source_question_ids),
            confirmed_source_links=len(confirmed_links),
            suggested_source_links=len(suggested_links),
            bank_question_total=bank_question_total,
            bank_questions_resolved=bank_questions_resolved,
            assessment_total=len(assessment_refs),
            assessment_resolved=assessment_resolved,
            error=error,
        )

    def _bank_question_coverage(self, source_value: str) -> tuple[int, int]:
        if not source_value:
            return 0, 0
        with connect(self.question_bank_db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM question_skill_links qsl
                            WHERE qsl.question_id = q.id
                              AND qsl.role = 'measured' AND qsl.status = 'resolved'
                        ) THEN 1 ELSE 0 END
                    ) AS resolved
                FROM questions q
                WHERE COALESCE(q.is_deleted, 0) = 0 AND q.source_file = ?
                """,
                (source_value,),
            ).fetchone()
        return int(row["total"] or 0), int(row["resolved"] or 0)


def _read_rubric(value: object, *, data_root: Path) -> dict[str, Any]:
    path = resolve_stored_file_path(value, data_root=data_root)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rubric = payload.get("rubric") if isinstance(payload, dict) and isinstance(payload.get("rubric"), Mapping) else payload
    return dict(rubric) if isinstance(rubric, Mapping) else {}


def _source_question_ids(rubric: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    questions = rubric.get("questions")
    for index, question in enumerate(questions if isinstance(questions, list) else [], start=1):
        if not isinstance(question, Mapping):
            continue
        source_id = str(
            question.get("question_id")
            or question.get("id")
            or question.get("number")
            or f"Q{index}"
        ).strip()
        if source_id:
            result.add(source_id)
    return result


def _derive_state(
    *,
    source_available: bool,
    source_total: int,
    confirmed_links: int,
    bank_total: int,
    bank_resolved: int,
    assessment_total: int,
    assessment_resolved: int,
    persisted_state: str,
    persisted_error: str,
) -> tuple[str, str]:
    ready = (
        source_available
        and source_total > 0
        and confirmed_links == source_total
        and bank_total >= confirmed_links
        and bank_resolved == bank_total
        and assessment_total > 0
        and assessment_resolved == assessment_total
    )
    if ready:
        return "ready", ""
    progress = confirmed_links > 0 or bank_total > 0 or bank_resolved > 0
    if progress:
        return "partial", persisted_error
    if persisted_state == "running":
        return "failed", "上次题库处理被中断，请重试。"
    if persisted_state in {"ready", "partial", "failed"}:
        return "failed", persisted_error or "题库处理未形成可用关联。"
    return "not_started", ""


__all__ = ["GradingPaperSkillWorkflowService", "GradingPaperWorkflowStatus"]
