from __future__ import annotations

import json
import sqlite3
import warnings
import gc
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.schemas.graph import GraphEvidenceRequest, GraphQueryRequest
from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService


def _query_payload() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
    }


def test_target_read_post_openapi_contracts_remain_stable() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected = {
        "/api/training/diagnosis": (
            "build_training_diagnosis_api_training_diagnosis_post",
            "TrainingDiagnosisResponse",
        ),
        "/api/training/plans/preview": (
            "preview_training_plan_api_training_plans_preview_post",
            "TrainingPlanResponse",
        ),
        "/api/graph/profiles": (
            "get_graph_profiles_api_graph_profiles_post",
            "GraphProfilesResponse",
        ),
        "/api/graph/rows": (
            "get_graph_rows_api_graph_rows_post",
            "GraphRowsResponse",
        ),
        "/api/graph/evidence": (
            "get_graph_evidence_api_graph_evidence_post",
            "GraphEvidenceResponse",
        ),
    }

    for path, (operation_id, response_schema) in expected.items():
        operation = schema["paths"][path]["post"]
        assert operation["operationId"] == operation_id
        assert operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": f"#/components/schemas/{response_schema}"}


def test_graph_query_request_rejects_legacy_controls() -> None:
    with pytest.raises(ValidationError):
        GraphQueryRequest.model_validate(
            {
                **_query_payload(),
                "read_mode": "skill",
            }
        )


@pytest.mark.parametrize(
    "knowledge_key",
    ["skill:7", "knowledge_point:", " knowledge_point:   "],
)
def test_graph_evidence_request_rejects_non_tag_identity(
    knowledge_key: str,
) -> None:
    with pytest.raises(ValidationError):
        GraphEvidenceRequest.model_validate(
            {
                **_query_payload(),
                "knowledge_key": knowledge_key,
            }
        )


def test_graph_evidence_request_normalizes_exact_tag_key_and_pagination() -> None:
    request = GraphEvidenceRequest.model_validate(
        {
            **_query_payload(),
            "knowledge_key": " knowledge_point:三角形全等 ",
            "page": 2,
            "page_size": 5,
        }
    )

    assert request.knowledge_key == "knowledge_point:三角形全等"
    assert request.page == 2
    assert request.page_size == 5


class CountingDiagnosisService:
    def __init__(
        self,
        service: DiagnosisProfileService,
        grading_path: Path,
        question_bank_path: Path,
    ) -> None:
        self.service = service
        self.grading_path = grading_path
        self.question_bank_path = question_bank_path
        self.build_calls = 0

    def build_tag_profiles(self, **kwargs):
        self.build_calls += 1
        return self.service.build_tag_profiles(**kwargs)


