from __future__ import annotations

import hashlib
import json
import statistics
from contextlib import closing
from datetime import date, timedelta
from typing import Any, Callable

from .assessment_evidence_service import AssessmentEvidenceService
from .attention_service import AttentionService
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository
from .sensitive_work_projection import SensitiveWorkProjection
from .session_label import session_order, short_label
from .subject_canonical import canonical_subjects


RULESET_VERSION = "academic_ruleset_v1"
CONTRACT_VERSION = "academic_analysis_v1"
CONTINUOUS_RANK_THRESHOLD = 0.05
CONTINUOUS_SCORE_RATIO_THRESHOLD = 0.05

# 学业画像阈值（相对位次口径，0~1，越高越好）
PROFILE_TREND_STEP = 0.05  # 趋势：每一步相对位次变化达到该幅度才算同向
PROFILE_STABLE_SWING = 0.05  # 稳定性：振幅不超过该比例为「稳定」
PROFILE_VOLATILE_SWING = 0.15  # 振幅达到该比例为「波动大」
PROFILE_SKEW_STRONG = 0.90  # 偏科：最强科位于前 10%
PROFILE_SKEW_WEAK = 0.50  # 且最弱科位于后 50%
PROFILE_ATTENTION_DROP = 0.10  # 单科一次下滑达到该比例即标记关注


def _term_short(session: dict[str, object]) -> str:
    grade = str(session.get("grade") or "").replace("年级", "")
    term = session.get("term")
    half = "上" if term == "上学期" else ("下" if term == "下学期" else "")
    return f"{grade}{half}" if grade and half else ""


def _academic_order(session: dict[str, object]) -> tuple[int, int, int] | None:
    """Order exams along the semester chain (七上→七下→…，同学期内期中<期末)。"""
    return session_order(session)


def _today() -> date:
    return date.today()


