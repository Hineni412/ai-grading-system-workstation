from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from annotation_renderer import render_annotated_paper
from db_manager import DBManager
from path_manager import resolve_stored_file_path


class ManualReviewService:
    def __init__(self, db: DBManager, annotated_dir: Path) -> None:
        self.db = db
        self.annotated_dir = annotated_dir

    def render_result_annotation(self, result_id: int, highlight_qids: list[str] | None = None) -> dict[str, str] | None:
        context = self.db.get_result_context(result_id)
        if not context:
            return None

        session_id = int(context["session_id"])
        regions = self.db.list_answer_regions(session_id)
        details = self.db.get_result_details(result_id)

        detail_map = {str(item.get("question_id")): item for item in details}
        question_scores: dict[str, dict[str, Any]] = {}

        for region in regions:
            qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "")
            if not qid:
                continue

            detail = detail_map.get(qid)
            if detail:
                if highlight_qids is not None:
                    if qid not in highlight_qids:
                        continue
                else:
                    confidence_raw = detail.get("confidence_score")
                    reason_raw = detail.get("deduction_reason")
                    cat_raw = detail.get("error_category")
                    
                    confidence = None
                    if confidence_raw is not None:
                        import pandas as pd
                        if not pd.isna(confidence_raw):
                            try:
                                confidence = float(confidence_raw)
                            except ValueError:
                                pass
                                
                    if confidence is not None and confidence < 50:
                        is_substantive = True
                    else:
                        reason = str(reason_raw or "").strip() if reason_raw is not None and str(reason_raw).lower() not in ("nan", "none", "<na>") else ""
                        cat = str(cat_raw or "").strip() if cat_raw is not None and str(cat_raw).lower() not in ("nan", "none", "<na>") else ""
                        name_only_markers = ["姓名", "名字", "学生名", "ocr", "OCR"]
                        review_markers = ["模糊", "看不清", "争议", "无法判断", "无法识别", "需复核", "复核", "遮挡", "请人工", "未确"]
                        
                        is_substantive = False
                        if confidence is not None and confidence >= 50 and cat != "提示注入":
                            is_substantive = False
                        elif cat in ("未作答", "作废答案"):
                            pass
                        elif "未作答" in reason and len(reason) < 15:
                            pass
                        elif any(m in reason for m in review_markers) or "需复核" in cat:
                            is_substantive = True
                        else:
                            is_substantive = bool(reason and not any(m in reason for m in name_only_markers))
                            
                    if not is_substantive:
                        continue
                        
                question_scores[qid] = {
                    "score_awarded": detail.get("score_awarded"),
                    "max_score": None,
                    "deduction_reason": detail.get("deduction_reason"),
                }

        max_score_map = self._load_max_score_map(session_id)
        for qid, item in question_scores.items():
            if qid in max_score_map:
                item["max_score"] = max_score_map[qid]
            else:
                item["max_score"] = float(item.get("score_awarded") or 0)

        front_image = self._resolve_stored_file_path(context["front_image"])
        back_image = self._resolve_stored_file_path(context["back_image"])
        front_out = self.annotated_dir / f"session_{session_id}" / f"result_{result_id}_front_annotated.jpg"
        back_out = self.annotated_dir / f"session_{session_id}" / f"result_{result_id}_back_annotated.jpg"

        filtered_regions = [
            r for r in regions 
            if str(r.get("mapped_question_id") or r.get("detected_question_id") or "") in question_scores
        ]

        front_path, back_path = render_annotated_paper(
            front_image=front_image,
            back_image=back_image,
            regions=filtered_regions,
            question_scores=question_scores,
            output_front=front_out,
            output_back=back_out,
        )

        self.db.upsert_annotated_result(session_id, result_id, str(front_path), str(back_path))
        return {"front": str(front_path), "back": str(back_path)}

    def apply_manual_adjustments(self, result_id: int, adjustments: list[dict[str, Any]], highlight_qids: list[str] | None = None) -> dict[str, Any]:
        changed = 0
        def sanitize_val(v: Any) -> str | None:
            import pandas as pd
            if pd.isna(v) or v is None:
                return None
            s = str(v).strip()
            return None if s.lower() in ("nan", "none", "<na>", "") else s

        for row in adjustments:
            detail_id = row.get("detail_id")
            if detail_id is None:
                continue
            self.db.update_result_detail(
                detail_id=int(detail_id),
                score_awarded=float(row.get("score_awarded", 0)),
                deduction_reason=sanitize_val(row.get("deduction_reason")),
                error_category=sanitize_val(row.get("error_category")),
                error_summary=sanitize_val(row.get("error_summary")),
            )
            changed += 1

        self.db.recalculate_result_score(result_id)
        paths = self.render_result_annotation(result_id, highlight_qids=highlight_qids)
        return {"updated_details": changed, "annotated_paths": paths}

    def _load_max_score_map(self, session_id: int) -> dict[str, float]:
        session = self.db.get_grading_session(session_id)
        if not session:
            return {}

        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            return {}

        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        result: dict[str, float] = {}
        questions = rubric.get("questions") if isinstance(rubric, dict) else []
        if not isinstance(questions, list):
            return result

        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("question_id") or "").strip()
            if qid:
                try:
                    result[qid] = float(q.get("max_score", 0))
                except Exception:
                    pass

            parts = q.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pid = str(part.get("part_id") or "").strip()
                    if not pid:
                        continue
                    try:
                        result[pid] = float(part.get("part_score", 0))
                    except Exception:
                        continue

        return result

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        data_root = self.db.db_path.parent.parent if self.db.db_path.parent.name == "databases" else None
        return resolve_stored_file_path(path_value, data_root=data_root)
