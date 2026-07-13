from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from ai_grader import GradingResult, QuestionGradingDetail
from backend.api.dependencies import (
    get_annotated_dir,
    get_backups_dir,
    get_data_root,
    get_exams_dir,
    get_grading_db,
    get_job_file_service,
    get_job_manager,
    get_ops_write_service,
    get_outputs_dir,
    get_reports_dir,
    get_templates_dir,
    get_upload_config_dir,
)
from backend.files.service import JobFileService
from backend.jobs.config_generation import run_config_generation_job
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.grading_run import run_grading_job
from backend.jobs.manager import JobManager
from backend.jobs.scan_analysis import run_scan_analysis
from backend.jobs.store import JobStore
from report import ReportGenerator
from scanner import ExamPaperGroup, ScanAnalysis


@dataclass
class E2EControls:
    scan_failures_remaining: int = 0
    grading_runs: int = 0


@dataclass(frozen=True)
class E2EPaths:
    data_root: Path
    db_path: Path
    qb_db_path: Path
    reports_dir: Path
    exams_dir: Path
    templates_dir: Path
    annotated_dir: Path
    outputs_dir: Path
    backups_dir: Path
    upload_config_dir: Path
    bootstrap_rubric: Path
    bootstrap_answer: Path
    template_front: Path
    template_back: Path


@dataclass
class FakeLLM:
    calls: list[str]

    def record(self, request_type: str) -> None:
        self.calls.append(request_type)


class SyntheticScanner:
    def __init__(self, paths: E2EPaths, scanner_kwargs: dict[str, Any]) -> None:
        self.paths = paths
        self.scanner_kwargs = dict(scanner_kwargs)

    def analyze(self, students: list[dict[str, Any]]) -> ScanAnalysis:
        students_by_code = {
            str(student.get("student_code") or ""): student
            for student in students
        }
        groups: list[ExamPaperGroup] = []
        for student_code in ("SYN-001", "SYN-002"):
            student = students_by_code[student_code]
            groups.append(
                ExamPaperGroup(
                    front_image=self.paths.exams_dir / f"{student_code}_front.png",
                    back_image=self.paths.exams_dir / f"{student_code}_back.png",
                    student_name=str(student["name"]),
                    student_id=int(student["id"]),
                    detected_name=str(student["name"]),
                    source_label=f"{student_code} synthetic scan",
                    match_method="exact",
                    match_score=1.0,
                )
            )
        return ScanAnalysis(groups=groups, total_pages=4)


