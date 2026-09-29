from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import asdict
import gc
import hashlib
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from PIL import Image, ImageDraw

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.media.service import ReviewMediaService
from ai_batch_grading_service import MajorQuestionSpec, grade_major_question_batch
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.question_tag_projection_service import QuestionTagProjectionService
from llm_client import LLMClient, LLMSettings
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from tools.performance.dataset import LARGE, build_benchmark_dataset
from tools.performance.evidence_optimization_report import (
    CandidateEvidence,
    EvidenceOptimizationReport,
    Timing,
    qualifies,
    render_json,
    render_markdown,
    timing_changes,
)


DEFAULT_JSON = Path("output/performance/p3-18-evidence-performance.json")
DEFAULT_MARKDOWN = Path("output/performance/p3-18-evidence-performance.md")


class _GeneratedMediaRepository:
    def __init__(self, source_path: Path, width: int, height: int) -> None:
        self.db_path = source_path.parent / "generated.db"
        self.source_path = source_path
        self.width = width
        self.height = height

    def get_review_media_context(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> dict[str, Any] | None:
        if (session_id, result_id, detail_id) != (1, 1, 1):
            return None
        return {
            "question_id": "Q1",
            "front_image": str(self.source_path),
            "back_image": str(self.source_path),
        }

    def list_answer_regions(self, session_id: int) -> list[dict[str, Any]]:
        if session_id != 1:
            return []
        return [
            {
                "region_uuid": "generated-q1",
                "page": "front",
                "x": self.width // 5,
                "y": self.height // 5,
                "w": self.width // 2,
                "h": self.height // 3,
                "mapped_question_id": "Q1",
                "source_image_width": self.width,
                "source_image_height": self.height,
            }
        ]

    def get_session_template(self, session_id: int) -> None:
        return None


def run_crop_experiment(
    root: Path,
    *,
    warmups: int,
    samples: int,
    image_width: int,
    image_height: int,
    repetitions: int = 2,
) -> CandidateEvidence:
    if min(warmups, samples, image_width, image_height, repetitions) <= 0:
        raise ValueError("benchmark counts and dimensions must be positive")
    root = Path(root)
    data_root = root / "generated_data"
    exams_dir = data_root / "exams"
    source_path = exams_dir / "session_1" / "front.jpg"
    _write_generated_image(source_path, image_width, image_height)
    repository = _GeneratedMediaRepository(source_path, image_width, image_height)
    common = {
        "data_root": data_root,
        "exams_dir": exams_dir,
        "templates_dir": data_root / "templates",
        "annotated_dir": data_root / "annotated",
    }
    uncached = ReviewMediaService(
        repository,
        **common,
        enable_crop_cache=False,
    )
    cached = ReviewMediaService(
        repository,
        **common,
        crop_cache_dir=data_root / "cache" / "review_crops",
    )
    call_uncached = lambda: uncached.render_detail_crop(1, 1, 1)
    call_cached = lambda: cached.render_detail_crop(1, 1, 1)

    before_samples: list[float] = []
    after_samples: list[float] = []
    equivalent = True
    repetition_results: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        for _ in range(warmups):
            call_uncached()
        current_before_samples, before_payload = _measure(call_uncached, samples)

        cached.clear_detail_crop_cache()
        cold_started = time.perf_counter_ns()
        cold_payload = call_cached()
        cold_miss_ms = (time.perf_counter_ns() - cold_started) / 1_000_000
        for _ in range(warmups):
            call_cached()
        current_after_samples, after_payload = _measure(call_cached, samples)
        current_before = _timing(current_before_samples)
        current_after = _timing(current_after_samples)
        current_equivalent = before_payload == cold_payload == after_payload
        equivalent = equivalent and current_equivalent
        before_samples.extend(current_before_samples)
        after_samples.extend(current_after_samples)
        repetition_results.append(
            {
                "repetition": repetition,
                "before_p50_ms": round(current_before.p50_ms, 4),
                "before_p95_ms": round(current_before.p95_ms, 4),
                "after_p50_ms": round(current_after.p50_ms, 4),
                "after_p95_ms": round(current_after.p95_ms, 4),
                "cold_miss_ms": round(cold_miss_ms, 4),
                "qualified": qualifies(current_before, current_after)
                and current_equivalent,
            }
        )
    before = _timing(before_samples)
    after = _timing(after_samples)
    p50_improvement, p95_change = timing_changes(before, after)
    equivalent = equivalent and (
        hashlib.sha256(before_payload).digest()
        == hashlib.sha256(after_payload).digest()
    )
    peak_memory = max(
        _peak_memory(call_uncached),
        _peak_memory(call_cached),
    )
    cache_dir = cached.crop_cache_dir
    disk_bytes = sum(
        path.stat().st_size for path in cache_dir.glob("*.jpg")
    )
    selected = (
        qualifies(before, after)
        and equivalent
        and all(item["qualified"] for item in repetition_results)
    )
    return CandidateEvidence(
        name="review_crop_cache",
        decision="selected" if selected else "rejected",
        reason=(
            "Repeated review crop reads met both latency gates with exact JPEG bytes."
            if selected
            else "Repeated review crop reads did not meet every frozen latency and correctness gate."
        ),
        dataset={
            "source": "generated",
            "image_count": 1,
            "image_width": image_width,
            "image_height": image_height,
            "source_bytes": source_path.stat().st_size,
            "cache_limit_bytes": cached.max_crop_cache_bytes,
            "warmups": warmups,
            "samples": samples,
            "repetitions": repetitions,
            "repetition_results": repetition_results,
            "workload": "same_detail_repeated_read",
        },
        before=before,
        after=after,
        p50_improvement_percent=p50_improvement,
        p95_change_percent=p95_change,
        output_equivalent=equivalent,
        peak_memory_bytes=peak_memory,
        disk_bytes=disk_bytes,
        invalidation=(
            "SHA-256 source bytes, region geometry, page and render version form the key; "
            "corrupt entries rebuild and explicit cleanup is supported."
        ),
        concurrency=(
            "One process-wide lock per cache directory and atomic replacement prevent partial reads."
        ),
    )


def build_p3_18_report(
    root: Path,
    *,
    code_sha: str,
    warmups: int,
    samples: int,
    image_width: int,
    image_height: int,
) -> EvidenceOptimizationReport:
    crop = run_crop_experiment(
        root,
        warmups=warmups,
        samples=samples,
        image_width=image_width,
        image_height=image_height,
    )
    relational = build_benchmark_dataset(root / "relational", LARGE, seed=318)
    tag_projection = _tag_projection_evidence(
        relational.paths.db_path,
        relational.paths.qb_db_path,
        samples=samples,
    )
    image_compression = _image_compression_evidence(
        root / "llm-images",
        samples=samples,
        image_width=image_width,
        image_height=image_height,
    )
    sql = _hot_sql_evidence(
        relational.paths.qb_db_path,
        samples=samples,
    )
    return EvidenceOptimizationReport(
        package="P3-18",
        code_sha=code_sha,
        generated_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        environment={
            "operating_system": platform.platform(),
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "logical_cpu_count": os.cpu_count() or 1,
        },
        thresholds={
            "minimum_p50_improvement_percent": 20.0,
            "maximum_p95_regression_percent": 5.0,
        },
        candidates=(crop, tag_projection, image_compression, sql),
        limitations=(
            "All writes use generated data under a system temporary directory.",
            "The benchmark is machine-specific and is not a service-level objective.",
            "Memo decisions execute public request seams with generated inputs and a no-network fake model gateway.",
            "The SQL candidate is measured before and after only on a generated large_5pct database.",
            "Python tracemalloc does not include every native allocation performed inside Pillow.",
        ),
    )


def _tag_projection_evidence(
    grading_db_path: Path,
    question_bank_db_path: Path,
    *,
    samples: int,
) -> CandidateEvidence:
    service = DiagnosisProfileService(grading_db_path, question_bank_db_path)
    scope = {"mode": "class", "class_id": ""}
    exam_scope = {"mode": "cross_exam"}
    original = QuestionTagProjectionService.project_session
    active_calls: list[str] = []

    def tracked(instance: QuestionTagProjectionService, *args: Any, **kwargs: Any) -> Any:
        active_calls.append(str(kwargs.get("grading_session_id", "")))
        return original(instance, *args, **kwargs)

    timings: list[float] = []
    hashes: list[str] = []
    repeats: list[int] = []
    calls_per_request: list[int] = []
    QuestionTagProjectionService.project_session = tracked
    try:
        for _ in range(samples):
            active_calls.clear()
            started = time.perf_counter_ns()
            payload = service.build_tag_profiles(scope=scope, exam_scope=exam_scope)
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
            hashes.append(_json_digest(payload))
            counts = Counter(active_calls)
            repeats.append(sum(max(count - 1, 0) for count in counts.values()))
            calls_per_request.append(len(active_calls))
    finally:
        QuestionTagProjectionService.project_session = original
    maximum_repeat = max(repeats, default=0)
    if maximum_repeat:
        raise RuntimeError("tag projection workload now repeats identities; benchmark a memo candidate")
    return CandidateEvidence(
        name="tag_projection_request_memo",
        decision="rejected",
        reason=(
            "Generated public diagnosis requests repeated no session projection identity "
            "within a request, so request-local memoization has no evidenced hit."
        ),
        dataset={
            "source": "generated_public_diagnosis_requests",
            "scale": LARGE.name,
            "request_samples": samples,
            "projection_calls_per_request": calls_per_request,
            "maximum_request_local_repeat_count": maximum_repeat,
        },
        before=_timing(timings),
        output_equivalent=len(set(hashes)) == 1,
    )


class _GeneratedGateway:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    def chat_completions(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok":true}'),
                )
            ]
        )


