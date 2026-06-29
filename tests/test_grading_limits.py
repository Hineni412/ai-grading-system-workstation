from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

import api_profiles
import grading_service
from objective_batch_recognition_service import ObjectiveQuestionSpec, build_objective_batch_prompt
from scanner import ExamPaperGroup, ScanAnalysis, Scanner


ROOT = Path(__file__).resolve().parents[1]


def _grading_limits_module():
    spec = importlib.util.find_spec("grading_limits")
    assert spec is not None, "grading_limits.py should define the shared configuration limits"
    return importlib.import_module("grading_limits")


def test_shared_grading_limit_constants_match_task_brief() -> None:
    limits = _grading_limits_module()

    assert limits.OBJECTIVE_BATCH_SIZE_MIN == 1
    assert limits.OBJECTIVE_BATCH_SIZE_MAX == 15
    assert limits.SUBJECTIVE_MAJOR_BATCH_SIZE_MIN == 1
    assert limits.SUBJECTIVE_MAJOR_BATCH_SIZE_MAX == 20
    assert limits.PRECHECK_WORKERS_MIN == 1
    assert limits.PRECHECK_WORKERS_MAX == 32
    assert limits.FULL_PAPER_WORKERS_MIN == 1
    assert limits.FULL_PAPER_WORKERS_MAX == 200
    assert limits.HYBRID_INFLIGHT_WORKERS_MIN == 1
    assert limits.HYBRID_INFLIGHT_WORKERS_MAX == 1000
    assert limits.GRADING_RPM_MIN == 1
    assert limits.GRADING_RPM_MAX == 10000


def test_saving_profiles_drops_only_deprecated_objective_keys(tmp_path: Path) -> None:
    profile_path = tmp_path / "config" / "api_profiles.json"
    profiles = [
        {
            "name": "legacy",
            "provider": "custom",
            "objective_api_key": "key",
            "objective_model": "model",
            "objective_timeout": 60,
            "objective_max_tokens": 100,
            "objective_batch_size": 15,
            "keep_me": {"nested": True},
        }
    ]

    api_profiles.save_api_profiles(profile_path, profiles)

    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved == [
        {
            "name": "legacy",
            "provider": "custom",
            "objective_api_key": "key",
            "objective_model": "model",
            "objective_batch_size": 15,
            "keep_me": {"nested": True},
        }
    ]


