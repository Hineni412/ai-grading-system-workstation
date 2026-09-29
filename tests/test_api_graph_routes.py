from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_ops_write_service,
    get_request_diagnosis_profile_service,
)
from backend.api.routers.graph import get_current_graph_query_service
from db_manager import DBManager
from question_bank.database.schema import initialize_database
from question_bank.relations.query_service import CurrentKnowledgeGraphQueryService
from tests.current_knowledge_support import install_current_knowledge
from path_manager import PathManager
from backend.repositories.grading_database import open_grading_repositories


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


class _EvidenceDiagnosisService:
    def __init__(
        self,
        grading_db_path: Path,
        profile: dict[str, object],
    ) -> None:
        self.db = open_grading_repositories(grading_db_path)
        self._profile = profile

    def build_tag_profiles(self, **_kwargs):
        return self._profile

    def mastery_session_times(self, **_kwargs):
        return {}


def _seed_evidence_grading_database(
    tmp_path: Path,
) -> tuple[Path, int, dict[str, dict[str, object]]]:
    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    seeded: dict[str, dict[str, object]] = {}
    with sqlite3.connect(database) as connection:
        student_id = int(
            connection.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('012', 'Beta', 'Class-B')
                """
            ).lastrowid
        )
        for session_name, reason in (
            ("阶段测A", "漏写单位"),
            ("阶段测B", "移项符号错误"),
        ):
            session_id = int(
                connection.execute(
                    """
                    INSERT INTO grading_sessions (
                        session_name, rubric_path, answer_key_path, status
                    ) VALUES (?, '', '', 'completed')
                    """,
                    (session_name,),
                ).lastrowid
            )
            paper_id = int(
                connection.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image,
                        ocr_name, student_id, match_status, processing_status
                    ) VALUES (
                        ?, 'front.png', 'back.png', 'Beta', ?, 'matched', 'graded'
                    )
                    """,
                    (session_id, student_id),
                ).lastrowid
            )
            result_id = int(
                connection.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id,
                        total_score, student_score, needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 10, 6, 0, '{}')
                    """,
                    (session_id, student_id, paper_id),
                ).lastrowid
            )
            detail_id = int(
                connection.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded,
                        deduction_reason, knowledge_ids
                    ) VALUES (
                        ?, 'Q1', 6, ?, '["knowledge_point:一元一次方程"]'
                    )
                    """,
                    (result_id, reason),
                ).lastrowid
            )
            seeded[session_name] = {
                "session_id": session_id,
                "result_id": result_id,
                "detail_id": detail_id,
                "deduction_reason": reason,
            }
    return database, student_id, seeded


def _evidence_profile(
    session_a: int,
    session_b: int,
    student_id: int,
) -> dict[str, object]:
    references = [
        {
            "session_id": session_a,
            "session_name": "阶段测A",
            "question_id": "Q1",
            "bank_question_id": 1,
            "score_awarded": 6,
            "full_score": 10,
        },
        {
            "session_id": session_a,
            "session_name": "阶段测A",
            "question_id": "Q2",
            "bank_question_id": 2,
            "score_awarded": 4,
            "full_score": 10,
        },
        {
            "session_id": session_b,
            "session_name": "阶段测B",
            "question_id": "Q1",
            "bank_question_id": 1,
            "score_awarded": 6,
            "full_score": 10,
        },
    ]
    return {
        "scope": {
            "mode": "student",
            "student_ids": [str(student_id)],
            "class_id": None,
        },
        "exam_scope": {
            "mode": "current",
            "session_ids": [session_a, session_b],
            "sessions": [
                {"session_id": session_a, "session_name": "阶段测A"},
                {"session_id": session_b, "session_name": "阶段测B"},
            ],
        },
        "coverage": {"covered_items": 1, "total_items": 1, "missing_items": {}},
        "warnings": [],
        "diagnosis_identity": "question_tag",
        "students": [
            {
                "student_id": student_id,
                "weak_points": [
                    {
                        "knowledge_point": "一元一次方程",
                        "knowledge_key": "knowledge_point:一元一次方程",
                        "source_question_refs": references,
                        "deduction_count": 1,
                        "evidence_count": 1,
                        "tag_context": {},
                        "error_counts": {"primary": {}, "secondary": {}},
                    }
                ],
            }
        ],
    }


def _evidence_client(
    tmp_path: Path,
) -> tuple[TestClient, dict[str, dict[str, object]]]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    grading_db, student_id, seeded = _seed_evidence_grading_database(tmp_path)
    session_a = int(seeded["阶段测A"]["session_id"])
    session_b = int(seeded["阶段测B"]["session_id"])
    service = _EvidenceDiagnosisService(
        grading_db,
        _evidence_profile(session_a, session_b, student_id),
    )
    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = (
        lambda: service
    )
    app.dependency_overrides[get_current_graph_query_service] = lambda: (
        CurrentKnowledgeGraphQueryService(database)
    )
    return TestClient(app), seeded


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


def test_application_startup_installs_current_standard_when_none_is_active(
    tmp_path: Path,
) -> None:
    paths = PathManager()
    paths._data_root = tmp_path / "user_data"
    paths._logs_root = tmp_path / "logs"
    paths.ensure_directories()
    app = create_app(path_manager=paths)
    app.dependency_overrides[get_job_manager] = lambda: object()
    app.dependency_overrides[get_ops_write_service] = lambda: object()

    with TestClient(app) as client:
        assert client.get("/api/healthz").status_code == 200

    resolver = CurrentKnowledgeGraphQueryService(paths.qb_db_path).resolver
    assert resolver.release_id


def test_current_graph_evidence_items_include_assessment_detail_fields(
    tmp_path: Path,
) -> None:
    client, seeded = _evidence_client(tmp_path)
    session_a = int(seeded["阶段测A"]["session_id"])
    session_b = int(seeded["阶段测B"]["session_id"])

    response = client.post(
        "/api/graph/evidence",
        json={
            "scope": {"mode": "student", "student_ids": ["1"]},
            "exam_scope": {
                "mode": "current",
                "session_ids": [session_a, session_b],
            },
            "stable_key": "kp_alg_linear_equation",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 3
    by_row = {
        (item["session_id"], item["question_id"]): item for item in items
    }
    for session_name in ("阶段测A", "阶段测B"):
        info = seeded[session_name]
        item = by_row[(info["session_id"], "Q1")]
        assert item["detail_id"] == info["detail_id"]
        assert item["deduction_reason"] == info["deduction_reason"]
        assert item["evidence_url"] == (
            f"/api/sessions/{info['session_id']}"
            f"/results/{info['result_id']}"
            f"/details/{info['detail_id']}/crop"
        )
    assert (
        by_row[(session_a, "Q1")]["evidence_url"]
        != by_row[(session_b, "Q1")]["evidence_url"]
    )
