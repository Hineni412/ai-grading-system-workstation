from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID
import warnings
from pathlib import Path

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.source_question_link_service import SourceQuestionLinkService
from question_bank.services.training_task_service import TrainingTaskService


@pytest.fixture
def training_services(tmp_path: Path) -> tuple[
    DiagnosisProfileService,
    PracticePlanService,
    TrainingTaskService,
]:
    # db 必须放在名为 "databases" 的目录下,生产代码据此把 tmp_path 推断为
    # data root(受控根),否则 resolve_stored_file_path 会拒绝 tmp_path 下的
    # rubric.json 等存储路径。
    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    grading_db_path = db_dir / "grading.db"
    question_bank_db_path = db_dir / "question_bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    initialize_database(question_bank_db_path)

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                    {"question_id": "Q3", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with grading_db._connect() as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S12", "学生甲", "八年级1班"),
                (15, "S15", "学生乙", "八年级1班"),
            ],
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '当前考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 20, 11, 0, '{}')
            """
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (14001, ?, ?, ?, '["UNKNOWN"]', ?, ?, ?)
            """,
            [
                (
                    "Q1",
                    6,
                    "缺少辅助线",
                    "逻辑断裂",
                    "辅助线思路缺失",
                    json.dumps(
                        [{"category": "审题错误", "summary": "条件识别不完整"}],
                        ensure_ascii=False,
                    ),
                ),
                ("Q2", 5, "", None, None, "[]"),
            ],
        )

    with connect(question_bank_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (?, ?, ?, 'success')
            """,
            [(1, "合成考试", "C:/private/source.docx")]
            + [
                (question_id, f"合成练习 {question_id}", f"practice-{question_id}.docx")
                for question_id in range(201, 209)
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, '解答题', ?, '5')
            """,
            [
                (101, 1, "1", "当前考试原题"),
                (102, 1, "2", "已掌握的题"),
                (201, 201, "11", "利用边角关系证明两个三角形全等"),
                (202, 202, "12", "根据中点条件构造全等三角形"),
                (203, 203, "13", "在折叠图形中寻找对应边并完成证明"),
                (204, 204, "14", "结合平行线性质判定三角形全等"),
                (205, 205, "15", "运用角平分线条件求未知线段长度"),
                (206, 206, "16", "从旋转图形中识别全等关系"),
                (207, 207, "17", "添加辅助线后证明两条线段相等"),
                (208, 208, "18", "在复杂几何图中选择合适的全等判定"),
            ],
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (question_id, "knowledge_point", "三角形全等")
                for question_id in [101, 102, *range(201, 209)]
            ]
            + [(101, "method", "构造辅助线")],
        )

    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=101,
        link_method="paper_question_number",
    )
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q2",
        bank_question_id=102,
        link_method="paper_question_number",
    )

    return (
        DiagnosisProfileService(grading_db_path, question_bank_db_path),
        PracticePlanService(question_bank_db_path),
        TrainingTaskService(question_bank_db_path),
    )


@pytest.fixture
def training_client(training_services) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_practice_plan_service,
        get_request_diagnosis_profile_service,
        get_request_practice_plan_service,
        get_training_task_service,
    )

    diagnosis, practice, tasks = training_services
    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: diagnosis
    app.dependency_overrides[get_practice_plan_service] = lambda: practice
    app.dependency_overrides[get_request_diagnosis_profile_service] = (
        lambda: diagnosis
    )
    app.dependency_overrides[get_request_practice_plan_service] = lambda: practice
    app.dependency_overrides[get_training_task_service] = lambda: tasks
    return TestClient(app)


def test_training_preview_resolves_services_from_one_cached_request_context(
    training_services,
) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_practice_plan_service,
        get_request_read_context,
    )

    diagnosis, practice, _tasks = training_services
    calls = 0

    def provide_context():
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            diagnosis_service=diagnosis,
            practice_service=practice,
        )

    app = create_app()
    app.dependency_overrides[get_request_read_context] = provide_context
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: diagnosis
    app.dependency_overrides[get_practice_plan_service] = lambda: practice

    response = TestClient(app).post(
        "/api/training/plans/preview",
        json=_preview_request(),
    )

    assert response.status_code == 200
    assert calls == 1