def test_runtime_objective_config_ignores_legacy_timeout_and_token_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path = tmp_path / "api_profiles.json"
    profile_path.write_text(
        json.dumps(
            [
                {
                    "name": "legacy",
                    "objective_enabled": True,
                    "objective_api_key": "key",
                    "objective_base_url": "https://example.test/v1",
                    "objective_model": "objective-mini",
                    "objective_temperature": 0.25,
                    "objective_max_tokens": 100,
                    "objective_timeout": 60,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "path_manager.get_path_manager",
        lambda: SimpleNamespace(api_profiles_path=profile_path),
    )

    config = api_profiles.get_objective_api_config()

    assert config == {
        "base_url": "https://example.test/v1",
        "api_key": "key",
        "model": "objective-mini",
        "temperature": 0.25,
        "thinking_type": "disabled",
        "enabled": True,
    }


def test_web_app_source_removes_deprecated_objective_widget_state_names() -> None:
    source = (ROOT / "web_app.py").read_text(encoding="utf-8")

    assert "objective_timeout_input" not in source
    assert "objective_max_tokens_input" not in source
    assert "objective_timeout" not in source
    assert "objective_max_tokens" not in source


def test_web_and_backend_reuse_shared_limit_constants() -> None:
    web_source = (ROOT / "web_app.py").read_text(encoding="utf-8")
    grading_source = (ROOT / "grading_service.py").read_text(encoding="utf-8")
    scanner_source = (ROOT / "scanner.py").read_text(encoding="utf-8")

    assert "OBJECTIVE_BATCH_SIZE_MAX" in web_source
    assert "SUBJECTIVE_MAJOR_BATCH_SIZE_MAX" in web_source
    assert "PRECHECK_WORKERS_MAX" in web_source
    assert "FULL_PAPER_WORKERS_MAX" in web_source
    assert "HYBRID_INFLIGHT_WORKERS_MAX" in web_source
    assert "GRADING_RPM_MAX" in web_source

    assert "OBJECTIVE_BATCH_SIZE_MAX" in grading_source
    assert "SUBJECTIVE_MAJOR_BATCH_SIZE_MAX" in grading_source
    assert "FULL_PAPER_WORKERS_MAX" in grading_source
    assert "HYBRID_INFLIGHT_WORKERS_MAX" in grading_source
    assert "GRADING_RPM_MAX" in grading_source

    assert "PRECHECK_WORKERS_MAX" in scanner_source


def test_scanner_precheck_workers_are_bounded_to_shared_maximum() -> None:
    scanner = Scanner(exams_dir=Path("."), llm_client=object(), ocr_workers=999)

    assert scanner.ocr_workers == 32


class _FakeDB:
    def __init__(self, db_path: Path) -> None:
        self._paper_id = 0
        self.db_path = db_path

    def is_template_ready(self, session_id: int) -> bool:
        return True

    def get_grading_session(self, session_id: int) -> dict[str, Any]:
        return {"session_name": "2026-Task-6"}

    def list_students(self) -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Student 1"}]

    def try_start_session_run(self, session_id: int) -> bool:
        return True

    def clear_session_run_data(self, session_id: int) -> None:
        return None

    def replace_session_attendance(self, session_id: int, rows: list[dict[str, Any]]) -> None:
        return None

    def find_student_by_name(self, name: str) -> dict[str, Any] | None:
        if name == "Student 1":
            return {"id": 1, "name": "Student 1"}
        return None

    def create_exam_paper(self, **kwargs: Any) -> int:
        self._paper_id += 1
        return self._paper_id

    def update_exam_paper_status(self, paper_id: int, status: str, error: str | None = None) -> None:
        return None

    def finish_session_run(self, session_id: int, status: str) -> None:
        return None

    def get_session_progress(self, session_id: int) -> dict[str, Any]:
        return {"completed": 1}


def test_hybrid_runtime_bounds_objective_batch_size_to_15(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    Image.new("RGB", (100, 100), "white").save(front)
    Image.new("RGB", (100, 100), "white").save(back)
    captured: dict[str, Any] = {}

    class FakeAIGrader:
        def __init__(self, **kwargs: Any) -> None:
            self.rubric = kwargs["rubric_path"] if isinstance(kwargs["rubric_path"], dict) else {}
            self.answer_key = kwargs["answer_key_path"] if isinstance(kwargs["answer_key_path"], dict) else {}
            self.question_tag_context = kwargs.get("question_tag_context") or {}

    def fake_run_hybrid_batch_grading(**kwargs: Any) -> Any:
        captured["objective_batch_size"] = kwargs["objective_batch_size"]
        raise RuntimeError("stop_after_capture")

    monkeypatch.setattr(grading_service, "AIGrader", FakeAIGrader)
    monkeypatch.setattr(grading_service, "run_hybrid_batch_grading", fake_run_hybrid_batch_grading)
    import answer_region_geometry

    monkeypatch.setattr(grading_service, "_load_rubric_for_preflight", lambda path: {})
    monkeypatch.setattr(grading_service, "_validate_session_exam_identity", lambda session, rubric: None)
    monkeypatch.setattr(answer_region_geometry, "answer_regions_with_template_source_sizes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: SimpleNamespace(outputs_dir=tmp_path / "outputs", templates_dir=tmp_path / "templates"),
    )
    monkeypatch.setenv("LLM_OBJECTIVE_BATCH_SIZE", "16")

    service = grading_service.GradingService(_FakeDB(tmp_path / "databases" / "grading.db"), object())
    analysis = ScanAnalysis(
        groups=[
            ExamPaperGroup(
                front_image=front,
                back_image=back,
                student_name="Student 1",
                student_id=1,
                source_label="scan_1",
            )
        ],
        issues=[],
        warnings=[],
        total_pages=2,
    )

    list(
        service.run_session_grading(
            session_id=1,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis=analysis,
            enhance_images=False,
            grading_mode="hybrid_batch",
        )
    )

    assert captured["objective_batch_size"] == 15


def test_objective_batch_prompt_uses_actual_manifest_count() -> None:
    prompt = build_objective_batch_prompt(
        ObjectiveQuestionSpec(
            question_id="Q7",
            question_type="choice",
            standard_answer="A",
            max_score=8,
            rubric={},
        ),
        {
            "question_id": "Q7",
            "question_type": "choice",
            "items": [
                {"paper_key": "paper_001", "student_id": 1},
                {"paper_key": "paper_002", "student_id": 2},
                {"paper_key": "paper_003", "student_id": 3},
            ],
        },
    )

    assert "across 3 students" in prompt
    assert "up to 15 students" not in prompt
