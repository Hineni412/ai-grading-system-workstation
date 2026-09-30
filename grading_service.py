from __future__ import annotations

import json
import os
import queue
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from ai_batch_grading_service import run_ai_batch_grading
from ai_grader import AIGrader
from backend.domain_models import (
    ExamPaperGroup,
    GradingResult,
    QuestionGradingDetail,
    SecondaryError,
    detail_ai_score,
)
from backend.grading_workflow import preflight_match_status, rubric_scoring_item_scores
from backend.llm.execution import execution_snapshot_from_profile
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from grading_completeness import (
    audit_grading_details,
    details_require_review,
    is_objective_detail,
    major_question_id,
    major_question_ids_for_issues,
    merge_detail_metadata,
    review_confidence_threshold,
)
from grading_limits import (
    FULL_PAPER_WORKERS_MAX,
    FULL_PAPER_WORKERS_MIN,
    GRADING_RPM_MAX,
    GRADING_RPM_MIN,
    HYBRID_INFLIGHT_WORKERS_MAX,
    HYBRID_INFLIGHT_WORKERS_MIN,
    OBJECTIVE_BATCH_SIZE_MAX,
    OBJECTIVE_BATCH_SIZE_MIN,
    bounded_int,
)
from image_preprocessor import enhance_image_file, is_standard_pdf_page
from integration.question_tag_projection_service import QuestionTagProjectionService
from llm_client import LLMClient
from path_manager import get_path_manager
from request_pacer import RequestPacer
from scanner import (
    STUDENT_NAME_REGION_ALIASES,
    ScanAnalysis,
    Scanner,
    student_name_region_from_regions,
)


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
        ai_score_awarded=row.get("ai_score_awarded"),
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
        grading_mode: str = "ai",
        scan_batch_id: str | None = None,
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
        if not self.db.templates.is_template_ready(session_id):
            raise ValueError("当前会话尚未完成模板题框映射确认，请先在“评分依据与会话”页完成模板配置")
        session = self.db.sessions.get_grading_session(session_id)
        rubric = _load_rubric_for_preflight(rubric_path)
        _validate_session_exam_identity(session, rubric)
        from answer_region_geometry import answer_regions_with_template_source_sizes
        data_root = self.db.db_path.parent.parent if self.db.db_path.parent.name == "databases" else None
        answer_regions = answer_regions_with_template_source_sizes(self.db, session_id, data_root=data_root)
        if str(grading_mode or "").strip() in {"full_paper", "hybrid_batch"}:
            raise ValueError(
                "旧批改方式已停用，请用 AI 批改重新开始未完成的部分"
            )
        resolved_grading_mode = "ai"
        teacher_locks = (
            self.db.reviews.list_teacher_score_locks(
                session_id,
                str(scan_batch_id),
            )
            if scan_batch_id
            else []
        )
        teacher_score_locks_by_student: dict[
            int,
            dict[str, dict[str, Any]],
        ] = {}
        current_max_scores = rubric_scoring_item_scores(rubric)
        for lock in teacher_locks:
            locked_question_id = str(lock["question_id"])
            current_max_score = current_max_scores.get(locked_question_id)
            if (
                current_max_score is None
                or abs(
                    float(lock.get("max_score") or 0)
                    - current_max_score
                )
                > 1e-6
                or float(lock.get("score_awarded") or 0)
                > current_max_score + 1e-6
            ):
                raise ValueError(
                    "教师最终分所用满分与当前评分依据不一致，"
                    "请先在人工批改中重新确认后再启动 AI 批改"
                )
            teacher_score_locks_by_student.setdefault(
                int(lock["student_id"]),
                {},
            )[locked_question_id] = dict(lock)

        scanner = Scanner(
            exams_dir=exams_dir,
            llm_client=self.llm_client,
            ocr_model=ocr_model,
            enhance_images=enhance_images,
            name_region=student_name_region_from_regions(answer_regions),
            front_page_parity=_session_front_page_parity(session_id),
        )
        students = self.db.students.list_students()
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
            from grading_run_identity import grading_config_fingerprint
            from grading_run_store import GradingRunStore

            config_fingerprint = grading_config_fingerprint(
                rubric=grader.rubric,
                answer_key=grader.answer_key,
                answer_regions=answer_regions,
                grading_mode=resolved_grading_mode,
                grading_model=grading_model or "",
            )
            run_store = GradingRunStore(self.db.db_path)
            target_run_id = (
                resume_run_id
                if resume_run_id is not None
                else supplement_run_id
            )
            if target_run_id is not None:
                stored_run = run_store.get_run(int(target_run_id))
                if (
                    stored_run is not None
                    and str(stored_run.grading_mode) != "ai"
                ):
                    raise GradingRunResumeMismatchError(
                        "旧批改方式已停用，请用 AI 批改重新开始未完成的部分"
                    )
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
                self.db.sessions.finish_session_run(session_id, "completed")
            return {
                "event": "session_cancelled",
                "run_id": run.id if run is not None else None,
                "progress": self.db.papers.get_session_progress(session_id),
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

        if not self.db.sessions.try_start_session_run(session_id):
            raise RuntimeError("当前考试批改已有运行中的批改任务，请等待完成后再启动")
        if _cancel_requested():
            yield _finish_cancelled_run(release_session=True)
            return
        if not failed_only and not supplement_only:
            self.db.sessions.clear_session_run_data(session_id)
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
            failed_detailed = self.db.papers.list_failed_papers_detailed(session_id)
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
                student = {"id": group.student_id, "name": group.student_name} if group.student_id else self.db.students.find_student_by_name(group.student_name)
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
        execution_profile = getattr(
            getattr(self.llm_client, "settings", None),
            "policy_profile",
            None,
        )
        execution_snapshot = execution_snapshot_from_profile(execution_profile)
        worker_count = bounded_int(
            max_workers,
            execution_snapshot.max_in_flight,
            FULL_PAPER_WORKERS_MIN,
            min(
                FULL_PAPER_WORKERS_MAX,
                execution_snapshot.max_in_flight,
            ),
        )
        large_request_workers = worker_count
        rpm_limit = bounded_int(
            requests_per_minute,
            execution_snapshot.requests_per_minute,
            GRADING_RPM_MIN,
            GRADING_RPM_MAX,
        )
        batch_worker_count = bounded_int(
            max_workers,
            execution_snapshot.max_in_flight,
            HYBRID_INFLIGHT_WORKERS_MIN,
            min(
                HYBRID_INFLIGHT_WORKERS_MAX,
                execution_snapshot.max_in_flight,
            ),
        )
        rate_limiter = RequestPacer(rpm_limit)
        event_queue = queue.Queue()

        if total:
            yield {
                "event": "batch_grading_config",
                "total": total,
                "max_workers": worker_count,
                "large_request_workers": large_request_workers,
                "hybrid_inflight_workers": batch_worker_count,
                "requests_per_minute": rpm_limit,
                "grading_mode": resolved_grading_mode,
            }

        matched_records, batch_run_item_by_paper = yield from (
            self._classify_grading_candidates(
                matched_records,
                run_store=run_store,
                run=run,
                session_id=session_id,
                config_fingerprint=config_fingerprint,
                resume_run_id=resume_run_id or supplement_run_id,
            )
        )
        total = len(matched_records)
        existing_results_by_student = {}
        # Teacher-locked questions are graded by the AI like everything
        # else; locks only win when results are merged and persisted.
        # This map is reserved for the failed_only retry path below.
        skipped_questions_by_student: dict[int, set[str]] = {}
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
                        skipped_questions_by_student.setdefault(
                            student_id,
                            set(),
                        )
                    else:
                        skipped_questions_by_student.setdefault(
                            student_id,
                            set(),
                        ).update({
                            detail["question_id"]
                            for detail in details_rows
                            if major_question_id(
                                grader.rubric,
                                detail["question_id"],
                            )
                            not in affected_major_ids
                        })

        marked_paper_ids: list[int] = []
        for idx, (paper_id, group, student_id) in enumerate(matched_records, start=1):
            if _cancel_requested():
                for marked_paper_id in marked_paper_ids:
                    _restore_paper_after_cancel(
                        marked_paper_id,
                        batch_run_item_by_paper.get(marked_paper_id),
                    )
                yield _finish_cancelled_run(release_session=True)
                return
            self.papers.update_exam_paper_status(paper_id, "grading")
            if paper_id in batch_run_item_by_paper:
                run_store.mark_grading(batch_run_item_by_paper[paper_id])
            marked_paper_ids.append(paper_id)
            yield {
                "event": "grading_started",
                "paper_id": paper_id,
                "student_name": group.student_name,
                "current": idx,
                "total": total,
            }
            if _cancel_requested():
                for marked_paper_id in marked_paper_ids:
                    _restore_paper_after_cancel(
                        marked_paper_id,
                        batch_run_item_by_paper.get(marked_paper_id),
                    )
                yield _finish_cancelled_run(release_session=True)
                return

        completed = 0
        try:
            objective_completed = 0
            subjective_completed = 0
            subjective_total = 0

            def _batch_progress(event: dict[str, Any]) -> None:
                nonlocal objective_completed, subjective_completed, subjective_total
                stage = str(event.get("stage") or "")
                qid = str(event.get("question_id") or "?")
                batch_index = event.get("batch_index")
                unit = "位"
                if stage.startswith("objective"):
                    if stage.endswith(("_done", "_error")):
                        objective_completed += 1
                    objective_total = max(1, total)
                    progress = 0.08 + 0.40 * min(
                        1.0,
                        objective_completed / objective_total,
                    )
                    if stage.endswith("_error"):
                        msg = (
                            f"正在识别客观题，已处理 "
                            f"{objective_completed}/{objective_total} 份答卷；"
                            "本次未识别项将转教师复核"
                        )
                    else:
                        msg = (
                            f"正在识别客观题，已处理 "
                            f"{objective_completed}/{objective_total} 份答卷"
                        )
                    public_stage = "grading_objective"
                elif stage.endswith("_summary"):
                    subjective_total = int(event.get("total_batches") or 0)
                    progress = 0.48
                    msg = (
                        f"客观题识别完成；正在准备主观题批改，"
                        f"共 {subjective_total} 个批次"
                    )
                    public_stage = "grading_subjective"
                elif stage.endswith(("_done", "_error")):
                    subjective_completed += 1
                    progress = 0.48 + 0.40 * min(
                        1.0,
                        subjective_completed / max(1, subjective_total),
                    )
                    suffix = "；失败项将转教师复核" if stage.endswith("_error") else ""
                    msg = (
                        f"正在批改主观题，已完成 "
                        f"{subjective_completed}/{max(1, subjective_total)} 个批次"
                        f"（当前 {qid}，第 {batch_index} {unit}）{suffix}"
                    )
                    public_stage = "grading_subjective"
                else:
                    return
                event_queue.put(
                    {
                        "event": "grading_progress",
                        "stage": public_stage,
                        "progress": progress,
                        "message": msg,
                    }
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_ai_batch_grading,
                    session_id=session_id,
                    paper_groups=[group for _, group, _ in matched_records],
                    answer_regions=answer_regions,
                    rubric=grader.rubric,
                    answer_key=grader.answer_key,
                    llm_client=self.llm_client,
                    grading_model=grading_model,
                    output_root=get_path_manager().outputs_dir / "ai_grading",
                    objective_batch_size=bounded_int(
                        os.getenv("LLM_OBJECTIVE_BATCH_SIZE"),
                        OBJECTIVE_BATCH_SIZE_MAX,
                        OBJECTIVE_BATCH_SIZE_MIN,
                        OBJECTIVE_BATCH_SIZE_MAX,
                    ),
                    batch_workers=batch_worker_count,
                    rate_limiter=rate_limiter,
                    progress_callback=_batch_progress,
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
                for marked_paper_id in marked_paper_ids:
                    _restore_paper_after_cancel(
                        marked_paper_id,
                        batch_run_item_by_paper.get(marked_paper_id),
                    )
                yield _finish_cancelled_run(release_session=True)
                return
            fallback_items_by_key = _fallback_items_by_paper_key(batch_run.fallback_items)
            paper_key_by_paper_id = {
                paper_id: entry.paper_key
                for entry, (paper_id, _, _) in zip(batch_run.paper_entries, matched_records)
            }
        except Exception as exc:
            if _cancel_requested():
                for marked_paper_id in marked_paper_ids:
                    _restore_paper_after_cancel(
                        marked_paper_id,
                        batch_run_item_by_paper.get(marked_paper_id),
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
                if paper_id in batch_run_item_by_paper:
                    run_store.set_item_status(
                        batch_run_item_by_paper[paper_id],
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
            self.db.sessions.finish_session_run(session_id, "completed")
            progress = self.db.papers.get_session_progress(session_id)
            yield {"event": "session_completed", "progress": progress}
            return

        for result_index, (paper_id, group, student_id) in enumerate(matched_records):
            if _cancel_requested():
                for pending_paper_id, _, _ in matched_records[result_index:]:
                    _restore_paper_after_cancel(
                        pending_paper_id,
                        batch_run_item_by_paper.get(pending_paper_id),
                    )
                yield _finish_cancelled_run(release_session=True)
                return
            completed += 1
            retry_existing = existing_results_by_student.get(student_id) if failed_only else None
            try:
                paper_key = paper_key_by_paper_id.get(paper_id, "")
                fallback_items = fallback_items_by_key.get(paper_key, [])
                result = batch_run.results_by_paper_key[paper_key]
                _merge_teacher_score_locks_into_result(
                    result,
                    teacher_score_locks_by_student.get(student_id, {}),
                    grader.rubric,
                    scan_batch_id=scan_batch_id,
                )

                atomic_major_retry = bool(retry_existing and retry_existing["atomic_retry"])
                if atomic_major_retry:
                    existing = retry_existing
                    affected_major_ids = existing["affected_major_ids"]
                    if existing["replace_all_details"] and not affected_major_ids:
                        raise ValueError("Structured incomplete result has no rubric major questions to retry safely")
                    merged_raw_json = dict(existing["raw_json"])
                    merged_raw_json.pop("hybrid_batch_fallback", None)
                    merged_raw_json.pop("detail_metadata", None)
                    if result.raw_json:
                        merged_raw_json.update(result.raw_json)
                    merged_raw_json = merge_detail_metadata(existing["raw_json"], merged_raw_json,
                        [d["question_id"] for d in existing["details"] if existing["replace_all_details"]
                         or major_question_id(grader.rubric, d["question_id"]) in affected_major_ids]
                        + [d.question_id for d in result.grading_details])
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
                    merged_raw_json["grading_completeness"] = audit_grading_details(grader.rubric, merged_details)
                    result.needs_human_review = details_require_review(merged_details, merged_raw_json)
                    result.grading_details = merged_details
                    result.raw_json = merged_raw_json

                elif failed_only and retry_existing:
                    existing = retry_existing
                    merged_raw_json = dict(existing["raw_json"])
                    merged_raw_json.pop("hybrid_batch_fallback", None)
                    merged_raw_json.pop("detail_metadata", None)
                    if result.raw_json:
                        merged_raw_json.update(result.raw_json)
                    merged_raw_json = merge_detail_metadata(existing["raw_json"], merged_raw_json,
                        [d.question_id for d in result.grading_details])

                    new_details_map = {d.question_id: d for d in result.grading_details}
                    merged_details = []
                    for old_detail in existing["details"]:
                        merged_details.append(new_details_map.pop(old_detail["question_id"], _detail_from_row(old_detail)))
                    merged_details.extend(new_details_map.values())
                    result.total_score = existing["total_score"]
                    result.student_score = sum(detail.score_awarded for detail in merged_details)
                    merged_raw_json["grading_completeness"] = audit_grading_details(grader.rubric, merged_details)
                    result.needs_human_review = details_require_review(merged_details, merged_raw_json)
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
                            batch_run_item_by_paper.get(pending_paper_id),
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
                        scan_batch_id=scan_batch_id,
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
                            scan_batch_id=scan_batch_id,
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
                if paper_id in batch_run_item_by_paper:
                    run_store.set_item_status(
                        batch_run_item_by_paper[paper_id],
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
            except Exception as exc:
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
                if paper_id in batch_run_item_by_paper:
                    run_store.set_item_status(
                        batch_run_item_by_paper[paper_id],
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

        run_paused = bool(getattr(batch_run, "paused", False))
        if run_store is not None and run is not None:
            try:
                run_store.finish(run.run_token, "paused" if run_paused else "completed")
            except Exception:
                pass
        self.db.sessions.finish_session_run(session_id, "completed")
        progress = self.db.papers.get_session_progress(session_id)
        if run_paused:
            yield {
                "event": "session_paused",
                "run_id": run.id if run is not None else None,
                "progress": progress,
            }
        else:
            yield {"event": "session_completed", "progress": progress}
        return

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

    def _classify_grading_candidates(
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
        self.db.sessions.replace_session_attendance(session_id, rows)


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


def _question_sort_key(value: str) -> list[tuple[int, Any]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", str(value or ""))
    ]


def _merge_teacher_score_locks_into_result(
    result: GradingResult,
    locks_by_question_id: dict[str, dict[str, Any]],
    rubric: dict[str, Any],
    *,
    scan_batch_id: str | None,
) -> None:
    """Complete an in-memory batch result with pre-existing teacher scores."""

    if not locks_by_question_id:
        return
    details_by_question_id = {
        str(detail.question_id): detail
        for detail in result.grading_details
    }
    locked_question_ids: set[str] = set()
    for question_id, lock in locks_by_question_id.items():
        previous = details_by_question_id.get(question_id)
        details_by_question_id[question_id] = QuestionGradingDetail(
            question_id=question_id,
            score_awarded=float(lock["score_awarded"]),
            deduction_reason=(
                str(lock["deduction_reason"])
                if lock.get("deduction_reason") is not None
                else "教师人工批改已确认"
            ),
            knowledge_id=(
                previous.knowledge_id if previous is not None else "UNKNOWN"
            ),
            error_category="教师已确认",
            error_summary="teacher_score_locked",
            confidence_score=None,
            knowledge_ids=(
                list(previous.knowledge_ids) if previous is not None else []
            ),
            secondary_errors=(
                list(previous.secondary_errors) if previous is not None else []
            ),
            # The AI still graded this question; keep its score beside the
            # teacher final so exports can compare both tracks.
            ai_score_awarded=(
                detail_ai_score(previous) if previous is not None else None
            ),
        )
        locked_question_ids.add(question_id)

    merged_details = sorted(
        details_by_question_id.values(),
        key=lambda detail: _question_sort_key(detail.question_id),
    )
    completeness = audit_grading_details(rubric, merged_details)
    raw_json = dict(result.raw_json or {})
    raw_json["grading_completeness"] = completeness
    raw_json["teacher_score_locks"] = {
        "scan_batch_id": str(scan_batch_id or ""),
        "question_ids": sorted(
            locked_question_ids,
            key=_question_sort_key,
        ),
    }
    fallback = raw_json.get("hybrid_batch_fallback")
    has_fallback = bool(
        isinstance(fallback, dict)
        and isinstance(fallback.get("items"), list)
        and fallback["items"]
    )
    result.grading_details = merged_details
    result.student_score = sum(
        float(detail.score_awarded) for detail in merged_details
    )
    result.needs_human_review = (
        completeness["status"] != "complete"
        or has_fallback
        or any(
            detail.question_id not in locked_question_ids
            and (
                (
                    detail.confidence_score is not None
                    and detail.confidence_score < review_confidence_threshold(is_objective_detail(detail))
                )
                or str(detail.error_category or "") == "需复核"
            )
            for detail in merged_details
        )
    )
    result.raw_json = raw_json


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
    decided_issue_ids: set[str] = set()
    result = [replace(group) for group in analysis.groups]

    for decision in manual_decisions:
        group_source_label = str(decision.get("group_source_label") or "")
        if group_source_label or decision.get("group_front_image"):
            candidates = [g for g in result if g.source_label == group_source_label and (
                not decision.get("group_front_image") or str(g.front_image) == str(decision["group_front_image"])
            )]
            if len(candidates) != 1:
                raise ValueError("答卷匹配目标不唯一或已变化，请重新检查扫描归属。")
            group = candidates[0]
            if decision.get("action") in {"invalid", "pending"}:
                result.remove(group)
                continue
            if str(decision.get("action") or "") != "match":
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
                detected_class_name=issue.detected_class_name,
                match_method="manual",
                match_score=1.0,
            )
        )

    # Compatible scan snapshots may store only a name. Resolve it once, before
    # duplicate checks and attendance, and never choose among homonyms.
    for group in result:
        if group.student_id is None:
            candidates = [s for s in students if str(s.get("name") or "").strip() == group.student_name.strip()]
            if len(candidates) != 1:
                raise ValueError("答卷归属存在冲突，请返回扫描预检按学号和班级确认学生。")
            group.student_id = int(candidates[0]["id"])

    final = preflight_match_status({"groups": [
        {
            "id": str(index), "student_id": group.student_id,
            "student_name": group.student_name, "detected_name": group.detected_name,
            "detected_class_name": group.detected_class_name, "match_method": group.match_method,
            "match_score": group.match_score,
            "front_media_url": str(group.front_image), "back_media_url": str(group.back_image),
        }
        for index, group in enumerate(result)
    ]}, students)
    if final["conflicts"]:
        # Before attendance, paper registration, score writes or model requests.
        raise ValueError("答卷归属存在冲突，请返回扫描预检处理后再批改。")
    analysis.issues = [issue for issue in analysis.issues if issue.issue_id not in decided_issue_ids]
    return result


def _apply_manual_decisions(
    analysis: ScanAnalysis,
    manual_decisions: list[dict],
    students: list[dict],
) -> list[ExamPaperGroup]:
    return apply_scan_manual_decisions(analysis, manual_decisions, students)


def _attach_enhanced_paths(analysis: ScanAnalysis, output_dir: Path) -> None:
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
    if is_standard_pdf_page(path):
        return path
    try:
        return enhance_image_file(path, output_dir)
    except Exception:
        return path


def _clear_enhanced_paths(analysis: ScanAnalysis) -> None:
    for group in analysis.groups:
        group.enhanced_front_image = None
        group.enhanced_back_image = None
    for issue in analysis.issues:
        issue.enhanced_front_image = None
        issue.enhanced_back_image = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
