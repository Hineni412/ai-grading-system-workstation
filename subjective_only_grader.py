import json
import logging
from pathlib import Path
from typing import Any

from llm_client import LLMClient
from ai_grader import AIGrader

logger = logging.getLogger(__name__)

def grade_subjective_only(
    subject_only_rubric: dict,
    subject_image_paths: list[Path],
    student_id: str,
    session_id: str,
    llm_client: "LLMClient",
    model: str | None = None,
    flow_type: str = "dry_run_subjective_only"
) -> dict:
    """
    Grades only the subjective parts of a paper using the provided rubric and subjective images.
    """
    system_prompt = (
        "你是一个专业的阅卷助手。你目前的任务是只批改本次提供的主观题/解答题区域。\n"
        "不要批改未提供的题。不要推测选择题、填空题得分。\n"
        "只根据 subject_only_rubric 中列出的题目评分。\n"
        "返回必须是严格的 JSON 对象，包含：student_name, total_score, student_score, needs_human_review, grading_details。\n"
        "grading_details 中的每一项需要包含：question_id, score_awarded, deduction_reason, knowledge_id, knowledge_ids, error_category, error_summary, confidence_score。\n"
    )
    
    # Use existing mapping/instructions logic from ai_grader to preserve formatting rules
    # We can create a dummy ai_grader just to reuse its user prompt logic, or rewrite it here.
    # It's cleaner to just rewrite a simplified but strict version for subjective only.
    
    user_prompt = f"已识别学生: {student_id}\n\n"
    user_prompt += "请仔细阅读下列评分标准，对提供的解答题切片图片进行批改。\n"
    user_prompt += f"subject_only_rubric:\n{json.dumps(subject_only_rubric, ensure_ascii=False)}\n\n"
    
    # Load image blobs
    image_blobs = []
    for path in subject_image_paths:
        with open(path, "rb") as f:
            image_blobs.append(f.read())
            
    import time
    t0 = time.time()
            
    from usage_logger import extract_usage_fields
    # Usage callback
    usage_stats = {}
    usage_missing = False
    def usage_cb(res, kwargs=None):
        try:
            fields = extract_usage_fields(res)
            usage_stats.update(fields)
        except Exception as e:
            logger.warning(f"Failed to extract subjective usage fields: {e}")

    try:
        parsed = llm_client.json_from_images(
            prompt=user_prompt,
            image_blobs=image_blobs,
            model=model or llm_client.settings.grading_model,
            system_prompt=system_prompt,
            usage_callback=usage_cb
        )
    except Exception as e:
        logger.error(f"Subjective only grading failed: {e}")
        raise e
        
    try:
        from usage_logger import log_llm_usage
        import datetime
        import time
        from PIL import Image
        
        # Calculate max image width and height
        max_w = 0
        max_h = 0
        for p in subject_image_paths:
            try:
                with Image.open(p) as img:
                    w, h = img.size
                    max_w = max(max_w, w)
                    max_h = max(max_h, h)
            except Exception: pass
        
        if usage_stats.get("total_tokens", 0) == 0:
            usage_missing = True
            
        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "chain_type": "subjective_only_grading",
            "flow_type": flow_type,
            "model": model or llm_client.settings.grading_model,
            "prompt_tokens": usage_stats.get("prompt_tokens", 0),
            "completion_tokens": usage_stats.get("completion_tokens", 0),
            "reasoning_tokens": usage_stats.get("reasoning_tokens", 0),
            "total_tokens": usage_stats.get("total_tokens", 0),
            "latency_ms": usage_stats.get("latency_ms", 0),  # llm_client should inject this if we didn't override it, but let's manually compute it below
            "image_count": len(subject_image_paths),
            "max_image_width": max_w,
            "max_image_height": max_h,
            "session_id": str(session_id),
            "student_id": str(student_id),
            "paper_id": None,
            "success": True,
            "usage_missing": usage_missing,
            "metadata": {
                "main_grading_config_used": True,
                "objective_config_used": False,
                "production_grading_model_used": False
            }
        }
        if record["latency_ms"] == 0 and "latency" in usage_stats:
            record["latency_ms"] = usage_stats["latency"]
            
        # fallback latency if extract_usage didn't grab it
        if record["latency_ms"] == 0:
            record["latency_ms"] = int((time.time() - t0) * 1000)
            
        log_llm_usage(record)
    except Exception as e:
        logger.warning(f"Failed to log usage: {e}")
        
    return parsed
