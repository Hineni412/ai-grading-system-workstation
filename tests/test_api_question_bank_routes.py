from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import warnings
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from question_bank.database.schema import initialize_database
import question_bank.services.question_read_service as question_read_module
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.solution_evidence import (
    CoreResolution,
    QuestionSolutionEvidence,
    SolutionEvidenceRepository,
)
from question_bank.training_criteria import (
    QuestionAnalysisInputLoader,
    solution_evidence_source_content_hash,
)
from tests.current_knowledge_support import install_current_knowledge


@pytest.fixture
def question_bank_fixture(
    tmp_path: Path,
) -> tuple[QuestionBankReadService, Path, bytes]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO papers (
                id, title, source_file, content_fingerprint, import_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Empty", "C:/private/empty.docx", "empty-secret", "success", "2026-01-01 00:00:00"),
                (2, "Newest", "C:/private/newest.docx", "newest-secret", "success", "2026-01-02 00:00:00"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, is_deleted
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 2, "1", "Active question", 0),
                (2, 2, "2", "Deleted question", 1),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (?, ?, ?)
            """,
            [
                (1, "knowledge_point", "一次函数"),
                (2, "ability", "Deleted tag"),
            ],
        )
        conn.commit()

    before = db_path.read_bytes()
    return QuestionBankReadService(db_path), db_path, before


def test_read_service_lists_active_papers_without_paths_or_writes(question_bank_fixture):
    service, db_path, _ = question_bank_fixture
    before = db_path.read_bytes()

    papers = service.list_papers()

    assert [item["title"] for item in papers] == ["Newest", "Empty"]
    assert papers[0]["question_count"] == 1
    assert papers[0]["tagged_question_count"] == 0
    assert papers[0]["tagged_any_question_count"] == 1
    assert papers[1]["question_count"] == 0
    assert papers[1]["tagged_question_count"] == 0
    assert "source_file" not in repr(papers)
    assert "content_fingerprint" not in repr(papers)
    assert db_path.read_bytes() == before


def test_question_read_payload_exposes_stable_state_revision(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture

    detail = service.get_question(1)
    page_item = service.list_questions(
        question_read_module.QuestionReadFilters()
    ).items[0]

    assert detail is not None
    assert detail["revision"] == page_item["revision"]
    assert len(detail["revision"]) == 64
    before_revision = detail["revision"]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'method', 'Revision change')
            """
        )
        conn.commit()

    changed = service.get_question(1)
    assert changed is not None
    assert changed["revision"] != before_revision


