from __future__ import annotations

import html
import json
from typing import Any

import pandas as pd
import streamlit as st

from integration.skill_conflict_inbox_service import (
    ConflictInboxSummary,
    ConflictSourceGroup,
    SkillConflictInboxService,
)
from path_manager import get_path_manager
from question_bank.services.skill_catalog_service import SkillCatalogService


st.set_page_config(page_title="技能目录与待处理问题", page_icon="🧭", layout="wide")


def _service() -> SkillCatalogService:
    return SkillCatalogService(get_path_manager().qb_db_path)


def _inbox_service() -> SkillConflictInboxService:
    paths = get_path_manager()
    return SkillConflictInboxService(paths.qb_db_path, paths.db_path)


def _apply_page_style() -> None:
    st.markdown(
        """
        <style>
        .skill-lead {
            max-width: 920px; color: #4b5870; font-size: 1.02rem;
            line-height: 1.75; margin: -.25rem 0 1.35rem;
        }
        .coverage-lane {
            border: 1px solid #d8e1ee; border-left: 5px solid #315f94;
            background: linear-gradient(90deg, #f5f8fc, #ffffff);
            border-radius: 10px; padding: .85rem 1rem; margin: .5rem 0 1rem;
            color: #24344d;
        }
        .source-context {
            border-left: 3px solid #91a7c2; padding: .55rem .8rem;
            color: #3f4f66; background: #f8fafc; margin: .35rem 0 .8rem;
        }
        div[data-testid="stMetric"] { background: #f8fafc; border: 1px solid #e2e8f0; padding: .6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _skill_label(skill: dict[str, Any]) -> str:
    return f"{skill['name']} · {skill['topic_name']}"


def _render_catalog(service: SkillCatalogService) -> None:
    st.subheader("技能目录")
    st.caption("这里收录系统用于诊断与选题的具体训练技能。老师平时不需要维护；管理员只处理少量本校特殊叫法。")
    topics = service.list_topics()
    topic_names = ["全部主题", *[str(item["name"]) for item in topics]]
    filter_col, search_col = st.columns([1, 2])
    selected_topic = filter_col.selectbox("所属主题", topic_names, key="skill_topic_filter")
    search = search_col.text_input("搜索技能", placeholder="例如：角平分线、一次函数、尺规作图")
    topic_by_name = {str(item["name"]): int(item["id"]) for item in topics}
    skills = service.list_skills(
        topic_id=topic_by_name.get(selected_topic) if selected_topic != "全部主题" else None
    )
    query = search.strip().casefold()
    if query:
        skills = [
            item
            for item in skills
            if query in str(item["name"]).casefold()
            or any(query in str(alias).casefold() for alias in item.get("aliases", []))
        ]
    rows = [
        {
            "具体训练技能": item["name"],
            "所属主题": item["topic_name"],
            "适用年级": _grade_range(item),
            "来源": "学校补充" if item["origin"] == "local" else "系统内置",
        }
        for item in skills
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"当前显示 {len(rows)} 个具体技能。")

    local_skills = [item for item in service.list_skills() if item["origin"] == "local"]
    with st.expander("整理本校重复技能", expanded=False):
        st.write("仅当两个名称确实表示同一种训练能力时才合并。历史题目会继续指向保留的技能。")
        if not local_skills:
            st.info("目前没有本校补充技能。")
            return
        all_skills = service.list_skills()
        source = st.selectbox(
            "要合并的本校技能",
            local_skills,
            format_func=_skill_label,
            key="merge_source",
        )
        targets = [item for item in all_skills if int(item["id"]) != int(source["id"])]
        target = st.selectbox("合并后保留", targets, format_func=_skill_label, key="merge_target")
        st.markdown(
            f"**合并依据预览**：`{source['name']}` 将统一为 `{target['name']}`；"
            "原题目、评分规则和历史诊断不会删除。"
        )
        confirmed = st.checkbox("我已核对两者含义相同", key="merge_confirm")
        if st.button("确认合并", disabled=not confirmed, type="primary"):
            service.merge_skill(int(source["id"]), int(target["id"]), actor="本机管理员")
            st.success("已合并，并保留历史引用。")
            st.rerun()


def _render_conflicts(
    service: SkillCatalogService,
    summary: ConflictInboxSummary,
) -> None:
    st.subheader(f"必须处理 · {len(summary.blocking)} 道题")
    st.caption("这里只统计整道题还没有可用训练技能的来源。")
    if summary.blocking:
        _render_conflict_groups(service, summary.blocking, key_prefix="blocking")
    else:
        st.success("当前没有会阻断知识图谱或训练推荐的问题。")

    with st.expander(f"可选检查 · {len(summary.advisory)} 道题", expanded=False):
        st.caption("这些题已经有可用技能；附加词条不会阻断知识图谱或训练推荐。")
        _render_conflict_groups(service, summary.advisory, key_prefix="advisory")

    with st.expander(f"历史记录 · {len(summary.historical)} 道题", expanded=False):
        st.caption("来源已删除或无法识别，仅供追溯，不再提供修改操作。")
        _render_conflict_groups(
            service,
            summary.historical,
            key_prefix="historical",
            read_only=True,
        )


def _render_conflict_groups(
    service: SkillCatalogService,
    groups: tuple[ConflictSourceGroup, ...],
    *,
    key_prefix: str,
    read_only: bool = False,
) -> None:
    if not groups:
        st.info("这一组目前为空。")
        return
    topics = service.list_topics() if not read_only else []
    all_skills = service.list_skills() if not read_only else []
    for group in groups:
        with st.container(border=True):
            st.markdown(
                f"#### {html.escape(group.source_label)}\n\n"
                f"<div class='source-context'>{html.escape(group.context)}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"该来源包含 {len(group.conflicts)} 个待核对词条。")
            for conflict in group.conflicts:
                st.markdown(f"**词条：{conflict['raw_label']}**")
                st.caption(f"系统没有自动决定：{conflict['reason']}")
                if not read_only:
                    _render_conflict_action(
                        service,
                        conflict,
                        topics=topics,
                        all_skills=all_skills,
                        key_prefix=key_prefix,
                    )
                with st.expander("技术详情", expanded=False):
                    st.json(
                        {
                            "问题编号": conflict["id"],
                            "来源引用": conflict["source_ref"],
                            "候选技能编号": [
                                item["id"] for item in conflict.get("candidates", [])
                            ],
                            "识别依据": conflict.get("evidence", {}),
                        }
                    )


def _render_conflict_action(
    service: SkillCatalogService,
    conflict: dict[str, Any],
    *,
    topics: list[dict[str, Any]],
    all_skills: list[dict[str, Any]],
    key_prefix: str,
) -> None:
    conflict_id = int(conflict["id"])
    action = st.radio(
        "怎么处理",
        ("选择已有技能", "新建本校技能", "暂不处理"),
        horizontal=True,
        key=f"{key_prefix}_action_{conflict_id}",
    )
    if action == "选择已有技能":
        preferred_ids = {int(item["id"]) for item in conflict.get("candidates", [])}
        choices = sorted(
            all_skills,
            key=lambda item: (
                0 if int(item["id"]) in preferred_ids else 1,
                item["topic_name"],
                item["name"],
            ),
        )
        selected = st.selectbox(
            "对应到",
            choices,
            format_func=_skill_label,
            key=f"{key_prefix}_skill_{conflict_id}",
        )
        if st.button("保存这个选择", type="primary", key=f"{key_prefix}_resolve_{conflict_id}"):
            service.resolve_conflict(conflict_id, int(selected["id"]), actor="本机管理员")
            st.success("已保存，相关题目现在可以按这个技能参与训练推荐。")
            st.rerun()
    elif action == "新建本校技能":
        name = st.text_input(
            "本校技能名称",
            value=str(conflict["raw_label"]),
            key=f"{key_prefix}_local_name_{conflict_id}",
        )
        topic = st.selectbox(
            "放入主题",
            topics,
            format_func=lambda item: item["name"],
            key=f"{key_prefix}_local_topic_{conflict_id}",
        )
        if st.button("新建并使用", type="primary", key=f"{key_prefix}_create_{conflict_id}"):
            service.create_local_from_conflict(
                conflict_id,
                name,
                int(topic["id"]),
                actor="本机管理员",
            )
            st.success("已建立本校技能并完成关联。")
            st.rerun()
    elif st.button("暂不处理", key=f"{key_prefix}_ignore_{conflict_id}"):
        service.ignore_conflict(conflict_id, actor="本机管理员")
        st.info("已移出待处理列表；不会影响阅卷。")
        st.rerun()


def _render_coverage(
    service: SkillCatalogService,
    summary: ConflictInboxSummary,
) -> None:
    st.subheader("覆盖情况")
    coverage = summary.coverage
    st.markdown(
        "<div class='coverage-lane'><b>评分规则与题库现在共用同一套具体技能目录。</b> "
        "已识别内容可直接参与诊断和选题；有歧义的内容留在待处理问题中，不会被系统猜测。</div>",
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    _coverage_card(left, "已有试卷评分规则", coverage["assessment"])
    _coverage_card(right, "题库题目", coverage["question_bank"])
    for warning in summary.warnings:
        st.warning(warning)

    st.markdown("#### 相近技能预览")
    st.caption("相近关系只用于题量不足时的明确补入，不会被当作精确匹配。发现明显错误时可停用。")
    neighbors = service.list_neighbors()
    if not neighbors:
        st.info("当前没有启用的相近技能关系。")
    for item in neighbors:
        col_text, col_action = st.columns([5, 1])
        col_text.write(f"{item['source_skill_name']} → {item['target_skill_name']}（{_neighbor_kind(item['kind'])}）")
        if col_action.button("停用", key=f"disable_neighbor_{item['id']}"):
            service.set_neighbor_enabled(int(item["id"]), False, actor="本机管理员")
            st.rerun()


def _render_migrations(service: SkillCatalogService) -> None:
    st.subheader("迁移记录")
    st.caption("这里只记录旧数据是否已安全转换。迁移不会自动切换推荐模式。")
    rows = service.list_migration_runs()
    if not rows:
        st.info("还没有执行过统一技能迁移。")
        return
    status_names = {
        "succeeded": "已完成",
        "failed": "失败",
        "rolled_back": "已回滚",
        "running": "进行中",
    }
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "批次": item["batch_id"],
                    "结果": status_names.get(item["status"], "未知"),
                    "开始时间": item["started_at"],
                    "完成时间": item["finished_at"],
                }
                for item in rows
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    with st.expander("技术详情", expanded=False):
        st.code(json.dumps(rows, ensure_ascii=False, indent=2, default=str), language="json")


def _coverage_card(container, title: str, values: dict[str, int]) -> None:
    container.markdown(f"#### {title}")
    first, second, third, fourth = container.columns(4)
    first.metric("总数", values["total"])
    second.metric("已覆盖", values["resolved"])
    third.metric("必须处理", values["blocking"])
    fourth.metric("可选检查", values["advisory"])


def _neighbor_kind(kind: str) -> str:
    return {"same_topic": "同类技能", "prerequisite": "前置技能", "advanced": "进阶技能", "co_assessed": "常一起考查"}.get(kind, "相关技能")


def _grade_range(skill: dict[str, Any]) -> str:
    lower, upper = skill.get("grade_min"), skill.get("grade_max")
    if lower is None and upper is None:
        return "不限"
    if lower == upper:
        return f"{lower} 年级"
    return f"{lower or '—'}–{upper or '—'} 年级"


_apply_page_style()
st.title("技能目录与待处理问题")
st.markdown(
    "<div class='skill-lead'>系统先把试卷评分规则、学生错题和题库题目统一到“具体训练技能”。"
    "大多数内容自动完成；这里只保留少量确实需要人判断的问题。</div>",
    unsafe_allow_html=True,
)

catalog_service = _service()
inbox_summary = _inbox_service().summary()
catalog_tab, inbox_tab, coverage_tab, migration_tab = st.tabs(
    ["技能目录", "待处理问题", "覆盖情况", "迁移记录"]
)
with catalog_tab:
    _render_catalog(catalog_service)
with inbox_tab:
    _render_conflicts(catalog_service, inbox_summary)
with coverage_tab:
    _render_coverage(catalog_service, inbox_summary)
with migration_tab:
    _render_migrations(catalog_service)
