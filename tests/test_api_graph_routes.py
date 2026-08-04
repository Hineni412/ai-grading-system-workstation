from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_ops_write_service,
    get_request_diagnosis_profile_service,
)
from backend.api.routers.graph import get_current_graph_query_service
from question_bank.database.schema import initialize_database
from question_bank.relations.query_service import CurrentKnowledgeGraphQueryService
from tests.current_knowledge_support import install_current_knowledge
from backend.workspaces.registry import WorkspaceRegistry
from path_manager import PathManager


class _DiagnosisService:
    def build_tag_profiles(self, **_kwargs):
        return _profile()

    def mastery_session_times(self, **_kwargs):
        return {14: "2026-07-30T08:00:00+08:00"}


def _profile() -> dict[str, object]:
    return {
        "scope": {"mode": "student", "student_ids": ["12"], "class_id": None},
        "exam_scope": {
            "mode": "current",
            "session_ids": [14],
            "sessions": [{"session_id": 14, "session_name": "合成考试"}],
        },
        "coverage": {"covered_items": 1, "total_items": 1, "missing_items": {}},
        "warnings": [],
        "diagnosis_identity": "question_tag",
        "students": [
            {
                "student_id": 12,
                "weak_points": [
                    {
                        "knowledge_point": "一元一次方程",
                        "knowledge_key": "knowledge_point:一元一次方程",
                        "source_question_refs": [
                            {
                                "session_id": 14,
                                "question_id": "Q1",
                                "bank_question_id": 1,
                                "score_awarded": 6,
                                "full_score": 10,
                            }
                        ],
                        "deduction_count": 1,
                        "evidence_count": 1,
                        "tag_context": {},
                        "error_counts": {"primary": {}, "secondary": {}},
                    }
                ],
            }
        ],
    }


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = _DiagnosisService
    app.dependency_overrides[get_current_graph_query_service] = lambda: (
        CurrentKnowledgeGraphQueryService(database)
    )
    return TestClient(app)


def test_openapi_exposes_only_current_graph_read_contract() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/graph/query" in paths
    assert "/api/graph/evidence" in paths
    assert "/api/graph/profiles" not in paths
    assert "/api/graph/rows" not in paths
    assert not any(path.startswith("/api/graph/v2") for path in paths)
    assert not any("mastery" in path and "graph" in path for path in paths)


def test_current_graph_api_returns_one_mastery_and_authority(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/graph/query",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
            "knowledge_keys": ["kp_alg_linear_equation"],
            "prerequisite_depth": 0,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["response_schema_version"] == "knowledge-graph-current"
    node = payload["nodes"][0]
    assert set(node).issuperset(
        {
            "definition",
            "include_scope",
            "exclude_scope",
            "curriculum_anchors",
            "observable_evidence",
            "rationale",
            "evidence_source_ids",
            "mastery",
        }
    )
    assert "mastery_v1" not in node and "mastery_v2" not in node


def test_removed_graph_paths_return_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
    }
    for path in ("/api/graph/profiles", "/api/graph/rows", "/api/graph/v2/query"):
        assert client.post(path, json=body).status_code == 404


def test_application_startup_installs_current_standard_when_none_is_active(
    tmp_path: Path,
) -> None:
    paths = PathManager()
    paths._data_root = tmp_path / "user_data"
    paths._logs_root = tmp_path / "logs"
    paths.ensure_directories()
    registry = WorkspaceRegistry((), paths=paths)
    app = create_app(path_manager=paths, workspace_registry=registry)
    app.dependency_overrides[get_job_manager] = lambda: object()
    app.dependency_overrides[get_ops_write_service] = lambda: object()

    with TestClient(app) as client:
        assert client.get("/api/healthz").status_code == 200

    resolver = CurrentKnowledgeGraphQueryService(paths.qb_db_path).resolver
    assert resolver.release_id
