import os
import json
import base64
import time
from pathlib import Path
from db_manager import DBManager
from api_profiles import get_objective_api_config
from answer_normalizer import contains_prompt_injection_or_score_bait, normalize_answer_text, match_fill_blank_answer

def score_fill_blank_by_program(
    raw_answer: str,
    standard_answer: str | list | None,
    max_score: float,
    confidence: float,
    threshold: float = 0.85
) -> dict:
    result = {
        "is_correct": None,
        "score": None,
        "auto_scored": False,
        "need_review": True,
        "review_reason": ""
    }
    
    if max_score is None or max_score <= 0:
        result["review_reason"] = "max_score_missing_or_invalid"
        return result
        
    if not raw_answer or str(raw_answer).strip() == "":
        result["is_correct"] = False
        result["score"] = 0.0
        result["auto_scored"] = True
        result["need_review"] = False
        result["review_reason"] = "blank"
        return result

    if contains_prompt_injection_or_score_bait(raw_answer):
        result["is_correct"] = False
        result["score"] = 0.0
        result["auto_scored"] = True
        result["need_review"] = False
        result["review_reason"] = "prompt_injection_or_score_bait"
        return result

    if not standard_answer or (isinstance(standard_answer, str) and standard_answer.strip() == "") or (isinstance(standard_answer, list) and len(standard_answer) == 0):
        result["review_reason"] = "standard_answer_missing"
        return result
        
    if confidence < threshold:
        result["review_reason"] = "low_confidence"
        return result
        
    match_res = match_fill_blank_answer(raw_answer, standard_answer)
    is_match = match_res.get("matched")
    status = match_res.get("match_status", "")
    reason = match_res.get("match_reason", "")
    
    result["match_status"] = status
    
    if is_match is True:
        result["is_correct"] = True
        result["score"] = max_score
        result["auto_scored"] = True
        result["need_review"] = False
    elif is_match is False:
        result["is_correct"] = False
        result["score"] = 0.0
        result["auto_scored"] = True
        result["need_review"] = False
    else:
        result["is_correct"] = None
        result["score"] = None
        result["auto_scored"] = False
        result["need_review"] = True
        result["review_reason"] = reason or status or "equivalence_uncertain"
        
    return result

def recognize_fill_blank_answer(
    image_path: str,
    question_id: str,
    standard_answer: str | list | None,
    max_score: float,
    model: str | None = None,
    session_id: str = "",
    student_id: str = ""
) -> dict:
    from backend.llm import LLMProtocolAdapter, LLMRequestKind
    start_time = time.time()
    
    config = get_objective_api_config()
    if model:
        config["model"] = model
        
    result = {
        "question_id": question_id,
        "raw_answer": "",
        "normalized_student_answer": "",
        "standard_answer": standard_answer,
        "normalized_standard_answer": normalize_answer_text(standard_answer),
        "is_correct": None,
        "score": None,
        "max_score": max_score,
        "confidence": 0.0,
        "need_review": True,
        "review_reason": "",
        "auto_scored": False,
        "model": config.get("model") or "unknown",
        "thinking_type": "disabled",
        
        "objective_enabled": config.get("enabled", False),
        "configured_objective_model": config.get("model", ""),
        "actual_model_used": "unknown",
        "objective_base_url_masked": str(config.get("base_url", ""))[:15] + "...",
        "temperature": config.get("temperature", 0.0),
        "max_tokens": config.get("max_tokens", 150),
        
        # token tracking
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "success": False,
        "error_type": ""
    }
    
    if not config.get("enabled"):
        result["review_reason"] = "objective_api_disabled"
        return result
        
    if not config.get("model"):
        result["review_reason"] = "objective_model_not_configured"
        return result
        
    if max_score is None or max_score <= 0:
        result["review_reason"] = "max_score_missing_or_invalid"
        return result
        
    if not config.get("api_key") or not config.get("base_url"):
        result["review_reason"] = "objective_api_key_or_url_missing"
        return result
        
    result["model"] = config["model"]
    result["actual_model_used"] = config["model"]
    
    try:
        from PIL import Image
        import io
        
        with open(image_path, "rb") as f:
            image_bytes = f.read()
            
        file_size_kb = len(image_bytes) / 1024.0
        
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            max_w, max_h = 1400, 1000
            if width > max_w or height > max_h:
                img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                image_bytes = buffer.getvalue()
                width, height = img.size
                
        result["image_width"] = width
        result["image_height"] = height
        result["image_file_size_kb"] = len(image_bytes) / 1024.0
            
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{encoded}"
        
        prompt = (
            "请识别图片中学生填空题最终答案。只返回 JSON：\n"
            '{"raw_answer":"", "confidence":0-1, "need_review":true/false, "review_reason":""}\n'
            "不要解释，不要评分。"
        )
        
        result["prompt_chars"] = len(prompt)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}}
                ]
            }
        ]
        
        adapter = LLMProtocolAdapter(
            config["api_key"],
            config["base_url"],
            policy_profile=config.get("policy_profile"),
        )
        
        completion = adapter.chat_completions(
            request_kind=LLMRequestKind.RECOGNITION,
            model=config["model"],
            kwargs={
                "messages": messages,
                "temperature": config.get("temperature", 0.0),
                "max_tokens": config.get("max_tokens", 150),
                "response_format": {"type": "json_object"},
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
        
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        
        if hasattr(completion, "usage") and completion.usage:
            result["prompt_tokens"] = getattr(completion.usage, "prompt_tokens", 0) or 0
            result["completion_tokens"] = getattr(completion.usage, "completion_tokens", 0) or 0
            result["total_tokens"] = getattr(completion.usage, "total_tokens", 0) or 0
            
            completion_tokens_details = getattr(completion.usage, "completion_tokens_details", None)
            if completion_tokens_details:
                result["reasoning_tokens"] = getattr(completion_tokens_details, "reasoning_tokens", 0) or 0
                
        content = completion.choices[0].message.content
        
        if content.startswith("```json"):
            content = content[7:-3]
        elif content.startswith("```"):
            content = content[3:-3]
            
        parsed = json.loads(content)
        
        raw_answer = parsed.get("raw_answer", "")
        confidence = float(parsed.get("confidence", 0.0))
        need_review = bool(parsed.get("need_review", False))
        review_reason = str(parsed.get("review_reason", ""))
        
        result["raw_answer"] = raw_answer
        result["normalized_student_answer"] = normalize_answer_text(raw_answer)
        result["confidence"] = confidence
        
        if need_review:
            result["need_review"] = True
            result["review_reason"] = review_reason
            result["match_status"] = "review_from_model"
        else:
            score_info = score_fill_blank_by_program(raw_answer, standard_answer, max_score, confidence)
            result["is_correct"] = score_info["is_correct"]
            result["score"] = score_info["score"]
            result["auto_scored"] = score_info["auto_scored"]
            result["match_status"] = score_info.get("match_status", "")
            
            if score_info["need_review"]:
                result["need_review"] = True
                result["review_reason"] = score_info["review_reason"]
            else:
                result["need_review"] = False
                result["review_reason"] = ""
                
        result["success"] = True
        
    except Exception as e:
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        result["success"] = False
        result["error_type"] = str(e)
        result["need_review"] = True
        result["review_reason"] = f"Exception: {str(e)}"
        
    return result

def log_fill_blank_recognition(log_file: Path, exam_id: str, student_id: str, image_path: str, result: dict):
    os.makedirs(log_file.parent, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "exam_id": exam_id,
            "student_id": student_id,
            "image_path": image_path,
            "result": result
        }
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
