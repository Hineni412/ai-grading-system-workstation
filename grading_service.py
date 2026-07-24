from __future__ import annotations

import json
import os
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Any, Iterable

from ai_grader import AIGrader
from backend.domain_models import ExamPaperGroup, QuestionGradingDetail, SecondaryError
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from evidence_atlas import EvidenceAtlasBuilder
from grading_limits import (
    FULL_PAPER_WORKERS_MAX,
    FULL_PAPER_WORKERS_MIN,
    GRADING_RPM_MAX,
    GRADING_RPM_MIN,
    HYBRID_INFLIGHT_WORKERS_MAX,
    HYBRID_INFLIGHT_WORKERS_MIN,
    LARGE_REQUEST_WORKERS_DEFAULT,
    LARGE_REQUEST_WORKERS_MAX,
    LARGE_REQUEST_WORKERS_MIN,
    OBJECTIVE_BATCH_SIZE_MAX,
    OBJECTIVE_BATCH_SIZE_MIN,
    SUBJECTIVE_MAJOR_BATCH_SIZE_MAX,
    SUBJECTIVE_MAJOR_BATCH_SIZE_MIN,
    bounded_int,
)
from grading_completeness import audit_grading_details, major_question_id, major_question_ids_for_issues
from image_preprocessor import enhance_image_file
from integration.question_tag_projection_service import QuestionTagProjectionService
from llm_client import LLMClient
from path_manager import get_path_manager
from hybrid_batch_grading_service import run_hybrid_batch_grading
from request_pacer import RequestPacer
from scanner import STUDENT_NAME_REGION_ALIASES, ScanAnalysis, Scanner, student_name_region_from_regions


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
    raw_secondary_errors = row.get("secondary_errors")
    if not isinstance(raw_secondary_errors, list):
        raw_secondary_errors = []
        raw_json = row.get("secondary_errors_json")
        if isinstance(raw_json, str):
            try:
                parsed = json.loads(raw_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = []
            if isinstance(parsed, list):
                raw_secondary_errors = parsed
    secondary_errors = [
        SecondaryError(
            category=str(item.get("category") or "").strip(),
            summary=str(item.get("summary") or "").strip(),
            evidence=str(item.get("evidence") or "").strip(),
        )
        for item in raw_secondary_errors[:2]
        if isinstance(item, dict)
        and str(item.get("category") or "").strip()
        and str(item.get("summary") or "").strip()
    ]
    return QuestionGradingDetail(
        question_id=row["question_id"],
        score_awarded=row["score_awarded"],
        deduction_reason=row.get("deduction_reason"),
        knowledge_id=knowledge_ids[0] if knowledge_ids else "",
        error_category=row.get("error_category"),
        error_summary=row.get("error_summary"),
        confidence_score=row.get("confidence_score"),
        knowledge_ids=knowledge_ids,
        secondary_errors=secondary_errors,
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
    def __init__(
        self,
        db_manager: GradingRepositoryAccess,
        llm_client: LLMClient,
        question_bank_db_path: Path | None = None,
    ) -> None:
        self.db = as_grading_repositories(db_manager)
        self.papers = self.db.papers
        self.results = self.db.results
        self.attendance = self.db.sessions
        self.llm_client = llm_client
        self.question_bank_db_path = Path(question_bank_db_path) if question_bank_db_path else None

    def _question_tag_context(
        self,
        session_id: int,
        rubric: dict[str, Any],
    ) -> dict[str, dict[str, list[str]]]:
        if self.question_bank_db_path is None:
            return {}
        return QuestionTagProjectionService(self.question_bank_db_path).project_session(
            grading_session_id=session_id,
            rubric=rubric,
        ).context_by_item()

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
        resume_run_id: int | None = None,
        supplement_only: bool = False,
        supplement_run_id: int | None = None,
        should_cancel: Any | None = None,
    ) -> Iterable[dict]:
        if resume_run_id is not None and supplement_run_id is not None:
            raise ValueError("resume and supplement cannot target the same job")
        if bool(supplement_only) != (supplement_run_id is not None):
            raise ValueError("supplement run identity is incomplete")
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
            question_tag_context=self._question_tag_context(session_id, rubric),
        )

        # 批改运行账本（附加层）：支持安全暂停/恢复与跨运行三元幂等。
        # 任何账本相关异常都回退到无账本行为，保证不影响既有批改主流程。
        run_store = None
        run = None
        config_fingerprint = ""
        strict_existing_run = resume_run_id is not None or supplement_run_id is not None
        from grading_run_store import GradingRunResumeMismatchError

        try:
            from grading_run_store import GradingRunStore
            from grading_run_identity import grading_config_fingerprint

            config_fingerprint = grading_config_fingerprint(
                rubric=grader.rubric,
                answer_key=grader.answer_key,
                answer_regions=answer_regions,
                grading_mode=resolved_grading_mode,
                grading_model=grading_model or "",
            )
            run_store = GradingRunStore(self.db.db_path)
            if resume_run_id is not None:
                run = run_store.resume_exact(
                    resume_run_id,
                    session_id,
                    config_fingerprint,
                    resolved_grading_mode,
                )
            elif supplement_run_id is not None:
                run = run_store.reopen_for_supplement(
                    supplement_run_id,
                    session_id,
                    config_fingerprint,
                    resolved_grading_mode,
                )
            else:
                run_store.fail_active_runs(session_id)
                run = run_store.begin(session_id, config_fingerprint, resolved_grading_mode)
        except GradingRunResumeMismatchError:
            raise
        except Exception as exc:
            if strict_existing_run:
                raise GradingRunResumeMismatchError(
                    "grading run configuration could not be verified"
                ) from exc
            run_store = None
            run = None

        def _pause_requested() -> bool:
            if run_store is None or run is None:
                return False
            try:
                return run_store.control_state(run.run_token) == "pause_requested"
            except Exception:
                return False

        def _cancel_requested() -> bool:
            if should_cancel is None:
                return False
            try:
                return bool(should_cancel())
            except Exception:
                return False

        def _finish_cancelled_run(*, release_session: bool) -> dict[str, Any]:
            if run_store is not None and run is not None:
                try:
                    # The ledger schema predates a dedicated cancelled state.
                    # Store cancellation as terminal failed; the workspace
                    # projects the persisted cancellation control as cancelled.
                    run_store.finish(run.run_token, "failed")
                except Exception:
                    pass
            if release_session:
                self.db.finish_session_run(session_id, "completed")
            return {
                "event": "session_cancelled",
                "run_id": run.id if run is not None else None,
                "progress": self.db.get_session_progress(session_id),
            }

        paper_cancel_restore_state: dict[int, tuple[str, str | None]] = {}

        def _restore_paper_after_cancel(
            paper_id: int,
            run_item_id: int | None = None,
        ) -> None:
            restore_status, restore_error = paper_cancel_restore_state.get(
                paper_id,
                ("pending", None),
            )
            self.papers.update_exam_paper_status(
                paper_id,
                restore_status,
                restore_error,
            )
            if run_store is not None and run_item_id is not None:
                try:
                    run_store.set_item_status(run_item_id, "pending")
                except Exception:
                    pass

        if _cancel_requested():
            yield _finish_cancelled_run(release_session=False)
            return

        if not self.db.try_start_session_run(session_id):
            raise RuntimeError("当前考试批改已有运行中的批改任务，请等待完成后再启动")
        if _cancel_requested():
            yield _finish_cancelled_run(release_session=True)
            return
        if not failed_only and not supplement_only:
            self.db.clear_session_run_data(session_id)
        if _cancel_requested():
            yield _finish_cancelled_run(release_session=True)
            return

        matched_records: list[tuple[int, ExamPaperGroup, int]] = []
        supplement_fingerprints: set[str] = set()
        supplement_student_ids: set[int] = set()
        if supplement_only:
            supplement_fingerprints, supplement_student_ids = (
                self._existing_supplement_identities(session_id)
            )

        if failed_only:
            failed_detailed = self.db.list_failed_papers_detailed(session_id)
            failed_paper_ids = [
                int(item["paper_id"])
                for item in failed_detailed
                if item.get("paper_id") is not None
            ]
            if failed_paper_ids:
                original_rows = self.papers.get_paper_statuses(
                    failed_paper_ids
                )
                paper_cancel_restore_state.update(
                    {
                        int(row["id"]): (
                            str(row["processing_status"]),
                            row["error_message"],
                        )
                        for row in original_rows
                    }
                )
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
                if _cancel_requested():
                    yield _finish_cancelled_run(release_session=True)
                    return
                source_fingerprint = ""
                if supplement_only:
                    from grading_run_identity import paper_fingerprint

                    source_fingerprint = paper_fingerprint(
                        group.front_image,
                        group.back_image,
                    )
                    if source_fingerprint in supplement_fingerprints:
                        yield {
                            "event": "paper_skipped",
                            "student_name": group.student_name,
                            "reason": "scan source was already handled by an earlier run",
                            "kind": "existing",
                        }
                        continue
                student = {"id": group.student_id, "name": group.student_name} if group.student_id else self.db.find_student_by_name(group.student_name)
                if student is None:
                    paper_id = self.papers.create_exam_paper(
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
                    if _cancel_requested():
                        yield _finish_cancelled_run(release_session=True)
                        return
                    continue

                if supplement_only and int(student["id"]) in supplement_student_ids:
                    yield {
                        "event": "paper_conflict",
                        "student_name": group.student_name,
                        "reason": "student already has another handled scan source",
                        "fingerprint": source_fingerprint[:8],
                    }
                    continue

                paper_id = self.papers.create_exam_paper(
                    session_id=session_id,
                    front_image=str(group.front_image),
                    back_image=str(group.back_image),
                    ocr_name=group.student_name,
                    student_id=int(student["id"]),
                    match_status="matched",
                    processing_status="pending",
                )
                paper_cancel_restore_state[paper_id] = ("pending", None)
                matched_records.append((paper_id, group, int(student["id"])))
                if supplement_only:
                    supplement_fingerprints.add(source_fingerprint)
                    supplement_student_ids.add(int(student["id"]))

                if _cancel_requested():
                    yield _finish_cancelled_run(release_session=True)
                    return

        if _cancel_requested():
            yield _finish_cancelled_run(release_session=True)
            return

        total = len(matched_records)
        worker_count = bounded_int(
            max_workers,
            _env_int("AI_GRADING_MAX_WORKERS", FULL_PAPER_WORKERS_MAX),
            FULL_PAPER_WORKERS_MIN,
            FULL_PAPER_WORKERS_MAX,
        )
        # 整卷大图请求（参考图 + 学生正反面）并发上限：抑制大 payload 瞬时并发。
        large_request_workers = bounded_int(
            None,
            _env_int("AI_GRADING_LARGE_REQUEST_MAX_WORKERS", LARGE_REQUEST_WORKERS_DEFAULT),
            LARGE_REQUEST_WORKERS_MIN,
            LARGE_REQUEST_WORKERS_MAX,
        )
        full_paper_workers = max(FULL_PAPER_WORKERS_MIN, min(worker_count, large_request_workers))
        rpm_limit = bounded_int(
            requests_per_minute,
            _env_int("AI_GRADING_REQUESTS_PER_MINUTE", 1000),
            GRADING_RPM_MIN,
            GRADING_RPM_MAX,
        )
        hybrid_worker_count = bounded_int(
            None,
            _env_int("AI_HYBRID_INFLIGHT_WORKERS", max(worker_count, min(rpm_limit, FULL_PAPER_WORKERS_MAX))),
            HYBRID_INFLIGHT_WORKERS_MIN,
            HYBRID_INFLIGHT_WORKERS_MAX,
        )
        rate_limiter = RequestPacer(rpm_limit)
        event_queue = queue.Queue()

        if total:
            yield {
                "event": "batch_grading_config",
                "total": total,
                "max_workers": worker_count,
                "large_request_workers": full_paper_workers,
                "hybrid_inflight_workers": hybrid_worker_count,
                "requests_per_minute": rpm_limit,
                "grading_mode": resolved_grading_mode,
            }

        if resolved_grading_mode == "hybrid_batch":
            hybrid_run_item_by_paper: dict[int, int] = {}
            if supplement_only:
                matched_records, hybrid_run_item_by_paper = yield from (
                    self._classify_full_paper_candidates(
                        matched_records,
                        run_store=run_store,
                        run=run,
                        session_id=session_id,
                        config_fingerprint=config_fingerprint,
                        resume_run_id=supplement_run_id,
                    )
                )
                total = len(matched_records)
            existing_results_by_student = {}
            skipped_questions_by_student = {}
            if failed_only:
                for paper_id, group, student_id in matched_records:
                    stored_result = self.results.get_student_result_for_retry(
                        session_id,
                        student_id,
                    )
                    if stored_result:
                        existing_results_by_student[student_id] = stored_result
                        details_rows = stored_result["details"]
                        completeness = audit_grading_details(
                            grader.rubric,
                            details_rows,
                        )
                        affected_major_ids = set(
                            major_question_ids_for_issues(completeness)
                        )
                        raw_completeness = existing_results_by_student[
                            student_id
                        ]["raw_json"].get("grading_completeness")
                        has_structured_audit = isinstance(
                            raw_completeness,
                            dict,
                        )
                        has_unmapped_unexpected = (
                            has_structured_audit
                            and any(
                                major_question_id(
                                    grader.rubric,
                                    question_id,
                                )
                                is None
                                for question_id in completeness[
                                    "unexpected_question_ids"
                                ]
                            )
                        )
                        replace_all_details = has_unmapped_unexpected or (
                            has_structured_audit and not affected_major_ids
                        )
                        if replace_all_details:
                            affected_major_ids = (
                                _rubric_major_question_ids(grader.rubric)
                            )
                        stored_result["affected_major_ids"] = (
                            affected_major_ids
                        )
                        stored_result["atomic_retry"] = bool(
                            affected_major_ids or has_structured_audit
                        )
                        stored_result["replace_all_details"] = (
                            replace_all_details
                        )
                        if replace_all_details:
                            skipped_questions_by_student[student_id] = set()
                        else:
                            skipped_questions_by_student[student_id] = {
                                detail["question_id"]
                                for detail in details_rows
                                if major_question_id(
                                    grader.rubric,
                                    detail["question_id"],
                                )
                                not in affected_major_ids
                            }

            hybrid_marked_paper_ids: list[int] = []
            for idx, (paper_id, group, student_id) in enumerate(matched_records, start=1):
                if _cancel_requested():
                    for marked_paper_id in hybrid_marked_paper_ids:
                        _restore_paper_after_cancel(
                            marked_paper_id,
                            hybrid_run_item_by_paper.get(marked_paper_id),
                        )
                    yield _finish_cancelled_run(release_session=True)
                    return
                self.papers.update_exam_paper_status(paper_id, "grading")
                if paper_id in hybrid_run_item_by_paper:
                    run_store.mark_grading(hybrid_run_item_by_paper[paper_id])
                hybrid_marked_paper_ids.append(paper_id)
                yield {
                    "event": "grading_started",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "current": idx,
                    "total": total,
                }
                if _cancel_requested():
                    for marked_paper_id in hybrid_marked_paper_ids:
                        _restore_paper_after_cancel(
                            marked_paper_id,
                            hybrid_run_item_by_paper.get(marked_paper_id),
                        )
                    yield _finish_cancelled_run(release_session=True)
                    return

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
                        batch_size=bounded_int(
                            os.getenv("LLM_HYBRID_MAJOR_BATCH_SIZE"),
                            4,
                            SUBJECTIVE_MAJOR_BATCH_SIZE_MIN,
                            SUBJECTIVE_MAJOR_BATCH_SIZE_MAX,
                        ),
                        objective_batch_size=bounded_int(
                            os.getenv("LLM_OBJECTIVE_BATCH_SIZE"),
                            OBJECTIVE_BATCH_SIZE_MAX,
                            OBJECTIVE_BATCH_SIZE_MIN,
                            OBJECTIVE_BATCH_SIZE_MAX,
                        ),
                        batch_workers=hybrid_worker_count,
                        rate_limiter=rate_limiter,
                        progress_callback=_hybrid_progress,
                        rubric_images_dir=get_path_manager().templates_dir / f"session_{session_id}" / "rubric_images",
                        skipped_questions_by_student=skipped_questions_by_student,
                        question_tag_context=grader.question_tag_context,
                        should_pause=lambda: _pause_requested() or _cancel_requested(),
                    )
                    while not future.done():
                        try:
                            while True:
                                yield event_queue.get_nowait()
                        except queue.Empty:
                            pass
                        time.sleep(0.1)
                    batch_run = future.result()
                if _cancel_requested():
                    for marked_paper_id in hybrid_marked_paper_ids:
                        _restore_paper_after_cancel(
                            marked_paper_id,
                            hybrid_run_item_by_paper.get(marked_paper_id),
                        )
                    yield _finish_cancelled_run(release_session=True)
                    return
                fallback_items_by_key = _fallback_items_by_paper_key(batch_run.fallback_items)
                paper_key_by_paper_id = {
                    paper_id: entry.paper_key
                    for entry, (paper_id, _, _) in zip(batch_run.paper_entries, matched_records)
                }
            except Exception as exc:  # noqa: BLE001
                if _cancel_requested():
                    for marked_paper_id in hybrid_marked_paper_ids:
                        _restore_paper_after_cancel(
                            marked_paper_id,
                            hybrid_run_item_by_paper.get(marked_paper_id),
                        )
                    yield _finish_cancelled_run(release_session=True)
                    return
                for _, (paper_id, group, student_id) in enumerate(matched_records, start=1):
                    completed += 1
                    retry_existing = existing_results_by_student.get(student_id) if failed_only else None
                    if retry_existing and retry_existing.get("atomic_retry"):
                        self.results.record_result_retry_failure(
                            retry_existing["result_id"],
                            _failed_retry_attempt(exc, retry_existing["affected_major_ids"]),
                        )
                    assignment_is_current = (
                        self.papers.update_exam_paper_status_if_current_assignment(
                            paper_id,
                            student_id,
                            "failed",
                            str(exc),
                        )
                    )
                    if not assignment_is_current:
                        yield {
                            "event": "paper_skipped",
                            "paper_id": paper_id,
                            "student_name": group.student_name,
                            "reason": "paper assignment changed while grading was running",
                            "kind": "assignment_changed",
                        }
                        continue
                    if paper_id in hybrid_run_item_by_paper:
                        run_store.set_item_status(
                            hybrid_run_item_by_paper[paper_id],
                            "failed",
                            disposition_reason=str(exc),
                        )
                    yield {
                        "event": "grading_failed",
                        "paper_id": paper_id,
                        "student_name": group.student_name,
                        "error": str(exc),
                        "current": completed,
                        "total": total,
                    }
                if run_store is not None and run is not None:
                    run_store.finish(run.run_token, "completed")
                self.db.finish_session_run(session_id, "completed")
                progress = self.db.get_session_progress(session_id)
                yield {"event": "session_completed", "progress": progress}
                return

            for result_index, (paper_id, group, student_id) in enumerate(matched_records):
                if _cancel_requested():
                    for pending_paper_id, _, _ in matched_records[result_index:]:
                        _restore_paper_after_cancel(
                            pending_paper_id,
                            hybrid_run_item_by_paper.get(pending_paper_id),
                        )
                    yield _finish_cancelled_run(release_session=True)
                    return
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

                    if _cancel_requested():
                        for pending_paper_id, _, _ in matched_records[result_index:]:
                            _restore_paper_after_cancel(
                                pending_paper_id,
                                hybrid_run_item_by_paper.get(pending_paper_id),
                            )
                        yield _finish_cancelled_run(release_session=True)
                        return

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
                        self.results.replace_result_details_atomic(
                            result_id,
                            remove_question_ids,
                            replacement_details,
                            rubric=grader.rubric,
                            student_score=result.student_score,
                            needs_human_review=result.needs_human_review,
                            raw_json=result.raw_json,
                        )
                        assignment_is_current = (
                            self.papers.update_exam_paper_status_if_current_assignment(
                                paper_id,
                                student_id,
                                "graded",
                            )
                        )
                    else:
                        result_id = (
                            self.results.publish_session_result_if_current_assignment(
                                session_id,
                                student_id,
                                paper_id,
                                result,
                            )
                        )
                        assignment_is_current = result_id is not None
                    if not assignment_is_current:
                        yield {
                            "event": "paper_skipped",
                            "paper_id": paper_id,
                            "student_name": group.student_name,
                            "reason": "paper assignment changed while grading was running",
                            "kind": "assignment_changed",
                        }
                        continue
                    if paper_id in hybrid_run_item_by_paper:
                        run_store.set_item_status(
                            hybrid_run_item_by_paper[paper_id],
                            "graded",
                            result_id=result_id,
                        )
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
                        self.results.record_result_retry_failure(
                            retry_existing["result_id"],
                            _failed_retry_attempt(exc, retry_existing["affected_major_ids"]),
                        )
                    assignment_is_current = (
                        self.papers.update_exam_paper_status_if_current_assignment(
                            paper_id,
                            student_id,
                            "failed",
                            str(exc),
                        )
                    )
                    if not assignment_is_current:
                        yield {
                            "event": "paper_skipped",
                            "paper_id": paper_id,
                            "student_name": group.student_name,
                            "reason": "paper assignment changed while grading was running",
                            "kind": "assignment_changed",
                        }
                        continue
                    if paper_id in hybrid_run_item_by_paper:
                        run_store.set_item_status(
                            hybrid_run_item_by_paper[paper_id],
                            "failed",
                            disposition_reason=str(exc),
                        )
                    yield {
                        "event": "grading_failed",
                        "paper_id": paper_id,
                        "student_name": group.student_name,
                        "error": str(exc),
                        "current": completed,
                        "total": total,
                    }

            hybrid_paused = bool(getattr(batch_run, "paused", False))
            if run_store is not None and run is not None:
                try:
                    run_store.finish(run.run_token, "paused" if hybrid_paused else "completed")
                except Exception:
                    pass
            self.db.finish_session_run(session_id, "completed")
            progress = self.db.get_session_progress(session_id)
            if hybrid_paused:
                yield {
                    "event": "session_paused",
                    "run_id": run.id if run is not None else None,
                    "progress": progress,
                }
            else:
                yield {"event": "session_completed", "progress": progress}
            return

        # ===== 整卷批改：候选判定 + 可暂停的有界增量派发 =====
        grade_records, run_item_by_paper = yield from self._classify_full_paper_candidates(
            matched_records,
            run_store=run_store,
            run=run,
            session_id=session_id,
            config_fingerprint=config_fingerprint,
            resume_run_id=resume_run_id or supplement_run_id,
        )

        total_grade = len(grade_records)
        completed = 0
        paused = False
        cancelled = False
        dispatch_exhausted = total_grade == 0
        record_iter = iter(list(enumerate(grade_records, start=1)))
        inflight: dict[Any, tuple[int, int, ExamPaperGroup, int]] = {}

        with ThreadPoolExecutor(max_workers=full_paper_workers, thread_name_prefix="grading") as executor:
            while True:
                while (
                    len(inflight) < full_paper_workers
                    and not paused
                    and not dispatch_exhausted
                ):
                    if _cancel_requested():
                        cancelled = True
                        paused = True
                        break
                    if _pause_requested():
                        paused = True
                        break
                    try:
                        idx, (paper_id, group, student_id) = next(record_iter)
                    except StopIteration:
                        dispatch_exhausted = True
                        break
                    self.papers.update_exam_paper_status(paper_id, "grading")
                    if run_store is not None and paper_id in run_item_by_paper:
                        try:
                            run_store.mark_grading(run_item_by_paper[paper_id])
                        except Exception:
                            pass
                    yield {
                        "event": "grading_started",
                        "paper_id": paper_id,
                        "student_name": group.student_name,
                        "current": idx,
                        "total": total_grade,
                    }
                    if _cancel_requested():
                        cancelled = True
                        paused = True
                        _restore_paper_after_cancel(
                            paper_id,
                            run_item_by_paper.get(paper_id),
                        )
                        break
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
                    inflight[future] = (idx, paper_id, group, student_id)
                    if idx >= total_grade:
                        dispatch_exhausted = True

                try:
                    while True:
                        yield event_queue.get_nowait()
                except queue.Empty:
                    pass

                if _cancel_requested() and inflight:
                    cancelled = True
                    paused = True

                if not inflight:
                    break

                done, _ = wait(set(inflight), timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    idx, paper_id, group, student_id = inflight.pop(future)
                    completed += 1
                    if cancelled or _cancel_requested():
                        cancelled = True
                        paused = True
                        try:
                            future.result()
                        except Exception:
                            pass
                        _restore_paper_after_cancel(
                            paper_id,
                            run_item_by_paper.get(paper_id),
                        )
                        continue
                    try:
                        result = future.result()
                        if _cancel_requested():
                            cancelled = True
                            paused = True
                            _restore_paper_after_cancel(
                                paper_id,
                                run_item_by_paper.get(paper_id),
                            )
                            continue
                        result_id = (
                            self.results.publish_session_result_if_current_assignment(
                                session_id,
                                student_id,
                                paper_id,
                                result,
                            )
                        )
                        if result_id is None:
                            yield {
                                "event": "paper_skipped",
                                "paper_id": paper_id,
                                "student_name": group.student_name,
                                "reason": "paper assignment changed while grading was running",
                                "kind": "assignment_changed",
                            }
                            continue
                        if run_store is not None and paper_id in run_item_by_paper:
                            try:
                                run_store.set_item_status(run_item_by_paper[paper_id], "graded", result_id=result_id)
                            except Exception:
                                pass
                        yield {
                            "event": "graded",
                            "paper_id": paper_id,
                            "result_id": result_id,
                            "student_name": result.student_name,
                            "score": result.student_score,
                            "total_score": result.total_score,
                            "needs_human_review": result.needs_human_review,
                            "current": completed,
                            "total": total_grade,
                        }
                    except Exception as exc:  # noqa: BLE001
                        assignment_is_current = (
                            self.papers.update_exam_paper_status_if_current_assignment(
                                paper_id,
                                student_id,
                                "failed",
                                str(exc),
                            )
                        )
                        if not assignment_is_current:
                            yield {
                                "event": "paper_skipped",
                                "paper_id": paper_id,
                                "student_name": group.student_name,
                                "reason": "paper assignment changed while grading was running",
                                "kind": "assignment_changed",
                            }
                            continue
                        if run_store is not None and paper_id in run_item_by_paper:
                            try:
                                run_store.set_item_status(
                                    run_item_by_paper[paper_id], "failed", disposition_reason=str(exc)
                                )
                            except Exception:
                                pass
                        yield {
                            "event": "grading_failed",
                            "paper_id": paper_id,
                            "student_name": group.student_name,
                            "error": str(exc),
                            "current": completed,
                            "total": total_grade,
                        }

                if paused and not inflight:
                    break

        if cancelled:
            yield _finish_cancelled_run(release_session=True)
            return

        if paused:
            # 未派发答卷保持 pending 待恢复；标记账本运行为 paused 并释放会话运行守卫。
            if run_store is not None and run is not None:
                try:
                    run_store.finish(run.run_token, "paused")
                except Exception:
                    pass
            self.db.finish_session_run(session_id, "completed")
            progress = self.db.get_session_progress(session_id)
            yield {
                "event": "session_paused",
                "run_id": run.id if run is not None else None,
                "progress": progress,
            }
            return

        if run_store is not None and run is not None:
            try:
                run_store.finish(run.run_token, "completed")
            except Exception:
                pass
        self.db.finish_session_run(session_id, "completed")
        progress = self.db.get_session_progress(session_id)
        yield {"event": "session_completed", "progress": progress}

    def _existing_supplement_identities(
        self,
        session_id: int,
    ) -> tuple[set[str], set[int]]:
        from grading_run_identity import paper_fingerprint

        rows = self.papers.get_session_paper_identities(session_id)
        fingerprints = {
            paper_fingerprint(
                row["front_image"],
                row["back_image"] if row["back_image"] else None,
            )
            for row in rows
        }
        student_ids = {
            int(row["student_id"])
            for row in rows
            if row["student_id"] is not None
        }
        return fingerprints, student_ids

    def _classify_full_paper_candidates(
        self,
        matched_records: list[tuple[int, ExamPaperGroup, int]],
        *,
        run_store: Any,
        run: Any,
        session_id: int,
        config_fingerprint: str,
        resume_run_id: int | None,
    ) -> Any:
        """判定候选答卷（去重/冲突/跳过已批），发出对应事件，返回待批改集合与账本项映射。

        账本不可用时退回全部批改（与旧行为一致）。跳过已批仅在"恢复运行"时启用，
        避免全新运行（已清空成绩）误跳导致学生漏批。生成器语义：用 ``yield from`` 调用。
        """
        run_item_by_paper: dict[int, int] = {}
        if run_store is None or run is None or not matched_records:
            return list(matched_records), run_item_by_paper

        from grading_run_identity import (
            CandidatePaper,
            CompletedIdentity,
            classify_student_candidates,
            paper_fingerprint,
        )

        fingerprint_by_paper: dict[int, str] = {}
        candidates: list[CandidatePaper] = []
        for paper_id, group, student_id in matched_records:
            fp = paper_fingerprint(group.front_image, group.back_image)
            fingerprint_by_paper[paper_id] = fp
            candidates.append(
                CandidatePaper(
                    source_label=str(paper_id),
                    student_id=int(student_id),
                    paper_fingerprint=fp,
                    paper_id=paper_id,
                )
            )

        completed_identities: list[CompletedIdentity] = []
        if resume_run_id is not None:
            for row in run_store.graded_identities(session_id):
                completed_identities.append(
                    CompletedIdentity(
                        int(row["student_id"]),
                        str(row["paper_fingerprint"]),
                        str(row["config_fingerprint"]),
                        True,
                    )
                )

        decisions = classify_student_candidates(candidates, completed_identities, config_fingerprint)
        decision_by_paper = {
            int(item.paper_id): item for item in decisions if item.paper_id is not None
        }

        grade_records: list[tuple[int, ExamPaperGroup, int]] = []
        for paper_id, group, student_id in matched_records:
            decision = decision_by_paper.get(paper_id)
            action = decision.action if decision is not None else "grade"
            reason = decision.reason if decision is not None else ""
            fp = fingerprint_by_paper.get(paper_id, "")
            if action == "grade":
                grade_records.append((paper_id, group, student_id))
                try:
                    run_item_by_paper[paper_id] = run_store.add_item(
                        run.id,
                        source_label=str(paper_id),
                        student_id=int(student_id),
                        paper_fingerprint=fp,
                        config_fingerprint=config_fingerprint,
                        status="pending",
                        paper_id=paper_id,
                    )
                except Exception:
                    pass
                continue

            try:
                run_store.add_item(
                    run.id,
                    source_label=str(paper_id),
                    student_id=int(student_id),
                    paper_fingerprint=fp,
                    config_fingerprint=config_fingerprint,
                    status=action,
                    paper_id=paper_id,
                    disposition_reason=reason,
                )
            except Exception:
                pass

            if action == "conflict":
                self.papers.update_exam_paper_status(paper_id, "skipped", reason or "同学生多份不同答卷冲突")
                yield {
                    "event": "paper_conflict",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "reason": reason,
                    "fingerprint": fp[:8],
                }
            elif action == "skipped_duplicate":
                self.papers.update_exam_paper_status(paper_id, "skipped", "本批次重复答卷")
                yield {
                    "event": "paper_skipped",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "reason": reason,
                    "kind": "duplicate",
                }
            else:  # skipped_existing
                yield {
                    "event": "paper_skipped",
                    "paper_id": paper_id,
                    "student_name": group.student_name,
                    "reason": reason,
                    "kind": "existing",
                }

        return grade_records, run_item_by_paper

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

    catalog = None
    if isinstance(rubric, dict) and rubric.get("questions"):
        from question_id_contract import QuestionIdCatalog

        try:
            catalog = QuestionIdCatalog.from_document(rubric)
        except Exception:
            catalog = None

    result: list[str] = []
    out_seen: set[str] = set()
    for qid in raw_ids:
        # 父题展开为规范小问；小问/未知项解析为规范号或原样保留。
        expanded: tuple[str, ...] = ()
        if catalog is not None:
            expanded = catalog.expand(qid)
            if not expanded:
                resolved = catalog.resolve(qid)
                expanded = (resolved,) if resolved else (qid,)
        else:
            expanded = (qid,)
        for item in expanded:
            if item and item not in out_seen:
                out_seen.add(item)
                result.append(item)
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
    """读取评分依据并返回规范化题号的内存副本；磁盘文件保持不变。"""
    try:
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    from question_id_contract import canonicalize_question_document

    try:
        return canonicalize_question_document(payload)
    except Exception:
        # 题号无法解析时退回原始内存副本，绝不写磁盘，也不阻断批改预检。
        return payload


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


def apply_scan_manual_decisions(
    analysis: ScanAnalysis,
    manual_decisions: list[dict],
    students: list[dict],
) -> list[ExamPaperGroup]:
    student_by_id = {int(student["id"]): student for student in students}
    issue_by_id = {issue.issue_id: issue for issue in analysis.issues}
    group_by_source = {group.source_label: group for group in analysis.groups}
    decided_issue_ids: set[str] = set()
    result = list(analysis.groups)

    for decision in manual_decisions:
        group_source_label = str(decision.get("group_source_label") or "")
        if group_source_label:
            group = group_by_source.get(group_source_label)
            if group is None or str(decision.get("action") or "") != "match":
                continue
            try:
                student_id = int(decision.get("student_id"))
            except (TypeError, ValueError):
                continue
            student = student_by_id.get(student_id)
            if student is None:
                continue
            group.student_id = student_id
            group.student_name = str(student["name"])
            group.match_method = "manual"
            group.match_score = 1.0
            continue

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


def _apply_manual_decisions(
    analysis: ScanAnalysis,
    manual_decisions: list[dict],
    students: list[dict],
) -> list[ExamPaperGroup]:
    return apply_scan_manual_decisions(analysis, manual_decisions, students)


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
    rate_limiter: RequestPacer,
    event_queue: queue.Queue | None = None,
    session_id: int | None = None,
    *,
    grading_mode: str = "full_paper",
    answer_regions: list[dict[str, Any]] | None = None,
    atlas_output_root: Path | None = None,
):
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    retry_count = bounded_int(os.getenv("AI_GRADING_FULL_PAPER_RETRIES"), 0, 0, 5)
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
    rate_limiter: RequestPacer,
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
