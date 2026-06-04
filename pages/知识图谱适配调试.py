from __future__ import annotations

import streamlit as st

from integration.sample_mastery_loader import (
    build_sample_debug_payload,
    list_sample_students,
    load_sample_mastery_rows,
)


st.set_page_config(page_title="知识图谱适配调试", layout="wide")
st.title("知识图谱适配调试")
st.caption("当前页面只读取 fake sample，用于验证题库推荐侧的统一知识图谱格式。")

try:
    rows = load_sample_mastery_rows()
    students = list_sample_students(rows)
except Exception as exc:  # noqa: BLE001
    st.error(f"读取 fake mastery sample 失败：{exc}")
    st.stop()

if not students:
    st.warning("fake mastery sample 中没有可调试的学生。")
    st.stop()

student_labels = {item["label"]: item["student_id"] for item in students}
selected_label = st.selectbox("选择学生", list(student_labels))
payload = build_sample_debug_payload(student_labels[selected_label], rows=rows)

st.subheader("薄弱知识点原始行")
st.dataframe(payload["raw_rows"], width="stretch", hide_index=True)

st.subheader("Adapter 统一结构")
if payload["normalized"] is None:
    st.warning("没有生成统一结构。")
else:
    st.json(payload["normalized"], expanded=True)
