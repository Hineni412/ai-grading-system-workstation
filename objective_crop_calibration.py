import json
import logging
from pathlib import Path

import cv2
import numpy as np

from path_manager import get_path_manager

logger = logging.getLogger(__name__)

def get_overrides_file() -> Path:
    pm = get_path_manager()
    return pm.data_root / "config" / "objective_recognition_overrides.json"

def load_recognition_overrides(session_id: str) -> dict:
    file_path = get_overrides_file()
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(str(session_id), {})

def save_recognition_override(session_id: str, question_id: str, box: dict, notes: str = "") -> None:
    file_path = get_overrides_file()
    data = {}
    if file_path.exists():
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
    sid = str(session_id)
    if sid not in data:
        data[sid] = {}
        
    import datetime
    data[sid][question_id] = {
        "recognition_box": box,
        "source": "manual_calibration",
        "updated_at": datetime.datetime.now().isoformat(),
        "updated_by": "user",
        "notes": notes
    }
    
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
def get_effective_recognition_box(session_id: str, question_id: str, base_region: dict) -> tuple[dict, str]:
    """Returns (box_dict, source)"""
    overrides = load_recognition_overrides(session_id)
    if question_id in overrides:
        return overrides[question_id]["recognition_box"], overrides[question_id]["source"]
        
    # fallback to base region
    rec_box = base_region.get("recognition_box") or base_region.get("answer_box") or base_region.get("choice_mark_box")
    if rec_box:
        if base_region.get("recognition_box"): return rec_box, "recognition_box"
        if base_region.get("answer_box"): return rec_box, "answer_box"
        return rec_box, "choice_mark_box"
        
    return base_region, "answer_region"

def validate_recognition_box_quality(session_id: str, question_id: str, box: dict, img_array=None) -> dict:
    """
    Validates crop quality. If img_array is None, it only does size checks.
    """
    res = {
        "crop_empty": False,
        "crop_too_small": False,
        "edge_touch_detected": False,
        "multi_option_text_suspected": False,
        "suspected_contamination": False,
        "suggested_action": "none"
    }
    
    w, h = int(box.get("w", 0)), int(box.get("h", 0))
    if w <= 0 or h <= 0:
        res["crop_empty"] = True
        res["suggested_action"] = "fix_empty_box"
        return res
        
    if w < 20 or h < 20:
        res["crop_too_small"] = True
        res["suggested_action"] = "enlarge_box"
        return res
        
    if h > 150:
        res["suspected_contamination"] = True
        res["suggested_action"] = "reduce_height_to_avoid_other_questions"
        
    if img_array is not None:
        # Check edge touch
        # Convert to grayscale
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_array
            
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
        # Check if there are significant black pixels on the edges
        top = np.sum(thresh[0:3, :]) / 255
        bottom = np.sum(thresh[-3:, :]) / 255
        left = np.sum(thresh[:, 0:3]) / 255
        right = np.sum(thresh[:, -3:]) / 255
        
        edge_pixels = top + bottom + left + right
        
        if edge_pixels > 20: # arbitrary threshold for touching ink
            res["edge_touch_detected"] = True
            if res["suggested_action"] == "none":
                res["suggested_action"] = "expand_box_to_contain_full_ink"
                
    return res

def generate_crop_preview_pack(session_id: str, question_id: str, sample_size: int = 5) -> dict:
    # This will be implemented in preview_objective_crop_calibration.py
    pass
