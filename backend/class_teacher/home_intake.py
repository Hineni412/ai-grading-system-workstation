from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from .content_policy import RedactionResult, SensitiveContentPolicy
from .crypto import canonical_json
from .errors import VaultError
from .model_approval import ModelApproval
from .ordinary_database import OrdinaryWorkDatabase
from .work_graph import WorkGraph
from .intake_draft import compose_sensitive_draft, merge_revision, selected_with_downstream


_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_NUMERIC_DATES = (
    re.compile(r"(?<!\d)(?:(?P<year>20\d{2})\s*年\s*)?(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)(?!\d)"),
    re.compile(r"(?<!\d)(?:(?P<year>20\d{2})[./-])?(?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"),
)
_CHINESE_DATE = re.compile(
    r"(?P<month>[一二三四五六七八九十]{1,3})月"
    r"(?P<day>[一二三四五六七八九十]{1,3})(?:日|号)"
)
_RELATIVE_DAY = re.compile(r"今天|明天|后天")
_RELATIVE_WEEKDAY = re.compile(r"(本周|这周|下周)(?:周|星期)?([一二三四五六日天])")
_INCOMPLETE_WEEK = re.compile(r"(本周|这周|下周)(?!\s*(?:周|星期)?[一二三四五六日天])")
_PREVENTION_THEME = re.compile(
    r"(?:预防|防范|反对|宣传|教育|主题).{0,12}(?:欺凌|冲突|暴力)"
    r"|(?:欺凌|冲突|暴力).{0,12}(?:预防|宣传|教育|主题班会)"
)
_EMERGENCY = re.compile(
    r"正在(?:打架|斗殴|自伤|伤人)|持刀|要跳楼|试图自杀|已经自伤|"
    r"扬言.{0,8}(?:自杀|伤人)|失去意识|无法呼吸|严重出血|人身安全.{0,8}危险"
)
_AFFAIR = re.compile(
    r"打架|斗殴|推搡|肢体冲突|"
    r"(?:发生|出现|产生)(?:了)?(?:冲突|矛盾|争执)|"
    r"闹(?:了)?矛盾|欺凌|受伤|处分|惩戒"
)
_SUPPORT = re.compile(r"情绪|焦虑|抑郁|自伤|自杀|心理|健康|用药|家庭|家访|成绩|作业|课堂")
_FENCED_JSON = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)
_HOME_SENSITIVE_OPERATION = "home.intake.sensitive"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: str, *, maximum: int = 4000) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        raise VaultError("home_intake_text_required", "请先写下需要处理的事情", status_code=422)
    if len(clean) > maximum:
        raise VaultError("home_intake_text_too_long", f"内容不能超过 {maximum} 个字符", status_code=422)
    return clean