@pytest.mark.parametrize("changed_content", ["stem", "answer", "media"])
def test_solution_evidence_route_returns_latest_point_level_union(
    question_bank_fixture,
    changed_content: str,
) -> None:
    service, db_path, _ = question_bank_fixture
    data_root = db_path.parent / "data"
    client = _question_bank_client(
        service,
        question_bank_db_path=db_path,
        data_root=data_root,
    )

    empty = client.get("/api/question-bank/questions/1/solution-evidence")
    assert empty.status_code == 200
    assert empty.json() == {
        "question_id": 1,
        "available": False,
        "evidence_version_id": None,
        "status": None,
        "evidence": None,
    }

    class Resolver:
        def resolve(self, fine_term_id: str) -> CoreResolution:
            if fine_term_id == "fine-direct":
                return CoreResolution(
                    status="resolved",
                    stable_keys=("kp_equation",),
                    reason="test mapping",
                )
            return CoreResolution(status="unmapped", reason="test unmapped")

    evidence = QuestionSolutionEvidence.from_model_dict(
        {
            "schema_version": "question-solution-evidence-v1",
            "question_id": 1,
            "parts": [
                {
                    "part_id": "part-1",
                    "label": "（1）",
                    "response_mode": "process_required",
                    "canonical_answer": "x=2",
                    "accepted_forms": ["x = 2"],
                    "full_answer": "移项后求得 x=2。",
                    "proof_obligations": [],
                    "visual_requirements": [],
                    "deduction_policy": ["没有等价变形过程则该点未达成"],
                    "allow_alternative_methods": True,
                    "evidence_points": [
                        {
                            "evidence_point_id": "point-1",
                            "target": "求出方程的解",
                            "observable_evidence": "给出等价变形并写出正确解。",
                            "fine_term_links": [
                                {
                                    "fine_term_id": "fine-direct",
                                    "fine_term_name": "一元一次方程求解",
                                    "role": "direct",
                                },
                                {
                                    "fine_term_id": "fine-support",
                                    "fine_term_name": "规范移项",
                                    "role": "supporting_prerequisite",
                                },
                            ],
                            "equivalent_rules": [],
                            "counterexamples": [],
                        }
                    ],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "按踩分点拆分。",
            "confidence": 0.9,
        },
        question_id=1,
        source_content_hash=solution_evidence_source_content_hash(
            QuestionAnalysisInputLoader(
                db_path=db_path,
                data_root=data_root,
            ).load((1,))[0]
        ),
        resolver=Resolver(),
    )
    version_id = SolutionEvidenceRepository(db_path).save(
        evidence,
        source_kind="combined_model",
        source_reference="analysis:test-route:1",
        created_by="model:synthetic",
    )

    response = client.get("/api/question-bank/questions/1/solution-evidence")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["evidence_version_id"] == version_id
    assert body["evidence"]["parts"][0]["evidence_points"][0]["target"] == "求出方程的解"
    union = body["evidence"]["whole_question_classification"]
    assert union["direct_fine_terms"] == [
        {"fine_term_id": "fine-direct", "fine_term_name": "一元一次方程求解"}
    ]
    assert union["supporting_prerequisite_fine_terms"] == [
        {"fine_term_id": "fine-support", "fine_term_name": "规范移项"}
    ]
    assert union["resolved_core_node_ids"] == ["kp_equation"]
    assert union["unmapped_fine_term_ids"] == ["fine-support"]

    with sqlite3.connect(db_path) as conn:
        if changed_content == "stem":
            conn.execute(
                "UPDATE questions SET question_text = 'Changed stem' WHERE id = 1"
            )
        elif changed_content == "answer":
            conn.execute(
                "UPDATE questions SET answer_text = 'Changed answer' WHERE id = 1"
            )
        else:
            image = data_root / "question_bank" / "extracted_images" / "changed.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"synthetic changed image")
            conn.execute(
                """
                UPDATE questions
                SET has_images = 1, image_paths = ?
                WHERE id = 1
                """,
                (json.dumps(["question_bank/extracted_images/changed.png"]),),
            )
        conn.commit()

    stale = client.get("/api/question-bank/questions/1/solution-evidence")
    assert stale.status_code == 200
    assert stale.json() == {
        "question_id": 1,
        "available": False,
        "evidence_version_id": version_id,
        "status": "stale",
        "evidence": None,
    }


def test_question_facets_include_curriculum_sections(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'curriculum_section', 'section-linear-functions')
            """
        )
        conn.commit()

    response = _question_bank_client(service).get("/api/question-bank/facets")

    assert response.status_code == 200
    assert response.json()["curriculum_sections"] == [
        {"value": "section-linear-functions", "count": 1}
    ]


def test_questions_and_facets_support_special_type_filter(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'special_type', '动态几何题')
            """
        )
        conn.commit()
    client = _question_bank_client(service)

    page = client.get(
        "/api/question-bank/questions",
        params=[("special_types", "动态几何题")],
    )
    facets = client.get("/api/question-bank/facets")

    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [1]
    assert {
        item["value"]: item["count"]
        for item in facets.json()["special_types"]
    }["动态几何题"] == 1


def test_legacy_knowledge_value_filters_and_facets_as_current_canonical_term(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'knowledge_point', '一次函数的实际应用')
            """
        )
        conn.commit()
    client = _question_bank_client(service)

    page = client.get(
        "/api/question-bank/questions",
        params=[("knowledge_points", "一次函数应用")],
    )
    facets = client.get("/api/question-bank/facets")

    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [1]
    knowledge_facets = {
        item["value"]: item["count"]
        for item in facets.json()["knowledge_points"]
    }
    assert knowledge_facets["一次函数应用"] == 1
    assert "一次函数的实际应用" not in knowledge_facets


def test_unknown_knowledge_filter_returns_no_questions(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'knowledge_point', '一次函数的实际应用')
            """
        )
        conn.commit()
    client = _question_bank_client(service)

    response = client.get(
        "/api/question-bank/questions",
        params=[("knowledge_points", "不存在的知识点")],
    )
    mixed = client.get(
        "/api/question-bank/questions",
        params=[
            ("knowledge_points", "一次函数应用"),
            ("knowledge_points", "不存在的知识点"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert mixed.status_code == 200
    assert [item["id"] for item in mixed.json()["items"]] == [1]


def test_legacy_method_thought_projects_to_thought_display_filter_and_facet(
    question_bank_fixture,
) -> None:
    service, db_path, _ = question_bank_fixture
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (1, 'method', '方程思想')
            """
        )
        conn.commit()
    client = _question_bank_client(service)

    page = client.get(
        "/api/question-bank/questions",
        params=[("thoughts", "方程思想")],
    )
    facets = client.get("/api/question-bank/facets").json()
    detail = client.get("/api/question-bank/questions/1").json()

    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [1]
    assert {item["value"] for item in facets["thoughts"]} >= {"方程思想"}
    assert "方程思想" not in {
        item["value"] for item in facets["methods"]
    }
    assert {
        (tag["tag_type"], tag["tag_value"])
        for tag in detail["tags"]
    } >= {("thought", "方程思想")}


def test_question_facets_do_not_expand_empty_taxonomy_filters_repeatedly(
    question_bank_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = question_bank_fixture
    governance = question_read_module.get_taxonomy_governance()
    original_expand = governance.expand_filter_values
    original_snapshot = governance.snapshot
    expand_calls: list[tuple[str, tuple[object, ...]]] = []
    snapshot_calls = 0

    def counted_expand(
        dimension: str,
        values: tuple[object, ...],
    ) -> tuple[str, ...]:
        expand_calls.append((dimension, tuple(values)))
        return original_expand(dimension, values)

    def counted_snapshot() -> dict[str, object]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_snapshot()

    monkeypatch.setattr(governance, "expand_filter_values", counted_expand)
    monkeypatch.setattr(governance, "snapshot", counted_snapshot)
    monkeypatch.setattr(
        question_read_module,
        "get_taxonomy_governance",
        lambda: governance,
    )

    service.list_facets(question_read_module.QuestionReadFilters())

    assert expand_calls == []
    assert snapshot_calls == 1


def test_question_facets_expand_each_selected_taxonomy_dimension_once(
    question_bank_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = question_bank_fixture
    governance = question_read_module.get_taxonomy_governance()
    original_expand = governance.expand_filter_values
    expand_calls: list[tuple[str, tuple[object, ...]]] = []

    def counted_expand(
        dimension: str,
        values: tuple[object, ...],
    ) -> tuple[str, ...]:
        expand_calls.append((dimension, tuple(values)))
        return original_expand(dimension, values)

    monkeypatch.setattr(governance, "expand_filter_values", counted_expand)
    monkeypatch.setattr(
        question_read_module,
        "get_taxonomy_governance",
        lambda: governance,
    )

    service.list_facets(
        question_read_module.QuestionReadFilters(
            exam_scopes=("七年级上册 第一章",),
            abilities=("推理能力",),
        )
    )

    assert expand_calls == [
        ("curriculum", ("七年级上册 第一章",)),
        ("ability", ("推理能力",)),
    ]


def test_question_facets_exclude_their_own_dimension_but_keep_other_filters(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (1, 'Facet paper', 'success')
            """
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text
            ) VALUES (?, 1, ?, ?, ?)
            """,
            [
                (1, "1", "选择题", "目标知识点"),
                (2, "2", "选择题", "同章节可选知识点"),
                (3, "3", "选择题", "不同能力"),
                (4, "4", "选择题", "不同章节"),
                (5, "5", "填空题", "不同题型和知识点"),
                (6, "6", "填空题", "同知识点的另一题型"),
            ],
        )
        tags_by_question = {
            1: ("一次函数", "运算能力", "八年级上册", "小节甲"),
            2: ("二次函数", "运算能力", "八年级上册", "小节甲"),
            3: ("一元一次方程", "推理能力", "八年级上册", "小节甲"),
            4: ("实数", "运算能力", "九年级上册", "小节乙"),
            5: ("整式", "运算能力", "八年级上册", "小节甲"),
            6: ("一次函数", "运算能力", "八年级上册", "小节甲"),
        }
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (?, ?, ?)
            """,
            [
                (question_id, tag_type, value)
                for question_id, values in tags_by_question.items()
                for tag_type, value in zip(
                    (
                        "knowledge_point",
                        "ability",
                        "exam_scope",
                        "curriculum_section",
                    ),
                    values,
                    strict=True,
                )
            ],
        )
        conn.commit()

    response = _question_bank_client(QuestionBankReadService(db_path)).get(
        "/api/question-bank/facets",
        params=[
            ("knowledge_points", "一次函数"),
            ("abilities", "运算能力"),
            ("exam_scopes", "八年级上册"),
            ("curriculum_sections", "小节甲"),
            ("question_types", "选择题"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_points"] == [
        {"value": "一次函数", "count": 1},
        {"value": "二次函数", "count": 1},
    ]
    assert payload["question_types"] == [
        {"value": "填空题", "count": 1},
        {"value": "选择题", "count": 1},
    ]


def _question_bank_client(
    service: QuestionBankReadService,
    *,
    question_bank_db_path: Path | None = None,
    data_root: Path | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_data_root,
        get_question_bank_db_path,
        get_question_bank_read_service,
    )

    app = create_app()
    app.dependency_overrides[get_question_bank_read_service] = lambda: service
    if question_bank_db_path is not None:
        app.dependency_overrides[get_question_bank_db_path] = (
            lambda: question_bank_db_path
        )
    if data_root is not None:
        app.dependency_overrides[get_data_root] = lambda: data_root
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _wal_source_state(db_path: Path) -> dict[str, tuple[bool, bytes | None]]:
    paths = {
        "main": db_path,
        "wal": Path(f"{db_path}-wal"),
        "shm": Path(f"{db_path}-shm"),
    }
    return {
        name: (path.exists(), path.read_bytes() if path.exists() else None)
        for name, path in paths.items()
    }


def _open_wal_writer(db_path: Path, *, paper_id: int, title: str) -> sqlite3.Connection:
    initialize_database(db_path)
    writer = sqlite3.connect(db_path, check_same_thread=False)
    assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    assert checkpoint is not None and checkpoint[0] == 0
    main_before_commit = db_path.read_bytes()
    writer.execute(
        "INSERT INTO papers (id, title, import_status) VALUES (?, ?, 'success')",
        (paper_id, title),
    )
    writer.commit()
    assert db_path.read_bytes() == main_before_commit
    assert Path(f"{db_path}-wal").stat().st_size > 0
    assert Path(f"{db_path}-shm").exists()
    return writer


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri.split("?", 1)[0])
    path_text = unquote(parsed.path)
    if os.name == "nt" and path_text.startswith("/"):
        path_text = path_text[1:]
    return Path(path_text)


def test_quiescent_wal_read_keeps_source_bytes_and_sidecars_absent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quiescent.db"
    initialize_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint is not None and checkpoint[0] == 0
    before = _wal_source_state(db_path)
    assert before["wal"] == (False, None)
    assert before["shm"] == (False, None)

    assert QuestionBankReadService(db_path).list_papers() == []

    assert _wal_source_state(db_path) == before


def test_active_wal_snapshot_sees_committed_row_without_changing_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "active.db"
    writer = _open_wal_writer(db_path, paper_id=601, title="Committed in WAL")
    try:
        before = _wal_source_state(db_path)

        papers = QuestionBankReadService(db_path).list_papers()

        assert [paper["title"] for paper in papers] == ["Committed in WAL"]
        assert _wal_source_state(db_path) == before
    finally:
        writer.close()


def test_wal_checkpoint_between_capture_and_compare_retries_stable_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "checkpoint-race.db"
    writer = _open_wal_writer(db_path, paper_id=602, title="Checkpointed row")
    attempts: list[int] = []

    def checkpoint_after_first_wal_capture(source: Path, attempt: int) -> None:
        assert source == db_path
        attempts.append(attempt)
        if attempt == 1:
            checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            assert checkpoint is not None and checkpoint[0] == 0

    monkeypatch.setattr(
        question_read_module,
        "_snapshot_compare_hook",
        checkpoint_after_first_wal_capture,
        raising=False,
    )
    try:
        papers = QuestionBankReadService(db_path).list_papers()
    finally:
        writer.close()

    assert attempts == [1, 2]
    assert [paper["title"] for paper in papers] == ["Checkpointed row"]


def test_exhausted_wal_instability_maps_to_sanitized_busy_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "busy.db"
    writer = _open_wal_writer(db_path, paper_id=603, title="Initial row")
    attempts: list[int] = []

    def commit_after_every_wal_capture(source: Path, attempt: int) -> None:
        assert source == db_path
        attempts.append(attempt)
        writer.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (?, ?, 'success')",
            (700 + attempt, f"Busy row {attempt}"),
        )
        writer.commit()

    monkeypatch.setattr(
        question_read_module,
        "_snapshot_compare_hook",
        commit_after_every_wal_capture,
        raising=False,
    )
    original_connect = sqlite3.connect
    connect_targets: list[str] = []

    def connect_spy(database: object, *args: object, **kwargs: object):
        connect_targets.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(question_read_module.sqlite3, "connect", connect_spy)
    client = _question_bank_client(
        QuestionBankReadService(db_path),
        raise_server_exceptions=False,
    )
    try:
        response = client.get(
            "/api/question-bank/papers",
            headers={"x-request-id": "rid-snapshot-busy"},
        )
    finally:
        writer.close()

    assert attempts == [1, 2, 3, 4]
    assert connect_targets == []
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": "question_bank_snapshot_busy",
            "message": "Question bank snapshot is temporarily busy",
            "details": {},
            "request_id": "rid-snapshot-busy",
        }
    }
    assert str(db_path) not in response.text
    assert "Busy row" not in response.text


def test_read_service_sqlite_connects_only_to_cleaned_system_temp_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "connect-spy.db"
    initialize_database(db_path)
    original_connect = sqlite3.connect
    connect_targets: list[str] = []

    def connect_spy(database: object, *args: object, **kwargs: object):
        connect_targets.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(question_read_module.sqlite3, "connect", connect_spy)

    assert QuestionBankReadService(db_path).list_papers() == []

    assert len(connect_targets) == 1
    assert not connect_targets[0].startswith(db_path.resolve().as_uri())
    assert "immutable" not in connect_targets[0].casefold()
    assert "nolock" not in connect_targets[0].casefold()
    candidate = _path_from_file_uri(connect_targets[0])
    candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    assert not candidate.exists()
    assert not candidate.parent.exists()


