from __future__ import annotations

import html
import time
from datetime import datetime
from collections import Counter
from typing import Any

import pandas as pd
import streamlit as st

from db_manager import DBManager
from path_manager import get_path_manager
from question_bank.database.schema import connect
from question_bank.models.knowledge_alignment import AlignmentStatus
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.services.ai_tagging_service import AITaggingService


ALIGNMENT_FOCUS_SESSION_KEY = "knowledge_alignment_focus_terms"
SOURCE_NAMESPACES = ("grading_weak_point", "question_tag", "canonical_knowledge_id")
STATUS_LABELS = {
    AlignmentStatus.CONFIRMED.value: "已确认",
    AlignmentStatus.SUGGESTED.value: "待确认",
    AlignmentStatus.UNMAPPED.value: "未映射",
    AlignmentStatus.REJECTED.value: "已拒绝",
}
NAMESPACE_MAP = {
    "grading_weak_point": "📝 阅卷诊断薄弱点",
    "question_tag": "🏷️ 题库题目标签",
    "canonical_knowledge_id": "🔑 题库标准编码",
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
    auto_select_threshold: float | None = None,
) -> list[dict[str, Any]]:
    concept_labels, concept_ids = _concept_options(service)
    label_by_id = {concept_id: label for label, concept_id in concept_ids.items()}
    rows: list[dict[str, Any]] = []
    for source in source_terms:
        resolution = service.resolve(source["source_namespace"], source["source_value"])
        target_label = label_by_id.get(resolution.concept.id, "") if resolution.concept else ""
        sub_skills = "、".join(resolution.sub_skill_tags) if hasattr(resolution, "sub_skill_tags") and resolution.sub_skill_tags else ""
        # 自动勾选：仅在"已有建议知识点(置信度>0 且 concept 非空)"且置信度≥阈值时默认勾选
        auto_selected = (
            auto_select_threshold is not None
            and resolution.concept is not None
            and resolution.confidence > 0
            and resolution.confidence >= auto_select_threshold
        )
        rows.append(
            {
                "选择": bool(auto_selected),
                "来源": NAMESPACE_MAP.get(source["source_namespace"], source["source_namespace"]),
                "原始词": source["source_value"],
                "状态": STATUS_LABELS[resolution.status.value],
                "建议/已绑定标准知识点": target_label,
                "子技能标签": sub_skills,
                "置信度": resolution.confidence,
                "证据数": source["evidence_count"],
            }
        )
    return rows


def _render_summary(rows: list[dict[str, Any]]) -> None:
    counts = Counter(str(row["状态"]) for row in rows)
    total = len(rows)
    # 来源分布
    source_counts = Counter(str(row["来源"]) for row in rows)
    source_bits = " · ".join(f"{name}: {cnt}" for name, cnt in source_counts.most_common())

    # 4 个统计卡片：色块 + 状态名 + 数字
    items = [
        ("已确认", counts.get("已确认", 0), "#10B981"),  # 绿
        ("待确认", counts.get("待确认", 0), "#F59E0B"),  # 黄
        ("未映射", counts.get("未映射", 0), "#EF4444"),  # 红
        ("已拒绝", counts.get("已拒绝", 0), "#9CA3AF"),  # 灰
    ]
    cells = []
    for label, value, color in items:
        cells.append(
            f'<div style="flex:1; min-width:0; background:#fff; border:1px solid #e5e7eb;'
            f'border-left:4px solid {color}; border-radius:8px; padding:10px 12px;">'
            f'<div style="font-size:0.78rem; color:#6b7280; margin-bottom:2px;">{label}</div>'
            f'<div style="font-size:1.5rem; font-weight:700; color:{color}; line-height:1.1;">{value}</div>'
            f'</div>'
        )
    st.markdown(
        '<div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:6px;">'
        + "".join(cells)
        + "</div>",
        unsafe_allow_html=True,
    )
    if total:
        st.caption(f"共 {total} 个原始词 — {source_bits}")


