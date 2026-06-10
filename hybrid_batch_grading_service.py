from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ai_grader import GradingResult, QuestionGradingDetail
from objective_batch_recognition_service import OBJECTIVE_AUTO_SCORE_MIN_CONFIDENCE, run_objective_batch_recognition
from scoring_prompt_rules import SHARED_GRADING_RULES
from solution_answer_guard import apply_solution_substance_rules, extract_observed_text, rubric_question_meta
from scanner import ExamPaperGroup
from usage_logger import extract_usage_fields
from session_manager import _canonical_question_id
OBJECTIVE_TYPES = {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}


@dataclass(frozen=True)
class PaperEntry:
    paper_key: str
    student_id: int | None
    student_name: str
    group: ExamPaperGroup


@dataclass(frozen=True)
class MajorQuestionSpec:
    question_id: str
    detail_question_ids: list[str]
    rubric: dict[str, Any]
    answer_key: dict[str, Any]
    max_score: float


@dataclass(frozen=True)
class HybridBatchRunResult:
    paper_entries: list[PaperEntry]
    results_by_paper_key: dict[str, GradingResult]
    fallback_items: list[dict[str, Any]]
    usage_records: list[dict[str, Any]]
    usage_summary: dict[str, int]


def run_hybrid_batch_grading(
    *,
    session_id: int | str,
    paper_groups: list[ExamPaperGroup],
    answer_regions: list[dict[str, Any]],
    rubric: dict[str, Any],
    answer_key: dict[str, Any],
    llm_client: Any,
    grading_model: str | None,
    output_root: Path,
    batch_size: int = 4,
    objective_batch_size: int = 15,
    min_confidence: float = 80.0,
    include_objective_local: bool = True,
    progress_callback: Any | None = None,
    batch_workers: int = 1,
    rate_limiter: Any | None = None,
    rubric_images_dir: Path | None = None,
) -> HybridBatchRunResult:
    entries = build_paper_entries(paper_groups)
    specs = build_major_question_specs(rubric, answer_key)
    details_by_key: dict[str, list[QuestionGradingDetail]] = {entry.paper_key: [] for entry in entries}
    metadata_by_key: dict[str, list[dict[str, Any]]] = {entry.paper_key: [] for entry in entries}
    fallback_items: list[dict[str, Any]] = []
    usage_records: list[dict[str, Any]] = []

    if include_objective_local:
        objective_run = run_objective_batch_recognition(
            session_id=session_id,
            paper_groups=paper_groups,
            answer_regions=answer_regions,
            rubric=rubric,
            answer_key=answer_key,
            output_root=output_root / "objective_batch",
            batch_size=objective_batch_size,
            min_confidence=OBJECTIVE_AUTO_SCORE_MIN_CONFIDENCE,
            fallback_recognition_client=llm_client,
            fallback_model=grading_model,
            progress_callback=progress_callback,
            batch_workers=batch_workers,
            rate_limiter=rate_limiter,
        )
        for paper_key, details in objective_run.details_by_paper_key.items():
            details_by_key.setdefault(paper_key, []).extend(details)
        for paper_key, metadata in objective_run.metadata_by_paper_key.items():
            metadata_by_key.setdefault(paper_key, []).extend(metadata)
        usage_records.extend(objective_run.usage_records)

    builder = MajorQuestionAtlasBuilder(output_root=output_root)
    major_tasks: list[tuple[MajorQuestionSpec, int, list[PaperEntry]]] = []
    for spec in specs:
        for batch_index, batch_entries in enumerate(_chunk(entries, batch_size), start=1):
            major_tasks.append((spec, batch_index, batch_entries))
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "major_batch_summary",
                "total_questions": len(specs),
                "total_batches": len(major_tasks),
                "total_papers": len(entries),
            }
        )

    def _run_major_task(task: tuple[MajorQuestionSpec, int, list[PaperEntry]]) -> dict[str, Any]:
        spec, batch_index, batch_entries = task
        try:
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "major_batch_start",
                        "question_id": spec.question_id,
                        "batch_index": batch_index,
                        "item_count": len(batch_entries),
                    }
                )
            if rate_limiter is not None:
                rate_limiter.acquire()
            try:
                result = grade_major_question_batch(
                    session_id=session_id,
                    spec=spec,
                    paper_entries=batch_entries,
                    answer_regions=answer_regions,
                    llm_client=llm_client,
                    grading_model=grading_model,
                    output_root=output_root,
                    batch_index=batch_index,
                    min_confidence=min_confidence,
                    builder=builder,
                    rubric_images_dir=rubric_images_dir,
                )
            except Exception as exc:  # noqa: BLE001
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "major_batch_error",
                            "question_id": spec.question_id,
                            "batch_index": batch_index,
                            "item_count": len(batch_entries),
                            "error": str(exc) or "hybrid_major_batch_failed",
                        }
                    )
                return {
                    "usage": None,
                    "accepted": [],
                    "failed": [
                        {
                            "paper_key": entry.paper_key,
                            "student_id": entry.student_id,
                            "question_id": spec.question_id,
                            "reason": str(exc) or "hybrid_major_batch_failed",
                        }
                        for entry in batch_entries
                    ],
                }
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "major_batch_done",
                        "question_id": spec.question_id,
                        "batch_index": batch_index,
                        "item_count": len(batch_entries),
                        "accepted_count": len(result.get("accepted", [])),
                        "failed_count": len(result.get("failed", [])),
                    }
                )
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "usage": None,
                "accepted": [],
                "failed": [
                    {
                        "paper_key": entry.paper_key,
                        "student_id": entry.student_id,
                        "question_id": spec.question_id,
                        "reason": str(exc) or "hybrid_major_batch_failed",
                    }
                    for entry in batch_entries
                ],
            }

    worker_count = max(1, min(int(batch_workers or 1), len(major_tasks) or 1))
    if worker_count == 1:
        major_results = [_run_major_task(task) for task in major_tasks]
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hybrid-major") as executor:
            futures = [executor.submit(_run_major_task, task) for task in major_tasks]
            major_results = [future.result() for future in as_completed(futures)]

    for result in major_results:
        if result.get("usage"):
            usage_records.append(result["usage"])
        for item in result.get("accepted", []):
            paper_key = str(item["paper_key"])
            details_by_key.setdefault(paper_key, []).extend(item["details"])
            metadata_by_key.setdefault(paper_key, []).extend(item.get("metadata", []))
        fallback_items.extend(result.get("failed", []))

    total_score = _float_value(rubric.get("total_score"), 100.0)
    entry_by_key = {entry.paper_key: entry for entry in entries}
    results = {
        paper_key: _build_result(
            student_name=entry_by_key[paper_key].student_name,
            total_score=total_score,
            details=details,
            metadata=metadata_by_key.get(paper_key, []),
            paper_key=paper_key,
        )
        for paper_key, details in details_by_key.items()
    }
    return HybridBatchRunResult(
        paper_entries=entries,
        results_by_paper_key=results,
        fallback_items=fallback_items,
        usage_records=usage_records,
        usage_summary=summarize_usage_records(usage_records),
    )


