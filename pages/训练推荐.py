from __future__ import annotations

import io
import re
import zipfile
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


def parse_difficulty(val: object) -> int | None:
    try:
        text = str(val or "").strip()
        nums = [int(s) for s in re.findall(r"\d+", text)]
        if nums:
            return nums[0]
    except Exception:
        pass
    return None


def _render_weak_points(weak_points: list[dict]) -> None:
    if not weak_points:
        st.info("当前没有可用于推荐的薄弱知识点。")
        return

    # Render clean colored metric cards using HTML layout
    cols = st.columns(4)
    for idx, item in enumerate(weak_points):
        kp = item.get("知识点") or item.get("knowledge_point", "")
        mastery = item.get("掌握度") or item.get("mastery", 0.0)
        
        # Normalize mastery rate
        if isinstance(mastery, str) and "%" in mastery:
            try:
                mastery_val = float(mastery.replace("%", "")) / 100
            except Exception:
                mastery_val = 0.5
        else:
            try:
                mastery_val = float(mastery)
            except Exception:
                mastery_val = 0.5

        # Classify mastery levels with visual alerts
        if mastery_val < 0.40:
            color = "#fee2e2"      # Soft Red
            border = "#ef4444"     # Crimson
            text_color = "#991b1b" # Dark Red
            tag = "重度薄弱"
        elif mastery_val < 0.70:
            color = "#fef3c7"      # Soft Yellow
            border = "#f59e0b"     # Amber
            text_color = "#92400e" # Dark Orange
            tag = "中度薄弱"
        else:
            color = "#dcfce7"      # Soft Green
            border = "#22c55e"     # Green
            text_color = "#166534" # Dark Green
            tag = "轻度掌握"

        errors = item.get("错因") or "，".join(item.get("error_types", [])) or "暂无明确错因"
        priority = item.get("优先级") or item.get("priority", "3")

        with cols[idx % 4]:
            st.markdown(f"""
            <div style="background-color: {color}; border: 1px solid {border}; border-radius: 8px; padding: 12px; margin-bottom: 12px; color: {text_color};">
                <div style="font-weight: bold; font-size: 0.95rem; margin-bottom: 4px;">{kp}</div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin-bottom: 4px;">
                    <span>掌握率: <b>{int(mastery_val * 100)}%</b></span>
                    <span style="background-color: {border}; color: white; padding: 1px 6px; border-radius: 4px; font-size: 0.7rem; font-weight: bold;">{tag}</span>
                </div>
                <div style="font-size: 0.75rem; opacity: 0.9;">错因: {errors}</div>
                <div style="font-size: 0.75rem; opacity: 0.9;">优先级: {priority} ⭐</div>
            </div>
            """, unsafe_allow_html=True)


def _render_recommendations(recommendations: list[dict], *, key_prefix: str) -> None:
    selected = st.session_state.setdefault(SELECTED_KEY, {})
    if not recommendations:
        st.info("当前筛选范围内没有匹配到可推荐的题目，请调整难度过滤滑块。")
        return

    for item in recommendations:
        question_id = str(item["question_id"])
        
        # Calculate fit rate indicator
        score_val = item.get("recommend_score", 0.8)
        match_rate = int((0.5 + min(max(score_val, 0.0), 1.0) * 0.48) * 100)
        
        if match_rate >= 85:
            match_color = "green"
            match_bg = "#dcfce7"
        elif match_rate >= 70:
            match_color = "orange"
            match_bg = "#fef3c7"
        else:
            match_color = "gray"
            match_bg = "#f1f5f9"

        difficulty = item.get("difficulty") or "未知"
        stage = item.get("training_stage") or "未分类"
        kps = "，".join(item.get("knowledge_points", []))
        reason = item.get("recommend_reason", "")
        source = f"{item['source_paper']} / 第{item['question_number']}题"

        # Expandable/card container layout
        with st.container(border=True):
            header_cols = st.columns([0.1, 0.5, 0.2, 0.2])
            
            with header_cols[0]:
                checked = st.checkbox(
                    "pick",
                    value=question_id in selected,
                    key=f"{key_prefix}_pick_{question_id}",
                    label_visibility="collapsed",
                )
            if checked:
                selected[question_id] = item
            else:
                selected.pop(question_id, None)

            with header_cols[1]:
                st.markdown(f"**#{item['suggested_order']}** | **{source}**")
            with header_cols[2]:
                st.markdown(f"<span style='background-color: {match_bg}; color: {match_color}; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: bold;'>🎯 契合度 {match_rate}%</span>", unsafe_allow_html=True)
            with header_cols[3]:
                st.markdown(f"难度: `{difficulty}` | `{stage}`")

            st.markdown(f"<div style='font-size: 0.85rem; color: #475569; margin-top: 4px;'><b>知识点：</b>{kps}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size: 0.85rem; color: #0284c7;'><b>推荐理由：</b>{reason}</div>", unsafe_allow_html=True)