class SyntheticGradingService:
    def __init__(
        self,
        db_manager: Any,
        llm_client: Any,
        *,
        controls: E2EControls,
        question_bank_db_path: Path | None = None,
    ) -> None:
        self.db = db_manager
        self.llm_client = llm_client
        self.controls = controls
        self.question_bank_db_path = question_bank_db_path

    def run_session_grading(
        self,
        *,
        session_id: int,
        scan_analysis: dict[str, Any] | None,
        failed_only: bool,
        **_kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        self.controls.grading_runs += 1
        self.db.try_start_session_run(session_id)
        if failed_only:
            failed_papers = self.db.list_failed_papers_detailed(session_id)
            total = len(failed_papers)
            for current, paper in enumerate(failed_papers, start=1):
                student_id = int(paper["student_id"])
                student = self._student_by_id(student_id)
                result_id = self.db.save_session_result(
                    session_id,
                    student_id,
                    int(paper["paper_id"]),
                    _synthetic_grading_result(
                        student_name=str(student["name"]),
                        scores=[10, 12, 12, 12, 11, 13],
                        needs_human_review=False,
                    ),
                )
                self.db.update_exam_paper_status(int(paper["paper_id"]), "graded")
                yield {
                    "event": "graded",
                    "student_name": str(student["name"]),
                    "result_id": result_id,
                    "score": 70.0,
                    "total_score": 100.0,
                    "current": current,
                    "total": total,
                }
        else:
            groups = list((scan_analysis or {}).get("groups") or [])
            total = len(groups)
            for current, group in enumerate(groups, start=1):
                student_id = int(group["student_id"])
                student = self._student_by_id(student_id)
                student_code = str(student["student_code"])
                paper_id = self.db.create_exam_paper(
                    session_id=session_id,
                    front_image=str(group["front_image"]),
                    back_image=str(group["back_image"]),
                    ocr_name=str(group.get("detected_name") or group["student_name"]),
                    student_id=student_id,
                    match_status="matched",
                    processing_status="grading",
                )
                if student_code == "SYN-001":
                    result_id = self.db.save_session_result(
                        session_id,
                        student_id,
                        paper_id,
                        _synthetic_grading_result(
                            student_name=str(student["name"]),
                            scores=[12, 17, 17, 17, 17, 5],
                            needs_human_review=True,
                        ),
                    )
                    self.db.update_exam_paper_status(paper_id, "graded")
                    yield {
                        "event": "graded",
                        "student_name": str(student["name"]),
                        "result_id": result_id,
                        "score": 85.0,
                        "total_score": 100.0,
                        "current": current,
                        "total": total,
                    }
                else:
                    self.db.update_exam_paper_status(
                        paper_id,
                        "failed",
                        "synthetic grading failure",
                    )
                    yield {
                        "event": "grading_failed",
                        "student_name": str(student["name"]),
                        "current": current,
                        "total": total,
                    }

        self.db.finish_session_run(session_id, "completed")
        yield {
            "event": "session_completed",
            "progress": self.db.get_session_progress(session_id),
        }

    def _student_by_id(self, student_id: int) -> dict[str, Any]:
        return next(
            student
            for student in self.db.list_students()
            if int(student["id"]) == int(student_id)
        )


def _synthetic_grading_result(
    *,
    student_name: str,
    scores: list[float],
    needs_human_review: bool,
) -> GradingResult:
    details = [
        QuestionGradingDetail(
            question_id=f"Q{index}",
            score_awarded=float(score),
            deduction_reason=(
                "synthetic review required"
                if needs_human_review and index == 1
                else "synthetic grading"
            ),
            knowledge_id=f"SYN-K{index}",
            confidence_score=0.5 if needs_human_review and index == 1 else 1.0,
            knowledge_ids=[f"SYN-K{index}"],
        )
        for index, score in enumerate(scores, start=1)
    ]
    return GradingResult(
        student_name=student_name,
        total_score=100.0,
        student_score=float(sum(scores)),
        needs_human_review=needs_human_review,
        grading_details=details,
        raw_json={
            "source": "synthetic_api_e2e",
            "grading_completeness": {"status": "complete"},
        },
    )


def make_scan_runner(paths: E2EPaths, controls: E2EControls):
    def scan_runner(**kwargs: Any) -> dict[str, object]:
        if controls.scan_failures_remaining > 0:
            controls.scan_failures_remaining -= 1
            raise RuntimeError("synthetic scan failure")
        return run_scan_analysis(
            **kwargs,
            scanner_factory=lambda **scanner_kwargs: SyntheticScanner(
                paths,
                scanner_kwargs,
            ),
        )

    return scan_runner


def make_grading_runner(controls: E2EControls):
    def grading_runner(**kwargs: Any) -> dict[str, object]:
        return run_grading_job(
            **kwargs,
            service_factory=lambda db_manager, llm_client, question_bank_db_path=None: (
                SyntheticGradingService(
                    db_manager,
                    llm_client,
                    controls=controls,
                    question_bank_db_path=question_bank_db_path,
                )
            ),
        )

    return grading_runner


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _normalized_public_text(value: str) -> str:
    return value.replace("\\", "/").casefold()


class ApiE2EHarness:
    TERMINAL = {"succeeded", "failed", "cancelled"}

    def __init__(
        self,
        client: TestClient,
        db: Any,
        manager: Any,
        paths: E2EPaths,
        controls: E2EControls,
    ) -> None:
        self.client = client
        self.db = db
        self.manager = manager
        self.paths = paths
        self.controls = controls

    def poll_job(
        self,
        job_id: int,
        expected_status: str,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            last = response.json()
            normalized_data_root = _normalized_public_text(
                str(self.paths.data_root)
            ).rstrip("/")
            for value in _iter_strings(last):
                assert normalized_data_root not in _normalized_public_text(value), (
                    "job response exposed the temporary data root"
                )
                assert "api_key" not in value.casefold(), (
                    "job response exposed an API key field or value"
                )
            if last["status"] in self.TERMINAL:
                assert last["status"] == expected_status
                return last
            time.sleep(0.01)
        raise AssertionError(f"job {job_id} did not reach a terminal state: {last}")

    @staticmethod
    def config_request() -> dict[str, Any]:
        return {
            "confirmed_blocks": [
                {
                    "question_id": "Q1",
                    "question_type": "comprehensive",
                    "text": "synthetic prompt",
                    "canonical_answer": "synthetic answer",
                }
            ],
            "document_text": "synthetic exam text",
            "question_images": {},
        }

    def bind_and_commit_template(self, session_id: int) -> dict[str, Any]:
        bound = self.client.put(
            f"/api/sessions/{session_id}/template",
            json={
                "front_template_path": str(self.paths.template_front),
                "back_template_path": str(self.paths.template_back),
            },
        )
        assert bound.status_code == 200
        region = {
            "region_uuid": "q1-region",
            "page": "front",
            "region_order": 1,
            "x": 10,
            "y": 10,
            "w": 80,
            "h": 60,
            "mapped_question_id": "Q1",
            "mapping_status": "manual",
            "is_confirmed": True,
        }
        draft = self.client.put(
            f"/api/sessions/{session_id}/regions/draft",
            json={"revision": 1, "regions": [region]},
        )
        assert draft.status_code == 200
        committed = self.client.post(
            f"/api/sessions/{session_id}/regions/commit",
            json={
                "regions": [region],
                "image_sizes": {"front": [120, 160], "back": [120, 160]},
                "template_matches": True,
                "expected_template_fingerprint": draft.json()[
                    "template_fingerprint"
                ],
            },
        )
        assert committed.status_code == 200
        return committed.json()

    def paper_statuses(self, session_id: int) -> list[str]:
        with self.db._connect() as connection:
            rows = connection.execute(
                "SELECT processing_status FROM exam_papers "
                "WHERE session_id = ? ORDER BY processing_status, id",
                (session_id,),
            ).fetchall()
        return [str(row["processing_status"]) for row in rows]

    def result_scores(self, session_id: int) -> dict[str, float]:
        with self.db._connect() as connection:
            rows = connection.execute(
                "SELECT s.student_code, sr.student_score "
                "FROM session_results sr "
                "JOIN students s ON s.id = sr.student_id "
                "WHERE sr.session_id = ? ORDER BY s.student_code",
                (session_id,),
            ).fetchall()
        return {
            str(row["student_code"]): float(row["student_score"])
            for row in rows
        }

    def result_ids(self, session_id: int) -> dict[str, int]:
        with self.db._connect() as connection:
            rows = connection.execute(
                "SELECT s.student_code, sr.id "
                "FROM session_results sr "
                "JOIN students s ON s.id = sr.student_id "
                "WHERE sr.session_id = ? ORDER BY s.student_code",
                (session_id,),
            ).fetchall()
        return {str(row["student_code"]): int(row["id"]) for row in rows}

    def result_review_flags(self, session_id: int) -> dict[str, bool]:
        with self.db._connect() as connection:
            rows = connection.execute(
                "SELECT s.student_code, sr.needs_human_review "
                "FROM session_results sr "
                "JOIN students s ON s.id = sr.student_id "
                "WHERE sr.session_id = ? ORDER BY s.student_code",
                (session_id,),
            ).fetchall()
        return {
            str(row["student_code"]): bool(row["needs_human_review"])
            for row in rows
        }

    def result_details(
        self,
        session_id: int,
    ) -> dict[str, list[tuple[str, float]]]:
        with self.db._connect() as connection:
            rows = connection.execute(
                "SELECT s.student_code, sd.question_id, sd.score_awarded "
                "FROM session_details sd "
                "JOIN session_results sr ON sr.id = sd.result_id "
                "JOIN students s ON s.id = sr.student_id "
                "WHERE sr.session_id = ? "
                "ORDER BY s.student_code, sd.id",
                (session_id,),
            ).fetchall()
        details: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            details.setdefault(str(row["student_code"]), []).append(
                (str(row["question_id"]), float(row["score_awarded"]))
            )
        return details


def valid_config_payload() -> dict[str, Any]:
    rubric_questions: list[dict[str, Any]] = []
    answer_questions: list[dict[str, Any]] = []
    for index, score in enumerate([17, 17, 17, 17, 17, 15], start=1):
        question_id = f"Q{index}"
        part_id = f"{question_id}-P1"
        rubric_questions.append(
            {
                "question_id": question_id,
                "question_type": "comprehensive",
                "max_score": score,
                "knowledge_id": f"SYN-K{index}",
                "parts": [
                    {
                        "part_id": part_id,
                        "part_score": score,
                        "steps": [
                            {
                                "step_id": f"{part_id}-S1",
                                "step_score": score,
                                "core_goal": "synthetic answer",
                                "required_elements": ["synthetic evidence"],
                                "allow_alternative_methods": True,
                            }
                        ],
                    }
                ],
            }
        )
        answer_questions.append(
            {
                "question_id": question_id,
                "canonical_answer": f"synthetic answer {index}",
                "accepted_forms": [f"synthetic answer {index}"],
                "method_variants": [],
                "parts": [
                    {
                        "part_id": part_id,
                        "answer": f"synthetic answer {index}",
                        "analysis": "synthetic analysis",
                        "step_milestones": ["synthetic evidence"],
                    }
                ],
            }
        )
    return {
        "rubric": {
            "exam_title": "Synthetic E2E Exam",
            "total_score": 100,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {"warnings": []},
    }


def build_paths(tmp_path: Path) -> E2EPaths:
    data_root = tmp_path / "synthetic_data"
    databases_dir = data_root / "databases"
    reports_dir = data_root / "reports"
    exams_dir = data_root / "exams"
    templates_dir = data_root / "templates"
    annotated_dir = data_root / "annotated"
    outputs_dir = data_root / "outputs"
    backups_dir = data_root / "backups"
    upload_config_dir = data_root / "config" / "uploaded"
    for directory in (
        databases_dir,
        reports_dir,
        exams_dir,
        templates_dir,
        annotated_dir,
        outputs_dir,
        backups_dir,
        upload_config_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    bootstrap_rubric = upload_config_dir / "bootstrap_rubric.json"
    bootstrap_answer = upload_config_dir / "bootstrap_answer.json"
    for path in (bootstrap_rubric, bootstrap_answer):
        path.write_text(json.dumps({}), encoding="utf-8")

    template_front = templates_dir / "template_front.png"
    template_back = templates_dir / "template_back.png"
    image_assets = (
        (template_front, "white"),
        (template_back, "white"),
        (exams_dir / "SYN-001_front.png", "lightgray"),
        (exams_dir / "SYN-001_back.png", "lightgray"),
        (exams_dir / "SYN-002_front.png", "gainsboro"),
        (exams_dir / "SYN-002_back.png", "gainsboro"),
    )
    for path, color in image_assets:
        Image.new("RGB", (120, 160), color=color).save(path, format="PNG")

    return E2EPaths(
        data_root=data_root,
        db_path=databases_dir / "grading_system.db",
        qb_db_path=databases_dir / "question_bank.db",
        reports_dir=reports_dir,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        annotated_dir=annotated_dir,
        outputs_dir=outputs_dir,
        backups_dir=backups_dir,
        upload_config_dir=upload_config_dir,
        bootstrap_rubric=bootstrap_rubric,
        bootstrap_answer=bootstrap_answer,
        template_front=template_front,
        template_back=template_back,
    )


def build_job_manager(paths: E2EPaths, *, controls: E2EControls) -> JobManager:
    manager = JobManager(JobStore(paths.db_path), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=paths.db_path,
            reports_dir=paths.reports_dir,
            exams_dir=paths.exams_dir,
            templates_dir=paths.templates_dir,
            data_root=paths.data_root,
            question_bank_db_path=paths.qb_db_path,
            upload_config_dir=paths.upload_config_dir,
            training_output_root=paths.outputs_dir / "training",
            report_generator_factory=ReportGenerator,
            scan_runner=make_scan_runner(paths, controls),
            grading_runner=make_grading_runner(controls),
            config_generation_runner=run_config_generation_job,
            llm_client_factory=lambda: FakeLLM([]),
        )
    except Exception:
        manager.shutdown()
        raise
    return manager


def install_dependency_overrides(
    app: FastAPI,
    *,
    db: Any,
    manager: JobManager,
    paths: E2EPaths,
) -> None:
    job_files = JobFileService(
        paths.reports_dir,
        training_outputs_dir=paths.outputs_dir / "training",
        backups_dir=paths.backups_dir,
        ops_outputs_dir=paths.outputs_dir / "ops",
    )
    inert_ops_service = SimpleNamespace()
    app.dependency_overrides.update(
        {
            get_grading_db: lambda: db,
            get_job_manager: lambda: manager,
            get_upload_config_dir: lambda: paths.upload_config_dir,
            get_templates_dir: lambda: paths.templates_dir,
            get_data_root: lambda: paths.data_root,
            get_exams_dir: lambda: paths.exams_dir,
            get_reports_dir: lambda: paths.reports_dir,
            get_annotated_dir: lambda: paths.annotated_dir,
            get_outputs_dir: lambda: paths.outputs_dir,
            get_backups_dir: lambda: paths.backups_dir,
            get_job_file_service: lambda: job_files,
            get_ops_write_service: lambda: inert_ops_service,
        }
    )
