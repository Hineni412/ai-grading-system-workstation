from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .encrypted_database import EncryptedDatabase
from .secure_repository import EncryptedObjectRepository


_REVIEW_SOON_DAYS = 14
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 50
_EXCERPT_LENGTH = 80
_BANDS = (
    ("90-100%", 90.0),
    ("80-89%", 80.0),
    ("70-79%", 70.0),
    ("60-69%", 60.0),
    ("60%以下", 0.0),
)


def _now() -> datetime:
    return datetime.now(UTC)


class ClassOverview:
    """Whole-class read models behind the student surface default panels.

    Read only. Identity display names come from the same encrypted identity
    payloads the student directory already uses; nothing new is persisted.
    """

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider

    def _identities(
        self,
        connection: Any,
        vmk: bytes,
        subject_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        unique_ids = list(dict.fromkeys(subject_ids))
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        rows = connection.execute(
            f"""
            SELECT subject_id, payload_object_id FROM student_subject_links
            WHERE subject_id IN ({placeholders})
            """,
            unique_ids,
        ).fetchall()
        identities: dict[str, dict[str, str]] = {}
        for row in rows:
            payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            identities[str(row["subject_id"])] = {
                "display_name": str(payload.get("display_name") or ""),
                "class_label": str(payload.get("class_label") or ""),
            }
        return identities

    @staticmethod
    def _identity(
        identities: dict[str, dict[str, str]],
        subject_id: str,
    ) -> dict[str, str]:
        return identities.get(subject_id) or {
            "display_name": "",
            "class_label": "",
        }

    def support_overview(
        self,
        *,
        token: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        size = max(1, min(int(limit), _MAX_LIMIT))
        horizon = (_now() + timedelta(days=_REVIEW_SOON_DAYS)).isoformat()
        with closing(self.database.connect()) as connection:
            record_rows = connection.execute(
                """
                SELECT r.record_id, r.subject_id, r.record_kind,
                       r.observed_at, r.review_at, r.created_at,
                       rv.payload_object_id
                FROM support_records r
                JOIN record_revisions rv
                  ON rv.record_id = r.record_id
                 AND rv.revision_number = r.current_revision
                WHERE r.state = 'active' AND r.record_kind <> 'ai_draft'
                ORDER BY r.observed_at DESC, r.created_at DESC
                """
            ).fetchall()
            plan_rows = connection.execute(
                """
                SELECT support_plan_id, subject_id, review_at
                FROM support_plans
                WHERE state = 'active' AND review_at IS NOT NULL
                """
            ).fetchall()

            timeline_rows = record_rows[:size]
            involved_ids = [str(row["subject_id"]) for row in record_rows]
            involved_ids += [str(row["subject_id"]) for row in plan_rows]
            identities = self._identities(connection, vmk, involved_ids)

            next_review: dict[str, str] = {}
            last_record: dict[str, str] = {}
            record_count: dict[str, int] = {}
            for row in record_rows:
                subject_id = str(row["subject_id"])
                record_count[subject_id] = record_count.get(subject_id, 0) + 1
                observed = str(row["observed_at"])
                if observed > last_record.get(subject_id, ""):
                    last_record[subject_id] = observed
                review = row["review_at"]
                if review and str(review) < next_review.get(subject_id, "\uffff"):
                    next_review[subject_id] = str(review)
            for row in plan_rows:
                subject_id = str(row["subject_id"])
                record_count.setdefault(subject_id, 0)
                review = row["review_at"]
                if review and str(review) < next_review.get(subject_id, "\uffff"):
                    next_review[subject_id] = str(review)

            due_soon: list[dict[str, object]] = []
            others: list[dict[str, object]] = []
            for subject_id in record_count:
                identity = self._identity(identities, subject_id)
                entry = {
                    "subject_id": subject_id,
                    "display_name": identity["display_name"],
                    "class_label": identity["class_label"],
                    "next_review_at": next_review.get(subject_id),
                    "last_record_at": last_record.get(subject_id),
                    "active_record_count": record_count[subject_id],
                    "due_soon": False,
                }
                review = next_review.get(subject_id)
                if review is not None and review <= horizon:
                    entry["due_soon"] = True
                    due_soon.append(entry)
                else:
                    others.append(entry)
            due_soon.sort(
                key=lambda item: (str(item["next_review_at"]), str(item["subject_id"]))
            )
            others.sort(
                key=lambda item: (
                    str(item["last_record_at"] or ""),
                    str(item["display_name"]),
                ),
                reverse=True,
            )
            follow_ups = (due_soon + others)[:size]

            recent_records = []
            for row in timeline_rows:
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                content = str(payload.get("content") or "").strip()
                excerpt = content[:_EXCERPT_LENGTH]
                if len(content) > _EXCERPT_LENGTH:
                    excerpt += "…"
                identity = self._identity(identities, str(row["subject_id"]))
                recent_records.append(
                    {
                        "record_id": str(row["record_id"]),
                        "subject_id": str(row["subject_id"]),
                        "display_name": identity["display_name"],
                        "class_label": identity["class_label"],
                        "record_kind": str(row["record_kind"]),
                        "observed_at": str(row["observed_at"]),
                        "excerpt": excerpt,
                    }
                )

        return {
            "follow_ups": follow_ups,
            "recent_records": recent_records,
            "has_records": bool(record_rows),
            "review_soon_days": _REVIEW_SOON_DAYS,
        }

    def academic_overview(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            session_rows = connection.execute(
                """
                SELECT session_id, payload_object_id, metadata_complete
                FROM assessment_sessions
                WHERE state = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (_MAX_LIMIT,),
            ).fetchall()
            sessions: list[dict[str, object]] = []
            stats_by_session: dict[str, dict[str, list[tuple[float, float | None]]]] = {}
            for row in session_rows:
                session_id = str(row["session_id"])
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                member_rows = connection.execute(
                    """
                    SELECT ev.payload_object_id AS evidence_object
                    FROM assessment_session_members m
                    JOIN evidence_versions ev
                      ON ev.evidence_version_id = m.evidence_version_id
                    WHERE m.session_id = ? AND ev.state = 'active'
                    """,
                    (session_id,),
                ).fetchall()
                subject_names: set[str] = set()
                scores: dict[str, list[tuple[float, float | None]]] = {}
                for member in member_rows:
                    evidence, _revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(member["evidence_object"]),
                    )
                    subject_name = str(evidence.get("subject_name") or "")
                    if subject_name:
                        subject_names.add(subject_name)
                    score = evidence.get("score")
                    if (
                        str(evidence.get("result_state") or "")
                        in {"normal", "makeup"}
                        and isinstance(score, int | float)
                    ):
                        max_score = evidence.get("max_score")
                        scores.setdefault(subject_name, []).append(
                            (
                                float(score),
                                float(max_score)
                                if isinstance(max_score, int | float) and max_score
                                else None,
                            )
                        )
                stats_by_session[session_id] = scores
                sessions.append(
                    {
                        "session_id": session_id,
                        "title": str(payload.get("title") or ""),
                        "occurred_on": str(payload.get("occurred_on") or ""),
                        "comparison_series": (
                            None
                            if not payload.get("comparison_series")
                            else str(payload["comparison_series"])
                        ),
                        "subject_names": sorted(subject_names),
                        "member_count": len(member_rows),
                        "metadata_complete": bool(row["metadata_complete"]),
                    }
                )
            sessions.sort(
                key=lambda item: (str(item["occurred_on"]), str(item["session_id"])),
                reverse=True,
            )

            attention_rows = connection.execute(
                """
                SELECT subject_id, COUNT(*) AS pending
                FROM attention_cards
                WHERE state = 'draft'
                GROUP BY subject_id
                ORDER BY pending DESC, subject_id
                """
            ).fetchall()
            identities = self._identities(
                connection,
                vmk,
                [str(row["subject_id"]) for row in attention_rows],
            )
            attention_students = []
            for row in attention_rows:
                identity = self._identity(identities, str(row["subject_id"]))
                attention_students.append(
                    {
                        "subject_id": str(row["subject_id"]),
                        "display_name": identity["display_name"],
                        "class_label": identity["class_label"],
                        "pending_count": int(row["pending"]),
                    }
                )

        latest_session = None
        if sessions:
            latest = sessions[0]
            subject_stats = []
            for subject_name, values in sorted(
                stats_by_session[str(latest["session_id"])].items()
            ):
                subject_stats.append(
                    self._subject_stats(subject_name, values)
                )
            latest_session = {
                "session_id": latest["session_id"],
                "title": latest["title"],
                "occurred_on": latest["occurred_on"],
                "subjects": subject_stats,
            }
        return {
            "sessions": sessions,
            "latest_session": latest_session,
            "attention_students": attention_students,
            "attention_pending_count": sum(
                int(item["pending_count"]) for item in attention_students
            ),
        }

    @staticmethod
    def _subject_stats(
        subject_name: str,
        values: list[tuple[float, float | None]],
    ) -> dict[str, object]:
        scores = [score for score, _max in values]
        band_counts: dict[str, int] = {label: 0 for label, _ in _BANDS}
        band_counts["未定标"] = 0
        for score, max_score in values:
            if not max_score:
                band_counts["未定标"] += 1
                continue
            percent = score / max_score * 100
            for label, threshold in _BANDS:
                if percent >= threshold:
                    band_counts[label] += 1
                    break
        return {
            "subject_name": subject_name,
            "count": len(scores),
            "average": round(sum(scores) / len(scores), 1),
            "maximum": max(scores),
            "minimum": min(scores),
            "bands": [
                {"label": label, "count": count}
                for label, count in band_counts.items()
            ],
        }


__all__ = ["ClassOverview"]