def _render_alignment_workbench(
    service: ConceptAlignmentService,
    source_terms: list[dict[str, Any]],
) -> None:
    st.subheader("集中对齐工作台")
    st.caption("自动识别只会生成待确认建议；只有教师确认后的映射才会进入训练推荐。")

    # Initialize AI client for batch align
    tagging_service = AITaggingService()
    llm_client = tagging_service.llm_client

    # AI batch align button layout
    col_ai, col_info = st.columns([0.4, 0.6])
    with col_ai:
        ai_btn_disabled = (llm_client is None)
        if st.button(
            "🤖 AI 一键对齐",
            type="primary",
            use_container_width=True,
            disabled=ai_btn_disabled,
            help="调用 AI 批量对齐所有待确认及未映射的原始词，并自动提取细粒度子技能标签",
        ):
            # Gather unconfirmed/unmapped source terms
            unaligned = []
            for source in source_terms:
                res_temp = service.resolve(source["source_namespace"], source["source_value"])
                if res_temp.status in (AlignmentStatus.SUGGESTED, AlignmentStatus.UNMAPPED):
                    unaligned.append(source)

            if unaligned:
                from collections import defaultdict
                by_ns = defaultdict(list)
                for item in unaligned:
                    by_ns[item["source_namespace"]].append(item["source_value"])

                with st.status("🤖 AI 正在批量对齐并提取子技能标签...", expanded=True) as status:
                    log_lines = []
                    log_container = st.empty()
                    progress_bar = st.progress(0.0)

                    def update_progress(completed: int, total: int, terms: list[str], results: list[Any], error_info: dict[str, Any] = None):
                        progress_val = min(float(completed) / total, 1.0)
                        progress_bar.progress(progress_val)

                        timestamp = datetime.now().strftime('%H:%M:%S')
                        log_lines.append(f"[{timestamp}] 📦 处理分片 {completed}/{total} (本组待处理 {len(terms)} 个原始词):")

                        if not results:
                            log_lines.append("   ⚠️ 接口异常或返回数据解析为空。")
                            if error_info:
                                if error_info.get("error"):
                                    log_lines.append(f"      ❌ 错误原因: {error_info['error']}")
                                if error_info.get("raw_response") is not None:
                                    log_lines.append(f"      ℹ️ 原始返回内容: {error_info['raw_response']}")
                        else:
                            for item in results:
                                if isinstance(item, list) and len(item) >= 2:
                                    orig = item[0]
                                    key = item[1]
                                    sub_tags = item[2] if len(item) > 2 else []
                                    conf = item[3] if len(item) > 3 else 0.5

                                    # Format sub-skills display
                                    sub_skills_str = f"，子技能: {sub_tags}" if sub_tags else ""

                                    if key:
                                        log_lines.append(f"   ✅ '{orig}' -> 对齐到标准概念: '{key}' (置信度: {int(conf * 100)}%{sub_skills_str})")
                                    else:
                                        log_lines.append(f"   ⚪ '{orig}' -> 未对齐任何大纲词 (置信度: {int(conf * 100)}%{sub_skills_str})")
                                else:
                                    log_lines.append(f"   ⚠️ 格式无法识别的项: {item}")

                        log_lines.append("") # 换行间隔

                        # Render terminal styled logs
                        escaped_logs = html.escape("\n".join(log_lines))
                        log_container.markdown(
                            '<div style="border:1px solid #333; border-radius:6px; overflow:hidden;">'
                            '<div style="background:#252526; color:#9cdcfe; font-family:monospace; font-size:0.8rem; '
                            'padding:6px 12px; border-bottom:1px solid #333; display:flex; align-items:center; gap:6px;">'
                            '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#ff5f56;"></span>'
                            '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#ffbd2e;"></span>'
                            '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#27c93f;"></span>'
                            '<span style="margin-left:8px;">🤖 AI 对齐日志</span>'
                            '</div>'
                            '<div style="height:420px; overflow-y:auto; font-family:monospace; font-size:0.85rem; '
                            'background-color:#1e1e1e; color:#d4d4d4; padding:10px;">'
                            f'<pre style="margin:0; white-space:pre-wrap; font-family:inherit; color:inherit; background:none; border:none; padding:0;">{escaped_logs}</pre>'
                            '</div></div>',
                            unsafe_allow_html=True
                        )

                    aligned_total = 0
                    align_error = None
                    try:
                        for ns, terms in by_ns.items():
                            aligned_res = service.ai_batch_align(
                                ns,
                                terms,
                                llm_client,
                                on_chunk_complete=update_progress
                            )
                            aligned_total += len(aligned_res)
                    except Exception as exc:
                        import traceback
                        align_error = f"{exc}\n{traceback.format_exc()}"

                    if align_error:
                        status.update(
                            label="❌ AI 对齐过程中发生异常，请查看日志",
                            state="error",
                            expanded=True
                        )
                        st.error(f"AI 对齐失败：{align_error.splitlines()[0]}")
                        with st.expander("完整错误堆栈", expanded=False):
                            st.code(align_error, language="python")
                    elif aligned_total == 0:
                        status.update(
                            label="ℹ️ 没有需要重新对齐的词（所有待处理项已是最新结果）",
                            state="complete",
                            expanded=False
                        )
                        st.info("当前没有需要 AI 重新对齐的词。已对齐的词请在下方表格中确认。")
                    else:
                        status.update(
                            label=f"🤖 AI 一键对齐完成！已处理并更新 {aligned_total} 个原始词的对齐映射与建议。",
                            state="complete",
                            expanded=True
                        )
                        st.toast(f"成功对齐并更新 {aligned_total} 个原始词！", icon="✅")
            else:
                st.info("没有需要 AI 对齐的待确认或未映射术语。")
        if llm_client is None:
            st.caption("⚠️ AI 功能未配置（请配置系统自检页面中的打标签 API Key）")
    with col_info:
        st.info("💡 **批量操作指引**：点击「AI一键对齐」完成预测后，可在下表微调修改绑定知识点与子技能标签，勾选后点击批量确认即可。")

    focus_terms = st.session_state.get(ALIGNMENT_FOCUS_SESSION_KEY) or []
    is_focus_active = False

    if focus_terms:
        st.info(f"📍 **当前焦点对齐模式**：正在处理从「训练推荐」跳转过来的待确认术语 ({len(focus_terms)} 个)")
        col_focus_1, col_focus_2 = st.columns([0.75, 0.25])
        with col_focus_1:
            st.write(f"待处理术语：`{', '.join(str(item) for item in focus_terms)}`")
        with col_focus_2:
            if st.button("❌ 退出焦点过滤", use_container_width=True):
                st.session_state.pop(ALIGNMENT_FOCUS_SESSION_KEY, None)
                st.rerun()

        # Filter terms by focus terms
        focus_set = {str(item).strip().lower() for item in focus_terms}
        filtered_terms = [
            row
            for row in source_terms
            if str(row["source_value"]).strip().lower() in focus_set
        ]
        is_focus_active = len(filtered_terms) > 0
        if not is_focus_active:
            st.warning("所有焦点术语都已在下方过滤掉（或已被对齐/拒绝），显示全部数据。")
            filtered_terms = source_terms
    else:
        filtered_terms = source_terms

    rows = _alignment_rows(service, filtered_terms)
    _render_summary(rows)

    visible_rows = [row for row in rows if row["状态"] in ["待确认", "未映射"]]
    concept_labels, concept_ids = _concept_options(service)
    if not concept_labels:
        st.warning("⚠️ **标准知识点库为空**")
        st.info("检测到您还没有建立或导入标准知识点大纲，因此下拉选择列表为空，无法进行对齐操作。建议您直接点击下方按钮导入内置标准知识点，或者前往「标准知识点管理」中手动新建。")
        if st.button("✨ 一键导入内置标准知识点（推荐）", type="primary", use_container_width=True):
            created = service.seed_registry_concepts()
            st.success(f"成功导入 {created} 个标准知识点！页面正在重新加载...")
            st.rerun()
        return

    # —— 按置信度批量选中（滑块 + 按钮）——
    AUTO_THRESHOLD_KEY = "align_auto_select_threshold"
    threshold_pct = st.slider(
        "置信度阈值",
        min_value=0,
        max_value=100,
        value=90,
        step=5,
        format="%d%%",
        help="拖动设定阈值，然后点击右侧按钮自动勾选所有「置信度≥阈值 且已绑定知识点」的行",
        key="align_conf_threshold_slider",
    )
    threshold_val = threshold_pct / 100.0
    # 预览：符合自动勾选条件的行数（置信度>0 且有建议知识点 且 ≥阈值）
    auto_eligible = [
        r for r in visible_rows
        if r["置信度"] > 0 and r["置信度"] >= threshold_val and r["建议/已绑定标准知识点"]
    ]
    auto_eligible_n = len(auto_eligible)

    col_auto, col_reset = st.columns([0.7, 0.3])
    with col_auto:
        if st.button(
            f"☑ 自动勾选 ≥{threshold_pct}% 的行（将勾选 {auto_eligible_n} / {len(visible_rows)} 行）",
            use_container_width=True,
            disabled=(auto_eligible_n == 0),
            help="一次性勾选高置信度的行，勾选后可继续手动调整，再点下方批量确认",
        ):
            st.session_state[AUTO_THRESHOLD_KEY] = threshold_val
            st.rerun()
    with col_reset:
        if st.session_state.get(AUTO_THRESHOLD_KEY) is not None:
            if st.button("↩ 重置自动勾选", use_container_width=True):
                st.session_state.pop(AUTO_THRESHOLD_KEY, None)
                st.rerun()

    # 若有未消费的阈值标记，带阈值重建 visible_rows（使"选择"列默认勾选）
    pending_threshold = st.session_state.get(AUTO_THRESHOLD_KEY)
    if pending_threshold is not None:
        visible_rows = _alignment_rows(service, filtered_terms, auto_select_threshold=pending_threshold)
        visible_rows = [r for r in visible_rows if r["状态"] in ["待确认", "未映射"]]

    edited = st.data_editor(
        pd.DataFrame(visible_rows),
        hide_index=True,
        width="stretch",
        disabled=["来源", "原始词", "状态", "置信度", "证据数"],
        column_config={
            "选择": st.column_config.CheckboxColumn("选择", default=False, help="勾选进行批量确认或拒绝"),
            "来源": st.column_config.TextColumn("术语来源", help="说明该术语来自阅卷诊断还是题库自身标签"),
            "原始词": st.column_config.TextColumn("待对齐原始词", help="在试题或阅卷中出现的原始名称"),
            "状态": st.column_config.TextColumn("对齐状态", help="当前与大纲标准知识点的对齐状态"),
            "建议/已绑定标准知识点": st.column_config.SelectboxColumn(
                "建议/已绑定标准知识点",
                options=concept_labels,
                help="下拉选择大纲标准知识点进行绑定",
            ),
            "子技能标签": st.column_config.TextColumn(
                "子技能标签",
                help="描述具体考法/题型/微技能点（使用逗号或顿号分隔），仅从原始词字面推断，可手动编辑",
            ),
            "置信度": st.column_config.ProgressColumn(
                "匹配置信度",
                min_value=0.0,
                max_value=1.0,
                format="%.0f%%",
                help="AI推荐绑定时的契合度。完全一致: 95% | 别名匹配: 90% | 包含匹配: 80% | 模糊推荐: 70%",
            ),
            "证据数": st.column_config.NumberColumn(
                "频次/证据数",
                format="%d",
                help="在错题或题库中出现的次数，频次越高表示该词越重要，需优先对齐",
            ),
        },
        key="knowledge_alignment_pending_editor",
    )

    selected = [row for _, row in edited.iterrows() if bool(row.get("选择"))]
    confirm_col, reject_col, refresh_col = st.columns(3)
    with confirm_col:
        if st.button("批量确认", type="primary", width="stretch", disabled=not selected):
            mappings: list[tuple[str, str, int, list[str]]] = []
            for row in selected:
                target_label = str(row.get("建议/已绑定标准知识点") or "")
                concept_id = concept_ids.get(target_label)

                source_disp = str(row["来源"])
                db_source = next(
                    (k for k, v in NAMESPACE_MAP.items() if v == source_disp),
                    source_disp
                )

                sub_skills_text = str(row.get("子技能标签") or "")
                tags = [t.strip() for t in sub_skills_text.replace("，", ",").replace("、", ",").split(",") if t.strip()]

                if concept_id is not None:
                    mappings.append((db_source, str(row["原始词"]), concept_id, tags))
            if not mappings:
                st.error("请为选中的来源词选择标准知识点。")
            else:
                service.confirm_many(mappings, reviewed_by="teacher")
                st.session_state.pop("align_auto_select_threshold", None)
                st.success(f"已确认 {len(mappings)} 条映射。")
                st.rerun()
    with reject_col:
        if st.button("拒绝选中", width="stretch", disabled=not selected):
            for row in selected:
                source_disp = str(row["来源"])
                db_source = next(
                    (k for k, v in NAMESPACE_MAP.items() if v == source_disp),
                    source_disp
                )
                service.reject_mapping(
                    db_source,
                    str(row["原始词"]),
                    reviewed_by="teacher",
                )
            st.session_state.pop("align_auto_select_threshold", None)
            st.success(f"已拒绝 {len(selected)} 条映射。")
            st.rerun()
    with refresh_col:
        if st.button("刷新对齐状态", width="stretch"):
            st.rerun()