def build_paper_entries(paper_groups: list[ExamPaperGroup]) -> list[PaperEntry]:
    entries: list[PaperEntry] = []
    for index, group in enumerate(paper_groups, start=1):
        student_part = group.student_id if group.student_id is not None else "unknown"
        source_part = _safe_path_part(group.source_label or group.front_image.name)
        paper_key = f"paper_{index:03d}_student_{student_part}_{source_part}"
        entries.append(
            PaperEntry(
                paper_key=paper_key,
                student_id=group.student_id,
                student_name=group.student_name,
                group=group,
            )
        )
    return entries


def build_major_question_specs(rubric: dict[str, Any], answer_key: dict[str, Any]) -> list[MajorQuestionSpec]:
    result: list[MajorQuestionSpec] = []
    answer_questions = {
        str(item.get("question_id") or "").strip(): item
        for item in answer_key.get("questions", [])
        if isinstance(item, dict)
    } if isinstance(answer_key, dict) else {}
    for question in rubric.get("questions", []) if isinstance(rubric, dict) else []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if not qid:
            continue
        qtype = str(question.get("question_type") or "").strip()
        if qtype in OBJECTIVE_TYPES:
            continue
        detail_ids = _detail_question_ids(question)
        result.append(
            MajorQuestionSpec(
                question_id=qid,
                detail_question_ids=detail_ids,
                rubric=dict(question),
                answer_key=dict(answer_questions.get(qid) or {}),
                max_score=_question_max_score(question),
            )
        )
    return result