class _RetryGateway:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.attempts = 0
        self.image_identities: list[list[str]] = []

    def chat_completions(self, **call: Any) -> SimpleNamespace:
        self.attempts += 1
        identities: list[str] = []
        for message in call["kwargs"]["messages"]:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if item.get("type") != "image_url":
                    continue
                url = str(item["image_url"]["url"])
                identities.append(hashlib.sha256(url.encode("ascii")).hexdigest())
        self.image_identities.append(identities)
        if self.attempts < 3:
            raise TimeoutError("generated transient failure")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(self.response, separators=(",", ":"))
                    ),
                )
            ]
        )


class _GeneratedHybridBuilder:
    def __init__(self, atlas_path: Path, manifest: dict[str, Any]) -> None:
        self.atlas_path = atlas_path
        self.manifest = manifest

    def build(self, **_kwargs: Any) -> dict[str, Any]:
        return {"atlas_path": self.atlas_path, "manifest": self.manifest}


class _GeneratedHybridClient:
    def __init__(self, delegate: LLMClient, *, use_memo: bool) -> None:
        self.delegate = delegate
        self.use_memo = use_memo
        self.maximum_memo_entries = 0

    def json_from_images_with_options(self, *args: Any, **kwargs: Any) -> Any:
        if not self.use_memo:
            kwargs.pop("image_compression_memo", None)
        # This synthetic benchmark explicitly repeats the request so it can
        # measure compression reuse. Production grading does not hide retries.
        kwargs["allow_gateway_retry"] = False
        memo = kwargs.get("image_compression_memo")
        try:
            for attempt in range(3):
                try:
                    return self.delegate.json_from_images_with_options(
                        *args,
                        **kwargs,
                    )
                except TimeoutError:
                    if attempt == 2:
                        raise
            raise AssertionError("synthetic retry loop did not return")
        finally:
            if isinstance(memo, dict):
                self.maximum_memo_entries = max(
                    self.maximum_memo_entries,
                    len(memo),
                )


