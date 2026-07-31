from __future__ import annotations

import math
import multiprocessing
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.teaching_prep.application.pptx_execution import verify_candidate
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.materials import MaterialParser


def verify_candidate_with_deadline(
    *,
    candidate: Path,
    preview_dir: Path,
    plan_payload: Mapping[str, object],
    expected_slide_count: int,
    source_path: Path,
    source_sha256: str,
    execution_report: Mapping[str, object],
    parser: MaterialParser,
    timeout_seconds: float,
) -> dict[str, object]:
    """Verify a candidate in a process that the parent can stop at deadline.

    Parsing a malformed PPTX or preview should never keep a lesson-generation
    run alive after its five-minute machine budget.  A process boundary is
    intentional here: a Python thread cannot reliably interrupt a blocking
    parser or image decoder on Windows.
    """
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("lesson generation budget exceeded")
    request = {
        "candidate": str(candidate),
        "preview_dir": str(preview_dir),
        "plan_payload": dict(plan_payload),
        "expected_slide_count": int(expected_slide_count),
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "execution_report": dict(execution_report),
    }
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(
        target=_verification_worker,
        args=(send, request, parser),
        name="teaching-prep-pptx-verification",
    )
    worker.daemon = True
    deadline = time.monotonic() + timeout
    worker_started = False
    try:
        worker.start()
        worker_started = True
        send.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not receive.poll(remaining):
            _stop_worker(worker)
            raise TimeoutError("lesson generation budget exceeded")
        try:
            result = receive.recv()
        except EOFError as exc:
            raise RuntimeError("candidate verification worker stopped") from exc
        if not isinstance(result, Mapping):
            raise RuntimeError("candidate verification worker returned invalid data")
        kind = result.get("kind")
        if kind == "ok":
            report = result.get("report")
            if not isinstance(report, dict):
                raise RuntimeError(
                    "candidate verification worker returned invalid report"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("lesson generation budget exceeded")
            return report
        if kind == "validation":
            raise TeachingPrepValidationError("candidate verification failed")
        if kind == "timeout":
            raise TimeoutError("lesson generation budget exceeded")
        raise RuntimeError("candidate verification worker failed")
    finally:
        receive.close()
        send.close()
        if worker_started:
            if worker.is_alive():
                _stop_worker(worker)
            else:
                worker.join(timeout=0.1)


def _verification_worker(
    send: Any,
    request: Mapping[str, object],
    parser: MaterialParser,
) -> None:
    try:
        report = verify_candidate(
            candidate=Path(str(request["candidate"])),
            preview_dir=Path(str(request["preview_dir"])),
            plan_payload=dict(request["plan_payload"]),
            expected_slide_count=int(request["expected_slide_count"]),
            source_path=Path(str(request["source_path"])),
            source_sha256=str(request["source_sha256"]),
            execution_report=dict(request["execution_report"]),
            parser=parser,
        )
        send.send({"kind": "ok", "report": report})
    except TeachingPrepValidationError:
        send.send({"kind": "validation"})
    except TimeoutError:
        send.send({"kind": "timeout"})
    except BaseException:
        # Do not transmit internal paths or parser implementation details into
        # a normal API response; the parent supplies a safe generic error.
        try:
            send.send({"kind": "failure"})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        send.close()


def _stop_worker(worker: multiprocessing.Process) -> None:
    if worker.is_alive():
        worker.terminate()
    worker.join(timeout=1)
    if worker.is_alive():
        worker.kill()
        worker.join(timeout=1)


__all__ = ["verify_candidate_with_deadline"]