@pytest.mark.parametrize(
    "route",
    [
        "/api/question-bank/papers",
        "/api/question-bank/questions",
        "/api/question-bank/questions/1",
        "/api/question-bank/questions/1/assets/0",
        "/api/question-bank/questions/1/previews/question",
    ],
)
def test_all_question_bank_routes_map_missing_snapshot_to_unavailable(
    tmp_path: Path,
    route: str,
) -> None:
    db_path = tmp_path / "missing.db"
    client = _question_bank_client(
        QuestionBankReadService(db_path),
        raise_server_exceptions=False,
    )

    response = client.get(route, headers={"x-request-id": "rid-snapshot-unavailable"})

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "retry-after" not in response.headers
    assert response.json() == {
        "error": {
            "code": "question_bank_snapshot_unavailable",
            "message": "Question bank snapshot is unavailable",
            "details": {},
            "request_id": "rid-snapshot-unavailable",
        }
    }
    assert str(db_path) not in response.text


class _SnapshotBusyService:
    @staticmethod
    def _raise() -> None:
        error_type = getattr(question_read_module, "QuestionBankSnapshotBusy")
        raise error_type("internal source path must not escape")

    def list_papers(self) -> None:
        self._raise()

    def list_questions(self, _filters: object) -> None:
        self._raise()

    def get_question(self, _question_id: int) -> None:
        self._raise()

    def resolve_asset(self, _question_id: int, _asset_index: int) -> None:
        self._raise()

    def resolve_preview(self, _question_id: int, _preview_type: str) -> None:
        self._raise()


@pytest.mark.parametrize(
    "route",
    [
        "/api/question-bank/papers",
        "/api/question-bank/questions",
        "/api/question-bank/questions/1",
        "/api/question-bank/questions/1/assets/0",
        "/api/question-bank/questions/1/previews/question",
    ],
)
def test_all_question_bank_routes_map_busy_snapshot_to_retryable_503(
    route: str,
) -> None:
    client = _question_bank_client(
        _SnapshotBusyService(),  # type: ignore[arg-type]
        raise_server_exceptions=False,
    )

    response = client.get(route, headers={"x-request-id": "rid-all-routes-busy"})

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "error": {
            "code": "question_bank_snapshot_busy",
            "message": "Question bank snapshot is temporarily busy",
            "details": {},
            "request_id": "rid-all-routes-busy",
        }
    }
    assert "internal source path" not in response.text