def _image_compression_evidence(
    root: Path,
    *,
    samples: int,
    image_width: int,
    image_height: int,
) -> CandidateEvidence:
    paths = [root / f"generated-{number}.jpg" for number in range(3)]
    for index, path in enumerate(paths):
        _write_generated_image(
            path,
            image_width + index,
            image_height + index,
        )
    blobs = [path.read_bytes() for path in paths]
    spec = MajorQuestionSpec(
        question_id="Q1",
        detail_question_ids=["Q1"],
        rubric={
            "question_id": "Q1",
            "question_image_base64": base64.b64encode(blobs[0]).decode("ascii"),
        },
        answer_key={
            "question_id": "Q1",
            "answer_image_base64": base64.b64encode(blobs[1]).decode("ascii"),
        },
        max_score=2,
    )
    manifest = {
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "student_name": "generated-student",
                "sub_items": [],
            }
        ]
    }
    response = {
        "question_id": "Q1",
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "grading_details": [
                    {
                        "question_id": "Q1",
                        "score_awarded": 2,
                        "confidence_score": 95,
                        "knowledge_ids": ["generated"],
                    }
                ],
            }
        ],
    }
    builder = _GeneratedHybridBuilder(paths[2], manifest)

    def run_request(use_memo: bool) -> tuple[float, str, int, int, int]:
        gateway = _RetryGateway(response)
        delegate = LLMClient(
            LLMSettings(
                api_key="generated-key",
                base_url="https://example.invalid/v1",
                ocr_model="generated-model",
                grading_model="generated-model",
                config_model="generated-model",
            ),
            gateway_factory=lambda **_kwargs: gateway,
            usage_sink_factory=lambda: object(),
            trace_sink_factory=lambda: object(),
        )
        client = _GeneratedHybridClient(delegate, use_memo=use_memo)
        started = time.perf_counter_ns()
        payload = grade_major_question_batch(
            session_id=1,
            spec=spec,
            paper_entries=[],
            answer_regions=[],
            llm_client=client,
            grading_model="generated-model",
            output_root=root,
            batch_index=1,
            builder=builder,
        )
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        flattened = [
            identity
            for attempt in gateway.image_identities
            for identity in attempt
        ]
        repeated = sum(
            max(count - 1, 0)
            for count in Counter(flattened).values()
        )
        return (
            elapsed,
            _json_digest(payload),
            gateway.attempts,
            repeated,
            client.maximum_memo_entries,
        )

    before_values: list[float] = []
    after_values: list[float] = []
    before_outputs: list[str] = []
    after_outputs: list[str] = []
    attempts: list[int] = []
    repeated_identities: list[int] = []
    memo_entries: list[int] = []
    peak_memory_before = 0
    peak_memory_after = 0
    original_sleep = time.sleep
    time.sleep = lambda _seconds: None
    try:
        for _ in range(samples):
            before_elapsed, before_output, before_attempts, repeated, _ = run_request(False)
            after_elapsed, after_output, after_attempts, _repeated, memo_count = run_request(True)
            if before_attempts != 3 or after_attempts != 3:
                raise RuntimeError("generated hybrid retry did not execute three attempts")
            before_values.append(before_elapsed)
            after_values.append(after_elapsed)
            before_outputs.append(before_output)
            after_outputs.append(after_output)
            attempts.append(after_attempts)
            repeated_identities.append(repeated)
            memo_entries.append(memo_count)
        peak_memory_before = _peak_memory(lambda: run_request(False))
        peak_memory_after = _peak_memory(lambda: run_request(True))
    finally:
        time.sleep = original_sleep
    before = _timing(before_values)
    after = _timing(after_values)
    p50_improvement, p95_change = timing_changes(before, after)
    equivalent = (
        len(set(before_outputs + after_outputs)) == 1
        and bool(before_outputs)
    )
    selected = qualifies(before, after) and equivalent
    return CandidateEvidence(
        name="image_compression_request_memo",
        decision="selected" if selected else "rejected",
        reason=(
            "Generated hybrid grading retries met both latency gates with identical "
            "business output and a memo bounded to the request's unique images."
            if selected
            else "Generated hybrid grading retries did not meet every frozen latency and correctness gate."
        ),
        dataset={
            "source": "generated_public_hybrid_retry_requests",
            "request_samples": samples,
            "attempts_per_request": attempts,
            "maximum_repeated_image_identities": max(repeated_identities, default=0),
            "maximum_memo_entries": max(memo_entries, default=0),
            "unique_images_per_request": len(blobs),
            "retry_backoff_excluded": True,
            "peak_memory_before_bytes": peak_memory_before,
            "peak_memory_after_bytes": peak_memory_after,
        },
        before=before,
        after=after,
        p50_improvement_percent=p50_improvement,
        p95_change_percent=p95_change,
        output_equivalent=equivalent,
        peak_memory_bytes=peak_memory_after,
        disk_bytes=0,
        invalidation="SHA-256 source bytes key a memo discarded when the batch request returns.",
        concurrency="Each concurrent batch owns a separate memo; no state is shared across requests.",
    )


