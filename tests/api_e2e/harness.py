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
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from report import ReportGenerator


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
    del controls  # Task 3 injects deterministic scan and grading runners.
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
