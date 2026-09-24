from __future__ import annotations

import json
from typing import Any, Sequence

from .contract import (
    GENERATED_ID_CONTRACT_PROMPT,
    TEACHER_TYPE_CONTRACT_PROMPT,
)


def build_batch_generation_prompt(
    question_ids: Sequence[str],
    contexts: Sequence[dict[str, Any]],
    image_map: Sequence[str],
    fallback: str,
    *,
    validation_feedback: Sequence[str] = (),
) -> str:
    """Build the active small-batch prompt without reading ambient state."""
    feedback = [
        str(item).strip()
        for item in validation_feedback
        if str(item).strip()
    ]
    repair_context = (
        "\n\n上一次结果未通过本地校验。请只修正下列明确问题，"
        "保留题号、正确答案含义和已经符合要求的评分内容；"
        "修正后仍须返回完整题目结构：\n- "
        + "\n- ".join(feedback[:12])
        if feedback
        else ""
    )
    return (
        "你正在为一个小批次的中学数学题生成可执行评分标准。仅返回一个完整、严格的 JSON 对象。\n"
        f"BATCH_QUESTION_IDS_JSON={json.dumps(list(question_ids), ensure_ascii=False)}\n"
        "必须完整且仅返回上述题号，并在 rubric.questions 与 answer_key.questions 中按同一顺序各出现一次；"
        "不得遗漏、重复或增加题号。所有 max_score、part_score、step_score 暂设为1，最终总分由本地程序分配。\n"
        f"{GENERATED_ID_CONTRACT_PROMPT}\n"
        f"{TEACHER_TYPE_CONTRACT_PROMPT}\n"
        "每道题都必须返回 question_type，且只允许 choice、fill_blank、calculation、proof、comprehensive。"
        "每个 part 的 response_mode 只允许 exact_objective、short_answer_points、"
        "process_required、visual_construction。"
        "选择题统一返回 question_type=choice、response_mode=exact_objective；"
        "single_choice、multiple_choice 不是 response_mode 允许值，不得写入 response_mode。\n"
        "question_rich_text、answer_rich_text、analysis_rich_text "
        "保留了表格行列、上下标、下划线和图片占位，遇到与普通文本差异时以这些结构化可读文本为准。"
        "输出完整的 parts、steps；知识点与题目标签不属于评分依据，不要输出任何 knowledge 字段。"
        "每个 part 必须有 response_mode，每个 step 必须有可核验的 core_goal 和 required_elements。"
        "统一答案与规则字段契约：选择、判断和普通填空必须把非空文本答案同时写入 "
        "answer_key.questions[].canonical_answer 与 answer_key.questions[].parts[].answer；"
        "解答题每个小问的完整参考答案写入 answer_key.questions[].parts[].answer，"
        "逐步参考过程写入 answer_key.questions[].parts[].step_milestones。"
        "证明义务只能写入 rubric.questions[].parts[].proof_obligations（字符串数组）；"
        "小问通用扣分依据写入 rubric.questions[].parts[].deduction_policy（字符串数组）；"
        "仅属于某一步的扣分依据写入 "
        "rubric.questions[].parts[].steps[].deduction_rules（字符串数组）。"
        "不要用 correct_value、answer_value、final_answer 或 step_content 代替上述字段。\n"
        "选择/普通填空只核对最终答案；过程题保留必要过程；作图题输出 visual_requirements。\n"
        "输入中的 response_form_fact=single_blank 表示题面只有一个明确填空位置且没有分小问；"
        "此时必须返回 question_type=fill_blank、单一 part 和 response_mode=exact_objective，"
        "不得因参考解析较长而增加小问或改成过程题。\n"
        "证明题、计算题和综合题的过程部分必须拆成可独立评分的逻辑步骤，不能把整段解答压成一个笼统步骤。"
        "定理或判定的适用前提、必要条件（直角/垂直/平行、全等或相似的对应关系、取值范围、分母不为零等）"
        "必须单列为独立步骤（判定点），不得与“列式”“代入”合并为一步；该步 core_goal 写明要求成立的"
        "数学含义，required_elements 写学生可写出的任一书面形式并注明“任一即可”"
        "（例：∠OEB=90°、OE⊥EB、△OEB 为直角三角形、图中直角标记并在推理中引用）。"
        "“任一即可”只用于同一个数学结果的不同写法（如 ∠OEB=90° 与 OE⊥EB）；"
        "不同的变形、运算或中间结果不得在同一步的 required_elements 中用“任一即可”"
        "并列为备选，确有独立教学价值的应各自单列为步骤，否则只保留该步必须成立的那一个结果。"
        "同一步的 counterexamples 与 deduction_rules 不得与其给分条件相矛盾："
        "列为反例的错误，不能仅凭写出其他备选形式而判为达成。"
        "每个步骤是整点有无的判定单位，粒度以“教师会否为此单独扣分”为准："
        "有独立教学价值的中间结论、条件、结论各占一步。"
        "证明题必须输出具体 proof_obligations（证明义务）；步骤应明确区分题设或目标、使用的定理或判定条件、"
        "由条件得到的推导以及最终结论，并让每个 required_elements 对应教师能够核验的书面证据。"
        "计算题应按关键公式或关系、代入或变形、计算结果与必要单位组织步骤。"
        "process_required 小问必须按可独立核验的逻辑步骤拆分，至少返回两个 core_goal 与 "
        "required_elements 均非空且互不重复的 step，不得把多个过程压成一个笼统步骤。"
        "若参考答案确实只能确认一个原子结果，应选择匹配的非过程 response_mode，不得为了满足数量编造步骤。"
        "过程题必须给出针对本题的 deduction_policy，说明缺少前提、定理条件不成立、关键推导跳步、"
        "结论与过程断裂等情况对应的扣分证据；允许替代方法时设置 allow_alternative_methods=true，"
        "每个 step 尽量给出 deduction_rules；缺少该字段时不能作为本地阻断理由。"
        "只核验等价数学义务，不要求学生逐句复现参考答案。"
        "core_goal 与 required_elements 应描述必须成立的数学条件、关系和结论；"
        "参考答案中的重复代入、逐项平方或简单算术展开不自动成为必写过程。"
        "在 equivalent_rules 中说明可接受的符号关系、数值关系、等价变形和合并书写；"
        "allow_alternative_methods=false 不禁止同一方法的等价表达。"
        "已给出对应数值和成立关系并完成结论时，允许省略可核实的简单算术展开；"
        "只抄条件、只写结论、关系不成立或循环论证不能视为完成证明。"
        "只有题干或教师明确要求特定计算过程或方法时才限定书写形式，"
        "扣分规则必须指出缺失的数学依据，不得仅因未照写参考计算链而扣分。\n"
        "只允许字段 rubric、answer_key、meta 及其既有评分结构；不要 Markdown、解释或续写建议。\n"
        f"图片顺序：{'; '.join(image_map) if image_map else '无'}\n"
        f"本批次结构化内容：{json.dumps(list(contexts), ensure_ascii=False)}\n"
        f"必要时参考的有限原文：{fallback}"
        + repair_context
    )


