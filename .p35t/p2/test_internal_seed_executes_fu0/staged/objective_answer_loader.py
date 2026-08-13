import json
import logging
import re
from pathlib import Path
from typing import Any

from path_manager import get_path_manager

logger = logging.getLogger(__name__)

ANSWER_FIELDS = [
    "standard_answer",
    "correct_answer",
    "answer",
    "answers",
    "reference_answer",
    "expected_answer",
    "solution",
    "canonical_answer",
    "accepted_forms"
]

def _normalize_question_id(qid: str) -> str:
    """Normalize question id: 1 -> Q1, 第1题 -> Q1, 1(1) -> Q1(1)"""
    if not isinstance(qid, str):
        qid = str(qid)
    
    qid = qid.strip()
    
    # Remove '第' and '题'
    qid = qid.replace("第", "").replace("题", "")
    
    # If it just a number, prefix with Q
    if re.match(r"^\d+$", qid):
        return f"Q{qid}"
        
    # If it is like 1(1) or 1.1 or 1-1, try to normalize
    m = re.match(r"^(\d+)([\.\-\(（]+)(\d+)([\)）]*)$", qid)
    if m:
        return f"Q{m.group(1)}({m.group(3)})"
        
    # Standardize Q1 to Q1
    m = re.match(r"^[Qq](\d+)$", qid)
    if m:
        return f"Q{m.group(1)}"
        
    return qid

def _extract_answer_from_dict(d: dict, file_name: str) -> tuple[str, str] | None:
    for field in ANSWER_FIELDS:
        if field in d:
            val = d[field]
            if isinstance(val, list):
                # e.g. accepted_forms
                val = "或".join(str(v) for v in val if str(v).strip())
            
            if val is not None and str(val).strip():
                return str(val).strip(), field
    return None

def _extract_answers_from_data(data: Any, file_name: str) -> dict[str, dict]:
    answers = {}
    
    # Case 1: list of questions
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # get ID
                qid = item.get("question_id") or item.get("id") or item.get("qid") or item.get("题号") or item.get("question")
                if not qid: continue
                qid = _normalize_question_id(str(qid))
                
                ans_info = _extract_answer_from_dict(item, file_name)
                if ans_info:
                    answers[qid] = {
                        "standard_answer": ans_info[0],
                        "source": file_name,
                        "field": ans_info[1]
                    }
                    
                # check parts
                parts = item.get("parts", [])
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict):
                            part_id = part.get("part_id") or part.get("id")
                            if not part_id: continue
                            part_id = _normalize_question_id(str(part_id))
                            part_ans_info = _extract_answer_from_dict(part, file_name)
                            if part_ans_info:
                                answers[part_id] = {
                                    "standard_answer": part_ans_info[0],
                                    "source": file_name,
                                    "field": part_ans_info[1]
                                }
                                
    # Case 2: dict containing 'questions' or top level dict
    elif isinstance(data, dict):
        # if 'questions' in data, recurse
        if "questions" in data and isinstance(data["questions"], list):
            answers.update(_extract_answers_from_data(data["questions"], file_name))
            
        # Or if the top-level keys look like question IDs
        for k, v in data.items():
            if k == "questions" or k == "meta": continue
            # check if v is dict
            if isinstance(v, dict):
                ans_info = _extract_answer_from_dict(v, file_name)
                if ans_info:
                    qid = _normalize_question_id(k)
                    answers[qid] = {
                        "standard_answer": ans_info[0],
                        "source": file_name,
                        "field": ans_info[1]
                    }
            elif isinstance(v, str) or isinstance(v, int) or isinstance(v, float):
                # raw key-value answers e.g. {"Q1": "B"}
                qid = _normalize_question_id(k)
                answers[qid] = {
                    "standard_answer": str(v),
                    "source": file_name,
                    "field": "top_level_value"
                }

    return answers

def load_objective_answer_sources(session_id: str) -> dict:
    pm = get_path_manager()
    
    # 查找可能的目录
    possible_dirs = [
        pm.templates_dir / f"session_{session_id}",
        pm.project_root / "user_data" / "templates" / f"session_{session_id}",
    ]
    
    sources_loaded = []
    answers_by_file = {}
    
    for d in possible_dirs:
        if not d.exists() or not d.is_dir():
            continue
            
        # load matching files
        for p in d.iterdir():
            if not p.is_file() or not p.name.endswith(".json"):
                continue
                
            name_lower = p.name.lower()
            if (name_lower.startswith("template_mapping_answer_") or
                name_lower == "answer_key.json" or
                name_lower.startswith("template_mapping_config_") or
                name_lower == "rubric.json" or
                name_lower.startswith("mapping_answer_") or
                name_lower.startswith("answer_")):
                
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    ans_map = _extract_answers_from_data(data, p.name)
                    answers_by_file[p.name] = ans_map
                    
                    sources_loaded.append({
                        "path": str(p),
                        "name": p.name,
                        "success": True
                    })
                except Exception as e:
                    sources_loaded.append({
                        "path": str(p),
                        "name": p.name,
                        "success": False,
                        "error": str(e)
                    })

    # Merge answers according to priority
    # priority from lowest to highest:
    # 4. rubric.json
    # 3. template_mapping_config_*.json
    # 2. answer_key.json
    # 1. template_mapping_answer_*.json / answer_*.json
    
    merged_answers = {}
    
    def get_priority(filename: str) -> int:
        name_lower = filename.lower()
        if name_lower.startswith("template_mapping_answer_") or name_lower.startswith("answer_") or name_lower.startswith("mapping_answer_"):
            return 100
        if name_lower == "answer_key.json":
            return 80
        if name_lower.startswith("template_mapping_config_"):
            return 60
        if name_lower == "rubric.json":
            return 40
        return 0
        
    sorted_files = sorted(answers_by_file.keys(), key=get_priority)
    
    # Merge (higher priority overrides lower)
    for f in sorted_files:
        merged_answers.update(answers_by_file[f])
        
    return {
        "sources_loaded": sources_loaded,
        "answers_by_question_id": merged_answers,
        "diagnostics": {
            "total_files_scanned": len(sources_loaded),
            "files_with_answers": [f for f, ans in answers_by_file.items() if len(ans) > 0]
        }
    }

