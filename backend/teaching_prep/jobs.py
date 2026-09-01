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


    registrar.register("reconcile", reconcile)
    registrar.register("material_parse", material_parse)
