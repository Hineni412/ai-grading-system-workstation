def normalize_question_id(question_id: str) -> str:
    if not question_id:
        return ""
    return str(question_id).lower().replace(" ", "").replace("第", "").replace("题", "").replace(".", "")

def get_standard_answer_for_question(rubric: dict, question_id: str) -> tuple[str | None, str]:
    if not isinstance(rubric, dict):
        return None, "missing"
        
    questions = rubric.get("questions", [])
    if isinstance(questions, dict):
        questions = list(questions.values())
        
    target_qid = normalize_question_id(question_id)
        
    for q in questions:
        q_id = str(q.get("question_id", ""))
        if normalize_question_id(q_id) == target_qid:
            for field in ["standard_answer", "correct_answer", "answer", "answers", "reference_answer", "expected_answer", "solution", "canonical_answer"]:
                if field in q and q[field]:
                    val = q[field]
                    if isinstance(val, list):
                        return val[0], "rubric_question"
                    return str(val), "rubric_question"
                    
            if "accepted_forms" in q and q["accepted_forms"]:
                return str(q["accepted_forms"][0]), "rubric_question"
                
            for part in q.get("parts", []):
                for field in ["standard_answer", "correct_answer", "answer", "answers", "reference_answer", "expected_answer", "solution", "canonical_answer"]:
                    if field in part and part[field]:
                        val = part[field]
                        if isinstance(val, list):
                            return val[0], "rubric_part"
                        return str(val), "rubric_part"
                        
    return None, "missing"
