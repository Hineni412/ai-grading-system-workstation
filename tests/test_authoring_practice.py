from __future__ import annotations

import json
import sqlite3
import warnings
from pathlib import Path

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from question_bank.authoring import AuthoringService
from question_bank.database.schema import initialize_database
from question_bank.services.question_read_service import QuestionBankReadService
from tests.current_knowledge_support import install_current_knowledge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTHORING_SCHEMA = (
    PROJECT_ROOT
    / "question_bank"
    / "authoring"
    / "schema_043_add_authoring_practice.sql"
)


def _token(seed: int) -> str:
    return f"{seed:032x}"


@pytest.fixture
def authoring_env(tmp_path: Path):
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(AUTHORING_SCHEMA.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO papers (id, title, import_status)"
            " VALUES (1, 'Authoring paper', 'success')"
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text, difficulty, is_deleted
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "1",
                    "解答题",
                    "已知函数 $f(x)=x^2$，求最值",
                    "最小值为 0",
                    "5",
                    0,
                ),
                (2, "2", "选择题", "另一道母题", "B", "3", 0),
                (3, "3", "填空题", "已删除母题", "", "2", 1),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, ?, ?)
            """,
            [
                ("knowledge_point", "二次函数"),
                ("ability", "运算能力"),
                ("exam_scope", "九年级上册"),
            ],
        )
        conn.execute(
            """
            INSERT INTO question_error_patterns (
                question_id, category, pattern, explanation, trigger_kind,
                trigger_value, status, source, occurrences_json
            ) VALUES (1, '运算', '符号处理错误', '移项忘记变号',
                      'step', '', 'confirmed', 'teacher_edit', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO training_criterion_versions (
                version_id, question_id, version_number, parent_version_id,
                source_content_hash, schema_version, status, source_kind,
                source_reference, criteria_json, criteria_hash,
                quality_status, quality_codes_json, created_by
            ) VALUES (?, 1, 1, NULL, ?, 'judgment-points-v1',
                      'approved', 'teacher_manual', 'test:authoring', ?, ?,
                      'passed', '[]', 'test')
            """,
            (
                "a" * 64,
                "b" * 64,
                json.dumps(
                    {
                        "points": [
                            {
                                "point_id": "p1",
                                "target": "配方或求导找最值",
                                "observable_evidence": "写出最小值 0",
                            }
                        ],
                        "auxiliary_rules": [],
                        "rationale": "覆盖最值求解",
                    },
                    ensure_ascii=False,
                ),
                "c" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO training_criterion_heads (
                question_id, current_version_id, approved_version_id,
                current_source_hash, revision
            ) VALUES (1, ?, ?, ?, 1)
            """,
            ("a" * 64, "a" * 64, "b" * 64),
        )
        conn.commit()

    data_root = tmp_path / "data"
    data_root.mkdir()
    service = AuthoringService(db_path, data_root=data_root)

    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_authoring_service,
        get_data_root,
        get_question_bank_db_path,
        get_question_bank_read_service,
    )

    app = create_app()
    app.dependency_overrides[get_authoring_service] = lambda: service
    app.dependency_overrides[get_question_bank_db_path] = lambda: db_path
    app.dependency_overrides[get_data_root] = lambda: data_root
    app.dependency_overrides[get_question_bank_read_service] = lambda: (
        QuestionBankReadService(db_path, data_root=data_root)
    )
    return TestClient(app), db_path


def _create_decompose(client: TestClient, token: int = 1) -> dict:
    response = client.post(
        "/api/authoring/works",
        json={
            "kind": "decompose",
            "source_question_id": 1,
            "title": "拆解练习",
            "operation_token": _token(token),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_version_save_conflict_and_idempotent_replay(authoring_env) -> None:
    client, _ = authoring_env
    work = _create_decompose(client, token=30)
    work_id = work["work_id"]

    content = {
        "intent": "练习拆解压轴题",
        "knowledge_points": ["二次函数"],
        "key_steps": ["配方"],
        "expected_errors": ["符号处理错误"],
        "predicted_difficulty": 7,
        "predicted_solo": "relational",
        "parts": [
            {
                "part_label": "（1）",
                "predicted_difficulty": 4,
                "predicted_solo": "unistructural",
            }
        ],
        "unknown_field_is_dropped": "x",
    }
    saved = client.post(
        f"/api/authoring/works/{work_id}/versions",
        json={
            "content": content,
            "base_version": 0,
            "operation_token": _token(31),
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version_no"] == 1
    assert body["current_version"] == 1
    assert "unknown_field_is_dropped" not in body["content"]

    replayed = client.post(
        f"/api/authoring/works/{work_id}/versions",
        json={
            "content": content,
            "base_version": 0,
            "operation_token": _token(31),
        },
    )
    assert replayed.status_code == 200
    assert replayed.json()["version_no"] == 1

    stale = client.post(
        f"/api/authoring/works/{work_id}/versions",
        json={
            "content": {"intent": "第二版"},
            "base_version": 0,
            "operation_token": _token(32),
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "authoring_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 1

    token_conflict = client.post(
        f"/api/authoring/works/{work_id}/versions",
        json={
            "content": {"intent": "不同内容"},
            "base_version": 1,
            "operation_token": _token(31),
        },
    )
    assert token_conflict.status_code == 409
    assert token_conflict.json()["error"]["code"] == "authoring_token_conflict"

    second = client.post(
        f"/api/authoring/works/{work_id}/versions",
        json={
            "content": {**content, "intent": "第二版"},
            "base_version": 1,
            "operation_token": _token(33),
        },
    )
    assert second.status_code == 200
    assert second.json()["version_no"] == 2

    detail = client.get(f"/api/authoring/works/{work_id}").json()
    assert detail["current_version"] == 2
    assert [v["version_no"] for v in detail["versions"]] == [1, 2]
    assert detail["latest_content"]["intent"] == "第二版"

    first_version = client.get(f"/api/authoring/works/{work_id}/versions/1").json()
    assert first_version["content"]["intent"] == "练习拆解压轴题"