def _hot_sql_evidence(
    question_bank_db_path: Path,
    *,
    samples: int,
) -> CandidateEvidence:
    import question_bank.services.question_read_service as read_module

    service = QuestionBankReadService(question_bank_db_path)
    filters = QuestionReadFilters()

    def call() -> Any:
        # Keep the public read path, but force a cache miss so every timing sample
        # measures the SQL candidate instead of the process-wide result cache.
        with read_module._read_connection(question_bank_db_path):
            return service.list_questions(filters)

    before_values, before_digest = _measure_object(call, samples)
    query_sql = _capture_question_list_sql(read_module, call)
    query_plan_before = _query_plan(question_bank_db_path, query_sql)
    write_before = _measure_generated_write(question_bank_db_path, samples)
    disk_before = _database_bytes(question_bank_db_path)
    index_sql = (
        "CREATE INDEX p3_18_candidate_questions_active_created "
        "ON questions(is_deleted, created_at DESC, id DESC)"
    )
    started = time.perf_counter_ns()
    with sqlite3.connect(question_bank_db_path) as conn:
        conn.execute(index_sql)
        conn.commit()
    index_create_ms = (time.perf_counter_ns() - started) / 1_000_000
    disk_after = _database_bytes(question_bank_db_path)
    query_plan_after = _query_plan(question_bank_db_path, query_sql)
    after_values, after_digest = _measure_object(call, samples)
    write_after = _measure_generated_write(question_bank_db_path, samples)
    before = _timing(before_values)
    after = _timing(after_values)
    p50_improvement, p95_change = timing_changes(before, after)
    equivalent = before_digest == after_digest
    selected = qualifies(before, after) and equivalent
    if selected:
        raise RuntimeError("SQL candidate qualified; production implementation is required")
    return CandidateEvidence(
        name="hot_sql_index",
        decision="rejected",
        reason=(
            "The generated public question-list experiment did not meet both frozen "
            "latency gates after accounting for write and disk evidence."
        ),
        dataset={
            "source": "generated_public_question_list_requests",
            "scale": LARGE.name,
            "questions": LARGE.question_bank_questions,
            "request_samples": samples,
            "query_plan_before": query_plan_before,
            "query_plan_after": query_plan_after,
            "candidate_index_sql": index_sql,
            "candidate_index_create_ms": round(index_create_ms, 4),
            "write_p50_before_ms": round(_timing(write_before).p50_ms, 4),
            "write_p95_before_ms": round(_timing(write_before).p95_ms, 4),
            "write_p50_after_ms": round(_timing(write_after).p50_ms, 4),
            "write_p95_after_ms": round(_timing(write_after).p95_ms, 4),
            "candidate_disk_delta_bytes": int(disk_after - disk_before),
        },
        before=before,
        after=after,
        p50_improvement_percent=p50_improvement,
        p95_change_percent=p95_change,
        output_equivalent=equivalent,
        disk_bytes=int(disk_after - disk_before),
        invalidation="Rejected candidate; no production index or invalidation change is applied.",
        concurrency="Generated read snapshots and rolled-back writes use SQLite's normal isolation.",
    )


