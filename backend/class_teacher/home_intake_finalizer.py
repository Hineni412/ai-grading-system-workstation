from __future__ import annotations

import hashlib
from .affair_workspace import AffairWorkspace
from .crypto import canonical_json
from .errors import VaultError
from .home_intake import HomeIntake
from .home_intake_drafts import HomeIntakeDrafts
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
        drafts: HomeIntakeDrafts,
    ) -> None:
        self.intake = intake
        self.baselines = baselines
        self.affairs = affairs
        self.projections = projections
        self.drafts = drafts

    def adopt(
        self,
        *,
        token: str,
        source_operation_id: str,
        operation_id: str,
        result_fingerprint: str,
        subject_ids: list[str],
        draft_id: str | None = None,
        draft_version: int | None = None,
    ) -> dict[str, object]:
        if draft_id is not None and draft_version is not None:
            self.drafts.begin_adoption(
                token=token,
                draft_id=draft_id,
                version=draft_version,
                source_operation_id=source_operation_id,
                result_fingerprint=result_fingerprint,
            )
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
        response = {
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
        if draft_id is not None and draft_version is not None:
            self.drafts.mark_adopted(
                token=token,
                draft_id=draft_id,
                version=draft_version,
                affair_id=affair_id,
            )
        return response

    def adopt_ordinary(
        self,
        *,
        token: str,
        draft_id: str,
        expected_version: int,
        source_operation_id: str,
        result_fingerprint: str,
    ) -> dict[str, object]:
        snapshot = self.drafts.begin_adoption(
            token=token,
            draft_id=draft_id,
            version=expected_version,
            source_operation_id=source_operation_id,
            result_fingerprint=result_fingerprint,
        )
        if snapshot["result_kind"] != "ordinary_plan":
            raise VaultError(
                "home_intake_draft_kind_invalid",
                "该草案不是普通工作方案，不能写入普通工作图",
                status_code=409,
            )
        stable_operation_id = "home-draft-confirm-" + hashlib.sha256(
            draft_id.encode("utf-8")
        ).hexdigest()[:40]
        persisted = self.intake.work.confirm_plan(
            model_operation_id=source_operation_id,
            plan_fingerprint=result_fingerprint,
            operation_id=stable_operation_id,
        )
        self.drafts.mark_adopted(
            token=token,
            draft_id=draft_id,
            version=expected_version,
            affair_id=None,
        )
        return {
            "state": "complete",
            "draft_id": draft_id,
            "version": expected_version,
            "operation_id": stable_operation_id,
            "work": persisted,
            "physical_request_count": 0,
        }


__all__ = ["HomeIntakeFinalizer"]
