from __future__ import annotations

import json
from pathlib import Path
import streamlit as st
import pandas as pd

from integration.sample_mastery_loader import list_sample_students, load_sample_mastery_rows
from integration.mastery_adapter import adapt_mastery_rows
from path_manager import get_path_manager
from question_bank.taxonomy.registry import canonicalize_knowledge, get_parent_knowledge_category

st.set_page_config(page_title="知识图谱适配调试", layout="wide")

# Theme styling & headers
st.markdown("""
<style>
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

st.title("🏷️ 知识图谱适配调试中心")
st.caption("对齐外部阅卷系统生成的「薄弱诊断点」与本题库系统的「标准图谱谱系」，确保推荐选题的精确性。")

pm = get_path_manager()
mapping_path = pm.config_dir / "knowledge_mapping.json"
mapping_path.parent.mkdir(parents=True, exist_ok=True)

# Pre-defined standard taxonomy categories based on registry rules
STANDARD_CATEGORIES = [
    "相交线与平行线",
    "三角形全等",
    "等腰三角形",
    "轴对称",
    "四边形",
    "图形相似",
    "二次函数",
    "反比例函数",
    "一次函数",
    "函数初步",
    "方程与方程组",
    "不等式与不等式组",
    "整式与因式分解",
    "分式",
    "数与式（实数）",
    "三角形",
    "圆",
    "图形与变换",
    "统计与概率"
]

# Helper to load mapping dictionary
def load_mapping_dict() -> dict[str, list[str]]:
    if mapping_path.exists():
        try:
            data = json.loads(mapping_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k).strip(): [str(vi).strip() for vi in v] if isinstance(v, list) else [str(v).strip()] for k, v in data.items()}
        except Exception as e:
            st.sidebar.error(f"加载映射文件出错: {e}")
    return {}

# Helper to save mapping dictionary
def save_mapping_dict(mapping: dict[str, list[str]]) -> None:
    try:
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success("💾 映射对齐规则字典已成功保存！")
    except Exception as e:
        st.error(f"保存失败: {e}")

# Load students & rows
try:
    rows = load_sample_mastery_rows()
    students = list_sample_students(rows)
except Exception as exc:
    st.error(f"加载学生诊断数据失败: {exc}")
    st.stop()

if not students:
    st.warning("暂无调试数据。")
    st.stop()

# Layout: Two Columns
col_match, col_editor = st.columns([1.1, 0.9], gap="large")

# Right Column: Mapping Dictionary Editor
with col_editor:
    st.subheader("🛠️ 映射规则词典编辑器")
    st.caption("手动关联阅卷输出词到题库标准分类。保存后将在题库选题中立即可用。")

    current_mapping = load_mapping_dict()
    
    # Flatten map for pandas editor
    flat_data = []
    for raw_word, std_list in current_mapping.items():
        std_word = std_list[0] if std_list else ""
        flat_data.append({"原始诊断词": raw_word, "映射标准词": std_word})
        
    df_mapping = pd.DataFrame(flat_data)
    if df_mapping.empty:
        df_mapping = pd.DataFrame(columns=["原始诊断词", "映射标准词"])

    # interactive data editor with standard dropdown options
    edited_df = st.data_editor(
        df_mapping,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "原始诊断词": st.column_config.TextColumn("原始诊断词 (如三视图、勾股定理)", required=True),
            "映射标准词": st.column_config.SelectboxColumn(
                "题库标准分类",
                options=STANDARD_CATEGORIES,
                required=True,
                help="对应标准题库图谱的顶层或二层知识树节点"
            )
        },
        key="mapping_editor_table"
    )

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        if st.button("💾 保存映射规则", use_container_width=True, type="primary"):
            new_map = {}
            for _, row_item in edited_df.iterrows():
                raw_w = str(row_item.get("原始诊断词", "")).strip()
                std_w = str(row_item.get("映射标准词", "")).strip()
                if raw_w and std_w:
                    new_map[raw_w] = [std_w]
            save_mapping_dict(new_map)
            st.rerun()
            
    with save_cols[1]:
        if st.button("🔄 刷新对齐状态", use_container_width=True):
            st.rerun()

# Left Column: Alignment Debugger
with col_match:
    st.subheader("🔍 知识图谱对齐自检")
    
    # Select Student
    student_labels = {item["label"]: item["student_id"] for item in students}
    selected_label = st.selectbox("选择要调试的学生", list(student_labels))
    sid = student_labels[selected_label]
    
    raw_student_rows = [r for r in rows if str(r.get("student_id") or r.get("student_code", "")).strip() == sid]
    profiles = adapt_mastery_rows(raw_student_rows, student_id=sid)
    
    if not profiles:
        st.warning("未能转换出学生档案。")
    else:
        profile = profiles[0]
        st.markdown(f"**学生**: `{profile.student_name}` | **班级**: `{profile.class_id}` | **诊断点数量**: `{len(profile.weak_points)}`")
        
        # Display alignment status list
        st.markdown("##### 📚 适配列表")
        
        table_data = []
        for wp in profile.weak_points:
            raw_kp = wp.raw_knowledge_point
            adapted_kp = wp.knowledge_point
            
            # Check canonical mapping status
            canonical = canonicalize_knowledge(adapted_kp)
            is_mapped = canonical is not None
            
            # Decide confidence rate
            if raw_kp in current_mapping:
                status = "🟢 手工绑定"
                confidence = "100%"
            elif is_mapped:
                status = "🟢 自动识别"
                confidence = "95%"
            else:
                parent = get_parent_knowledge_category(adapted_kp)
                if parent != adapted_kp and parent in STANDARD_CATEGORIES:
                    status = "🟡 模糊匹配"
                    confidence = "75%"
                    adapted_kp = parent
                else:
                    status = "🔴 待对齐"
                    confidence = "0%"
                    
            table_data.append({
                "原始诊断词": raw_kp,
                "适配后标准词": adapted_kp,
                "掌握度": f"{int(wp.mastery * 100)}%",
                "状态": status,
                "置信度": confidence
            })
            
        df_status = pd.DataFrame(table_data)
        st.dataframe(
            df_status,
            column_config={
                "状态": st.column_config.TextColumn("状态", width="medium"),
                "置信度": st.column_config.ProgressColumn("置信度", min_value=0, max_value=1, format="%d%%") if not df_status.empty else "置信度"
            },
            use_container_width=True,
            hide_index=True
        )

        # Quick Assist: Add unmatched nodes to mapping editor
        unmatched_raws = [row["原始诊断词"] for row in table_data if "🔴" in row["状态"]]
        if unmatched_raws:
            st.warning(f"检测到 {len(unmatched_raws)} 个诊断点尚未对齐。请在右侧字典编辑器中为它们设置标准分类。")
            st.info(f"待对齐名单: `{', '.join(unmatched_raws)}`")
