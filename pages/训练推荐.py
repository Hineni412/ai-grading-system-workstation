from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import streamlit as st

from integration.mastery_adapter import adapt_mastery_rows
from integration.sample_mastery_loader import load_sample_mastery_rows
from question_bank.database.paths import question_bank_db_path
from question_bank.exporters.docx_exporter import export_training_docx
from question_bank.exporters.markdown_exporter import export_training_markdown
from question_bank.recommendation.training_plan import (
    generate_class_training_plan,
    generate_student_training_plan,
)


SELECTED_KEY = "training_recommendation_selected_questions"
PLAN_KEY = "training_recommendation_plan"
EXPORT_CONTEXT_KEY = "training_recommendation_export_context"


def _load_sample_profiles() -> list[dict]:
    return [profile.to_dict() for profile in adapt_mastery_rows(load_sample_mastery_rows())]


def _render_weak_points(weak_points: list[dict]) -> None:
    if not weak_points:
        st.info("当前没有可用于推荐的薄弱知识点。")
        return
    st.dataframe(
        [
            {
                "知识点": item.get("knowledge_point", ""),
                "掌握度": item.get("mastery", ""),
                "错因": "，".join(item.get("error_types", [])),
                "推荐层级": item.get("recommended_level", ""),
                "优先级": item.get("priority", ""),
            }
            for item in weak_points
        ],
        width="stretch",
        hide_index=True,
    )


def _render_recommendations(recommendations: list[dict], *, key_prefix: str) -> None:
    selected = st.session_state.setdefault(SELECTED_KEY, {})
    if not recommendations:
        st.info("当前没有匹配到可推荐题目，请先为题库题目补充知识点标签。")
        return

    for item in recommendations:
        question_id = str(item["question_id"])
        cols = st.columns([0.6, 1, 1.2, 1.2, 1, 3.6])
        with cols[0]:
            checked = st.checkbox(
                "加入",
                value=question_id in selected,
                key=f"{key_prefix}_pick_{question_id}",
                label_visibility="collapsed",
            )
        if checked:
            selected[question_id] = item
        else:
            selected.pop(question_id, None)
        cols[1].write(f"#{item['suggested_order']}")
        cols[2].write(f"{item['source_paper']} / {item['question_number']}")
        cols[3].write("，".join(item["knowledge_points"]))
        cols[4].write(f"{item['training_stage']} / 难度 {item['difficulty'] or '-'}")
        cols[5].write(item["recommend_reason"])


def _render_selected_preview() -> None:
    selected = st.session_state.setdefault(SELECTED_KEY, {})
    st.subheader("当前会话训练卷")
    if not selected:
        st.caption("勾选推荐题目后会在这里汇总，本阶段不写入训练集表。")
        return
    st.dataframe(
        [
            {
                "题目ID": item["question_id"],
                "来源": item["source_paper"],
                "题号": item["question_number"],
                "知识点": "，".join(item["knowledge_points"]),
                "阶段": item["training_stage"],
            }
            for item in selected.values()
        ],
        width="stretch",
        hide_index=True,
    )
    st.caption("导出只读取当前会话中已勾选的题目，不写入训练集表。")
    export_format = st.segmented_control("导出格式", ["docx", "markdown"], default="docx")
    use_real_name = st.checkbox("允许在导出标题中使用当前显示姓名/班级名", value=False)
    cols = st.columns(3)
    with cols[0]:
        if st.button("导出学生版"):
            _handle_export("student", export_format, use_real_name)
    with cols[1]:
        if st.button("导出教师版"):
            _handle_export("teacher", export_format, use_real_name)
    with cols[2]:
        if st.button("导出全部"):
            _handle_export("student", export_format, use_real_name)
            _handle_export("teacher", export_format, use_real_name)


def _handle_export(audience: str, export_format: str, use_real_name: bool) -> None:
    selected = list(st.session_state.get(SELECTED_KEY, {}).values())
    if not selected:
        st.warning("请先勾选要加入训练卷的题目。")
        return
    context = st.session_state.get(EXPORT_CONTEXT_KEY, {})
    try:
        exporter = export_training_markdown if export_format == "markdown" else export_training_docx
        output_path = exporter(
            question_bank_db_path(),
            selected,
            question_bank_db_path().parent / "outputs",
            audience=audience,
            display_name=context.get("display_name"),
            student_id=context.get("student_id"),
            class_id=context.get("class_id"),
            use_real_name=use_real_name,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"导出失败：{exc}")
        return
    st.success(f"已导出：{output_path}")


