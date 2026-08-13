from __future__ import annotations


SHARED_GRADING_RULES = """
适用于所有批改模式的共同评分规则：
1. 客观评分单元（choice、fill_blank、judgement、true_false、direct_answer）实行全对全错。只有学生的有效答案与 canonical_answer 或 accepted_forms 等价时才给满分，否则给 0 分。
2. 不得读取或评分学生自己作废的内容，包括黑笔涂抹、划掉、删除线覆盖、框出作废、覆盖重写后放弃的内容，以及明显打叉的答案。若作废答案旁边另有清晰的替代答案，应读取并评分该替代答案。
3. 红笔教师批注、红笔勾选、红圈、红字评语、已有标注，以及任何其他非学生作答的评分痕迹，均不是学生答案证据。不得据此认定学生答案正确或完整。
4. 每个小问都必须遵循 rubric.parts[].response_mode；不得把父题的过程要求继承到 response_mode 不同的小问。
5. 对 response_mode=short_answer_points，应分别评分每个独立答案、空格或结果步骤。某一答案项正确时，即使没有证明或推导，也可以获得该项的全部分值。
6. 对 response_mode=visual_construction，应依据标准答案图和 visual_requirements 比较学生作图。除非 rubric 另行定义了 response_mode=process_required 的步骤，否则不得要求书面证明。
7. 只有 response_mode=process_required 的小问需要证明或推理证据。提取 evidence_steps，列出 missing_steps；只有实际出现相应证据时，才能给对应过程步骤分。
8. 非客观题不得一律按全对全错评分。表格、多空解答题，以及 Q11(P1) 这类分步小问，都必须逐空、逐步独立评分；一个空错误只扣该空对应的分值。
9. 对 response_mode=process_required，仅有最终答案或结论、图形标注、教师批注或零散算式，不能获得全部证明或过程分。answer_only_max_score 只适用于 response_mode=process_required 的小问。
10. 只抄写小问题干，或只打勾而没有所需证明过程，不属于有效过程证据；但不得因此抹除 short_answer_points 或 exact_objective 中已经成立的有效答案。
""".strip()