def _capture_question_list_sql(
    read_module: Any,
    call: Callable[[], Any],
) -> str:
    statements: list[str] = []
    original = read_module._open_direct_read_connection

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    read_module._open_direct_read_connection = traced
    try:
        call()
    finally:
        read_module._open_direct_read_connection = original
    return next(
        statement
        for statement in statements
        if "SELECT DISTINCT" in statement.upper()
        and "FROM QUESTIONS Q" in statement.upper()
    )


def _query_plan(db_path: Path, query_sql: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"EXPLAIN QUERY PLAN {query_sql}").fetchall()
    return [str(row[3]) for row in rows]


def _measure_generated_write(db_path: Path, samples: int) -> list[float]:
    values: list[float] = []
    with sqlite3.connect(db_path) as conn:
        for _ in range(samples):
            conn.execute("BEGIN")
            started = time.perf_counter_ns()
            conn.execute(
                "UPDATE questions SET created_at = created_at WHERE id = 1"
            )
            conn.rollback()
            values.append((time.perf_counter_ns() - started) / 1_000_000)
    return values


def _database_bytes(db_path: Path) -> int:
    return sum(
        path.stat().st_size
        for path in (
            db_path,
            db_path.with_name(f"{db_path.name}-wal"),
            db_path.with_name(f"{db_path.name}-shm"),
        )
        if path.exists()
    )


