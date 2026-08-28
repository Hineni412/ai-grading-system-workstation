from __future__ import annotations

import statistics
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository
from .session_label import short_label
from .student_academic_analysis import _academic_order
from .subject_canonical import canonical_subjects


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
# 等级分布的固定段序；未识别或缺失的等级计入「其他」。
_GRADE_LEVELS = ("A+", "A", "B+", "B", "C+", "C")


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
            SELECT subject_id, payload_object_id, source_fingerprint FROM student_subject_links
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
                "student_ref": str(row["source_fingerprint"]),
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
            "student_ref": "",
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
                    "student_ref": identity["student_ref"],
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
                        "student_ref": identity["student_ref"],
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
                        "short_label": short_label(
                            grade=payload.get("grade"),
                            term=payload.get("term"),
                            exam_type=payload.get("exam_type"),
                            title=payload.get("title"),
                        ),
                        "grade": (
                            None
                            if not payload.get("grade")
                            else str(payload["grade"])
                        ),
                        "term": (
                            None
                            if not payload.get("term")
                            else str(payload["term"])
                        ),
                        "exam_type": (
                            None
                            if not payload.get("exam_type")
                            else str(payload["exam_type"])
                        ),
                        "academic_year": (
                            None
                            if not payload.get("academic_year")
                            else str(payload["academic_year"])
                        ),
                        "comparison_series": (
                            None
                            if not payload.get("comparison_series")
                            else str(payload["comparison_series"])
                        ),
                        # 科目名按归一展示名返回（变体列并入，同场原始列消歧）。
                        "subject_names": sorted(
                            set(canonical_subjects(subject_names).values())
                        ),
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
                        "student_ref": identity["student_ref"],
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
                "short_label": latest["short_label"],
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

    def _session_members(
        self,
        connection: Any,
        vmk: bytes,
        session_id: str,
    ) -> list[dict[str, object]]:
        """展开场次成员：每条 active 证据连同学生、排名与考试满分。"""
        rows = connection.execute(
            """
            SELECT m.measure_role,
                   ev.payload_object_id AS evidence_object,
                   ev.result_id,
                   sr.subject_id,
                   a.payload_object_id AS assessment_object
            FROM assessment_session_members m
            JOIN evidence_versions ev
              ON ev.evidence_version_id = m.evidence_version_id
            JOIN subject_results sr ON sr.result_id = ev.result_id
            JOIN assessments a ON a.assessment_id = sr.assessment_id
            WHERE m.session_id = ? AND ev.state = 'active'
            """,
            (session_id,),
        ).fetchall()
        assessment_payloads: dict[str, dict[str, object]] = {}
        members: list[dict[str, object]] = []
        for row in rows:
            evidence, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["evidence_object"]),
            )
            assessment_object = str(row["assessment_object"])
            if assessment_object not in assessment_payloads:
                assessment_payloads[assessment_object], _revision = (
                    self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=assessment_object,
                    )
                )
            rank_row = connection.execute(
                """
                SELECT payload_object_id FROM rank_contexts
                WHERE result_id = ?
                """,
                (str(row["result_id"]),),
            ).fetchone()
            rank_context: dict[str, object] = {}
            if rank_row is not None:
                rank_context, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(rank_row["payload_object_id"]),
                )
            members.append(
                {
                    "subject_id": str(row["subject_id"]),
                    "measure_role": str(row["measure_role"]),
                    "evidence": evidence,
                    "rank_context": rank_context,
                    "assessment_max_score": assessment_payloads[
                        assessment_object
                    ].get("max_score"),
                }
            )
        return members

    @staticmethod
    def _relative_position(rank: object, participants: object) -> float | None:
        # 与学生分析页同一口径：参评人数大于 1 时才有相对位次。
        if (
            isinstance(rank, int)
            and isinstance(participants, int)
            and participants > 1
            and 1 <= rank <= participants
        ):
            return 1 - (rank - 1) / (participants - 1)
        return None

    def session_class_results(
        self,
        *,
        token: str,
        session_id: str,
    ) -> dict[str, object]:
        """单场次全班明细 + 各科统计，供班级页场次切换与名次热力表使用。"""
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            session_row = connection.execute(
                """
                SELECT session_id, payload_object_id
                FROM assessment_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise VaultError(
                    "assessment_session_not_found",
                    "成绩场次不存在",
                    status_code=404,
                )
            session_payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(session_row["payload_object_id"]),
            )
            members = self._session_members(connection, vmk, session_id)
            # 科目归一只在读取层：同 canonical 名的多列合并为一组参与统计；
            # 被隐藏的原始列（英语听说、被变体取代的原始「英语」）不出现在输出里。
            name_map = canonical_subjects(
                str(member["evidence"].get("subject_name") or "")
                for member in members
                if isinstance(member["evidence"], dict)
            )

            subject_max: dict[str, float | None] = {}
            stats_values: dict[str, list[tuple[float, float | None]]] = {}
            grade_values: dict[str, list[str | None]] = {}
            rank_values: dict[str, list[int]] = {}
            results_by_student: dict[str, dict[str, dict[str, object]]] = {}
            total_ranks: dict[str, int] = {}
            participant_count: int | None = None
            any_participant_count: int | None = None
            for member in members:
                evidence = member["evidence"]
                assert isinstance(evidence, dict)
                rank_context = member["rank_context"]
                assert isinstance(rank_context, dict)
                raw_name = str(evidence.get("subject_name") or "")
                subject_name = name_map.get(raw_name)
                if subject_name is None:
                    continue
                subject_id = str(member["subject_id"])
                if subject_name not in subject_max:
                    raw_max = member["assessment_max_score"]
                    subject_max[subject_name] = (
                        float(raw_max)
                        if isinstance(raw_max, int | float)
                        else None
                    )
                score = evidence.get("score")
                raw_max = evidence.get("max_score")
                evidence_max = (
                    float(raw_max)
                    if isinstance(raw_max, int | float) and raw_max
                    else None
                )
                result_state = str(evidence.get("result_state") or "")
                # 统计只计 normal，与总览口径一致；其余状态仍返回给前端置灰。
                if result_state == "normal" and isinstance(score, int | float):
                    stats_values.setdefault(subject_name, []).append(
                        (float(score), evidence_max)
                    )
                    grade_level = evidence.get("grade_level")
                    grade_values.setdefault(subject_name, []).append(
                        str(grade_level).strip() if grade_level else None
                    )
                rank = rank_context.get("rank")
                # 位次指标只计 normal 且有校次的记录。
                if (
                    result_state == "normal"
                    and isinstance(rank, int)
                    and not isinstance(rank, bool)
                ):
                    rank_values.setdefault(subject_name, []).append(rank)
                results_by_student.setdefault(subject_id, {})[subject_name] = {
                    "score": score,
                    "rank": rank,
                    "class_rank": rank_context.get("class_rank"),
                    "relative_position": self._relative_position(
                        rank,
                        rank_context.get("participant_count"),
                    ),
                    "max_score": evidence_max,
                    "grade_level": evidence.get("grade_level") or None,
                    "result_state": result_state,
                }
                if (
                    member["measure_role"] == "total_score"
                    or subject_name == "总分"
                ) and isinstance(rank, int):
                    current = total_ranks.get(subject_id)
                    if current is None or rank < current:
                        total_ranks[subject_id] = rank
                # 顶层年级人数：优先取总分排名上下文，其次任意科目。
                count = rank_context.get("participant_count")
                if isinstance(count, int) and not isinstance(count, bool):
                    if any_participant_count is None:
                        any_participant_count = count
                    if participant_count is None and (
                        member["measure_role"] == "total_score"
                        or subject_name == "总分"
                    ):
                        participant_count = count

            resolved_participants = (
                participant_count
                if participant_count is not None
                else any_participant_count
            )
            # 总分满分不由教师单独维护：等于同场其余展示科目满分之和，
            # 任一展示科目未定标时总分同样未定标；统计分段也用该口径。
            subject_max = self._with_derived_total_max(subject_max)
            if "总分" in stats_values:
                stats_values["总分"] = [
                    (score, subject_max["总分"])
                    for score, _max in stats_values["总分"]
                ]
            subjects = [
                {
                    "subject_name": subject_name,
                    "max_score": subject_max[subject_name],
                    "stats": {
                        **self._subject_stats(
                            subject_name,
                            stats_values.get(subject_name, []),
                        ),
                        **self._rank_metrics(
                            rank_values.get(subject_name, []),
                            resolved_participants,
                        ),
                        "grade_counts": self._grade_counts(
                            grade_values.get(subject_name, [])
                        ),
                    },
                }
                for subject_name in sorted(subject_max)
            ]

            identities = self._identities(
                connection,
                vmk,
                list(results_by_student),
            )
            students = []
            for subject_id, results in results_by_student.items():
                identity = self._identity(identities, subject_id)
                students.append(
                    {
                        "subject_id": subject_id,
                        "student_ref": identity["student_ref"],
                        "display_name": identity["display_name"],
                        "class_label": identity["class_label"],
                        "total_rank": total_ranks.get(subject_id),
                        "results": results,
                    }
                )
            # 按总分校次升序；没有总分校次的学生排在最后。
            students.sort(
                key=lambda item: (
                    item["total_rank"] is None,
                    item["total_rank"] if item["total_rank"] is not None else 0,
                    str(item["subject_id"]),
                )
            )

        return {
            "session_id": session_id,
            "title": str(session_payload.get("title") or ""),
            "occurred_on": str(session_payload.get("occurred_on") or ""),
            "short_label": short_label(
                grade=session_payload.get("grade"),
                term=session_payload.get("term"),
                exam_type=session_payload.get("exam_type"),
                title=session_payload.get("title"),
            ),
            "participant_count": resolved_participants,
            "subjects": subjects,
            "students": students,
        }

    def class_trend(self, *, token: str) -> dict[str, object]:
        """全部 active 场次的班级均分走势，按学期链序排列。"""
        vmk = self._key_provider(token)
        sessions: list[dict[str, object]] = []
        with closing(self.database.connect()) as connection:
            session_rows = connection.execute(
                """
                SELECT session_id, payload_object_id
                FROM assessment_sessions
                WHERE state = 'active'
                """
            ).fetchall()
            for row in session_rows:
                session_id = str(row["session_id"])
                payload, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                members = self._session_members(connection, vmk, session_id)
                name_map = canonical_subjects(
                    str(member["evidence"].get("subject_name") or "")
                    for member in members
                    if isinstance(member["evidence"], dict)
                )
                subject_max: dict[str, float | None] = {}
                scores: dict[str, list[float]] = {}
                rank_values: dict[str, list[int]] = {}
                participant_count: int | None = None
                any_participant_count: int | None = None
                for member in members:
                    evidence = member["evidence"]
                    assert isinstance(evidence, dict)
                    rank_context = member["rank_context"]
                    assert isinstance(rank_context, dict)
                    raw_name = str(evidence.get("subject_name") or "")
                    subject_name = name_map.get(raw_name)
                    if subject_name is None:
                        continue
                    if subject_name not in subject_max:
                        raw_max = member["assessment_max_score"]
                        subject_max[subject_name] = (
                            float(raw_max)
                            if isinstance(raw_max, int | float)
                            else None
                        )
                    score = evidence.get("score")
                    result_state = str(evidence.get("result_state") or "")
                    # 均分只计 result_state 为 normal 的有效分数。
                    if result_state == "normal" and isinstance(score, int | float):
                        scores.setdefault(subject_name, []).append(float(score))
                    rank = rank_context.get("rank")
                    if (
                        result_state == "normal"
                        and isinstance(rank, int)
                        and not isinstance(rank, bool)
                    ):
                        rank_values.setdefault(subject_name, []).append(rank)
                    # 年级人数：优先取总分排名上下文，其次任意科目。
                    count = rank_context.get("participant_count")
                    if isinstance(count, int) and not isinstance(count, bool):
                        if any_participant_count is None:
                            any_participant_count = count
                        if participant_count is None and (
                            member["measure_role"] == "total_score"
                            or subject_name == "总分"
                        ):
                            participant_count = count
                resolved_participants = (
                    participant_count
                    if participant_count is not None
                    else any_participant_count
                )
                # 与单场明细同一口径：总分满分取其余展示科目满分之和。
                subject_max = self._with_derived_total_max(subject_max)
                subjects = [
                    {
                        "subject_name": subject_name,
                        "average": (
                            round(
                                sum(scores.get(subject_name, []))
                                / len(scores[subject_name]),
                                1,
                            )
                            if scores.get(subject_name)
                            else None
                        ),
                        "count": len(scores.get(subject_name, [])),
                        "max_score": subject_max[subject_name],
                        **self._rank_metrics(
                            rank_values.get(subject_name, []),
                            resolved_participants,
                        ),
                    }
                    for subject_name in sorted(subject_max)
                ]
                sessions.append(
                    {
                        "session_id": session_id,
                        "occurred_on": str(payload.get("occurred_on") or ""),
                        "title": str(payload.get("title") or ""),
                        "short_label": short_label(
                            grade=payload.get("grade"),
                            term=payload.get("term"),
                            exam_type=payload.get("exam_type"),
                            title=payload.get("title"),
                        ),
                        "term": (
                            None
                            if not payload.get("term")
                            else str(payload["term"])
                        ),
                        "grade": (
                            None
                            if not payload.get("grade")
                            else str(payload["grade"])
                        ),
                        "subjects": subjects,
                        "_order": _academic_order(payload),
                    }
                )
        # 学期链优先（七上→七下→八上…，同学期内期中<期末），同序内按考试日期，缺元数据排最后。
        sessions.sort(
            key=lambda item: (
                item["_order"] if item["_order"] is not None else (99, 99, 99),
                str(item["occurred_on"]),
                str(item["session_id"]),
            )
        )
        for item in sessions:
            item.pop("_order")
        return {"sessions": sessions}

    @staticmethod
    def _with_derived_total_max(
        subject_max: dict[str, float | None],
    ) -> dict[str, float | None]:
        """总分满分 = 同场其余展示科目满分之和；任一未定标则为 None。

        只针对展示口径计算：被读取层隐藏的原始列（英语听说等）不在其中，
        存储层的总分满分也不参与，避免旧口径残留。
        """
        if "总分" not in subject_max:
            return subject_max
        components = [
            value for name, value in subject_max.items() if name != "总分"
        ]
        total: float | None = (
            sum(components)
            if components and all(value is not None for value in components)
            else None
        )
        return {**subject_max, "总分": total}

    @staticmethod
    def _grade_counts(
        levels: list[str | None],
    ) -> list[dict[str, object]] | None:
        """等级人数：固定段序 A+…C，未识别或缺失计入「其他」。

        该科没有任何一条 normal 成绩带等级时返回 None，前端回退到得分率分段。
        """
        if not any(levels):
            return None
        counts: dict[str, int] = {label: 0 for label in _GRADE_LEVELS}
        counts["其他"] = 0
        for level in levels:
            if level is not None and level in counts:
                counts[level] += 1
            else:
                counts["其他"] += 1
        return [
            {"label": label, "count": count} for label, count in counts.items()
        ]

    @staticmethod
    def _rank_metrics(
        ranks: list[int],
        participant_count: int | None,
    ) -> dict[str, object]:
        """位次指标：校次均值与前 50/100/前 30% 人数，无校次或无人数时为 null。"""
        return {
            "average_rank": (
                round(sum(ranks) / len(ranks), 1) if ranks else None
            ),
            "top50_count": sum(1 for rank in ranks if rank <= 50),
            "top100_count": sum(1 for rank in ranks if rank <= 100),
            "front30pct_count": (
                sum(
                    1 for rank in ranks if rank / participant_count <= 0.3
                )
                if participant_count
                else None
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
            "average": round(sum(scores) / len(scores), 1) if scores else None,
            # 总体标准差（标准分雷达用）；少于 2 个有效分数时为 None
            "stddev": (
                round(statistics.pstdev(scores), 2) if len(scores) >= 2 else None
            ),
            "maximum": max(scores) if scores else None,
            "minimum": min(scores) if scores else None,
            "bands": [
                {"label": label, "count": count}
                for label, count in band_counts.items()
            ],
        }


__all__ = ["ClassOverview"]