@pytest.fixture
def graph_service(tmp_path: Path) -> CountingDiagnosisService:
    grading_path = tmp_path / "grading.db"
    question_bank_path = tmp_path / "question_bank.db"
    grading = DBManager(grading_path)
    grading.initialize()
    initialize_database(question_bank_path)

    rubric_paths: dict[int, Path] = {}
    for session_id in (14, 15):
        rubric_path = tmp_path / f"rubric-{session_id}.json"
        rubric_path.write_text(
            json.dumps(
                {"questions": [{"question_id": "Q1", "max_score": 10}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        rubric_paths[session_id] = rubric_path

    with grading._connect() as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S12", "学生甲", "八年级1班"),
                (15, "S15", "学生乙", "八年级1班"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (?, ?, ?, '', 'completed', 0)
            """,
            [
                (14, "当前考试", str(rubric_paths[14])),
                (15, "第二次考试", str(rubric_paths[15])),
            ],
        )
        conn.executemany(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (?, ?, ?, '', ?, 'matched', 'graded')
            """,
            [
                (1401, 14, "C:/private/paper-14.png", 12),
                (1501, 15, "C:/private/paper-15.png", 15),
            ],
        )
        conn.executemany(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (?, ?, ?, ?, 10, ?, 0, '{}')
            """,
            [
                (14001, 14, 12, 1401, 5),
                (15001, 15, 15, 1501, 9),
            ],
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (?, 'Q1', ?, ?, '["UNKNOWN"]', ?, ?, ?)
            """,
            [
                (
                    14001,
                    5,
                    "缺少辅助线",
                    "逻辑断裂",
                    "辅助线思路缺失",
                    json.dumps([], ensure_ascii=False),
                ),
                (
                    15001,
                    9,
                    "条件遗漏",
                    "审题错误",
                    "条件识别不完整",
                    json.dumps([], ensure_ascii=False),
                ),
            ],
        )

    with connect(question_bank_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (1, '合成考试', 'C:/private/source.docx', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (101, 1, '1', '解答题', '合成题目', '5')
            """
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (101, ?, ?)",
            [
                ("knowledge_point", "三角形全等"),
                ("method", "构造辅助线"),
            ],
        )

    links = SourceQuestionLinkService(question_bank_path)
    for session_id in (14, 15):
        links.confirm_link(
            grading_session_id=session_id,
            source_question_id="Q1",
            bank_question_id=101,
            link_method="paper_question_number",
        )

    return CountingDiagnosisService(
        DiagnosisProfileService(grading_path, question_bank_path),
        grading_path,
        question_bank_path,
    )


@pytest.fixture
def graph_client(graph_service: CountingDiagnosisService) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import get_request_diagnosis_profile_service

    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = (
        lambda: graph_service
    )
    return TestClient(app)


def _class_cross_exam_payload() -> dict[str, object]:
    return {
        "scope": {"mode": "class", "class_id": "八年级1班"},
        "exam_scope": {"mode": "cross_exam"},
    }


def test_graph_profiles_return_tag_only_filtered_data_once(
    graph_client: TestClient,
    graph_service: CountingDiagnosisService,
) -> None:
    response = graph_client.post(
        "/api/graph/profiles",
        json=_class_cross_exam_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnosis_identity"] == "question_tag"
    assert set(payload["scope"]["student_ids"]) == {"12", "15"}
    assert payload["exam_scope"]["session_ids"] == [14, 15]
    assert "confirmed_concept_ids" not in payload
    assert graph_service.build_calls == 1


def test_graph_rows_return_nodes_and_no_relationship_edges(
    graph_client: TestClient,
    graph_service: CountingDiagnosisService,
) -> None:
    response = graph_client.post(
        "/api/graph/rows",
        json=_class_cross_exam_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["edges"] == []
    assert len(payload["rows"]) == 2
    assert payload["nodes"] == [
        {
            "knowledge_key": "knowledge_point:三角形全等",
            "knowledge_label": "三角形全等",
            "student_count": 2,
            "item_count": 2,
            "deduction_count": 2,
            "average_mastery": 0.7,
            "tag_context": {"method": ["构造辅助线"]},
            "error_counts": {
                "primary": {
                    "条件识别不完整": 1,
                    "辅助线思路缺失": 1,
                },
                "secondary": {},
            },
        }
    ]
    assert graph_service.build_calls == 1
    assert "C:/private" not in response.text


def test_graph_evidence_is_paginated_and_path_free(
    graph_client: TestClient,
    graph_service: CountingDiagnosisService,
) -> None:
    response = graph_client.post(
        "/api/graph/evidence",
        json={
            **_class_cross_exam_payload(),
            "knowledge_key": "knowledge_point:三角形全等",
            "page": 2,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["page"] == 2
    assert payload["total_pages"] == 2
    assert [(item["student_id"], item["session_id"]) for item in payload["items"]] == [
        (15, 15)
    ]
    assert graph_service.build_calls == 1
    assert "C:/private" not in response.text


def test_graph_evidence_returns_stable_empty_state_for_unknown_tag(
    graph_client: TestClient,
) -> None:
    response = graph_client.post(
        "/api/graph/evidence",
        json={
            **_class_cross_exam_payload(),
            "knowledge_key": "knowledge_point:不存在的标签",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_label"] == "不存在的标签"
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["total_pages"] == 1


def test_graph_evidence_page_past_end_is_empty_with_true_total(
    graph_client: TestClient,
) -> None:
    response = graph_client.post(
        "/api/graph/evidence",
        json={
            **_class_cross_exam_payload(),
            "knowledge_key": "knowledge_point:三角形全等",
            "page": 99,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 2
    assert payload["page"] == 99
    assert payload["total_pages"] == 2


def test_graph_routes_return_clear_empty_state_for_unknown_selection(
    graph_client: TestClient,
) -> None:
    response = graph_client.post(
        "/api/graph/rows",
        json={
            "scope": {"mode": "selected", "student_ids": ["999"]},
            "exam_scope": {"mode": "manual", "session_ids": [999]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == payload["nodes"] == payload["edges"] == []
    assert any("不存在" in warning for warning in payload["warnings"])


def test_graph_routes_reject_legacy_or_relation_controls(
    graph_client: TestClient,
) -> None:
    body = {**_query_payload(), "include_relations": True}

    response = graph_client.post("/api/graph/rows", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_graph_database_failures_are_sanitized() -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_request_diagnosis_profile_service

    class FailingService:
        @staticmethod
        def build_tag_profiles(**_kwargs):
            raise sqlite3.OperationalError("C:/private/question_bank.db is busy")

    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = FailingService
    response = TestClient(app).post(
        "/api/graph/profiles",
        headers={"x-request-id": "rid-graph-db"},
        json=_query_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "graph_database_unavailable",
        "message": "Graph data is temporarily unavailable",
        "details": {},
        "request_id": "rid-graph-db",
    }
    assert "C:/private" not in response.text


def test_graph_snapshot_failures_are_sanitized(
    graph_service: CountingDiagnosisService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.app import create_app
    import backend.api.read_connections as read_connections
    from path_manager import get_path_manager
    from question_bank.services.question_read_service import (
        QuestionBankSnapshotUnavailable,
    )

    def fail_snapshot(*_args, **_kwargs):
        raise QuestionBankSnapshotUnavailable("C:/private/grading.db changed")

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        fail_snapshot,
    )
    app = create_app()
    app.dependency_overrides[get_path_manager] = lambda: SimpleNamespace(
        db_path=graph_service.grading_path,
        qb_db_path=graph_service.question_bank_path,
    )
    response = TestClient(app).post(
        "/api/graph/profiles",
        headers={"x-request-id": "rid-graph-snapshot"},
        json=_query_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "graph_database_unavailable",
        "message": "Graph data is temporarily unavailable",
        "details": {},
        "request_id": "rid-graph-snapshot",
    }
    assert "C:/private" not in response.text


def _install_request_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    import backend.api.read_connections as read_connections

    real_capture = read_connections.captured_sqlite_read_connection
    cleanup_attempts: list[Path] = []

    @contextmanager
    def cleanup_fails(db_path: Path, **kwargs):
        try:
            with real_capture(db_path, **kwargs) as connection:
                yield connection
        finally:
            cleanup_attempts.append(Path(db_path))
            raise OSError("C:/private/cleanup.db")

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        cleanup_fails,
    )
    return cleanup_attempts


def test_graph_cleanup_only_failure_returns_sanitized_503_before_success_commit(
    graph_service: CountingDiagnosisService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.app import create_app
    from path_manager import get_path_manager

    cleanup_attempts = _install_request_cleanup_failure(monkeypatch)
    app = create_app()
    app.dependency_overrides[get_path_manager] = lambda: SimpleNamespace(
        db_path=graph_service.grading_path,
        qb_db_path=graph_service.question_bank_path,
    )
    response = TestClient(app).post(
        "/api/graph/profiles",
        headers={"x-request-id": "rid-graph-cleanup"},
        json=_query_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "graph_database_unavailable",
        "message": "Graph data is temporarily unavailable",
        "details": {},
        "request_id": "rid-graph-cleanup",
    }
    assert "C:/private" not in response.text
    assert cleanup_attempts == [
        graph_service.question_bank_path,
        graph_service.grading_path,
    ]


def test_graph_business_error_survives_request_cleanup_failure(
    graph_service: CountingDiagnosisService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.app import create_app
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from path_manager import get_path_manager

    cleanup_attempts = _install_request_cleanup_failure(monkeypatch)

    def fail_business(self, **_kwargs):
        raise ValueError("C:/private/business.db")

    monkeypatch.setattr(
        DiagnosisProfileService,
        "build_tag_profiles",
        fail_business,
    )
    app = create_app()
    app.dependency_overrides[get_path_manager] = lambda: SimpleNamespace(
        db_path=graph_service.grading_path,
        qb_db_path=graph_service.question_bank_path,
    )
    response = TestClient(app).post(
        "/api/graph/profiles",
        headers={"x-request-id": "rid-graph-business"},
        json=_query_payload(),
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "graph_scope_invalid",
        "message": "Graph scope is invalid",
        "details": {},
        "request_id": "rid-graph-business",
    }
    assert "C:/private" not in response.text
    assert cleanup_attempts == [
        graph_service.question_bank_path,
        graph_service.grading_path,
    ]


def _database_file_state(path: Path) -> dict[str, bytes | None]:
    return {
        suffix: candidate.read_bytes() if candidate.exists() else None
        for suffix in ("", "-wal", "-shm", "-journal")
        for candidate in (Path(f"{path}{suffix}"),)
    }


def test_graph_routes_do_not_open_or_change_source_databases(
    graph_service: CountingDiagnosisService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.app import create_app
    import integration.question_tag_projection_service as projection_module
    import question_bank.services.source_question_link_service as link_module
    from path_manager import get_path_manager

    grading_source = graph_service.grading_path.resolve()
    question_bank_source = graph_service.question_bank_path.resolve()
    gc.collect()
    before = {
        "grading": _database_file_state(grading_source),
        "question_bank": _database_file_state(question_bank_source),
    }

    original_db_connect = DBManager._connect
    original_projection_initialize = projection_module.initialize_database
    original_link_initialize = link_module.initialize_database

    def guarded_db_connect(manager: DBManager):
        assert manager.db_path.resolve() != grading_source
        return original_db_connect(manager)

    def guarded_projection_initialize(path: Path, *args, **kwargs):
        assert Path(path).resolve() != question_bank_source
        return original_projection_initialize(path, *args, **kwargs)

    def guarded_link_initialize(path: Path, *args, **kwargs):
        assert Path(path).resolve() != question_bank_source
        return original_link_initialize(path, *args, **kwargs)

    monkeypatch.setattr(DBManager, "_connect", guarded_db_connect)
    monkeypatch.setattr(
        projection_module,
        "initialize_database",
        guarded_projection_initialize,
    )
    monkeypatch.setattr(
        link_module,
        "initialize_database",
        guarded_link_initialize,
    )

    app = create_app()
    app.dependency_overrides[get_path_manager] = lambda: SimpleNamespace(
        db_path=graph_service.grading_path,
        qb_db_path=graph_service.question_bank_path,
    )
    client = TestClient(app)

    requests = (
        ("/api/graph/profiles", _class_cross_exam_payload()),
        ("/api/graph/rows", _class_cross_exam_payload()),
        (
            "/api/graph/evidence",
            {
                **_class_cross_exam_payload(),
                "knowledge_key": "knowledge_point:三角形全等",
            },
        ),
    )
    for path, body in requests:
        assert client.post(path, json=body).status_code == 200

    assert _database_file_state(grading_source) == before["grading"]
    assert _database_file_state(question_bank_source) == before["question_bank"]
