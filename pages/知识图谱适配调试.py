from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
import streamlit as st

from db_manager import DBManager
from path_manager import get_path_manager
from question_bank.database.schema import connect
from question_bank.models.knowledge_alignment import AlignmentStatus
from question_bank.services.concept_alignment_service import ConceptAlignmentService


ALIGNMENT_FOCUS_SESSION_KEY = "knowledge_alignment_focus_terms"
SOURCE_NAMESPACES = ("grading_weak_point", "question_tag", "canonical_knowledge_id")
STATUS_LABELS = {
    AlignmentStatus.CONFIRMED.value: "已确认",
    AlignmentStatus.SUGGESTED.value: "待确认",
    AlignmentStatus.UNMAPPED.value: "未映射",
    AlignmentStatus.REJECTED.value: "已拒绝",
}


def _add_source_term(
    target: dict[tuple[str, str], dict[str, Any]],
    *,
    source_namespace: str,
    source_value: object,
    evidence_count: int = 1,
) -> None:
    value = str(source_value or "").strip()
    if not value:
        return
    key = (source_namespace, value)
    row = target.setdefault(
        key,
        {
            "source_namespace": source_namespace,
            "source_value": value,
            "evidence_count": 0,
        },
    )
    row["evidence_count"] += max(1, int(evidence_count or 1))


def _load_real_source_terms() -> tuple[list[dict[str, Any]], list[str]]:
    pm = get_path_manager()
    terms: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []

    try:
        weak_points = DBManager(pm.db_path).get_active_global_weak_points()
        for item in weak_points:
            _add_source_term(
                terms,
                source_namespace="grading_weak_point",
                source_value=item.get("knowledge_label") or item.get("knowledge_id"),
                evidence_count=int(item.get("item_count") or 1),
            )
    except Exception as exc:
        warnings.append(f"读取批改薄弱知识点失败：{exc}")

    try:
        with connect(pm.qb_db_path) as conn:
            rows = conn.execute(
                """
                SELECT tag_type, tag_value, COUNT(*) AS evidence_count
                FROM question_tags
                WHERE tag_type IN ('knowledge_point', 'canonical_knowledge_id', 'prerequisite')
                  AND TRIM(tag_value) <> ''
                GROUP BY tag_type, tag_value
                ORDER BY tag_type, tag_value
                """
            ).fetchall()
        for row in rows:
            namespace = (
                "canonical_knowledge_id"
                if row["tag_type"] == "canonical_knowledge_id"
                else "question_tag"
            )
            _add_source_term(
                terms,
                source_namespace=namespace,
                source_value=row["tag_value"],
                evidence_count=int(row["evidence_count"] or 1),
            )
    except Exception as exc:
        warnings.append(f"读取题库知识点标签失败：{exc}")

    return sorted(
        terms.values(),
        key=lambda item: (item["source_namespace"], item["source_value"]),
    ), warnings


def _concept_options(service: ConceptAlignmentService) -> tuple[list[str], dict[str, int]]:
    concepts = service.list_concepts()
    labels = [f"{item.name} | {item.canonical_key} | #{item.id}" for item in concepts]
    return labels, {label: concept.id for label, concept in zip(labels, concepts)}