def _render_selected_preview() -> None:
    selected = st.session_state.setdefault(SELECTED_KEY, {})
    st.subheader("🛒 当前会话已选题（拼卷篮）")
    if not selected:
        st.caption("勾选上方的推荐题目后，这里会进行汇总预览。")
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
    st.caption("导出功能仅读取拼卷篮中选中的题目；不写入原系统成绩表。")
    export_format = st.segmented_control("导出格式", ["docx", "markdown"], default="docx")
    use_real_name = st.checkbox("允许在导出标题中使用真实姓名/班级名", value=False)
    
    cols = st.columns(3)
    with cols[0]:
        if st.button("导出学生版习题", use_container_width=True):
            _handle_export("student", export_format, use_real_name)
    with cols[1]:
        if st.button("导出教师版 (带解析)", use_container_width=True):
            _handle_export("teacher", export_format, use_real_name)
    with cols[2]:
        if st.button("清空选题篮", use_container_width=True):
            st.session_state[SELECTED_KEY] = {}
            st.rerun()


def _handle_export(audience: str, export_format: str, use_real_name: bool) -> None:
    selected = list(st.session_state.get(SELECTED_KEY, {}).values())
    if not selected:
        st.warning("请先在上方勾选要加入训练卷的题目。")
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
    except Exception as exc:
        st.error(f"导出失败：{exc}")
        return
    st.success(f"导出成功！文件已存盘：{output_path}")


