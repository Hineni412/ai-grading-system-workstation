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

from ai_grader import AIGrader
from db_manager import DBManager
from evidence_atlas import EvidenceAtlasBuilder
from image_preprocessor import enhance_image_file
from llm_client import LLMClient
from path_manager import get_path_manager
from hybrid_batch_grading_service import run_hybrid_batch_grading
from scanner import STUDENT_NAME_REGION_ALIASES, ExamPaperGroup, ScanAnalysis, Scanner, student_name_region_from_regions


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
    ) -> Iterable[dict]:
        if not self.db.is_template_ready(session_id):
            raise ValueError("当前会话尚未完成模板题框映射确认，请先在“评分依据与会话”页完成模板配置")
        session = self.db.get_grading_session(session_id)
        rubric = _load_rubric_for_preflight(rubric_path)
        _validate_session_exam_identity(session, rubric)
        answer_regions = self.db.list_answer_regions(session_id)
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
        target_question_ids = _target_question_ids_from_regions(answer_regions)
        grader = AIGrader(
            rubric_path=rubric_path,
            answer_key_path=answer_key_path,
            llm_client=self.llm_client,
            grading_model=grading_model,
            target_question_ids=target_question_ids,
            answer_regions=answer_regions,
        )

        if not self.db.try_start_session_run(session_id):
            raise RuntimeError("当前考试批改已有运行中的批改任务，请等待完成后再启动")
        self.db.clear_session_run_data(session_id)

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

        matched_records: list[tuple[int, ExamPaperGroup, int]] = []

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
                for _, (paper_id, group, _) in enumerate(matched_records, start=1):
                    completed += 1
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
                try:
                    paper_key = paper_key_by_paper_id.get(paper_id, "")
                    fallback_items = fallback_items_by_key.get(paper_key, [])
                    result = batch_run.results_by_paper_key[paper_key]
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


def _target_question_ids_from_regions(regions: list[dict]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for region in regions:
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        if qid in STUDENT_NAME_REGION_ALIASES:
            continue
        if not qid or qid in seen:
            continue
        seen.add(qid)
        result.append(qid)
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
    try:
        return enhance_image_file(path, output_dir)
    except Exception:  # noqa: BLE001
        return path


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
