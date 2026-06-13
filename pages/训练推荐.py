from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import streamlit as st

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from path_manager import get_path_manager
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.training_export_service import TrainingExportService
from question_bank.services.training_task_service import TrainingTaskService


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


def _scope_signature(scope: Mapping[str, Any], exam_scope: Mapping[str, Any]) -> str:
    return json.dumps(
        {"scope": dict(scope), "exam_scope": dict(exam_scope)},
        ensure_ascii=False,
        sort_keys=True,
    )


def _score_by_student(db: DBManager) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for row in db.get_active_student_score_rates():
        try:
            result[str(row["student_id"])] = float(row["avg_score_rate"])
        except (KeyError, TypeError, ValueError):
            result[str(row.get("student_id") or "")] = None
    return result


def _render_diagnosis(diagnosis: Mapping[str, Any]) -> None:
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
                    "证据题数": weak.get("evidence_count") or 0,
                }
            )
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("所选范围内暂时没有薄弱知识点证据。")

    excluded_terms = _unique_text([*suggested, *unmapped])
    if excluded_terms:
        st.warning(
            "以下知识点尚未确认映射，本次推荐会明确排除："
            + "、".join(excluded_terms)
        )
        if st.button("去知识图谱适配中心处理", key="open_alignment_center"):
            st.session_state[ALIGNMENT_FOCUS_SESSION_KEY] = excluded_terms
            st.switch_page("pages/知识图谱适配调试.py")

    for warning in diagnosis.get("warnings") or []:
        st.warning(str(warning))


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

    for variant in variants:
        students = "、".join(str(value) for value in variant.get("student_ids") or [])
        title = f"{variant.get('variant_key')} | 学生 {students or '未分配'} | {len(variant.get('items') or [])} 题"
        with st.expander(title, expanded=True):
            reason = variant.get("grouping_reason") or {}
            if reason:
                st.caption(f"分组依据：{reason.get('rule') or '未记录'}")
            items = list(variant.get("items") or [])
            if items:
                st.dataframe(
                    [
                        {
                            "顺序": item.get("item_order"),
                            "阶段": item.get("stage"),
                            "题库题号": item.get("question_id"),
                            "来源": item.get("source_paper"),
                            "难度": item.get("difficulty") or "未标注",
                            "推荐分": item.get("recommend_score"),
                        }
                        for item in items
                    ],
                    width="stretch",
                    hide_index=True,
                )
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


st.set_page_config(page_title="训练推荐", layout="wide")
st.title("个性化训练任务")
st.caption("基于真实批改诊断和已确认知识点映射生成训练题。未确认映射不会静默参与推荐。")

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
selection_signature = _scope_signature(scope, exam_scope)

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
        diagnosis = DiagnosisProfileService(pm.db_path, pm.qb_db_path).build_profiles(
            scope=scope,
            exam_scope=exam_scope,
        )
    except Exception as exc:
        st.error(f"诊断分析失败：{exc}")
    else:
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
    _render_diagnosis(diagnosis)
elif diagnosis is not None:
    st.info("学生或考试范围已变化，请重新分析薄弱知识点。")

st.subheader("4. 配置并生成训练任务")
variant_label = st.segmented_control(
    "训练版本",
    list(VARIANT_MODE_MAP),
    default="每人独立个性卷",
)
question_count = st.slider("每个版本题量", min_value=8, max_value=12, value=10)

st.markdown("**训练阶段比例**")
stage_columns = st.columns(3)
direct_percent = stage_columns[0].number_input("直接补弱 %", 0, 100, 60, 5)
prerequisite_percent = stage_columns[1].number_input("前置巩固 %", 0, 100, 25, 5)
transfer_percent = stage_columns[2].number_input("迁移验证 %", 0, 100, 15, 5)
stage_total = direct_percent + prerequisite_percent + transfer_percent

st.markdown("**推荐排序权重**")
weight_columns = st.columns(4)
concept_percent = weight_columns[0].number_input("知识点匹配 %", 0, 100, 40, 5)
frequency_percent = weight_columns[1].number_input("考频 %", 0, 100, 35, 5)
gradient_percent = weight_columns[2].number_input("难度梯度 %", 0, 100, 10, 5)
diversity_percent = weight_columns[3].number_input("来源与方法多样性 %", 0, 100, 15, 5)
weight_total = concept_percent + frequency_percent + gradient_percent + diversity_percent
exclude_current_exam_originals = st.checkbox(
    "排除当前所选考试的原题和可识别近重复题",
    value=True,
)

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
if weight_total != 100:
    generation_reasons.append("推荐排序权重合计必须为 100%")

if generation_reasons:
    st.info("当前不能生成：" + "；".join(_unique_text(generation_reasons)))

if st.button(
    "生成训练任务预览",
    type="primary",
    disabled=bool(generation_reasons),
):
    try:
        plan = PracticePlanService(pm.qb_db_path).generate(
            diagnosis,
            variant_mode=VARIANT_MODE_MAP[variant_label or "每人独立个性卷"],
            question_count=int(question_count),
            stage_ratios={
                "direct": direct_percent / 100,
                "prerequisite": prerequisite_percent / 100,
                "transfer": transfer_percent / 100,
            },
            weights={
                "concept": concept_percent / 100,
                "frequency": frequency_percent / 100,
                "gradient": gradient_percent / 100,
                "diversity": diversity_percent / 100,
            },
            exclude_current_exam_originals=exclude_current_exam_originals,
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
