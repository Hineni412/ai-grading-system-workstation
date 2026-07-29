from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Protocol

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from grading_service import GradingService
from path_manager import resolve_stored_file_path


class GradingServiceFactory(Protocol):
    def __call__(
        self,
        db_manager: GradingRepositoryAccess,
        llm_client: Any,
        question_bank_db_path: Path | None = None,
    ) -> GradingService:
        ...


def run_grading_job(
    *,
    db: GradingRepositoryAccess,
    session_id: int,
    exams_dir: Path,
    session_work_dir: Path,
    data_root: Path,
    question_bank_db_path: Path | None,
    llm_client_factory: Callable[[], Any],
    service_factory: GradingServiceFactory = GradingService,
    report: Callable[[float, str, str], None] | None = None,
    grading_mode: str = "full_paper",
    scan_batch_id: str | None = None,
    failed_only: bool = False,
    enhance_images: bool = True,
    max_workers: int | None = None,
    requests_per_minute: int | None = None,
    resume_run_id: int | None = None,
    supplement_only: bool = False,
    supplement_run_id: int | None = None,
    raise_if_cancelled: Callable[[], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    db = as_grading_repositories(db)
    session_id = int(session_id)
    _check_cancelled(raise_if_cancelled)
    session = db.get_grading_session(session_id)
    if session is None:
        raise ValueError(f"session not found: {session_id}")

    session_work_dir = Path(session_work_dir)
    analysis_path = session_work_dir / "scan_analysis_latest.json"
    scan_analysis = None if failed_only else _read_required_json(analysis_path)
    manual_decisions = None if failed_only else _read_current_manual_decisions(session_work_dir, analysis_path)

    service = service_factory(db, llm_client_factory(), question_bank_db_path=question_bank_db_path)
    summary = {
        "graded": 0,
        "failed": 0,
        "skipped": 0,
        "conflicts": 0,
        "scan_issues": 0,
        "unmatched": 0,
    }
    state = "running"
    last_event: dict[str, Any] = {}
    _report(report, 0.03, "grading_starting", "正在建立本次批改队列")

    for event in service.run_session_grading(
        session_id=session_id,
        exams_dir=Path(exams_dir),
        rubric_path=resolve_stored_file_path(session.get("rubric_path"), data_root=Path(data_root)),
        answer_key_path=resolve_stored_file_path(session.get("answer_key_path"), data_root=Path(data_root)),
        ocr_model=_model_for_client(service, "ocr_model"),
        grading_model=_model_for_client(service, "grading_model"),
        scan_analysis=scan_analysis,
        manual_decisions=manual_decisions,
        enhance_images=bool(enhance_images),
        max_workers=max_workers,
        requests_per_minute=requests_per_minute,
        grading_mode=_normalize_grading_mode(grading_mode),
        scan_batch_id=(
            str(scan_batch_id).strip()
            if scan_batch_id is not None and str(scan_batch_id).strip()
            else None
        ),
        failed_only=bool(failed_only),
        resume_run_id=resume_run_id,
        supplement_only=bool(supplement_only),
        supplement_run_id=supplement_run_id,
        should_cancel=should_cancel,
    ):
        event_type = str(event.get("event") or "")
        last_event = dict(event)
        if event_type == "graded":
            summary["graded"] += 1
            _report_saving_progress(report, event)
        elif event_type == "grading_failed":
            summary["failed"] += 1
            _report_saving_progress(report, event)
        elif event_type == "grading_progress":
            _report(
                report,
                float(event.get("progress") or 0.08),
                str(event.get("stage") or "grading_running"),
                str(event.get("message") or "正在批改答卷"),
            )
        elif event_type == "paper_skipped":
            summary["skipped"] += 1
        elif event_type == "paper_conflict":
            summary["conflicts"] += 1
        elif event_type == "scan_issue":
            summary["scan_issues"] += 1
        elif event_type == "paper_unmatched":
            summary["unmatched"] += 1
        elif event_type == "session_paused":
            state = "paused"
            _report(report, 1.0, "grading_paused", "本次批改已安全暂停")
        elif event_type == "session_completed":
            state = "completed"
            _report(report, 1.0, "grading_completed", "本次批改已完成")
        elif event_type == "session_cancelled":
            state = "cancelled"
            _report(report, 1.0, "grading_cancelled", "本次批改已取消")
            _check_cancelled(raise_if_cancelled)
        elif event_type == "batch_grading_config":
            total = int(event.get("total") or 0)
            _report(
                report,
                0.08,
                "grading_queue_ready",
                f"批改队列已建立，共 {total} 份答卷",
            )

    if state == "running":
        state = "finished"
        _report(report, 1.0, "grading_finished", "本次批改处理已结束")

    return {
        "session_id": session_id,
        "state": state,
        "summary": summary,
        "last_event": last_event,
    }


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{path.name} is required before grading")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _read_optional_json_list(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a JSON list")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _normalize_grading_mode(value: str) -> str:
    return "hybrid_batch" if str(value or "").strip() == "hybrid_batch" else "full_paper"


def _model_for_client(service: Any, attr_name: str) -> str | None:
    llm_client = getattr(service, "llm_client", None)
    settings = getattr(llm_client, "settings", None)
    value = getattr(settings, attr_name, None)
    return str(value) if value else None


def _report(
    report: Callable[[float, str, str], None] | None,
    progress: float,
    stage: str,
    detail: str,
) -> None:
    if report is not None:
        report(progress, stage, detail)


def _report_from_event(
    report: Callable[[float, str, str], None] | None,
    event: dict[str, Any],
    stage: str,
    detail: str,
) -> None:
    current = int(event.get("current") or 0)
    total = int(event.get("total") or 0)
    if total > 0:
        progress = max(0.08, min(0.98, current / total))
    else:
        progress = 0.5
    _report(report, progress, stage, detail)


def _report_saving_progress(
    report: Callable[[float, str, str], None] | None,
    event: dict[str, Any],
) -> None:
    current = int(event.get("current") or 0)
    total = int(event.get("total") or 0)
    ratio = current / total if total > 0 else 0.5
    progress = min(0.99, 0.88 + 0.11 * max(0.0, min(1.0, ratio)))
    _report(
        report,
        progress,
        "grading_saving",
        (
            f"正在保存成绩，已处理 {current}/{total} 份答卷"
            if total > 0
            else "正在保存批改成绩"
        ),
    )


def _check_cancelled(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _read_current_manual_decisions(session_work_dir: Path, analysis_path: Path) -> list[dict[str, Any]] | None:
    state_path = session_work_dir / "scan_decisions_state.json"
    if state_path.exists():
        if not analysis_path.exists():
            return None
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            identity = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
            decisions = state.get("internal_decisions") if isinstance(state, dict) else None
            if (
                isinstance(state, dict)
                and state.get("analysis_identity") == identity
                and isinstance(decisions, list)
            ):
                return [dict(item) for item in decisions if isinstance(item, dict)]
        except (OSError, ValueError):
            return None
        return None
    return _read_optional_json_list(session_work_dir / "scan_manual_decisions_latest.json")
