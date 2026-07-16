from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any, Callable

from .store import JobRecord, JobStore


JobHandler = Callable[["JobContext"], dict[str, Any] | None]


class UnsupportedJobTypeError(ValueError):
    pass


class JobCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: int
    job_type: str
    payload: dict[str, Any]
    store: JobStore

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


class JobManager:
    def __init__(
        self,
        store: JobStore,
        *,
        max_workers: int = 2,
        cleanup_interrupted: bool = True,
    ) -> None:
        self.store = store
        self._handlers: dict[str, JobHandler] = {}
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))
        self._futures: dict[int, Future[None]] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        if cleanup_interrupted:
            self.store.fail_interrupted_jobs()

    def register(self, job_type: str, handler: JobHandler) -> None:
        clean_type = str(job_type or "").strip()
        if not clean_type:
            raise ValueError("job_type must be nonblank")
        self._handlers[clean_type] = handler

    def submit(self, job_type: str, payload: dict[str, Any] | None = None) -> JobRecord:
        clean_type = str(job_type or "").strip()
        handler = self._handlers.get(clean_type)
        if handler is None:
            raise UnsupportedJobTypeError(f"unsupported job type: {clean_type}")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job = self.store.create_job(clean_type, dict(payload or {}))
            future = self._executor.submit(self._run_job, job.id, handler)
            self._futures[job.id] = future
        future.add_done_callback(
            lambda completed, job_id=job.id: self._discard_completed_future(
                job_id,
                completed,
            )
        )
        return job

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
            job, created = self.store.create_idempotent_config_job(dict(payload))
            if not created:
                return job, False
            future = self._executor.submit(self._run_job, job.id, handler)
            self._futures[job.id] = future
        future.add_done_callback(
            lambda completed, job_id=job.id: self._discard_completed_future(
                job_id,
                completed,
            )
        )
        return job, True

    def submit_config_retry(self, payload: dict[str, Any]) -> JobRecord:
        handler = self._handlers.get("config_generation")
        if handler is None:
            raise UnsupportedJobTypeError("unsupported job type: config_generation")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobManager has shut down")
            job = self.store.create_config_retry_job(dict(payload))
            future = self._executor.submit(self._run_job, job.id, handler)
            self._futures[job.id] = future
        future.add_done_callback(
            lambda completed, job_id=job.id: self._discard_completed_future(
                job_id,
                completed,
            )
        )
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
            job, created = self.store.create_idempotent_config_retry_job(dict(payload))
            if not created:
                return job, False
            future = self._executor.submit(self._run_job, job.id, handler)
            self._futures[job.id] = future
        future.add_done_callback(
            lambda completed, job_id=job.id: self._discard_completed_future(
                job_id,
                completed,
            )
        )
        return job, True

    def get(self, job_id: int) -> JobRecord | None:
        return self.store.get_job(int(job_id))

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
        )
        try:
            result = handler(context)
        except JobCancellationRequested as exc:
            if not self.store.confirm_cancelled(job_id):
                self.store.finish(job_id, "failed", error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.store.finish(job_id, "failed", error=str(exc))
            return
        try:
            self.store.finish(
                job_id,
                "succeeded",
                result=result if isinstance(result, dict) else {},
            )
        except Exception:  # noqa: BLE001
            self.store.finish(
                job_id,
                "failed",
                error="Job result could not be persisted.",
            )
