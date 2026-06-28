from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from integration.grading_paper_skill_workflow_service import (
    GradingPaperSkillWorkflowService,
    GradingPaperWorkflowStatus,
)


_STATE_LABELS = {
    "not_started": "未处理",
    "running": "处理中",
    "ready": "已完成",
    "partial": "部分完成",
    "failed": "失败",
}

_STAGE_LABELS = {
    "import": "解析并导入题目",
    "tag": "AI 批量打标签",
    "save": "保存题库标签",
    "link": "建立题目对应关系",
    "complete": "重算完整度",
}


def render_grading_paper_skill_workflow_card(
    service: GradingPaperSkillWorkflowService,
    session_id: int,
    *,
    key_prefix: str,
    ai_service_factory: Callable[[], Any],
    max_workers: int,
    requests_per_minute: int,
    compact: bool = False,
) -> GradingPaperWorkflowStatus:
    status = service.status(int(session_id))
    with st.container(border=True):
        title = "题库与知识图谱" if compact else "试卷入题库与知识图谱"
        st.markdown(f"{'**' if compact else '### '}{title}{'**' if compact else ''}")
        state_label = _STATE_LABELS.get(status.state, status.state)
        st.caption(
            f"状态：{state_label} · 导入题目 {status.imported_question_total} · "
            f"完整标签 {status.complete_tag_question_total}/{status.imported_question_total} · "
            f"已关联 {status.linked_source_total}/{status.source_question_total}"
        )
        if status.state == "ready":
            st.success("试卷已入库，知识图谱会直接读取当前题库标签。")
        else:
            st.info("建议把原始试卷走一遍题库打标签；可稍后处理，不影响批改。")
        if status.current_stage:
            stage_label = _STAGE_LABELS.get(status.current_stage, status.current_stage)
            st.caption(
                f"当前阶段：{stage_label} · 已发送模型请求 {status.request_count} · "
                f"失败题目 {status.failed_questions}"
            )
        if status.missing_items:
            missing_text = "；".join(
                f"{question_id}（{reason}）"
                for question_id, reason in status.missing_items
            )
            st.warning(f"缺失题目：{missing_text}")
        if status.error:
            st.warning(status.error)

        if not status.source_available:
            source_upload = st.file_uploader(
                "补充原始试卷",
                type=["docx", "pdf"],
                key=f"{key_prefix}_source_upload",
            )
            if st.button(
                "保存原始试卷",
                key=f"{key_prefix}_save_source",
                disabled=source_upload is None,
            ):
                try:
                    service.save_source(
                        int(session_id),
                        filename=str(source_upload.name),
                        content=bytes(source_upload.getvalue()),
                    )
                    st.success("原始试卷已保存，尚未入库。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"保存原始试卷失败：{exc}")

        button_label = "入库并打标签" if status.state == "not_started" else "继续处理缺失项"
        can_run = (
            status.state in {"not_started", "partial", "failed"}
            and status.source_available
        )
        if st.button(button_label, key=f"{key_prefix}_run", disabled=not can_run, type="primary"):
            progress = st.progress(0.0, text="准备处理试卷…")

            def on_progress(event: Any) -> None:
                ratio = event.completed / max(event.total, 1)
                stage_label = _STAGE_LABELS.get(event.stage, event.stage)
                progress.progress(
                    ratio,
                    text=(
                        f"{stage_label} · 请求 {event.request_count} · "
                        f"失败 {event.failed_questions}"
                    ),
                )

            try:
                next_status = service.run(
                    int(session_id),
                    ai_service=ai_service_factory(),
                    max_workers=int(max_workers),
                    requests_per_minute=int(requests_per_minute),
                    progress_callback=on_progress,
                )
                if next_status.state == "ready":
                    st.success("试卷已入库，知识图谱会直接读取当前题库标签。")
                elif next_status.state == "partial":
                    st.warning("已保留成功结果，仍有部分题目待补充。")
                else:
                    st.error(next_status.error or "题库处理失败，请重试。")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"题库处理失败，请重试：{exc}")
    return status


__all__ = ["render_grading_paper_skill_workflow_card"]
