"""改编练习任务卡目录：稳定的卡片 id、中文名称与一句话说明。

目录是静态契约；``id`` 一旦发布不再改名，前端与已保存作品按 id 引用。
"""

from __future__ import annotations

from typing import Any

TASK_CARDS: tuple[dict[str, Any], ...] = (
    {
        "id": "change_data",
        "label": "换数据",
        "method": "change_data",
        "description": "保持结构与考点不变，替换题目中的数值或条件数据。",
    },
    {
        "id": "change_context",
        "label": "换情境",
        "method": "change_context",
        "description": "保持数量关系不变，把题目放进新的生活或学科情境。",
    },
    {
        "id": "swap_condition_conclusion",
        "label": "条件与结论互换",
        "method": "swap_condition_conclusion",
        "description": "把原题的条件与结论对调，考查逆向理解。",
    },
    {
        "id": "deepen_add_part",
        "label": "加一问或去铺垫",
        "method": "deepen_add_part",
        "description": "增加递进小问，或撤掉中间铺垫让思维跨度变大。",
    },
    {
        "id": "explore_new_conclusion",
        "label": "探索新结论",
        "method": "explore_new_conclusion",
        "description": "沿用原题条件，让学生探究并证明新的结论。",
    },
    {
        "id": "change_type",
        "label": "换题型",
        "method": "change_type",
        "description": "把同一考查内容改写成选择、填空或解答等不同题型。",
    },
    {
        "id": "what_if_not",
        "label": "如果不是这样呢",
        "method": "what_if_not",
        "description": "改动一个关键前提，追问结论是否仍然成立。",
    },
    {
        "id": "solo_ladder",
        "label": "设计SOLO四层递进小问",
        "method": "solo_ladder",
        "description": "按单点、多点、关联、拓展抽象四级设计递进小问。",
    },
    {
        "id": "to_new_definition",
        "label": "改造成新定义题",
        "method": "to_new_definition",
        "description": "引入一个自定义概念或规则，让学生在题内学习并运用。",
    },
    {
        "id": "sz_q19_five_section",
        "label": "按深圳第19题五段式改编",
        "method": "sz_q19_five_section",
        "description": "按问题背景、研究条件、模型构建、模型应用、总结反思五段组织。",
    },
    {
        "id": "sz_q20_new_definition",
        "label": "熟悉图形加一个约束改造成新定义",
        "method": "sz_q20_new_definition",
        "description": "在熟悉图形上加一条约束形成新定义，考查迁移运用。",
    },
)

_TASK_CARD_IDS = frozenset(card["id"] for card in TASK_CARDS)


def task_card_catalog() -> list[dict[str, Any]]:
    return [dict(card) for card in TASK_CARDS]


def is_task_card_id(value: object) -> bool:
    return isinstance(value, str) and value in _TASK_CARD_IDS


__all__ = ["TASK_CARDS", "is_task_card_id", "task_card_catalog"]
