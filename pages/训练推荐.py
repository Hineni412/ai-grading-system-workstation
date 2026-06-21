from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import streamlit as st

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from path_manager import get_path_manager
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.training_export_service import TrainingExportService
from question_bank.services.training_task_service import TrainingTaskService
from question_bank.services.question_service import QuestionService
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.models.knowledge_alignment import normalize_source_value
from question_bank.services.alignment_review_service import (
    AlignmentReviewService,
    apply_scope_exclusions,
    focus_items_from_diagnosis,
)

# 共享组件（难度 badge / 标签 chip 群），与题库管理页风格统一
try:
    import importlib
    import pages_shared.shared_components
    importlib.reload(pages_shared.shared_components)
    from pages_shared.shared_components import format_difficulty_badge
except Exception:  # 共享组件不可用时回退为纯文本
    def format_difficulty_badge(difficulty: object) -> str:
        try:
            v = float(difficulty)
        except (TypeError, ValueError):
            return '<span class="qb-badge qb-badge-gray">未标注</span>'
        return f'<span class="qb-badge qb-badge-gray">难度 {v:.1f}</span>' if v > 0 else '<span class="qb-badge qb-badge-gray">未标注</span>'


ALIGNMENT_FOCUS_SESSION_KEY = "knowledge_alignment_focus_terms"
DIAGNOSIS_KEY = "training_recommendation_diagnosis"
DIAGNOSIS_SIGNATURE_KEY = "training_recommendation_diagnosis_signature"
PLAN_KEY = "training_recommendation_plan"
PLAN_SIGNATURE_KEY = "training_recommendation_plan_signature"
SAVED_TASK_KEY = "training_recommendation_saved_task"

STUDENT_MODE_MAP = {
    "单个学生": "student",
    "筛选多个学生": "selected",
    "全部班级": "class",
}
EXAM_MODE_MAP = {
    "当前考试": "current",
    "跨考试": "cross_exam",
    "手动选择考试": "manual",
}
VARIANT_MODE_MAP = {
    "每人独立个性卷": "individual",
    "自动分组卷": "auto_group",
}
STAGE_LABELS = {
    "prerequisite": "基础巩固",
    "direct": "针对训练",
    "transfer": "提升应用",
}


def _student_label(student: Mapping[str, Any]) -> str:
    name = str(student.get("name") or student.get("student_name") or student.get("id") or "")
    code = str(student.get("student_code") or "")
    class_name = str(student.get("class_name") or "未分班")
    return f"{name} | {class_name} | {code}"


def _session_label(session: Mapping[str, Any]) -> str:
    return f"{session.get('session_name') or '未命名考试'} | ID {session['id']}"


