from __future__ import annotations

import json
from typing import Any, Sequence


def build_batch_generation_prompt(
    question_ids: Sequence[str],
    contexts: Sequence[dict[str, Any]],
    image_map: Sequence[str],
    fallback: str,
) -> str:
    """Build the active small-batch prompt without reading ambient state."""
    return (
        "你正在为一个小批次的中学数学题生成可执行评分标准。仅返回一个完整、严格的 JSON 对象。\n"
        f"BATCH_QUESTION_IDS_JSON={json.dumps(list(question_ids), ensure_ascii=False)}\n"
        "必须完整且仅返回上述题号，并在 rubric.questions 与 answer_key.questions 中按同一顺序各出现一次；"
        "不得遗漏、重复或增加题号。所有 max_score、part_score、step_score 暂设为1，最终总分由本地程序分配。\n"
        "每题必须保留教师确认题型，输出具体 knowledge_id、knowledge_name、parts、steps；"
        "每个 part 必须有 response_mode，每个 step 必须有可核验的 core_goal 和 required_elements。"
        "选择/普通填空只核对最终答案；过程题保留必要过程；作图题输出 visual_requirements。\n"
        "只允许字段 rubric、answer_key、meta 及其既有评分结构；不要 Markdown、解释或续写建议。\n"
        f"图片顺序：{'; '.join(image_map) if image_map else '无'}\n"
        f"本批次结构化内容：{json.dumps(list(contexts), ensure_ascii=False)}\n"
        f"必要时参考的有限原文：{fallback}"
    )


def build_score_allocation_prompt(
    structure_summary: list[dict[str, Any]],
    doc_text: str,
    *,
    include_document_text: bool,
) -> str:
    """Build the active whole-paper score prompt without side effects."""
    source_context = (
        f"\n原始文档文本（仅用于识别原卷分值提示）：\n{str(doc_text or '')[:6000]}\n"
        if include_document_text and str(doc_text or "").strip()
        else "\n本次为图片语义来源，不提供也不得推测 PDF 抽取文字或原卷分值。\n"
    )
    return (
        "请仅为下列已确认题目结构分配分值，总分必须精确等于100。\n"
        "不同题型之间不限制分值高低；相同类型客观题必须同分；所有分值均为正整数；单题不超过18分。\n"
        "保持所有 question_id、part_id、step_id 和小问结构不变。"
        "分值可以不采用原卷分值，但不得改变小问作答要求或 response_mode。\n"
        f"{source_context}"
        "仅返回 JSON：{\"question_scores\":[{\"question_id\":\"Q1\",\"max_score\":1,"
        "\"parts\":[{\"part_id\":\"Q1\",\"part_score\":1,\"steps\":[{\"step_id\":\"S1\",\"step_score\":1}]}]}]}\n"
        f"待分值结构：\n{json.dumps(structure_summary, ensure_ascii=False)}"
    )


def build_manual_structure_refinement_prompt(
    payload: dict[str, Any],
) -> str:
    """Build the teacher-structure refinement prompt without side effects."""
    return (
        "您正在对教师编辑过的评分标准进行精修/对齐。请仅返回严格的 JSON 数据。\n"
        "硬性要求：\n"
        "1) 必须保留每一个已有的 rubric.questions[].question_id。\n"
        "2) 必须保留每一个已有的 parts[].part_id；绝对不能合并、删除或修改教师创建 the parts 部分。\n"
        "3) answer_key.questions[].parts 必须通过 part_id 与 rubric 中的 parts 保持一致对齐。\n"
        "4) 补全缺失的答案、accepted_forms、解析、步骤分、证明扣分项和证据链规则。\n"
        "5) 保持整张试卷总分 total_score 和各题 max_score 的总和精确等于 100。\n"
        "6) 对于证明题或计算解答题，按证据步骤步骤分进行细化，并保守地给与仅有答案无过程的得分限制。\n\n"
        f"当前评分标准 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
    )