def _render_concept_editor(service: ConceptAlignmentService) -> None:
    st.subheader("标准知识点管理")
    concepts = service.list_concepts(status=None)
    
    # Calculate mappings count
    try:
        mappings = service.list_mappings(status=AlignmentStatus.CONFIRMED)
        mapping_counts = Counter(m.concept_id for m in mappings if m.concept_id is not None)
    except Exception:
        mapping_counts = {}
        
    st.dataframe(
        [
            {
                "ID": item.id,
                "稳定编码": item.canonical_key,
                "标准名称": item.name,
                "别名/同义词": "、".join(item.aliases),
                "已绑定原始词数": mapping_counts.get(item.id, 0),
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
    
    st.markdown("""
    💡 **知识点关系的方向说明**：
    * **前置关系 (prerequisite)**：表示「来源知识点」依赖于「目标知识点」（如：*二次函数 $\rightarrow$ 一元二次方程*）。学生在来源点薄弱时，系统会在**“前置巩固”**阶段优先推荐目标点的题目。
    * **关联关系 (related)**：表示两知识点水平相关，用于在**“迁移验证”**阶段推荐较难或综合的交叉考题。
    * **包含关系 (parent)**：表示概念的上下级从属关系（如：*一元二次方程根的判别式 $\rightarrow$ 一元二次方程*）。
    """)

    concepts = service.list_concepts()
    concept_labels = {f"{item.name} | #{item.id}": item.id for item in concepts}
    name_by_id = {item.id: item.name for item in concepts}
    relations = service.list_relations()
    
    # Friendly type map
    rel_type_map = {
        "prerequisite": "🧱 前置基础 (prerequisite)",
        "related": "🚀 关联迁移 (related)",
        "parent": "📂 从属父级 (parent)",
    }
    
    st.dataframe(
        [
            {
                "来源知识点": name_by_id.get(int(row["source_concept_id"]), row["source_concept_id"]),
                "关系类型": rel_type_map.get(row["relation_type"], row["relation_type"]),
                "目标（依赖）知识点": name_by_id.get(int(row["target_concept_id"]), row["target_concept_id"]),
                "关联权重": row["weight"],
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

# Inject premium shared CSS
try:
    from pages_shared.shared_styles import inject_shared_css
    inject_shared_css(st)
except Exception:
    pass

st.title("知识点对齐中心")

with st.expander("💡 知识点对齐中心是如何工作的？（新手教师快速入门）", expanded=True):
    st.markdown("""
    #### 🎯 为什么需要“知识点对齐”？
    1. **诊断出的词不标准**：系统在批改阅卷时识别到的学生薄弱点（如“求根公式”、“一元二次函数解析式”）是比较零散的**“原始词”**。
    2. **选题要求的词标准**：题库中的题目都是挂载在统一的**“大纲标准知识点”**下。
    3. **对齐能够架起桥梁**：通过把“原始词”与“标准知识点”绑定，算法才能知道：“该学生在‘求根公式’上错得多，应该去挑选挂载了‘二次函数的图像与性质’的题来训练他”。
    
    #### 🛠️ 对齐工作台使用指南：
    * **待确认 (Suggested)**：AI 根据语义算法自动预测的推荐绑定。如果合理，请**勾选行**并点击下方的 **【批量确认】**。
    * **未映射 (Unmapped)**：未找到高度契合的标准词。您可以点击下拉框，**手动选择**最契合的知识点，然后勾选并确认。
    * **已拒绝 (Rejected)**：被您拒绝的映射。已拒绝的映射不会参与选题算分，可借此过滤掉不合理的错题诊断。
    
    > ⚠️ **重要提示**：只有映射状态为 **「已确认」** 且绑定了标准知识点的原始词，才会被当做有效的薄弱证据，进而能够参与 **「训练推荐」** 选题！
    """)

st.caption("集中连接批改薄弱点、题库标签与稳定标准知识点，为后续训练推荐提供可信入口。")

path_manager = get_path_manager()
alignment_service = ConceptAlignmentService(path_manager.qb_db_path)
alignment_service.initialize_database()
real_source_terms, load_warnings = _load_real_source_terms()
for warning in load_warnings:
    st.warning(warning)

alignment_tab, concept_tab = st.tabs(
    ["对齐工作台", "知识点管理"]
)
with alignment_tab:
    _render_alignment_workbench(alignment_service, real_source_terms)
with concept_tab:
    _render_concept_editor(alignment_service)
    st.markdown("---")
    with st.expander("🔗 知识点关系管理", expanded=False):
        _render_relation_editor(alignment_service)
