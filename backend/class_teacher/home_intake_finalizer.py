from __future__ import annotations

import hashlib
from .affair_workspace import AffairWorkspace
from .crypto import canonical_json
from .errors import VaultError
from .home_intake import HomeIntake
from .sensitive_work_projection import SensitiveWorkProjection
from .sop_baseline_service import SopBaselineService


class HomeIntakeFinalizer:
    """Idempotently adopt one reviewed sensitive draft across both databases."""

    def __init__(
        self,
        intake: HomeIntake,
        baselines: SopBaselineService,
        affairs: AffairWorkspace,
        projections: SensitiveWorkProjection,
    ) -> None:
        self.intake = intake
        self.baselines = baselines
        self.affairs = affairs
        self.projections = projections

    def adopt(
        self,
        *,
        token: str,
        source_operation_id: str,
        operation_id: str,
        result_fingerprint: str,
        subject_ids: list[str],
    ) -> dict[str, object]:
        status = self.intake.status(token=token, operation_id=source_operation_id)
        result = status.get("result")
        if status.get("result_kind") not in {
            "affair_recommendation",
            "student_support_recommendation",
        } or not isinstance(result, dict):
            raise VaultError(
                "home_intake_adopt_not_ready", "当前方案还不能保存", status_code=409
            )
        actual_fingerprint = hashlib.sha256(canonical_json(result)).hexdigest()
        if actual_fingerprint != str(result_fingerprint or ""):
            raise VaultError(
                "home_intake_result_changed", "方案已经变化，请重新检查后保存", status_code=409
            )
        selected_subjects = list(dict.fromkeys(str(item) for item in subject_ids))
        if not selected_subjects:
            raise VaultError(
                "home_intake_subject_required",
                "请先确认该事务关联的我班学生",
                status_code=422,
            )
        request_fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "source_operation_id": source_operation_id,
                    "result_fingerprint": actual_fingerprint,
                    "subject_ids": sorted(selected_subjects),
                }
            )
        ).hexdigest()
        stable_affair_operation = "home-adopt-" + hashlib.sha256(
            source_operation_id.encode("utf-8")
        ).hexdigest()[:40]
        templates = self.baselines.ensure_baselines(token=token)["items"]
        template_key = str(result.get("template_key") or "")
        template = next(
            (item for item in templates if str(item.get("template_key")) == template_key),
            None,
        )
        if template is None:
            raise VaultError(
                "home_intake_template_unavailable", "对应事务流程暂不可用", status_code=409
            )
        affair = self.affairs.create(
            token=token,
            operation_id=stable_affair_operation,
            template_version_id=str(template["template_version_id"]),
            title=str(result.get("title") or "班主任事务"),
            summary=(None if result.get("summary") is None else str(result["summary"])),
            participant_refs=[],
            subject_ids=selected_subjects,
            idempotency_fingerprint=request_fingerprint,
        )
        affair_id = str(affair["affair_id"])
        occurrence_id = str(affair["occurrence_id"])
        applied = 0
        for item in list(result.get("calendar_items") or []):
            if not isinstance(item, dict):
                continue
            step_key = str(item.get("step_key") or item.get("key") or "")
            if not step_key:
                continue
            projected = self.projections.upsert(
                token=token,
                source_kind="sensitive_affair",
                source_id=affair_id,
                occurrence_id=f"{occurrence_id}-{step_key}",
                state="pending",
                due_date=(None if item.get("due_date") is None else str(item["due_date"])),
            )
            if projected.get("projection_state") != "applied":
                raise VaultError(
                    "home_intake_calendar_projection_pending",
                    "方案已保存，日历仍在恢复中；请使用同一保存按钮继续",
                    status_code=503,
                )
            applied += 1
        return {
            "state": "complete",
            "source_operation_id": source_operation_id,
            "operation_id": operation_id,
            "affair": affair,
            "student_link_count": len(selected_subjects),
            "calendar_projection_count": applied,
            "physical_request_count": 0,
            "recovery": {
                "affair_stage": "complete",
                "student_links_stage": "complete",
                "calendar_stage": "complete",
            },
        }


__all__ = ["HomeIntakeFinalizer"]