def build_score_allocation_prompt(
    structure_summary: list[dict[str, Any]],
    doc_text: str,
    *,
    include_document_text: bool,
    validation_feedback: Sequence[str] = (),
) -> str:
    """Build the active whole-paper score prompt without side effects."""
    source_context = (
        f"\n原始文档文本（仅用于识别原卷分值提示）：\n{str(doc_text or '')[:6000]}\n"
        if include_document_text and str(doc_text or "").strip()
        else "\n本次为图片语义来源，不提供也不得推测 PDF 抽取文字或原卷分值。\n"
    )
    feedback = [
        str(item).strip()
        for item in validation_feedback
        if str(item).strip()
    ]
    repair_context = (
        "\n上一次统一配分未通过本地校验。请保持评分标准文字和结构不变，"
        "只修正以下分值问题：\n- "
        + "\n- ".join(feedback[:12])
        + "\n"
        if feedback
        else ""
    )
    return (
        "请仅为下列已确认题目结构分配分值，总分必须精确等于100。\n"
        "优先继承原卷可识别的分值比例；没有可靠原卷分值时，再按题型、有效评分步骤数量、难度和工作量分配。"
        "相同类型客观题必须同分；选择题单题分值不得高于填空题，且不得低于填空题的一半；"
        "选择、填空、判断等简单客观题单题分值不得高于任一道过程题。"
        "解答题之间允许不同分，但有效评分步骤更多的题不得反而更低。"
        "同一个小问内部，最高评分步骤与最低评分步骤的分值差不得超过2分；"
        "这条规则不限制不同小问或不同大题的总分差。"
        "所有分值均为正整数；单题不超过18分。\n"
        f"{GENERATED_ID_CONTRACT_PROMPT}"
        "保持所有给定的 question_id、part_id、step_id 和小问结构不变。"
        "只有原卷分值缺失或与总分100、整数及上述教学约束冲突时，才可调整原卷比例；"
        "不得改变小问作答要求或 response_mode。\n"
        f"{source_context}"
        f"{repair_context}"
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
        "2) 必须保留每一个已有的 parts[].part_id；绝对不能合并、删除或修改教师创建的 parts[] 小问结构。\n"
        "3) answer_key.questions[].parts 必须通过 part_id 与 rubric 中的 parts 保持一致对齐。\n"
        "4) 补全缺失的答案、accepted_forms、解析、步骤分、证明扣分项和证据链规则。\n"
        "5) 保持整张试卷总分 total_score 和各题 max_score 的总和精确等于 100。\n"
        "6) 对于证明题或计算解答题，按证据步骤步骤分进行细化，并保守地给与仅有答案无过程的得分限制。\n\n"
        "7) 知识点与题目标签由题库程序单独维护，不要输出任何 knowledge 字段。\n\n"
        "8) 区分必要数学义务与参考答案的书写形式：同一方法中的符号关系、数值关系、"
        "等价变形和合并书写可以完成相同评分点，补入 equivalent_rules；"
        "allow_alternative_methods=false 不禁止这种等价表达。"
        "已给出对应数值、成立关系与结论时，不把省略简单算术展开判作缺少证明。"
        "保留题干或教师明确规定的特殊计算过程和方法要求；不因参考答案较详细而新增要求，"
        "也不把只抄条件、关系错误或循环论证放宽为完整证明。\n\n"
        "9) 定理或判定的适用前提、必要条件（直角/垂直/平行、全等或相似的对应关系、取值范围、"
        "分母不为零等）必须单列为独立步骤（判定点），不得与“列式”“代入”合并为一步；"
        "该步 core_goal 写明要求成立的数学含义，required_elements 写学生可写出的任一书面形式"
        "并注明“任一即可”（例：∠OEB=90°、OE⊥EB、△OEB 为直角三角形、图中直角标记并在推理中引用）。"
        "“任一即可”只用于同一个数学结果的不同写法（如 ∠OEB=90° 与 OE⊥EB）；"
        "不同的变形、运算或中间结果不得在同一步的 required_elements 中用“任一即可”"
        "并列为备选，确有独立教学价值的应各自单列为步骤，否则只保留该步必须成立的那一个结果。"
        "同一步的 counterexamples 与 deduction_rules 不得与其给分条件相矛盾："
        "列为反例的错误，不能仅凭写出其他备选形式而判为达成。"
        "每个步骤是整点有无的判定单位，粒度以“教师会否为此单独扣分”为准："
        "有独立教学价值的中间结论、条件、结论各占一步。\n\n"
        f"当前评分标准 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
    )
