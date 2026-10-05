from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from backend.grading_workflow import effective_preflight_papers, preflight_match_status


class ScanGradingWorkspaceError(RuntimeError):
    """扫描批改工作区拒绝当前操作。"""


class FrozenUploadBatchError(ScanGradingWorkspaceError):
    """上传批次已冻结，不能再修改。"""


class UploadBatchRevisionError(ScanGradingWorkspaceError):
    """上传批次已被其他页面更新。"""


class InvalidScanUploadError(ScanGradingWorkspaceError):
    """上传文件类型、名称或摘要不符合要求。"""


class ScanUploadTooLargeError(ScanGradingWorkspaceError):
    """单个扫描文件超过允许大小。"""


class PendingScanIssuesError(ScanGradingWorkspaceError):
    """仍有异常卷，启动前尚未得到明确确认。"""


class ScanMatchConflictError(ScanGradingWorkspaceError):
    """答卷归属冲突必须先在扫描预检中解决。"""

    def __init__(self, message: str, conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []


class GradingConfigChangedError(ScanGradingWorkspaceError):
    """暂停或终态运行所用的批改配置已经变化。"""


class ActiveScanAnalysisError(ScanGradingWorkspaceError):
    """当前上传批次仍有预检任务在运行。"""


class ScanReplacementCleanupIncompleteError(ScanGradingWorkspaceError):
    """新答卷已生效，但旧文件清理仍需安全重试。"""


class LegacyGradingModeError(ScanGradingWorkspaceError):
    """旧批改方式的历史运行只能查看，不能继续执行。"""


LEGACY_GRADING_MODE_MESSAGE = "旧批改方式已停用，请用 AI 批改重新开始未完成的部分"


class ScanGradingWorkspace:
    """隐藏上传文件、manifest 与后续扫描状态的会话级模块。"""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    _allowed_uploads = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    _upload_signatures = {
        ".pdf": b"%PDF-",
        ".jpg": b"\xff\xd8\xff",
        ".jpeg": b"\xff\xd8\xff",
        ".png": b"\x89PNG\r\n\x1a\n",
    }

    def __init__(
        self,
        *,
        exams_root: Path,
        templates_root: Path,
        grading_db_path: Path | None = None,
        job_manager: Any | None = None,
        data_root: Path | None = None,
        replacement_storage_paths: Callable[[int], list[str]] | None = None,
        replacement_reset: Callable[[int], list[str]] | None = None,
        config_fingerprint_resolver: Callable[[int, str], str] | None = None,
        incomplete_result_counter: Callable[[int], int] | None = None,
        incomplete_item_counter: Callable[[int], int] | None = None,
        max_file_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.exams_root = Path(exams_root)
        self.templates_root = Path(templates_root)
        self.grading_db_path = Path(grading_db_path) if grading_db_path is not None else None
        self.job_manager = job_manager
        self.data_root = Path(data_root).resolve() if data_root is not None else None
        self.replacement_storage_paths = replacement_storage_paths
        self.replacement_reset = replacement_reset
        self.config_fingerprint_resolver = config_fingerprint_resolver
        self.incomplete_result_counter = incomplete_result_counter
        self.incomplete_item_counter = incomplete_item_counter
        self.max_file_bytes = int(max_file_bytes)

    def get_workspace(self, session_id: int) -> dict[str, Any]:
        with self._lock(session_id):
            self._recover_replacement_commit(session_id)
            manifest = self._load_or_create_manifest(session_id)
            scan_batch_id = str(manifest["batch_id"])
            return {
                "session_id": int(session_id),
                "upload_batch": self._public_batch(manifest),
                "replacement_batch": self._public_batch(
                    self._load_replacement_manifest(session_id)
                ) if self._replacement_manifest_path(session_id).exists() else None,
                "grading_run": self.get_grading_run(session_id),
                "grading_job": self._grading_job_summary(
                    session_id,
                    scan_batch_id,
                ),
                "scan_analysis_job": self._scan_analysis_job_summary(
                    session_id,
                    scan_batch_id,
                ),
            }

    def get_grading_run(self, session_id: int) -> dict[str, Any] | None:
        if self.grading_db_path is None:
            return None
        from backend.scan_grading.grading_run_store import GradingRunStore

        store = GradingRunStore(self.grading_db_path)
        run = store.latest(int(session_id))
        if run is None:
            return None
        manifest = self._load_or_create_manifest(session_id)
        if run.id <= int(manifest.get("run_floor_id") or 0):
            return None
        raw = store.counts(run.id)
        counts = {
            "graded": int(raw.get("graded", 0)),
            "grading": int(raw.get("grading", 0)),
            "pending": int(raw.get("pending", 0)),
            "skipped": int(raw.get("skipped", 0)),
            "failed": int(raw.get("failed", 0)),
            "conflict": int(raw.get("conflict", 0)),
        }
        counts["total"] = sum(
            int(raw.get(status, 0))
            for status in (
                "pending",
                "grading",
                "graded",
                "failed",
                "skipped_existing",
                "skipped_duplicate",
                "conflict",
            )
        )
        actions = {
            "running": ["pause", "cancel"],
            "pause_requested": ["cancel"],
            "paused": ["resume", "cancel"],
        }.get(run.state, [])
        if run.state in {"completed", "failed"} and (
            counts["failed"] > 0 or self._has_retryable_incomplete_results(session_id)
        ):
            actions.append("retry_failed")
        if run.state in {"completed", "failed"}:
            actions.append("supplement_new_matches")
        projected_state = run.state
        active_job = self._active_grading_job(session_id)
        if run.state in {"running", "pause_requested"} and active_job is None:
            projected_state = "interrupted"
            actions = ["resume", "cancel"]
        elif run.state in {"completed", "failed"} and active_job is not None:
            projected_state = "starting"
            actions = []
        control = self._read_grading_control(session_id)
        if int(control.get("run_id") or 0) == run.id and control.get("cancel_requested"):
            control_job = self._grading_control_job(control)
            if control.get("cancel_confirmed") or (
                control_job is not None and control_job.status == "cancelled"
            ):
                projected_state = "cancelled"
                actions = []
            elif run.state in {"completed", "failed"}:
                pass
            elif (
                active_job is not None
                and control_job is not None
                and active_job.id == control_job.id
                and control_job.cancel_requested
            ):
                projected_state = "cancel_requested"
                actions = []
            elif (
                run.state in {"running", "pause_requested"}
                and active_job is None
            ):
                projected_state = "interrupted"
                actions = ["resume", "cancel"]
        if str(run.grading_mode) != "ai":
            actions = [action for action in actions if action == "cancel"]
        result: dict[str, Any] = {
            "run_id": run.id,
            "mode": run.grading_mode,
            "state": projected_state,
            "counts": counts,
            "allowed_actions": actions,
            "incomplete_result_count": 0,
            "incomplete_item_count": 0,
        }
        if run.state in {"completed", "failed"}:
            result.update(self._incomplete_result_summary(session_id))
        if active_job is not None:
            job = active_job
            result.update(
                {
                    "job_id": job.id,
                    "job_status": job.status,
                    "progress": job.progress,
                    "started_at": job.started_at,
                    "updated_at": job.updated_at,
                }
            )
        return result

    def _grading_control_job(self, control: dict[str, Any]):
        if self.job_manager is None:
            return None
        try:
            job_id = int(control.get("job_id") or 0)
        except (TypeError, ValueError):
            return None
        return self.job_manager.get(job_id) if job_id > 0 else None

    def _active_grading_job(self, session_id: int):
        if self.job_manager is None:
            return None
        jobs, _total = self.job_manager.list(
            session_id=int(session_id),
            job_types=("grading_run",),
            statuses=("queued", "running"),
            limit=1,
        )
        return jobs[0] if jobs else None

    def _grading_job(self, session_id: int, batch_id: str):
        if self.job_manager is None:
            return None
        jobs, _total = self.job_manager.list(
            session_id=int(session_id),
            job_types=("grading_run",),
            limit=100,
        )
        return next(
            (
                job
                for job in jobs
                if str(job.payload.get("scan_batch_id") or "") == str(batch_id)
            ),
            None,
        )

    def _grading_job_summary(
        self,
        session_id: int,
        batch_id: str,
    ) -> dict[str, Any] | None:
        job = self._grading_job(session_id, batch_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "updated_at": job.updated_at,
            "cancel_requested": job.cancel_requested,
            "scan_batch_id": str(batch_id),
        }

    def _scan_analysis_job(self, session_id: int, batch_id: str, *, active_only: bool = False):
        if self.job_manager is None:
            return None
        statuses = ("queued", "running") if active_only else ()
        jobs, _total = self.job_manager.list(
            session_id=int(session_id),
            job_types=("scan_analysis",),
            statuses=statuses,
            limit=100,
        )
        return next(
            (
                job
                for job in jobs
                if str(job.payload.get("scan_batch_id") or "") == str(batch_id)
            ),
            None,
        )

    def _active_scan_analysis_job(
        self,
        session_id: int,
        batch_id: str | None = None,
    ):
        if batch_id is not None:
            return self._scan_analysis_job(session_id, batch_id, active_only=True)
        if self.job_manager is None:
            return None
        jobs, _total = self.job_manager.list(
            session_id=int(session_id),
            job_types=("scan_analysis",),
            statuses=("queued", "running"),
            limit=1,
        )
        return jobs[0] if jobs else None

    def _scan_analysis_job_summary(
        self,
        session_id: int,
        batch_id: str,
    ) -> dict[str, Any] | None:
        job = self._scan_analysis_job(session_id, batch_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "updated_at": job.updated_at,
            "cancel_requested": job.cancel_requested,
            "scan_batch_id": str(batch_id),
        }

    def submit_scan_analysis(
        self,
        session_id: int,
        payload: dict[str, Any],
    ) -> Any:
        """Bind preflight submission to the current upload batch atomically."""
        if self.job_manager is None:
            raise ScanGradingWorkspaceError("scan analysis job manager is unavailable")
        with self._lock(session_id):
            from backend.files.session_originals import originals_state, ScanSourcesReleased
            if originals_state(self.data_root or self.exams_root.parent, session_id) != "complete":
                raise ScanSourcesReleased()
            clean_payload = dict(payload)
            clean_payload["session_id"] = int(session_id)
            if self.upload_batch_exists(session_id):
                scan_dir, scan_batch_id = self.frozen_scan_input(session_id)
                clean_payload["exams_dir"] = str(scan_dir)
                clean_payload["scan_batch_id"] = scan_batch_id
            return self.job_manager.submit_unique_active(
                "scan_analysis",
                clean_payload,
            )

    def pause_grading_run(self, session_id: int, run_id: int) -> dict[str, Any]:
        if self.grading_db_path is None:
            raise ScanGradingWorkspaceError("grading run store is unavailable")
        from backend.scan_grading.grading_run_store import GradingRunStore

        store = GradingRunStore(self.grading_db_path)
        run = store.get_run(int(run_id))
        if run is None or run.session_id != int(session_id):
            raise ScanGradingWorkspaceError("grading run was not found")
        if run.state != "running" or not store.request_pause(int(session_id)):
            raise ScanGradingWorkspaceError("grading run cannot be paused")
        summary = self.get_grading_run(session_id)
        if summary is None:
            raise ScanGradingWorkspaceError("grading run was not found")
        return summary

    def record_cancel_request(
        self,
        session_id: int,
        run_id: int,
        job_id: int | None,
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        if self.grading_db_path is None:
            raise ScanGradingWorkspaceError("grading run store is unavailable")
        from backend.scan_grading.grading_run_store import GradingRunStore

        store = GradingRunStore(self.grading_db_path)
        run = store.get_run(int(run_id))
        if run is None or run.session_id != int(session_id):
            raise ScanGradingWorkspaceError("grading run was not found")
        if run.state in {"completed", "failed"}:
            summary = self.get_grading_run(session_id)
            if summary is None:
                raise ScanGradingWorkspaceError("grading run was not found")
            return summary
        if run.state not in {"running", "pause_requested", "paused"}:
            raise ScanGradingWorkspaceError("grading run cannot be cancelled")
        if confirmed:
            store.finish(run.run_token, "failed")
        self._atomic_write_json(
            self._grading_control_path(session_id),
            {
                "run_id": int(run_id),
                "job_id": int(job_id) if job_id is not None else None,
                "cancel_requested": True,
                "cancel_confirmed": bool(confirmed),
                "updated_at": self._now(),
            },
        )
        summary = self.get_grading_run(session_id)
        if summary is None:
            raise ScanGradingWorkspaceError("grading run was not found")
        return summary

    def prepare_resume(self, session_id: int, run_id: int) -> dict[str, Any]:
        self._require_original_pages(session_id)
        run, counts = self._require_run(session_id, run_id)
        if str(run.grading_mode) != "ai":
            raise LegacyGradingModeError(LEGACY_GRADING_MODE_MESSAGE)
        control = self._read_grading_control(session_id)
        if (
            int(control.get("run_id") or 0) == run.id
            and control.get("cancel_requested")
            and (
                control.get("cancel_confirmed")
                or (
                    (control_job := self._grading_control_job(control)) is not None
                    and control_job.status == "cancelled"
                )
            )
        ):
            raise ScanGradingWorkspaceError("cancelled grading run cannot resume")
        self._require_current_config(session_id, run)
        if run.state in {"running", "pause_requested"} and self._active_grading_job(session_id) is None:
            from backend.scan_grading.grading_run_store import GradingRunStore

            GradingRunStore(self.grading_db_path).finish(run.run_token, "paused")
        elif run.state != "paused":
            raise ScanGradingWorkspaceError("grading run cannot resume")
        if counts.get("pending", 0) <= 0:
            raise ScanGradingWorkspaceError("grading run cannot resume")
        payload = {
            "session_id": int(session_id),
            "grading_mode": run.grading_mode,
            "failed_only": False,
            "enhance_images": True,
            "resume_run_id": run.id,
        }
        return payload

    def _require_current_config(self, session_id: int, run: Any) -> None:
        if self.config_fingerprint_resolver is None:
            raise GradingConfigChangedError("grading configuration cannot be verified")
        try:
            current = self.config_fingerprint_resolver(
                int(session_id),
                str(run.grading_mode),
            )
        except Exception as exc:
            raise GradingConfigChangedError(
                "grading configuration cannot be verified"
            ) from exc
        if str(current) != str(run.config_fingerprint):
            raise GradingConfigChangedError("grading configuration changed")

    def _has_retryable_incomplete_results(self, session_id: int) -> bool:
        return self._incomplete_result_summary(session_id)["incomplete_result_count"] > 0

    def _incomplete_result_summary(self, session_id: int) -> dict[str, int]:
        summary = {"incomplete_result_count": 0, "incomplete_item_count": 0}
        if self.incomplete_result_counter is not None:
            try:
                summary["incomplete_result_count"] = int(
                    self.incomplete_result_counter(int(session_id))
                )
            except Exception:
                summary["incomplete_result_count"] = 0
        if self.incomplete_item_counter is not None:
            try:
                summary["incomplete_item_count"] = int(
                    self.incomplete_item_counter(int(session_id))
                )
            except Exception:
                summary["incomplete_item_count"] = 0
        return summary

    def prepare_failed_retry(self, session_id: int, run_id: int) -> dict[str, Any]:
        self._require_original_pages(session_id)
        run, counts = self._require_run(session_id, run_id)
        if str(run.grading_mode) != "ai":
            raise LegacyGradingModeError(LEGACY_GRADING_MODE_MESSAGE)
        if self._run_was_cancelled(session_id, run.id):
            raise ScanGradingWorkspaceError("cancelled grading run cannot retry")
        if run.state not in {"completed", "failed"} or (
            counts.get("failed", 0) <= 0
            and not self._has_retryable_incomplete_results(session_id)
        ):
            raise ScanGradingWorkspaceError("grading run has no retryable failures")
        payload = {
            "session_id": int(session_id),
            "grading_mode": run.grading_mode,
            "failed_only": True,
            "enhance_images": True,
            "source_run_id": run.id,
        }
        return payload

    def prepare_supplement(self, session_id: int, run_id: int) -> dict[str, Any]:
        self._require_original_pages(session_id)
        run, _counts = self._require_run(session_id, run_id)
        if str(run.grading_mode) != "ai":
            raise LegacyGradingModeError(LEGACY_GRADING_MODE_MESSAGE)
        if self._run_was_cancelled(session_id, run.id):
            raise ScanGradingWorkspaceError("cancelled grading run cannot supplement")
        if run.state not in {"completed", "failed"}:
            raise ScanGradingWorkspaceError("grading run cannot be supplemented")
        self._require_current_config(session_id, run)
        manifest = self._load_or_create_manifest(session_id)
        if manifest.get("state") != "frozen":
            raise ScanGradingWorkspaceError("scan upload batch is not frozen")
        if self._active_scan_analysis_job(session_id, str(manifest["batch_id"])) is not None:
            raise ScanGradingWorkspaceError("scan preflight is still active")
        self.require_resolved_matches(self.get_preflight(session_id))
        payload = {
            "session_id": int(session_id),
            "grading_mode": run.grading_mode,
            "failed_only": False,
            "supplement_only": True,
            "supplement_run_id": run.id,
            "enhance_images": True,
        }
        return payload

    def submit_resume(self, session_id: int, run_id: int) -> Any:
        if self.job_manager is None:
            raise ScanGradingWorkspaceError("grading job manager is unavailable")
        with self._lock(session_id):
            payload = self.prepare_resume(session_id, run_id)
            manifest = self._load_or_create_manifest(session_id)
            payload["scan_batch_id"] = str(manifest["batch_id"])
            if manifest.get("state") == "frozen":
                payload["exams_dir"] = str(self.frozen_scan_dir(session_id))
            job = self.job_manager.submit_unique_active("grading_run", payload)
            self._clear_grading_control(session_id, run_id)
            return job

    def submit_failed_retry(self, session_id: int, run_id: int) -> Any:
        if self.job_manager is None:
            raise ScanGradingWorkspaceError("grading job manager is unavailable")
        with self._lock(session_id):
            payload = self.prepare_failed_retry(session_id, run_id)
            manifest = self._load_or_create_manifest(session_id)
            payload["scan_batch_id"] = str(manifest["batch_id"])
            return self.job_manager.submit_unique_active("grading_run", payload)

    def submit_supplement(self, session_id: int, run_id: int) -> Any:
        if self.job_manager is None:
            raise ScanGradingWorkspaceError("grading job manager is unavailable")
        with self._lock(session_id):
            payload = self.prepare_supplement(session_id, run_id)
            manifest = self._load_or_create_manifest(session_id)
            payload["scan_batch_id"] = str(manifest["batch_id"])
            payload["exams_dir"] = str(self.frozen_scan_dir(session_id))
            return self.job_manager.submit_unique_active("grading_run", payload)

    def prepare_start(
        self,
        session_id: int,
        *,
        grading_mode: str,
        upload_revision: int,
        decision_revision: int,
        confirm_pending_issues: bool,
        enhance_images: bool,
        max_workers: int | None,
        requests_per_minute: int | None,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            self._require_original_pages(session_id)
            current_run = self.get_grading_run(session_id)
            if current_run is not None:
                raise ScanGradingWorkspaceError(
                    "the current scan batch already has a grading run"
                )
            manifest = self._load_or_create_manifest(session_id)
            if manifest.get("state") != "frozen":
                raise ScanGradingWorkspaceError("scan upload batch is not frozen")
            if int(upload_revision) != int(manifest["revision"]):
                raise UploadBatchRevisionError("scan upload batch revision changed")
            preflight = self.get_preflight(session_id)
            analysis, _identity = self._read_analysis(session_id)
            current_template = self._require_current_preflight_template(
                session_id,
                analysis=analysis,
            )
            if int(decision_revision) != int(preflight["revision"]):
                raise UploadBatchRevisionError("preflight decision revision changed")
            self.require_resolved_matches(preflight)
            if int(preflight["pending_issue_count"]) > 0 and not confirm_pending_issues:
                raise PendingScanIssuesError("pending scan issues require confirmation")
            if self.grading_db_path is None:
                raise GradingConfigChangedError(
                    "grading configuration binding cannot be verified"
                )
            from backend.repositories.grading_database import open_grading_repositories

            repositories = open_grading_repositories(self.grading_db_path)
            current_session = repositories.sessions.get_grading_session(int(session_id))
            stored_template = repositories.templates.get_session_template(int(session_id))
            config_revision = str(
                analysis.get("config_revision") or ""
            ).strip()
            if (
                current_session is None
                or stored_template is None
                or int(stored_template.get("id") or 0)
                != int(current_template.template_id)
                or not config_revision
            ):
                raise GradingConfigChangedError(
                    "grading configuration binding cannot be verified"
                )
            if str(grading_mode) != "ai":
                raise LegacyGradingModeError(LEGACY_GRADING_MODE_MESSAGE)
            payload: dict[str, Any] = {
                "session_id": int(session_id),
                "grading_mode": "ai",
                "failed_only": False,
                "enhance_images": bool(enhance_images),
                "config_revision": config_revision,
                "expected_rubric_path": str(
                    current_session.get("rubric_path") or ""
                ),
                "expected_answer_key_path": str(
                    current_session.get("answer_key_path") or ""
                ),
                "expected_template_id": int(current_template.template_id),
                "expected_front_template_path": str(
                    stored_template.get("front_template_path") or ""
                ),
                "expected_back_template_path": str(
                    stored_template.get("back_template_path") or ""
                ),
            }
            if max_workers is not None:
                payload["max_workers"] = int(max_workers)
            if requests_per_minute is not None:
                payload["requests_per_minute"] = int(requests_per_minute)
            return payload

    def submit_start(
        self,
        session_id: int,
        *,
        grading_mode: str,
        upload_revision: int,
        decision_revision: int,
        confirm_pending_issues: bool,
        enhance_images: bool,
        max_workers: int | None,
        requests_per_minute: int | None,
    ) -> Any:
        """Validate the current batch and create its only active job atomically."""
        if self.job_manager is None:
            raise ScanGradingWorkspaceError("grading job manager is unavailable")
        from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock

        with self._lock(session_id):
            with get_answer_region_session_lock(self._session_dir(session_id)):
                payload = self.prepare_start(
                    session_id,
                    grading_mode=grading_mode,
                    upload_revision=upload_revision,
                    decision_revision=decision_revision,
                    confirm_pending_issues=confirm_pending_issues,
                    enhance_images=enhance_images,
                    max_workers=max_workers,
                    requests_per_minute=requests_per_minute,
                )
                manifest = self._load_or_create_manifest(session_id)
                payload["scan_batch_id"] = str(manifest["batch_id"])
                payload["exams_dir"] = str(self.frozen_scan_dir(session_id))
                from backend.jobs.store import GradingSubmissionChangedError

                try:
                    job, created = self.job_manager.submit_idempotent_scan_start(
                        payload
                    )
                except GradingSubmissionChangedError as exc:
                    raise GradingConfigChangedError(
                        "grading configuration or template changed"
                    ) from exc
                if not created:
                    raise ScanGradingWorkspaceError(
                        "grading submission was already accepted"
                    )
                return job

    def start_new_upload_batch(self, session_id: int) -> dict[str, Any]:
        with self._lock(session_id):
            current_manifest = self._load_or_create_manifest(session_id)
            if self._active_scan_analysis_job(session_id) is not None:
                raise ActiveScanAnalysisError(
                    "active scan analysis must be resolved first"
                )
            current_run = self.get_grading_run(session_id)
            active_job = self._active_grading_job(session_id)
            if (
                current_run is not None
                and current_run["state"]
                in {
                    "running",
                    "pause_requested",
                    "paused",
                    "interrupted",
                    "cancel_requested",
                }
            ) or active_job is not None:
                raise ScanGradingWorkspaceError("active grading run must be resolved first")
            previous_manifest = current_manifest
            if not self._manifest_path(session_id).exists():
                self._write_manifest(session_id, previous_manifest)
            run_floor_id = int(previous_manifest.get("run_floor_id") or 0)
            if self.grading_db_path is not None:
                from backend.scan_grading.grading_run_store import GradingRunStore

                latest_run = GradingRunStore(self.grading_db_path).latest(int(session_id))
                if latest_run is not None:
                    run_floor_id = max(run_floor_id, latest_run.id)
            manifest = {
                "batch_id": uuid4().hex,
                "revision": 0,
                "state": "draft",
                "files": [],
                "frozen_at": None,
                "run_floor_id": run_floor_id,
            }
            history_dir = (
                self._session_dir(session_id)
                / "scan_history"
                / str(previous_manifest["batch_id"])
            )
            archive_pairs = []
            for name in (
                "scan_analysis_latest.json",
                "scan_decisions_state.json",
                "scan_manual_decisions_latest.json",
            ):
                source = self._session_dir(session_id) / name
                if source.exists():
                    archive_pairs.append((source, history_dir / name))
            if any(target.exists() for _source, target in archive_pairs):
                raise ScanGradingWorkspaceError("scan batch archive already exists")
            transition = {
                "version": 2,
                "previous_batch_id": str(previous_manifest["batch_id"]),
                "previous_manifest": previous_manifest,
                "next_manifest": manifest,
                "archive_files": [source.name for source, _target in archive_pairs],
            }
            self._atomic_write_json(self._transition_path(session_id), transition)
            try:
                if archive_pairs:
                    history_dir.mkdir(parents=True, exist_ok=True)
                for source, target in archive_pairs:
                    os.replace(source, target)
                self._write_manifest(session_id, manifest)
                self._transition_path(session_id).unlink()
            except Exception as exc:
                try:
                    committed = self._recover_batch_transition(session_id)
                except ScanGradingWorkspaceError:
                    raise
                if committed:
                    return self._public_batch(manifest)
                raise ScanGradingWorkspaceError("scan batch archive failed") from exc
            return self._public_batch(manifest)

    def _require_run(self, session_id: int, run_id: int):
        if self.grading_db_path is None:
            raise ScanGradingWorkspaceError("grading run store is unavailable")
        from backend.scan_grading.grading_run_store import GradingRunStore

        store = GradingRunStore(self.grading_db_path)
        run = store.get_run(int(run_id))
        if run is None or run.session_id != int(session_id):
            raise ScanGradingWorkspaceError("grading run was not found")
        manifest = self._load_or_create_manifest(session_id)
        latest = store.latest(int(session_id))
        if (
            run.id <= int(manifest.get("run_floor_id") or 0)
            or latest is None
            or latest.id != run.id
        ):
            raise ScanGradingWorkspaceError("grading run does not belong to the current batch")
        return run, store.counts(run.id)

    def upload_batch_exists(self, session_id: int) -> bool:
        return self._manifest_path(session_id).is_file()

    def add_upload(
        self,
        session_id: int,
        *,
        filename: str,
        media_type: str,
        content_sha256: str,
        source: BinaryIO,
        replacement: bool = False,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            manifest = (
                self._load_or_create_replacement_manifest(session_id)
                if replacement
                else self._load_or_create_manifest(session_id)
            )
            self._require_draft(manifest)
            digest = str(content_sha256 or "").strip().lower()
            safe_name = Path(str(filename or "")).name
            suffix = Path(safe_name).suffix.lower()
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or self._allowed_uploads.get(suffix) != str(media_type or "").lower()
            ):
                raise InvalidScanUploadError("scan upload metadata is invalid")
            duplicate = next(
                (item for item in manifest["files"] if item["sha256"] == digest),
                None,
            )

            batch_dir = self._batch_dir(session_id, manifest["batch_id"])
            files_dir = batch_dir / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            storage_name = f"{digest}{suffix}"
            target = files_dir / storage_name
            temporary_path: Path | None = None
            calculated = hashlib.sha256()
            header = bytearray()
            size = 0
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=files_dir,
                    prefix=".upload-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        if len(header) < 8:
                            header.extend(chunk[: 8 - len(header)])
                        calculated.update(chunk)
                        size += len(chunk)
                        if size > self.max_file_bytes:
                            raise ScanUploadTooLargeError("scan upload is too large")
                        temporary.write(chunk)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                if calculated.hexdigest() != digest:
                    raise ScanGradingWorkspaceError("uploaded content digest does not match")
                if not bytes(header).startswith(self._upload_signatures[suffix]):
                    raise InvalidScanUploadError(
                        "scan upload content does not match its declared type"
                    )
                if duplicate is not None:
                    return {"duplicate": True, "file": self._public_file(duplicate)}
                os.replace(temporary_path, target)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

            item = {
                "id": uuid4().hex,
                "name": safe_name,
                "media_type": str(media_type or "application/octet-stream"),
                "size_bytes": size,
                "sha256": digest,
                "storage_name": storage_name,
                "added_at": self._now(),
            }
            manifest["files"].append(item)
            manifest["revision"] += 1
            self._write_target_manifest(session_id, manifest, replacement=replacement)
            return {"duplicate": False, "file": self._public_file(item)}

    def freeze_uploads(self, session_id: int, *, expected_revision: int) -> dict[str, Any]:
        with self._lock(session_id):
            manifest = self._load_or_create_manifest(session_id)
            self._require_draft(manifest)
            if int(expected_revision) != int(manifest["revision"]):
                raise UploadBatchRevisionError("upload batch revision changed")
            if not manifest["files"]:
                raise ScanGradingWorkspaceError("upload batch is empty")
            manifest["state"] = "frozen"
            manifest["frozen_at"] = self._now()
            manifest["revision"] += 1
            self._write_manifest(session_id, manifest)
            return self._public_batch(manifest)

    def remove_upload(
        self,
        session_id: int,
        upload_id: str,
        *,
        expected_revision: int,
        replacement: bool = False,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            manifest = (
                self._load_replacement_manifest(session_id)
                if replacement
                else self._load_or_create_manifest(session_id)
            )
            self._require_draft(manifest)
            self._require_revision(manifest, expected_revision)
            removed = next(
                (item for item in manifest["files"] if item["id"] == str(upload_id)),
                None,
            )
            if removed is None:
                raise ScanGradingWorkspaceError("upload file was not found")
            manifest["files"] = [
                item for item in manifest["files"] if item["id"] != str(upload_id)
            ]
            manifest["revision"] += 1
            self._write_target_manifest(session_id, manifest, replacement=replacement)
            self._stored_file(session_id, manifest, removed).unlink(missing_ok=True)
            return self._public_batch(manifest)

    def clear_uploads(
        self,
        session_id: int,
        *,
        expected_revision: int,
        replacement: bool = False,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            manifest = (
                self._load_replacement_manifest(session_id)
                if replacement
                else self._load_or_create_manifest(session_id)
            )
            self._require_draft(manifest)
            self._require_revision(manifest, expected_revision)
            removed = list(manifest["files"])
            manifest["files"] = []
            manifest["revision"] += 1
            self._write_target_manifest(session_id, manifest, replacement=replacement)
            for item in removed:
                self._stored_file(session_id, manifest, item).unlink(missing_ok=True)
            return self._public_batch(manifest)

    def begin_replacement_upload(self, session_id: int) -> dict[str, Any]:
        with self._lock(session_id):
            self._require_no_active_scan_work(session_id)
            path = self._replacement_manifest_path(session_id)
            manifest = (
                self._load_replacement_manifest(session_id)
                if path.exists()
                else self._new_manifest()
            )
            if not path.exists():
                self._write_target_manifest(session_id, manifest, replacement=True)
            return self._public_batch(manifest)

    def cancel_replacement_upload(self, session_id: int) -> None:
        with self._lock(session_id):
            path = self._replacement_manifest_path(session_id)
            if not path.exists():
                return
            manifest = self._load_replacement_manifest(session_id)
            shutil.rmtree(
                self._batch_dir(session_id, str(manifest["batch_id"])),
                ignore_errors=True,
            )
            path.unlink(missing_ok=True)

    def commit_replacement_upload(
        self,
        session_id: int,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            self._require_no_active_scan_work(session_id)
            candidate = self._load_replacement_manifest(session_id)
            self._require_draft(candidate)
            self._require_revision(candidate, expected_revision)
            if not candidate["files"]:
                raise ScanGradingWorkspaceError("replacement upload batch is empty")
            if self.replacement_reset is None:
                raise ScanGradingWorkspaceError("replacement reset is unavailable")
            old = self._load_or_create_manifest(session_id)
            pending_storage_paths = (
                self._normalize_cleanup_paths(
                    self.replacement_storage_paths(session_id)
                )
                if self.replacement_storage_paths is not None
                else None
            )
            candidate = {
                **candidate,
                "state": "frozen",
                "frozen_at": self._now(),
                "revision": int(candidate["revision"]) + 1,
            }
            journal = {
                "version": 1,
                "old_batch_id": str(old["batch_id"]),
                "candidate": candidate,
            }
            if pending_storage_paths is not None:
                journal["pending_storage_paths"] = pending_storage_paths
            self._atomic_write_json(self._replacement_commit_path(session_id), journal)
            self._write_target_manifest(session_id, candidate, replacement=True)
            return self._finish_replacement_commit(session_id, journal)

    def _require_no_active_scan_work(self, session_id: int) -> None:
        if self._active_scan_analysis_job(session_id) is not None:
            raise ActiveScanAnalysisError("active scan analysis must be resolved first")
        current_run = self.get_grading_run(session_id)
        active_job = self._active_grading_job(session_id)
        if (
            current_run is not None
            and current_run["state"] in {
                "running",
                "pause_requested",
                "paused",
                "interrupted",
                "cancel_requested",
            }
        ) or active_job is not None:
            raise ScanGradingWorkspaceError("active grading run must be resolved first")

    def _recover_replacement_commit(self, session_id: int) -> None:
        path = self._replacement_commit_path(session_id)
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ScanGradingWorkspaceError("replacement commit recovery failed")
        self._finish_replacement_commit(session_id, payload)

    def _finish_replacement_commit(
        self,
        session_id: int,
        journal: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = journal.get("candidate")
        if not isinstance(candidate, dict):
            raise ScanGradingWorkspaceError("replacement commit is invalid")
        stored_paths = journal.get("pending_storage_paths")
        reset_completed = False
        if stored_paths is None:
            if self.replacement_storage_paths is not None:
                stored_paths = self.replacement_storage_paths(session_id)
            else:
                stored_paths = (
                    self.replacement_reset(session_id)
                    if self.replacement_reset
                    else []
                )
                reset_completed = True
            stored_paths = self._normalize_cleanup_paths(stored_paths)
            journal = {
                **journal,
                "pending_storage_paths": stored_paths,
            }
            self._atomic_write_json(
                self._replacement_commit_path(session_id),
                journal,
            )
        else:
            stored_paths = self._normalize_cleanup_paths(stored_paths)
        if not reset_completed and self.replacement_reset is not None:
            self.replacement_reset(session_id)
        self._write_manifest(session_id, candidate)
        session_dir = self._session_dir(session_id)
        try:
            self._replacement_manifest_path(session_id).unlink(missing_ok=True)
            for name in (
                "scan_analysis_latest.json",
                "scan_decisions_state.json",
                "scan_manual_decisions_latest.json",
                "grading_control_state.json",
            ):
                (session_dir / name).unlink(missing_ok=True)
            self._remove_tree(session_dir / "scan_history")
            old_batch_id = str(journal.get("old_batch_id") or "")
            if re.fullmatch(r"[0-9a-f]{32}", old_batch_id):
                self._remove_tree(
                    self._batch_dir(session_id, old_batch_id)
                )
            for raw_path in stored_paths:
                self._unlink_owned_data_file(raw_path)
        except OSError as exc:
            raise ScanReplacementCleanupIncompleteError(
                "replacement cleanup is incomplete"
            ) from exc
        try:
            from backend.files.session_originals import receipt_path
            receipt_path(self.data_root or self.exams_root.parent, session_id).unlink(missing_ok=True)
            self._replacement_commit_path(session_id).unlink(missing_ok=True)
        except OSError as exc:
            raise ScanReplacementCleanupIncompleteError(
                "replacement cleanup is incomplete"
            ) from exc
        return self._public_batch(candidate)

    @staticmethod
    def _normalize_cleanup_paths(paths: object) -> list[str]:
        if not isinstance(paths, list):
            raise ScanGradingWorkspaceError(
                "replacement cleanup plan is invalid"
            )
        return [str(path) for path in paths if str(path)]

    @staticmethod
    def _remove_tree(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return

    def _unlink_owned_data_file(self, raw_path: str) -> None:
        if self.data_root is None or not raw_path:
            return
        path = Path(str(raw_path))
        candidates = [path] if path.is_absolute() else [self.data_root.parent / path, self.data_root / path]
        delete_error: OSError | None = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError as exc:
                delete_error = exc
                continue
            if not resolved.is_relative_to(self.data_root):
                continue
            try:
                if not resolved.exists():
                    continue
                if not resolved.is_file():
                    raise OSError("replacement cleanup target is not a file")
                resolved.unlink()
                return
            except OSError as exc:
                delete_error = exc
        if delete_error is not None:
            raise delete_error

    def frozen_scan_dir(self, session_id: int) -> Path:
        with self._lock(session_id):
            manifest = self._load_or_create_manifest(session_id)
            if manifest.get("state") != "frozen":
                raise ScanGradingWorkspaceError("scan upload batch is not frozen")
            return self._batch_dir(session_id, manifest["batch_id"]) / "files"

    def frozen_scan_input(self, session_id: int) -> tuple[Path, str]:
        with self._lock(session_id):
            manifest = self._load_or_create_manifest(session_id)
            if manifest.get("state") != "frozen":
                raise ScanGradingWorkspaceError("scan upload batch is not frozen")
            return (
                self._batch_dir(session_id, manifest["batch_id"]) / "files",
                str(manifest["batch_id"]),
            )

    def require_preflight_config_revision(
        self,
        session_id: int,
        expected_revision: str,
    ) -> None:
        """Reject a preflight created for another grading configuration.

        Older preflight files do not have this binding and are intentionally
        treated as stale once this contract is active.
        """
        clean_revision = str(expected_revision or "").strip()
        if not clean_revision:
            raise GradingConfigChangedError(
                "grading configuration revision is unavailable"
            )
        with self._lock(session_id):
            analysis, _identity = self._read_analysis(session_id)
            if str(analysis.get("config_revision") or "") != clean_revision:
                raise GradingConfigChangedError(
                    "scan preflight grading configuration changed"
                )
            self._require_current_preflight_template(
                session_id,
                analysis=analysis,
            )

    def _require_current_preflight_template(
        self,
        session_id: int,
        *,
        analysis: dict[str, Any] | None = None,
    ) -> Any:
        if self.grading_db_path is None:
            raise GradingConfigChangedError(
                "session template binding cannot be verified"
            )
        if analysis is None:
            analysis, _identity = self._read_analysis(session_id)
        expected_fingerprint = str(
            analysis.get("template_fingerprint") or ""
        ).strip()
        expected_role = str(
            analysis.get("template_first_page_role") or ""
        ).strip()
        try:
            expected_template_id = int(analysis.get("template_id"))
        except (TypeError, ValueError) as exc:
            raise GradingConfigChangedError(
                "scan preflight template binding is unavailable"
            ) from exc
        if (
            not expected_fingerprint
            or expected_role not in {"front", "back"}
        ):
            raise GradingConfigChangedError(
                "scan preflight template binding is unavailable"
            )

        from backend.repositories.grading_database import open_grading_repositories
        from backend.exam_intake.template_upload_service import TemplateUploadError, TemplateUploadService

        try:
            current = TemplateUploadService(self.templates_root).load_current(
                db=open_grading_repositories(self.grading_db_path),
                session_id=int(session_id),
            )
        except (FileNotFoundError, OSError, TemplateUploadError) as exc:
            raise GradingConfigChangedError(
                "session template binding cannot be verified"
            ) from exc
        if (
            not current.is_confirmed
            or current.regions_snapshot_pending
            or current.template_id != expected_template_id
            or current.template_fingerprint != expected_fingerprint
            or current.first_page_role != expected_role
        ):
            raise GradingConfigChangedError(
                "scan preflight template changed"
            )
        return current

    def get_preflight(self, session_id: int, *, read_only: bool = False) -> dict[str, Any]:
        with self._lock(session_id):
            if read_only:
                try:
                    manifest = json.loads(self._manifest_path(session_id).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ScanGradingWorkspaceError("scan upload batch is unavailable") from exc
                if not isinstance(manifest, dict):
                    raise ScanGradingWorkspaceError("scan upload batch is invalid")
            else:
                manifest = self._load_or_create_manifest(session_id)
            if manifest.get("state") != "frozen":
                raise ScanGradingWorkspaceError("scan upload batch is not frozen")
            analysis, identity = self._read_analysis(session_id)
            state = self._read_decision_state(session_id, identity)
            analysis_groups = [
                item
                for item in analysis.get("groups", [])
                if isinstance(item, dict)
            ]
            analysis_issues = [
                item
                for item in analysis.get("issues", [])
                if isinstance(item, dict)
            ]
            groups = [
                self._public_group(session_id, item)
                for item in analysis_groups
            ]
            issues = [
                self._public_issue(session_id, item)
                for item in analysis_issues
            ]
            absent = [
                {
                    key: item.get(key)
                    for key in ("id", "name", "student_code", "class_name")
                    if item.get(key) is not None
                }
                for item in analysis.get("absent_students", [])
                if isinstance(item, dict)
            ]
            front_page_parity = str(
                analysis.get("front_page_parity") or "odd"
            )
            if front_page_parity not in {"odd", "even"}:
                front_page_parity = "odd"
            first_page_role = str(
                analysis.get("template_first_page_role")
                or ("front" if front_page_parity == "odd" else "back")
            )
            if first_page_role not in {"front", "back"}:
                first_page_role = (
                    "front" if front_page_parity == "odd" else "back"
                )
            payload = {
                "scan_batch_id": str(manifest["batch_id"]),
                "revision": int(state["revision"]),
                "summary": {
                    "auto_matched": len(groups),
                    "issues": len(issues),
                    "absent_candidates": len(absent),
                    "total_pages": int(analysis.get("total_pages") or 0),
                },
                "page_assignment": {
                    "first_page_role": first_page_role,
                    "front_page_parity": front_page_parity,
                },
                "groups": groups,
                "issues": issues,
                "absent_students": absent,
                "warnings": [
                    "扫描预检有需要注意的信息"
                    for _ in analysis.get("warnings", [])
                ],
                "decisions": list(state.get("public_decisions", [])),
            }
            identity = analysis.get("identity")
            if isinstance(identity, dict):
                payload["identity"] = {
                    "method": str(identity.get("method") or ""),
                    "auto": int(identity.get("auto") or 0),
                    "needs_confirmation": int(identity.get("needs_confirmation") or 0),
                    "model_requests": int(identity.get("model_requests") or 0),
                }
            students = analysis.get("students")
            if self.grading_db_path is not None:
                from backend.repositories.grading_database import (
                    open_grading_repositories,
                )
                students = open_grading_repositories(self.grading_db_path).students.list_students()
            status = preflight_match_status(payload, students)
            payload["summary"].update(status["summary"])
            payload["match_conflicts"] = status["conflicts"]
            payload["pending_issue_count"] = status["pending_issue_count"]
            if students is not None:
                payload["absent_students"] = [
                    {key: student.get(key) for key in ("id", "name", "student_code", "class_name")}
                    for student in status["absent_students"]
                ]
                payload["summary"]["absent_candidates"] = len(payload["absent_students"])
            return payload

    @staticmethod
    def require_resolved_matches(preflight: dict[str, Any]) -> None:
        if preflight.get("match_conflicts"):
            raise ScanMatchConflictError("答卷归属存在冲突，请返回扫描预检处理后再批改。")

    def save_decisions(
        self,
        session_id: int,
        *,
        expected_revision: int,
        valid_student_ids: set[int],
        decisions: list[dict[str, Any]],
        allow_partial_matches: bool = False,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            analysis, identity = self._read_analysis(session_id)
            state = self._read_decision_state(session_id, identity)
            if int(expected_revision) != int(state["revision"]):
                raise UploadBatchRevisionError("preflight decision revision changed")
            group_by_id = {
                self._group_id(item): item
                for item in analysis.get("groups", [])
                if isinstance(item, dict)
            }
            issue_ids = {
                str(item.get("issue_id") or "")
                for item in analysis.get("issues", [])
                if isinstance(item, dict)
            }
            public_decisions: list[dict[str, Any]] = []
            internal_decisions: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for raw in decisions:
                target_type = str(raw.get("target_type") or "")
                target_id = str(raw.get("target_id") or "")
                action = str(raw.get("action") or "")
                key = (target_type, target_id)
                if key in seen:
                    raise ScanGradingWorkspaceError("preflight decision target is duplicated")
                seen.add(key)
                student_id = raw.get("student_id")
                if action == "match":
                    try:
                        student_id = int(student_id)
                    except (TypeError, ValueError) as exc:
                        raise ScanGradingWorkspaceError("matching decision requires a student") from exc
                    if student_id not in valid_student_ids:
                        raise ScanGradingWorkspaceError("matching student is not available")
                elif student_id is not None:
                    raise ScanGradingWorkspaceError("non-matching decision cannot include a student")

                if target_type == "group" and target_id in group_by_id and action in {"match", "invalid", "pending"}:
                    internal = {
                        "group_source_label": str(group_by_id[target_id].get("source_label") or ""),
                        "group_front_image": str(group_by_id[target_id].get("front_image") or ""),
                        "action": action,
                    }
                    if action == "match":
                        internal["student_id"] = student_id
                elif target_type == "issue" and target_id in issue_ids and action in {"pending", "invalid", "match"}:
                    internal = {"issue_id": target_id, "action": action}
                    if action == "match":
                        internal["student_id"] = student_id
                else:
                    raise ScanGradingWorkspaceError("preflight decision target or action is invalid")
                public = {
                    "target_type": target_type,
                    "target_id": target_id,
                    "action": action,
                }
                if action == "match":
                    public["student_id"] = student_id
                public_decisions.append(public)
                internal_decisions.append(internal)

            # The request is the complete decision set. Validate it together with
            # existing automatic assignments before either decision file is saved.
            snapshot = {
                "groups": [self._public_group(session_id, g) for g in group_by_id.values()],
                "issues": [self._public_issue(session_id, i) for i in analysis.get("issues", [])],
                "decisions": state.get("public_decisions", []),
            }
            old_conflicts = {
                c["student_id"]: {(t["target_type"], t["target_id"]) for t in c["targets"]}
                for c in preflight_match_status(snapshot)["conflicts"]
                if c["code"] == "scan_student_multiple_papers"
            }
            old_assignments = {(p["target_type"], p["target_id"]): p["student_id"]
                               for p in effective_preflight_papers(snapshot)}
            previous_decisions = {(d["target_type"], d["target_id"]): d for d in state.get("public_decisions", [])}
            candidates = {(d["target_type"], d["target_id"]): d for d in public_decisions}
            snapshot["decisions"] = public_decisions
            rejected_conflicts = []
            while True:
                conflicts = [c for c in preflight_match_status(snapshot)["conflicts"]
                             if not {(t["target_type"], t["target_id"]) for t in c["targets"]}
                             <= old_conflicts.get(c["student_id"], set())]
                if not conflicts:
                    break
                if not allow_partial_matches:
                    raise ScanMatchConflictError("同一学生被分配了多份答卷，本次匹配未保存。请核对已选学生；重复扫描可标为无效。", conflicts)
                rejected = set()
                for conflict in conflicts:
                    for target in conflict["targets"]:
                        key = (target["target_type"], target["target_id"])
                        decision = candidates.get(key)
                        if decision and decision["action"] == "match" and old_assignments.get(key) != decision["student_id"]:
                            rejected.add(key)
                if not rejected:
                    raise ScanMatchConflictError("答卷归属存在冲突，请核对相关答卷。", conflicts)
                rejected_conflicts.extend(c for c in conflicts if c not in rejected_conflicts)
                for key in rejected:
                    if key in previous_decisions:
                        candidates[key] = previous_decisions[key]
                    else:
                        candidates.pop(key)
                # Restoring a rejected change can expose another conflict in a
                # chain of reassignments; settle the entire set before saving.
                snapshot["decisions"] = list(candidates.values())
            if rejected_conflicts:
                if candidates == previous_decisions:
                    result = self.get_preflight(session_id)
                    result["ready_to_grade"] = result["summary"]["ready_to_grade"]
                else:
                    result = self.save_decisions(session_id, expected_revision=expected_revision,
                                                 valid_student_ids=valid_student_ids, decisions=list(candidates.values()))
                result["rejected_conflicts"] = rejected_conflicts
                return result

            next_state = {
                "analysis_identity": identity,
                "revision": int(state["revision"]) + 1,
                "public_decisions": public_decisions,
                "internal_decisions": internal_decisions,
                "updated_at": self._now(),
            }
            self._atomic_write_json(self._decision_state_path(session_id), next_state)
            self._atomic_write_json(
                self._session_dir(session_id) / "scan_manual_decisions_latest.json",
                internal_decisions,
            )
            preflight = self.get_preflight(session_id)
            return {
                "revision": next_state["revision"],
                "decisions": public_decisions,
                "pending_issue_count": preflight["pending_issue_count"],
                "ready_to_grade": preflight["summary"]["ready_to_grade"],
                "summary": preflight["summary"],
                "absent_students": preflight["absent_students"],
                "match_conflicts": preflight["match_conflicts"],
            }

    def resolve_preflight_media(self, session_id: int, media_ref: str) -> Path:
        with self._lock(session_id):
            parts = str(media_ref or "").split(":")
            if len(parts) != 3 or parts[2] not in {"front", "back"}:
                raise ScanGradingWorkspaceError("preflight media reference is invalid")
            target_type, target_id, side = parts
            analysis, _identity = self._read_analysis(session_id)
            item: dict[str, Any] | None = None
            if target_type == "group":
                item = next(
                    (
                        candidate
                        for candidate in analysis.get("groups", [])
                        if isinstance(candidate, dict) and self._group_id(candidate) == target_id
                    ),
                    None,
                )
            elif target_type == "issue":
                item = next(
                    (
                        candidate
                        for candidate in analysis.get("issues", [])
                        if isinstance(candidate, dict)
                        and str(candidate.get("issue_id") or "") == target_id
                    ),
                    None,
                )
            if item is None:
                raise ScanGradingWorkspaceError("preflight media was not found")
            raw_path = item.get(f"enhanced_{side}_image") or item.get(f"{side}_image")
            if not raw_path:
                raise ScanGradingWorkspaceError("preflight media was not found")
            path = Path(str(raw_path)).resolve()
            allowed_root = self.frozen_scan_dir(session_id).parent.resolve()
            if not path.is_file() or not path.is_relative_to(allowed_root):
                raise ScanGradingWorkspaceError("preflight media is outside the frozen batch")
            return path

    def _load_or_create_manifest(self, session_id: int) -> dict[str, Any]:
        self._recover_batch_transition(session_id)
        path = self._manifest_path(session_id)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        return self._new_manifest()

    def _new_manifest(self) -> dict[str, Any]:
        return {
            "batch_id": uuid4().hex,
            "revision": 0,
            "state": "draft",
            "files": [],
            "frozen_at": None,
            "run_floor_id": 0,
        }

    def _load_or_create_replacement_manifest(self, session_id: int) -> dict[str, Any]:
        path = self._replacement_manifest_path(session_id)
        if path.exists():
            return self._load_replacement_manifest(session_id)
        manifest = self._new_manifest()
        self._write_target_manifest(session_id, manifest, replacement=True)
        return manifest

    def _load_replacement_manifest(self, session_id: int) -> dict[str, Any]:
        path = self._replacement_manifest_path(session_id)
        if not path.exists():
            raise ScanGradingWorkspaceError("replacement upload batch was not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ScanGradingWorkspaceError("replacement upload batch is invalid")
        return payload

    def _write_target_manifest(
        self,
        session_id: int,
        manifest: dict[str, Any],
        *,
        replacement: bool,
    ) -> None:
        path = (
            self._replacement_manifest_path(session_id)
            if replacement
            else self._manifest_path(session_id)
        )
        self._atomic_write_json(path, manifest)

    def _write_manifest(self, session_id: int, manifest: dict[str, Any]) -> None:
        self._atomic_write_json(self._manifest_path(session_id), manifest)

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".scan-upload-batch-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _manifest_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "scan_upload_batch.json"

    def _replacement_manifest_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "scan_upload_replacement.json"

    def _replacement_commit_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "scan_upload_replacement_commit.json"

    def _transition_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "scan_batch_transition.json"

    def _recover_batch_transition(self, session_id: int) -> bool | None:
        transition_path = self._transition_path(session_id)
        if not transition_path.exists():
            return None
        try:
            transition = json.loads(transition_path.read_text(encoding="utf-8"))
            if not isinstance(transition, dict) or transition.get("version") != 2:
                raise ValueError("invalid transition")
            previous_batch_id = str(transition["previous_batch_id"])
            previous_manifest = transition["previous_manifest"]
            next_manifest = transition["next_manifest"]
            archive_files = transition["archive_files"]
            if (
                not re.fullmatch(r"[0-9a-f]{32}", previous_batch_id)
                or not isinstance(previous_manifest, dict)
                or str(previous_manifest.get("batch_id") or "")
                != previous_batch_id
                or not isinstance(next_manifest, dict)
                or not re.fullmatch(
                    r"[0-9a-f]{32}",
                    str(next_manifest.get("batch_id") or ""),
                )
                or str(next_manifest.get("batch_id")) == previous_batch_id
                or not isinstance(archive_files, list)
            ):
                raise ValueError("invalid transition")
            allowed_names = {
                "scan_analysis_latest.json",
                "scan_decisions_state.json",
                "scan_manual_decisions_latest.json",
            }
            names = [str(name) for name in archive_files]
            if len(names) != len(set(names)) or any(
                name not in allowed_names for name in names
            ):
                raise ValueError("invalid transition")
            manifest_path = self._manifest_path(session_id)
            if not manifest_path.exists():
                raise ValueError("missing manifest")
            current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(current_manifest, dict):
                raise ValueError("invalid manifest")
            if current_manifest == next_manifest:
                committed = True
            elif current_manifest == previous_manifest:
                committed = False
            else:
                raise ValueError("transition manifest conflict")
            history_dir = (
                self._session_dir(session_id)
                / "scan_history"
                / previous_batch_id
            )
            pairs = [
                (self._session_dir(session_id) / name, history_dir / name)
                for name in names
            ]
            if any(
                source.exists() == target.exists()
                for source, target in pairs
            ):
                raise ValueError("transition file locations are inconsistent")
            ordered_pairs = pairs if committed else list(reversed(pairs))
            for source, target in ordered_pairs:
                move_from, move_to = (
                    (source, target) if committed else (target, source)
                )
                if move_from.exists():
                    if move_to.exists():
                        raise ValueError("transition paths conflict")
                    move_to.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(move_from, move_to)
            transition_path.unlink()
            return committed
        except (KeyError, OSError, ValueError, TypeError) as exc:
            raise ScanGradingWorkspaceError(
                "scan batch transition recovery failed"
            ) from exc

    def _session_dir(self, session_id: int) -> Path:
        return self.templates_root / f"session_{int(session_id)}"

    def _decision_state_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "scan_decisions_state.json"

    def _grading_control_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "grading_control_state.json"

    def _read_grading_control(self, session_id: int) -> dict[str, Any]:
        path = self._grading_control_path(session_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _run_was_cancelled(self, session_id: int, run_id: int) -> bool:
        control = self._read_grading_control(session_id)
        if (
            int(control.get("run_id") or 0) != int(run_id)
            or not control.get("cancel_requested")
        ):
            return False
        if control.get("cancel_confirmed"):
            return True
        control_job = self._grading_control_job(control)
        return control_job is not None and control_job.status == "cancelled"

    def _clear_grading_control(self, session_id: int, run_id: int) -> None:
        path = self._grading_control_path(session_id)
        control = self._read_grading_control(session_id)
        if int(control.get("run_id") or 0) != int(run_id):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _batch_dir(self, session_id: int, batch_id: str) -> Path:
        return self.exams_root / f"session_{int(session_id)}" / "scan_batches" / str(batch_id)

    def _stored_file(
        self,
        session_id: int,
        manifest: dict[str, Any],
        item: dict[str, Any],
    ) -> Path:
        return self._batch_dir(session_id, manifest["batch_id"]) / "files" / item["storage_name"]

    def _read_analysis(self, session_id: int) -> tuple[dict[str, Any], str]:
        path = self._session_dir(session_id) / "scan_analysis_latest.json"
        if not path.exists():
            raise ScanGradingWorkspaceError("scan preflight is not available")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ScanGradingWorkspaceError("scan preflight is invalid")
        manifest = self._load_or_create_manifest(session_id)
        if str(payload.get("scan_batch_id") or "") != str(manifest["batch_id"]):
            raise ScanGradingWorkspaceError("scan preflight belongs to another batch")
        return payload, hashlib.sha256(raw).hexdigest()

    def _read_decision_state(self, session_id: int, identity: str) -> dict[str, Any]:
        path = self._decision_state_path(session_id)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("analysis_identity") == identity:
                    return payload
            except (OSError, ValueError):
                pass
        return {
            "analysis_identity": identity,
            "revision": 0,
            "public_decisions": [],
            "internal_decisions": [],
        }

    @staticmethod
    def _group_id(group: dict[str, Any]) -> str:
        source = f"{group.get('source_label') or ''}\0{group.get('front_image') or ''}"
        return f"group-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]}"

    def _public_group(self, session_id: int, group: dict[str, Any]) -> dict[str, Any]:
        group_id = self._group_id(group)
        return {
            "id": group_id,
            "source_label": str(group.get("source_label") or ""),
            "detected_name": str(group.get("detected_name") or ""),
            "detected_class_name": str(group.get("detected_class_name") or ""),
            "student_id": group.get("student_id"),
            "student_name": str(group.get("student_name") or ""),
            "match_method": str(group.get("match_method") or ""),
            "match_score": float(group.get("match_score") or 0),
            "front_media_url": self._media_url(session_id, "group", group_id, "front"),
            "back_media_url": self._media_url(session_id, "group", group_id, "back"),
        }

    def _public_issue(self, session_id: int, issue: dict[str, Any]) -> dict[str, Any]:
        issue_id = str(issue.get("issue_id") or "")
        result = {
            "id": issue_id,
            "issue_type": str(issue.get("issue_type") or "unknown"),
            "message": str(issue.get("message") or "扫描文件需要人工处理"),
            "source_label": str(issue.get("source_label") or ""),
            "detected_name": str(issue.get("detected_name") or ""),
            "detected_class_name": str(issue.get("detected_class_name") or ""),
            "suggested_student_id": issue.get("suggested_student_id"),
            "suggested_student_name": str(issue.get("suggested_student_name") or ""),
            "suggested_match_score": issue.get("suggested_match_score"),
            "suggested_students": [
                {
                    "student_id": int(item["student_id"]),
                    "student_name": str(item.get("student_name") or ""),
                    "class_name": str(item.get("class_name") or ""),
                    "score": float(item.get("score") or 0),
                }
                for item in issue.get("suggested_students") or []
                if isinstance(item, dict) and item.get("student_id") is not None
            ],
            "front_media_url": self._media_url(session_id, "issue", issue_id, "front"),
            "back_media_url": None,
        }
        if issue.get("back_image"):
            result["back_media_url"] = self._media_url(session_id, "issue", issue_id, "back")
        return result

    @staticmethod
    def _media_url(session_id: int, target_type: str, target_id: str, side: str) -> str:
        return f"/api/sessions/{int(session_id)}/scan/preflight/media/{target_type}:{target_id}:{side}"

    def _require_original_pages(self, session_id: int) -> None:
        from backend.files.session_originals import require_original_pages
        require_original_pages(self.data_root or self.exams_root.parent, session_id)

    @contextmanager
    def _lock(self, session_id: int):
        from backend.files.session_cleanup import session_lifecycle_guard
        key = f"{self.templates_root.resolve()}:{int(session_id)}"
        with self._locks_guard:
            lock = self._locks.setdefault(key, threading.RLock())
        with session_lifecycle_guard(session_id), lock:
            yield

    @staticmethod
    def _require_draft(manifest: dict[str, Any]) -> None:
        if manifest.get("state") != "draft":
            raise FrozenUploadBatchError("upload batch is frozen")

    @staticmethod
    def _require_revision(manifest: dict[str, Any], expected_revision: int) -> None:
        if int(expected_revision) != int(manifest["revision"]):
            raise UploadBatchRevisionError("upload batch revision changed")

    @staticmethod
    def _public_file(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "name": item["name"],
            "media_type": item["media_type"],
            "size_bytes": int(item["size_bytes"]),
            "sha256_prefix": str(item["sha256"])[:12],
            "added_at": item["added_at"],
        }

    def _public_batch(self, manifest: dict[str, Any]) -> dict[str, Any]:
        files = [self._public_file(item) for item in manifest.get("files", [])]
        return {
            "batch_id": manifest["batch_id"],
            "revision": int(manifest["revision"]),
            "state": manifest["state"],
            "files": files,
            "file_count": len(files),
            "total_bytes": sum(int(item["size_bytes"]) for item in files),
            "frozen_at": manifest.get("frozen_at"),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
