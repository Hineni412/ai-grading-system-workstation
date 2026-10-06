import datetime
import json
import os
from pathlib import Path

from path_manager import get_path_manager

LOG_FILE = Path("logs/llm_usage.jsonl")


def usage_log_path() -> Path:
    return get_path_manager().logs_dir / "llm_usage.jsonl"

def extract_usage_fields(response_or_usage: object) -> dict:
    result = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
    
    if not response_or_usage:
        return result
        
    usage = getattr(response_or_usage, "usage", None)
    if usage is None:
        usage = response_or_usage # Maybe they passed usage directly
        
    if isinstance(usage, dict):
        result["prompt_tokens"] = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
        result["completion_tokens"] = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
        result["total_tokens"] = usage.get("total_tokens", 0) or 0
        
        prompt_details = usage.get("prompt_tokens_details", {})
        if isinstance(prompt_details, dict):
            result["cached_tokens"] = prompt_details.get("cached_tokens", 0) or 0
        else:
            result["cached_tokens"] = usage.get("cached_tokens", 0) or 0
            
        comp_details = usage.get("completion_tokens_details", {})
        if isinstance(comp_details, dict):
            result["reasoning_tokens"] = comp_details.get("reasoning_tokens", 0) or 0
        else:
            result["reasoning_tokens"] = usage.get("reasoning_tokens", 0) or 0
    else:
        result["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
        result["completion_tokens"] = getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0
        result["total_tokens"] = getattr(usage, "total_tokens", 0) or 0
        
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details:
            result["cached_tokens"] = getattr(prompt_details, "cached_tokens", 0) or 0
        else:
            result["cached_tokens"] = getattr(usage, "cached_tokens", 0) or 0
            
        comp_details = getattr(usage, "completion_tokens_details", None)
        if comp_details:
            result["reasoning_tokens"] = getattr(comp_details, "reasoning_tokens", 0) or 0
        else:
            result["reasoning_tokens"] = getattr(usage, "reasoning_tokens", 0) or 0
            
    return result

def log_llm_usage(record: dict, *, log_file: Path | None = None) -> None:
    try:
        log_file = usage_log_path() if log_file is None else Path(log_file)
        os.makedirs(log_file.parent, exist_ok=True)
        # Ensure default fields are present
        default_record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "request_id": "",
            "session_id": "",
            "exam_id": "",
            "student_id": "",
            "question_id": "",
            "question_type": "",
            "chain_type": "",
            "flow_type": "",
            "model": "",
            "actual_model_used": "",
            "api_base_url_masked": "",
            "thinking_type": "",
            "temperature": 0.0,
            "max_tokens": 0,
            "cache_mode": "",
            "context_id": "",
            "objective_config_used": False,
            "main_grading_config_used": False,
            "production_grading_model_used": False,
            "prompt_tokens": 0,
            "cached_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "image_count": 0,
            "image_width": None,
            "image_height": None,
            "image_file_size_kb": None,
            "max_image_width": None,
            "max_image_height": None,
            "total_image_file_size_kb": None,
            "prompt_chars": None,
            "system_prompt_chars": None,
            "user_prompt_chars": None,
            "response_chars": None,
            "latency_ms": 0,
            "success": False,
            "json_valid": False,
            "need_review": False,
            "auto_scored": False,
            "score": None,
            "max_score": None,
            "error_type": "",
            "error_message": ""
        }
        
        # Merge
        default_record.update(record)
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(default_record, ensure_ascii=False) + "\n")
    except Exception:
        print("Warning: Failed to log usage")
