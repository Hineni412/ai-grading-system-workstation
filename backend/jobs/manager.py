from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import (
    GradingSessionBusyError,
    JobRecord,
    JobStore,
    QuestionBankSyncSessionBusyError,
)

JobHandler = Callable[["JobContext"], dict[str, Any] | None]

# 阅卷全流程任务使用独立线程池，避免被备课 OCR 等长任务占满共享池后
# 一直停在 queued（前端显示为「等待开始」）。
_INTERACTIVE_JOB_TYPES = frozenset({"grading_run", "scan_analysis"})


class UnsupportedJobTypeError(ValueError):
    pass


class ActiveJobExistsError(RuntimeError):
    pass


class JobCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: int
    job_type: str
    payload: dict[str, Any]
    store: JobStore
    question_bank_sync_submitter: (
        Callable[[dict[str, Any]], tuple[JobRecord, bool]] | None
    ) = None

    def report(self, progress: float, stage: str, detail: str = "") -> None:
        self.store.update_progress(
            self.job_id,
            progress=progress,
            stage=stage,
            detail=detail,
        )

    @property
    def cancel_requested(self) -> bool:
        return self.store.is_cancel_requested(self.job_id)

    def is_cancel_requested(self) -> bool:
        return self.store.is_cancel_requested(self.job_id)

    def raise_if_cancelled(self) -> None:
        if self.is_cancel_requested():
            raise JobCancellationRequested(
                f"job {self.job_id} cancellation requested"
            )

    def submit_question_bank_sync(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        if self.question_bank_sync_submitter is None:
            raise RuntimeError("question-bank sync submission is unavailable")
        return self.question_bank_sync_submitter(dict(payload))


class JobManager:
    def __init__(
        self,
        store: JobStore,
        *,
        max_workers: int = 2,
        cleanup_interrupted: bool = True,
        interrupted_input_root: Path | None = None,
        question_bank_db_path: Path | None = None,
    ) -> None:
        self.store = store
        self._handlers: dict[str, JobHandler] = {}
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))
        self._interactive_executor = ThreadPoolExecutor(max_workers=2)
        self._futures: dict[int, Future[None]] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        self._config_lock_root = (
            Path(interrupted_input_root).resolve(strict=False)
            if interrupted_input_root is not None
            else None
        )
        if cleanup_interrupted:
            interrupted_sync_owners = (
                self.store.interrupted_question_bank_sync_owners()
            )
            if question_bank_db_path is not None and interrupted_sync_owners:
                from question_bank.services.source_question_link_service import (
                    SourceQuestionLinkService,
                )

                SourceQuestionLinkService(
                    Path(question_bank_db_path)
                ).discard_automatic_links_for_interrupted_syncs(
                    interrupted_sync_owners
                )
            owned_input_ids = (
                self.store.interrupted_owned_config_input_ids()
                if interrupted_input_root is not None
                else set()
            )
            protected_input_ids: set[str] = set()
            if interrupted_input_root is not None:
                from .config_generation import (
                    preserve_interrupted_config_generation_checkpoints,
                )

                protected_input_ids = preserve_interrupted_config_generation_checkpoints(
                    interrupted_input_root,
                    self.store,
                )
            self.store.fail_interrupted_jobs()
            if interrupted_input_root is not None:
                from .config_generation import (
                    cleanup_consumed_config_retry_artifacts,
                    discard_config_generation_input,
                )

                for input_id in owned_input_ids - protected_input_ids:
                    discard_config_generation_input(interrupted_input_root, input_id)
                cleanup_consumed_config_retry_artifacts(
                    interrupted_input_root,
                    self.store,
                )

    def register(self, job_type: str, handler: JobHandler) -> None:
        clean_type = str(job_type or "").strip()
        if not clean_type:
            raise ValueError("job_type must be nonblank")
        if not callable(handler):
            raise TypeError("job handler must be callable")
        if clean_type in self._handlers:
            raise ValueError(f"duplicate job type: {clean_type}")
        self._handlers[clean_type] = handler

    def submit(self, job_type: str, payload: dict[str, Any] | None = None) -> JobRecord:
        clean_type = str(job_type or "").strip()
        handler = self._handlers.get(clean_type)
        if handler is None:
            raise UnsupportedJobTypeError(f"unsupported job type: {clean_type}")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            clean_payload = dict(payload or {})
            with self._config_submission_guard(clean_type, clean_payload):
                if clean_type == "config_generation":
                    job = self.store.create_claimed_config_job(clean_payload)
                else:
                    job = self.store.create_job(clean_type, clean_payload)
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job

    def start_existing(self, job_id: int) -> JobRecord:
        """Schedule one atomically pre-created queued Job.

        Deep modules use this after committing their own metadata and the Job row
        in one database transaction. The Job payload remains the execution seam;
        no business body is copied into the manager.
        """
        clean_job_id = int(job_id)
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job = self.store.get_job(clean_job_id)
            if job is None:
                raise ValueError("pre-created job was not found")
            if job.status != "queued":
                return job
            handler = self._handlers.get(job.job_type)
            if handler is None:
                raise UnsupportedJobTypeError(
                    f"unsupported job type: {job.job_type}"
                )
            if clean_job_id in self._futures:
                return job
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job

    def submit_idempotent_export(self, job_type: str, payload: dict[str, Any]) -> tuple[JobRecord, bool]:
        if job_type not in self._handlers:
            raise UnsupportedJobTypeError(f"unsupported job type: {job_type}")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job, created = self.store.create_idempotent_export_job(job_type, payload)
        if created:
            try:
                self.start_existing(job.id)
            except Exception:
                current = self.store.get_job(job.id)
                if current is not None and current.status == "queued":
                    self.store.finish(job.id, "failed", "job scheduling failed")
                raise
        return job, created

    def submit_unique_active(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        """Create one active job of this type per session as one locked operation."""
        clean_type = str(job_type or "").strip()
        handler = self._handlers.get(clean_type)
        if handler is None:
            raise UnsupportedJobTypeError(f"unsupported job type: {clean_type}")
        clean_payload = dict(payload or {})
        session_id = int(clean_payload.get("session_id") or 0)
        if session_id <= 0:
            raise ValueError("session_id must be positive")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            active, _total = self.store.list_jobs(
                session_id=session_id,
                job_types=(clean_type,),
                statuses=("queued", "running"),
                limit=1,
                offset=0,
            )
            if active:
                raise ActiveJobExistsError(
                    f"active {clean_type} job already exists for session {session_id}"
                )
            job = self.store.create_job(clean_type, clean_payload)
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job

    def submit_idempotent_scan_start(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("grading_run")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: grading_run")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            try:
                job, created = self.store.create_idempotent_scan_grading_start(payload)
            except GradingSessionBusyError as exc:
                raise ActiveJobExistsError(str(exc)) from exc
            if not created:
                return job, False
            future = self._schedule_locked(
                job, handler, error_message="grading job could not be scheduled"
            )
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_config(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("config_generation")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: config_generation")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            clean_payload = dict(payload)
            with self._config_submission_guard("config_generation", clean_payload):
                job, created = self.store.create_idempotent_config_job(clean_payload)
            if not created:
                return job, False
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_question_bank_sync(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("question_bank_sync")
        if handler is None:
            raise UnsupportedJobTypeError(
                "unsupported job type: question_bank_sync"
            )
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            try:
                job, created = (
                    self.store.create_idempotent_question_bank_sync_job(payload)
                )
            except QuestionBankSyncSessionBusyError as exc:
                raise ActiveJobExistsError(str(exc)) from exc
            if not created:
                return job, False
            future = self._schedule_locked(
                job, handler,
                error_message="question-bank sync job could not be scheduled",
            )
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_taxonomy_suggestion(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("taxonomy_suggestion")
        if handler is None:
            raise UnsupportedJobTypeError(
                "unsupported job type: taxonomy_suggestion"
            )
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job, created = (
                self.store.create_idempotent_taxonomy_suggestion_job(
                    payload
                )
            )
            if not created:
                return job, False
            future = self._schedule_locked(
                job, handler,
                error_message="taxonomy suggestion job could not be scheduled",
            )
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_skill_candidate(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("skill_candidate")
        if handler is None:
            raise UnsupportedJobTypeError(
                "unsupported job type: skill_candidate"
            )
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job, created = (
                self.store.create_idempotent_skill_candidate_job(
                    payload
                )
            )
            if not created:
                return job, False
            future = self._schedule_locked(
                job, handler,
                error_message="skill candidate job could not be scheduled",
            )
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_tagging_sync(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("tagging_sync")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: tagging_sync")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job, created = self.store.create_idempotent_tagging_sync_job(payload)
            if not created:
                return job, False
            future = self._schedule_locked(
                job, handler, error_message="tagging sync job could not be scheduled"
            )
        self._watch_completion(job.id, future)
        return job, True

    def submit_idempotent_question_repair(self, payload: dict[str, Any]) -> tuple[JobRecord, bool]:
        handler = self._handlers.get('question_bank_repair')
        if handler is None:
            raise UnsupportedJobTypeError('unsupported job type: question_bank_repair')
        with self._lock:
            if self._shutdown:
                raise RuntimeError('JobManager has shut down')
            job, created = self.store.create_idempotent_tagging_sync_job(payload, job_type='question_bank_repair')
            if not created:
                return job, False
            future = self._schedule_locked(job, handler, error_message='question repair could not be scheduled')
        self._watch_completion(job.id, future)
        return job, True

    def submit_config_retry(self, payload: dict[str, Any]) -> JobRecord:
        handler = self._handlers.get("config_generation")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: config_generation")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            clean_payload = dict(payload)
            with self._config_submission_guard("config_generation", clean_payload):
                job = self.store.create_config_retry_job(clean_payload)
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job

    def submit_idempotent_config_retry(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        handler = self._handlers.get("config_generation")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: config_generation")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            clean_payload = dict(payload)
            with self._config_submission_guard("config_generation", clean_payload):
                job, created = self.store.create_idempotent_config_retry_job(clean_payload)
            if not created:
                return job, False
            future = self._schedule_locked(job, handler)
        self._watch_completion(job.id, future)
        return job, True

    def _schedule_locked(
        self,
        job: JobRecord,
        handler: JobHandler,
        *,
        error_message: str | None = None,
    ) -> Future[None]:
        """Start and register a committed job while the submission lock is held."""
        try:
            future = self._executor_for(job.job_type).submit(
                self._run_job, job.id, handler
            )
        except Exception as exc:
            self.store.finish(job.id, "failed", "job scheduling failed")
            if error_message is not None:
                raise RuntimeError(error_message) from exc
            raise
        self._futures[job.id] = future
        return future

    def _watch_completion(self, job_id: int, future: Future[None]) -> None:
        # An already completed Future runs this callback immediately. Register it
        # outside the submission lock, since cleanup acquires that same lock.
        future.add_done_callback(
            lambda completed: self._discard_completed_future(job_id, completed)
        )

    def get(self, job_id: int) -> JobRecord | None:
        return self.store.get_job(int(job_id))

    def list(
        self,
        *,
        session_id: int | None = None,
        job_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        return self.store.list_jobs(
            session_id=session_id,
            job_types=job_types,
            statuses=statuses,
            limit=limit,
            offset=offset,
        )

    def _config_submission_guard(self, job_type: str, payload: dict[str, Any]):
        if job_type != "config_generation" or self._config_lock_root is None:
            return nullcontext()
        try:
            session_id = int(payload.get("session_id") or 0)
        except (TypeError, ValueError):
            return nullcontext()
        if session_id <= 0:
            return nullcontext()
        from backend.config_workspace.locks import session_config_lock

        return session_config_lock(self._config_lock_root, session_id)

    def cancel(self, job_id: int) -> bool:
        return self.store.request_cancel(int(job_id))

    def wait(self, job_id: int, timeout: float | None = None) -> None:
        with self._lock:
            future = self._futures.get(int(job_id))
        if future is None:
            return
        try:
            future.result(timeout=timeout)
        except TimeoutError:
            raise
        finally:
            if future.done():
                self._discard_completed_future(int(job_id), future)

    def _discard_completed_future(
        self,
        job_id: int,
        future: Future[None],
    ) -> None:
        with self._lock:
            if self._futures.get(int(job_id)) is future:
                self._futures.pop(int(job_id), None)

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=True)
        self._interactive_executor.shutdown(wait=True)

    def _executor_for(self, job_type: str) -> ThreadPoolExecutor:
        if job_type in _INTERACTIVE_JOB_TYPES:
            return self._interactive_executor
        return self._executor

    def _run_job(self, job_id: int, handler: JobHandler) -> None:
        if not self.store.mark_running(job_id):
            return
        job = self.store.get_job(job_id)
        if job is None:
            return
        context = JobContext(
            job_id=job.id,
            job_type=job.job_type,
            payload=job.payload,
            store=self.store,
            question_bank_sync_submitter=(
                self.submit_idempotent_question_bank_sync
            ),
        )
        try:
            result = handler(context)
        except JobCancellationRequested as exc:
            if not self.store.confirm_cancelled(job_id):
                self.store.finish(job_id, "failed", error=str(exc))
            return
        except Exception as exc:
            self.store.finish(job_id, "failed", error=str(exc) or type(exc).__name__)
            return
        except BaseException as exc:
            if self.store.is_cancel_requested(job_id):
                if not self.store.confirm_cancelled(job_id):
                    self.store.finish(job_id, "failed", error=type(exc).__name__)
            else:
                self.store.finish(job_id, "failed", error=str(exc) or type(exc).__name__)
            return
        try:
            self.store.finish(
                job_id,
                "succeeded",
                result=result if isinstance(result, dict) else {},
            )
            self._cleanup_completed_config_retry(job_id)
        except Exception:
            self.store.finish(
                job_id,
                "failed",
                error="Job result could not be persisted.",
            )

    def _cleanup_completed_config_retry(self, job_id: int) -> None:
        if self._config_lock_root is None:
            return
        job = self.store.get_job(job_id)
        if (
            job is None
            or job.status != "succeeded"
            or job.job_type != "config_generation"
            or job.payload.get("mode") != "retry"
            or job.result.get("outcome") != "complete"
        ):
            return
        from .config_generation import cleanup_consumed_config_retry_artifacts

        cleanup_consumed_config_retry_artifacts(
            self._config_lock_root,
            self.store,
            session_id=int(job.payload.get("session_id") or 0),
        )