class MajorQuestionAtlasBuilder:
    def __init__(self, output_root: Path, crop_padding: int = 8, max_width: int = 1500, jpeg_quality: int = 88) -> None:
        self.output_root = Path(output_root)
        self.crop_padding = max(0, int(crop_padding))
        self.max_width = max(320, int(max_width))
        self.jpeg_quality = max(40, min(95, int(jpeg_quality)))

    def build(
        self,
        *,
        session_id: int | str,
        spec: MajorQuestionSpec,
        paper_entries: list[PaperEntry],
        answer_regions: list[dict[str, Any]],
        batch_index: int,
    ) -> dict[str, Any]:
        # Resolve sub-regions: one region per part_id, matched by position/prefix
        sub_regions = _major_sub_regions(spec, answer_regions)
        # sub_regions = [(part_id, region_bbox), ...]

        output_dir = self.output_root / f"session_{session_id}" / f"major_{_safe_path_part(spec.question_id)}"
        output_dir.mkdir(parents=True, exist_ok=True)
        atlas_path = output_dir / f"batch_{int(batch_index)}.jpg"
        manifest_path = output_dir / f"batch_{int(batch_index)}_manifest.json"

        tile_images: list[Image.Image] = []
        tile_labels: list[str] = []
        items: list[dict[str, Any]] = []

        # Build one tile per (student, sub-question), grouped by student
        try:
            for item_index, entry in enumerate(paper_entries, start=1):
                student_sub_items: list[dict[str, Any]] = []
                for part_id, region in sub_regions:
                    page = str(region["page"])
                    source_path = _source_image_path(entry.group, page)
                    crop, bbox = _crop_region(source_path, region, self.crop_padding)
                    tile_images.append(crop)
                    tile_label = f"{item_index:02d}. [{entry.student_name}] 小问: {part_id}"
                    tile_labels.append(tile_label)
                    student_sub_items.append(
                        {
                            "part_id": part_id,
                            "tile_label": tile_label,
                            "page": page,
                            "bbox": bbox,
                        }
                    )
                items.append(
                    {
                        "paper_key": entry.paper_key,
                        "student_id": entry.student_id,
                        "student_name": entry.student_name,
                        "question_id": spec.question_id,
                        "detail_question_ids": spec.detail_question_ids,
                        "batch_index": int(batch_index),
                        "item_index": item_index,
                        "sub_items": student_sub_items,
                    }
                )
            _save_atlas_with_labels(tile_labels, tile_images, atlas_path, self.max_width, self.jpeg_quality)
        finally:
            for image in tile_images:
                image.close()

        manifest = {
            "schema_version": 2,
            "mode": "hybrid_major_batch",
            "session_id": session_id,
            "question_id": spec.question_id,
            "detail_question_ids": spec.detail_question_ids,
            "batch_index": int(batch_index),
            "atlas_path": str(atlas_path),
            "items": items,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"atlas_path": atlas_path, "manifest_path": manifest_path, "manifest": manifest}


def grade_major_question_batch(
    *,
    session_id: int | str,
    spec: MajorQuestionSpec,
    paper_entries: list[PaperEntry],
    answer_regions: list[dict[str, Any]],
    llm_client: Any,
    grading_model: str | None,
    output_root: Path,
    batch_index: int,
    min_confidence: float = 80.0,
    builder: MajorQuestionAtlasBuilder | None = None,
    rubric_images_dir: Path | None = None,
) -> dict[str, Any]:
    atlas_builder = builder or MajorQuestionAtlasBuilder(output_root)
    atlas = atlas_builder.build(
        session_id=session_id,
        spec=spec,
        paper_entries=paper_entries,
        answer_regions=answer_regions,
        batch_index=batch_index,
    )
    
    rubric_image_bytes = None
    
    b64 = spec.answer_key.get("answer_image_base64")
    if b64:
        import base64
        try:
            rubric_image_bytes = base64.b64decode(b64)
        except Exception:
            pass

    if not rubric_image_bytes and rubric_images_dir and rubric_images_dir.exists():
        for ext in [".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG"]:
            img_path = rubric_images_dir / f"{spec.question_id}{ext}"
            if img_path.exists():
                with img_path.open("rb") as f:
                    rubric_image_bytes = f.read()
                break
                
    prompt = build_hybrid_major_prompt(spec, atlas["manifest"], has_rubric_image=bool(rubric_image_bytes))
    usage: dict[str, Any] = {}

    def _usage_callback(completion: Any, kwargs: dict[str, Any] | None = None) -> None:
        usage.update(extract_usage_fields(completion))
        usage["model"] = (kwargs or {}).get("model") or grading_model
        usage["image_count"] = 2 if rubric_image_bytes else 1
        usage["effective_uncached_tokens"] = (usage.get("total_tokens") or 0) - (usage.get("cached_tokens") or 0)

    with Path(atlas["atlas_path"]).open("rb") as image_file:
        image_bytes = image_file.read()
        
    images_to_send = [rubric_image_bytes, image_bytes] if rubric_image_bytes else [image_bytes]
    
    json_from_images = getattr(llm_client, "json_from_images_with_options", None)
    
    max_retries = 3
    last_err = None
    import time
    for attempt in range(max_retries):
        try:
            if callable(json_from_images):
                response = json_from_images(
                    prompt,
                    images_to_send,
                    model=grading_model,
                    usage_callback=_usage_callback,
                    extra_kwargs={"omit_token_limit": True, "timeout": None},
                )
            else:
                response = llm_client.json_from_images(prompt, images_to_send, model=grading_model, usage_callback=_usage_callback)
            break
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    else:
        raise Exception(f"Major batch failed after {max_retries} retries: {last_err}")
    accepted, failed = validate_hybrid_major_response(response, atlas["manifest"], spec, min_confidence=min_confidence)
    usage["question_id"] = spec.question_id
    usage["batch_index"] = int(batch_index)
    return {"accepted": accepted, "failed": failed, "usage": usage}