def _class_profiles(profiles: list[dict]) -> dict[str, list[dict]]:
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for profile in profiles:
        grouped[profile.get("class_id") or "sample-class"].append(profile)
    return dict(grouped)


st.set_page_config(page_title="训练推荐", layout="wide")
st.title("训练推荐")
st.caption("规则推荐只读取 fake mastery 与本地题库标签，不调用 AI。勾选题目仅保存在当前页面会话。")

try:
    sample_profiles = _load_sample_profiles()
except Exception as exc:  # noqa: BLE001
    st.error(f"读取 fake mastery sample 失败：{exc}")
    st.stop()

if not sample_profiles:
    st.warning("当前没有 sample mastery 数据可供推荐测试。")
    st.stop()

db_path = question_bank_db_path()
if not Path(db_path).exists():
    st.info("本地题库数据库尚未创建。你仍可查看 sample 薄弱点，导入并标注题目后再生成推荐。")

mode = st.segmented_control("推荐模式", ["学生推荐", "班级分层推荐"], default="学生推荐")
filters = {"db_path": db_path, "per_weak_point_limit": 5}

if mode == "学生推荐":
    profile_by_label = {
        f"{profile.get('student_name') or profile['student_id']} | {profile.get('class_id') or '未分班'} | {profile['student_id']}": profile
        for profile in sample_profiles
    }
    selected_label = st.selectbox("选择学生", list(profile_by_label))
    selected_profile = profile_by_label[selected_label]
    st.subheader("薄弱知识点")
    _render_weak_points(selected_profile["weak_points"])
    if st.button("生成学生推荐", type="primary"):
        st.session_state[EXPORT_CONTEXT_KEY] = {
            "mode": "student",
            "student_id": selected_profile.get("student_id"),
            "display_name": selected_profile.get("student_name"),
            "class_id": selected_profile.get("class_id"),
        }
        st.session_state[PLAN_KEY] = {
            "mode": "student",
            "student_id": selected_profile["student_id"],
            "plan": generate_student_training_plan(selected_profile, filters),
        }
    current = st.session_state.get(PLAN_KEY)
    if current and current.get("mode") == "student" and current.get("student_id") == selected_profile["student_id"]:
        st.subheader("推荐题目")
        _render_recommendations(current["plan"]["recommendations"], key_prefix=f"student_{selected_profile['student_id']}")
else:
    class_options = _class_profiles(sample_profiles)
    selected_class = st.selectbox("选择班级", list(class_options))
    selected_class_profiles = class_options[selected_class]
    st.subheader("班级 sample 学生薄弱点")
    _render_weak_points(
        [
            {
                **weak_point,
                "knowledge_point": f"{profile.get('student_name') or profile['student_id']} / {weak_point.get('knowledge_point', '')}",
            }
            for profile in selected_class_profiles
            for weak_point in profile.get("weak_points", [])
        ]
    )
    if st.button("生成班级分层推荐", type="primary"):
        st.session_state[EXPORT_CONTEXT_KEY] = {
            "mode": "class",
            "class_id": selected_class,
            "display_name": selected_class,
        }
        st.session_state[PLAN_KEY] = {
            "mode": "class",
            "class_id": selected_class,
            "plan": generate_class_training_plan(selected_class_profiles, filters),
        }
    current = st.session_state.get(PLAN_KEY)
    if current and current.get("mode") == "class" and current.get("class_id") == selected_class:
        for layer in current["plan"]["layers"]:
            with st.expander(f"{layer['layer_name']} | 覆盖 {layer['covered_student_count']} 名学生", expanded=True):
                _render_weak_points(layer["group_weak_points"])
                _render_recommendations(layer["recommendations"], key_prefix=f"class_{selected_class}_{layer['layer_name']}")

_render_selected_preview()
