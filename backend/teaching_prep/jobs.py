from __future__ import annotations

from backend.jobs.manager import JobContext
from backend.teaching_prep.application import TeachingPrepService
from backend.workspaces.contracts import WorkspaceJobRegistrar


def register_jobs(
    registrar: WorkspaceJobRegistrar,
    service: object | None,
) -> None:
    if not isinstance(service, TeachingPrepService):
        raise TypeError("teaching-prep service is required for Job registration")

    def reconcile(context: JobContext) -> dict[str, object]:
        context.report(0.25, "checking", "Checking interrupted operations")
        context.raise_if_cancelled()
        interrupted = service.mark_interrupted_operations()
        context.report(1.0, "completed", "Interrupted operations reconciled")
        return {"interrupted_operations": interrupted}

    def material_parse(context: JobContext) -> dict[str, object]:
        material_version_id = str(
            context.payload.get("material_version_id") or ""
        ).strip()
        if len(material_version_id) != 32:
            raise ValueError("material_version_id is invalid")

        def report(phase: str, completed: int, total: int) -> None:
            context.raise_if_cancelled()
            clean_total = max(0, int(total))
            clean_completed = max(0, min(int(completed), clean_total))
            ratio = (
                clean_completed / clean_total
                if clean_total > 0
                else 1.0
            )
            if phase == "preview":
                progress = 0.03 + (0.22 * ratio)
                detail = f"正在生成原页预览：{clean_completed}/{clean_total} 页"
            elif phase == "ocr":
                progress = 0.25 + (0.70 * ratio)
                detail = (
                    f"正在本机识别扫描文字：{clean_completed}/{clean_total} 页"
                    if clean_total > 0
                    else "资料已有文字层，不需要扫描文字识别"
                )
            else:
                progress = 0.98
                detail = "正在核对页数并发布页级索引"
            context.report(progress, phase, detail)

        context.report(0.01, "checking", "正在核对资料文件和版本")
        units = service.parse_material_version(
            material_version_id,
            progress_callback=report,
            cancel_check=context.raise_if_cancelled,
        )
        context.report(1.0, "completed", f"已完成 {len(units)} 页/张")
        return {
            "material_version_id": material_version_id,
            "unit_count": len(units),
        }

    def semester_mapping(context: JobContext) -> dict[str, object]:
        semester_id = str(context.payload.get("semester_id") or "").strip()
        material_record_id = str(
            context.payload.get("material_record_id") or ""
        ).strip()
        operation_id = str(context.payload.get("operation_id") or "").strip()
        source_state_sha256 = str(
            context.payload.get("source_state_sha256") or ""
        ).strip()
        if len(semester_id) != 32 or len(material_record_id) != 32:
            raise ValueError("semester mapping target is invalid")
        if not 8 <= len(operation_id) <= 96:
            raise ValueError("semester mapping operation_id is invalid")
        if (
            len(source_state_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_state_sha256)
        ):
            raise ValueError("semester mapping source digest is invalid")

        stage_progress = {
            "checking": (0.05, "正在核对学期、资料和模型配置"),
            "snapshotting": (0.12, "正在固定本次发送范围"),
            "claiming_operation": (0.20, "正在取得防重复调用权"),
            "calling_model": (0.35, "模型请求已开始，正在等待返回"),
            "validating_response": (0.75, "正在校验模型返回的目录和页码"),
            "persisting_proposal": (0.90, "正在保存待确认建议"),
            "recovered": (1.0, "已恢复此前保存的待确认建议"),
            "completed": (1.0, "待确认建议已保存"),
        }

        def report(stage: str) -> None:
            progress, detail = stage_progress[stage]
            context.report(progress, stage, detail)

        proposal, created = service.generate_semester_mapping_proposal(
            semester_id,
            operation_id=operation_id,
            material_record_ids=[material_record_id],
            expected_source_state_sha256=source_state_sha256,
            progress_callback=report,
            cancel_check=context.raise_if_cancelled,
        )
        return {
            "semester_id": semester_id,
            "operation_id": proposal.operation_id,
            "source_state_sha256": proposal.source_state_sha256,
            "proposal_id": proposal.id,
            "recovered_existing": not created,
        }

    registrar.register("reconcile", reconcile)
    registrar.register("material_parse", material_parse)
    registrar.register("semester_mapping", semester_mapping)