@pytest.mark.parametrize("invalid_kind", ["corrupt", "missing_tables"])
def test_stable_invalid_snapshot_maps_to_unavailable(
    tmp_path: Path,
    invalid_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / f"{invalid_kind}.db"
    if invalid_kind == "corrupt":
        db_path.write_bytes(b"not-a-sqlite-database")
    else:
        with closing(sqlite3.connect(db_path)):
            pass
    original_connect = sqlite3.connect
    connect_targets: list[str] = []

    def connect_spy(database: object, *args: object, **kwargs: object):
        connect_targets.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(question_read_module.sqlite3, "connect", connect_spy)
    client = _question_bank_client(
        QuestionBankReadService(db_path),
        raise_server_exceptions=False,
    )

    response = client.get(
        "/api/question-bank/papers",
        headers={"x-request-id": "rid-invalid-snapshot"},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "question_bank_snapshot_unavailable"
    assert response.json()["error"]["request_id"] == "rid-invalid-snapshot"
    assert str(db_path) not in response.text
    assert len(connect_targets) == 1
    candidate = _path_from_file_uri(connect_targets[0])
    candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    assert not candidate.exists()
    assert not candidate.parent.exists()


def test_snapshot_query_error_closes_connection_before_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "invalid-query-schema.db"
    with closing(sqlite3.connect(db_path)) as conn:
        for table_name in (
            "papers",
            "questions",
            "question_tags",
            "question_frequency_cache",
            "question_previews",
        ):
            conn.execute(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)')
        conn.commit()
    original_connect = sqlite3.connect
    connect_targets: list[str] = []

    def connect_spy(database: object, *args: object, **kwargs: object):
        connect_targets.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(question_read_module.sqlite3, "connect", connect_spy)
    client = _question_bank_client(
        QuestionBankReadService(db_path),
        raise_server_exceptions=False,
    )

    response = client.get("/api/question-bank/papers")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "question_bank_snapshot_unavailable"
    assert len(connect_targets) == 1
    candidate = _path_from_file_uri(connect_targets[0])
    candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    assert not candidate.exists()
    assert not candidate.parent.exists()


def test_visible_rollback_journal_exhausts_to_busy_without_source_changes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rollback.db"
    initialize_database(db_path)
    journal_path = Path(f"{db_path}-journal")
    journal_path.write_bytes(b"visible rollback journal")
    before = {
        "main": db_path.read_bytes(),
        "journal": journal_path.read_bytes(),
    }
    client = _question_bank_client(
        QuestionBankReadService(db_path),
        raise_server_exceptions=False,
    )

    response = client.get("/api/question-bank/papers")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "question_bank_snapshot_busy"
    assert db_path.read_bytes() == before["main"]
    assert journal_path.read_bytes() == before["journal"]


def test_question_bank_routes_registration_and_empty_state(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    client = _question_bank_client(QuestionBankReadService(db_path))

    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/question-bank/papers" in paths
    assert "/api/question-bank/questions" in paths
    assert client.get("/api/question-bank/papers").json() == {
        "items": [],
        "total": 0,
    }
    assert client.get("/api/question-bank/questions").json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
    }


def test_questions_pagination_sorts_before_slicing(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status, created_at)
            VALUES (1, 'Pagination paper', 'success', '2026-01-01 00:00:00')
            """
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                difficulty, created_at
            ) VALUES (?, 1, ?, '选择题', ?, '5', ?)
            """,
            [
                (
                    index,
                    str(index),
                    f"Question {index}",
                    f"2026-01-{index:02d} 00:00:00",
                )
                for index in range(1, 26)
            ],
        )
        conn.commit()

    client = _question_bank_client(QuestionBankReadService(db_path))
    response = client.get(
        "/api/question-bank/questions",
        params={"page": 2, "page_size": 20},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 25
    assert payload["total_pages"] == 2
    assert [item["question_number"] for item in payload["items"]] == [
        "5",
        "4",
        "3",
        "2",
        "1",
    ]


def _seed_combination_filter_questions(db_path: Path) -> None:
    papers = [
        (1, "Target", "2026", "期中", "八年级"),
        (2, "Wrong year", "2025", "期中", "八年级"),
        (3, "Wrong exam", "2026", "期末", "八年级"),
        (4, "Wrong grade", "2026", "期中", "七年级"),
        (99, "Wrong paper", "2026", "期中", "八年级"),
    ]
    questions = [
        (100, 1, "7", "选择题", "needle target [[IMAGE:C:/private/stem.png]]", "answer [[IMAGE:C:/private/answer.png]]", "6", 0),
        (101, 99, "7", "选择题", "needle wrong paper", "answer", "6", 0),
        (102, 2, "7", "选择题", "needle wrong year", "answer", "6", 0),
        (103, 3, "7", "选择题", "needle wrong exam", "answer", "6", 0),
        (104, 4, "7", "选择题", "needle wrong grade", "answer", "6", 0),
        (105, 1, "7", "解答题", "needle wrong type", "answer", "6", 0),
        (106, 1, "7", "选择题", "needle wrong difficulty", "answer", "9", 0),
        (107, 1, "7", "选择题", "needle incomplete tags", "answer", None, 0),
        (108, 1, "7", "选择题", "needle wrong scope", "answer", "6", 0),
        (109, 1, "7", "选择题", "needle wrong knowledge", "answer", "6", 0),
        (110, 1, "7", "选择题", "unrelated text", "different answer", "6", 0),
        (111, 1, "8", "选择题", "needle wrong number", "answer", "6", 0),
        (112, 1, "7", "选择题", "needle deleted", "answer", "6", 1),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (
                id, title, source_file, year, exam_type, grade, import_status,
                content_fingerprint, created_at
            ) VALUES (?, ?, 'C:/private/paper.docx', ?, ?, ?, 'success',
                      'paper-secret', '2026-01-01 00:00:00')
            """,
            papers,
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, source_file, image_paths, difficulty, is_deleted,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'C:/private/question.docx',
                      '["C:/private/list.png"]', ?, ?, '2026-02-01 00:00:00')
            """,
            questions,
        )
        for question_id, *_ in questions:
            tags = [
                (question_id, "knowledge_point", "二次函数" if question_id == 109 else "一次函数"),
                (question_id, "ability", "运算求解"),
                (question_id, "exam_scope", "九年级下册" if question_id == 108 else "八年级上册"),
            ]
            if question_id != 107:
                tags.append((question_id, "student_level", "中档提升"))
            conn.executemany(
                """
                INSERT INTO question_tags (question_id, tag_type, tag_value)
                VALUES (?, ?, ?)
                """,
                tags,
            )
        conn.execute(
            """
            INSERT INTO question_fingerprints (
                question_id, base_fingerprint, style_features_json
            ) VALUES (100, 'question-secret', '{"private": "C:/private/fingerprint"}')
            """
        )
        conn.commit()


def test_questions_combined_filters_and_public_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    _seed_combination_filter_questions(db_path)
    client = _question_bank_client(QuestionBankReadService(db_path))

    response = client.get(
        "/api/question-bank/questions",
        params=[
            ("question_number", "7"),
            ("keyword", "needle"),
            ("knowledge_point", "一次函数"),
            ("difficulty_min", "5"),
            ("difficulty_max", "7"),
            ("question_types", "选择题"),
            ("question_types", "填空题"),
            ("paper_ids", "1"),
            ("paper_ids", "2"),
            ("paper_ids", "3"),
            ("paper_ids", "4"),
            ("years", "2026"),
            ("years", "2027"),
            ("exam_types", "期中"),
            ("exam_types", "模拟"),
            ("grades", "八年级"),
            ("grades", "九年级"),
            ("exam_scopes", "八年级上册"),
            ("exam_scopes", "八年级下册"),
            ("tag_status", "tagged"),
            ("sort", "newest"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [100]
    item = payload["items"][0]
    assert item["question_text"] == "needle target "
    assert item["answer_text"] == "answer "
    assert item["asset_urls"] == [
        "/api/question-bank/questions/100/assets/0",
        "/api/question-bank/questions/100/assets/1",
        "/api/question-bank/questions/100/assets/2",
    ]
    serialized = response.text
    for forbidden in (
        "source_file",
        "fingerprint",
        "image_paths",
        "C:/private",
        "[[IMAGE:",
        "paper-secret",
        "question-secret",
    ):
        assert forbidden not in serialized


def test_questions_filter_unsafe_tag_rows_from_public_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (1, 'Safe tags', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text
            ) VALUES (1, 1, '1', 'Public question')
            """
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence
            ) VALUES (1, ?, ?, ?)
            """,
            [
                (
                    "legacy_source_fingerprint",
                    "source_file=C:/private/legacy.docx; fingerprint=legacy-secret",
                    0.1,
                ),
                ("method", "C:/private/method-tag.txt", 0.2),
                (
                    "ability",
                    "[[IMAGE:file:///C:/private/ability-tag.png]]",
                    0.3,
                ),
                ("knowledge_point", "一次函数", 0.95),
            ],
        )
        conn.commit()

    client = _question_bank_client(QuestionBankReadService(db_path))
    response = client.get("/api/question-bank/questions")

    assert response.status_code == 200
    assert response.json()["items"][0]["tags"] == [
        {
            "tag_type": "knowledge_point",
            "tag_value": "一次函数",
            "confidence": 0.95,
        }
    ]
    serialized = response.text
    for forbidden in (
        "legacy_source_fingerprint",
        "source_file",
        "fingerprint",
        "legacy-secret",
        "C:/private",
        "file:///",
        "[[IMAGE:",
    ):
        assert forbidden not in serialized


def _seed_sort_questions(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, exam_type, import_status)
            VALUES (?, ?, ?, 'success')
            """,
            [
                (1, "Midterm", "期中",),
                (2, "Final", "期末",),
                (3, "Zhongkao", "中考",),
                (4, "Practice", "阶段练习",),
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, difficulty,
                created_at
            ) VALUES (?, ?, ?, ?, ?, '2026-01-01 00:00:00')
            """,
            [
                (1, 1, "1", "Question 1", "3"),
                (2, 2, "2", "Question 2", "9"),
                (3, 3, "3", "Question 3", "6"),
                (4, 4, "4", "Practice question", "10"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_frequency_cache (
                question_id, score_midterm, score_final, score_zhongkao
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (1, 30.0, 2.0, 1.0),
                (2, 20.0, 40.0, 2.0),
                (3, 10.0, 3.0, 50.0),
            ],
        )
        conn.commit()


def test_questions_difficulty_sort_orders_before_pagination(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    _seed_sort_questions(db_path)
    client = _question_bank_client(QuestionBankReadService(db_path))

    descending = client.get(
        "/api/question-bank/questions",
        params={"sort": "difficulty_desc", "page_size": 2},
    )
    ascending = client.get(
        "/api/question-bank/questions",
        params={"sort": "difficulty_asc", "page_size": 2},
    )

    assert descending.status_code == 200
    assert [item["id"] for item in descending.json()["items"]] == [4, 2]
    assert ascending.status_code == 200
    assert [item["id"] for item in ascending.json()["items"]] == [1, 3]


def test_question_frequency_sorts_read_existing_cache_without_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    _seed_sort_questions(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        cache_before = conn.execute(
            "SELECT * FROM question_frequency_cache ORDER BY question_id"
        ).fetchall()
    before = db_path.read_bytes()
    client = _question_bank_client(QuestionBankReadService(db_path))

    expected_orders = {
        "frequency_desc": [3, 2, 1, 4],
        "frequency_asc": [1, 2, 3, 4],
        "frequency_midterm": [1, 2, 3, 4],
        "frequency_final": [2, 3, 1, 4],
        "frequency_zhongkao": [3, 2, 1, 4],
        "frequency_contextual": [3, 2, 1, 4],
    }
    for sort, expected_ids in expected_orders.items():
        response = client.get(
            "/api/question-bank/questions",
            params={"sort": sort},
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == expected_ids

    assert db_path.read_bytes() == before
    with closing(sqlite3.connect(db_path)) as conn:
        cache_after = conn.execute(
            "SELECT * FROM question_frequency_cache ORDER BY question_id"
        ).fetchall()
    assert cache_after == cache_before


def test_questions_untagged_requires_three_core_tags_and_valid_difficulty(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Tags', 'success')"
        )
        conn.executemany(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text, difficulty)
            VALUES (?, 1, ?, ?, ?)
            """,
            [
                (1, "1", "Complete core tags", "6"),
                (2, "2", "No legacy student level required", "5"),
                (3, "3", "Only non-core tags", None),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (?, ?, ?)
            """,
            [
                (1, "knowledge_point", "一次函数"),
                (1, "ability", "运算求解"),
                (1, "exam_scope", "八年级上册"),
                (1, "student_level", "中档提升"),
                (2, "knowledge_point", "二次函数"),
                (2, "ability", "逻辑推理"),
                (2, "exam_scope", "九年级上册"),
                (3, "method", "待定系数法"),
            ],
        )
        conn.commit()

    client = _question_bank_client(QuestionBankReadService(db_path))
    response = client.get(
        "/api/question-bank/questions",
        params={"tag_status": "untagged"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [3]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"tag_status": "sometimes"},
        {"sort": "random"},
        {"difficulty_min": 4},
        {"difficulty_max": 7},
        {"difficulty_min": 0, "difficulty_max": 7},
        {"difficulty_min": 4, "difficulty_max": 11},
        {"difficulty_min": 8, "difficulty_max": 4},
    ],
)
def test_invalid_questions_query_returns_unified_422(
    tmp_path: Path,
    params: dict[str, object],
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    client = _question_bank_client(QuestionBankReadService(db_path))

    response = client.get("/api/question-bank/questions", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {
        "validation_error",
        "invalid_difficulty_range",
    }


def _json_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _json_keys(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _json_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_keys(item)


def test_question_detail_preserves_long_text_and_redacts_rich_preview_paths(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "injected-data-root"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    private_root = tmp_path / "detail-private"
    question_marker = str(private_root / "question-marker.png")
    rich_question_asset = str(private_root / "rich-question.png")
    question_prefix = "Q" * 10_251
    answer_prefix = "A" * 10_379
    question_text = (
        f"{question_prefix}[[IMAGE:{question_marker}]]"
        "question-tail[[IMAGE:question_bank/extracted_images/shared.png]]"
    )
    answer_text = (
        f"{answer_prefix}[[IMAGE:file:///C:/detail-private/answer-marker.png]]"
        "answer-tail"
    )
    expected_question_text = f"{question_prefix}question-tail"
    expected_answer_text = f"{answer_prefix}answer-tail"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (
                id, title, source_file, year, province, city, district,
                exam_type, grade, semester, textbook_version, import_status,
                content_fingerprint, created_at, updated_at
            ) VALUES (
                1, 'Detail paper', 'C:/detail-private/source.docx', '2026',
                'Province', 'City', 'District', 'midterm', 'grade-8',
                'spring', 'v1', 'success', 'detail-private-fingerprint',
                '2026-01-01 00:00:00', '2026-01-02 00:00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, source_file, page_range, image_paths, difficulty,
                typicality, reason, needs_review, has_images,
                needs_image_review, created_at, updated_at
            ) VALUES (
                10, 1, '10', 'choice', ?, ?,
                'C:/detail-private/question.docx', '3-4', ?, '7', 'high',
                'normal public reason', 1, 1, 0,
                '2026-02-01 00:00:00', '2026-02-02 00:00:00'
            )
            """,
            (
                question_text,
                answer_text,
                json.dumps(
                    [
                        "question_bank/extracted_images/db-first.png",
                        "question_bank/extracted_images/shared.png",
                    ]
                ),
            ),
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence
            ) VALUES (10, ?, ?, ?)
            """,
            [
                ("knowledge_point", "一次函数", 0.9),
                ("method", "C:/detail-private/malicious-tag.txt", 0.1),
                ("ability", "Reasoning", None),
                (
                    "legacy_source_fingerprint",
                    "source_file=C:/detail-private/legacy.docx",
                    0.2,
                ),
                ("exam_scope", "Grade 8", 0.8),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_previews (
                question_id, preview_type, source_file, page_number,
                image_path, bbox_json, status, message, updated_at
            ) VALUES (10, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "question",
                    "C:/detail-private/preview-source.pdf",
                    3,
                    "C:/detail-private/question-preview.png",
                    json.dumps({"x0": 1, "y0": "2.5", "x1": 301.25, "y1": 402}),
                    "ready",
                    None,
                    "2026-03-01 10:11:12",
                ),
                (
                    "answer",
                    "C:/detail-private/preview-source.pdf",
                    None,
                    None,
                    "{}",
                    "failed",
                    "Cannot render C:/detail-private/failed-answer.png",
                    "2026-03-01 10:12:13",
                ),
                (
                    "internal",
                    "C:/detail-private/internal.pdf",
                    99,
                    "C:/detail-private/internal.png",
                    "{}",
                    "ready",
                    None,
                    "2026-03-01 10:13:14",
                ),
            ],
        )
        conn.commit()

    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_10.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 10,
                "question_blocks": [
                    {
                        "text": (
                            "Rich question "
                            f"[[IMAGE:{rich_question_asset}]]"
                            " after image"
                        ),
                        "xml": "<w:p>private-docx-xml-question</w:p>",
                        "image_relationships": {
                            "rId5": rich_question_asset,
                        },
                    },
                    {
                        "text": "Second rich question block",
                        "xml": "<w:p>private-docx-xml-second</w:p>",
                        "image_relationships": {},
                    },
                ],
                "answer_blocks": [
                    {
                        "text": (
                            "Rich answer [[IMAGE:question_bank/extracted_images/"
                            "rich-answer.png]] after image"
                        ),
                        "xml": "<w:p>private-docx-xml-answer</w:p>",
                        "image_relationships": {
                            "rId9": "question_bank/extracted_images/rich-answer.png",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    before = db_path.read_bytes()
    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    response = client.get("/api/question-bank/questions/10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["question_text"] == expected_question_text
    assert payload["answer_text"] == expected_answer_text
    assert len(payload["question_text"]) > 10_000
    assert len(payload["answer_text"]) > 10_000
    assert payload["page_range"] == "3-4"
    assert {
        key: payload[key]
        for key in (
            "id",
            "paper_id",
            "question_number",
            "question_type",
            "difficulty",
            "typicality",
            "reason",
            "needs_review",
            "has_images",
            "needs_image_review",
            "paper_title",
            "year",
            "province",
            "city",
            "district",
            "exam_type",
            "grade",
            "semester",
            "textbook_version",
        )
    } == {
        "id": 10,
        "paper_id": 1,
        "question_number": "10",
        "question_type": "choice",
        "difficulty": "7",
        "typicality": "high",
        "reason": "normal public reason",
        "needs_review": True,
        "has_images": True,
        "needs_image_review": False,
        "paper_title": "Detail paper",
        "year": "2026",
        "province": "Province",
        "city": "City",
        "district": "District",
        "exam_type": "midterm",
        "grade": "grade-8",
        "semester": "spring",
        "textbook_version": "v1",
    }
    assert payload["tags"] == [
        {
            "tag_type": "knowledge_point",
            "tag_value": "一次函数",
            "confidence": 0.9,
        },
        {
            "tag_type": "ability",
            "tag_value": "Reasoning",
            "confidence": None,
        },
        {
            "tag_type": "exam_scope",
            "tag_value": "Grade 8",
            "confidence": 0.8,
        },
    ]
    assert payload["assets"] == [
        {
            "index": index,
            "url": f"/api/question-bank/questions/10/assets/{index}",
        }
        for index in range(6)
    ]
    assert payload["asset_urls"] == [item["url"] for item in payload["assets"]]
    rich_content = payload["rich_content"]
    assert rich_content["available"] is True
    assert rich_content["question_block_count"] == 2
    assert rich_content["answer_block_count"] == 1
    assert [
        {key: block[key] for key in ("text", "asset_indexes", "asset_urls")}
        for block in rich_content["question_blocks"]
    ] == [
        {
            "text": "Rich question  after image",
            "asset_indexes": [4],
            "asset_urls": ["/api/question-bank/questions/10/assets/4"],
        },
        {
            "text": "Second rich question block",
            "asset_indexes": [],
            "asset_urls": [],
        },
    ]
    assert [
        {key: block[key] for key in ("text", "asset_indexes", "asset_urls")}
        for block in rich_content["answer_blocks"]
    ] == [
        {
            "text": "Rich answer  after image",
            "asset_indexes": [5],
            "asset_urls": ["/api/question-bank/questions/10/assets/5"],
        }
    ]
    assert all("kind" in block for block in rich_content["question_blocks"])
    assert payload["previews"] == [
        {
            "preview_type": "question",
            "status": "ready",
            "page_number": 3,
            "bbox": {"x0": 1.0, "y0": 2.5, "x1": 301.25, "y1": 402.0},
            "updated_at": "2026-03-01 10:11:12",
            "url": "/api/question-bank/questions/10/previews/question",
        },
        {
            "preview_type": "answer",
            "status": "failed",
            "page_number": None,
            "bbox": None,
            "updated_at": "2026-03-01 10:12:13",
            "url": None,
        },
    ]
    serialized_values = "\n".join(_json_strings(payload))
    for forbidden in (
        "source_file",
        "content_fingerprint",
        "image_paths",
        "image_path",
        "message",
        "detail-private",
        "question_bank/extracted_images",
        "private-docx-xml",
        "image_relationships",
        "[[IMAGE:",
        "file://",
    ):
        assert forbidden not in serialized_values
    assert db_path.read_bytes() == before


def test_question_text_projection_preserves_whitespace_and_incomplete_markers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "injected-data-root"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    question_text = (
        " \tQuestion [[IMAGE:C:/private/complete-question.png]] middle "
        "[[IMAGE:unfinished-question  \n"
    )
    answer_text = (
        " \nAnswer[[IMAGE:relative/complete-answer.png]] tail "
        "[[IMAGE:unfinished-answer\t "
    )
    rich_text = (
        "\tRich [[IMAGE:relative/complete-rich.png]] end "
        "[[IMAGE:unfinished-rich \r\n"
    )
    expected_question = " \tQuestion  middle [[IMAGE:unfinished-question  \n"
    expected_answer = " \nAnswer tail [[IMAGE:unfinished-answer\t "
    expected_rich = "\tRich  end [[IMAGE:unfinished-rich \r\n"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, answer_text
            ) VALUES (20, 1, '20', ?, ?)
            """,
            (question_text, answer_text),
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence
            ) VALUES (
                20, 'knowledge_point',
                '  一次函数 [[IMAGE:relative/tag.png]]  ', 0.75
            )
            """
        )
        conn.commit()

    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_20.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 20,
                "question_blocks": [
                    {
                        "text": rich_text,
                        "image_relationships": {
                            "rId1": "relative/complete-rich.png",
                        },
                    }
                ],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    list_response = client.get("/api/question-bank/questions")
    detail_response = client.get("/api/question-bank/questions/20")

    assert list_response.status_code == 200
    list_item = list_response.json()["items"][0]
    assert list_item["question_text"] == expected_question
    assert list_item["answer_text"] == expected_answer
    assert list_item["tags"] == [
        {
            "tag_type": "knowledge_point",
            "tag_value": "一次函数",
            "confidence": 0.75,
        }
    ]
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["question_text"] == expected_question
    assert detail["answer_text"] == expected_answer
    assert detail["rich_content"]["question_blocks"][0]["text"] == expected_rich


def test_question_detail_returns_only_current_preview_per_type(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (30, 1, '30', 'Question')
            """
        )
        conn.executemany(
            """
            INSERT INTO question_previews (
                id, question_id, preview_type, page_number, image_path,
                bbox_json, status, updated_at
            ) VALUES (?, 30, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "question",
                    2,
                    "C:/private/current-question.png",
                    json.dumps({"x0": 2, "y0": 3, "x1": 20, "y1": 30}),
                    "ready",
                    "2026-03-02 00:00:00",
                ),
                (
                    2,
                    "question",
                    1,
                    "C:/private/stale-question.png",
                    json.dumps({"x0": 1, "y0": 1, "x1": 10, "y1": 10}),
                    "failed",
                    "2026-03-01 00:00:00",
                ),
                (
                    3,
                    "answer",
                    3,
                    None,
                    json.dumps({"x0": 3, "y0": 3, "x1": 30, "y1": 30}),
                    "failed",
                    "2026-03-03 00:00:00",
                ),
                (
                    4,
                    "answer",
                    4,
                    "C:/private/current-answer.png",
                    json.dumps({"x0": 4, "y0": 5, "x1": 40, "y1": 50}),
                    "ready",
                    "2026-03-03 00:00:00",
                ),
            ],
        )
        conn.commit()

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=tmp_path)
    )
    response = client.get("/api/question-bank/questions/30")

    assert response.status_code == 200
    assert response.json()["previews"] == [
        {
            "preview_type": "question",
            "status": "ready",
            "page_number": 2,
            "bbox": {"x0": 2.0, "y0": 3.0, "x1": 20.0, "y1": 30.0},
            "updated_at": "2026-03-02 00:00:00",
            "url": "/api/question-bank/questions/30/previews/question",
        },
        {
            "preview_type": "answer",
            "status": "ready",
            "page_number": 4,
            "bbox": {"x0": 4.0, "y0": 5.0, "x1": 40.0, "y1": 50.0},
            "updated_at": "2026-03-03 00:00:00",
            "url": "/api/question-bank/questions/30/previews/answer",
        },
    ]


def test_question_detail_rejects_mismatched_rich_content_without_fallback(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "injected-data-root"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (10, 1, '10', 'Question')
            """
        )
        conn.commit()

    injected_rich_root = data_root / "question_bank" / "rich_content"
    injected_rich_root.mkdir(parents=True)
    injected_rich_root.joinpath("question_10.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 999,
                "question_blocks": [{"text": "mismatched", "image_relationships": {}}],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )
    unrelated_root = (
        tmp_path / "global-looking-data" / "question_bank" / "rich_content"
    )
    unrelated_root.mkdir(parents=True)
    unrelated_root.joinpath("question_10.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 10,
                "question_blocks": [{"text": "must not leak", "image_relationships": {}}],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    response = client.get("/api/question-bank/questions/10")

    assert response.status_code == 200
    assert response.json()["rich_content"] == {
        "available": False,
        "question_block_count": 0,
        "answer_block_count": 0,
        "question_blocks": [],
        "answer_blocks": [],
    }
    assert "must not leak" not in response.text


@pytest.mark.parametrize(
    ("question_id", "request_id"),
    [
        (404, "rid-missing-question-detail"),
        (11, "rid-deleted-question-detail"),
        (12, "rid-deleted-paper-question-detail"),
    ],
)
def test_missing_question_detail_returns_exact_unified_404(
    tmp_path: Path,
    question_id: int,
    request_id: str,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (?, ?, ?)
            """,
            [
                (1, "Active paper", "success"),
                (2, "Deleted paper", "deleted"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, is_deleted
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (11, 1, "11", "Soft deleted", 1),
                (12, 2, "12", "Deleted parent", 0),
            ],
        )
        conn.commit()

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=tmp_path)
    )
    response = client.get(
        f"/api/question-bank/questions/{question_id}",
        headers={"x-request-id": request_id},
    )

    assert response.status_code == 404
    assert response.headers["x-request-id"] == request_id
    assert response.json() == {
        "error": {
            "code": "question_not_found",
            "message": "Question not found",
            "details": {"question_id": question_id},
            "request_id": request_id,
        }
    }


@dataclass(frozen=True)
class _QuestionBankMediaSeed:
    client: TestClient
    db_path: Path
    data_root: Path
    asset_root: Path
    preview_root: Path
    question_id: int
    ordered_assets: tuple[tuple[bytes, str], ...]
    current_preview_bytes: bytes
    db_before: bytes
    sqlite_sidecars_before: dict[str, bytes | None]


def _sqlite_sidecar_snapshot(db_path: Path) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        snapshot[suffix] = sidecar.read_bytes() if sidecar.exists() else None
    return snapshot


@pytest.fixture
def question_bank_media_seed(tmp_path: Path) -> _QuestionBankMediaSeed:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "isolated-data-root"
    asset_root = data_root / "question_bank" / "extracted_images"
    preview_root = data_root / "question_bank" / "previews"
    asset_root.mkdir(parents=True)
    preview_root.mkdir(parents=True)
    initialize_database(db_path)

    db_asset = asset_root / "set" / "shared-fallback.png"
    question_asset = asset_root / "question-marker.jpg"
    answer_asset = asset_root / "answer" / "answer-marker.webp"
    rich_asset = asset_root / "rich" / "rich.bmp"
    current_preview = preview_root / "current" / "shared-preview.jpg"
    old_preview = preview_root / "old-question.jpg"
    old_answer_preview = preview_root / "old-answer.jpg"
    for path, content in (
        (db_asset, b"asset-png-current"),
        (question_asset, b"asset-jpeg-question"),
        (answer_asset, b"asset-webp-answer"),
        (rich_asset, b"asset-bmp-rich"),
        (current_preview, b"preview-jpeg-current"),
        (old_preview, b"preview-jpeg-old"),
        (old_answer_preview, b"preview-jpeg-old-answer"),
        (
            preview_root / "shadow" / "shared-fallback.png",
            b"wrong-preview-search-root",
        ),
        (
            asset_root / "shadow" / "shared-preview.jpg",
            b"wrong-asset-search-root",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    outside_asset = tmp_path / "outside-private.png"
    outside_asset.write_bytes(b"outside-private")
    traversal_asset = tmp_path / "outside-traversal.png"
    traversal_asset.write_bytes(b"outside-traversal")
    unsafe_asset = asset_root / "unsafe.txt"
    unsafe_asset.write_bytes(b"unsafe-asset-type")
    outside_preview = asset_root / "outside-preview.jpg"
    outside_preview.write_bytes(b"outside-preview-root")
    unsafe_preview = preview_root / "unsafe-preview.txt"
    unsafe_preview.write_bytes(b"unsafe-preview-type")
    for folder in ("ambiguous-a", "ambiguous-b"):
        ambiguous = asset_root / folder / "ambiguous.png"
        ambiguous.parent.mkdir(parents=True)
        ambiguous.write_bytes(folder.encode())

    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (?, ?, ?)
            """,
            [
                (1, "Active", "success"),
                (2, "Deleted", "deleted"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, answer_text,
                image_paths, is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    100,
                    1,
                    "100",
                    (
                        "Stem[[IMAGE:question_bank/extracted_images/"
                        "question-marker.jpg]]"
                    ),
                    "Answer[[IMAGE:C:/offline/answer-marker.webp]]",
                    json.dumps(["C:/offline/shared-fallback.png"]),
                    0,
                ),
                (
                    101,
                    1,
                    "101",
                    "Soft deleted",
                    None,
                    json.dumps(["question_bank/extracted_images/question-marker.jpg"]),
                    1,
                ),
                (
                    102,
                    2,
                    "102",
                    "Deleted parent",
                    None,
                    json.dumps(["question_bank/extracted_images/question-marker.jpg"]),
                    0,
                ),
                (
                    110,
                    1,
                    "110",
                    "Missing asset",
                    None,
                    json.dumps([str(asset_root / "missing-current.png")]),
                    0,
                ),
                (111, 1, "111", "Missing preview", None, "[]", 0),
                (
                    112,
                    1,
                    "112",
                    "Outside asset",
                    None,
                    json.dumps([str(outside_asset.resolve())]),
                    0,
                ),
                (
                    113,
                    1,
                    "113",
                    "Traversal asset",
                    None,
                    json.dumps(["../outside-traversal.png"]),
                    0,
                ),
                (
                    114,
                    1,
                    "114",
                    "Unsafe asset",
                    None,
                    json.dumps(["question_bank/extracted_images/unsafe.txt"]),
                    0,
                ),
                (
                    115,
                    1,
                    "115",
                    "Ambiguous asset",
                    None,
                    json.dumps(["C:/offline/ambiguous.png"]),
                    0,
                ),
                (116, 1, "116", "Outside preview", None, "[]", 0),
                (117, 1, "117", "Unsafe preview", None, "[]", 0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_previews (
                id, question_id, preview_type, image_path, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    100,
                    "question",
                    "question_bank/previews/old-question.jpg",
                    "ready",
                    "2026-01-01 00:00:00",
                ),
                (
                    2,
                    100,
                    "question",
                    "C:/offline/shared-preview.jpg",
                    "ready",
                    "2026-02-01 00:00:00",
                ),
                (
                    3,
                    100,
                    "answer",
                    "question_bank/previews/old-answer.jpg",
                    "ready",
                    "2026-01-01 00:00:00",
                ),
                (4, 100, "answer", None, "failed", "2026-02-01 00:00:00"),
                (
                    5,
                    111,
                    "question",
                    str(preview_root / "missing-current.jpg"),
                    "ready",
                    "2026-03-01 00:00:00",
                ),
                (
                    6,
                    116,
                    "question",
                    str(outside_preview),
                    "ready",
                    "2026-03-01 00:00:00",
                ),
                (
                    7,
                    117,
                    "question",
                    "question_bank/previews/unsafe-preview.txt",
                    "ready",
                    "2026-03-01 00:00:00",
                ),
                (
                    8,
                    101,
                    "question",
                    "question_bank/previews/old-question.jpg",
                    "ready",
                    "2026-03-01 00:00:00",
                ),
                (
                    9,
                    102,
                    "question",
                    "question_bank/previews/old-question.jpg",
                    "ready",
                    "2026-03-01 00:00:00",
                ),
            ],
        )
        conn.commit()
        conn.execute("PRAGMA journal_mode = DELETE").fetchone()

    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_100.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 100,
                "question_blocks": [
                    {
                        "text": (
                            "Rich[[IMAGE:question_bank/extracted_images/"
                            "rich/rich.bmp]]"
                        ),
                        "image_relationships": {
                            "rId1": "question_bank/extracted_images/rich/rich.bmp"
                        },
                    }
                ],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    return _QuestionBankMediaSeed(
        client=client,
        db_path=db_path,
        data_root=data_root,
        asset_root=asset_root,
        preview_root=preview_root,
        question_id=100,
        ordered_assets=(
            (b"asset-png-current", "image/png"),
            (b"asset-jpeg-question", "image/jpeg"),
            (b"asset-webp-answer", "image/webp"),
            (b"asset-bmp-rich", "image/bmp"),
        ),
        current_preview_bytes=b"preview-jpeg-current",
        db_before=db_path.read_bytes(),
        sqlite_sidecars_before=_sqlite_sidecar_snapshot(db_path),
    )


def _assert_question_media_error(
    response,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object],
    request_id: str,
    forbidden_values: tuple[str, ...] = (),
) -> None:
    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == request_id
    payload = response.json()
    assert payload == {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }
    serialized = "\n".join(_json_strings(payload))
    assert not {
        "path",
        "file",
        "file_path",
        "filename",
    }.intersection(_json_keys(payload))
    for forbidden in (
        "C:/offline",
        *forbidden_values,
    ):
        assert forbidden not in serialized


def test_question_asset_media_serves_task3_order_with_mime_and_no_store(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    detail = seed.client.get(
        f"/api/question-bank/questions/{seed.question_id}"
    ).json()
    expected_urls = [
        f"/api/question-bank/questions/{seed.question_id}/assets/{index}"
        for index in range(len(seed.ordered_assets))
    ]
    assert detail["asset_urls"] == expected_urls
    assert [item["url"] for item in detail["assets"]] == expected_urls

    for url, (expected_bytes, expected_mime) in zip(
        expected_urls,
        seed.ordered_assets,
        strict=True,
    ):
        response = seed.client.get(url)
        assert response.status_code == 200
        assert response.content == expected_bytes
        assert response.headers["content-type"] == expected_mime
        assert response.headers["cache-control"] == "no-store"

    assert seed.db_path.read_bytes() == seed.db_before
    assert _sqlite_sidecar_snapshot(seed.db_path) == seed.sqlite_sidecars_before


def test_question_preview_media_uses_current_row_without_ready_fallback(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    detail = seed.client.get(
        f"/api/question-bank/questions/{seed.question_id}"
    ).json()
    assert [(item["preview_type"], item["updated_at"], item["url"]) for item in detail["previews"]] == [
        (
            "question",
            "2026-02-01 00:00:00",
            f"/api/question-bank/questions/{seed.question_id}/previews/question",
        ),
        ("answer", "2026-02-01 00:00:00", None),
    ]

    response = seed.client.get(
        f"/api/question-bank/questions/{seed.question_id}/previews/question"
    )
    assert response.status_code == 200
    assert response.content == seed.current_preview_bytes
    assert response.content != b"preview-jpeg-old"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"

    request_id = "rid-preview-current-without-path"
    no_fallback = seed.client.get(
        f"/api/question-bank/questions/{seed.question_id}/previews/answer",
        headers={"x-request-id": request_id},
    )
    _assert_question_media_error(
        no_fallback,
        status_code=404,
        code="question_media_not_found",
        message="Question media resource not found",
        details={"question_id": seed.question_id, "preview_type": "answer"},
        request_id=request_id,
        forbidden_values=("old-answer.jpg", str(seed.preview_root)),
    )


def test_question_asset_media_returns_exact_404_for_unowned_identifiers(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    cases = [
        (
            "/api/question-bank/questions/999/assets/0",
            {"question_id": 999, "asset_index": 0},
        ),
        (
            f"/api/question-bank/questions/{seed.question_id}/assets/99",
            {"question_id": seed.question_id, "asset_index": 99},
        ),
        (
            f"/api/question-bank/questions/{seed.question_id}/assets/-1",
            {"question_id": seed.question_id, "asset_index": -1},
        ),
        (
            "/api/question-bank/questions/101/assets/0",
            {"question_id": 101, "asset_index": 0},
        ),
        (
            "/api/question-bank/questions/102/assets/0",
            {"question_id": 102, "asset_index": 0},
        ),
        (
            "/api/question-bank/questions/101/previews/question",
            {"question_id": 101, "preview_type": "question"},
        ),
        (
            "/api/question-bank/questions/102/previews/question",
            {"question_id": 102, "preview_type": "question"},
        ),
    ]
    for index, (url, details) in enumerate(cases):
        request_id = f"rid-question-media-not-found-{index}"
        response = seed.client.get(url, headers={"x-request-id": request_id})
        _assert_question_media_error(
            response,
            status_code=404,
            code="question_media_not_found",
            message="Question media resource not found",
            details=details,
            request_id=request_id,
        )


def test_question_asset_and_preview_media_return_410_for_missing_files(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    cases = [
        (
            "/api/question-bank/questions/110/assets/0",
            {"question_id": 110, "asset_index": 0},
        ),
        (
            "/api/question-bank/questions/111/previews/question",
            {"question_id": 111, "preview_type": "question"},
        ),
    ]
    for index, (url, details) in enumerate(cases):
        request_id = f"rid-question-media-expired-{index}"
        response = seed.client.get(url, headers={"x-request-id": request_id})
        _assert_question_media_error(
            response,
            status_code=410,
            code="question_media_expired",
            message="Question media resource is no longer available",
            details=details,
            request_id=request_id,
            forbidden_values=(
                "missing-current",
                str(seed.data_root),
            ),
        )


def test_question_asset_media_enforces_controlled_roots_and_image_types(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    cases = [
        (112, 403, "question_media_forbidden", "outside-private"),
        (113, 403, "question_media_forbidden", "outside-traversal"),
        (114, 415, "question_media_type_not_supported", "unsafe.txt"),
        (115, 403, "question_media_forbidden", "ambiguous.png"),
    ]
    for question_id, status_code, code, forbidden in cases:
        request_id = f"rid-question-asset-boundary-{question_id}"
        response = seed.client.get(
            f"/api/question-bank/questions/{question_id}/assets/0",
            headers={"x-request-id": request_id},
        )
        message = (
            "Question media type is not supported"
            if status_code == 415
            else "Question media resource is outside the allowed storage boundary"
        )
        _assert_question_media_error(
            response,
            status_code=status_code,
            code=code,
            message=message,
            details={"question_id": question_id, "asset_index": 0},
            request_id=request_id,
            forbidden_values=(forbidden, str(seed.data_root)),
        )


def test_question_preview_media_enforces_preview_root_and_image_types(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    cases = [
        (116, 403, "question_media_forbidden", "outside-preview.jpg"),
        (117, 415, "question_media_type_not_supported", "unsafe-preview.txt"),
    ]
    for question_id, status_code, code, forbidden in cases:
        request_id = f"rid-question-preview-boundary-{question_id}"
        response = seed.client.get(
            f"/api/question-bank/questions/{question_id}/previews/question",
            headers={"x-request-id": request_id},
        )
        message = (
            "Question media type is not supported"
            if status_code == 415
            else "Question media resource is outside the allowed storage boundary"
        )
        _assert_question_media_error(
            response,
            status_code=status_code,
            code=code,
            message=message,
            details={"question_id": question_id, "preview_type": "question"},
            request_id=request_id,
            forbidden_values=(forbidden, str(seed.data_root)),
        )


def test_question_asset_media_without_injected_data_root_fails_closed(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    client = _question_bank_client(QuestionBankReadService(seed.db_path))
    request_id = "rid-question-media-no-data-root"
    response = client.get(
        f"/api/question-bank/questions/{seed.question_id}/assets/0",
        headers={"x-request-id": request_id},
    )

    _assert_question_media_error(
        response,
        status_code=403,
        code="question_media_forbidden",
        message="Question media resource is outside the allowed storage boundary",
        details={"question_id": seed.question_id, "asset_index": 0},
        request_id=request_id,
        forbidden_values=(str(seed.data_root),),
    )


def test_question_preview_media_rejects_invalid_type_with_shared_422(
    question_bank_media_seed: _QuestionBankMediaSeed,
) -> None:
    seed = question_bank_media_seed
    response = seed.client.get(
        f"/api/question-bank/questions/{seed.question_id}/previews/solution",
        headers={"x-request-id": "rid-invalid-preview-type"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == "rid-invalid-preview-type"


def _create_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as symlink_error:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(
                "Unable to create a temporary directory symlink or junction"
            ) from symlink_error
    assert link.is_dir()


def _linked_route_root_client(
    tmp_path: Path,
    *,
    route_subdir: str,
    preview: bool,
) -> tuple[TestClient, Path]:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "isolated-data-root"
    outside_root = tmp_path / "outside-route-root"
    outside_root.mkdir()
    link = data_root / "question_bank" / route_subdir
    link.parent.mkdir(parents=True)
    _create_directory_link(link, outside_root)
    filename = "escaped-preview.jpg" if preview else "escaped-asset.png"
    escaped_file = outside_root / filename
    escaped_file.write_bytes(b"must-not-be-served")

    initialize_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, image_paths
            ) VALUES (?, 1, '1', 'Question', ?)
            """,
            (
                201 if preview else 200,
                "[]"
                if preview
                else json.dumps(
                    [f"question_bank/extracted_images/{filename}"]
                ),
            ),
        )
        if preview:
            conn.execute(
                """
                INSERT INTO question_previews (
                    question_id, preview_type, image_path, status, updated_at
                ) VALUES (
                    201, 'question', ?, 'ready', '2026-03-01 00:00:00'
                )
                """,
                (f"question_bank/previews/{filename}",),
            )
        conn.commit()

    return (
        _question_bank_client(
            QuestionBankReadService(db_path, data_root=data_root)
        ),
        escaped_file,
    )


def test_question_asset_media_rejects_linked_route_root_escape(
    tmp_path: Path,
) -> None:
    client, escaped_file = _linked_route_root_client(
        tmp_path,
        route_subdir="extracted_images",
        preview=False,
    )
    request_id = "rid-linked-asset-root"
    response = client.get(
        "/api/question-bank/questions/200/assets/0",
        headers={"x-request-id": request_id},
    )

    _assert_question_media_error(
        response,
        status_code=403,
        code="question_media_forbidden",
        message="Question media resource is outside the allowed storage boundary",
        details={"question_id": 200, "asset_index": 0},
        request_id=request_id,
        forbidden_values=("outside-route-root", str(escaped_file)),
    )


def test_question_preview_media_rejects_linked_route_root_escape(
    tmp_path: Path,
) -> None:
    client, escaped_file = _linked_route_root_client(
        tmp_path,
        route_subdir="previews",
        preview=True,
    )
    request_id = "rid-linked-preview-root"
    response = client.get(
        "/api/question-bank/questions/201/previews/question",
        headers={"x-request-id": request_id},
    )

    _assert_question_media_error(
        response,
        status_code=403,
        code="question_media_forbidden",
        message="Question media resource is outside the allowed storage boundary",
        details={"question_id": 201, "preview_type": "question"},
        request_id=request_id,
        forbidden_values=("outside-route-root", str(escaped_file)),
    )


def _cross_linked_route_root_client(
    tmp_path: Path,
    *,
    preview: bool,
) -> tuple[TestClient, Path]:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "isolated-data-root"
    question_bank_root = data_root / "question_bank"
    route_name = "previews" if preview else "extracted_images"
    sibling_name = "extracted_images" if preview else "previews"
    sibling_root = question_bank_root / sibling_name
    sibling_root.mkdir(parents=True)
    _create_directory_link(question_bank_root / route_name, sibling_root)
    filename = "cross-preview.jpg" if preview else "cross-asset.png"
    cross_file = sibling_root / filename
    cross_file.write_bytes(b"must-not-cross-route-roots")

    initialize_database(db_path)
    question_id = 211 if preview else 210
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, image_paths
            ) VALUES (?, 1, '1', 'Question', ?)
            """,
            (
                question_id,
                "[]"
                if preview
                else json.dumps(
                    [f"question_bank/extracted_images/{filename}"]
                ),
            ),
        )
        if preview:
            conn.execute(
                """
                INSERT INTO question_previews (
                    question_id, preview_type, image_path, status, updated_at
                ) VALUES (
                    211, 'question', ?, 'ready', '2026-03-01 00:00:00'
                )
                """,
                (f"question_bank/previews/{filename}",),
            )
        conn.commit()

    return (
        _question_bank_client(
            QuestionBankReadService(db_path, data_root=data_root)
        ),
        cross_file,
    )


def test_question_asset_media_rejects_cross_link_to_preview_root(
    tmp_path: Path,
) -> None:
    client, cross_file = _cross_linked_route_root_client(
        tmp_path,
        preview=False,
    )
    request_id = "rid-cross-linked-asset-root"
    response = client.get(
        "/api/question-bank/questions/210/assets/0",
        headers={"x-request-id": request_id},
    )

    _assert_question_media_error(
        response,
        status_code=403,
        code="question_media_forbidden",
        message="Question media resource is outside the allowed storage boundary",
        details={"question_id": 210, "asset_index": 0},
        request_id=request_id,
        forbidden_values=("cross-asset.png", str(cross_file)),
    )


def test_question_preview_media_rejects_cross_link_to_asset_root(
    tmp_path: Path,
) -> None:
    client, cross_file = _cross_linked_route_root_client(
        tmp_path,
        preview=True,
    )
    request_id = "rid-cross-linked-preview-root"
    response = client.get(
        "/api/question-bank/questions/211/previews/question",
        headers={"x-request-id": request_id},
    )

    _assert_question_media_error(
        response,
        status_code=403,
        code="question_media_forbidden",
        message="Question media resource is outside the allowed storage boundary",
        details={"question_id": 211, "preview_type": "question"},
        request_id=request_id,
        forbidden_values=("cross-preview.jpg", str(cross_file)),
    )


def test_question_json_case_insensitive_markers_preserve_field_text(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "isolated-data-root"
    initialize_database(db_path)
    question_text = (
        " \tQuestion [[image:C:/private/question-leak.png]] body "
        "[[IMAGE:kept-question-incomplete  \n"
    )
    answer_text = (
        "\nAnswer [[ImAgE:C:/private/answer-leak.png]] tail "
        "[[image:kept-answer-incomplete\t "
    )
    reason = (
        "  Reason [[image:C:/private/reason-leak.png]] remains "
        "[[IMAGE:kept-reason-incomplete  "
    )
    rich_text = (
        "\tRich [[iMaGe:C:/private/rich-leak.png]] remains "
        "[[IMAGE:kept-rich-incomplete\r\n"
    )
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, answer_text,
                reason
            ) VALUES (300, 1, '300', ?, ?, ?)
            """,
            (question_text, answer_text, reason),
        )
        conn.commit()

    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_300.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 300,
                "question_blocks": [
                    {"text": rich_text, "image_relationships": {}}
                ],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    list_response = client.get("/api/question-bank/questions")
    detail_response = client.get("/api/question-bank/questions/300")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_item = list_response.json()["items"][0]
    detail = detail_response.json()
    expected_question = (
        " \tQuestion  body [[IMAGE:kept-question-incomplete  \n"
    )
    expected_answer = "\nAnswer  tail [[image:kept-answer-incomplete\t "
    expected_reason = (
        "  Reason  remains [[IMAGE:kept-reason-incomplete  "
    )
    expected_rich = (
        "\tRich  remains [[IMAGE:kept-rich-incomplete\r\n"
    )
    for payload in (list_item, detail):
        assert payload["question_text"] == expected_question
        assert payload["answer_text"] == expected_answer
        assert payload["reason"] == expected_reason
    assert detail["rich_content"]["question_blocks"][0]["text"] == expected_rich
    serialized = f"{list_response.text}\n{detail_response.text}"
    for forbidden in (
        "question-leak.png",
        "answer-leak.png",
        "reason-leak.png",
        "rich-leak.png",
    ):
        assert forbidden not in serialized


def test_question_tags_reject_embedded_filesystem_tokens_without_false_positives(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (301, 1, '301', 'Question')
            """
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence
            ) VALUES (301, ?, ?, ?)
            """,
            [
                ("method", "参考 C:/private/source.docx", 0.1),
                ("ability", "请查看 file:///C:/private/ability.png", 0.2),
                ("exam_scope", r"共享 \\server\share\scope.txt", 0.3),
                ("model", "参考 /var/tmp/private-model.json 完成", 0.4),
                (
                    "canonical_knowledge_id",
                    "共享 //server/share/source.txt",
                    0.5,
                ),
                ("knowledge_point", "一次函数", 0.9),
                ("student_level", "中等/提升", 0.8),
                ("teaching_stage", "初中阶段：函数/图象", 0.7),
                ("supporting_skill_name", "profile:///函数画像", 0.65),
                (
                    "error_type",
                    "  易错 [[image:relative/safe-tag.png]]  ",
                    0.6,
                ),
            ],
        )
        conn.commit()

    client = _question_bank_client(QuestionBankReadService(db_path))
    list_response = client.get("/api/question-bank/questions")
    detail_response = client.get("/api/question-bank/questions/301")

    expected_tags = [
        {
            "tag_type": "knowledge_point",
            "tag_value": "一次函数",
            "confidence": 0.9,
        },
        {
            "tag_type": "student_level",
            "tag_value": "中等/提升",
            "confidence": 0.8,
        },
        {
            "tag_type": "error_type",
            "tag_value": "易错",
            "confidence": 0.6,
        },
    ]
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert list_response.json()["items"][0]["tags"] == expected_tags
    assert detail_response.json()["tags"] == expected_tags
    serialized = f"{list_response.text}\n{detail_response.text}"
    for forbidden in (
        "C:/private/source.docx",
        "file:///C:/private/ability.png",
        "server",
        "//server/share",
        "/var/tmp/private-model.json",
    ):
        assert forbidden not in serialized


def test_question_rich_content_rejects_linked_internal_sibling_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "isolated-data-root"
    initialize_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'Paper', 'success')"
        )
        conn.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text)
            VALUES (302, 1, '302', 'Question')
            """
        )
        conn.commit()

    question_bank_root = data_root / "question_bank"
    sibling_root = question_bank_root / "rich-sibling"
    sibling_root.mkdir(parents=True)
    _create_directory_link(question_bank_root / "rich_content", sibling_root)
    sibling_root.joinpath("question_302.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 302,
                "question_blocks": [
                    {
                        "text": "sibling-rich-secret",
                        "image_relationships": {
                            "rId1": "C:/private/sibling-rich.png"
                        },
                    }
                ],
                "answer_blocks": [],
            }
        ),
        encoding="utf-8",
    )

    client = _question_bank_client(
        QuestionBankReadService(db_path, data_root=data_root)
    )
    response = client.get("/api/question-bank/questions/302")

    assert response.status_code == 200
    assert response.json()["rich_content"] == {
        "available": False,
        "question_block_count": 0,
        "answer_block_count": 0,
        "question_blocks": [],
        "answer_blocks": [],
    }
    assert "sibling-rich-secret" not in response.text
    assert "sibling-rich.png" not in response.text
