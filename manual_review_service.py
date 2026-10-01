from __future__ import annotations

import json
import math
import time
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from annotation_renderer import render_annotated_paper
from answer_region_geometry import answer_regions_with_template_source_sizes
from answer_region_session_lock import get_answer_region_session_lock
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.base import RepositoryError
from path_manager import resolve_stored_file_path
from question_id_contract import (
    QuestionIdContractError,
    canonicalize_question_document,
    resolve_known_question_id,
)
from session_originals import require_original_pages

ANNOTATION_RETRY_MESSAGE = "Annotation rendering failed; retry required."
_ANNOTATED_IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})


class ManualReviewService:
    def __init__(
        self,
        db: GradingRepositoryAccess,
        annotated_dir: Path,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.results = self.db.results
        self.review = self.db.reviews
        self.annotated_dir = annotated_dir

    def ensure_result_annotation(self, result_id: int) -> None:
        context = self.results.get_result_context(result_id)
        if not context:
            return
        with get_answer_region_session_lock(self.annotated_dir / f"session_{context['session_id']}"):
            require_original_pages(self._data_root(), int(context['session_id']))
            annotated = self.review.get_annotated_result(result_id)
            if annotated and all(self._is_cached_annotation(annotated.get(f"annotated_{page}_path")) for page in ("front", "back")):
                return
            details = self.results.get_result_details(result_id)
            self._render_result_annotation_locked(
                result_id, [str(item["question_id"]) for item in details],
            )

    def cleanup_invalidated_annotations(self, rows: list[dict[str, Any]]) -> None:
        paths = {row.get(field) for row in rows
                 for field in ("annotated_front_path", "annotated_back_path")
                 if isinstance(row.get(field), str) and row.get(field)}
        try:
            referenced_paths = self.review.referenced_annotation_paths(paths)
        except (OSError, sqlite3.Error, RepositoryError):
            # The score and invalidation are already committed. Keep old files
            # if cleanup cannot check references; never report a failed save.
            return
        for row in rows:
            self._cleanup_replaced_annotation_files(row, current_paths=set(), referenced_paths=referenced_paths)

    def render_result_annotation(
        self,
        result_id: int,
        highlight_qids: list[str] | None = None,
    ) -> dict[str, str] | None:
        context = self.results.get_result_context(int(result_id))
        if not context:
            return None
        session_id = int(context["session_id"])
        lock_dir = self.annotated_dir / f"session_{session_id}"
        with get_answer_region_session_lock(lock_dir):
            return self._render_result_annotation_locked(result_id, highlight_qids)

    def _render_result_annotation_locked(
        self,
        result_id: int,
        highlight_qids: list[str] | None = None,
    ) -> dict[str, str] | None:
        context = self.results.get_result_context(result_id)
        if not context:
            return None

        session_id = int(context["session_id"])
        require_original_pages(self._data_root(), session_id)
        regions = answer_regions_with_template_source_sizes(
            self.db,
            session_id,
            data_root=self._data_root(),
        )
        details = self.results.get_result_details(result_id)
        max_score_map = self._load_max_score_map(session_id)
        highlighted = {
            resolve_known_question_id(question_id, max_score_map) or str(question_id).strip()
            for question_id in (highlight_qids or [])
            if str(question_id or "").strip()
        }
        question_scores: dict[str, dict[str, Any]] = {}
        source_id_by_question: dict[str, str] = {}

        for detail in details:
            raw_qid = str(detail.get("question_id") or "").strip()
            if not raw_qid:
                continue
            qid = resolve_known_question_id(raw_qid, max_score_map)
            if qid is None:
                # Keep exact historical behavior only when no usable rubric is
                # available.  With a current rubric, unknown identities are
                # excluded instead of being guessed into another question.
                if max_score_map:
                    continue
                qid = raw_qid
            previous_source = source_id_by_question.get(qid)
            if previous_source is not None and previous_source != raw_qid:
                raise QuestionIdContractError(
                    f"多个历史题号同时对应当前题号 {qid}: "
                    f"{previous_source!r}, {raw_qid!r}"
                )
            source_id_by_question[qid] = raw_qid

            if highlight_qids is not None:
                if qid not in highlighted:
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

        for qid, item in question_scores.items():
            if qid in max_score_map:
                item["max_score"] = max_score_map[qid]
            else:
                item["max_score"] = float(item.get("score_awarded") or 0)

        front_image = self._resolve_stored_file_path(context["front_image"])
        back_image = self._resolve_stored_file_path(context["back_image"])
        render_id = uuid4().hex
        output_dir = self.annotated_dir / f"session_{session_id}"
        front_out = output_dir / f"result_{result_id}_{render_id}_front_annotated.jpg"
        back_out = output_dir / f"result_{result_id}_{render_id}_back_annotated.jpg"

        filtered_regions: list[dict[str, Any]] = []
        for source_region in regions:
            raw_region_id = str(
                source_region.get("mapped_question_id")
                or source_region.get("detected_question_id")
                or ""
            ).strip()
            canonical_region_id = resolve_known_question_id(
                raw_region_id,
                max_score_map,
            )
            if canonical_region_id is None:
                if max_score_map:
                    continue
                canonical_region_id = raw_region_id
            if canonical_region_id not in question_scores:
                continue
            region = dict(source_region)
            region["mapped_question_id"] = canonical_region_id
            detected = str(region.get("detected_question_id") or "").strip()
            region["detected_question_id"] = (
                resolve_known_question_id(detected, max_score_map)
                if detected
                else None
            ) or canonical_region_id
            filtered_regions.append(region)

        try:
            front_path, back_path = render_annotated_paper(
                front_image=front_image,
                back_image=back_image,
                regions=filtered_regions,
                question_scores=question_scores,
                output_front=front_out,
                output_back=back_out,
            )
            previous = self.review.upsert_annotated_result(
                session_id,
                result_id,
                str(front_path),
                str(back_path),
            )
        except Exception:
            for output_path in (front_out, back_out):
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        self._cleanup_replaced_annotation_files(
            previous,
            current_paths={str(front_path), str(back_path)},
        )
        self._trim_annotation_cache(keep={Path(front_path), Path(back_path)})
        return {"front": str(front_path), "back": str(back_path)}

    def _is_cached_annotation(self, value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            path = Path(value).resolve()
            path.relative_to(self.annotated_dir.resolve())
            return path.is_file()
        except (OSError, ValueError):
            return False

    def _trim_annotation_cache(self, max_bytes: int = 256 * 1024 * 1024, *, keep: set[Path] | None = None) -> None:
        from session_originals import _files
        entries = []
        for path in _files(self.annotated_dir, self.annotated_dir):
            if path.suffix.lower() == ".jpg" and path.parent.name.startswith("session_"):
                try:
                    entries.append((path.stat(), path))
                except OSError:
                    pass
        total = sum(stat.st_size for stat, _ in entries)
        keep = {path.resolve() for path in (keep or set())}
        for stat, path in sorted(entries, key=lambda item: item[0].st_mtime):
            if total <= max_bytes:
                break
            if path in keep or stat.st_mtime > time.time() - 600:
                continue
            try:
                path.unlink()
                total -= stat.st_size
            except OSError:
                pass

    def _cleanup_replaced_annotation_files(
        self,
        previous: dict[str, Any] | None,
        *,
        current_paths: set[str],
        referenced_paths: set[str] | None = None,
    ) -> None:
        if not previous:
            return
        try:
            annotated_root = self.annotated_dir.resolve(strict=False)
        except OSError:
            return
        for field in ("annotated_front_path", "annotated_back_path"):
            value = previous.get(field)
            if not isinstance(value, str) or not value or value in current_paths:
                continue
            try:
                candidate = Path(value)
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(annotated_root)
            except (OSError, ValueError):
                continue
            if candidate.suffix.lower() not in _ANNOTATED_IMAGE_SUFFIXES:
                continue
            if (value in referenced_paths if referenced_paths is not None
                    else self.review.is_annotated_result_path_referenced(value)):
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

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
            self.review.update_result_detail(
                detail_id=int(detail_id),
                score_awarded=float(row.get("score_awarded", 0)),
                deduction_reason=sanitize_val(row.get("deduction_reason")),
                error_category=sanitize_val(row.get("error_category")),
                error_summary=sanitize_val(row.get("error_summary")),
            )
            changed += 1

        self.review.recalculate_result_score(result_id)
        paths = self.render_result_annotation(result_id, highlight_qids=highlight_qids)
        return {"updated_details": changed, "annotated_paths": paths}

    def apply_review_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
        highlight_qids: list[str] | None = None,
        *,
        defer_annotations: bool = False,
    ) -> dict[str, Any]:
        result = self.review.apply_session_review_adjustments(
            session_id,
            adjustments,
            **({"defer_annotations": True} if defer_annotations else {}),
        )
        result_ids = sorted({int(item["result_id"]) for item in adjustments})
        if defer_annotations:
            self.cleanup_invalidated_annotations(result.pop("invalidated_annotations", []))
            return {**result, "annotation_outcomes": [
                {"result_id": result_id, "status": "on_demand"} for result_id in result_ids
            ]}
        annotation_outcomes: list[dict[str, Any]] = []
        for result_id in result_ids:
            try:
                rendered = self.render_result_annotation(
                    result_id,
                    highlight_qids=highlight_qids,
                )
            except Exception:
                rendered = None
            if rendered is None:
                annotation_outcomes.append(
                    {
                        "result_id": result_id,
                        "status": "retry_required",
                        "message": ANNOTATION_RETRY_MESSAGE,
                    }
                )
            else:
                annotation_outcomes.append(
                    {"result_id": result_id, "status": "succeeded"}
                )
        return {**result, "annotation_outcomes": annotation_outcomes}

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
