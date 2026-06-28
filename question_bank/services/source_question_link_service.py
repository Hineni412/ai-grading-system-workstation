from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.services.similarity_service import text_similarity


SUGGESTION_THRESHOLD = 0.82


class SourceQuestionLinkService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_database(self) -> None:
        initialize_database(self.db_path)

    def confirm_link(
        self,
        *,
        grading_session_id: str | int,
        source_question_id: str | int,
        bank_question_id: int,
        link_method: str,
        reviewed_by: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_link(
                conn,
                grading_session_id=grading_session_id,
                source_question_id=source_question_id,
                bank_question_id=bank_question_id,
                link_method=link_method,
                confidence=1.0,
                status="confirmed",
                reviewed_by=reviewed_by,
                evidence=evidence,
            )

    def suggest_link(
        self,
        *,
        grading_session_id: str | int,
        source_question_id: str | int,
        bank_question_id: int,
        confidence: float,
        link_method: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_link(
                conn,
                grading_session_id=grading_session_id,
                source_question_id=source_question_id,
                bank_question_id=bank_question_id,
                link_method=link_method,
                confidence=min(0.99, max(0.0, float(confidence))),
                status="suggested",
                evidence=evidence,
            )

    def reject_link(
        self,
        *,
        grading_session_id: str | int,
        source_question_id: str | int,
        bank_question_id: int,
        link_method: str = "manual",
        reviewed_by: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_link(
                conn,
                grading_session_id=grading_session_id,
                source_question_id=source_question_id,
                bank_question_id=bank_question_id,
                link_method=link_method,
                confidence=0.0,
                status="rejected",
                reviewed_by=reviewed_by,
                evidence=evidence,
            )

    def confirmed_bank_question_ids(self, grading_session_id: str | int) -> set[int]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT bank_question_id
                FROM grading_question_links
                WHERE grading_session_id = ? AND status = 'confirmed'
                """,
                (_required_text(grading_session_id, "grading_session_id"),),
            ).fetchall()
        return {int(row["bank_question_id"]) for row in rows}

    def exclusion_warning(self, grading_session_id: str | int) -> str:
        links = self.list_links(grading_session_id)
        if not links:
            return "当前考试尚无已确认的题库原题关联，无法可靠排除全部原题。"
        pending = sum(item["status"] != "confirmed" for item in links)
        if pending:
            return f"当前考试仍有 {pending} 道题的来源关联未经确认，原题排除可能不完整。"
        return ""

    def list_links(self, grading_session_id: str | int | None = None) -> list[dict[str, Any]]:
        self.initialize_database()
        sql = "SELECT * FROM grading_question_links"
        params: list[Any] = []
        if grading_session_id is not None:
            sql += " WHERE grading_session_id = ?"
            params.append(_required_text(grading_session_id, "grading_session_id"))
        sql += " ORDER BY grading_session_id, source_question_id, id"
        with connect(self.db_path) as conn:
            return [_link_from_row(row) for row in conn.execute(sql, params).fetchall()]

    def link_questions_for_session(
        self,
        *,
        grading_session_id: str | int,
        source_questions: Iterable[Mapping[str, Any]],
        candidate_bank_questions: Iterable[Mapping[str, Any]] | None = None,
    ) -> dict[str, int]:
        self.initialize_database()
        paper_scoped_candidates = candidate_bank_questions is not None
        candidates = (
            [dict(item) for item in candidate_bank_questions]
            if candidate_bank_questions is not None
            else self._active_bank_questions()
        )
        summary = {"confirmed": 0, "suggested": 0, "unresolved": 0}
        for source in source_questions:
            source_id = _source_question_id(source)
            if not source_id:
                summary["unresolved"] += 1
                continue

            existing = self._link_for_source(grading_session_id, source_id)
            if existing is not None and existing["status"] in {"confirmed", "rejected"}:
                summary[existing["status"] if existing["status"] == "confirmed" else "unresolved"] += 1
                continue

            explicit_bank_id = _optional_int(source.get("bank_question_id"))
            if explicit_bank_id is not None and any(int(item["id"]) == explicit_bank_id for item in candidates):
                self.confirm_link(
                    grading_session_id=grading_session_id,
                    source_question_id=source_id,
                    bank_question_id=explicit_bank_id,
                    link_method="source_metadata",
                    evidence={"source_question_id": source_id},
                )
                summary["confirmed"] += 1
                continue

            source_number = _normalize_question_number(source_id) if paper_scoped_candidates else ""
            number_matches = [
                item
                for item in candidates
                if source_number
                and _normalize_question_number(item.get("question_number")) == source_number
            ]
            if len(number_matches) == 1:
                self.confirm_link(
                    grading_session_id=grading_session_id,
                    source_question_id=source_id,
                    bank_question_id=int(number_matches[0]["id"]),
                    link_method="paper_question_number",
                    evidence={"normalized_question_number": source_number},
                )
                summary["confirmed"] += 1
                continue

            source_text = _source_question_text(source)
            exact = [
                item
                for item in candidates
                if source_text and _normalize_exact_text(item.get("question_text")) == _normalize_exact_text(source_text)
            ]
            if len(exact) == 1:
                self.confirm_link(
                    grading_session_id=grading_session_id,
                    source_question_id=source_id,
                    bank_question_id=int(exact[0]["id"]),
                    link_method="exact_text",
                    evidence={"normalized_text": _normalize_exact_text(source_text)},
                )
                summary["confirmed"] += 1
                continue

            scored = sorted(
                (
                    (text_similarity(source_text, item.get("question_text")), int(item["id"]))
                    for item in candidates
                    if source_text and item.get("question_text")
                ),
                reverse=True,
            )
            if scored and scored[0][0] >= SUGGESTION_THRESHOLD:
                confidence, bank_question_id = scored[0]
                self.suggest_link(
                    grading_session_id=grading_session_id,
                    source_question_id=source_id,
                    bank_question_id=bank_question_id,
                    confidence=confidence,
                    link_method="text_similarity",
                    evidence={"similarity": confidence},
                )
                summary["suggested"] += 1
            else:
                summary["unresolved"] += 1
        return summary

    def _active_bank_questions(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, question_number, question_text, source_file
                FROM questions
                WHERE COALESCE(is_deleted, 0) = 0
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _link_for_source(
        self,
        grading_session_id: str | int,
        source_question_id: str | int,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM grading_question_links
                WHERE grading_session_id = ? AND source_question_id = ?
                """,
                (
                    _required_text(grading_session_id, "grading_session_id"),
                    _required_text(source_question_id, "source_question_id"),
                ),
            ).fetchone()
        return _link_from_row(row) if row is not None else None


def _upsert_link(
    conn: sqlite3.Connection,
    *,
    grading_session_id: str | int,
    source_question_id: str | int,
    bank_question_id: int,
    link_method: str,
    confidence: float,
    status: str,
    reviewed_by: str | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = _required_text(grading_session_id, "grading_session_id")
    source_id = _required_text(source_question_id, "source_question_id")
    method = _required_text(link_method, "link_method")
    if status not in {"confirmed", "suggested", "rejected"}:
        raise ValueError(f"unsupported link status: {status}")
    if conn.execute("SELECT id FROM questions WHERE id = ?", (int(bank_question_id),)).fetchone() is None:
        raise KeyError(f"bank question not found: {bank_question_id}")
    reviewed_at = "datetime('now','localtime')" if reviewed_by or status != "suggested" else "NULL"
    conn.execute(
        f"""
        INSERT INTO grading_question_links (
            grading_session_id, source_question_id, bank_question_id, link_method,
            confidence, status, evidence_json, reviewed_by, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, {reviewed_at})
        ON CONFLICT(grading_session_id, source_question_id) DO UPDATE SET
            bank_question_id = excluded.bank_question_id,
            link_method = excluded.link_method,
            confidence = excluded.confidence,
            status = excluded.status,
            evidence_json = excluded.evidence_json,
            reviewed_by = excluded.reviewed_by,
            reviewed_at = {reviewed_at},
            updated_at = datetime('now','localtime')
        WHERE excluded.status <> 'suggested'
           OR grading_question_links.status = 'suggested'
        """,
        (
            session_id,
            source_id,
            int(bank_question_id),
            method,
            float(confidence),
            status,
            json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True),
            _optional_text(reviewed_by),
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM grading_question_links
        WHERE grading_session_id = ? AND source_question_id = ?
        """,
        (session_id, source_id),
    ).fetchone()
    return _link_from_row(row)


def _link_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["evidence"] = json.loads(str(item.pop("evidence_json") or "{}"))
    except json.JSONDecodeError:
        item["evidence"] = {}
    return item


def _source_question_id(source: Mapping[str, Any]) -> str:
    for key in ("source_question_id", "question_id", "question_number"):
        value = _optional_text(source.get(key))
        if value:
            return value
    return ""


def _source_question_text(source: Mapping[str, Any]) -> str:
    for key in ("question_text", "text", "stem_summary"):
        value = _optional_text(source.get(key))
        if value:
            return value
    return ""


def _normalize_exact_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).casefold()


def _normalize_question_number(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"^(?:第|q|题)+", "", text)
    text = re.sub(r"(?:题)$", "", text)
    return re.sub(r"[\s._、，,:：-]+", "", text)


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str:
    return str(value or "").strip()


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["SourceQuestionLinkService"]
