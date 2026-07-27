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
    def __init__(
        self,
        db_path: str | Path,
        *,
        external_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.external_connection = external_connection

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
        if self.external_connection is None:
            self.initialize_database()
        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
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
        if self.external_connection is None:
            self.initialize_database()
        sql = "SELECT * FROM grading_question_links"
        params: list[Any] = []
        if grading_session_id is not None:
            sql += " WHERE grading_session_id = ?"
            params.append(_required_text(grading_session_id, "grading_session_id"))
        sql += " ORDER BY grading_session_id, source_question_id, id"
        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
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

    def confirm_imported_questions_for_session(
        self,
        *,
        grading_session_id: str | int,
        source_questions: Iterable[Mapping[str, Any]],
        imported_bank_questions: Iterable[Mapping[str, Any]],
        sync_job_id: int | None = None,
        sync_config_revision: str | None = None,
    ) -> dict[str, object]:
        """Confirm only relationships proven by import metadata or a unique paper-local number."""
        self.initialize_database()
        candidates = [dict(item) for item in imported_bank_questions]
        valid_ids = {
            candidate_id
            for candidate in candidates
            if (candidate_id := _optional_int(candidate.get("id"))) is not None
        }
        confirmed = 0
        unresolved_ids: list[str] = []
        changes: list[dict[str, Any]] = []
        try:
            for source in source_questions:
                source_id = _source_question_id(source)
                if not source_id:
                    unresolved_ids.append("")
                    continue

                existing = self._link_for_source(grading_session_id, source_id)
                if existing is not None and existing["status"] == "confirmed":
                    existing_owner = _sync_owner(existing)
                    if (
                        sync_job_id is not None
                        and existing_owner is not None
                        and existing_owner != int(sync_job_id)
                    ):
                        if existing_owner > int(sync_job_id):
                            confirmed += 1
                            continue
                        else:
                            self.discard_automatic_links_for_interrupted_syncs(
                                [(grading_session_id, existing_owner)]
                            )
                            existing = None
                    else:
                        confirmed += 1
                        continue
                if existing is not None and existing["status"] == "rejected":
                    unresolved_ids.append(source_id)
                    continue

                explicit_bank_id = _optional_int(source.get("bank_question_id"))
                if explicit_bank_id in valid_ids:
                    changes.append({"source_question_id": source_id, "before": existing})
                    self.confirm_link(
                        grading_session_id=grading_session_id,
                        source_question_id=source_id,
                        bank_question_id=int(explicit_bank_id),
                        link_method="source_metadata",
                        evidence={
                            "source_question_id": source_id,
                            **_sync_evidence(sync_job_id, sync_config_revision),
                        },
                    )
                    confirmed += 1
                    continue

                source_number = _normalize_question_number(source_id)
                number_matches = [
                    item
                    for item in candidates
                    if source_number
                    and _normalize_question_number(item.get("question_number")) == source_number
                ]
                if len(number_matches) == 1:
                    changes.append({"source_question_id": source_id, "before": existing})
                    self.confirm_link(
                        grading_session_id=grading_session_id,
                        source_question_id=source_id,
                        bank_question_id=int(number_matches[0]["id"]),
                        link_method="paper_question_number",
                        evidence={
                            "normalized_question_number": source_number,
                            **_sync_evidence(sync_job_id, sync_config_revision),
                        },
                    )
                    confirmed += 1
                    continue

                unresolved_ids.append(source_id)
        except BaseException:
            if sync_job_id is not None and changes:
                self.rollback_imported_question_links(
                    grading_session_id=grading_session_id,
                    sync_job_id=sync_job_id,
                    changes=changes,
                )
            raise

        result: dict[str, object] = {
            "confirmed": confirmed,
            "unresolved": len(unresolved_ids),
            "unresolved_question_ids": unresolved_ids,
        }
        if sync_job_id is not None:
            result["_rollback_changes"] = changes
        return result

    def rollback_imported_question_links(
        self,
        *,
        grading_session_id: str | int,
        sync_job_id: int,
        changes: Iterable[Mapping[str, Any]],
    ) -> None:
        """Undo only link rows that are still owned by the stale sync job."""

        self.initialize_database()
        session_id = _required_text(grading_session_id, "grading_session_id")
        with connect(self.db_path) as conn:
            for change in reversed([dict(item) for item in changes]):
                source_id = _required_text(
                    change.get("source_question_id"),
                    "source_question_id",
                )
                current = conn.execute(
                    """
                    SELECT * FROM grading_question_links
                    WHERE grading_session_id = ? AND source_question_id = ?
                    """,
                    (session_id, source_id),
                ).fetchone()
                if current is None or _sync_owner(_link_from_row(current)) != int(sync_job_id):
                    continue
                before = change.get("before")
                if (
                    not isinstance(before, Mapping)
                    or _sync_owner(before) is not None
                ):
                    conn.execute(
                        """
                        DELETE FROM grading_question_links
                        WHERE grading_session_id = ? AND source_question_id = ?
                        """,
                        (session_id, source_id),
                    )
                    continue
                conn.execute(
                    """
                    UPDATE grading_question_links
                    SET bank_question_id = ?, link_method = ?, confidence = ?,
                        status = ?, evidence_json = ?, reviewed_by = ?,
                        reviewed_at = ?, updated_at = datetime('now','localtime')
                    WHERE grading_session_id = ? AND source_question_id = ?
                    """,
                    (
                        int(before["bank_question_id"]),
                        str(before["link_method"]),
                        float(before["confidence"]),
                        str(before["status"]),
                        json.dumps(
                            dict(before.get("evidence") or {}),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        _optional_text(before.get("reviewed_by")),
                        before.get("reviewed_at"),
                        session_id,
                        source_id,
                    ),
                )

    def discard_automatic_links_for_interrupted_syncs(
        self,
        sync_owners: Iterable[tuple[str | int, int]],
    ) -> int:
        """Delete only automatic links still owned by interrupted sync jobs."""

        owners = {
            (
                _required_text(session_id, "grading_session_id"),
                int(job_id),
            )
            for session_id, job_id in sync_owners
            if _optional_int(job_id) is not None and int(job_id) > 0
        }
        if not owners:
            return 0
        self.initialize_database()
        deleted = 0
        with connect(self.db_path) as conn:
            for session_id, job_id in owners:
                cursor = conn.execute(
                    """
                    DELETE FROM grading_question_links
                    WHERE grading_session_id = ?
                      AND json_valid(evidence_json) = 1
                      AND CAST(
                            json_extract(evidence_json, '$.sync_job_id')
                            AS INTEGER
                          ) = ?
                    """,
                    (session_id, job_id),
                )
                deleted += int(cursor.rowcount)
        return deleted

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


def _sync_evidence(
    sync_job_id: int | None,
    sync_config_revision: str | None,
) -> dict[str, Any]:
    if sync_job_id is None:
        return {}
    evidence: dict[str, Any] = {"sync_job_id": int(sync_job_id)}
    revision = str(sync_config_revision or "").strip()
    if revision:
        evidence["sync_config_revision"] = revision
    return evidence


def _sync_owner(link: Mapping[str, Any]) -> int | None:
    evidence = link.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    return _optional_int(evidence.get("sync_job_id"))


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
