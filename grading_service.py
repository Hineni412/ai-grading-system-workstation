from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Any, Iterable

from ai_grader import AIGrader, QuestionGradingDetail
from db_manager import DBManager
from evidence_atlas import EvidenceAtlasBuilder
from grading_completeness import audit_grading_details, major_question_id, major_question_ids_for_issues
from image_preprocessor import enhance_image_file
from llm_client import LLMClient
from path_manager import get_path_manager
from hybrid_batch_grading_service import run_hybrid_batch_grading
from scanner import STUDENT_NAME_REGION_ALIASES, ExamPaperGroup, ScanAnalysis, Scanner, student_name_region_from_regions


def _detail_from_row(row: dict[str, Any]) -> QuestionGradingDetail:
    knowledge_ids = row.get("knowledge_ids")
    if isinstance(knowledge_ids, str):
        try:
            knowledge_ids = json.loads(knowledge_ids)
        except (TypeError, ValueError, json.JSONDecodeError):
            knowledge_ids = []
    if not isinstance(knowledge_ids, list):
        knowledge_ids = []
    if not knowledge_ids and row.get("knowledge_id"):
        knowledge_ids = [row["knowledge_id"]]
    return QuestionGradingDetail(
        question_id=row["question_id"],
        score_awarded=row["score_awarded"],
        deduction_reason=row.get("deduction_reason"),
        knowledge_id=row.get("knowledge_id") or "",
        error_category=row.get("error_category"),
        error_summary=row.get("error_summary"),
        confidence_score=row.get("confidence_score"),
        knowledge_ids=knowledge_ids,
    )


def _failed_retry_attempt(exc: Exception, affected_major_ids: set[str]) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": str(exc),
        "affected_major_question_ids": sorted(affected_major_ids),
        "attempted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _rubric_major_question_ids(rubric: dict) -> set[str]:
    questions = rubric.get("questions", []) if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return set()
    return {
        question_id
        for question in questions
        if isinstance(question, dict)
        and (question_id := str(question.get("question_id") or "").strip())
    }


