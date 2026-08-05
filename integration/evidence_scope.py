from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable, Mapping

from backend.repositories.access import GradingRepositoryAccess


@dataclass(frozen=True, slots=True)
class ResolvedEvidenceScope:
    sessions: tuple[dict[str, Any], ...]
    students: tuple[dict[str, Any], ...]
    score_profiles: Mapping[str, dict[str, Any]]
    historical_session_ids_by_student: Mapping[str, tuple[int, ...]]
    warnings: tuple[str, ...]

    def normalized_scope(self, request: Mapping[str, Any]) -> dict[str, Any]:
        class_ids = _text_list(request.get("class_ids"))
        legacy_class = _optional_text(request.get("class_id") or request.get("class_name"))
        if not class_ids and legacy_class:
            class_ids = [legacy_class]
        result = {
            "mode": str(request.get("mode") or "all"),
            "student_ids": [str(item["id"]) for item in self.students],
            "class_id": class_ids[0] if len(class_ids) == 1 else None,
            "class_ids": class_ids,
            "score_rate_min": _optional_rate(request.get("score_rate_min")),
            "score_rate_max": _optional_rate(request.get("score_rate_max")),
            "include_student_ids": _text_list(request.get("include_student_ids")),
            "exclude_student_ids": _text_list(request.get("exclude_student_ids")),
            "use_historical_fallback": bool(
                request.get("use_historical_fallback", True)
            ),
            "matched_student_count": len(self.students),
            "student_score_profiles": {
                str(student_id): dict(profile)
                for student_id, profile in self.score_profiles.items()
            },
        }
        result["scope_revision"] = hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return result