class StudentAcademicAnalysis:
    """Build the academic read model from confirmed evidence only.

    All chart decisions live here. The browser receives segments that already
    say whether a line may be drawn and never has to reinterpret missing data.
    """

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        evidence: AssessmentEvidenceService,
        attention: AttentionService,
        projections: SensitiveWorkProjection,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.evidence = evidence
        self.attention = attention
        self.projections = projections

    def read(
        self,
        *,
        token: str,
        subject_id: str,
        time_range: str = "all",
        comparison_series: str | None = None,
        subject_name: str | None = None,
        comparable_only: bool = False,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        evidence_items = list(self.evidence.list_subject_evidence(
            token=token, subject_id=subject_id
        )["items"])
        sessions: dict[str, dict[str, object]] = {}
        with closing(self.database.connect()) as connection:
            for item in evidence_items:
                evidence_id = str(item["evidence_version_id"])
                member = connection.execute(
                    """
                    SELECT m.*, s.payload_object_id, s.metadata_complete, s.state
                    FROM assessment_session_members m
                    JOIN assessment_sessions s ON s.session_id = m.session_id
                    WHERE m.evidence_version_id = ?
                    """,
                    (evidence_id,),
                ).fetchone()
                if member is None:
                    session_id = f"legacy-{evidence_id}"
                    payload = {
                        "title": item.get("title"),
                        "occurred_on": item.get("occurred_on"),
                        "comparison_series": None,
                        "raw_file_retained": False,
                        "metadata_complete": False,
                        "legacy_virtual": True,
                    }
                    measure_role = "subject_score"
                else:
                    session_id = str(member["session_id"])
                    payload, _revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(member["payload_object_id"]),
                    )
                    payload = {**payload, "legacy_virtual": False}
                    measure_role = str(member["measure_role"])
                point = self._point(
                    item,
                    session_id=session_id,
                    measure_role=measure_role,
                    session=payload,
                )
                session = sessions.setdefault(
                    session_id,
                    {
                        "session_id": session_id,
                        **payload,
                        "evidence": [],
                    },
                )
                session["evidence"].append(point)

        # 科目展示名归一：同场变体列并入同一科目（如「英语笔试加听力」→「英语」），
        # 只改读取口径，不改库存数据；分组、筛选与比较都用归一名。
        # 被隐藏的原始列（英语听说、被变体取代的原始「英语」）不进入学生分析。
        for session in sessions.values():
            points = session["evidence"]
            name_map = canonical_subjects(
                str(point.get("subject_name") or "") for point in points
            )
            visible = []
            for point in points:
                raw = str(point.get("subject_name") or "")
                if raw:
                    display = name_map.get(raw)
                    if display is None:
                        continue
                    point["subject_name"] = display
                visible.append(point)
            session["evidence"] = visible

        all_sessions = sorted(
            sessions.values(),
            key=lambda value: (str(value.get("occurred_on") or ""), str(value["session_id"])),
        )
        for session in all_sessions:
            session["short_label"] = short_label(
                grade=session.get("grade"),
                term=session.get("term"),
                exam_type=session.get("exam_type"),
                title=session.get("title"),
            )
        source_version = self._source_version(all_sessions)
        filter_options = {
            "series": sorted({
                str(item.get("comparison_series"))
                for item in all_sessions
                if item.get("comparison_series")
            }),
            "subjects": sorted({
                str(point.get("subject_name"))
                for item in all_sessions
                for point in item["evidence"]
                if point.get("subject_name")
            }),
        }
        scope_sessions = self._filter_sessions(
            all_sessions,
            time_range=time_range,
            comparison_series=comparison_series,
            subject_name=None,
        )
        ordered_sessions = self._filter_sessions(
            scope_sessions,
            time_range="all",
            comparison_series=None,
            subject_name=subject_name,
        )
        series = self._series(ordered_sessions)
        self._mark_comparable_points(series)
        if comparable_only:
            ordered_sessions = [
                {
                    **session,
                    "evidence": [
                        point
                        for point in session["evidence"]
                        if point.get("is_comparable") is True
                    ],
                }
                for session in ordered_sessions
            ]
            ordered_sessions = [
                session for session in ordered_sessions if session["evidence"]
            ]
            series = self._series(ordered_sessions)
            self._mark_comparable_points(series)
        rank_change_pairs = self._rank_pairs(series)
        relative_signals, insufficient = self._relative_signals(scope_sessions)
        if subject_name:
            relative_signals = [
                item
                for item in relative_signals
                if item["subject_name"] == subject_name
            ]
            if not relative_signals:
                insufficient = ["当前筛选范围内没有满足重复证据条件的学科线索"]
        if comparable_only and not ordered_sessions:
            insufficient = ["筛选后没有可直接比较的证据"]
        recent_changes = self._recent_changes(series)
        cards = list(self.attention.list_for_subject(token=token, subject_id=subject_id)["items"])
        profile = self._profile(scope_sessions)
        return {
            "contract_version": CONTRACT_VERSION,
            "source_version": source_version,
            "ruleset_version": RULESET_VERSION,
            "sessions": ordered_sessions,
            "series": series,
            "rank_change_pairs": rank_change_pairs,
            "relative_subject_signals": relative_signals,
            "recent_changes": recent_changes,
            "insufficient_reasons": insufficient,
            "attention_cards": cards,
            "profile": profile,
            "detail_ref": "/api/class-teacher/evidence/{evidence_version_id}/snapshot",
            "model_enabled": False,
            "physical_request_count": 0,
            "filter_options": filter_options,
            "applied_filters": {
                "time_range": time_range,
                "comparison_series": comparison_series,
                "subject_name": subject_name,
                "comparable_only": comparable_only,
            },
        }

    @staticmethod
    def _filter_sessions(
        sessions: list[dict[str, object]],
        *,
        time_range: str,
        comparison_series: str | None,
        subject_name: str | None,
    ) -> list[dict[str, object]]:
        if time_range not in {"all", "recent_90", "year"}:
            raise VaultError(
                "academic_time_range_invalid",
                "学业证据时间范围无效",
                status_code=422,
            )
        latest_academic_year = max(
            (
                str(item.get("academic_year") or "")
                for item in sessions
                if item.get("academic_year")
            ),
            default="",
        )
        boundary = _today() - timedelta(days=89)
        filtered: list[dict[str, object]] = []
        for session in sessions:
            occurred_on = str(session.get("occurred_on") or "")
            if comparison_series and session.get("comparison_series") != comparison_series:
                continue
            if (
                time_range == "year"
                and (
                    not latest_academic_year
                    or session.get("academic_year") != latest_academic_year
                )
            ):
                continue
            if (
                time_range == "recent_90"
                and date.fromisoformat(occurred_on) < boundary
            ):
                continue
            evidence = [
                point
                for point in session["evidence"]
                if not subject_name or point.get("subject_name") == subject_name
            ]
            if evidence:
                filtered.append({**session, "evidence": evidence})
        return filtered

    def snapshot(self, *, token: str, evidence_version_id: str) -> dict[str, object]:
        item = self.evidence.get_evidence(
            token=token, evidence_version_id=evidence_version_id
        )
        return {
            "contract_version": "academic_evidence_snapshot_v1",
            "teacher_confirmed": True,
            "raw_file_retained": False,
            "source_write_back": False,
            "evidence": item,
            "language": "教师确认的证据快照",
        }

    def decide(
        self,
        *,
        token: str,
        attention_card_id: str,
        operation_id: str,
        revision: int,
        decision: str,
        reason: str | None,
        plan_id: str | None,
        review_at: str | None,
        source_version: str,
    ) -> dict[str, object]:
        card = self.attention.get(token=token, attention_card_id=attention_card_id)
        current = self.read(token=token, subject_id=str(card["subject_id"]))
        if str(current["source_version"]) != str(source_version):
            raise VaultError(
                "academic_analysis_stale",
                "学业证据已经变化，请刷新后重新判断",
                status_code=409,
            )
        if not str(reason or "").strip():
            raise VaultError(
                "attention_reason_required",
                "跟进、观察或暂不行动都必须记录教师理由",
                status_code=422,
            )
        projection_state = (
            "pending" if decision == "follow_up" else "waiting"
        )

        def enqueue_projection(connection: Any, vmk: bytes) -> None:
            if decision not in {"follow_up", "observe"}:
                return
            self.projections.enqueue(
                connection,
                vmk=vmk,
                source_kind="attention_followup",
                source_id=attention_card_id,
                state=projection_state,
                due_date=(str(review_at)[:10] if review_at else None),
            )

        result = self.attention.resolve(
            token=token,
            attention_card_id=attention_card_id,
            operation_id=operation_id,
            revision=revision,
            decision=decision,
            reason=reason,
            plan_id=plan_id,
            review_at=review_at,
            transaction_hook=enqueue_projection,
        )
        if decision in {"follow_up", "observe"}:
            self.projections.drain(token=token)
            projection = self.projections.read_source_group(
                token=token,
                source_kind="attention_followup",
                source_id=attention_card_id,
            )
            result = {**result, "projection": projection}
        else:
            result = {**result, "projection": None}
        return {**result, "source_version": source_version}

    @staticmethod
    def _point(
        item: dict[str, object],
        *,
        session_id: str,
        measure_role: str,
        session: dict[str, object],
    ) -> dict[str, object]:
        rank_context = dict(item.get("rank_context") or {})
        rank = rank_context.get("rank")
        participants = rank_context.get("participant_count")
        relative = None
        if isinstance(rank, int) and isinstance(participants, int) and participants > 1 and 1 <= rank <= participants:
            relative = 1 - (rank - 1) / (participants - 1)
        return {
            "evidence_version_id": item["evidence_version_id"],
            "session_id": session_id,
            "subject_name": item.get("subject_name"),
            "assessment_nature": item.get("assessment_nature"),
            "comparison_series": session.get("comparison_series"),
            "metadata_complete": bool(session.get("metadata_complete")),
            "measure_role": measure_role,
            "occurred_on": item.get("occurred_on"),
            "score": item.get("score"),
            "max_score": item.get("max_score"),
            "grade_level": item.get("grade_level") or None,
            "result_state": item.get("result_state"),
            "rank": rank,
            "class_rank": rank_context.get("class_rank"),
            "participant_count": participants,
            "rank_scope": rank_context.get("rank_scope") or item.get("rank_scope"),
            "rank_origin": rank_context.get("rank_origin"),
            "cohort_key": rank_context.get("cohort_key"),
            "ranking_rule_version": rank_context.get("ranking_rule_version"),
            "relative_position": relative,
            "state": item.get("state"),
            "teacher_confirmed": True,
            "detail_ref": f"/api/class-teacher/evidence/{item['evidence_version_id']}/snapshot",
        }

    def _series(self, sessions: list[dict[str, object]]) -> list[dict[str, object]]:
        buckets: dict[tuple[str, str], list[tuple[tuple[int, int, int] | None, dict[str, object]]]] = {}
        for session in sessions:
            series_name = str(session.get("comparison_series") or "未归组")
            order = _academic_order(session)
            for point in session["evidence"]:
                key = (series_name, str(point.get("subject_name") or "未命名学科"))
                buckets.setdefault(key, []).append((order, point))
        result = []
        for (series_name, subject_name), ordered in sorted(buckets.items()):
            # 学期链条优先：有年级/学期元数据的按七上→七下→八上…排，
            # 同一学期内按考试日期排；缺元数据的排在最后按日期兜底。
            ordered.sort(key=lambda item: (
                item[0] if item[0] is not None else (99, 99, 99),
                str(item[1].get("occurred_on") or ""),
                str(item[1]["evidence_version_id"]),
            ))
            points = [point for _, point in ordered]
            segments = [self._comparison(points[index - 1], points[index]) for index in range(1, len(points))]
            result.append({
                "series": series_name,
                "subject_name": subject_name,
                "points": points,
                "segments": segments,
            })
        return result

    @staticmethod
    def _mark_comparable_points(series: list[dict[str, object]]) -> None:
        for item in series:
            points = list(item["points"])
            by_id = {
                str(point["evidence_version_id"]): point
                for point in points
            }
            for point in points:
                point["is_comparable"] = False
            outputs: dict[str, set[str]] = {
                evidence_id: set() for evidence_id in by_id
            }
            for segment in item["segments"]:
                allowed = set(segment.get("allowed_outputs") or [])
                if allowed:
                    outputs[str(segment["from"])].update(allowed)
                    outputs[str(segment["to"])].update(allowed)
                if segment.get("overall_status") == "directly_comparable":
                    by_id[str(segment["from"])]["is_comparable"] = True
                    by_id[str(segment["to"])]["is_comparable"] = True
            for evidence_id, point in by_id.items():
                point_outputs = sorted(outputs[evidence_id])
                point["comparable_outputs"] = point_outputs

    @staticmethod
    def _comparison(older: dict[str, object], newer: dict[str, object]) -> dict[str, object]:
        score_reasons: list[str] = []
        rank_reasons: list[str] = []
        score_status = "directly_comparable"
        rank_status = "directly_comparable"
        special = {str(older.get("result_state")), str(newer.get("result_state"))}
        if special - {"normal"}:
            score_status = "not_comparable"
            score_reasons.append("special_result_state")
            rank_status = "not_comparable"
            rank_reasons.append("special_result_state")
        if score_status == "directly_comparable" and not (
            older.get("metadata_complete") and newer.get("metadata_complete")
        ):
            score_status = "insufficient_information"
            score_reasons.append("session_metadata_incomplete")
        elif score_status == "directly_comparable" and (
            not older.get("comparison_series")
            or older.get("comparison_series") != newer.get("comparison_series")
            or not older.get("assessment_nature")
            or older.get("assessment_nature") != newer.get("assessment_nature")
            or older.get("subject_name") != newer.get("subject_name")
        ):
            score_status = "reference_only"
            score_reasons.append("score_basis_changed")
        if score_status == "directly_comparable" and (older.get("max_score") is None or newer.get("max_score") is None):
            score_status = "insufficient_information"
            score_reasons.append("max_score_missing")
        elif score_status == "directly_comparable" and older.get("max_score") != newer.get("max_score"):
            score_status = "reference_only"
            score_reasons.append("max_score_changed")
        rank_basis = ("rank_scope", "cohort_key", "ranking_rule_version")
        if rank_status == "directly_comparable" and not (
            older.get("metadata_complete") and newer.get("metadata_complete")
        ):
            rank_status = "insufficient_information"
            rank_reasons.append("session_metadata_incomplete")
        elif rank_status == "directly_comparable" and (
            older.get("relative_position") is None
            or newer.get("relative_position") is None
            or any(not older.get(key) or not newer.get(key) for key in rank_basis)
        ):
            rank_status = "insufficient_information"
            rank_reasons.append("rank_context_invalid")
        elif rank_status == "directly_comparable" and any(older.get(key) != newer.get(key) for key in rank_basis):
            rank_status = "reference_only"
            rank_reasons.append("rank_basis_changed")
        allowed = []
        if score_status == "directly_comparable":
            allowed.append("score_line")
        if rank_status == "directly_comparable":
            allowed.append("rank_line")
        return {
            "from": older["evidence_version_id"],
            "to": newer["evidence_version_id"],
            "overall_status": "directly_comparable" if len(allowed) == 2 else ("reference_only" if allowed else "not_comparable"),
            "dimensions": {
                "score": {"status": score_status, "reason_codes": score_reasons},
                "rank": {"status": rank_status, "reason_codes": rank_reasons},
            },
            "allowed_outputs": allowed,
            "basis": {"same_series": True, "teacher_confirmed": True},
            "ruleset_version": RULESET_VERSION,
        }

    def ai_summary(self, *, token: str, subject_id: str) -> dict[str, object] | None:
        """Compact pre-computed academic card injected into class-teacher AI flows.

        Facts only: positioning, trend, stability and subject skew labels are
        computed here; wording and strategy remain the model's job, and the
        teacher still confirms before anything is saved.
        """
        analysis = self.read(token=token, subject_id=subject_id)
        profile = analysis["profile"]
        assert isinstance(profile, dict)
        current = profile.get("current")
        if not isinstance(current, dict):
            current = None
        subjects = [
            item for item in list(profile.get("subjects") or [])
            if isinstance(item, dict) and item.get("latest")
        ]
        if current is None and not subjects:
            return None
        trend = profile.get("trend") if isinstance(profile.get("trend"), dict) else {}
        stability = profile.get("stability") if isinstance(profile.get("stability"), dict) else {}
        skew = profile.get("skew") if isinstance(profile.get("skew"), dict) else {}
        previous = current.get("previous") if current and isinstance(current.get("previous"), dict) else None
        return {
            "contract_version": "academic_ai_summary_v1",
            "latest_exam": (
                {
                    "title": current["session_title"],
                    "occurred_on": current["occurred_on"],
                    "term_label": current["term_label"],
                }
                if current else None
            ),
            "total": (
                {
                    "score": current["score"],
                    "rank": current["rank"],
                    "participant_count": current["participant_count"],
                    "top_ratio": current["top_ratio"],
                }
                if current else None
            ),
            "rank_change_vs_previous": (
                {
                    "previous_title": previous["session_title"],
                    "previous_rank": previous["rank"],
                    "delta": current["rank_delta"],
                }
                if previous else None
            ),
            "trend": trend.get("label", "insufficient"),
            "stability": stability.get("label", "insufficient"),
            "skew": {
                "label": skew.get("label", "insufficient"),
                "strongest": [str(item["subject_name"]) for item in list(skew.get("strongest") or [])],
                "weakest": [str(item["subject_name"]) for item in list(skew.get("weakest") or [])],
            },
            "subjects": [
                {
                    "name": item["subject_name"],
                    "latest_rank": item["latest"]["rank"],
                    "participant_count": item["latest"]["participant_count"],
                    "rank_delta": item.get("rank_delta"),
                    "attention": bool(item.get("attention")),
                }
                for item in subjects
            ],
            "notes": [
                "仅比较同学期链内同年级、同口径（校次）的场次",
                "缺考不参与计算，也不按 0 分处理",
            ],
        }

    def _profile(self, sessions: list[dict[str, object]]) -> dict[str, object]:
        """Positioning / trend / stability / skew labels from confirmed evidence.

        Rank metrics only compare points inside the latest point's own grade
        (七年级名次不与八年级名次直接比)；points without a usable rank are
        skipped, and thin data yields explicit "insufficient" labels instead
        of a forced judgement.
        """
        ordered = sorted(
            sessions,
            key=lambda value: (
                _academic_order(value) or (99, 99, 99),
                str(value.get("occurred_on") or ""),
                str(value["session_id"]),
            ),
        )
        totals: list[dict[str, object]] = []
        by_subject: dict[str, list[dict[str, object]]] = {}
        for session in ordered:
            for point in session["evidence"]:
                entry = {
                    "session_title": session.get("title"),
                    "occurred_on": point.get("occurred_on"),
                    "grade": session.get("grade"),
                    "term": session.get("term"),
                    "term_label": _term_short(session),
                    "short_label": session.get("short_label") or "",
                    "grade_level": point.get("grade_level") or None,
                    "score": point.get("score"),
                    "rank": point.get("rank"),
                    "class_rank": point.get("class_rank"),
                    "participant_count": point.get("participant_count"),
                    "relative_position": point.get("relative_position"),
                    "result_state": point.get("result_state"),
                }
                if point.get("measure_role") == "total_score":
                    totals.append(entry)
                elif point.get("measure_role") == "subject_score":
                    by_subject.setdefault(
                        str(point.get("subject_name") or "未命名学科"), []
                    ).append(entry)

        ranked_totals = [item for item in totals if self._ranked(item)]
        latest_total = ranked_totals[-1] if ranked_totals else None
        same_grade = (
            [item for item in ranked_totals if item["grade"] == latest_total["grade"]]
            if latest_total else []
        )
        previous = same_grade[-2] if len(same_grade) >= 2 else None
        current = None
        if latest_total is not None:
            participants = int(latest_total["participant_count"])
            rank = int(latest_total["rank"])
            current = {
                **latest_total,
                "top_ratio": rank / participants,
                "previous": (
                    {
                        "session_title": previous["session_title"],
                        "occurred_on": previous["occurred_on"],
                        "term_label": previous["term_label"],
                        "short_label": previous["short_label"],
                        "rank": previous["rank"],
                        "participant_count": previous["participant_count"],
                    }
                    if previous else None
                ),
                "rank_delta": (
                    int(previous["rank"]) - rank if previous else None
                ),
            }
        trend = self._profile_trend(same_grade)
        stability = self._profile_stability(same_grade)
        # 一场考试的多科目在存储层可能是多个场次行，按考试日期归并取最新一场。
        skew = self._profile_skew(latest_total, by_subject)
        subjects = self._profile_subjects(by_subject, latest_total)
        return {
            "current": current,
            "trend": trend,
            "stability": stability,
            "skew": skew,
            "subjects": subjects,
            "total_trend": [
                {
                    "occurred_on": item["occurred_on"],
                    "term_label": item["term_label"],
                    "short_label": item["short_label"],
                    "session_title": item["session_title"],
                    "rank": item["rank"],
                    "participant_count": item["participant_count"],
                    "relative_position": item["relative_position"],
                    "result_state": item["result_state"],
                }
                for item in totals
            ],
            "basis": {
                "total_session_count": len(ranked_totals),
                "grade": (latest_total["grade"] if latest_total else None),
            },
        }

    @staticmethod
    def _ranked(entry: dict[str, object]) -> bool:
        return (
            isinstance(entry.get("rank"), int)
            and isinstance(entry.get("participant_count"), int)
            and int(entry["participant_count"]) > 0
            and entry.get("result_state") == "normal"
        )

    @staticmethod
    def _profile_trend(same_grade: list[dict[str, object]]) -> dict[str, object]:
        recent = same_grade[-3:]
        if len(recent) < 3 or any(item.get("relative_position") is None for item in recent):
            return {"label": "insufficient", "step_deltas": [], "session_count": len(recent)}
        deltas = [
            float(recent[index]["relative_position"]) - float(recent[index - 1]["relative_position"])
            for index in (1, 2)
        ]
        if all(delta >= PROFILE_TREND_STEP for delta in deltas):
            label = "improving"
        elif all(delta <= -PROFILE_TREND_STEP for delta in deltas):
            label = "declining"
        elif all(abs(delta) < PROFILE_TREND_STEP for delta in deltas):
            label = "flat"
        else:
            label = "fluctuating"
        return {"label": label, "step_deltas": deltas, "session_count": len(recent)}

    @staticmethod
    def _profile_stability(same_grade: list[dict[str, object]]) -> dict[str, object]:
        recent = same_grade[-4:]
        positions = [
            float(item["relative_position"])
            for item in recent
            if item.get("relative_position") is not None
        ]
        if len(positions) < 2:
            return {"label": "insufficient", "swing_ratio": None, "session_count": len(recent)}
        swing = max(positions) - min(positions)
        label = (
            "stable" if swing <= PROFILE_STABLE_SWING
            else ("volatile" if swing >= PROFILE_VOLATILE_SWING else "moderate")
        )
        return {"label": label, "swing_ratio": swing, "session_count": len(recent)}

    @staticmethod
    def _profile_skew(
        latest_total: dict[str, object] | None,
        by_subject: dict[str, list[dict[str, object]]],
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "label": "insufficient", "strongest": [], "weakest": [], "gap_ratio": None,
        }
        if latest_total is None:
            return result
        exam_date = str(latest_total["occurred_on"] or "")
        ranked = [
            {"subject_name": subject_name, **entries[-1]}
            for subject_name, entries in by_subject.items()
            if entries
            and str(entries[-1]["occurred_on"] or "") == exam_date
            and entries[-1]["result_state"] == "normal"
            and entries[-1]["relative_position"] is not None
        ]
        if len(ranked) < 3:
            return result
        ranked.sort(key=lambda point: float(point["relative_position"]), reverse=True)
        strongest = ranked[0]
        weakest = ranked[-1]
        gap = float(strongest["relative_position"]) - float(weakest["relative_position"])
        label = (
            "skewed"
            if float(strongest["relative_position"]) >= PROFILE_SKEW_STRONG
            and float(weakest["relative_position"]) <= PROFILE_SKEW_WEAK
            else "balanced"
        )
        return {
            "label": label,
            "strongest": [{
                "subject_name": strongest["subject_name"],
                "rank": strongest.get("rank"),
                "relative_position": strongest["relative_position"],
            }],
            "weakest": [{
                "subject_name": weakest["subject_name"],
                "rank": weakest.get("rank"),
                "relative_position": weakest["relative_position"],
            }],
            "gap_ratio": gap,
        }

    @staticmethod
    def _profile_subjects(
        by_subject: dict[str, list[dict[str, object]]],
        latest_total: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        latest_grade = latest_total["grade"] if latest_total else None
        rows: list[dict[str, object]] = []
        for subject_name, entries in sorted(by_subject.items()):
            ranked = [item for item in entries if StudentAcademicAnalysis._ranked(item)]
            latest = ranked[-1] if ranked else (entries[-1] if entries else None)
            same_grade = [
                item for item in ranked
                if latest_grade is None or item["grade"] == latest_grade
            ]
            previous = same_grade[-2] if len(same_grade) >= 2 else None
            rank_delta = None
            drop_ratio = None
            if latest is not None and previous is not None and any(item is latest for item in same_grade):
                rank_delta = int(previous["rank"]) - int(latest["rank"])
                participants = int(latest["participant_count"] or 0)
                if participants > 0:
                    drop_ratio = -rank_delta / participants
            rows.append({
                "subject_name": subject_name,
                "latest": latest,
                "rank_delta": rank_delta,
                "points": [
                    {
                        "occurred_on": item["occurred_on"],
                        "term_label": item["term_label"],
                        "short_label": item["short_label"],
                        "grade_level": item["grade_level"],
                        "session_title": item["session_title"],
                        "rank": item["rank"],
                        "relative_position": item["relative_position"],
                        "result_state": item["result_state"],
                    }
                    for item in entries
                ],
                # 占位，排序后统一回填
                "attention": False,
                "_drop_ratio": drop_ratio,
            })
        # 关注标记：当前最弱的两科，或最近一次下滑超过阈值的科
        with_position = [
            row for row in rows
            if row["latest"] is not None and row["latest"].get("relative_position") is not None
        ]
        with_position.sort(key=lambda row: float(row["latest"]["relative_position"]))
        for row in with_position[:2]:
            row["attention"] = True
        for row in rows:
            drop = row.pop("_drop_ratio")
            if drop is not None and drop >= PROFILE_ATTENTION_DROP:
                row["attention"] = True
        return rows

    @staticmethod
    def _rank_pairs(series: list[dict[str, object]]) -> list[dict[str, object]]:
        pairs = []
        for item in series:
            points = item["points"]
            if len(points) < 2:
                continue
            older, newer = points[-2], points[-1]
            segment = item["segments"][-1]
            if segment["dimensions"]["rank"]["status"] == "directly_comparable":
                from_rank = older.get("rank")
                to_rank = newer.get("rank")
                pairs.append({
                    "subject_name": item["subject_name"],
                    "from": older["relative_position"],
                    "to": newer["relative_position"],
                    "delta": newer["relative_position"] - older["relative_position"],
                    "rank_scope": older.get("rank_scope"),
                    "from_rank": from_rank,
                    "to_rank": to_rank,
                    # 名次变化：正数表示进步（名次数字变小）
                    "rank_delta": (
                        int(from_rank) - int(to_rank)
                        if isinstance(from_rank, int) and isinstance(to_rank, int)
                        else None
                    ),
                })
        return pairs

    @staticmethod
    def _relative_signals(sessions: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[str]]:
        by_subject: dict[str, list[float]] = {}
        for session in sessions:
            eligible = [
                point for point in session["evidence"]
                if session.get("metadata_complete")
                and point.get("measure_role") == "subject_score"
                and point.get("relative_position") is not None
                and int(point.get("participant_count") or 0) >= 10
                and point.get("result_state") == "normal"
            ]
            if len(eligible) < 3:
                continue
            bases = {
                (
                    point.get("cohort_key"),
                    point.get("rank_scope"),
                    point.get("ranking_rule_version"),
                )
                for point in eligible
            }
            if len(bases) != 1 or any(not value for value in next(iter(bases))):
                continue
            median = statistics.median(float(point["relative_position"]) for point in eligible)
            for point in eligible:
                by_subject.setdefault(str(point["subject_name"]), []).append(float(point["relative_position"]) - median)
        signals = []
        for subject_name, deltas in sorted(by_subject.items()):
            if len(deltas) < 3:
                continue
            median_delta = statistics.median(deltas)
            nonnegative = sum(value >= 0 for value in deltas)
            nonpositive = sum(value <= 0 for value in deltas)
            kind = "inconsistent"
            if median_delta >= 0.10 and nonnegative / len(deltas) >= 2 / 3:
                kind = "relative_strength_clue"
            elif median_delta <= -0.10 and nonpositive / len(deltas) >= 2 / 3:
                kind = "relative_support_clue"
            signals.append({
                "subject_name": subject_name,
                "signal": kind,
                "eligible_session_count": len(deltas),
                "median_relative_difference": median_delta,
                "persistent_label": False,
            })
        insufficient = [] if signals else ["至少需要 3 个同口径场次，且每场至少 3 科、参评不少于 10 人"]
        return signals, insufficient

    @staticmethod
    def _recent_changes(series: list[dict[str, object]]) -> list[dict[str, object]]:
        changes = []
        for item in series:
            if not item["segments"]:
                continue
            segment = item["segments"][-1]
            older, newer = item["points"][-2], item["points"][-1]
            changes.append({
                "subject_name": item["subject_name"],
                "score_delta": (
                    float(newer["score"]) - float(older["score"])
                    if segment["dimensions"]["score"]["status"] == "directly_comparable"
                    and newer.get("score") is not None and older.get("score") is not None else None
                ),
                "rank_delta": (
                    float(newer["relative_position"]) - float(older["relative_position"])
                    if segment["dimensions"]["rank"]["status"] == "directly_comparable" else None
                ),
                "comparison": segment,
                **StudentAcademicAnalysis._continuous_change(item),
            })
        return changes

    @staticmethod
    def _continuous_change(item: dict[str, object]) -> dict[str, object]:
        points = list(item["points"])
        segments = list(item["segments"])
        result: dict[str, object] = {
            "continuous_score_direction": None,
            "continuous_rank_direction": None,
        }
        if len(points) < 3 or len(segments) < 2:
            return result
        recent_points = points[-3:]
        recent_segments = segments[-2:]
        if all(segment["dimensions"]["score"]["status"] == "directly_comparable" for segment in recent_segments):
            deltas = [
                float(recent_points[index]["score"]) - float(recent_points[index - 1]["score"])
                for index in (1, 2)
            ]
            threshold = CONTINUOUS_SCORE_RATIO_THRESHOLD * float(recent_points[-1]["max_score"])
            if all(delta >= threshold for delta in deltas):
                result["continuous_score_direction"] = "improving"
            elif all(delta <= -threshold for delta in deltas):
                result["continuous_score_direction"] = "declining"
        if all(segment["dimensions"]["rank"]["status"] == "directly_comparable" for segment in recent_segments):
            deltas = [
                float(recent_points[index]["relative_position"])
                - float(recent_points[index - 1]["relative_position"])
                for index in (1, 2)
            ]
            if all(delta >= CONTINUOUS_RANK_THRESHOLD for delta in deltas):
                result["continuous_rank_direction"] = "improving"
            elif all(delta <= -CONTINUOUS_RANK_THRESHOLD for delta in deltas):
                result["continuous_rank_direction"] = "declining"
        return result

    @staticmethod
    def _source_version(sessions: list[dict[str, object]]) -> str:
        payload = [
            {
                "session_id": session["session_id"],
                "metadata_complete": session.get("metadata_complete"),
                "evidence": [
                    {key: point.get(key) for key in (
                        "evidence_version_id", "state", "result_state", "score", "rank",
                        "participant_count", "rank_scope", "cohort_key", "ranking_rule_version",
                    )}
                    for point in session["evidence"]
                ],
            }
            for session in sessions
        ]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = ["CONTRACT_VERSION", "RULESET_VERSION", "StudentAcademicAnalysis"]