def test_training_task_confirmation_keeps_legacy_services_and_writes_temp_db(
    training_client: TestClient,
) -> None:
    from backend.api.dependencies import get_request_read_context

    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()

    def reject_read_context():
        pytest.fail("training task writes must not create the readonly context")

    training_client.app.dependency_overrides[get_request_read_context] = (
        reject_read_context
    )
    response = training_client.post(
        "/api/training/tasks",
        json=_confirmation_request(preview),
    )

    assert response.status_code == 201
    assert training_client.get("/api/training/tasks").json()["total"] == 1


def test_training_second_snapshot_failure_is_sanitized_and_cleans_first(
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.api.read_connections as read_connections
    from backend.api.app import create_app
    from path_manager import get_path_manager
    from question_bank.services.question_read_service import (
        QuestionBankSnapshotUnavailable,
    )

    diagnosis, _practice, _tasks = training_services
    paths = SimpleNamespace(
        db_path=diagnosis.db.db_path,
        qb_db_path=diagnosis.question_bank_db_path,
    )
    real_capture = read_connections.captured_sqlite_read_connection
    first_connection: sqlite3.Connection | None = None
    first_candidate: Path | None = None

    @contextmanager
    def fail_second_capture(db_path: Path, **kwargs):
        nonlocal first_connection, first_candidate
        if Path(db_path) == paths.qb_db_path:
            raise QuestionBankSnapshotUnavailable(
                "C:/private/question_bank.db changed"
            )
        with real_capture(db_path, **kwargs) as connection:
            first_connection = connection
            first_candidate = Path(
                next(
                    row[2]
                    for row in connection.execute("PRAGMA database_list").fetchall()
                    if str(row[1]) == "main"
                )
            )
            yield connection

    @contextmanager
    def fail_direct_bank_read(db_path: Path, **kwargs):
        raise QuestionBankSnapshotUnavailable(
            "C:/private/question_bank.db changed"
        )
        yield  # pragma: no cover - unreachable, keeps this a context manager

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        fail_second_capture,
    )
    monkeypatch.setattr(
        read_connections,
        "_direct_question_bank_read",
        fail_direct_bank_read,
    )
    app = create_app()
    app.dependency_overrides[get_path_manager] = lambda: paths
    response = TestClient(app).post(
        "/api/training/diagnosis",
        headers={"x-request-id": "rid-training-snapshot"},
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "training_database_unavailable",
        "message": "Training data is temporarily unavailable",
        "details": {},
        "request_id": "rid-training-snapshot",
    }
    assert "C:/private" not in response.text
    assert first_connection is not None
    assert first_candidate is not None
    assert not first_candidate.exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        first_connection.execute("SELECT 1")