def _chinese_number(value: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    match = re.fullmatch(r"(?:(?P<tens>[二三])?十)(?P<ones>[一二三四五六七八九])?", value)
    if match is None:
        return None
    tens = digits.get(str(match.group("tens") or ""), 1)
    ones = digits.get(str(match.group("ones") or ""), 0)
    return (tens * 10) + ones


def interpret_local_date(
    text: str,
    *,
    selected_date: str | None = None,
    reference_date: str | None = None,
) -> dict[str, object]:
    """Interpret only supported local calendar forms and expose uncertainty."""

    try:
        reference = date.fromisoformat(reference_date) if reference_date else date.today()
        selected = date.fromisoformat(selected_date) if selected_date else None
    except ValueError as exc:
        raise VaultError("home_intake_date_invalid", "日期必须是有效年月日", status_code=422) from exc

    candidates: list[tuple[date, str]] = []
    matches = sorted(
        (match for pattern in _NUMERIC_DATES for match in pattern.finditer(text)),
        key=lambda match: match.start(),
    )
    for match in matches:
        month = int(match.group("month"))
        day = int(match.group("day"))
        year_text = match.group("year")
        year = int(year_text) if year_text else reference.year
        if not year_text and selected and (selected.month, selected.day) == (month, day):
            year = selected.year
        try:
            value = date(year, month, day)
            if not year_text and selected is None and value < reference:
                value = date(year + 1, month, day)
        except ValueError as exc:
            raise VaultError("home_intake_date_invalid", "文字中的日期不是有效年月日", status_code=422) from exc
        candidates.append((value, "explicit_numeric"))

    for match in _CHINESE_DATE.finditer(text):
        month = _chinese_number(match.group("month"))
        day = _chinese_number(match.group("day"))
        if month is None or day is None:
            raise VaultError("home_intake_date_invalid", "文字中的日期不是有效年月日", status_code=422)
        year = reference.year
        if selected and (selected.month, selected.day) == (month, day):
            year = selected.year
        try:
            value = date(year, month, day)
            if selected is None and value < reference:
                value = date(year + 1, month, day)
        except ValueError as exc:
            raise VaultError("home_intake_date_invalid", "文字中的日期不是有效年月日", status_code=422) from exc
        candidates.append((value, "explicit_numeric"))

    relative_offsets = {"今天": 0, "明天": 1, "后天": 2}
    for match in _RELATIVE_DAY.finditer(text):
        candidates.append((reference + timedelta(days=relative_offsets[match.group(0)]), "relative_day"))
    for match in _RELATIVE_WEEKDAY.finditer(text):
        monday = reference - timedelta(days=reference.weekday())
        offset = 7 if match.group(1) == "下周" else 0
        candidates.append((monday + timedelta(days=offset + _WEEKDAY[match.group(2)]), "relative_weekday"))

    unique = sorted({candidate.isoformat() for candidate, _source in candidates})
    if len(unique) > 1 or (selected is not None and unique and unique[0] != selected.isoformat()):
        all_candidates = sorted(set([*unique, *( [selected.isoformat()] if selected else [] )]))
        return {
            "status": "conflict",
            "source": "multiple_or_selected_conflict",
            "resolved_date": None,
            "selected_date": selected.isoformat() if selected else None,
            "candidates": all_candidates,
            "pending_reason": None,
        }
    if unique:
        source = next(source for candidate, source in candidates if candidate.isoformat() == unique[0])
        return {
            "status": "resolved",
            "source": source,
            "resolved_date": unique[0],
            "selected_date": selected.isoformat() if selected else None,
            "candidates": unique,
            "pending_reason": None,
        }
    if selected is not None:
        return {
            "status": "resolved",
            "source": "selected_date",
            "resolved_date": selected.isoformat(),
            "selected_date": selected.isoformat(),
            "candidates": [selected.isoformat()],
            "pending_reason": None,
        }
    return {
        "status": "pending",
        "source": "incomplete_week" if _INCOMPLETE_WEEK.search(text) else "not_provided",
        "resolved_date": None,
        "selected_date": None,
        "candidates": [],
        "pending_reason": (
            "已识别周范围，但仍需明确星期几" if _INCOMPLETE_WEEK.search(text) else "未识别到明确日期"
        ),
    }


class HomeIntake:
    """Deep homepage intake module over planning, approval, and WorkGraph seams."""

    def __init__(
        self,
        database: OrdinaryWorkDatabase,
        work: WorkGraph,
        model: ModelApproval,
    ) -> None:
        self.database = database
        self.work = work
        self.model = model
        self._previews: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()

    def prepare(
        self,
        *,
        token: str,
        text: str,
        due_date: str | None = None,
        reference_date: str | None = None,
        prior_operations: list[str] | None = None,
        round_number: int = 1,
        existing_aliases: tuple[tuple[str, str], ...] = (),
        revision_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        clean = _clean_text(text)
        date_info = interpret_local_date(
            clean,
            selected_date=due_date,
            reference_date=reference_date,
        )
        redaction = SensitiveContentPolicy.prepare_stable_model_text(
            clean,
            existing_aliases=existing_aliases,
        )
        route, recommendation = self._route(clean, redaction)
        emergency = self._emergency_guidance() if route == "emergency" else None
        common: dict[str, object] = {
            "route": route,
            "recommended_route": recommendation,
            "date_interpretation": date_info,
            "emergency_guidance": emergency,
            "round_number": round_number,
            "prior_operations": list(prior_operations or []),
            "round_physical_request_count": 0,
            "cumulative_physical_request_count": self._cumulative_count(
                token, list(prior_operations or [])
            ),
            "physical_request_count": 0,
            "dispatch_ready": date_info["status"] != "conflict",
            "local_only": bool(redaction.blocked_categories),
            "blocked_categories": list(redaction.blocked_categories),
            "removed_categories": list(redaction.removed_categories),
            "student_aliases": [alias for _name, alias in redaction.identity_aliases],
        }
        if date_info["status"] == "conflict":
            preview_id = uuid4().hex
            stored = {"route": route, "dispatch_ready": False}
            with self._lock:
                self._previews[preview_id] = stored
            return {**common, "preview_id": preview_id, "exact_payload": None, "fingerprint": None}
        if redaction.blocked_categories:
            preview_id = uuid4().hex
            with self._lock:
                self._previews[preview_id] = {"route": route, "dispatch_ready": False}
            return {
                **common,
                "dispatch_ready": False,
                "preview_id": preview_id,
                "exact_payload": None,
                "fingerprint": None,
            }

        prior = list(prior_operations or [])
        local_context = {
            "home_intake": True,
            "mode": "new_work",
            "round_number": round_number,
            "prior_operations": prior,
            "source_text": clean,
            "final_due_date": date_info.get("resolved_date"),
            "date_interpretation": date_info,
            "revision_context": dict(revision_context or {}),
        }
        if route == "ordinary":
            if SensitiveContentPolicy.ordinary_findings(clean):
                raise VaultError(
                    "home_intake_route_uncertain",
                    "内容可能涉及具体学生，请从受保护路径继续",
                    status_code=422,
                )
            preview = self.work.planning.prepare(
                source_text=clean,
                final_due_date=(
                    str(date_info["resolved_date"]) if date_info["resolved_date"] else None
                ),
                mode="new_work",
                local_context=local_context,
            )
            stored = {
                "route": route,
                "dispatch_ready": True,
                "underlying_preview_id": preview["preview_id"],
                "underlying_fingerprint": preview["fingerprint"],
            }
            with self._lock:
                self._previews[str(preview["preview_id"])] = stored
            return {**common, **preview, "route": route, "recommended_route": recommendation}

        context = {
            **local_context,
            "route": route,
            "recommended_route": recommendation,
            "identity_aliases": [list(item) for item in redaction.identity_aliases],
        }
        preview = self.model.prepare(
            token=token,
            purpose="home_sensitive_intake",
            source_text=clean,
            context=context,
            redaction=redaction,
            route_hint=recommendation,
            output_contract=self._sensitive_output_contract(),
            request_context=(
                {
                    "revision_context": {
                        "base_draft": dict(revision_context.get("base_draft") or {}),
                        "selected_step_keys": list(
                            revision_context.get("selected_step_keys") or []
                        ),
                        "selected_calendar_keys": list(
                            revision_context.get("selected_calendar_keys") or []
                        ),
                        "locked_rule": str(revision_context.get("locked_rule") or ""),
                    }
                }
                if revision_context
                else None
            ),
        )
        destination = self._destination()
        home_fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "payload_fingerprint": preview["fingerprint"],
                    "destination_fingerprint": destination["destination_fingerprint"],
                }
            )
        ).hexdigest()
        stored = {
            "route": route,
            "dispatch_ready": True,
            "underlying_preview_id": preview["preview_id"],
            "underlying_fingerprint": preview["fingerprint"],
            "home_fingerprint": home_fingerprint,
            "destination_fingerprint": destination["destination_fingerprint"],
            "round_number": round_number,
            "prior_operations": prior,
            "recommended_route": recommendation,
        }
        with self._lock:
            self._previews[str(preview["preview_id"])] = stored
        return {
            **common,
            **preview,
            "fingerprint": home_fingerprint,
            "route": route,
            "recommended_route": recommendation,
            "model_provider": destination.get("model_provider"),
            "model_endpoint": destination.get("model_endpoint"),
            "destination_fingerprint": destination["destination_fingerprint"],
            "physical_request_count": 0,
        }

    def dispatch(
        self,
        *,
        token: str,
        preview_id: str,
        fingerprint: str,
        operation_id: str,
    ) -> dict[str, object]:
        self._validate_operation(operation_id)
        with self._lock:
            preview = self._previews.get(preview_id)
        if preview is None:
            raise VaultError(
                "home_intake_preview_not_found",
                "首页发送预览不存在或已经过期，请重新生成",
                status_code=404,
            )
        if not preview.get("dispatch_ready"):
            raise VaultError(
                "home_intake_preview_not_dispatchable",
                "当前内容需先解决日期冲突或仅限本机处理",
                status_code=409,
            )
        route = str(preview["route"])
        if route == "ordinary":
            if fingerprint != str(preview["underlying_fingerprint"]):
                raise VaultError(
                    "home_intake_preview_changed",
                    "发送内容已经变化，请重新预览",
                    status_code=409,
                )
            self.work.invoke_plan(
                preview_id=str(preview["underlying_preview_id"]),
                fingerprint=str(preview["underlying_fingerprint"]),
                operation_id=operation_id,
            )
            return self.status(token=token, operation_id=operation_id)

        if fingerprint != str(preview["home_fingerprint"]):
            raise VaultError(
                "home_intake_preview_changed",
                "发送内容或目的地已经变化，请重新预览",
                status_code=409,
            )
        current_destination = self._destination()
        local_state = (
            "dispatching"
            if current_destination["destination_fingerprint"]
            == preview["destination_fingerprint"]
            else "destination_changed"
        )
        metadata = {
            "preview_id": preview_id,
            "underlying_fingerprint": preview["underlying_fingerprint"],
            "route": route,
            "recommended_route": preview["recommended_route"],
            "round_number": preview["round_number"],
            "prior_operations": preview["prior_operations"],
            "local_state": local_state,
        }
        if not self._claim_sensitive(operation_id, metadata):
            return self.status(token=token, operation_id=operation_id)
        if local_state == "destination_changed":
            return self.status(token=token, operation_id=operation_id)
        try:
            self.model.confirm(
                token=token,
                preview_id=preview_id,
                fingerprint=str(preview["underlying_fingerprint"]),
                operation_id=operation_id,
                expected_destination_fingerprint=str(
                    preview["destination_fingerprint"]
                ),
            )
        except VaultError as exc:
            if not self._has_model_operation(operation_id):
                self._set_sensitive_local_state(
                    operation_id,
                    state="failed_before_send",
                    error_category=exc.code,
                )
            raise
        return self.status(token=token, operation_id=operation_id)

    def status(self, *, token: str, operation_id: str) -> dict[str, object]:
        self._validate_operation(operation_id)
        row = self._work_operation(operation_id)
        if row is None:
            raise VaultError(
                "home_intake_operation_not_found",
                "首页处理操作不存在；状态查询不会重新发送内容",
                status_code=404,
            )
        operation_type = str(row["operation_type"])
        if operation_type == "work.plan.invoke":
            return self._ordinary_status(operation_id)
        if operation_type != _HOME_SENSITIVE_OPERATION:
            raise VaultError(
                "home_intake_operation_not_found",
                "该操作不是首页处理操作；状态查询不会重新发送内容",
                status_code=404,
            )
        self.model.session_key(token)
        metadata = self._decoded_receipt(row)
        prior = [str(item) for item in list(metadata.get("prior_operations") or [])]
        round_count = 0
        state = str(metadata.get("local_state") or "dispatching")
        if state in {"destination_changed", "failed_before_send"}:
            return self._result(
                operation_id=operation_id,
                route=str(metadata["route"]),
                state=state,
                result_kind=None,
                result=None,
                questions=[],
                error_category=str(
                    metadata.get("error_category") or state
                ),
                round_number=int(metadata.get("round_number") or 1),
                round_count=0,
                cumulative_count=self._cumulative_count(token, prior),
                teacher_confirmation_required=False,
                local_context={"prior_operations": prior},
            )
        try:
            model_result = self.model.status(token=token, operation_id=operation_id)
        except VaultError as exc:
            if exc.code != "class_teacher_model_operation_not_found":
                raise
            model_result = {
                "state": "result_unknown",
                "physical_request_count": 1,
                "error_category": "interrupted_after_home_claim",
                "draft_text": None,
            }
        round_count = int(model_result.get("physical_request_count") or 0)
        model_state = str(model_result.get("state") or "result_unknown")
        if model_state != "succeeded":
            mapped = {
                "failed_before_send": "unavailable",
                "result_unknown": "result_unknown",
                "claimed": "in_progress",
            }.get(model_state, model_state)
            return self._result(
                operation_id=operation_id,
                route=str(metadata["route"]),
                state=mapped,
                result_kind=None,
                result=None,
                questions=[],
                error_category=(
                    None
                    if model_result.get("error_category") is None
                    else str(model_result["error_category"])
                ),
                round_number=int(metadata.get("round_number") or 1),
                round_count=round_count,
                cumulative_count=self._cumulative_count(token, prior) + round_count,
                teacher_confirmation_required=False,
                local_context={"prior_operations": prior},
            )

        interpreted = self._interpret_sensitive_result(
            str(model_result.get("draft_text") or ""),
            recommended_route=str(metadata.get("recommended_route") or "student_support"),
        )
        model_context = self.model.operation_context(
            token=token, operation_id=operation_id
        )
        if interpreted.get("kind") in {
            "follow_up",
            "plain_text",
            "affair_recommendation",
            "student_support_recommendation",
        }:
            aliases = [
                str(item[1])
                for item in list(model_context.get("identity_aliases") or [])
                if isinstance(item, list) and len(item) == 2
            ]
            raw_result = interpreted.get("result")
            payload = dict(raw_result) if isinstance(raw_result, dict) else {}
            draft = compose_sensitive_draft(
                source_text=str(model_context.get("source_text") or ""),
                recommended_route=str(
                    metadata.get("recommended_route") or "student_support"
                ),
                resolved_date=(
                    str(model_context["final_due_date"])
                    if model_context.get("final_due_date")
                    else None
                ),
                model_payload=payload,
                model_questions=list(interpreted.get("questions") or []),
                student_aliases=aliases,
            )
            revision = model_context.get("revision_context")
            if isinstance(revision, dict) and isinstance(
                revision.get("base_draft"), dict
            ):
                draft = merge_revision(
                    dict(revision["base_draft"]),
                    draft,
                    [str(item) for item in list(revision.get("selected_step_keys") or [])],
                    [str(item) for item in list(revision.get("selected_calendar_keys") or [])],
                )
            interpreted = {
                "state": "succeeded",
                "kind": draft["kind"],
                "result": draft,
                "questions": list(draft.get("to_verify") or []),
                "error_category": None,
                "teacher_confirmation_required": True,
            }
        return self._result(
            operation_id=operation_id,
            route=str(metadata["route"]),
            state=str(interpreted["state"]),
            result_kind=interpreted.get("kind"),
            result=interpreted.get("result"),
            questions=list(interpreted.get("questions") or []),
            error_category=interpreted.get("error_category"),
            round_number=int(metadata.get("round_number") or 1),
            round_count=round_count,
            cumulative_count=self._cumulative_count(token, prior) + round_count,
            teacher_confirmation_required=bool(interpreted.get("teacher_confirmation_required")),
            local_context={
                "prior_operations": prior,
                "source_text": str(model_context.get("source_text") or ""),
                "final_due_date": model_context.get("final_due_date"),
            },
            result_fingerprint=(
                hashlib.sha256(canonical_json(interpreted["result"])).hexdigest()
                if isinstance(interpreted.get("result"), dict)
                else None
            ),
        )

    def prepare_follow_up(
        self,
        *,
        token: str,
        operation_id: str,
        answer: str,
        reference_date: str | None = None,
        selected_step_keys: list[str] | None = None,
        selected_calendar_keys: list[str] | None = None,
    ) -> dict[str, object]:
        parent = self.status(token=token, operation_id=operation_id)
        if parent.get("result_kind") not in {
            "follow_up",
            "plain_text",
            "ordinary_plan",
            "affair_recommendation",
            "student_support_recommendation",
        }:
            raise VaultError(
                "home_intake_follow_up_not_requested",
                "当前结果不能开始调整",
                status_code=409,
            )
        clean_answer = _clean_text(answer)
        revision_context: dict[str, object] = {}
        if parent.get("result_kind") in {
            "affair_recommendation",
            "student_support_recommendation",
        } and isinstance(parent.get("result"), dict):
            selected_steps, selected_calendar = selected_with_downstream(
                dict(parent["result"]),
                [str(item) for item in list(selected_step_keys or [])],
                [str(item) for item in list(selected_calendar_keys or [])],
            )
            if not selected_steps and not selected_calendar:
                raise VaultError(
                    "home_intake_revision_scope_required",
                    "请先选择需要调整的流程卡片或日历安排",
                    status_code=422,
                )
            revision_context = {
                "base_draft": dict(parent["result"]),
                "selected_step_keys": selected_steps,
                "selected_calendar_keys": selected_calendar,
                "teacher_feedback": clean_answer,
                "locked_rule": "未选内容必须原样保留；安全必做步骤不得删除",
            }
        row = self._work_operation(operation_id)
        if row is None:
            raise VaultError("home_intake_operation_not_found", "首页处理操作不存在", status_code=404)
        prior: list[str]
        aliases: tuple[tuple[str, str], ...] = ()
        if str(row["operation_type"]) == "work.plan.invoke":
            receipt = self._decoded_receipt(row)
            context = receipt.get("local_context")
            context = dict(context) if isinstance(context, dict) else {}
        else:
            receipt = self._decoded_receipt(row)
            context = self.model.operation_context(token=token, operation_id=operation_id)
            raw_aliases = context.get("identity_aliases")
            if isinstance(raw_aliases, list):
                aliases = tuple(
                    (str(item[0]), str(item[1]))
                    for item in raw_aliases
                    if isinstance(item, list) and len(item) == 2
                )
        prior = [str(item) for item in list(context.get("prior_operations") or [])]
        prior.append(operation_id)
        source = str(context.get("source_text") or "")
        combined = f"{source}；教师对上一轮追问的补充：{clean_answer}"
        return self.prepare(
            token=token,
            text=combined,
            due_date=(
                str(context["final_due_date"]) if context.get("final_due_date") else None
            ),
            reference_date=reference_date,
            prior_operations=prior,
            round_number=int(parent.get("round_number") or 1) + 1,
            existing_aliases=aliases,
            revision_context=revision_context,
        )

    def confirm_manual_fallback(
        self,
        *,
        token: str,
        source_operation_id: str,
        operation_id: str,
        title: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, object]:
        source = self.status(token=token, operation_id=source_operation_id)
        if source.get("route") != "ordinary":
            raise VaultError(
                "home_intake_manual_fallback_ordinary_only",
                "敏感事项不能写成普通单节点任务",
                status_code=422,
            )
        context = source.get("local_context")
        context = dict(context) if isinstance(context, dict) else {}
        clean_title = _clean_text(title or str(context.get("source_text") or ""), maximum=240)
        final_due = due_date or (
            str(context["final_due_date"]) if context.get("final_due_date") else None
        )
        return self.work.create_manual_fallback(
            source_operation_id=source_operation_id,
            title=clean_title,
            due_date=final_due,
            operation_id=operation_id,
        )

    def _ordinary_status(self, operation_id: str) -> dict[str, object]:
        planned = self.work.plan_status(operation_id=operation_id)
        context = dict(planned.get("local_context") or {})
        prior = [str(item) for item in list(context.get("prior_operations") or [])]
        state = str(planned["state"])
        result_kind = None
        result = None
        questions = list(planned.get("questions") or [])
        if state == "succeeded":
            result_kind = str(planned.get("result_kind") or "ordinary_plan")
            result = planned.get("result") or planned.get("plan")
        elif state == "needs_information":
            result_kind = "follow_up"
        round_count = int(planned.get("physical_request_count") or 0)
        return self._result(
            operation_id=operation_id,
            route="ordinary",
            state=state,
            result_kind=result_kind,
            result=result,
            questions=questions,
            error_category=planned.get("error_category"),
            round_number=int(context.get("round_number") or 1),
            round_count=round_count,
            cumulative_count=self._cumulative_count("", prior) + round_count,
            teacher_confirmation_required=bool(planned.get("teacher_confirmation_required")),
            local_context=context,
            result_fingerprint=planned.get("plan_fingerprint"),
            assistant_message=planned.get("assistant_message"),
            validation_issue=planned.get("validation_issue"),
        )

    @staticmethod
    def _result(
        *,
        operation_id: str,
        route: str,
        state: str,
        result_kind: object,
        result: object,
        questions: list[str],
        error_category: object,
        round_number: int,
        round_count: int,
        cumulative_count: int,
        teacher_confirmation_required: bool,
        local_context: dict[str, object],
        result_fingerprint: object = None,
        assistant_message: object = None,
        validation_issue: object = None,
    ) -> dict[str, object]:
        return {
            "operation_id": operation_id,
            "route": route,
            "state": state,
            "result_kind": result_kind,
            "result": result,
            "follow_up_questions": questions,
            "can_follow_up": result_kind in {
                "follow_up",
                "plain_text",
                "ordinary_plan",
                "affair_recommendation",
                "student_support_recommendation",
            },
            "assistant_message": assistant_message,
            "validation_issue": validation_issue,
            "error_category": error_category,
            "round_number": round_number,
            "round_physical_request_count": round_count,
            "cumulative_physical_request_count": cumulative_count,
            "physical_request_count": round_count,
            "teacher_confirmation_required": teacher_confirmation_required,
            "result_fingerprint": result_fingerprint,
            "local_context": local_context,
        }

    @staticmethod
    def _interpret_sensitive_result(
        draft_text: str,
        *,
        recommended_route: str,
    ) -> dict[str, object]:
        clean = draft_text.strip()
        match = _FENCED_JSON.fullmatch(clean)
        json_text = match.group(1).strip() if match else clean
        try:
            decoded = json.loads(json_text)
        except (json.JSONDecodeError, TypeError):
            readable = " ".join(clean.split())
            if (
                match is None
                and 2 <= len(readable) <= 800
                and re.search(r"[一-鿿]", readable)
            ):
                return {
                    "state": "succeeded",
                    "kind": "plain_text",
                    "result": {"text": readable},
                    "questions": [],
                    "error_category": None,
                    "teacher_confirmation_required": True,
                }
            return HomeIntake._invalid_sensitive_result(
                error_category="nonmeaningful_plain_text",
            )
        if not isinstance(decoded, dict):
            return HomeIntake._invalid_sensitive_result(
                error_category="unsupported_result_shape",
            )
        raw_kind = decoded.get("kind")
        if raw_kind is not None and not isinstance(raw_kind, str):
            return HomeIntake._invalid_sensitive_result(
                error_category="unsupported_result_kind",
            )
        kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
        questions = HomeIntake._normalized_result_string_list(
            decoded.get("questions", []),
            maximum_items=3,
        )
        if questions is None:
            return HomeIntake._invalid_sensitive_result(
                error_category="invalid_questions",
            )
        assumptions = HomeIntake._normalized_result_string_list(
            decoded.get("assumptions", [])
        )
        if assumptions is None:
            return HomeIntake._invalid_sensitive_result(
                error_category="invalid_assumptions",
            )
        raw_nodes = decoded.get("nodes", [])
        raw_edges = decoded.get("edges", [])
        recommendation_payload_empty = all(
            decoded.get(key) in (None, "", [])
            for key in ("summary", "reasons", "text")
        )
        if (
            not kind
            and questions
            and raw_nodes == []
            and raw_edges == []
            and recommendation_payload_empty
        ):
            kind = "follow_up"
        if kind == "follow_up":
            if not questions:
                return HomeIntake._invalid_sensitive_result(
                    error_category="follow_up_questions_missing",
                )
            if (
                raw_nodes != []
                or raw_edges != []
                or not recommendation_payload_empty
            ):
                return HomeIntake._invalid_sensitive_result(
                    error_category="mixed_follow_up_result",
                )
            return {
                "state": "needs_information",
                "kind": "follow_up",
                "result": None,
                "questions": questions,
                "error_category": None,
                "teacher_confirmation_required": False,
            }
        if questions:
            return HomeIntake._invalid_sensitive_result(
                error_category="questions_require_follow_up_kind",
            )
        if kind == "plain_text":
            raw_text = decoded.get("text")
            if not isinstance(raw_text, str):
                return HomeIntake._invalid_sensitive_result(
                    error_category="invalid_plain_text",
                )
            text = " ".join(raw_text.split())
            if not text or len(text) > 800:
                return HomeIntake._invalid_sensitive_result(
                    error_category="invalid_plain_text",
                )
            return {
                "state": "succeeded",
                "kind": kind,
                "result": {"text": text},
                "questions": [],
                "error_category": None,
                "teacher_confirmation_required": True,
            }
        if kind in {"affair_recommendation", "student_support_recommendation"}:
            raw_summary = decoded.get("summary")
            if raw_summary is not None and not isinstance(raw_summary, str):
                return HomeIntake._invalid_sensitive_result(
                    error_category="invalid_recommendation_summary",
                )
            summary = None if raw_summary is None else " ".join(raw_summary.split())
            if summary == "":
                summary = None
            if summary is not None and len(summary) > 800:
                return HomeIntake._invalid_sensitive_result(
                    error_category="invalid_recommendation_summary",
                )
            reasons = HomeIntake._normalized_result_string_list(
                decoded.get("reasons", [])
            )
            if reasons is None:
                return HomeIntake._invalid_sensitive_result(
                    error_category="invalid_recommendation_reasons",
                )
            if kind.split("_recommendation")[0] != recommended_route:
                return HomeIntake._invalid_sensitive_result(
                    error_category="route_recommendation_mismatch",
                )
            return {
                "state": "succeeded",
                "kind": kind,
                "result": {
                    **decoded,
                    "kind": kind,
                    "summary": summary,
                    "reasons": reasons,
                    "assumptions": assumptions,
                    "questions": [],
                },
                "questions": [],
                "error_category": None,
                "teacher_confirmation_required": True,
            }
        return HomeIntake._invalid_sensitive_result(
            error_category="unsupported_result_kind",
        )

    @staticmethod
    def _normalized_result_string_list(
        value: object,
        *,
        maximum_items: int = 8,
    ) -> list[str] | None:
        if not isinstance(value, list) or len(value) > maximum_items:
            return None
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return None
            clean = " ".join(item.split())
            if not clean or len(clean) > 240:
                return None
            normalized.append(clean)
        return normalized

    @staticmethod
    def _invalid_sensitive_result(
        *,
        error_category: str,
        state: str = "invalid_result",
    ) -> dict[str, object]:
        return {
            "state": state,
            "kind": None,
            "result": None,
            "questions": [],
            "error_category": error_category,
            "teacher_confirmation_required": False,
        }

    @staticmethod
    def _route(text: str, redaction: RedactionResult) -> tuple[str, str]:
        prevention = bool(_PREVENTION_THEME.search(text))
        emergency = bool(_EMERGENCY.search(text))
        has_identity = bool(redaction.identity_aliases)
        if prevention and not emergency and not has_identity and not redaction.blocked_categories:
            return "ordinary", "ordinary_plan"
        if emergency:
            return "emergency", "affair" if _AFFAIR.search(text) else "student_support"
        if _AFFAIR.search(text):
            return "sensitive", "affair"
        if has_identity or _SUPPORT.search(text) or SensitiveContentPolicy.ordinary_findings(text):
            return "sensitive", "student_support"
        return "ordinary", "ordinary_plan"

    @staticmethod
    def _emergency_guidance() -> dict[str, object]:
        return {
            "priority": "before_ai",
            "title": "先处理现场安全，不要等待 AI",
            "steps": [
                "立即停止围观和进一步接触，在确保自身安全的前提下分隔风险源",
                "立即通知学校值班负责人、保卫人员或校医并按校内应急流程处置",
                "存在即时人身危险、严重伤情或无法控制的现场时，立即联系 110 或 120",
                "只记录已经确认的时间、地点、现场事实和已采取措施，不先作责任认定",
            ],
        }

    @staticmethod
    def _sensitive_output_contract() -> dict[str, object]:
        return {
            "allowed_kinds": [
                "affair_recommendation",
                "student_support_recommendation",
            ],
            "rules": [
                "第一轮必须基于现有信息形成可执行初稿，不得只追问；缺失信息写入 to_verify，不能阻止输出",
                "返回 template_key、title、summary、steps、calendar_items、assumptions 和 to_verify；steps 使用基线步骤 key，日历使用 calendar.<步骤key>",
                "按实际工作量排期；同一天可以安排多个可连续完成的步骤，不得机械地把每个步骤拆成一天",
                "若 context 含 revision_context，只允许调整 selected_step_keys 和 selected_calendar_keys；其他内容原样保留，安全必做步骤不得删除",
                "不得重复询问 task_text 中已有信息，不得索要不必要的姓名、电话或地址",
                "建议只供教师复核，不创建事务、学生记录或外发消息",
                "所有判断和建议都作为待教师核对的草稿，不得自动发送或自动结案",
            ],
            "draft_example": {
                "kind": "affair_recommendation",
                "template_key": "baseline.student_conflict",
                "title": "学生矛盾处理初稿",
                "summary": "先确保安全，再分别记录、核实并跟进。",
                "steps": [],
                "calendar_items": [],
                "assumptions": ["暂按普通学生矛盾处理"],
                "to_verify": ["是否有人受伤或仍存在即时风险"],
            },
        }

    def _destination(self) -> dict[str, object]:
        gateway = self.model.gateway
        snapshot = getattr(gateway, "destination_snapshot", None)
        if callable(snapshot):
            value = snapshot()
            if isinstance(value, dict) and value.get("destination_fingerprint"):
                return dict(value)
        available = bool(gateway.is_available())
        identity: dict[str, object] = {
            "available": available,
            "model_provider": getattr(gateway, "model_provider", None) if available else None,
            "model_endpoint": getattr(gateway, "model_endpoint", None) if available else None,
            "model": gateway.model_name if available else None,
        }
        identity["destination_fingerprint"] = hashlib.sha256(canonical_json(identity)).hexdigest()
        return identity

    def _claim_sensitive(self, operation_id: str, metadata: dict[str, object]) -> bool:
        self.database.initialize_schema() if not self.database.exists else None
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT operation_type, result_json FROM work_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is not None:
                    if str(row["operation_type"]) != _HOME_SENSITIVE_OPERATION:
                        raise VaultError(
                            "class_teacher_operation_conflict",
                            "同一操作编号不能用于不同操作",
                            status_code=409,
                        )
                    stored = self._decoded_receipt(row)
                    if str(stored.get("preview_id")) != str(metadata.get("preview_id")):
                        raise VaultError(
                            "class_teacher_operation_conflict",
                            "同一操作编号不能用于不同发送预览",
                            status_code=409,
                        )
                    return False
                connection.execute(
                    "INSERT INTO work_operations VALUES (?, ?, ?, ?)",
                    (
                        operation_id,
                        _HOME_SENSITIVE_OPERATION,
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        _iso(),
                    ),
                )
        return True

    def _has_model_operation(self, operation_id: str) -> bool:
        if not self.database.exists:
            return False
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM model_approval_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return row is not None

    def _set_sensitive_local_state(
        self,
        operation_id: str,
        *,
        state: str,
        error_category: str,
    ) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT operation_type, result_json FROM work_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None or str(row["operation_type"]) != _HOME_SENSITIVE_OPERATION:
                    raise VaultError(
                        "home_intake_integrity_error",
                        "首页处理记录未通过完整性校验",
                        status_code=409,
                    )
                metadata = self._decoded_receipt(row)
                metadata["local_state"] = state
                metadata["error_category"] = error_category
                connection.execute(
                    "UPDATE work_operations SET result_json = ? WHERE operation_id = ?",
                    (
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        operation_id,
                    ),
                )

    def _work_operation(self, operation_id: str):
        if not self.database.exists:
            return None
        with closing(self.database.connect()) as connection:
            return connection.execute(
                "SELECT operation_type, result_json FROM work_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()

    @staticmethod
    def _decoded_receipt(row: Any) -> dict[str, object]:
        try:
            value = json.loads(str(row["result_json"]))
        except json.JSONDecodeError as exc:
            raise VaultError(
                "home_intake_integrity_error",
                "首页处理记录未通过完整性校验",
                status_code=409,
            ) from exc
        if not isinstance(value, dict):
            raise VaultError(
                "home_intake_integrity_error",
                "首页处理记录未通过完整性校验",
                status_code=409,
            )
        return value

    def _cumulative_count(self, token: str, operations: list[str]) -> int:
        total = 0
        for operation_id in dict.fromkeys(operations):
            row = self._work_operation(operation_id)
            if row is None:
                continue
            operation_type = str(row["operation_type"])
            if operation_type == "work.plan.invoke":
                receipt = self._decoded_receipt(row)
                total += int(receipt.get("physical_request_count") or 0)
            elif operation_type == _HOME_SENSITIVE_OPERATION:
                try:
                    result = self.model.status(token=token, operation_id=operation_id)
                except VaultError:
                    total += 1
                else:
                    total += int(result.get("physical_request_count") or 0)
        return total

    @staticmethod
    def _validate_operation(operation_id: str) -> None:
        if _OPERATION_ID.fullmatch(str(operation_id or "")) is None:
            raise VaultError("home_intake_operation_id_invalid", "操作编号无效", status_code=422)


__all__ = ["HomeIntake", "interpret_local_date"]