def _alignment_rows(
    service: ConceptAlignmentService,
    source_terms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    concept_labels, concept_ids = _concept_options(service)
    label_by_id = {concept_id: label for label, concept_id in concept_ids.items()}
    rows: list[dict[str, Any]] = []
    for source in source_terms:
        resolution = service.resolve(source["source_namespace"], source["source_value"])
        target_label = label_by_id.get(resolution.concept.id, "") if resolution.concept else ""
        rows.append(
            {
                "选择": False,
                "来源": source["source_namespace"],
                "原始词": source["source_value"],
                "状态": STATUS_LABELS[resolution.status.value],
                "建议/已绑定标准知识点": target_label,
                "置信度": resolution.confidence,
                "证据数": source["evidence_count"],
            }
        )
    return rows


def _render_summary(rows: list[dict[str, Any]]) -> None:
    counts = Counter(str(row["状态"]) for row in rows)
    columns = st.columns(4)
    for column, label in zip(columns, ("已确认", "待确认", "未映射", "已拒绝")):
        column.metric(label, counts.get(label, 0))


def _render_alignment_workbench(
    service: ConceptAlignmentService,
    source_terms: list[dict[str, Any]],
) -> None:
    st.subheader("集中对齐工作台")
    st.caption("自动识别只会生成待确认建议；只有教师确认后的映射才会进入训练推荐。")

    focus_terms = st.session_state.get(ALIGNMENT_FOCUS_SESSION_KEY) or []
    if focus_terms:
        st.info(f"从训练推荐带来的待处理知识点：{', '.join(str(item) for item in focus_terms)}")

    namespace = st.segmented_control(
        "来源范围",
        ["全部", *SOURCE_NAMESPACES],
        default="全部",
    )
    filtered_terms = [
        row
        for row in source_terms
        if namespace == "全部" or row["source_namespace"] == namespace
    ]
    rows = _alignment_rows(service, filtered_terms)
    _render_summary(rows)

    status_filter = st.multiselect(
        "状态筛选",
        ["待确认", "未映射", "已拒绝", "已确认"],
        default=["待确认", "未映射"],
    )
    visible_rows = [row for row in rows if row["状态"] in status_filter]
    concept_labels, concept_ids = _concept_options(service)
    if not concept_labels:
        st.warning("尚未建立标准知识点。请先在“标准知识点管理”中创建或从内置注册表导入。")

    edited = st.data_editor(
        pd.DataFrame(visible_rows),
        hide_index=True,
        width="stretch",
        disabled=["来源", "原始词", "状态", "置信度", "证据数"],
        column_config={
            "选择": st.column_config.CheckboxColumn("选择", default=False),
            "建议/已绑定标准知识点": st.column_config.SelectboxColumn(
                "建议/已绑定标准知识点",
                options=concept_labels,
            ),
            "置信度": st.column_config.ProgressColumn(
                "置信度",
                min_value=0.0,
                max_value=1.0,
                format="%.0f%%",
            ),
        },
        key="knowledge_alignment_pending_editor",
    )

    selected = [row for _, row in edited.iterrows() if bool(row.get("选择"))]
    confirm_col, reject_col, refresh_col = st.columns(3)
    with confirm_col:
        if st.button("批量确认", type="primary", width="stretch", disabled=not selected):
            mappings: list[tuple[str, str, int]] = []
            for row in selected:
                target_label = str(row.get("建议/已绑定标准知识点") or "")
                concept_id = concept_ids.get(target_label)
                if concept_id is not None:
                    mappings.append((str(row["来源"]), str(row["原始词"]), concept_id))
            if not mappings:
                st.error("请为选中的来源词选择标准知识点。")
            else:
                service.confirm_many(mappings, reviewed_by="teacher")
                st.success(f"已确认 {len(mappings)} 条映射。")
                st.rerun()
    with reject_col:
        if st.button("拒绝选中", width="stretch", disabled=not selected):
            for row in selected:
                service.reject_mapping(
                    str(row["来源"]),
                    str(row["原始词"]),
                    reviewed_by="teacher",
                )
            st.success(f"已拒绝 {len(selected)} 条映射。")
            st.rerun()
    with refresh_col:
        if st.button("刷新对齐状态", width="stretch"):
            st.rerun()


def _render_concept_editor(service: ConceptAlignmentService) -> None:
    st.subheader("标准知识点管理")
    concepts = service.list_concepts(status=None)
    st.dataframe(
        [
            {
                "ID": item.id,
                "稳定编码": item.canonical_key,
                "名称": item.name,
                "别名": "、".join(item.aliases),
            }
            for item in concepts
        ],
        hide_index=True,
        width="stretch",
    )

    import_col, create_col = st.columns([0.35, 0.65])
    with import_col:
        st.markdown("##### 内置注册表")
        st.caption("导入只会补充不存在的标准知识点，不会修改已有教师配置。")
        if st.button("从内置注册表补充", width="stretch"):
            created = service.seed_registry_concepts()
            st.success(f"新增 {created} 个标准知识点。")
            st.rerun()
    with create_col:
        st.markdown("##### 新建标准知识点")
        with st.form("create_knowledge_concept"):
            canonical_key = st.text_input("稳定编码", placeholder="math.quadratic_function")
            name = st.text_input("标准名称", placeholder="二次函数")
            aliases = st.text_input("别名（使用逗号分隔）")
            subject = st.text_input("学科", value="math")
            grade = st.text_input("适用年级（可留空）")
            submitted = st.form_submit_button("创建标准知识点", type="primary")
        if submitted:
            if not canonical_key.strip() or not name.strip():
                st.error("稳定编码和标准名称不能为空。")
            else:
                service.create_concept(
                    canonical_key,
                    name,
                    aliases=[item.strip() for item in aliases.split(",") if item.strip()],
                    subject=subject,
                    grade=grade,
                )
                st.success("标准知识点已创建。")
                st.rerun()

    if concepts:
        st.markdown("##### 编辑已有标准知识点")
        labels = {f"{item.name} | {item.canonical_key} | #{item.id}": item for item in concepts}
        selected_label = st.selectbox("选择标准知识点", list(labels), key="edit_concept_select")
        selected = labels[selected_label]
        with st.form("edit_knowledge_concept"):
            edited_name = st.text_input("名称", value=selected.name)
            edited_aliases = st.text_input("别名", value=",".join(selected.aliases))
            edited_status = st.selectbox("状态", ["active", "archived"])
            update_submitted = st.form_submit_button("保存标准知识点修改")
        if update_submitted:
            service.update_concept(
                selected.id,
                name=edited_name,
                aliases=[item.strip() for item in edited_aliases.split(",") if item.strip()],
                status=edited_status,
            )
            st.success("标准知识点已更新。")
            st.rerun()


def _render_relation_editor(service: ConceptAlignmentService) -> None:
    st.subheader("知识点关系管理")
    concepts = service.list_concepts()
    concept_labels = {f"{item.name} | #{item.id}": item.id for item in concepts}
    name_by_id = {item.id: item.name for item in concepts}
    relations = service.list_relations()
    st.dataframe(
        [
            {
                "来源知识点": name_by_id.get(int(row["source_concept_id"]), row["source_concept_id"]),
                "关系": row["relation_type"],
                "目标知识点": name_by_id.get(int(row["target_concept_id"]), row["target_concept_id"]),
                "权重": row["weight"],
            }
            for row in relations
        ],
        hide_index=True,
        width="stretch",
    )

    if len(concepts) < 2:
        st.info("至少需要两个标准知识点才能建立关系。")
        return
    with st.form("create_knowledge_relation"):
        source_label = st.selectbox("来源知识点", list(concept_labels))
        relation_type = st.selectbox("关系类型", ["prerequisite", "related", "parent"])
        target_label = st.selectbox("目标知识点", list(concept_labels))
        weight = st.slider("关系权重", 0.0, 1.0, 1.0, 0.05)
        submitted = st.form_submit_button("创建知识点关系", type="primary")
    if submitted:
        source_id = concept_labels[source_label]
        target_id = concept_labels[target_label]
        if source_id == target_id:
            st.error("来源知识点与目标知识点不能相同。")
        else:
            service.create_relation(source_id, target_id, relation_type, weight=weight)
            st.success("知识点关系已创建。")
            st.rerun()


st.set_page_config(page_title="知识点对齐中心", layout="wide")
st.title("知识点对齐中心")
st.caption("集中连接批改薄弱点、题库标签与稳定标准知识点，为后续训练推荐提供可信入口。")

path_manager = get_path_manager()
alignment_service = ConceptAlignmentService(path_manager.qb_db_path)
alignment_service.initialize_database()
real_source_terms, load_warnings = _load_real_source_terms()
for warning in load_warnings:
    st.warning(warning)

alignment_tab, concept_tab, relation_tab = st.tabs(
    ["集中对齐", "标准知识点管理", "知识点关系管理"]
)
with alignment_tab:
    _render_alignment_workbench(alignment_service, real_source_terms)
with concept_tab:
    _render_concept_editor(alignment_service)
with relation_tab:
    _render_relation_editor(alignment_service)
