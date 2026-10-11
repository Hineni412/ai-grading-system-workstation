from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from path_manager import resolve_stored_file_path
from backend.question_id_contract import (
    QuestionIdContractError,
    canonicalize_question_document,
    resolve_known_question_id,
)


class ManualReviewService:
    def __init__(
        self,
        db: GradingRepositoryAccess,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.results = self.db.results
        self.review = self.db.reviews

    def apply_manual_adjustments(self, result_id: int, adjustments: list[dict[str, Any]]) -> dict[str, Any]:
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
            self.review.update_result_detail(
                detail_id=int(detail_id),
                score_awarded=float(row.get("score_awarded", 0)),
                deduction_reason=sanitize_val(row.get("deduction_reason")),
                error_category=sanitize_val(row.get("error_category")),
                error_summary=sanitize_val(row.get("error_summary")),
            )
            changed += 1

        self.review.recalculate_result_score(result_id)
        return {"updated_details": changed}

    def apply_review_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.review.apply_session_review_adjustments(
            session_id,
            adjustments,
        )

    def apply_batch_score_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not adjustments:
            return {"updated_details": 0, "updated_results": 0}

        score_map = self._load_max_score_map(session_id)
        results = self.results.get_session_results(session_id)
        details_by_result: dict[int, list[dict[str, Any]]] = {}
        detail_lookup: dict[int, dict[str, Any]] = {}
        for result in results:
            result_id = int(result["result_id"])
            details = self.results.get_result_details(result_id)
            details_by_result[result_id] = details
            for detail in details:
                detail_lookup[int(detail["detail_id"])] = {**detail, "result_id": result_id}

        proposed_scores: dict[int, float] = {}
        affected_results: set[int] = set()
        affected_buckets_by_result: dict[int, set[str]] = {}
        normalized_adjustments: list[dict[str, Any]] = []
        seen_detail_ids: set[int] = set()
        for adjustment in adjustments:
            detail_id = int(adjustment.get("detail_id") or 0)
            if detail_id in seen_detail_ids:
                raise ValueError("同一评分明细不能重复调整。")
            seen_detail_ids.add(detail_id)

            detail = detail_lookup.get(detail_id)
            if detail is None:
                raise ValueError(f"评分明细 {detail_id} 不属于当前考试。")

            score = float(adjustment.get("score_awarded"))
            if not math.isfinite(score) or score < 0:
                raise ValueError(f"{detail.get('question_id')} 的得分必须是非负有限数字。")
            if not score.is_integer():
                raise ValueError(f"{detail.get('question_id')} 的得分必须是整数。")
            score = float(int(score))

            result_id = int(detail["result_id"])
            qid = str(detail.get("question_id") or "").strip()
            bucket_id = _score_bucket_id(qid, score_map)
            if bucket_id is None:
                raise ValueError(f"{qid} 未找到对应满分，无法安全调整。")

            proposed_scores[detail_id] = score
            affected_results.add(result_id)
            affected_buckets_by_result.setdefault(result_id, set()).add(bucket_id)
            normalized_adjustments.append({"detail_id": detail_id, "score_awarded": score})

        for result_id in affected_results:
            bucket_scores: dict[str, float] = {}
            for detail in details_by_result[result_id]:
                detail_id = int(detail["detail_id"])
                qid = str(detail.get("question_id") or "").strip()
                bucket_id = _score_bucket_id(qid, score_map)
                if bucket_id not in affected_buckets_by_result[result_id]:
                    continue
                score = proposed_scores.get(detail_id, float(detail.get("score_awarded") or 0))
                bucket_scores[bucket_id] = bucket_scores.get(bucket_id, 0.0) + score

            for bucket_id, score_sum in bucket_scores.items():
                max_score = float(score_map[bucket_id])
                if score_sum > max_score + 1e-6:
                    raise ValueError(f"{bucket_id} 得分 {score_sum:g} 超过满分 {max_score:g}。")

        self.db.create_backup("manual_score_adjustment")
        return self.review.update_session_detail_scores(
            session_id,
            normalized_adjustments,
        )

    def _load_max_score_map(self, session_id: int) -> dict[str, float]:
        session = self.db.sessions.get_grading_session(session_id)
        if not session:
            return {}

        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            return {}

        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
            if not isinstance(rubric, dict):
                return {}
            rubric = canonicalize_question_document(rubric)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            QuestionIdContractError,
        ):
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
                except (TypeError, ValueError, OverflowError):
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
                        if pid != qid:
                            result[pid] = float(part.get("part_score", 0))
                    except (TypeError, ValueError, OverflowError):
                        continue

        return result

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        return resolve_stored_file_path(path_value, data_root=self._data_root())

    def _data_root(self) -> Path | None:
        return self.db.db_path.parent.parent if self.db.db_path.parent.name == "databases" else None


def _score_bucket_id(question_id: str, score_map: dict[str, float]) -> str | None:
    return resolve_known_question_id(question_id, score_map)