def build_hybrid_major_prompt(spec: MajorQuestionSpec, manifest: dict[str, Any], has_rubric_image: bool = False) -> str:
    payload = {
        "question_id": spec.question_id,
        "detail_question_ids": spec.detail_question_ids,
        "rubric": spec.rubric,
        "answer_key": spec.answer_key,
    }

    # Build a schema grading_details that contains ONE entry per sub-question (part_id),
    # so the model knows it must return a separate score for each part.
    detail_ids = spec.detail_question_ids if spec.detail_question_ids else [spec.question_id]
    schema_grading_details = [
        {
            "question_id": sub_qid,
            "score_awarded": 0,
            "deduction_reason": "",
            "knowledge_id": "UNKNOWN",
            "knowledge_ids": [],
            "confidence_score": 90,
            "needs_human_review": False,
            "error_category": None,
            "error_summary": None,
            "answer_discarded_by_smudge": False,
            "answer_is_blank_or_no_valid_work": False,
            "observed_answer": "",
            "evidence_steps": [],
            "missing_steps": [],
            "alternative_solution_detected": False,
            "alternative_solution_summary": None,
            "candidate_scores": [
                {"score": 0, "confidence": 0.0, "reason": ""}
            ],
        }
        for sub_qid in detail_ids
    ]

    schema = {
        "question_id": spec.question_id,
        "items": [
            {
                "paper_key": "paper_001_student_1_sample",
                "student_id": 1,
                "grading_details": schema_grading_details,
            }
        ],
    }

    image_instruction = (
        f"Grade question {spec.question_id} for every student in the atlas. "
        f"Return one grading_details entry per sub-question. "
        f"Required sub-question IDs: {detail_ids}."
    )
    if has_rubric_image:
        image_instruction = (
            "CRITICAL: I have provided the PERFECT STANDARD ANSWER IMAGE as the FIRST IMAGE. "
            "You MUST use this FIRST IMAGE as the ultimate source of truth for formulas, geometry, and steps, ignoring any garbled text in the JSON rubric. "
            "The SECOND IMAGE is the atlas containing the students' answers to grade. "
            f"Return one grading_details entry per sub-question. Required sub-question IDs: {detail_ids}."
        )

    # Build a TILE → SUBQUESTION mapping table from the manifest
    # Each item now has sub_items: [{part_id, tile_label, ...}, ...]
    tile_map_lines: list[str] = []
    for item in manifest.get("items", []):
        pk = item.get("paper_key", "?")
        name = item.get("student_name", "?")
        for si in item.get("sub_items", []):
            tile_map_lines.append(
                f"  Tile '{si['tile_label']}' → paper_key={pk!r} (学生:{name}), 小问 question_id={si['part_id']!r}"
            )
    tile_map_block = "TILE_TO_SUBQUESTION_MAP:\n" + "\n".join(tile_map_lines) if tile_map_lines else ""

    return "\n".join(
        filter(None, [
            "You are grading one major question across up to 4 students.",
            SHARED_GRADING_RULES,
            image_instruction,
            tile_map_block,
            "CRITICAL: Each tile in the atlas image is labeled with the student name and the exact sub-question (小问) it covers.",
            "You MUST match each tile label to the tile_to_subquestion_map above and return the correct question_id for each grading_detail.",
            "Use paper_key as the primary identifier. student_id is not unique.",
            "Hard rule: smudged/crossed-out/deletion-line/X-marked answer content is discarded and must not be read or scored.",
            "If only discarded content exists for a subquestion, score it 0 and set answer_discarded_by_smudge=true and error_category=作废答案.",
            "For proof or reasoning questions, full proof/process credit requires explicit evidence steps in the student's visible, non-discarded work.",
            "Before scoring a proof, extract evidence_steps from the answer, then list missing_steps from the rubric/key proof chain.",
            "Award each step score only when its corresponding evidence step is actually present; do not infer missing proof from the final conclusion.",
            "Only a final conclusion, a diagram label, or fragmented equations cannot receive complete process/proof credit.",
            "If a student only copies the sub-question stem (e.g. '(3)是不是定值') or only adds a tick/check without proof work, score 0 or answer_only_max_score; never award full credit for stem echo plus a checkmark.",
            "Each grading_detail must include observed_answer with the student's visible non-discarded text.",
            "If another valid method appears, set alternative_solution_detected=true, summarize the equivalent proof chain, and grade it with the same strict evidence-step standard.",
            "For blank, mostly blank, crossed-out-only, or no-valid-work answers, score conservatively and mark needs_human_review when uncertainty remains.",
            "When confidence is low or multiple scores are plausible, include 2-3 candidate_scores with score, confidence, and reason.",
            "Return strict JSON only.",
            "QUESTION_PAYLOAD_JSON:",
            _stable_json(payload),
            "RESPONSE_SCHEMA_JSON:",
            _stable_json(schema),
            "BATCH_MANIFEST_JSON:",
            _stable_json(manifest),
        ])
    )


