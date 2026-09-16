import base64
import io
import json
import os
import time
from pathlib import Path
from typing import Any

from PIL import Image
from answer_normalizer import contains_prompt_injection_or_score_bait

def crop_choice_region(front_image_path: Path, back_image_path: Path, question_id: str, regions: list[dict], output_dir: Path, session_id: str = "") -> tuple[Path | None, bool, bool, str]:
    """从正面或背面裁剪出目标题目的图片。"""
    target_region = None
    for region in regions:
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        if qid == question_id:
            target_region = region
            break
            
    if not target_region:
        return None, False, False, "not_found"
        
    page_type = str(target_region.get("page", "front"))
    img_path = front_image_path if page_type == "front" else back_image_path
    
    if not img_path or not img_path.exists():
        return None, False, False, "image_missing"
        
    try:
        from objective_crop_calibration import get_effective_recognition_box, validate_recognition_box_quality
        with Image.open(img_path) as img:
            width, height = img.size
            
            use_box, source = get_effective_recognition_box(session_id, question_id, target_region)
            
            rx = int(float(target_region.get("x", 0)))
            ry = int(float(target_region.get("y", 0)))
            
            x = int(float(use_box.get("x", rx)))
            y = int(float(use_box.get("y", ry)))
            w = int(float(use_box.get("w", 0)))
            h = int(float(use_box.get("h", 0)))
            
            left = max(0, min(x, width - 1))
            top = max(0, min(y, height - 1))
            right = max(left + 1, min(x + w, width))
            bottom = max(top + 1, min(y + h, height))
            
            suspected_contamination = False
            edge_touch_detected = False
            
            if w > 8 and h > 8:
                cropped = img.crop((left, top, right, bottom))
                import numpy as np
                img_array = np.array(cropped)
                
                quality = validate_recognition_box_quality(session_id, question_id, use_box, img_array)
                suspected_contamination = quality.get("suspected_contamination", False)
                edge_touch_detected = quality.get("edge_touch_detected", False)
                
                os.makedirs(output_dir, exist_ok=True)
                out_path = output_dir / f"q_{question_id}_{os.path.basename(img_path)}"
                cropped.save(out_path, format="JPEG", quality=85)
                return out_path, suspected_contamination, edge_touch_detected, source
    except Exception as e:
        print(f"Crop failed: {e}")
        return None, False, False, f"error_{e}"
        
    return None, False, False, "unknown"

def score_choice_by_program(
    selected: str,
    standard_answer: str | list[str] | None,
    max_score: float,
    confidence: float,
    threshold: float = 0.85,
) -> dict:
    missing_answer = selected is None
    selected = str(selected or "").strip().upper()
    
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
        
    if not missing_answer and selected in {"", "BLANK"}:
        result["is_correct"] = False
        result["score"] = 0.0
        result["auto_scored"] = True
        result["need_review"] = False
        result["review_reason"] = "blank"
        return result

    if contains_prompt_injection_or_score_bait(selected):
        result["is_correct"] = False
        result["score"] = 0.0
        result["auto_scored"] = True
        result["need_review"] = False
        result["review_reason"] = "prompt_injection_or_score_bait"
        return result

    answer_values = standard_answer if isinstance(standard_answer, list) else [standard_answer]
    accepted_answers = {
        str(answer or "").strip().upper()
        for answer in answer_values
        if str(answer or "").strip()
    }
    if not accepted_answers:
        result["review_reason"] = "standard_answer_missing"
        return result
    
    if selected in {"MULTIPLE", "UNCLEAR"} or not selected:
        result["review_reason"] = selected.lower() if selected else "unclear"
        return result

    if selected not in {"A", "B", "C", "D", "E", "F"}:
        result["review_reason"] = "invalid_choice_answer"
        return result
        
    if confidence < threshold:
        result["review_reason"] = "low_confidence"
        return result
        
    result["auto_scored"] = True
    result["need_review"] = False
    
    if selected in accepted_answers:
        result["is_correct"] = True
        result["score"] = max_score
    else:
        result["is_correct"] = False
        result["score"] = 0.0
        
    return result