class EvidenceScopeResolver:
    """Resolve one auditable student/exam scope for graph, diagnosis and papers."""

    def __init__(self, grading_db: GradingRepositoryAccess) -> None:
        self.db = grading_db

    def resolve(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> ResolvedEvidenceScope:
        warnings: list[str] = []
        all_sessions = [
            dict(item)
            for item in self.db.list_grading_sessions()
            if not item.get("is_deleted")
        ]
        sessions = self._resolve_sessions(all_sessions, exam_scope, warnings)
        all_students = [dict(item) for item in self.db.list_students()]
        score_profiles, history_ids = self._score_profiles(
            students=all_students,
            selected_sessions=sessions,
            all_sessions=all_sessions,
            use_historical_fallback=bool(
                scope.get("use_historical_fallback", True)
            ),
        )
        students = self._resolve_students(
            all_students,
            scope,
            score_profiles,
            warnings,
        )
        return ResolvedEvidenceScope(
            sessions=tuple(sessions),
            students=tuple(students),
            score_profiles=score_profiles,
            historical_session_ids_by_student=history_ids,
            warnings=tuple(warnings),
        )

    def _resolve_sessions(
        self,
        active_sessions: list[dict[str, Any]],
        exam_scope: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        active = {int(item["id"]): item for item in active_sessions}
        mode = str(exam_scope.get("mode") or "current")
        requested = _int_list(exam_scope.get("session_ids"))
        if mode == "cross_exam":
            ids = sorted(active)
        elif mode == "manual":
            ids = [item for item in requested if item in active]
        elif mode == "current":
            if requested:
                ids = [requested[0]] if requested[0] in active else []
            else:
                ids = [max(active, key=lambda item: _session_order(active[item]))] if active else []
        else:
            raise ValueError(f"unsupported exam scope mode: {mode}")
        missing = [item for item in requested if item not in active]
        if missing:
            warnings.append(f"已忽略不存在或已删除的考试：{', '.join(map(str, missing))}")
        if not ids:
            warnings.append("未选择可用考试。")
        return [active[item] for item in ids]

    def _resolve_students(
        self,
        all_students: list[dict[str, Any]],
        scope: Mapping[str, Any],
        score_profiles: Mapping[str, dict[str, Any]],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        by_id = {str(item["id"]): item for item in all_students}
        mode = str(scope.get("mode") or "all")
        requested = _text_list(scope.get("student_ids"))
        class_ids = set(_text_list(scope.get("class_ids")))
        legacy_class = _optional_text(scope.get("class_id") or scope.get("class_name"))
        if not class_ids and legacy_class:
            class_ids.add(legacy_class)
        if mode == "all":
            base = list(all_students)
        elif mode == "class":
            base = [
                item for item in all_students
                if str(item.get("class_name") or "") in class_ids
            ]
        elif mode in {"student", "selected"}:
            base = [by_id[item] for item in requested if item in by_id]
        else:
            raise ValueError(f"unsupported student scope mode: {mode}")

        minimum = _optional_rate(scope.get("score_rate_min"))
        maximum = _optional_rate(scope.get("score_rate_max"))
        if minimum is not None or maximum is not None:
            base = [
                item
                for item in base
                if _rate_matches(
                    score_profiles.get(str(item["id"]), {}).get("score_rate"),
                    minimum,
                    maximum,
                )
            ]

        excluded = set(_text_list(scope.get("exclude_student_ids")))
        included = set(_text_list(scope.get("include_student_ids")))
        selected_ids = {
            str(item["id"]) for item in base if str(item["id"]) not in excluded
        }
        allowed_manual_ids = {
            str(item["id"])
            for item in all_students
            if not class_ids or str(item.get("class_name") or "") in class_ids
        }
        selected_ids.update(
            item for item in included if item in by_id and item in allowed_manual_ids
        )
        blocked = [item for item in included if item in by_id and item not in allowed_manual_ids]
        if blocked:
            warnings.append("已忽略不属于当前班级范围的手动纳入学生。")
        selected = [item for item in all_students if str(item["id"]) in selected_ids]

        unknown = [
            item for item in [*requested, *excluded, *included] if item not in by_id
        ]
        if unknown:
            warnings.append(f"已忽略不存在的学生：{', '.join(dict.fromkeys(unknown))}")
        if not selected:
            warnings.append("当前条件没有匹配到学生。")
        return selected

    def _score_profiles(
        self,
        *,
        students: list[dict[str, Any]],
        selected_sessions: list[dict[str, Any]],
        all_sessions: list[dict[str, Any]],
        use_historical_fallback: bool,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[int, ...]]]:
        by_code = {
            str(item.get("student_code") or ""): str(item["id"])
            for item in students
            if str(item.get("student_code") or "")
        }
        selected_ids = {int(item["id"]) for item in selected_sessions}
        current_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for session_id in selected_ids:
            for result in self.db.get_session_results(session_id):
                student_id = by_code.get(str(result.get("student_code") or ""))
                if student_id is None:
                    continue
                current_totals[student_id][0] += _number(result.get("student_score"))
                current_totals[student_id][1] += _number(result.get("total_score"))

        reference_order = max(
            (_session_order(item) for item in selected_sessions if _has_valid_session_time(item)),
            default=None,
        )
        historical_sessions = [
            item for item in all_sessions
            if int(item["id"]) not in selected_ids
            and reference_order is not None
            and _has_valid_session_time(item)
            and _session_order(item) < reference_order
        ]
        historical_rates: dict[str, list[tuple[float, float, dict[str, Any]]]] = defaultdict(list)
        for session in historical_sessions:
            for result in self.db.get_session_results(int(session["id"])):
                student_id = by_code.get(str(result.get("student_code") or ""))
                total = _number(result.get("total_score"))
                if student_id is None or total <= 0:
                    continue
                historical_rates[student_id].append(
                    (_number(result.get("student_score")), total, session)
                )

        profiles: dict[str, dict[str, Any]] = {}
        history_ids: dict[str, tuple[int, ...]] = {}
        for student in students:
            student_id = str(student["id"])
            awarded, total = current_totals[student_id]
            history = historical_rates.get(student_id, [])
            if total > 0:
                rate = awarded / total
                source = "current_exam"
            elif history and use_historical_fallback:
                rate = sum(item[0] for item in history) / sum(item[1] for item in history)
                source = "historical_fallback"
            else:
                rate = None
                source = "none"
            latest = max((item[2] for item in history), key=_session_order, default=None)
            profiles[student_id] = {
                "score_rate": round(rate, 4) if rate is not None else None,
                "score_rate_source": source,
                "historical_exam_count": len(history),
                "historical_latest_exam_at": (
                    str(latest.get("created_at") or "") if latest else None
                ),
            }
            history_ids[student_id] = tuple(
                int(item[2]["id"]) for item in sorted(history, key=lambda value: _session_order(value[2]))
            )
        return profiles, history_ids


def _session_order(session: Mapping[str, Any]) -> tuple[datetime, int]:
    raw = str(session.get("created_at") or "").strip().replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError:
        timestamp = datetime.min
    if timestamp.tzinfo is not None:
        timestamp = timestamp.replace(tzinfo=None)
    return timestamp, int(session.get("id") or 0)


def _has_valid_session_time(session: Mapping[str, Any]) -> bool:
    raw = str(session.get("created_at") or "").strip().replace("Z", "+00:00")
    if not raw:
        return False
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return False
    return True


def _rate_matches(value: object, minimum: float | None, maximum: float | None) -> bool:
    if value is None:
        return False
    rate = float(value)
    return (minimum is None or rate >= minimum) and (maximum is None or rate <= maximum)


def _optional_rate(value: object) -> float | None:
    if value is None or value == "":
        return None
    return round(float(value), 4)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_list(value: object) -> list[int]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    result: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _text_list(value: object) -> list[str]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["EvidenceScopeResolver", "ResolvedEvidenceScope"]