def _measure_object(
    call: Callable[[], Any],
    samples: int,
) -> tuple[list[float], str]:
    values: list[float] = []
    digest = ""
    for _ in range(samples):
        started = time.perf_counter_ns()
        payload = call()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
        digest = _json_digest(asdict(payload))
    return values, digest


def _json_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_generated_image(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), (242, 242, 238))
    draw = ImageDraw.Draw(image)
    step = max(24, min(width, height) // 40)
    for offset in range(0, max(width, height), step):
        color = (
            (offset * 17) % 220,
            (offset * 31) % 220,
            (offset * 47) % 220,
        )
        draw.line((0, offset, width, max(0, offset - height // 5)), fill=color, width=3)
        draw.line((offset, 0, max(0, offset - width // 6), height), fill=color, width=2)
    image.save(path, format="JPEG", quality=94)
    image.close()


def _measure(call: Callable[[], bytes], samples: int) -> tuple[list[float], bytes]:
    values: list[float] = []
    payload = b""
    for _ in range(samples):
        started = time.perf_counter_ns()
        payload = call()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return values, payload


def _timing(values: list[float]) -> Timing:
    ordered = sorted(values)
    return Timing(
        p50_ms=_nearest_rank(ordered, 0.50),
        p95_ms=_nearest_rank(ordered, 0.95),
    )


def _nearest_rank(ordered: list[float], percentile: float) -> float:
    return float(ordered[math.ceil(percentile * len(ordered)) - 1])


def _peak_memory(call: Callable[[], Any]) -> int:
    tracemalloc.start()
    try:
        call()
        _current, peak = tracemalloc.get_traced_memory()
        return int(peak)
    finally:
        tracemalloc.stop()


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _code_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("invalid code revision")
    return value


def _publish(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run P3-18 evidence experiments")
    parser.add_argument("--warmups", type=_positive, default=3)
    parser.add_argument("--samples", type=_positive, default=20)
    parser.add_argument("--image-width", type=_positive, default=2400)
    parser.add_argument("--image-height", type=_positive, default=3200)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output_json.resolve() == args.output_markdown.resolve():
        print("benchmark_failed report destinations must be distinct", file=sys.stderr)
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="p3-18-benchmark-") as temp:
            try:
                report = build_p3_18_report(
                    Path(temp),
                    code_sha=_code_sha(),
                    warmups=args.warmups,
                    samples=args.samples,
                    image_width=args.image_width,
                    image_height=args.image_height,
                )
            finally:
                # Windows may defer finalization of SQLite read-snapshot handles.
                gc.collect()
                time.sleep(0.1)
        _publish(args.output_json, render_json(report))
        _publish(args.output_markdown, render_markdown(report))
    except Exception as exc:
        print(f"benchmark_failed {type(exc).__name__}", file=sys.stderr)
        return 1
    selected = sum(item.decision == "selected" for item in report.candidates)
    print(f"benchmark_passed candidates=4 selected={selected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_p3_18_report",
    "build_parser",
    "main",
    "run_crop_experiment",
]