class GradingService:
    def __init__(self, db_manager: DBManager, llm_client: LLMClient) -> None:
        self.db = db_manager
        self.llm_client = llm_client

    def run_session_grading(
        self,
        session_id: int,
        exams_dir: Path,
        rubric_path: Path,
        answer_key_path: Path,
        ocr_model: str | None = None,
        grading_model: str | None = None,
        scan_analysis: ScanAnalysis | dict | None = None,
        manual_decisions: list[dict] | None = None,
        enhance_images: bool = True,
        max_workers: int | None = None,
        requests_per_minute: int | None = None,
        grading_mode: str = "full_paper",
        objective_escalation_question_ids: Iterable[str] | None = None,
        failed_only: bool = False,
    ) -> Iterable[dict]:
        if not self.db.is_template_ready(session_id):
            raise ValueError("当前会话尚未完成模板题框映射确认，请先在“评分依据与会话”页完成模板配置")
        session = self.db.get_grading_session(session_id)
        rubric = _load_rubric_for_preflight(rubric_path)
        _validate_session_exam_identity(session, rubric)
        from answer_region_geometry import answer_regions_with_template_source_sizes
        data_root = self.db.db_path.parent.parent if self.db.db_path.parent.name == "databases" else None
        answer_regions = answer_regions_with_template_source_sizes(self.db, session_id, data_root=data_root)
        if grading_mode == "hybrid_batch":
            resolved_grading_mode = "hybrid_batch"
        else:
            resolved_grading_mode = "full_paper"

        scanner = Scanner(
            exams_dir=exams_dir,
            llm_client=self.llm_client,
            ocr_model=ocr_model,
            enhance_images=enhance_images,
            name_region=student_name_region_from_regions(answer_regions),
            front_page_parity=_session_front_page_parity(session_id),
        )
        students = self.db.list_students()
        target_question_ids = _target_question_ids_from_regions(answer_regions, rubric=rubric)
        grader = AIGrader(
            rubric_path=rubric_path,
            answer_key_path=answer_key_path,
            llm_client=self.llm_client,
            grading_model=grading_model,
            target_question_ids=target_question_ids,
            answer_regions=answer_regions,
        )

        if not failed_only:
            if not self.db.try_start_session_run(session_id):
                raise RuntimeError("当前考试批改已有运行中的批改任务，请等待完成后再启动")
            self.db.clear_session_run_data(session_id)
        else:
            if not self.db.try_start_session_run(session_id):
                raise RuntimeError("当前考试批改已有运行中的批改任务，请等待完成后再启动")

        matched_records: list[tuple[int, ExamPaperGroup, int]] = []

        if failed_only:
            failed_detailed = self.db.list_failed_papers_detailed(session_id)
            for item in failed_detailed:
                group = ExamPaperGroup(
                    front_image=Path(item["front_image"]),
                    back_image=Path(item["back_image"]) if item["back_image"] else None,
                    student_name=item["ocr_name"],
                    student_id=item["student_id"],
                    detected_name=item["ocr_name"],
                    source_label=f"Retry {item['ocr_name']}",
                )
                matched_records.append((item["paper_id"], group, item["student_id"]))
        else:
            if isinstance(scan_analysis, dict):
                analysis = ScanAnalysis.from_dict(scan_analysis)
            elif isinstance(scan_analysis, ScanAnalysis):
                analysis = scan_analysis
            else:
                analysis = scanner.analyze(students)
            if enhance_images:
                _attach_enhanced_paths(analysis, exams_dir / "_enhanced")
            else:
                _clear_enhanced_paths(analysis)

            scanned_groups = _apply_manual_decisions(analysis, manual_decisions or [], students)
            self._record_attendance(session_id, students, scanned_groups, analysis.issues)

            for issue in analysis.issues:
                yield {
                    "event": "scan_issue",
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "message": issue.message,
                    "source_label": issue.source_label,
                    "detected_name": issue.detected_name,
                }

            for group in scanned_groups:
                student = {"id": group.student_id, "name": group.student_name} if group.student_id else self.db.find_student_by_name(group.student_name)
                if student is None:
                    paper_id = self.db.create_exam_paper(
                        session_id=session_id,
                        front_image=str(group.front_image),
                        back_image=str(group.back_image),
                        ocr_name=group.student_name,
                        student_id=None,
                        match_status="unmatched",
                        processing_status="skipped",
                        error_message="名单未匹配到该姓名",
                    )
                    yield {
                        "event": "paper_unmatched",
                        "paper_id": paper_id,
                        "ocr_name": group.student_name,
                        "front_image": group.front_image.name,
                        "back_image": group.back_image.name,
                        "message": "OCR 姓名未匹配到学生名单，已跳过",
                    }
                    continue

                paper_id = self.db.create_exam_paper(
                    session_id=session_id,
                    front_image=str(group.front_image),
                    back_image=str(group.back_image),
                    ocr_name=group.student_name,
                    student_id=int(student["id"]),
                    match_status="matched",
                    processing_status="pending",
                )
                matched_records.append((paper_id, group, int(student["id"])))

        total = len(matched_records)
        worker_count = _bounded_int(max_workers, _env_int("AI_GRADING_MAX_WORKERS", 200), 1, 200)
        rpm_limit = _bounded_int(requests_per_minute, _env_int("AI_GRADING_REQUESTS_PER_MINUTE", 1000), 1, 10000)
        hybrid_worker_count = _bounded_int(
            None,
            _env_int("AI_HYBRID_INFLIGHT_WORKERS", max(worker_count, min(rpm_limit, 200))),
            1,
            1000,
        )
        rate_limiter = _RateLimiter(rpm_limit)
        event_queue = queue.Queue()

        if total:
            yield {
                "event": "batch_grading_config",
                "total": total,
                "max_workers": worker_count,
                "hybrid_inflight_workers": hybrid_worker_count,
                "requests_per_minute": rpm_limit,
                "grading_mode": resolved_grading_mode,
            }

        if resolved_grading_mode == "hybrid_batch":
            existing_results_by_student = {}
            skipped_questions_by_student = {}
            if failed_only:
                for paper_id, group, student_id in matched_records:
                    with self.db._connect() as conn:
                        row = conn.execute(
                            "SELECT id, total_score, student_score, needs_human_review, raw_json FROM session_results WHERE session_id = ? AND student_id = ?",
                            (session_id, student_id)
                        ).fetchone()
                        if row:
                            res_id = row["id"]
                            details_rows = self.db.get_result_details(res_id)
                            existing_results_by_student[student_id] = {
                                "result_id": res_id,
                                "total_score": row["total_score"],
                                "student_score": row["student_score"],
                                "needs_human_review": bool(row["needs_human_review"]),
                                "raw_json": json.loads(row["raw_json"]) if row["raw_json"] else {},
                                "details": details_rows
                            }
                            completeness = audit_grading_details(grader.rubric, details_rows)
                            affected_major_ids = set(major_question_ids_for_issues(completeness))
                            raw_completeness = existing_results_by_student[student_id]["raw_json"].get(
                                "grading_completeness"
                            )
                            has_structured_audit = isinstance(raw_completeness, dict)
                            replace_all_details = has_structured_audit and not affected_major_ids
                            if replace_all_details:
                                affected_major_ids = _rubric_major_question_ids(grader.rubric)
                            existing_results_by_student[student_id]["affected_major_ids"] = affected_major_ids
                            existing_results_by_student[student_id]["atomic_retry"] = bool(
                                affected_major_ids or has_structured_audit
                            )
                            existing_results_by_student[student_id]["replace_all_details"] = replace_all_details
                            if replace_all_details:
                                skipped_questions_by_student[student_id] = set()
                            else:
                                skipped_questions_by_student[student_id] = {
                                    d["question_id"]
                                    for d in details_rows
                                    if major_question_id(grader.rubric, d["question_id"]) not in affected_major_ids
                                }

            for idx, (paper_id, group, student_id) in enumerate(matched_records, start=1):
                self.db.update_exam_paper_status(paper_id, "grading")
                yield {
                    "event": "grading_started",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "current": idx,
                    "total": total,
                }

            completed = 0
            try:
                def _hybrid_progress(event: dict[str, Any]) -> None:
                    stage = str(event.get("stage") or "")
                    qid = str(event.get("question_id") or "?")
                    batch_index = event.get("batch_index")
                    item_count = event.get("item_count")
                    prefix = "【选填题并发】" if stage.startswith("objective") else "【主观题并发】"
                    if stage.endswith("_summary"):
                        total_q = event.get('total_questions', 0)
                        total_b = event.get('total_batches', 0)
                        total_p = event.get('total_papers', 0)
                        msg = f"【总览】 {prefix}即将开始，共 {total_q} 道题，拆分为 {total_b} 个并发批次，覆盖 {total_p} 份答卷。"
                    elif stage.endswith("_start"):
                        msg = f"{prefix} 开始发送 -> 第 {qid} 题 (批次 #{batch_index})，合并了 {item_count} 份切片..."
                    elif stage.endswith("_done"):
                        accepted = event.get('accepted_count', 0)
                        failed = event.get('review_count', event.get('failed_count', 0))
                        msg = f"{prefix} 收到结果 <- 第 {qid} 题 (批次 #{batch_index})，AI 成功: {accepted} 份，失败/低置信: {failed} 份"
                    elif stage.endswith("_error"):
                        msg = f"{prefix} 请求失败 <- 第 {qid} 题 (批次 #{batch_index})，原因: {event.get('error')}"
                    else:
                        msg = f"{prefix} 进度: {qid} batch={batch_index}"
                    event_queue.put({"event": "grading_log", "student_name": "混合批改引擎", "message": msg})

                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        run_hybrid_batch_grading,
                        session_id=session_id,
                        paper_groups=[group for _, group, _ in matched_records],
                        answer_regions=answer_regions,
                        rubric=grader.rubric,
                        answer_key=grader.answer_key,
                        llm_client=self.llm_client,
                        grading_model=grading_model,
                        output_root=get_path_manager().outputs_dir / "hybrid_batch",
                        batch_size=_bounded_int(os.getenv("LLM_HYBRID_MAJOR_BATCH_SIZE"), 4, 1, 20),
                        objective_batch_size=_bounded_int(os.getenv("LLM_OBJECTIVE_BATCH_SIZE"), 15, 1, 50),
                        batch_workers=hybrid_worker_count,
                        rate_limiter=rate_limiter,
                        progress_callback=_hybrid_progress,
                        rubric_images_dir=get_path_manager().templates_dir / f"session_{session_id}" / "rubric_images",
                        skipped_questions_by_student=skipped_questions_by_student,
                    )
                    while not future.done():
                        try:
                            while True:
                                yield event_queue.get_nowait()
                        except queue.Empty:
                            pass
                        time.sleep(0.1)
                    batch_run = future.result()
                fallback_items_by_key = _fallback_items_by_paper_key(batch_run.fallback_items)
                paper_key_by_paper_id = {
                    paper_id: entry.paper_key
                    for entry, (paper_id, _, _) in zip(batch_run.paper_entries, matched_records)
                }
            except Exception as exc:  # noqa: BLE001
                for _, (paper_id, group, student_id) in enumerate(matched_records, start=1):
                    completed += 1
                    retry_existing = existing_results_by_student.get(student_id) if failed_only else None
                    if retry_existing and retry_existing.get("atomic_retry"):
                        self.db.record_result_retry_failure(
                            retry_existing["result_id"],
                            _failed_retry_attempt(exc, retry_existing["affected_major_ids"]),
                        )
                    self.db.update_exam_paper_status(paper_id, "failed", str(exc))
                    yield {
                        "event": "grading_failed",
                        "paper_id": paper_id,
                        "student_name": group.student_name,
                        "error": str(exc),
                        "current": completed,
                        "total": total,
                    }
                self.db.finish_session_run(session_id, "completed")
                progress = self.db.get_session_progress(session_id)
                yield {"event": "session_completed", "progress": progress}
                return

            for paper_id, group, student_id in matched_records:
                completed += 1
                retry_existing = existing_results_by_student.get(student_id) if failed_only else None
                try:
                    paper_key = paper_key_by_paper_id.get(paper_id, "")
                    fallback_items = fallback_items_by_key.get(paper_key, [])
                    result = batch_run.results_by_paper_key[paper_key]

                    atomic_major_retry = bool(retry_existing and retry_existing["atomic_retry"])
                    if atomic_major_retry:
                        existing = retry_existing
                        affected_major_ids = existing["affected_major_ids"]
                        if existing["replace_all_details"] and not affected_major_ids:
                            raise ValueError("Structured incomplete result has no rubric major questions to retry safely")
                        merged_raw_json = dict(existing["raw_json"])
                        merged_raw_json.pop("hybrid_batch_fallback", None)
                        if result.raw_json:
                            merged_raw_json.update(result.raw_json)
                        preserved_details = [
                            _detail_from_row(detail)
                            for detail in existing["details"]
                            if not existing["replace_all_details"]
                            and major_question_id(grader.rubric, detail["question_id"]) not in affected_major_ids
                        ]
                        replacement_details = [
                            detail
                            for detail in result.grading_details
                            if major_question_id(grader.rubric, detail.question_id) in affected_major_ids
                        ]
                        merged_details = [*preserved_details, *replacement_details]
                        if fallback_items:
                            raise ValueError("Retry did not return every affected major-question part exactly once and in range")
                        result.total_score = existing["total_score"]
                        result.student_score = sum(detail.score_awarded for detail in merged_details)
                        result.needs_human_review = (
                            any(
                                detail.confidence_score is not None and detail.confidence_score < 80
                                for detail in merged_details
                            )
                            or result.needs_human_review
                        )
                        result.grading_details = merged_details
                        result.raw_json = merged_raw_json

                    elif failed_only and retry_existing:
                        existing = retry_existing
                        merged_raw_json = dict(existing["raw_json"])
                        merged_raw_json.pop("hybrid_batch_fallback", None)
                        if result.raw_json:
                            merged_raw_json.update(result.raw_json)

                        new_details_map = {d.question_id: d for d in result.grading_details}
                        merged_details = []
                        for old_detail in existing["details"]:
                            merged_details.append(new_details_map.pop(old_detail["question_id"], _detail_from_row(old_detail)))
                        merged_details.extend(new_details_map.values())
                        result.total_score = existing["total_score"]
                        result.student_score = sum(detail.score_awarded for detail in merged_details)
                        result.needs_human_review = (
                            any(
                                detail.confidence_score is not None and detail.confidence_score < 80
                                for detail in merged_details
                            )
                            or result.needs_human_review
                        )
                        result.grading_details = merged_details
                        result.raw_json = merged_raw_json

                    if fallback_items:
                        result.needs_human_review = True
                        result.raw_json = dict(result.raw_json or {})
                        result.raw_json["hybrid_batch_fallback"] = {
                            "mode": "partial_failure",
                            "items": fallback_items,
                        }

                    try:
                        while True:
                            yield event_queue.get_nowait()
                    except queue.Empty:
                        pass

                    if atomic_major_retry:
                        remove_question_ids = [
                            detail["question_id"]
                            for detail in retry_existing["details"]
                            if retry_existing["replace_all_details"]
                            or major_question_id(grader.rubric, detail["question_id"])
                            in retry_existing["affected_major_ids"]
                        ]
                        replacement_details = [
                            detail
                            for detail in result.grading_details
                            if major_question_id(grader.rubric, detail.question_id)
                            in retry_existing["affected_major_ids"]
                        ]
                        result_id = retry_existing["result_id"]
                        self.db.replace_result_details_atomic(
                            result_id,
                            remove_question_ids,
                            replacement_details,
                            rubric=grader.rubric,
                            student_score=result.student_score,
                            needs_human_review=result.needs_human_review,
                            raw_json=result.raw_json,
                        )
                    else:
                        result_id = self.db.save_session_result(session_id, student_id, paper_id, result)
                    self.db.update_exam_paper_status(paper_id, "graded")
                    yield {
                        "event": "graded",
                        "paper_id": paper_id,
                        "result_id": result_id,
                        "student_name": result.student_name,
                        "score": result.student_score,
                        "total_score": result.total_score,
                        "needs_human_review": result.needs_human_review,
                        "current": completed,
                        "total": total,
                    }
                except Exception as exc:  # noqa: BLE001
                    if retry_existing and retry_existing.get("atomic_retry"):
                        self.db.record_result_retry_failure(
                            retry_existing["result_id"],
                            _failed_retry_attempt(exc, retry_existing["affected_major_ids"]),
                        )
                    self.db.update_exam_paper_status(paper_id, "failed", str(exc))
                    yield {
                        "event": "grading_failed",
                        "paper_id": paper_id,
                        "student_name": group.student_name,
                        "error": str(exc),
                        "current": completed,
                        "total": total,
                    }

            self.db.finish_session_run(session_id, "completed")
            progress = self.db.get_session_progress(session_id)
            yield {"event": "session_completed", "progress": progress}
            return

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="grading") as executor:
            future_map = {}
            for idx, (paper_id, group, student_id) in enumerate(matched_records, start=1):
                self.db.update_exam_paper_status(paper_id, "grading")
                yield {
                    "event": "grading_started",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "current": idx,
                    "total": total,
                }
                future = executor.submit(
                    _grade_one_paper_with_retries,
                    grader,
                    group,
                    rate_limiter,
                    event_queue,
                    session_id,
                    grading_mode=resolved_grading_mode,
                    answer_regions=answer_regions,
                    atlas_output_root=get_path_manager().outputs_dir / "evidence_atlas",
                )
                future_map[future] = (idx, paper_id, group, student_id)

            completed = 0
            pending_futures = set(future_map.keys())
            
            while pending_futures or not event_queue.empty():
                try:
                    while True:
                        yield event_queue.get_nowait()
                except queue.Empty:
                    pass

                if pending_futures:
                    done, pending_futures = wait(pending_futures, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        idx, paper_id, group, student_id = future_map[future]
                        completed += 1
                        try:
                            result = future.result()
                            result_id = self.db.save_session_result(session_id, student_id, paper_id, result)
                            self.db.update_exam_paper_status(paper_id, "graded")
        
                            yield {
                                "event": "graded",
                                "paper_id": paper_id,
                                "result_id": result_id,
                                "student_name": result.student_name,
                                "score": result.student_score,
                                "total_score": result.total_score,
                                "needs_human_review": result.needs_human_review,
                                "current": completed,
                                "total": total,
                            }
                        except Exception as exc:  # noqa: BLE001
                            self.db.update_exam_paper_status(paper_id, "failed", str(exc))
                            yield {
                                "event": "grading_failed",
                                "paper_id": paper_id,
                                "student_name": group.student_name,
                                "error": str(exc),
                                "current": completed,
                                "total": total,
                            }
                else:
                    break

        self.db.finish_session_run(session_id, "completed")
        progress = self.db.get_session_progress(session_id)
        yield {"event": "session_completed", "progress": progress}

    def _record_attendance(
        self,
        session_id: int,
        students: list[dict],
        scanned_groups: list[ExamPaperGroup],
        issues: list,
    ) -> None:
        matched_ids = {int(group.student_id) for group in scanned_groups if group.student_id is not None}
        has_scan_issues = bool(issues)
        rows: list[dict] = []
        for student in students:
            student_id = int(student["id"])
            if student_id in matched_ids:
                rows.append(
                    {
                        "student_id": student_id,
                        "attendance_status": "present",
                        "source_reason": "已成功匹配答卷",
                    }
                )
            else:
                rows.append(
                    {
                        "student_id": student_id,
                        "attendance_status": "absent",
                        "source_reason": "未检测到有效答卷；请结合扫描异常确认" if has_scan_issues else "未检测到有效答卷",
                    }
                )
        self.db.replace_session_attendance(session_id, rows)


def _target_question_ids_from_regions(regions: list[dict], rubric: dict | None = None) -> list[str]:
    raw_ids: list[str] = []
    seen: set[str] = set()
    for region in regions:
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        if qid in STUDENT_NAME_REGION_ALIASES:
            continue
        if not qid or qid in seen:
            continue
        seen.add(qid)
        raw_ids.append(qid)

    # Expand to parts if rubric is provided
    result: list[str] = []
    if rubric and isinstance(rubric, dict):
        rubric_parts: dict[str, list[str]] = {}
        for q in rubric.get("questions", []):
            if not isinstance(q, dict):
                continue
            parent_qid = str(q.get("question_id") or "").strip()
            parts = q.get("parts", [])
            if parent_qid and isinstance(parts, list) and len(parts) > 0:
                part_ids = [str(p.get("part_id") or "").strip() for p in parts if isinstance(p, dict) and p.get("part_id")]
                if part_ids:
                    rubric_parts[parent_qid] = part_ids

        for qid in raw_ids:
            if qid in rubric_parts:
                result.extend(rubric_parts[qid])
            else:
                result.append(qid)
    else:
        result = raw_ids
    return result


def _fallback_items_by_student(fallback_items: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for item in fallback_items:
        try:
            student_id = int(item.get("student_id"))
        except (TypeError, ValueError):
            continue
        result.setdefault(student_id, []).append(dict(item))
    return result


def _fallback_items_by_paper_key(fallback_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in fallback_items:
        paper_key = str(item.get("paper_key") or "").strip()
        if not paper_key:
            continue
        result.setdefault(paper_key, []).append(dict(item))
    return result


def _session_front_page_parity(session_id: int) -> str:
    state_path = get_path_manager().templates_dir / f"session_{session_id}" / "workflow_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "odd"
    extra = state.get("extra") if isinstance(state, dict) else {}
    if not isinstance(extra, dict):
        return "odd"
    parity = str(extra.get("front_page_parity") or "").strip().lower()
    if parity in {"odd", "even"}:
        return parity
    role = str(extra.get("template_first_page_role") or "").strip().lower()
    if role == "back":
        return "even"
    return "odd"


def _load_rubric_for_preflight(rubric_path: Path) -> dict:
    try:
        return json.loads(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _validate_session_exam_identity(session: dict | None, rubric: dict) -> None:
    if not session or not isinstance(rubric, dict):
        return
    session_name = str(session.get("session_name") or "")
    exam_title = str(rubric.get("exam_title") or rubric.get("title") or "")
    session_tokens = _exam_identity_tokens(session_name)
    rubric_tokens = _exam_identity_tokens(exam_title)
    if not session_tokens or not rubric_tokens:
        return
    if session_tokens.isdisjoint(rubric_tokens):
        raise ValueError(
            "当前考试批改与评分标准疑似不匹配："
            f"考试名称为“{session_name}”，评分标准标题为“{exam_title}”。"
            "请先在“评分依据与会话”页重新绑定本场考试对应的评分标准。"
        )


def _exam_identity_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.findall(r"(?<!\d)(\d{4})(?!\d)", str(text or "")):
        tokens.add(match)
    for month, day in re.findall(r"(?<!\d)(\d{1,2})\s*[./月-]\s*(\d{1,2})(?:\s*日)?(?!\d)", str(text or "")):
        tokens.add(f"{int(month):02d}{int(day):02d}")
    return tokens


def _apply_manual_decisions(
    analysis: ScanAnalysis,
    manual_decisions: list[dict],
    students: list[dict],
) -> list[ExamPaperGroup]:
    student_by_id = {int(student["id"]): student for student in students}
    issue_by_id = {issue.issue_id: issue for issue in analysis.issues}
    decided_issue_ids: set[str] = set()
    result = list(analysis.groups)

    for decision in manual_decisions:
        issue_id = str(decision.get("issue_id") or "")
        action = str(decision.get("action") or "")
        issue = issue_by_id.get(issue_id)
        if not issue:
            continue
        decided_issue_ids.add(issue_id)
        if action == "invalid":
            continue
        if action != "match" or issue.back_image is None:
            continue
        try:
            student_id = int(decision.get("student_id"))
        except (TypeError, ValueError):
            continue
        student = student_by_id.get(student_id)
        if not student:
            continue
        result.append(
            ExamPaperGroup(
                front_image=issue.front_image,
                back_image=issue.back_image,
                student_name=str(student["name"]),
                student_id=student_id,
                detected_name=issue.detected_name,
                source_label=issue.source_label,
                enhanced_front_image=issue.enhanced_front_image,
                enhanced_back_image=issue.enhanced_back_image,
            )
        )

    analysis.issues = [issue for issue in analysis.issues if issue.issue_id not in decided_issue_ids]
    return result


def _attach_enhanced_paths(analysis: ScanAnalysis, output_dir: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import os

    tasks = []
    
    def process_group_front(g):
        g.enhanced_front_image = _enhance_or_original(g.front_image, output_dir)
    def process_group_back(g):
        g.enhanced_back_image = _enhance_or_original(g.back_image, output_dir)
    def process_issue_front(i):
        i.enhanced_front_image = _enhance_or_original(i.front_image, output_dir)
    def process_issue_back(i):
        i.enhanced_back_image = _enhance_or_original(i.back_image, output_dir)

    for group in analysis.groups:
        if group.enhanced_front_image is None:
            tasks.append(lambda g=group: process_group_front(g))
        if group.enhanced_back_image is None and group.back_image is not None:
            tasks.append(lambda g=group: process_group_back(g))
            
    for issue in analysis.issues:
        if issue.enhanced_front_image is None and issue.front_image is not None:
            tasks.append(lambda i=issue: process_issue_front(i))
        if issue.back_image is not None and issue.enhanced_back_image is None:
            tasks.append(lambda i=issue: process_issue_back(i))

    if not tasks:
        return

    workers = min(len(tasks), os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(t) for t in tasks]
        for f in as_completed(futures):
            f.result()


def _enhance_or_original(path: Path, output_dir: Path) -> Path:
    if _is_standard_pdf_page(path):
        return path
    try:
        return enhance_image_file(path, output_dir)
    except Exception:  # noqa: BLE001
        return path


def _is_standard_pdf_page(path: Path) -> bool:
    page_dir = Path(path).parent
    if page_dir.parent.name != "_pdf_pages":
        return False
    manifest_path = page_dir / "source_manifest.json"
    return manifest_path.exists()


def _clear_enhanced_paths(analysis: ScanAnalysis) -> None:
    for group in analysis.groups:
        group.enhanced_front_image = None
        group.enhanced_back_image = None
    for issue in analysis.issues:
        issue.enhanced_front_image = None
        issue.enhanced_back_image = None


def _grade_one_paper_with_retries(
    grader: AIGrader,
    group: ExamPaperGroup,
    rate_limiter: "_RateLimiter",
    event_queue: queue.Queue | None = None,
    session_id: int | None = None,
    *,
    grading_mode: str = "full_paper",
    answer_regions: list[dict[str, Any]] | None = None,
    atlas_output_root: Path | None = None,
):
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    retry_count = _bounded_int(os.getenv("AI_GRADING_FULL_PAPER_RETRIES"), 1, 0, 5)
    attempts = retry_count + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _grade_one_paper(
                grader,
                group,
                rate_limiter,
                event_queue,
                session_id,
                grading_mode=grading_mode,
                answer_regions=answer_regions,
                atlas_output_root=atlas_output_root,
            )
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            # 网络/超时/限流错误——值得重试，加指数退避
            last_error = exc
            if attempt >= attempts:
                break
            wait_secs = min(2 ** attempt, 30)
            if event_queue is not None:
                event_queue.put(
                    {
                        "event": "grading_log",
                        "student_name": group.student_name,
                        "message": (
                            f"网络/限流错误，{wait_secs}s 后重试"
                            f"（第 {attempt}/{retry_count} 次）：{exc}"
                        ),
                    }
                )
            time.sleep(wait_secs)
        except Exception as exc:  # noqa: BLE001
            # 其他错误（JSON解析失败、模型参数错误等）——不值得重试
            last_error = exc
            if event_queue is not None:
                event_queue.put(
                    {
                        "event": "grading_log",
                        "student_name": group.student_name,
                        "message": f"批改出错（非网络问题，不重试）：{exc}",
                    }
                )
            break
    if last_error is not None:
        raise last_error
    raise RuntimeError("full_paper_grading_failed_without_error")


def _grade_one_paper(
    grader: AIGrader,
    group: ExamPaperGroup,
    rate_limiter: "_RateLimiter",
    event_queue: queue.Queue | None = None,
    session_id: int | None = None,
    *,
    grading_mode: str = "full_paper",
    answer_regions: list[dict[str, Any]] | None = None,
    atlas_output_root: Path | None = None,
):
    def report(msg: str):
        if event_queue:
            event_queue.put({"event": "grading_log", "student_name": group.student_name, "message": msg})
            
    rate_limiter.acquire()
    
    # [ROUTING LOGIC START] Simulate routing and log it
    from grading_router import route_grading_task
    for qid in grader.target_question_ids:
        q_type = grader.get_question_type(qid)
                
        route_grading_task(
            question_type=q_type,
            exam_id=str(session_id) if session_id is not None else "unknown",
            student_id=str(group.student_id),
            question_id=qid
        )
    # [ROUTING LOGIC END]

    # [SUBJECTIVE ONLY REPLACEMENT START]
    main_result = None

    def _run_main_grading():
        if grading_mode == "evidence_atlas":
            try:
                builder = EvidenceAtlasBuilder(
                    output_root=atlas_output_root or get_path_manager().outputs_dir / "evidence_atlas"
                )
                atlas_result = builder.build(
                    session_id=session_id if session_id is not None else "unknown",
                    paper_group=group,
                    answer_regions=answer_regions or getattr(grader, "answer_regions", []),
                )
                report(f"Evidence atlas grading enabled: {atlas_result.atlas_path}")
                return grader.grade_with_atlas(
                    group,
                    atlas_path=atlas_result.atlas_path,
                    atlas_manifest=atlas_result.manifest,
                    report=report,
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Evidence atlas grading failed, falling back to full_paper: {e}")
                report(f"Evidence atlas failed, falling back to full paper: {e}")
        return grader.grade(group, report=report)

    main_result = _run_main_grading()
    # [SUBJECTIVE ONLY REPLACEMENT END]


    return main_result


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bounded_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        resolved = int(default)
    return max(minimum, min(maximum, resolved))


class _RateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self.capacity = max(1, requests_per_minute)
        self.rate = self.capacity / 60.0  # tokens per second
        self.tokens = float(self.capacity)
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
                self.last_update = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
            time.sleep(0.05)