def get_standard_answer_for_question(combined_answer_source: dict, question_id: str) -> tuple[str, str, str]:
    """
    Returns (standard_answer, source_file, source_field).
    If not found, returns ("", "", "").
    """
    answers_map = combined_answer_source.get("answers_by_question_id", {})
    
    # Try direct match
    if question_id in answers_map:
        ans = answers_map[question_id]
        return ans.get("standard_answer", ""), ans.get("source", ""), ans.get("field", "")
        
    # Try normalized match
    norm_qid = _normalize_question_id(question_id)
    if norm_qid in answers_map:
        ans = answers_map[norm_qid]
        return ans.get("standard_answer", ""), ans.get("source", ""), ans.get("field", "")
        
    return "", "", ""

def _extract_score_from_dict(item: dict) -> float | None:
    score_fields = ["max_score", "score", "points", "full_score", "total_score", "分值", "满分", "question_score", "part_score"]
    for field in score_fields:
        val = item.get(field)
        if val is not None:
            try:
                return float(val)
            except:
                pass
    return None

def get_max_score_for_question(
    session_id: str,
    question_id: str,
    question_type: str | None = None,
    rubric: dict | None = None,
    template_config: dict | None = None,
    main_result: dict | None = None,
    answer_sources: dict | None = None
) -> tuple[float | None, str, dict]:
    """
    Finds the max_score for a given objective question.
    Returns: (max_score, max_score_source, diagnostics)
    """
    diagnostics = {
        "template_max_score": None,
        "rubric_max_score": None,
        "main_max_score_if_available": None,
        "subquestion_aggregation_mismatch": False
    }
    
    qid_norm = _normalize_question_id(question_id)
    
    # 1. Search in template_config
    if template_config:
        for q in template_config.get("questions", []):
            if _normalize_question_id(str(q.get("question_id", ""))) == qid_norm:
                score = _extract_score_from_dict(q)
                if score is not None:
                    diagnostics["template_max_score"] = score
                    break
                    
    # 2. Search in rubric
    if rubric:
        for q in rubric.get("questions", []):
            if _normalize_question_id(str(q.get("question_id", ""))) == qid_norm:
                score = _extract_score_from_dict(q)
                if score is not None:
                    diagnostics["rubric_max_score"] = score
                    break
                    
    # 3. Search in main_result (which might aggregate subquestions)
    if main_result:
        items = getattr(main_result, "grading_details", None)
        if items is None and hasattr(main_result, "get"):
            items = main_result.get("grading_details", [])
            if not items: items = main_result.get("details", [])
            if not items: items = main_result.get("items", [])
            if not items: items = main_result.get("questions", [])
            if not items: items = main_result.get("scores", [])
        if items is None and isinstance(main_result, list):
            items = main_result
            
        if items and isinstance(items, list):
            for item in items:
                it_qid = getattr(item, "question_id", None) or getattr(item, "id", None)
                if it_qid is None and hasattr(item, "get"):
                    it_qid = item.get("question_id") or item.get("id") or item.get("题号")
                if it_qid and _normalize_question_id(str(it_qid)) == qid_norm:
                    sc = _extract_score_from_dict(item if isinstance(item, dict) else item.__dict__)
                    if sc is not None:
                        diagnostics["main_max_score_if_available"] = sc
                    break

    # Determine aggregated mismatch
    t_score = diagnostics["template_max_score"]
    r_score = diagnostics["rubric_max_score"]
    m_score = diagnostics["main_max_score_if_available"]
    
    if (t_score is not None or r_score is not None) and m_score is not None:
        base_score = t_score if t_score is not None else r_score
        if m_score > base_score * 1.5:  # Significant difference
            diagnostics["subquestion_aggregation_mismatch"] = True
            
    # Resolution logic: Always prefer template/rubric over main_result
    if diagnostics["template_max_score"] is not None:
        return diagnostics["template_max_score"], "template_mapping_config", diagnostics
    if diagnostics["rubric_max_score"] is not None:
        return diagnostics["rubric_max_score"], "rubric.json", diagnostics
        
    if answer_sources and "answers_by_question_id" in answer_sources:
        pass # The current loader doesn't keep full object, but we could check if score was stored.
        # Actually answer_sources only keeps standard_answer. So we fallback.
                        
    # Last fallback is main result max score
    if diagnostics["main_max_score_if_available"] is not None:
        return diagnostics["main_max_score_if_available"], "main_result_grading_details", diagnostics

    return None, "fallback_missing", diagnostics