def validate_hybrid_major_response(
    response: dict[str, Any],
    manifest: dict[str, Any],
    spec: MajorQuestionSpec,
    *,
    min_confidence: float = 80.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if str(response.get("question_id") or "").strip() != spec.question_id:
        return [], [_failed_manifest_item(item, "question_id_mismatch", spec.question_id) for item in manifest.get("items", [])]
    expected = {str(item.get("paper_key")): item for item in manifest.get("items", [])}
    # Allow both the sub-question IDs (part_ids) AND the parent question_id itself,
    # so that if the model returns the parent Q-ID it is not immediately rejected.
    allowed_qids = set(spec.detail_question_ids or [spec.question_id]) | {spec.question_id}
    accepted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in response.get("items", []):
        if not isinstance(item, dict):
            continue
        paper_key = str(item.get("paper_key") or "").strip()
        if paper_key not in expected:
            failed.append({"paper_key": paper_key, "student_id": item.get("student_id"), "question_id": spec.question_id, "reason": "unknown_paper_key"})
            continue
        if paper_key in seen:
            failed.append({"paper_key": paper_key, "student_id": expected[paper_key].get("student_id"), "question_id": spec.question_id, "reason": "duplicate_paper_key"})
            continue
        seen.add(paper_key)
        details = []
        metadata = []
        item_failed_reason = ""
        for detail in item.get("grading_details", []):
            converted, reason, detail_metadata = _detail_from_ai_item(detail, allowed_qids, min_confidence, spec=spec)
            if reason:
                item_failed_reason = reason
                break
            details.append(converted)
            if detail_metadata:
                metadata.append(detail_metadata)
        if item_failed_reason or not details:
            failed.append({"paper_key": paper_key, "student_id": expected[paper_key].get("student_id"), "question_id": spec.question_id, "reason": item_failed_reason or "missing_grading_details"})
            continue
        accepted.append({"paper_key": paper_key, "student_id": expected[paper_key].get("student_id"), "details": details, "metadata": metadata})
    for paper_key, item in expected.items():
        if paper_key not in seen:
            failed.append({"paper_key": paper_key, "student_id": item.get("student_id"), "question_id": spec.question_id, "reason": "missing_paper_result"})
    return accepted, failed


def _append_objective_local_details(
    *,
    session_id: int | str,
    entries: list[PaperEntry],
    rubric: dict[str, Any],
    details_by_key: dict[str, list[QuestionGradingDetail]],
    escalation_items: list[dict[str, Any]],
) -> None:
    try:
        from objective_shadow_service import run_objective_shadow_grading
    except Exception:
        return
    shadow_config = {"enabled": True, "question_types": ["choice", "fill_blank"], "sample_limit": 0, "write_report": False}
    for entry in entries:
        shadow = run_objective_shadow_grading(
            session_id=str(session_id),
            paper_group=entry.group,
            main_result={"grading_details": []},
            rubric=rubric,
            template_config={},
            shadow_config=shadow_config,
        )
        for item in shadow.get("items", []):
            qid = str(item.get("question_id") or "")
            if item.get("objective_auto_scored") and not item.get("objective_need_review") and item.get("objective_score") is not None:
                details_by_key[entry.paper_key].append(
                    QuestionGradingDetail(
                        question_id=qid,
                        score_awarded=float(item.get("objective_score") or 0),
                        deduction_reason=item.get("objective_review_reason") or None,
                        knowledge_id="OBJECTIVE",
                        error_category=None,
                        error_summary=None,
                        confidence_score=_confidence_0_to_100(item.get("confidence", 100)),
                        knowledge_ids=["OBJECTIVE"],
                    )
                )
            else:
                escalation_items.append(
                    {
                        "paper_key": entry.paper_key,
                        "student_id": entry.student_id,
                        "question_id": qid,
                        "reason": item.get("objective_review_reason") or "objective_low_confidence",
                    }
                )


def _detail_from_ai_item(
    detail: dict[str, Any],
    allowed_qids: set[str],
    min_confidence: float,
    *,
    spec: MajorQuestionSpec | None = None,
) -> tuple[QuestionGradingDetail | None, str, dict[str, Any] | None]:
    if not isinstance(detail, dict):
        return None, "invalid_detail", None
    qid = str(detail.get("question_id") or "").strip()
    if qid not in allowed_qids:
        return None, "unexpected_detail_question_id", None
    score = _float_value(detail.get("score_awarded"), None)
    if score is None or score < 0:
        return None, "invalid_score", None
    confidence = _float_value(detail.get("confidence_score"), 100.0)
    if _truthy(detail.get("answer_discarded_by_smudge")) and score > 0:
        return None, "discarded_answer_scored", None
    blank_or_no_work = _truthy(detail.get("answer_is_blank_or_no_valid_work"))
    if blank_or_no_work:
        score = 0.0
        confidence = 100.0
    knowledge_ids = detail.get("knowledge_ids")
    if not isinstance(knowledge_ids, list) or not knowledge_ids:
        knowledge_ids = [detail.get("knowledge_id") or "UNKNOWN"]
    normalized = [str(value) for value in knowledge_ids]
    needs_review = blank_or_no_work or confidence < min_confidence or _truthy(detail.get("needs_human_review"))
    error_category = detail.get("error_category")
    error_summary = detail.get("error_summary")
    deduction_reason = detail.get("deduction_reason")
    if blank_or_no_work:
        error_category = error_category or "未作答"
        error_summary = error_summary or "blank_or_no_valid_work"
        deduction_reason = deduction_reason or "未见有效作答或有效证明，自动 0 分。"
    elif needs_review:
        error_category = error_category or "需复核"
        error_summary = error_summary or ("low_confidence" if confidence < min_confidence else "needs_human_review")
        if not deduction_reason:
            deduction_reason = "需复核: 模型置信度不足或存在多种可能评分"
    if spec is not None and not blank_or_no_work:
        question_type, full_score, answer_only_max = rubric_question_meta(spec.rubric, qid)
        adjusted_score, substance_category, substance_summary, substance_reason = apply_solution_substance_rules(
            observed_answer=extract_observed_text(detail),
            question_type=question_type,
            full_score=float(full_score or 0),
            answer_only_max_score=answer_only_max,
            current_score=float(score),
        )
        if adjusted_score < float(score) - 1e-6:
            score = adjusted_score
            if substance_category:
                error_category = substance_category
                error_summary = substance_summary
                deduction_reason = substance_reason
                needs_review = False
                confidence = 100.0
    detail_metadata = _subjective_detail_metadata(detail, qid)
    return (
        QuestionGradingDetail(
            question_id=qid,
            score_awarded=score,
            deduction_reason=deduction_reason,
            knowledge_id=normalized[0],
            error_category=error_category,
            error_summary=error_summary,
            confidence_score=confidence,
            knowledge_ids=normalized,
        ),
        "",
        detail_metadata,
    )


def _subjective_detail_metadata(detail: dict[str, Any], qid: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"question_id": qid}
    for key in (
        "evidence_steps",
        "missing_steps",
        "candidate_scores",
    ):
        value = detail.get(key)
        if isinstance(value, list):
            metadata[key] = value
        else:
            metadata[key] = []
    metadata["alternative_solution_detected"] = bool(_truthy(detail.get("alternative_solution_detected")))
    metadata["alternative_solution_summary"] = detail.get("alternative_solution_summary")
    metadata["answer_is_blank_or_no_valid_work"] = bool(_truthy(detail.get("answer_is_blank_or_no_valid_work")))
    metadata["answer_discarded_by_smudge"] = bool(_truthy(detail.get("answer_discarded_by_smudge")))
    return metadata


def _objective_escalation_batches(
    escalation_items: list[dict[str, Any]],
    entries: list[PaperEntry],
    rubric: dict[str, Any],
    answer_key: dict[str, Any],
) -> list[tuple[MajorQuestionSpec, list[PaperEntry]]]:
    if not escalation_items:
        return []
    entry_by_key = {entry.paper_key: entry for entry in entries}
    rubric_questions = _questions_by_id(rubric)
    answer_questions = _questions_by_id(answer_key)
    grouped: dict[str, list[PaperEntry]] = {}
    seen: set[tuple[str, str]] = set()
    for item in escalation_items:
        qid = str(item.get("question_id") or "").strip()
        paper_key = str(item.get("paper_key") or "").strip()
        if not qid or paper_key not in entry_by_key or (qid, paper_key) in seen:
            continue
        seen.add((qid, paper_key))
        grouped.setdefault(qid, []).append(entry_by_key[paper_key])

    result: list[tuple[MajorQuestionSpec, list[PaperEntry]]] = []
    for qid in sorted(grouped, key=_question_sort_key):
        question = dict(rubric_questions.get(qid) or {"question_id": qid, "question_type": "objective"})
        result.append(
            (
                MajorQuestionSpec(
                    question_id=qid,
                    detail_question_ids=[qid],
                    rubric=question,
                    answer_key=dict(answer_questions.get(qid) or {}),
                    max_score=_question_max_score(question),
                ),
                grouped[qid],
            )
        )
    return result


def _questions_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return result
    for question in payload.get("questions", []):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if qid:
            result[qid] = question
        for part in question.get("parts", []) if isinstance(question.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or part.get("question_id") or "").strip()
            if part_id:
                result[part_id] = part
    return result


def _build_result(
    student_name: str,
    total_score: float,
    details: list[QuestionGradingDetail],
    metadata: list[dict[str, Any]],
    paper_key: str,
) -> GradingResult:
    metadata_by_qid = {
        str(item.get("question_id")): item
        for item in metadata
        if isinstance(item, dict) and item.get("question_id") is not None
    }
    return GradingResult(
        student_name=student_name,
        total_score=total_score,
        student_score=sum(detail.score_awarded for detail in details),
        needs_human_review=any(
            (detail.confidence_score is not None and detail.confidence_score < 80)
            or str(detail.error_category or "") == "需复核"
            for detail in details
        ),
        grading_details=sorted(details, key=lambda detail: _question_sort_key(detail.question_id)),
        raw_json={"mode": "hybrid_batch", "paper_key": paper_key, "detail_metadata": metadata_by_qid},
    )


def _bbox_from_regions(region_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple regions into a single bounding box on the same page."""
    page = _region_page(region_list[0])
    left = min(_int_region_value(r, "x") for r in region_list)
    top = min(_int_region_value(r, "y") for r in region_list)
    right = max(_int_region_value(r, "x") + _int_region_value(r, "w") for r in region_list)
    bottom = max(_int_region_value(r, "y") + _int_region_value(r, "h") for r in region_list)
    return {"page": page, "x": left, "y": top, "w": right - left, "h": bottom - top}


def _extract_sub_number(qid: str) -> int:
    """Extract the numeric index from labels like 'Q10(1)' -> 1, 'Q10(2)' -> 2."""
    m = re.search(r'\((\d+)\)', qid)
    return int(m.group(1)) if m else 999


def _major_sub_regions(spec: MajorQuestionSpec, regions: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """
    Return list of (part_id, region_bbox) pairs – one entry per detail_question_id.

    Matching strategy (in order of priority):
    1. Exact match: region's mapped_question_id == part_id directly.
    2. Positional prefix match: regions named 'Q10(1)', 'Q10(2)' are sorted by
       their sub-number and matched positionally to ['10-1', '10-2'] etc.
    3. Fallback: a single merged bounding box is used for all parts.
    """
    detail_ids = spec.detail_question_ids or [spec.question_id]
    page_candidates = [r for r in regions if _region_page(r) in {"front", "back"}]

    # --- Strategy 1: exact match per part_id ---
    exact: list[tuple[str, dict[str, Any]]] = []
    for did in detail_ids:
        matched = [r for r in page_candidates if _region_question_id(r) == did]
        if matched:
            exact.append((did, _bbox_from_regions(matched)))
    if len(exact) == len(detail_ids):
        return exact

    # --- Strategy 2: prefix + positional match (e.g. Q10(1)->10-1, Q10(2)->10-2) ---
    parent = spec.question_id
    prefix_regions = sorted(
        [r for r in page_candidates if _region_question_id(r).startswith(parent)],
        key=lambda r: _extract_sub_number(_region_question_id(r)),
    )
    if prefix_regions:
        result: list[tuple[str, dict[str, Any]]] = []
        for i, did in enumerate(detail_ids):
            region = prefix_regions[i] if i < len(prefix_regions) else prefix_regions[-1]
            result.append((did, dict(region)))
        return result

    # --- Strategy 3: single merged bbox for all parts ---
    all_matching = [
        r for r in page_candidates
        if _region_question_id(r) in set(detail_ids) or _region_question_id(r).startswith(parent)
    ]
    if not all_matching:
        raise ValueError(f"No regions found for major question {spec.question_id}")
    # Normalise page
    page_counts: dict[str, int] = {}
    for r in all_matching:
        p = _region_page(r)
        page_counts[p] = page_counts.get(p, 0) + 1
    best_page = max(page_counts, key=page_counts.get)
    best = [r for r in all_matching if _region_page(r) == best_page]
    merged = _bbox_from_regions(best)
    return [(did, dict(merged)) for did in detail_ids]


def _crop_region(source_path: Path, region: dict[str, Any], padding: int) -> tuple[Image.Image, dict[str, int]]:
    with Image.open(source_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        x = _int_region_value(region, "x")
        y = _int_region_value(region, "y")
        w = _int_region_value(region, "w")
        h = _int_region_value(region, "h")
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(width, x + w + padding)
        bottom = min(height, y + h + padding)
        if right <= left or bottom <= top:
            raise ValueError(f"Invalid major answer bbox: {region}")
        crop = rgb.crop((left, top, right, bottom))
    return crop, {"x": left, "y": top, "w": right - left, "h": bottom - top}


def _save_atlas_with_labels(labels: list[str], tile_images: list[Image.Image], atlas_path: Path, max_width: int, jpeg_quality: int) -> None:
    """Save atlas where each tile has a custom pre-built label string."""
    label_height = 42
    gap = 12
    margin = 18
    max_tile_width = max_width - margin * 2
    scaled: list[Image.Image] = []
    for image in tile_images:
        if image.width > max_tile_width:
            ratio = max_tile_width / float(image.width)
            scaled.append(image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.Resampling.LANCZOS))
        else:
            scaled.append(image.copy())
    atlas_width = max(image.width for image in scaled) + margin * 2
    atlas_height = margin + sum(label_height + image.height + gap for image in scaled) + margin
    atlas = Image.new("RGB", (atlas_width, atlas_height), "white")
    draw = ImageDraw.Draw(atlas)
    y = margin
    try:
        for label, image in zip(labels, scaled):
            draw.rectangle((margin, y, atlas_width - margin, y + label_height - 4), fill=(242, 244, 247))
            draw.text((margin + 10, y + 10), label, fill=(20, 30, 40))
            y += label_height
            atlas.paste(image, (margin, y))
            y += image.height + gap
        atlas.save(atlas_path, format="JPEG", quality=jpeg_quality)
    finally:
        atlas.close()
        for image in scaled:
            image.close()


def _save_atlas(items: list[dict[str, Any]], tile_images: list[Image.Image], atlas_path: Path, max_width: int, jpeg_quality: int) -> None:
    """Legacy single-tile-per-student atlas saver (kept for compatibility)."""
    labels = [
        f"{item['item_index']:02d}. paper_key={item['paper_key']} student={item.get('student_id')} name={item.get('student_name')}"
        for item in items
    ]
    _save_atlas_with_labels(labels, tile_images, atlas_path, max_width, jpeg_quality)


def _source_image_path(group: ExamPaperGroup, page: str) -> Path:
    if page == "back":
        return Path(group.enhanced_back_image or group.back_image)
    return Path(group.enhanced_front_image or group.front_image)


def _detail_question_ids(question: dict[str, Any]) -> list[str]:
    parts = question.get("parts")
    if isinstance(parts, list) and parts:
        return [str(part.get("part_id") or "").strip() for part in parts if isinstance(part, dict) and str(part.get("part_id") or "").strip()]
    return [str(question.get("question_id") or "").strip()]


def _question_max_score(question: dict[str, Any]) -> float:
    score = _float_value(question.get("max_score") or question.get("score"), None)
    if score is not None:
        return score
    parts = question.get("parts")
    if isinstance(parts, list):
        return sum(_float_value(part.get("max_score") or part.get("part_score") or part.get("score"), 0.0) for part in parts if isinstance(part, dict))
    return 100.0


def _failed_manifest_item(item: dict[str, Any], reason: str, question_id: str) -> dict[str, Any]:
    return {"paper_key": item.get("paper_key"), "student_id": item.get("student_id"), "question_id": question_id, "reason": reason}


def _region_question_id(region: dict[str, Any]) -> str:
    return _canonical_question_id(region.get("mapped_question_id") or region.get("detected_question_id"))


def _region_page(region: dict[str, Any]) -> str:
    return "back" if str(region.get("page") or "front").strip().lower() == "back" else "front"


def _int_region_value(region: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(round(float(region.get(key, default) or default)))
    except (TypeError, ValueError):
        return default


def _safe_path_part(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return text.strip("._") or "unknown"


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _float_value(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_0_to_100(value: Any) -> float:
    confidence = _float_value(value, 100.0) or 0.0
    if confidence <= 1:
        return confidence * 100
    return confidence


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _question_sort_key(question_id: Any) -> tuple[int, int | str, str]:
    text = str(question_id)
    match = re.search(r"\d+", text)
    if match:
        return (0, int(match.group()), text)
    return (1, text, text)


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    chunk_size = max(1, int(size or 1))
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]



def summarize_usage_records(records: list[dict[str, Any]]) -> dict[str, int]:
    fields = [
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cached_tokens",
        "latency_ms",
        "image_count",
    ]
    
    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
            
    summary = {field: sum(_int_value(item.get(field)) for item in records) for field in fields}
    summary["request_count"] = len(records)
    summary["effective_uncached_tokens"] = summary["total_tokens"] - summary["cached_tokens"]
    return summary
