from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from path_manager import get_path_manager
from question_bank.services.skill_catalog_service import SkillCatalogService


st.set_page_config(page_title="技能目录与待处理问题", page_icon="🧭", layout="wide")


def _service() -> SkillCatalogService:
    return SkillCatalogService(get_path_manager().qb_db_path)


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


def _render_conflicts(service: SkillCatalogService) -> None:
    conflicts = service.list_open_conflicts(limit=100)
    st.subheader(f"待处理问题 · {len(conflicts)}")
    st.caption("只有系统无法唯一判断的叫法才会来到这里。阅卷不受影响；处理后可让后续选题更准确。")
    if not conflicts:
        st.success("当前没有需要人工判断的问题。")
        return
    topics = service.list_topics()
    all_skills = service.list_skills()
    for conflict in conflicts:
        with st.container(border=True):
            st.markdown(f"#### {conflict['raw_label']}")
            st.markdown(
                f"<div class='source-context'>来源：{_source_name(conflict['source_type'])}<br>"
                f"系统没有自动决定：{conflict['reason']}</div>",
                unsafe_allow_html=True,
            )
            action = st.radio(
                "怎么处理",
                ("选择已有技能", "新建本校技能", "暂不处理"),
                horizontal=True,
                key=f"conflict_action_{conflict['id']}",
            )
            if action == "选择已有技能":
                preferred_ids = {int(item["id"]) for item in conflict.get("candidates", [])}
                choices = sorted(
                    all_skills,
                    key=lambda item: (0 if int(item["id"]) in preferred_ids else 1, item["topic_name"], item["name"]),
                )
                selected = st.selectbox(
                    "对应到",
                    choices,
                    format_func=_skill_label,
                    key=f"conflict_skill_{conflict['id']}",
                )
                if st.button("保存这个选择", type="primary", key=f"resolve_{conflict['id']}"):
                    service.resolve_conflict(int(conflict["id"]), int(selected["id"]), actor="本机管理员")
                    st.success("已保存，相关题目现在可以按这个技能参与训练推荐。")
                    st.rerun()
            elif action == "新建本校技能":
                name = st.text_input("本校技能名称", value=str(conflict["raw_label"]), key=f"local_name_{conflict['id']}")
                topic = st.selectbox("放入主题", topics, format_func=lambda item: item["name"], key=f"local_topic_{conflict['id']}")
                if st.button("新建并使用", type="primary", key=f"create_{conflict['id']}"):
                    service.create_local_from_conflict(
                        int(conflict["id"]), name, int(topic["id"]), actor="本机管理员"
                    )
                    st.success("已建立本校技能并完成关联。")
                    st.rerun()
            else:
                if st.button("暂不处理", key=f"ignore_{conflict['id']}"):
                    service.ignore_conflict(int(conflict["id"]), actor="本机管理员")
                    st.info("已移出待处理列表；不会影响阅卷。")
                    st.rerun()
            with st.expander("技术详情", expanded=False):
                st.json(
                    {
                        "问题编号": conflict["id"],
                        "来源引用": conflict["source_ref"],
                        "候选技能编号": [item["id"] for item in conflict.get("candidates", [])],
                        "识别依据": conflict.get("evidence", {}),
                    }
                )


def _render_coverage(service: SkillCatalogService) -> None:
    st.subheader("覆盖情况")
    coverage = service.coverage_summary()
    st.markdown(
        "<div class='coverage-lane'><b>评分规则与题库现在共用同一套具体技能目录。</b> "
        "已识别内容可直接参与诊断和选题；有歧义的内容留在待处理问题中，不会被系统猜测。</div>",
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    _coverage_card(left, "已有试卷评分规则", coverage["assessment"])
    _coverage_card(right, "题库题目", coverage["question_bank"])

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
    first, second, third = container.columns(3)
    first.metric("总数", values["total"])
    second.metric("已识别", values["resolved"])
    third.metric("待处理", values["conflicts"])


def _source_name(source_type: str) -> str:
    return {"question_bank_item": "题库题目", "assessment_item": "评分规则", "legacy_term": "旧知识点叫法"}.get(source_type, "历史数据")


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
catalog_tab, inbox_tab, coverage_tab, migration_tab = st.tabs(
    ["技能目录", "待处理问题", "覆盖情况", "迁移记录"]
)
with catalog_tab:
    _render_catalog(catalog_service)
with inbox_tab:
    _render_conflicts(catalog_service)
with coverage_tab:
    _render_coverage(catalog_service)
with migration_tab:
    _render_migrations(catalog_service)
