import copy

def build_subjective_only_rubric(
    full_rubric: dict,
    objective_question_ids: list[str],
    objective_question_types: list[str] = None
) -> dict:
    if objective_question_types is None:
        objective_question_types = ["choice", "fill_blank"]
        
    subjective_rubric = copy.deepcopy(full_rubric)
    questions = subjective_rubric.get("questions", [])
    
    excluded_questions = []
    included_questions = []
    diagnostics = []
    
    new_questions = []
    for q in questions:
        qid = q.get("question_id")
        qtype = q.get("question_type")
        
        # Determine if we should exclude this question
        # Condition: must be in objective_question_ids AND its type must be allowed
        if qid in objective_question_ids and qtype in objective_question_types:
            excluded_questions.append(qid)
            diagnostics.append(f"Excluded {qid} (type: {qtype}) as it is covered by objective pipeline.")
        else:
            included_questions.append(qid)
            new_questions.append(q)
            
    subjective_rubric["questions"] = new_questions
    
    # We do NOT recompute total_score of the rubric because the prompt might get confused, 
    # but the AI needs to know it's only grading a subset. 
    # Actually, we can leave total_score as is, or remove it so it doesn't cause friction.
    # Leaving it as is for now.

    return {
        "subjective_rubric": subjective_rubric,
        "excluded_questions": excluded_questions,
        "included_questions": included_questions,
        "diagnostics": diagnostics
    }