def recognize_choice_answer(
    image_path: Path,
    question_id: str,
    standard_answer: str | list | None,
    max_score: float,
    model: str | None = None,
    session_id: str = "",
    student_id: str = "",
    suspected_contamination: bool = False
) -> dict:
    from api_profiles import get_objective_api_config
    from backend.llm import LLMProtocolAdapter, LLMRequestKind
    config = get_objective_api_config()
    
    start_time = time.time()
    result = {
        "question_id": question_id,
        "selected": "unclear",
        "standard_answer": standard_answer,
        "is_correct": False,
        "score": 0.0,
        "max_score": max_score,
        "confidence": 0.0,
        "need_review": True,
        "review_reason": "",
        "auto_scored": False,
        "model": "unknown",
        "thinking_type": config.get("thinking_type", "disabled"),
        
        "objective_enabled": config.get("enabled", False),
        "configured_objective_model": config.get("model", ""),
        "actual_model_used": "",
        "objective_base_url_masked": str(config.get("base_url", ""))[:15] + "...",
        "temperature": config.get("temperature", 0.0),
        "max_tokens": config.get("max_tokens", 100),
        
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
            "请识别图片中学生选择题最终选项。只返回 JSON：\n"
            '{"selected":"A|B|C|D|E|F|blank|multiple|unclear","confidence":0-1,"need_review":true/false,"review_reason":""}\n'
            "不要解释，不要评分。\n"
            "只识别学生作答痕迹，例如圈选、勾选、涂黑、手写标记。\n"
            "不要把图片中印刷的 A/B/C/D 选项文字当作学生选择。\n"
            "如果图片中出现多个题目的选项或无法判断学生标记，返回 unclear。"
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
                "temperature": config["temperature"],
                "max_tokens": config.get("max_tokens", 100),
                "response_format": {"type": "json_object"},
                "extra_body": {"thinking": {"type": config["thinking_type"]}}
                if config["thinking_type"] != "disabled"
                else None,
            },
        )
        
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        
        if hasattr(completion, "usage") and completion.usage:
            result["prompt_tokens"] = getattr(completion.usage, "prompt_tokens", 0) or 0
            result["completion_tokens"] = getattr(completion.usage, "completion_tokens", 0) or 0
            result["total_tokens"] = getattr(completion.usage, "total_tokens", 0) or 0
            
            # extract reasoning tokens if available
            completion_tokens_details = getattr(completion.usage, "completion_tokens_details", None)
            if completion_tokens_details:
                result["reasoning_tokens"] = getattr(completion_tokens_details, "reasoning_tokens", 0) or 0
                
        content = completion.choices[0].message.content
        
        # Simple cleanup before json parse
        if content.startswith("```json"):
            content = content[7:-3]
        elif content.startswith("```"):
            content = content[3:-3]
            
        try:
            parsed = json.loads(content, strict=False)
        except json.JSONDecodeError:
            import re
            # Try to extract json object if surrounded by text
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0), strict=False)
            else:
                raise
        
        selected = parsed.get("selected", "unclear")
        confidence = float(parsed.get("confidence", 0.0))
        need_review = bool(parsed.get("need_review", False))
        review_reason = str(parsed.get("review_reason", ""))
        
        result["selected"] = selected
        result["confidence"] = confidence
        result["need_review"] = need_review
        result["review_reason"] = review_reason
        
        score_info = score_choice_by_program(selected, standard_answer, max_score, confidence)
        result["is_correct"] = score_info["is_correct"]
        result["score"] = score_info["score"]
        result["auto_scored"] = score_info["auto_scored"]
        
        if score_info["need_review"]:
            result["need_review"] = True
            result["review_reason"] = score_info["review_reason"]
            
        if suspected_contamination:
            result["auto_scored"] = False
            result["need_review"] = True
            result["review_reason"] = "crop_contamination_suspected"
            
        result["success"] = True
        
    except Exception as e:
        end_time = time.time()
        result["latency_ms"] = int((end_time - start_time) * 1000)
        result["success"] = False
        result["error_type"] = str(e)
        result["need_review"] = True
        result["review_reason"] = f"Exception: {str(e)}"
        
    return result

def log_choice_recognition(log_file: Path, exam_id: str, student_id: str, image_path: str, result: dict):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    entry = (
        f"exam_id={exam_id}, "
        f"student_id={student_id}, "
        f"question_id={result.get('question_id')}, "
        f"image_path={image_path}, "
        f"selected={result.get('selected')}, "
        f"standard_answer={result.get('standard_answer')}, "
        f"is_correct={result.get('is_correct')}, "
        f"score={result.get('score')}, "
        f"max_score={result.get('max_score')}, "
        f"confidence={result.get('confidence')}, "
        f"need_review={result.get('need_review')}, "
        f"review_reason={result.get('review_reason')}, "
        f"auto_scored={result.get('auto_scored')}, "
        f"model={result.get('model')}, "
        f"objective_enabled={result.get('objective_enabled')}, "
        f"configured_objective_model={result.get('configured_objective_model')}, "
        f"actual_model_used={result.get('actual_model_used')}, "
        f"objective_base_url_masked={result.get('objective_base_url_masked')}, "
        f"thinking_type={result.get('thinking_type')}, "
        f"temperature={result.get('temperature')}, "
        f"max_tokens={result.get('max_tokens')}, "
        f"prompt_tokens={result.get('prompt_tokens')}, "
        f"completion_tokens={result.get('completion_tokens')}, "
        f"reasoning_tokens={result.get('reasoning_tokens')}, "
        f"total_tokens={result.get('total_tokens')}, "
        f"latency_ms={result.get('latency_ms')}, "
        f"success={result.get('success')}, "
        f"error_type={result.get('error_type')}\n"
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)