def test_training_diagnosis_uses_question_tag_identity(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnosis_identity"] == "question_tag"
    assert payload["scope"]["mode"] == "student"
    assert payload["scope"]["student_ids"] == ["12"]
    assert payload["scope"]["matched_student_count"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["scope"]["scope_revision"])
    assert (
        payload["scope"]["student_score_profiles"]["12"]["score_rate_source"]
        == "current_exam"
    )
    assert payload["exam_scope"]["session_ids"] == [14]
    weak = payload["students"][0]["weak_points"][0]
    assert weak["knowledge_point"] == "三角形全等"
    assert weak["mastery"] == pytest.approx(11 / 15, abs=0.0001)
    assert weak["tag_context"]["method"] == ["构造辅助线"]
    assert payload["coverage"] == {
        "covered_items": 2,
        "total_items": 3,
        "missing_items": {"Q3": "missing_link"},
    }
    assert "C:/private" not in response.text


def test_training_diagnosis_accepts_cause_fields_in_weak_points(
    training_client: TestClient,
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnosis, _practice, _tasks = training_services
    real_build_profiles = diagnosis.build_profiles

    def build_profiles_with_causes(**kwargs):
        payload = real_build_profiles(**kwargs)
        for student in payload["students"]:
            for weak in student["weak_points"]:
                weak["error_categories"] = ["概念不清"]
                weak["error_patterns"] = ["错用判定条件"]
                for reference in weak["source_question_refs"]:
                    reference["causes"] = [
                        {
                            "category": "概念不清",
                            "pattern": "错用判定条件",
                            "status": "confirmed",
                        }
                    ]
        return payload

    monkeypatch.setattr(diagnosis, "build_profiles", build_profiles_with_causes)
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 200
    weak = response.json()["students"][0]["weak_points"][0]
    assert weak["error_categories"] == ["概念不清"]
    assert weak["error_patterns"] == ["错用判定条件"]
    assert weak["source_question_refs"][0]["causes"][0]["category"] == "概念不清"


def test_training_diagnosis_caches_identical_grouping_requests(
    training_client: TestClient,
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from backend.api.routers import training as training_router

    diagnosis, _practice, _tasks = training_services
    training_router._GROUPING_RESULT_CACHE.clear()

    calls: list[dict] = []

    def fake_chapter_groups(**kwargs):
        calls.append(kwargs)
        return {
            "version": 1,
            "groups": [],
            "unassigned": [],
            "selection": None,
            "scope_keys": list(kwargs["config"].group_scope_keys),
            "warnings": [],
            "summary": {"student_count": 0},
        }

    fake_module = SimpleNamespace(
        db_path=diagnosis.question_bank_db_path,
        current_knowledge=SimpleNamespace(release_id="rel-test"),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        chapter_groups=fake_chapter_groups,
    )
    training_client.app.dependency_overrides[training_router._grouping_module] = (
        lambda: fake_module
    )
    body = {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "grouping": {"scope_keys": ["kp_x"]},
    }

    first = training_client.post("/api/training/diagnosis", json=body)
    second = training_client.post("/api/training/diagnosis", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["grouping"] == second.json()["grouping"]
    # Second identical request is a whole-result cache hit.
    assert len(calls) == 1

    changed = {
        **body,
        "grouping": {**body["grouping"], "question_count": 9},
    }
    third = training_client.post("/api/training/diagnosis", json=changed)
    assert third.status_code == 200
    assert len(calls) == 2


def test_training_diagnosis_returns_clear_empty_state_for_missing_selection(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "selected", "student_ids": ["999"]},
            "exam_scope": {"mode": "manual", "session_ids": [999]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["students"] == []
    assert payload["exam_scope"]["session_ids"] == []
    assert any("不存在" in warning for warning in payload["warnings"])
    assert any("没有已关联" in warning for warning in payload["warnings"])


def test_training_diagnosis_rejects_unknown_scope_mode_without_reflecting_input(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        headers={"x-request-id": "rid-training-scope"},
        json={
            "scope": {"mode": "legacy-secret-mode", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == "rid-training-scope"
    assert "legacy-secret-mode" not in response.text


def test_training_diagnosis_rejects_class_scope_without_class_id(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "class", "student_ids": []},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def _overview_stub_diagnosis() -> dict[str, object]:
    return {
        "scope": {
            "mode": "all",
            "student_ids": ["12"],
            "class_id": None,
            "class_ids": [],
            "score_rate_min": None,
            "score_rate_max": None,
            "include_student_ids": [],
            "exclude_student_ids": [],
            "use_historical_fallback": False,
            "matched_student_count": 1,
            "scope_revision": "0" * 64,
            "student_score_profiles": {},
        },
        "exam_scope": {
            "mode": "semester",
            "curriculum_volume_id": "bnu24-math-g8-upper",
            "session_ids": [],
            "sessions": [],
        },
        "knowledge_catalog": [
            {
                "knowledge_key": "kp_bnu24_math_g8_upper_1",
                "knowledge_point": "八年级上册｜第一章 勾股定理",
                "parent_knowledge_key": None,
                "parent_knowledge_point": None,
                "node_kind": "chapter",
            },
            {
                "knowledge_key": "kp_bnu24_math_g8_upper_1_1",
                "knowledge_point": "八年级上册｜第一章｜1 探索勾股定理",
                "parent_knowledge_key": "kp_bnu24_math_g8_upper_1",
                "parent_knowledge_point": "八年级上册｜第一章 勾股定理",
                "node_kind": "section",
            },
            {
                "knowledge_key": "kp_bnu24_math_g8_upper_1_1_1",
                "knowledge_point": "八年级上册｜第一章｜1｜用勾股定理求边长",
                "parent_knowledge_key": "kp_bnu24_math_g8_upper_1_1",
                "parent_knowledge_point": "八年级上册｜第一章｜1 探索勾股定理",
                "node_kind": "topic",
            },
            {
                "knowledge_key": "sk_stub_overview",
                "knowledge_point": "技能·列勾股等式",
                "parent_knowledge_key": "kp_bnu24_math_g8_upper_1_1_1",
                "parent_knowledge_point": "用勾股定理求边长",
                "node_kind": "skill",
            },
        ],
        "group_weak_points": [
            {"knowledge_key": "kp_bnu24_math_g8_upper_1_1_1", "mastery": 0.5},
        ],
        "students": [
            {
                "student_id": "12",
                "student_code": "S12",
                "student_name": "学生甲",
                "class_id": "八年级1班",
                "score_rate": 0.55,
                "score_rate_source": "current_exam",
                "weak_points": [
                    {
                        "knowledge_key": "kp_bnu24_math_g8_upper_1_1_1",
                        "knowledge_point": "用勾股定理求边长",
                        "mastery": 0.5,
                        "evidence_count": 2,
                    },
                    {
                        "knowledge_key": "sk_stub_overview",
                        "knowledge_point": "技能·列勾股等式",
                        "mastery": 0.9,
                        "evidence_count": 1,
                    },
                ],
            }
        ],
        "warnings": [],
        "diagnosis_identity": "question_tag",
    }


def test_training_overview_returns_tier_distribution(
    training_client: TestClient,
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnosis, _practice, _tasks = training_services
    monkeypatch.setattr(
        diagnosis, "build_profiles", lambda **_kwargs: _overview_stub_diagnosis()
    )
    response = training_client.post(
        "/api/training/overview",
        json={
            "scope": {"mode": "all"},
            "exam_scope": {
                "mode": "semester",
                "curriculum_volume_id": "bnu24-math-g8-upper",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exam_scope"]["mode"] == "semester"
    nodes = {
        node["knowledge_key"]: node for node in payload["nodes"]
    }
    assert nodes["kp_bnu24_math_g8_upper_1_1_1"]["distribution"] == {
        "weak": 1,
        "review": 0,
        "stable": 0,
        "missing": 0,
    }
    assert nodes["sk_stub_overview"]["kind"] == "skill"
    assert nodes["sk_stub_overview"]["section_key"] == "kp_bnu24_math_g8_upper_1_1"
    assert payload["summary"]["topic_count"] >= 1
    assert payload["summary"]["weak_topic_count"] == 1
    assert payload["students"][0]["student_id"] == "12"
    assert payload["students"][0]["topics"]["weak"] == 1


def test_training_overview_passes_class_scope_to_build_profiles(
    training_client: TestClient,
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnosis, _practice, _tasks = training_services
    captured: dict[str, object] = {}

    def fake_build_profiles(**kwargs):
        captured.update(kwargs)
        return _overview_stub_diagnosis()

    monkeypatch.setattr(diagnosis, "build_profiles", fake_build_profiles)
    response = training_client.post(
        "/api/training/overview",
        json={
            "scope": {
                "mode": "class",
                "class_id": "10",
                "class_ids": ["10"],
                "student_ids": [],
            },
            "exam_scope": {
                "mode": "semester",
                "curriculum_volume_id": "bnu24-math-g8-upper",
            },
        },
    )

    assert response.status_code == 200
    scope = captured["scope"]
    assert scope["mode"] == "class"
    assert scope["class_id"] == "10"
    assert scope["class_ids"] == ["10"]
    assert captured["exam_scope"]["mode"] == "semester"
    assert captured["exam_scope"]["curriculum_volume_id"] == "bnu24-math-g8-upper"


def test_training_overview_rejects_non_semester_scope(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/overview",
        json={
            "scope": {"mode": "all"},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "training_scope_invalid"


def test_training_overview_rejects_empty_volume(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/overview",
        json={
            "scope": {"mode": "all"},
            "exam_scope": {"mode": "semester", "curriculum_volume_id": ""},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "training_scope_invalid"


def _preview_request() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "variant_mode": "individual",
        "question_count": 8,
        "stage_ratios": {
            "direct": 0.6,
            "prerequisite": 0.3,
            "transfer": 0.1,
        },
        "exclude_current_exam_originals": True,
    }


def test_training_plan_preview_uses_exact_tags_and_excludes_current_questions(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert re.fullmatch(r"[0-9a-f]{64}", payload["plan_revision"])
    plan = payload["plan"]
    assert plan["diagnosis_snapshot"]["diagnosis_identity"] == "question_tag"
    items = [item for variant in plan["variants"] for item in variant["items"]]
    assert len(items) == 8
    assert {item["question_id"] for item in items} == set(range(201, 209))
    assert all(item["match_kind"] == "exact" for item in items)
    assert all(item["knowledge_point"] == "三角形全等" for item in items)
    assert "C:/private" not in response.text


def test_training_plan_preview_reports_missing_tag_empty_state(
    training_client: TestClient,
    training_services,
) -> None:
    diagnosis, _practice, _tasks = training_services
    with connect(diagnosis.question_bank_db_path) as conn:
        conn.execute(
            "DELETE FROM question_tags WHERE question_id IN (101, 102) "
            "AND tag_type = 'knowledge_point'"
        )

    response = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    )

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["variants"][0]["items"] == []
    assert any("没有可用于推荐" in item for item in plan["warnings"])
    assert any("没有已关联" in item for item in plan["diagnosis_snapshot"]["warnings"])


def test_training_plan_preview_accepts_bounded_teacher_groups(
    training_client: TestClient,
) -> None:
    body = _preview_request()
    body["variant_mode"] = "auto_group"
    body["teacher_groups"] = {"重点巩固组": ["12"]}

    response = training_client.post("/api/training/plans/preview", json=body)

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["teacher_override"] == {
        "allowed": True,
        "applied": True,
        "assignments": {"重点巩固组": ["12"]},
    }
    assert plan["variants"][0]["student_ids"] == ["12"]


def test_training_plan_preview_rejects_invalid_stage_ratios(
    training_client: TestClient,
) -> None:
    body = _preview_request()
    body["stage_ratios"] = {
        "direct": 0.8,
        "prerequisite": 0.3,
        "transfer": 0.1,
    }

    response = training_client.post("/api/training/plans/preview", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("forbidden_field", ["allow_broad_fallback", "read_mode"])
def test_training_plan_preview_rejects_legacy_or_broad_controls(
    training_client: TestClient,
    forbidden_field: str,
) -> None:
    body = _preview_request()
    body[forbidden_field] = True if forbidden_field == "allow_broad_fallback" else "skill"

    response = training_client.post("/api/training/plans/preview", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def _confirmation_request(preview: dict[str, object]) -> dict[str, object]:
    return {
        **_preview_request(),
        "confirmation_id": "12345678-1234-5678-1234-567812345678",
        "expected_plan_revision": preview["plan_revision"],
    }


def test_training_task_confirmation_is_idempotent_and_teacher_owned(
    training_client: TestClient,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    body = _confirmation_request(preview)

    first = training_client.post("/api/training/tasks", json=body)
    second = training_client.post("/api/training/tasks", json=body)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["task_code"] == second.json()["task_code"]
    assert first.json()["created_by"] == "teacher"
    assert UUID(str(body["confirmation_id"])).hex.upper() in first.json()["task_code"]
    assert "source_file" not in first.text
    assert "C:/private" not in first.text

    listing = training_client.get("/api/training/tasks", params={"page_size": 1})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["page"] == 1
    assert listing.json()["total_pages"] == 1
    task_id = first.json()["id"]
    detail = training_client.get(f"/api/training/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == task_id


def test_concurrent_training_task_confirmation_creates_one_task(
    training_client: TestClient,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    body = _confirmation_request(preview)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _index: training_client.post("/api/training/tasks", json=body),
                range(2),
            )
        )

    assert [response.status_code for response in responses] == [201, 201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert training_client.get("/api/training/tasks").json()["total"] == 1


def test_training_task_confirmation_detects_plan_revision_change(
    training_client: TestClient,
    training_services,
) -> None:
    original = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    body = _confirmation_request(original)
    assert training_client.post("/api/training/tasks", json=body).status_code == 201

    diagnosis, _practice, _tasks = training_services
    with connect(diagnosis.question_bank_db_path) as conn:
        conn.execute(
            "UPDATE questions SET question_text = '候选题内容已经改变' WHERE id = 201"
        )
    changed = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    changed_body = _confirmation_request(changed)

    response = training_client.post("/api/training/tasks", json=changed_body)

    assert changed["plan_revision"] != original["plan_revision"]
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "training_confirmation_conflict"


def test_training_task_confirmation_rejects_stale_preview(
    training_client: TestClient,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    body = _confirmation_request(preview)
    body["expected_plan_revision"] = "0" * 64

    response = training_client.post("/api/training/tasks", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "training_confirmation_conflict"


def test_training_task_confirmation_rejects_empty_plan(
    training_client: TestClient,
    training_services,
) -> None:
    diagnosis, _practice, _tasks = training_services
    with connect(diagnosis.question_bank_db_path) as conn:
        conn.execute("DELETE FROM question_tags WHERE tag_type = 'knowledge_point'")
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()

    response = training_client.post(
        "/api/training/tasks",
        json=_confirmation_request(preview),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "training_plan_invalid"


def test_training_task_routes_return_sanitized_not_found(
    training_client: TestClient,
) -> None:
    response = training_client.get(
        "/api/training/tasks/999",
        headers={"x-request-id": "rid-training-task"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "training_task_not_found",
            "message": "Training task not found",
            "details": {"task_id": 999},
            "request_id": "rid-training-task",
        }
    }


def test_training_task_detail_does_not_publish_export_storage_or_raw_errors(
    training_client: TestClient,
    training_services,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    created = training_client.post(
        "/api/training/tasks",
        json=_confirmation_request(preview),
    ).json()
    _diagnosis, _practice, tasks = training_services
    with connect(tasks.db_path) as conn:
        conn.execute(
            """
            INSERT INTO training_exports (
                task_id, audience, export_format, output_path, status, error_message
            ) VALUES (?, 'teacher', 'docx', 'C:/private/export.docx', 'failed', ?)
            """,
            (created["id"], "private parser stack detail"),
        )

    response = training_client.get(f"/api/training/tasks/{created['id']}")

    assert response.status_code == 200
    assert "output_path" not in response.text
    assert "error_message" not in response.text
    assert "private parser stack detail" not in response.text


def test_training_task_confirmation_does_not_accept_created_by(
    training_client: TestClient,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    body = _confirmation_request(preview)
    body["created_by"] = "administrator"

    response = training_client.post("/api/training/tasks", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_training_task_confirmation_maps_prelookup_database_failure_to_503(
    training_client: TestClient,
    training_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = training_client.post(
        "/api/training/plans/preview",
        json=_preview_request(),
    ).json()
    _diagnosis, _practice, tasks = training_services

    def fail_lookup(_task_code: str):
        raise sqlite3.OperationalError("C:/private/question_bank.db unavailable")

    monkeypatch.setattr(tasks, "get_task_by_code", fail_lookup)

    response = training_client.post(
        "/api/training/tasks",
        json=_confirmation_request(preview),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "training_database_unavailable"
    assert "C:/private" not in response.text