def _handle_batch_zip_export(current_plan: dict, export_format: str, use_real_name: bool) -> None:
    layers = current_plan.get("plan", {}).get("layers", [])
    if not layers:
        st.warning("无可导出的分层推荐内容。")
        return

    zip_buffer = io.BytesIO()
    exporter = export_training_markdown if export_format == "markdown" else export_training_docx
    suffix = ".md" if export_format == "markdown" else ".docx"

    try:
        temp_dir = question_bank_db_path().parent / "outputs" / "temp_zip"
        temp_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for layer in layers:
                layer_name = layer["layer_name"]
                layer_recs = layer["recommendations"]
                if not layer_recs:
                    continue

                # Generate Student version
                student_path = exporter(
                    question_bank_db_path(),
                    layer_recs,
                    temp_dir,
                    audience="student",
                    display_name=layer_name,
                    class_id=current_plan.get("class_id"),
                    use_real_name=use_real_name
                )
                zip_file.write(student_path, arcname=f"{layer_name}_学生版{suffix}")
                student_path.unlink()

                # Generate Teacher version
                teacher_path = exporter(
                    question_bank_db_path(),
                    layer_recs,
                    temp_dir,
                    audience="teacher",
                    display_name=layer_name,
                    class_id=current_plan.get("class_id"),
                    use_real_name=use_real_name
                )
                zip_file.write(teacher_path, arcname=f"{layer_name}_教师版_带解析{suffix}")
                teacher_path.unlink()

        zip_data = zip_buffer.getvalue()
        
        st.download_button(
            label="📦 点击下载分层作业打包 ZIP (包含所有组的学生+教师版)",
            data=zip_data,
            file_name=f"{current_plan.get('class_id') or '班级'}_分层作业卷.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )
        st.success("🎉 分层作业卷已完成后台打包，请点击上方按钮下载！")
    except Exception as exc:
        st.error(f"打包导出失败: {exc}")


def _class_profiles(profiles: list[dict]) -> dict[str, list[dict]]:
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for profile in profiles:
        grouped[profile.get("class_id") or "sample-class"].append(profile)
    return dict(grouped)


# Streamlit Page Render
st.set_page_config(page_title="训练推荐", layout="wide")
st.title("🎯 个性化分层训练推荐")

# Sidebar Filters & Scaffolding Slide Controls
st.sidebar.markdown("### ⚙️ 选题脚手架过滤")
st.sidebar.caption("动态控制推荐题目的难度梯度区间")
difficulty_range = st.sidebar.slider(
    "难度爬坡区间 (1-10)",
    min_value=1,
    max_value=10,
    value=(3, 7),
    help="根据课标和学生薄弱情况，锁定推荐题目的难度下限与上限"
)

try:
    sample_profiles = _load_sample_profiles()
except Exception as exc:
    st.error(f"读取 fake mastery sample 失败：{exc}")
    st.stop()

if not sample_profiles:
    st.warning("当前没有 sample mastery 数据可供推荐测试。")
    st.stop()

db_path = question_bank_db_path()
if not Path(db_path).exists():
    st.info("本地题库数据库尚未创建。您仍可查看薄弱点分析，但无法匹配题目。")

mode = st.segmented_control("推荐模式", ["学生推荐", "班级分层推荐"], default="学生推荐")
filters = {"db_path": db_path, "per_weak_point_limit": 8} # retrieve more to filter locally

if mode == "学生推荐":
    profile_by_label = {
        f"{profile.get('student_name') or profile['student_id']} | {profile.get('class_id') or '未分班'} | {profile['student_id']}": profile
        for profile in sample_profiles
    }
    selected_label = st.selectbox("选择学生", list(profile_by_label))
    selected_profile = profile_by_label[selected_label]
    
    st.subheader("📊 学生薄弱诊断雷达")
    _render_weak_points(selected_profile["weak_points"])
    
    if st.button("⚡ 生成学生靶向推荐", type="primary"):
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
        raw_recs = current["plan"]["recommendations"]
        
        # Apply local difficulty slider filtering (Scaffolding range)
        filtered_recs = []
        for rec in raw_recs:
            diff_num = parse_difficulty(rec.get("difficulty"))
            if diff_num is None or (difficulty_range[0] <= diff_num <= difficulty_range[1]):
                filtered_recs.append(rec)
                
        st.subheader("📋 靶向提升题单")
        _render_recommendations(filtered_recs, key_prefix=f"student_{selected_profile['student_id']}")
        
else:
    class_options = _class_profiles(sample_profiles)
    selected_class = st.selectbox("选择班级", list(class_options))
    selected_class_profiles = class_options[selected_class]
    
    st.subheader("📊 班级薄弱分布诊断")
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
    
    if st.button("⚡ 生成班级分层推荐", type="primary"):
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
        st.subheader("📋 班级分层推荐结果")
        
        # Batch zip downloader settings
        st.markdown("##### 📦 分层作业打包")
        zip_format = st.segmented_control("打包文件格式", ["docx", "markdown"], default="docx", key="zip_format_sel")
        zip_real_name = st.checkbox("在文件名和标头中使用班级真实名称", value=True, key="zip_real_name_chk")
        _handle_batch_zip_export(current, zip_format, zip_real_name)
        st.divider()
        
        for layer in current["plan"]["layers"]:
            layer_recs = layer["recommendations"]
            
            # Apply local difficulty slider filtering (Scaffolding range)
            filtered_layer_recs = []
            for rec in layer_recs:
                diff_num = parse_difficulty(rec.get("difficulty"))
                if diff_num is None or (difficulty_range[0] <= diff_num <= difficulty_range[1]):
                    filtered_layer_recs.append(rec)
                    
            with st.expander(f"📌 {layer['layer_name']} | 覆盖 {layer['covered_student_count']} 名学生 (过滤后匹配 {len(filtered_layer_recs)} 题)", expanded=True):
                _render_weak_points(layer["group_weak_points"])
                _render_recommendations(filtered_layer_recs, key_prefix=f"class_{selected_class}_{layer['layer_name']}")

st.divider()
_render_selected_preview()