def _unique_text(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _scope_signature(
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    alignment_revision: str,
) -> str:
    return json.dumps(
        {
            "scope": dict(scope),
            "exam_scope": dict(exam_scope),
            "alignment_revision": alignment_revision,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _scope_exclusion_key(
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
) -> str:
    stable_scope = json.dumps(
        {"scope": dict(scope), "exam_scope": dict(exam_scope)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"training_scope_exclusions:{stable_scope}"


def _score_by_student(db: DBManager) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for row in db.get_active_student_score_rates():
        try:
            result[str(row["student_id"])] = float(row["avg_score_rate"])
        except (KeyError, TypeError, ValueError):
            result[str(row.get("student_id") or "")] = None
    return result


def _render_diagnosis(
    diagnosis: Mapping[str, Any],
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
) -> None:
    students = list(diagnosis.get("students") or [])
    confirmed = list(diagnosis.get("confirmed_concept_ids") or [])
    suggested = list(diagnosis.get("suggested_terms") or [])
    unmapped = list(diagnosis.get("unmapped_terms") or [])
    columns = st.columns(4)
    columns[0].metric("学生", len(students))
    columns[1].metric("已确认标准知识点", len(confirmed))
    columns[2].metric("待确认术语", len(suggested))
    columns[3].metric("未映射术语", len(unmapped))

    rows: list[dict[str, Any]] = []
    for student in students:
        for weak in student.get("weak_points", []):
            rows.append(
                {
                    "学生": student.get("student_name") or student.get("student_id"),
                    "班级": student.get("class_id") or "",
                    "诊断术语": weak.get("source_term") or "",
                    "标准知识点": weak.get("concept_name") or "未映射",
                    "映射状态": weak.get("mapping_status") or "",
                    "掌握率": f"{float(weak.get('mastery') or 0) * 100:.1f}%",
                    "掌握率数值": float(weak.get('mastery') or 0),
                    "证据题数": weak.get("evidence_count") or 0,
                }
            )
            
    if rows:
        st.dataframe(
            pd.DataFrame(rows).drop(columns=["掌握率数值"]),
            width="stretch",
            hide_index=True
        )
        
        # Mastery heatmap
        confirmed_rows = [r for r in rows if r["标准知识点"] != "未映射" and r["映射状态"] == "confirmed"]
        if confirmed_rows:
            st.markdown("##### 📊 班级/学生薄弱点掌握率热力图")
            st.caption("展示所选学生在各个已确认标准知识点上的平均掌握率（红色代表薄弱度高，绿色代表掌握较好，-代表无数据）")
            try:
                df_heat = pd.DataFrame(confirmed_rows)
                pivot = df_heat.pivot_table(
                    index="学生",
                    columns="标准知识点",
                    values="掌握率数值",
                    aggfunc="mean"
                )
                st.dataframe(
                    pivot.style.background_gradient(cmap="RdYlGn", vmin=0.0, vmax=1.0)
                    .format("{:.1%}", na_rep="-"),
                    width="stretch",
                )
            except Exception as e:
                st.caption(f"（热力图渲染提示：{e}）")
    else:
        st.info("所选范围内暂时没有薄弱知识点证据。")

    focus_items = focus_items_from_diagnosis(diagnosis)
    confirmed_count = sum(
        1
        for student in students
        for weak in student.get("weak_points", [])
        if weak.get("eligible_for_recommendation")
    )
    if focus_items:
        st.info(
            f"已自动整理 {confirmed_count} 个薄弱点，还有 "
            f"{len(focus_items)} 个需要您确认。"
        )
        pm = get_path_manager()
        alignment_service = ConceptAlignmentService(pm.qb_db_path)
        review_service = AlignmentReviewService(pm.db_path, pm.qb_db_path)
        concepts = alignment_service.list_concepts()
        concept_by_id = {concept.id: concept for concept in concepts}
        concept_ids = list(concept_by_id)
        exclusion_key = _scope_exclusion_key(scope, exam_scope)
        exclusions = set(st.session_state.get(exclusion_key) or [])

        if not concept_ids:
            st.warning("标准知识点库为空，请先打开高级整理并导入标准知识点。")
        else:
            for item in focus_items:
                resolution = alignment_service.resolve(
                    item.source_namespace,
                    item.source_value,
                )
                suggested_id = (
                    resolution.concept.id if resolution.concept else None
                )
                default_index = (
                    concept_ids.index(suggested_id)
                    if suggested_id in concept_ids
                    else 0
                )
                item_key = normalize_source_value(item.source_value)
                with st.container(border=True):
                    st.markdown(f"**薄弱点：{item.source_value}**")
                    st.caption(
                        f"诊断来源：{item.display_value} · "
                        f"证据题数：{item.evidence_count}"
                    )
                    selected_id = st.selectbox(
                        "系统建议",
                        concept_ids,
                        index=default_index,
                        format_func=lambda concept_id: concept_by_id[
                            concept_id
                        ].name,
                        key=f"alignment_choice_{item_key}",
                    )
                    accept_col, skip_col = st.columns(2)
                    if accept_col.button(
                        "使用这个匹配",
                        key=f"alignment_accept_{item_key}",
                        use_container_width=True,
                    ):
                        refreshed = review_service.confirm_and_rebuild(
                            scope=scope,
                            exam_scope=exam_scope,
                            source_value=item.source_value,
                            concept_id=int(selected_id),
                        )
                        if exclusions:
                            refreshed = apply_scope_exclusions(
                                refreshed,
                                exclusions,
                            )
                        alignment_revision = (
                            review_service.alignment.revision_token()
                        )
                        st.session_state[DIAGNOSIS_KEY] = refreshed
                        st.session_state[DIAGNOSIS_SIGNATURE_KEY] = (
                            _scope_signature(
                                scope,
                                exam_scope,
                                alignment_revision,
                            )
                        )
                        st.session_state.pop(PLAN_KEY, None)
                        st.session_state.pop(PLAN_SIGNATURE_KEY, None)
                        st.session_state.pop(SAVED_TASK_KEY, None)
                        st.rerun()
                    if skip_col.button(
                        "本次不推荐",
                        key=f"alignment_skip_{item_key}",
                        use_container_width=True,
                    ):
                        exclusions.add(item.source_value)
                        st.session_state[exclusion_key] = sorted(exclusions)
                        st.session_state[DIAGNOSIS_KEY] = (
                            apply_scope_exclusions(diagnosis, exclusions)
                        )
                        st.session_state.pop(PLAN_KEY, None)
                        st.session_state.pop(PLAN_SIGNATURE_KEY, None)
                        st.session_state.pop(SAVED_TASK_KEY, None)
                        st.rerun()
    else:
        st.success(f"已自动整理 {confirmed_count} 个薄弱点，无需额外确认。")

    if st.button("打开知识点整理（高级）", key="open_advanced_alignment"):
        st.session_state[ALIGNMENT_FOCUS_SESSION_KEY] = [
            item.to_dict() for item in focus_items
        ]
        st.switch_page("pages/知识点整理（高级）.py")

def _render_question_images(question_detail: dict[str, Any]) -> None:
    IMAGE_MARKER_PATTERN = re.compile(r"\[image:\s*(?P<path>[^\]]+)\]", re.IGNORECASE)
    
    raw_images = question_detail.get("image_paths") or []
    text_content = (question_detail.get("question_text") or "") + " " + (question_detail.get("answer_text") or "")
    extracted_images = [m.group("path").strip() for m in IMAGE_MARKER_PATTERN.finditer(text_content)]
    
    all_images = []
    for img in list(raw_images) + extracted_images:
        img_str = str(img).strip()
        if img_str and img_str not in all_images:
            all_images.append(img_str)
            
    valid_paths = [Path(p) for p in all_images if Path(p).exists()]
    if not valid_paths:
        return
        
    if len(valid_paths) == 1:
        st.image(str(valid_paths[0]), use_container_width=True)
    else:
        cols = st.columns(min(len(valid_paths), 3))
        for idx, path in enumerate(valid_paths):
            with cols[idx % len(cols)]:
                st.image(str(path), use_container_width=True)


def _render_question_detail_expander(order: int, item: dict[str, Any], qb_service: QuestionService | None, key_prefix: str = "") -> None:
    q_id = item.get("question_id")
    stage = item.get("stage", "")
    stage_info = {
        "direct": ("🎯 针对训练", "purple"),
        "prerequisite": ("🧱 基础巩固", "blue"),
        "transfer": ("🚀 提升应用", "green"),
    }.get(stage, (stage or "推荐", "gray"))

    source = item.get("source_paper") or "题库"
    difficulty = item.get("difficulty") or 0

    # Format recommendation reason
    relation_type = item.get("relation_type", "")
    target_concept_id = item.get("target_concept_id")
    matched_concept_id = item.get("matched_concept_id")

    target_concept_name = "未知"
    matched_concept_name = "未知"
    if qb_service:
        try:
            align_service = ConceptAlignmentService(qb_service.db_path)
            if target_concept_id:
                c_target = align_service.get_concept(int(target_concept_id))
                if c_target:
                    target_concept_name = c_target.name
            if matched_concept_id:
                c_matched = align_service.get_concept(int(matched_concept_id))
                if c_matched:
                    matched_concept_name = c_matched.name
        except Exception:
            pass

    reason_desc = ""
    if relation_type == "direct":
        reason_desc = f"直接关联了您已确认的薄弱知识点「{target_concept_name}」"
    elif relation_type == "prerequisite":
        reason_desc = f"薄弱点「{target_concept_name}」的必备前置基础「{matched_concept_name}」（夯实根基）"
    elif relation_type == "parent":
        reason_desc = f"薄弱点「{target_concept_name}」的分类大纲「{matched_concept_name}」（分类巩固）"
    elif relation_type == "related":
        reason_desc = f"薄弱点「{target_concept_name}」的相关关联知识「{matched_concept_name}」（迁移突破）"
    elif relation_type == "difficulty_scaffold":
        reason_desc = f"针对薄弱点「{target_concept_name}」进行的难度搭建（易错拉练）"
    elif relation_type == "direct_transfer":
        reason_desc = f"针对薄弱点「{target_concept_name}」进行的综合题型挑战（提升应用）"
    else:
        reason_desc = "关联大纲知识点匹配推荐"

    # —— 白底卡片头部：题号标题 + 彩色 badges 一行 ——
    diff_badge = format_difficulty_badge(difficulty)
    source_badge = f'<span class="qb-badge qb-badge-gray">{html.escape(str(source))}</span>'
    stage_badge = f'<span class="qb-badge qb-badge-{stage_info[1]}">{html.escape(stage_info[0])}</span>'

    with st.container(border=True):
        st.markdown(
            f'<div class="qb-paper-header">'
            f'<span class="qb-paper-title-tag">第 {order} 题</span>'
            f'<div style="display:flex; gap:4px; flex-wrap:wrap;">'
            f'{stage_badge}{diff_badge}{source_badge}'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        col_q_left, col_q_right = st.columns([0.65, 0.35])

        with col_q_left:
            st.markdown("**📝 题目内容**")
            q_text = item.get("question_text") or ""
            IMAGE_MARKER_PATTERN = re.compile(r"\[image:\s*(?P<path>[^\]]+)\]", re.IGNORECASE)
            q_text_clean = IMAGE_MARKER_PATTERN.sub("", q_text).strip()
            st.markdown(
                f'<div class="qb-rich-text">{q_text_clean}</div>',
                unsafe_allow_html=True,
            )

            if qb_service:
                q_detail = qb_service.get_question(int(q_id))
                if q_detail:
                    _render_question_images(q_detail)
                    with st.expander("🔑 参考答案与解析"):
                        ans_text = q_detail.get("answer_text") or "暂无参考答案"
                        ans_text_clean = IMAGE_MARKER_PATTERN.sub("", ans_text).strip()
                        st.markdown(
                            f'<div class="qb-rich-text">{ans_text_clean}</div>',
                            unsafe_allow_html=True,
                        )
                        raw_ans_images = [m.group("path").strip() for m in IMAGE_MARKER_PATTERN.finditer(ans_text)]
                        if raw_ans_images:
                            valid_ans_paths = [Path(p) for p in raw_ans_images if Path(p).exists()]
                            if valid_ans_paths:
                                if len(valid_ans_paths) == 1:
                                    st.image(str(valid_ans_paths[0]), use_container_width=True)
                                else:
                                    cols = st.columns(min(len(valid_ans_paths), 3))
                                    for idx, path in enumerate(valid_ans_paths):
                                        with cols[idx % len(cols)]:
                                            st.image(str(path), use_container_width=True)
            else:
                st.caption("⚠️ 题库数据不可用，无法加载图片和答案。")

        with col_q_right:
            # 推荐理由：蓝边教师批注框
            st.markdown(
                f'<div class="qb-teacher-box">'
                f'<div class="qb-teacher-title">💡 推荐理由</div>'
                f'<div>{html.escape(reason_desc)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # 精准匹配子技能：chip 群
            sub_skills = item.get("sub_skill_match")
            if sub_skills:
                chips = "".join(
                    f'<span class="qb-tag-chip">{html.escape(str(s))}</span>'
                    for s in sub_skills
                )
                st.markdown(
                    f'<div class="qb-tag-row" style="margin-top:8px;">'
                    f'<span class="qb-tag-label">🎯 命中子技能</span>'
                    f'<div class="qb-tag-panel" style="display:flex; flex-wrap:wrap; gap:4px;">{chips}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # 评分拆解
            score_comps = item.get("score_components") or {}
            if score_comps:
                with st.expander("推荐依据（高级）"):
                    for comp_key, comp_val in score_comps.items():
                        comp_name = {
                            "concept": "标准知识点",
                            "fine_skill": "具体训练技能",
                            "frequency": "常见程度",
                            "gradient": "难度合适度",
                            "diversity": "题目多样性",
                        }.get(comp_key, comp_key)
                        st.progress(min(1.0, max(0.0, float(comp_val))), text=f"{comp_name}: {float(comp_val)*100:.0f}%")


def _render_plan(plan: Mapping[str, Any]) -> None:
    st.subheader("训练任务预览")
    variants = list(plan.get("variants") or [])
    total_items = sum(len(item.get("items") or []) for item in variants)
    columns = st.columns(3)
    columns[0].metric("训练版本", len(variants))
    columns[1].metric("入选题目", total_items)
    columns[2].metric(
        "缺题数量",
        sum(
            int(shortage.get("missing_count") or 0)
            for variant in variants
            for shortage in variant.get("shortages") or []
        ),
    )
    for warning in plan.get("warnings") or []:
        st.warning(str(warning))

    pm = get_path_manager()
    qb_service = QuestionService(pm.qb_db_path) if Path(pm.qb_db_path).exists() else None

    for variant in variants:
        students = "、".join(str(value) for value in variant.get("student_ids") or [])
        title = f"{variant.get('variant_key')} | 学生 {students or '未分配'} | {len(variant.get('items') or [])} 题"
        with st.expander(title, expanded=True):
            reason = variant.get("grouping_reason") or {}
            if reason:
                st.caption(f"分组依据：{reason.get('rule') or '未记录'}")
            items = list(variant.get("items") or [])
            if items:
                # 1. Summary table
                st.dataframe(
                    [
                        {
                            "顺序": item.get("item_order"),
                            "训练环节": STAGE_LABELS.get(item.get("stage"), item.get("stage")),
                            "题库题号": item.get("question_id"),
                            "来源": item.get("source_paper"),
                            "难度": item.get("difficulty") or "未标注",
                        }
                        for item in items
                    ],
                    width="stretch",
                    hide_index=True,
                )
                
                # 2. Detailed previews
                st.markdown("📋 **试题内容深度预览与推荐分析**")
                for item in items:
                    _render_question_detail_expander(item.get("item_order"), item, qb_service, key_prefix=f"plan_{variant.get('variant_key')}")
            else:
                st.info("当前版本没有符合硬性知识点映射规则的候选题。")
            for shortage in variant.get("shortages") or []:
                st.warning(
                    f"{shortage.get('stage')} 阶段缺少 "
                    f"{shortage.get('missing_count')} 题。"
                )
            for warning in variant.get("warnings") or []:
                st.warning(str(warning))


def _render_export_records(
    exports: list[dict[str, Any]],
    export_service: TrainingExportService,
    *,
    key_prefix: str,
) -> None:
    if not exports:
        st.caption("尚无导出记录。")
        return
    st.dataframe(
        [
            {
                "版本ID": item.get("variant_id") or "任务包",
                "受众": item.get("audience"),
                "格式": item.get("export_format"),
                "状态": item.get("status"),
                "重试次数": item.get("retry_count"),
                "文件": item.get("output_path") or "",
                "错误": item.get("error_message") or "",
            }
            for item in exports
        ],
        width="stretch",
        hide_index=True,
    )
    failed = [
        item
        for item in exports
        if item.get("status") == "failed" and item.get("audience") != "bundle"
    ]
    for item in failed:
        if st.button(
            f"重试失败导出 #{item['id']}（{item['audience']} / {item['export_format']}）",
            key=f"{key_prefix}_retry_{item['id']}",
        ):
            retried = export_service.retry_export(int(item["id"]))
            if retried["status"] == "succeeded":
                st.success(f"导出重试成功：{retried['output_path']}")
            else:
                st.error(f"导出仍失败：{retried['error_message']}")
    if any(item.get("status") == "failed" and item.get("audience") == "bundle" for item in exports):
        st.caption("完整任务包失败时，请修复单项失败后重新生成任务包。")


def _render_task_detail(
    task: Mapping[str, Any],
    export_service: TrainingExportService,
    *,
    key_prefix: str,
    allow_export: bool,
) -> None:
    columns = st.columns(4)
    columns[0].metric("任务状态", task.get("status") or "")
    columns[1].metric("训练版本", len(task.get("variants") or []))
    columns[2].metric(
        "题目数",
        sum(len(variant.get("items") or []) for variant in task.get("variants") or []),
    )
    columns[3].metric(
        "缺题数",
        sum(
            int(shortage.get("missing_count") or 0)
            for variant in task.get("variants") or []
            for shortage in variant.get("shortages") or []
        ),
    )
    st.caption(
        f"任务代码：{task.get('task_code')} | 创建时间：{task.get('created_at')} | "
        f"考试范围：{task.get('exam_scope')}"
    )
    for warning in task.get("warnings") or []:
        st.warning(str(warning))

    pm = get_path_manager()
    qb_service = QuestionService(pm.qb_db_path) if Path(pm.qb_db_path).exists() else None

    for variant in task.get("variants") or []:
        students = "、".join(
            str(item.get("student_name_snapshot") or item.get("student_id"))
            for item in variant.get("students") or []
        )
        with st.expander(
            f"{variant.get('variant_key')} | {students or '未分配学生'} | "
            f"{len(variant.get('items') or [])} 题"
        ):
            st.caption(f"分组依据：{variant.get('grouping_reason') or {}}")
            if variant.get("shortages"):
                st.warning(f"缺题记录：{variant['shortages']}")
            if variant.get("warnings"):
                st.warning("；".join(str(value) for value in variant["warnings"]))

            items = list(variant.get("items") or [])
            if items:
                # 1. Summary table
                st.dataframe(
                    [
                        {
                            "顺序": item.get("item_order"),
                            "训练环节": STAGE_LABELS.get(item.get("stage"), item.get("stage")),
                            "题库题号": item.get("question_id"),
                            "来源": item.get("source_paper"),
                            "难度": item.get("difficulty") or "未标注",
                        }
                        for item in items
                    ],
                    width="stretch",
                    hide_index=True,
                )
                
                # 2. Detailed previews
                st.markdown("📋 **试题内容深度预览与推荐分析**")
                for item in items:
                    _render_question_detail_expander(item.get("item_order"), item, qb_service, key_prefix=f"{key_prefix}_{variant.get('variant_key')}")

    if allow_export and task.get("status") != "cancelled":
        export_format = st.segmented_control(
            "导出格式",
            ["docx", "markdown"],
            default="docx",
            key=f"{key_prefix}_format",
        )
        export_columns = st.columns(2)
        if export_columns[0].button(
            "导出学生卷和教师卷",
            key=f"{key_prefix}_export_variants",
            use_container_width=True,
        ):
            results = [
                export_service.export_variant(
                    int(task["id"]),
                    int(variant["id"]),
                    formats=[export_format or "docx"],
                )
                for variant in task.get("variants") or []
            ]
            succeeded = sum(
                item["status"] == "succeeded"
                for result in results
                for item in result["exports"]
            )
            failed = sum(
                item["status"] == "failed"
                for result in results
                for item in result["exports"]
            )
            st.success(f"导出完成：成功 {succeeded} 个，失败 {failed} 个。")
        if export_columns[1].button(
            "导出完整任务包",
            key=f"{key_prefix}_export_bundle",
            use_container_width=True,
        ):
            bundle = export_service.export_task_bundle(
                int(task["id"]),
                formats=[export_format or "docx"],
            )
            if bundle["export"]["status"] == "succeeded":
                st.success(f"完整任务包已生成：{bundle['export']['output_path']}")
            else:
                st.error(f"完整任务包生成失败：{bundle['export']['error_message']}")

    _render_export_records(
        list(task.get("exports") or []),
        export_service,
        key_prefix=key_prefix,
    )


def _render_task_history(
    task_service: TrainingTaskService,
    export_service: TrainingExportService,
) -> None:
    st.subheader("历史训练任务")
    st.info("训练结果回流尚未启用：当前只保存稳定任务题码和预留证据结构，不会自动更新学生掌握度。")
    tasks = task_service.list_tasks()
    if not tasks:
        st.caption("尚无已保存训练任务。")
        return
    st.dataframe(
        [
            {
                "任务代码": item.get("task_code"),
                "创建时间": item.get("created_at"),
                "状态": item.get("status"),
                "学生范围": item.get("scope_snapshot"),
                "考试范围": item.get("exam_scope"),
            }
            for item in tasks[:30]
        ],
        width="stretch",
        hide_index=True,
    )
    task_by_code = {str(item["task_code"]): item for item in tasks[:30]}
    selected_code = st.selectbox(
        "查看历史任务详情",
        list(task_by_code),
        key="training_history_selected_task",
    )
    selected = task_by_code.get(selected_code)
    if selected:
        detail = task_service.get_task(int(selected["id"]))
        _render_task_detail(
            detail,
            export_service,
            key_prefix=f"history_{detail['id']}",
            allow_export=True,
        )


st.set_page_config(page_title="生成错题巩固练习", layout="wide")

try:
    from pages_shared.shared_styles import inject_shared_css
    inject_shared_css(st)
except Exception:
    pass

st.title("生成错题巩固练习")
st.caption("选好学生和考试，确认少数知识点例外后即可生成；系统默认只选择具体技能贴合的题目。")

pm = get_path_manager()
grading_db_available = Path(pm.db_path).exists()
question_bank_available = Path(pm.qb_db_path).exists()

if not grading_db_available:
    st.error("阅卷数据库不可用，无法读取学生和考试。")
    st.stop()

grading_db = DBManager(pm.db_path)
try:
    students = grading_db.list_students()
    sessions = grading_db.list_grading_sessions()
    score_by_student = _score_by_student(grading_db)
except Exception as exc:
    st.error(f"读取学生或考试失败：{exc}")
    st.stop()

if not question_bank_available:
    st.error("题库数据库不可用，当前只能检查选择范围，不能生成训练任务。")

st.subheader("1. 选择考试范围")
exam_mode_label = st.segmented_control(
    "考试范围",
    list(EXAM_MODE_MAP),
    default="当前考试",
)
exam_mode = EXAM_MODE_MAP[exam_mode_label or "当前考试"]
session_by_label = {_session_label(item): item for item in sessions}
current_session_id = st.session_state.get("selected_session_id")
active_session_ids = {int(item["id"]) for item in sessions}
if current_session_id not in active_session_ids:
    current_session_id = int(sessions[0]["id"]) if sessions else None

selected_session_ids: list[int]
if exam_mode == "current":
    selected_session_ids = [int(current_session_id)] if current_session_id is not None else []
    if selected_session_ids:
        current = next(item for item in sessions if int(item["id"]) == selected_session_ids[0])
        st.info(f"当前考试：{current.get('session_name') or current['id']}")
elif exam_mode == "cross_exam":
    selected_session_ids = [int(item["id"]) for item in sessions]
    st.caption(f"将综合当前全部 {len(selected_session_ids)} 场有效考试。")
else:
    selected_session_labels = st.multiselect(
        "手动选择考试",
        list(session_by_label),
        default=[],
    )
    selected_session_ids = [
        int(session_by_label[label]["id"]) for label in selected_session_labels
    ]

st.subheader("2. 选择学生范围")
student_mode_label = st.segmented_control(
    "学生范围",
    list(STUDENT_MODE_MAP),
    default="单个学生",
)
student_mode = STUDENT_MODE_MAP[student_mode_label or "单个学生"]
student_by_label = {_student_label(item): item for item in students}
classes = _unique_text(item.get("class_name") for item in students)
selected_student_ids: list[str] = []
selected_class = ""

if student_mode == "student":
    selected_label = st.selectbox(
        "选择学生",
        [""] + list(student_by_label),
        format_func=lambda value: value or "请选择学生",
    )
    if selected_label:
        selected_student_ids = [str(student_by_label[selected_label]["id"])]
elif student_mode == "selected":
    filter_columns = st.columns(2)
    selected_class_filter = filter_columns[0].selectbox(
        "先按班级筛选",
        ["全部班级"] + classes,
    )
    score_range = filter_columns[1].slider(
        "再按平均得分率筛选",
        min_value=0,
        max_value=100,
        value=(0, 100),
    )
    filtered_students = [
        item
        for item in students
        if (selected_class_filter == "全部班级" or item.get("class_name") == selected_class_filter)
        and (
            score_by_student.get(str(item["id"])) is None
            or score_range[0] <= float(score_by_student[str(item["id"])]) <= score_range[1]
        )
    ]
    filtered_by_label = {_student_label(item): item for item in filtered_students}
    explicit_labels = st.multiselect(
        "明确选择要训练的学生",
        list(filtered_by_label),
        help="筛选只缩小候选范围；只有这里明确选中的学生会保存到任务快照。",
    )
    selected_student_ids = [
        str(filtered_by_label[label]["id"]) for label in explicit_labels
    ]
    st.caption(f"筛选到 {len(filtered_students)} 人，已明确选择 {len(selected_student_ids)} 人。")
else:
    selected_class = st.selectbox("选择整个班级", [""] + classes)
    selected_student_ids = [
        str(item["id"]) for item in students if item.get("class_name") == selected_class
    ]
    if selected_class:
        st.caption(f"将覆盖 {selected_class} 的全部 {len(selected_student_ids)} 名学生。")

scope = {
    "mode": student_mode,
    "student_ids": selected_student_ids,
}
if selected_class:
    scope["class_id"] = selected_class
exam_scope = {
    "mode": exam_mode,
    "session_ids": selected_session_ids,
}
alignment_revision = (
    ConceptAlignmentService(pm.qb_db_path).revision_token()
    if question_bank_available
    else "question-bank-unavailable"
)
selection_signature = _scope_signature(
    scope,
    exam_scope,
    alignment_revision,
)

safety_reasons: list[str] = []
if not selected_student_ids:
    safety_reasons.append("未选择学生")
if not selected_session_ids:
    safety_reasons.append("未选择考试")
if not question_bank_available:
    safety_reasons.append("题库数据库不可用")

analyze_disabled = bool(safety_reasons)
if safety_reasons:
    st.info("当前不能分析：" + "；".join(safety_reasons))
if st.button(
    "分析薄弱知识点",
    type="primary",
    disabled=analyze_disabled,
):
    try:
        with st.spinner("正在分析薄弱知识点…"):
            diagnosis = DiagnosisProfileService(pm.db_path, pm.qb_db_path).build_profiles(
                scope=scope,
                exam_scope=exam_scope,
            )
    except Exception as exc:
        st.error(f"诊断分析失败：{exc}")
    else:
        alignment_revision = ConceptAlignmentService(
            pm.qb_db_path
        ).revision_token()
        selection_signature = _scope_signature(
            scope,
            exam_scope,
            alignment_revision,
        )
        exclusions = set(
            st.session_state.get(_scope_exclusion_key(scope, exam_scope))
            or []
        )
        if exclusions:
            diagnosis = apply_scope_exclusions(diagnosis, exclusions)
        st.session_state[DIAGNOSIS_KEY] = diagnosis
        st.session_state[DIAGNOSIS_SIGNATURE_KEY] = selection_signature
        st.session_state.pop(PLAN_KEY, None)
        st.session_state.pop(PLAN_SIGNATURE_KEY, None)
        st.session_state.pop(SAVED_TASK_KEY, None)
        st.rerun()

diagnosis = st.session_state.get(DIAGNOSIS_KEY)
diagnosis_is_current = (
    isinstance(diagnosis, Mapping)
    and st.session_state.get(DIAGNOSIS_SIGNATURE_KEY) == selection_signature
)
if diagnosis_is_current:
    st.subheader("3. 检查诊断与知识点覆盖")
    _render_diagnosis(
        diagnosis,
        scope=scope,
        exam_scope=exam_scope,
    )
elif diagnosis is not None:
    st.info("学生或考试范围已变化，请重新分析薄弱知识点。")

st.subheader("4. 生成练习")
variant_label = st.segmented_control(
    "训练版本",
    list(VARIANT_MODE_MAP),
    default="每人独立个性卷",
)
question_count = st.slider("每个版本题量", min_value=8, max_value=12, value=10)

exclude_current_exam_originals = st.checkbox(
    "排除当前所选考试的原题和可识别近重复题",
    value=True,
)

with st.expander("高级设置"):
    st.caption("通常无需修改。三类练习的比例合计需为 100%。")
    stage_columns = st.columns(3)
    prerequisite_percent = stage_columns[0].number_input("基础巩固 %", 0, 100, 30, 5)
    direct_percent = stage_columns[1].number_input("针对训练 %", 0, 100, 60, 5)
    transfer_percent = stage_columns[2].number_input("提升应用 %", 0, 100, 10, 5)
    allow_broad_fallback = st.checkbox(
        "允许仅大类匹配的题目补足（不推荐）",
        value=False,
        help="开启后，具体训练技能不匹配但标准知识点大类相同的题目也可能入选。",
    )
stage_total = direct_percent + prerequisite_percent + transfer_percent

confirmed_available = bool(
    diagnosis_is_current and diagnosis.get("confirmed_concept_ids")
)
generation_reasons = list(safety_reasons)
if not diagnosis_is_current:
    generation_reasons.append("请先分析当前选择范围")
elif not confirmed_available:
    generation_reasons.append("没有已确认映射的薄弱知识点")
if stage_total != 100:
    generation_reasons.append("训练阶段比例合计必须为 100%")

if generation_reasons:
    st.info("当前不能生成：" + "；".join(_unique_text(generation_reasons)))

if st.button(
    "生成练习预览",
    type="primary",
    disabled=bool(generation_reasons),
):
    try:
        with st.spinner("正在生成训练任务…"):
            plan = PracticePlanService(pm.qb_db_path).generate(
                diagnosis,
                variant_mode=VARIANT_MODE_MAP[variant_label or "每人独立个性卷"],
                question_count=int(question_count),
                stage_ratios={
                    "direct": direct_percent / 100,
                    "prerequisite": prerequisite_percent / 100,
                    "transfer": transfer_percent / 100,
                },
                exclude_current_exam_originals=exclude_current_exam_originals,
                allow_broad_fallback=allow_broad_fallback,
            )
    except Exception as exc:
        st.error(f"生成训练任务失败：{exc}")
    else:
        st.session_state[PLAN_KEY] = plan
        st.session_state[PLAN_SIGNATURE_KEY] = selection_signature
        st.session_state.pop(SAVED_TASK_KEY, None)
        st.rerun()

plan = st.session_state.get(PLAN_KEY)
plan_is_current = (
    isinstance(plan, Mapping)
    and st.session_state.get(PLAN_SIGNATURE_KEY) == selection_signature
)
if plan_is_current:
    _render_plan(plan)
    if st.button("保存训练任务", type="primary"):
        try:
            saved_task = TrainingTaskService(pm.qb_db_path).create_task(
                plan,
                created_by="teacher",
            )
        except Exception as exc:
            st.error(f"保存训练任务失败：{exc}")
        else:
            st.session_state[SAVED_TASK_KEY] = {
                "id": saved_task.id,
                "task_code": saved_task.task_code,
            }
            st.success(f"训练任务已保存：{saved_task.task_code}")
elif plan is not None:
    st.info("学生或考试范围已变化，旧预览不能保存，请重新分析并生成。")

saved_task = st.session_state.get(SAVED_TASK_KEY)
if saved_task:
    st.success(f"已保存任务：{saved_task['task_code']}。后续导出都从该固定快照生成。")
    saved_task_service = TrainingTaskService(pm.qb_db_path)
    saved_export_service = TrainingExportService(
        pm.qb_db_path,
        pm.outputs_dir / "training_tasks",
    )
    _render_task_detail(
        saved_task_service.get_task(int(saved_task["id"])),
        saved_export_service,
        key_prefix=f"saved_{saved_task['id']}",
        allow_export=True,
    )
else:
    st.info("请先保存训练任务，再执行导出。")

if question_bank_available:
    st.divider()
    _render_task_history(
        TrainingTaskService(pm.qb_db_path),
        TrainingExportService(pm.qb_db_path, pm.outputs_dir / "training_tasks"),
    )
