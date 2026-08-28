from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.assessment_evidence_service import (
    ConfirmedSpreadsheetAdapter,
)
from backend.class_teacher.errors import VaultError
from backend.class_teacher.subject_canonical import (
    canonical_subjects,
    hidden_subjects,
)
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"x-class-teacher-client": "class-teacher-browser-v1"}


def _service(tmp_path: Path) -> VaultService:
    service = VaultService(WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
    ))
    service.ensure_plaintext_ready()
    return service


def _client(service: VaultService) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app)


def _subject(
    service: VaultService,
    token: str,
    operation_id: str,
    source_student_id: str,
    display_name: str,
) -> str:
    subject = service.support.create_subject(
        token=token,
        operation_id=operation_id,
        source_student_id=source_student_id,
        display_name=display_name,
        class_label="合成一班",
    )
    return str(subject["subject_id"])


def _session(
    title: str,
    *,
    occurred_on: str,
    source_reference: str,
    grade: str = "八年级",
    term: str = "下学期",
) -> dict[str, object]:
    return {
        "title": title,
        "academic_year": "2025-2026",
        "term": term,
        "grade": grade,
        "exam_type": "期中",
        "comparison_series": "学期大考",
        "occurred_on": occurred_on,
        "source_reference": source_reference,
    }


def _confirm(
    service: VaultService,
    token: str,
    operation_id: str,
    assessments: list[dict[str, object]],
) -> dict[str, object]:
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        # 来源标签随操作编号变化，避免触发导入级整批去重。
        "source_label": f"合成来源-{operation_id}",
        "assessments": assessments,
    })
    return service.evidence.confirm_batch(
        token=token, operation_id=operation_id, batch=batch
    )


def _session_id(service: VaultService, token: str, title: str) -> str:
    matches = [
        str(item["session_id"])
        for item in service.class_overview.academic_overview(token=token)["sessions"]
        if item["title"] == title
    ]
    assert len(matches) == 1
    return matches[0]


def _build_class_session(
    service: VaultService,
    token: str,
) -> dict[str, str]:
    """三名学生一场考试：数学（有满分）、物理（无满分）、总分（校次）。"""
    alpha = _subject(service, token, "csr-subject-a", "synthetic-csr-001", "合成学生甲")
    beta = _subject(service, token, "csr-subject-b", "synthetic-csr-002", "合成学生乙")
    gamma = _subject(service, token, "csr-subject-c", "synthetic-csr-003", "合成学生丙")
    session = _session("合成期末", occurred_on="2026-04-15", source_reference="ref-csr-final")
    _confirm(service, token, "csr-confirm-final", [
        {
            "title": "合成期末",
            "subject_name": "数学",
            "occurred_on": "2026-04-15",
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 95, "rank": 2, "class_rank": 1},
                {"subject_id": beta, "result_state": "normal", "score": 55, "rank": 30, "class_rank": 2},
                # 缺考：明细里返回（前端置灰），但不计入统计。
                {"subject_id": gamma, "result_state": "absent"},
            ],
        },
        {
            "title": "合成期末",
            "subject_name": "物理",
            "occurred_on": "2026-04-15",
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 88, "rank": 3},
                {"subject_id": beta, "result_state": "normal", "score": 70, "rank": 12},
            ],
        },
        {
            "title": "合成期末",
            "subject_name": "总分",
            "occurred_on": "2026-04-15",
            "measure_role": "total_score",
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 183, "rank": 2},
                {"subject_id": beta, "result_state": "normal", "score": 125, "rank": 1},
                {"subject_id": gamma, "result_state": "normal", "score": 100, "rank": 5},
            ],
        },
    ])
    return {"alpha": alpha, "beta": beta, "gamma": gamma}


def test_class_results_structure_ordering_and_stats(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    ids = _build_class_session(service, token)
    session_id = _session_id(service, token, "合成期末")
    client = _client(service)

    response = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    payload = response.json()
    assert payload["session_id"] == session_id
    assert payload["title"] == "合成期末"
    assert payload["occurred_on"] == "2026-04-15"

    subjects = {item["subject_name"]: item for item in payload["subjects"]}
    assert list(subjects) == sorted(subjects)
    assert set(subjects) == {"数学", "物理", "总分"}
    math = subjects["数学"]
    assert math["max_score"] == 100.0
    assert math["stats"]["subject_name"] == "数学"
    # 缺考不计入统计。
    assert math["stats"]["count"] == 2
    assert math["stats"]["average"] == 75.0
    assert math["stats"]["maximum"] == 95.0
    assert math["stats"]["minimum"] == 55.0
    # 总体标准差（pstdev）：95、55 → 20.0
    assert math["stats"]["stddev"] == 20.0
    math_bands = {band["label"]: band["count"] for band in math["stats"]["bands"]}
    assert math_bands["90-100%"] == 1
    assert math_bands["60%以下"] == 1
    assert math_bands["未定标"] == 0
    # 位次指标：数学校次 2、30，年级人数 40。
    assert math["stats"]["average_rank"] == 16.0
    assert math["stats"]["top50_count"] == 2
    assert math["stats"]["top100_count"] == 2
    # rank/40 ≤ 0.3 → 校次 2 计入，30 不计。
    assert math["stats"]["front30pct_count"] == 1
    # 缺满分的科目：max_score 为 null，bands 全落「未定标」。
    physics = subjects["物理"]
    assert physics["max_score"] is None
    assert physics["stats"]["count"] == 2
    # 物理 88、70 → pstdev 9.0
    assert physics["stats"]["stddev"] == 9.0
    physics_bands = {band["label"]: band["count"] for band in physics["stats"]["bands"]}
    assert physics_bands["未定标"] == 2
    assert all(
        count == 0
        for label, count in physics_bands.items()
        if label != "未定标"
    )
    # 物理校次 3、12：均次 7.5；年级人数取场次级 40，前 30%（≤12 名）两人都在。
    assert physics["stats"]["average_rank"] == 7.5
    assert physics["stats"]["front30pct_count"] == 2
    # 总分校次 2、1、5。
    total_stats = subjects["总分"]["stats"]
    assert total_stats["average_rank"] == pytest.approx(2.7)
    assert total_stats["top50_count"] == 3
    assert total_stats["front30pct_count"] == 3

    students = payload["students"]
    # 按总分校次升序。
    assert [item["display_name"] for item in students] == [
        "合成学生乙",
        "合成学生甲",
        "合成学生丙",
    ]
    assert [item["total_rank"] for item in students] == [1, 2, 5]
    assert all(item["class_label"] == "合成一班" for item in students)
    alpha = next(item for item in students if item["subject_id"] == ids["alpha"])
    alpha_math = alpha["results"]["数学"]
    assert alpha_math["score"] == 95
    assert alpha_math["rank"] == 2
    assert alpha_math["class_rank"] == 1
    assert alpha_math["max_score"] == 100.0
    assert alpha_math["result_state"] == "normal"
    # 与学生分析页同一口径：1 - (rank - 1) / (participant_count - 1)。
    assert alpha_math["relative_position"] == pytest.approx(1 - 1 / 39)
    # 物理没有参评人数：相对位次为 null。
    assert alpha["results"]["物理"]["relative_position"] is None
    gamma = next(item for item in students if item["subject_id"] == ids["gamma"])
    # 缺考科目仍然返回，前端置灰。
    assert gamma["results"]["数学"]["result_state"] == "absent"
    assert gamma["results"]["数学"]["score"] is None
    # 没有成绩的科目不出现在 results 里。
    assert "物理" not in gamma["results"]


def test_class_results_excludes_superseded_evidence(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    ids = _build_class_session(service, token)
    session_id = _session_id(service, token, "合成期末")
    beta_math = next(
        item
        for item in service.evidence.list_subject_evidence(
            token=token, subject_id=ids["beta"]
        )["items"]
        if item["subject_name"] == "数学"
    )
    service.evidence.supersede_evidence(
        token=token,
        evidence_version_id=str(beta_math["evidence_version_id"]),
        operation_id="csr-supersede-1",
        reason="合成作废",
    )
    client = _client(service)

    payload = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()

    beta = next(
        item for item in payload["students"] if item["subject_id"] == ids["beta"]
    )
    assert "数学" not in beta["results"]
    math = next(
        item for item in payload["subjects"] if item["subject_name"] == "数学"
    )
    assert math["stats"]["count"] == 1
    assert math["stats"]["average"] == 95.0


def test_class_results_unknown_session_is_404(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _client(service)

    response = client.get(
        "/api/class-teacher/evidence/sessions/session-missing/class-results",
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "assessment_session_not_found"


def test_class_trend_orders_term_chain_and_counts_normal_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    alpha = _subject(service, token, "csr-trend-subject-a", "synthetic-csr-101", "合成趋势甲")
    beta = _subject(service, token, "csr-trend-subject-b", "synthetic-csr-102", "合成趋势乙")
    # 八上考试日期更晚：学期链序优先于考试日期。
    autumn = _session(
        "八上场", occurred_on="2026-03-01", source_reference="ref-csr-autumn", term="上学期"
    )
    spring = _session(
        "八下场", occurred_on="2026-02-01", source_reference="ref-csr-spring", term="下学期"
    )
    _confirm(service, token, "csr-confirm-autumn", [{
        "title": "八上场",
        "subject_name": "数学",
        "occurred_on": "2026-03-01",
        "max_score": 100,
        "session": autumn,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 80},
            # 缺考不计入均分与人数。
            {"subject_id": beta, "result_state": "absent"},
        ],
    }])
    _confirm(service, token, "csr-confirm-spring", [{
        "title": "八下场",
        "subject_name": "数学",
        "occurred_on": "2026-02-01",
        "max_score": 120,
        "session": spring,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 90},
            {"subject_id": beta, "result_state": "normal", "score": 60},
        ],
    }])
    client = _client(service)

    response = client.get("/api/class-teacher/evidence/class-trend", headers=HEADERS)

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert [item["title"] for item in sessions] == ["八上场", "八下场"]
    first, second = sessions
    assert first["term"] == "上学期"
    assert first["grade"] == "八年级"
    assert first["occurred_on"] == "2026-03-01"
    assert first["subjects"] == [{
        "subject_name": "数学",
        "average": 80.0,
        "count": 1,
        "max_score": 100.0,
        # 没有校次与年级人数：位次指标为空口径。
        "average_rank": None,
        "top50_count": 0,
        "top100_count": 0,
        "front30pct_count": None,
    }]
    assert second["subjects"] == [{
        "subject_name": "数学",
        "average": 75.0,
        "count": 2,
        "max_score": 120.0,
        "average_rank": None,
        "top50_count": 0,
        "top100_count": 0,
        "front30pct_count": None,
    }]


def _max_score_session(service: VaultService, token: str) -> str:
    alpha = _subject(service, token, "csr-max-subject-a", "synthetic-csr-201", "合成满分甲")
    beta = _subject(service, token, "csr-max-subject-b", "synthetic-csr-202", "合成满分乙")
    session = _session("满分更正场", occurred_on="2026-04-20", source_reference="ref-csr-max")
    _confirm(service, token, "csr-confirm-max", [{
        "title": "满分更正场",
        "subject_name": "数学",
        "occurred_on": "2026-04-20",
        "session": session,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 108},
            {"subject_id": beta, "result_state": "normal", "score": 60},
        ],
    }])
    return _session_id(service, token, "满分更正场")


def _evidence_revisions(service: VaultService) -> list[tuple[str, int]]:
    with closing(service.database.connect()) as connection:
        return [
            (str(row["object_id"]), int(row["revision"]))
            for row in connection.execute(
                """
                SELECT object_id, revision FROM encrypted_objects
                WHERE object_id LIKE 'evidence-version-%'
                   OR object_id LIKE 'assessment-%'
                ORDER BY object_id
                """
            ).fetchall()
        ]


def test_update_session_max_scores_updates_bands_and_replays(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    session_id = _max_score_session(service, token)
    client = _client(service)
    url = f"/api/class-teacher/evidence/sessions/{session_id}/class-results"

    before = client.get(url, headers=HEADERS).json()
    math_before = before["subjects"][0]
    assert math_before["subject_name"] == "数学"
    assert math_before["max_score"] is None
    bands_before = {
        band["label"]: band["count"] for band in math_before["stats"]["bands"]
    }
    assert bands_before["未定标"] == 2

    updated = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-max-update", "max_scores": {"数学": 120}},
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "session_id": session_id,
        "updated": True,
        "subjects": ["数学"],
        "participant_count": None,
    }

    after = client.get(url, headers=HEADERS).json()
    math_after = after["subjects"][0]
    # 考试 payload 的满分已更新，bands 变为真实分段。
    assert math_after["max_score"] == 120.0
    bands_after = {
        band["label"]: band["count"] for band in math_after["stats"]["bands"]
    }
    assert bands_after["90-100%"] == 1
    assert bands_after["60%以下"] == 1
    assert bands_after["未定标"] == 0
    # 学生明细来自证据 payload：两级同步更新。
    assert all(
        entry["max_score"] == 120.0
        for student in after["students"]
        for subject, entry in student["results"].items()
        if subject == "数学"
    )

    revisions_after_update = _evidence_revisions(service)
    replayed = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-max-update", "max_scores": {"数学": 120}},
    )
    assert replayed.status_code == 200
    assert replayed.json() == updated.json()
    # 幂等重放：不再重复加 revision。
    assert _evidence_revisions(service) == revisions_after_update


def test_update_session_max_scores_rejects_invalid_input(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    session_id = _max_score_session(service, token)
    client = _client(service)
    url = f"/api/class-teacher/evidence/sessions/{session_id}/max-scores"

    unknown = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "csr-max-unknown", "max_scores": {"化学": 100}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "assessment_subject_not_in_session"
    # 错误消息列出该场次的科目。
    assert "数学" in unknown.json()["error"]["message"]

    for index, value in enumerate((0, -5)):
        rejected = client.patch(
            url,
            headers=HEADERS,
            json={
                "operation_id": f"csr-max-nonpositive-{index}",
                "max_scores": {"数学": value},
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "assessment_max_score_invalid"

    missing = client.patch(
        "/api/class-teacher/evidence/sessions/session-missing/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-max-missing", "max_scores": {"数学": 100}},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "assessment_session_not_found"


def _rank_session(service: VaultService, token: str) -> tuple[str, str]:
    """有校次但无参评人数的场次：用于验证年级人数补填。"""
    alpha = _subject(service, token, "csr-pc-subject-a", "synthetic-csr-301", "合成人数甲")
    beta = _subject(service, token, "csr-pc-subject-b", "synthetic-csr-302", "合成人数乙")
    session = _session("人数补填场", occurred_on="2026-04-22", source_reference="ref-csr-pc")
    _confirm(service, token, "csr-confirm-pc", [{
        "title": "人数补填场",
        "subject_name": "数学",
        "occurred_on": "2026-04-22",
        "max_score": 100,
        "rank_scope": "grade",
        "session": session,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 90, "rank": 2},
            {"subject_id": beta, "result_state": "normal", "score": 70, "rank": 9},
        ],
    }])
    return _session_id(service, token, "人数补填场"), alpha


def _all_object_revisions(service: VaultService) -> list[tuple[str, int]]:
    with closing(service.database.connect()) as connection:
        return [
            (str(row["object_id"]), int(row["revision"]))
            for row in connection.execute(
                "SELECT object_id, revision FROM encrypted_objects ORDER BY object_id"
            ).fetchall()
        ]


def test_update_session_participant_count_enables_relative_position(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    session_id, alpha = _rank_session(service, token)
    client = _client(service)
    url = f"/api/class-teacher/evidence/sessions/{session_id}/class-results"

    before = client.get(url, headers=HEADERS).json()
    assert before["participant_count"] is None
    assert all(
        entry["relative_position"] is None
        for student in before["students"]
        for entry in student["results"].values()
    )

    updated = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={
            "operation_id": "csr-pc-update",
            "max_scores": {},
            "participant_count": 120,
        },
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "session_id": session_id,
        "updated": True,
        "subjects": [],
        "participant_count": 120,
    }

    after = client.get(url, headers=HEADERS).json()
    # 顶层年级人数生效，相对位次随之可算：1 - (rank - 1) / (120 - 1)。
    assert after["participant_count"] == 120
    alpha_row = next(
        item for item in after["students"] if item["subject_id"] == alpha
    )
    assert alpha_row["results"]["数学"]["relative_position"] == pytest.approx(
        1 - 1 / 119
    )

    revisions_after_update = _all_object_revisions(service)
    replayed = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={
            "operation_id": "csr-pc-update",
            "max_scores": {},
            "participant_count": 120,
        },
    )
    assert replayed.status_code == 200
    assert replayed.json() == updated.json()
    # 幂等重放：不再重复加 revision。
    assert _all_object_revisions(service) == revisions_after_update


def test_update_session_participant_count_rejects_invalid_input(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    session_id, _alpha = _rank_session(service, token)
    client = _client(service)
    url = f"/api/class-teacher/evidence/sessions/{session_id}/max-scores"

    for index, value in enumerate((0, -3)):
        rejected = client.patch(
            url,
            headers=HEADERS,
            json={
                "operation_id": f"csr-pc-nonpositive-{index}",
                "participant_count": value,
            },
        )
        assert rejected.status_code == 422
        assert (
            rejected.json()["error"]["code"]
            == "assessment_participant_count_invalid"
        )

    # 空更新：既不改满分也不改年级人数。
    empty = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "csr-pc-empty", "max_scores": {}},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "assessment_session_update_empty"


# ---------- 科目名归一 ----------

def test_canonical_subjects_merges_variants() -> None:
    assert canonical_subjects(["英语笔试加听力", "英语笔试加听说", "数学"]) == {
        "英语笔试加听力": "英语",
        "英语笔试加听说": "英语",
        "数学": "数学",
    }


def test_canonical_subjects_hides_raw_english_when_variant_present() -> None:
    # 变体列（带听说总分）占「英语」；同场原始「英语」列（笔试单项）被隐藏。
    assert canonical_subjects(["英语", "英语笔试加听力"]) == {
        "英语笔试加听力": "英语",
    }
    assert hidden_subjects(["英语", "英语笔试加听力"]) == {"英语"}


def test_canonical_subjects_keeps_plain_english_and_others() -> None:
    # 只有原始「英语」时保持「英语」；「英语听说」始终隐藏；其余不受影响。
    assert canonical_subjects(["英语", "数学", "英语听说"]) == {
        "英语": "英语",
        "数学": "数学",
    }
    assert hidden_subjects(["英语", "数学", "英语听说"]) == {"英语听说"}


def _english_sessions(service: VaultService, token: str) -> tuple[str, str]:
    """A 场只有「英语」；B 场有「英语笔试加听力」+「英语听说」+ 多余「英语」。"""
    alpha = _subject(service, token, "csr-eng-subject-a", "synthetic-csr-401", "合成英语甲")
    beta = _subject(service, token, "csr-eng-subject-b", "synthetic-csr-402", "合成英语乙")
    session_a = _session("英语A场", occurred_on="2026-03-10", source_reference="ref-csr-eng-a")
    session_b = _session("英语B场", occurred_on="2026-05-10", source_reference="ref-csr-eng-b")
    _confirm(service, token, "csr-confirm-eng-a", [{
        "title": "英语A场",
        "subject_name": "英语",
        "occurred_on": "2026-03-10",
        "max_score": 100,
        "rank_scope": "grade",
        "participant_count": 40,
        "session": session_a,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 90, "rank": 5},
            {"subject_id": beta, "result_state": "normal", "score": 70, "rank": 20},
        ],
    }])
    _confirm(service, token, "csr-confirm-eng-b", [
        {
            "title": "英语B场",
            "subject_name": "英语笔试加听力",
            "occurred_on": "2026-05-10",
            "max_score": 120,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_b,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 96, "rank": 4},
                {"subject_id": beta, "result_state": "normal", "score": 60, "rank": 25},
            ],
        },
        {
            "title": "英语B场",
            "subject_name": "英语听说",
            "occurred_on": "2026-05-10",
            "max_score": 30,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_b,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 28, "rank": 6},
            ],
        },
        {
            "title": "英语B场",
            "subject_name": "英语",
            "occurred_on": "2026-05-10",
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_b,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 85, "rank": 8},
            ],
        },
    ])
    return alpha, beta


def test_academic_series_merges_english_variants_across_sessions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    alpha, _beta = _english_sessions(service, token)

    analysis = service.academic.read(token=token, subject_id=alpha)

    series = {
        (item["series"], item["subject_name"]): item
        for item in analysis["series"]
    }
    # A 场原始「英语」与 B 场「英语笔试加听力」归入同一条英语链，跨场次连续。
    english = series[("学期大考", "英语")]
    assert [point["occurred_on"] for point in english["points"]] == [
        "2026-03-10",
        "2026-05-10",
    ]
    assert [point["score"] for point in english["points"]] == [90, 96]
    assert len(english["segments"]) == 1
    # B 场多余的原始「英语」列与「英语听说」都被隐藏，不再单独成科。
    assert set(series) == {("学期大考", "英语")}
    # 筛选项与 subject_name 筛选都按归一名匹配。
    assert set(analysis["filter_options"]["subjects"]) == {"英语"}
    filtered = service.academic.read(
        token=token, subject_id=alpha, subject_name="英语"
    )
    filtered_series = filtered["series"]
    assert len(filtered_series) == 1
    assert filtered_series[0]["subject_name"] == "英语"
    assert len(filtered_series[0]["points"]) == 2


def test_class_results_and_trend_use_canonical_subjects(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    alpha, _beta = _english_sessions(service, token)
    session_b_id = _session_id(service, token, "英语B场")
    client = _client(service)

    payload = client.get(
        f"/api/class-teacher/evidence/sessions/{session_b_id}/class-results",
        headers=HEADERS,
    ).json()

    subjects = {item["subject_name"]: item for item in payload["subjects"]}
    # 隐藏列不出现：只剩归一后的「英语」一科。
    assert set(subjects) == {"英语"}
    # 变体列并入「英语」参与统计。
    english = subjects["英语"]
    assert english["max_score"] == 120.0
    assert english["stats"]["count"] == 2
    assert english["stats"]["average"] == 78.0
    assert english["stats"]["average_rank"] == 14.5
    assert english["stats"]["front30pct_count"] == 1
    alpha_row = next(
        item for item in payload["students"] if item["subject_id"] == alpha
    )
    assert set(alpha_row["results"]) == {"英语"}
    assert alpha_row["results"]["英语"]["score"] == 96

    trend = client.get(
        "/api/class-teacher/evidence/class-trend", headers=HEADERS
    ).json()
    by_title = {item["title"]: item for item in trend["sessions"]}
    a_names = {item["subject_name"] for item in by_title["英语A场"]["subjects"]}
    assert a_names == {"英语"}
    b_subjects = {
        item["subject_name"]: item for item in by_title["英语B场"]["subjects"]
    }
    assert set(b_subjects) == {"英语"}
    assert b_subjects["英语"]["average"] == 78.0
    assert b_subjects["英语"]["average_rank"] == 14.5
    assert b_subjects["英语"]["front30pct_count"] == 1

    overview = service.class_overview.academic_overview(token=token)
    b_overview = next(
        item for item in overview["sessions"] if item["title"] == "英语B场"
    )
    assert b_overview["subject_names"] == ["英语"]


# ---------- 全局满分/年级人数 ----------

def _global_sessions(service: VaultService, token: str) -> str:
    """A 场：数学 + 英语笔试加听力 + 多余「英语」；B 场：只有数学（无满分）。"""
    alpha = _subject(service, token, "csr-glb-subject-a", "synthetic-csr-501", "合成全局甲")
    beta = _subject(service, token, "csr-glb-subject-b", "synthetic-csr-502", "合成全局乙")
    session_a = _session("全局A场", occurred_on="2026-04-01", source_reference="ref-csr-glb-a")
    session_b = _session("全局B场", occurred_on="2026-05-01", source_reference="ref-csr-glb-b")
    _confirm(service, token, "csr-confirm-glb-a", [
        {
            "title": "全局A场",
            "subject_name": "数学",
            "occurred_on": "2026-04-01",
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_a,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 90, "rank": 3},
                {"subject_id": beta, "result_state": "normal", "score": 60, "rank": 9},
            ],
        },
        {
            "title": "全局A场",
            "subject_name": "英语笔试加听力",
            "occurred_on": "2026-04-01",
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_a,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 80, "rank": 6},
            ],
        },
        {
            "title": "全局A场",
            "subject_name": "英语",
            "occurred_on": "2026-04-01",
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 40,
            "session": session_a,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 70, "rank": 10},
            ],
        },
    ])
    _confirm(service, token, "csr-confirm-glb-b", [{
        "title": "全局B场",
        "subject_name": "数学",
        "occurred_on": "2026-05-01",
        "rank_scope": "grade",
        "participant_count": 40,
        "session": session_b,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 96, "rank": 2},
            {"subject_id": beta, "result_state": "normal", "score": 72, "rank": 7},
        ],
    }])
    return alpha


def test_global_max_scores_override_variant_columns_and_replay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    alpha = _global_sessions(service, token)
    client = _client(service)
    session_a_id = _session_id(service, token, "全局A场")

    # 先单场把 A 场数学改成 110：验证全局的有值即覆盖（含已有值）。
    single = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_a_id}/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-glb-single", "max_scores": {"数学": 110}},
    )
    assert single.status_code == 200

    response = client.patch(
        "/api/class-teacher/evidence/max-scores/global",
        headers=HEADERS,
        json={
            "operation_id": "csr-glb-update",
            "max_scores": {"数学": 150, "英语": 120},
            "participant_count": 300,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "sessions_updated": 2,
        "subjects": {"数学": 2, "英语": 1},
        "participant_count": 300,
    }

    a_results = client.get(
        f"/api/class-teacher/evidence/sessions/{session_a_id}/class-results",
        headers=HEADERS,
    ).json()
    subjects = {item["subject_name"]: item for item in a_results["subjects"]}
    # 隐藏列不出现：只剩「数学」与归一后的「英语」。
    assert set(subjects) == {"数学", "英语"}
    # 单场已改的 110 被全局覆盖为 150。
    assert subjects["数学"]["max_score"] == 150.0
    # canonical「英语」落到变体列「英语笔试加听力」。
    assert subjects["英语"]["max_score"] == 120.0
    # 隐藏的原始「英语」列不被 canonical「英语」覆盖，库存满分仍是 100。
    raw_english = next(
        item
        for item in service.evidence.list_subject_evidence(
            token=token, subject_id=alpha
        )["items"]
        if item["subject_name"] == "英语"
    )
    assert raw_english["max_score"] == 100
    assert a_results["participant_count"] == 300

    session_b_id = _session_id(service, token, "全局B场")
    b_results = client.get(
        f"/api/class-teacher/evidence/sessions/{session_b_id}/class-results",
        headers=HEADERS,
    ).json()
    assert b_results["subjects"][0]["subject_name"] == "数学"
    assert b_results["subjects"][0]["max_score"] == 150.0
    assert b_results["participant_count"] == 300

    revisions_after_update = _all_object_revisions(service)
    replayed = client.patch(
        "/api/class-teacher/evidence/max-scores/global",
        headers=HEADERS,
        json={
            "operation_id": "csr-glb-update",
            "max_scores": {"数学": 150, "英语": 120},
            "participant_count": 300,
        },
    )
    assert replayed.status_code == 200
    assert replayed.json() == response.json()
    # 幂等重放：不再重复加 revision。
    assert _all_object_revisions(service) == revisions_after_update


def test_global_max_scores_rejects_invalid_input(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    _global_sessions(service, token)
    client = _client(service)
    url = "/api/class-teacher/evidence/max-scores/global"

    zero = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "csr-glb-zero", "max_scores": {"数学": 0}},
    )
    assert zero.status_code == 422
    assert zero.json()["error"]["code"] == "assessment_max_score_invalid"

    bad_count = client.patch(
        url,
        headers=HEADERS,
        json={
            "operation_id": "csr-glb-bad-count",
            "max_scores": {},
            "participant_count": 0,
        },
    )
    assert bad_count.status_code == 422
    assert (
        bad_count.json()["error"]["code"]
        == "assessment_participant_count_invalid"
    )

    # 空更新：既不改满分也不改年级人数。
    empty = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "csr-glb-empty", "max_scores": {}},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "assessment_session_update_empty"


def test_rank_metrics_null_without_participant_count(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    session_id, _alpha = _rank_session(service, token)
    client = _client(service)

    payload = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()

    stats = payload["subjects"][0]["stats"]
    assert stats["subject_name"] == "数学"
    # 有校次（2、9）但无年级人数：均次与前 50/100 可算，前 30% 为 null。
    assert stats["average_rank"] == 5.5
    assert stats["top50_count"] == 2
    assert stats["top100_count"] == 2
    assert stats["front30pct_count"] is None


# ---------- 总分满分派生 ----------

def test_total_max_score_derives_from_component_subjects(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    _build_class_session(service, token)
    session_id = _session_id(service, token, "合成期末")
    client = _client(service)
    url = f"/api/class-teacher/evidence/sessions/{session_id}/class-results"

    before = client.get(url, headers=HEADERS).json()
    total_before = next(
        item for item in before["subjects"] if item["subject_name"] == "总分"
    )
    # 物理未定标：总分满分同样未定标，分段全部落「未定标」。
    assert total_before["max_score"] is None
    bands_before = {
        band["label"]: band["count"] for band in total_before["stats"]["bands"]
    }
    assert bands_before["未定标"] == 3

    updated = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-total-max", "max_scores": {"物理": 80}},
    )
    assert updated.status_code == 200

    after = client.get(url, headers=HEADERS).json()
    total_after = next(
        item for item in after["subjects"] if item["subject_name"] == "总分"
    )
    # 各科满分齐备：总分满分 = 数学 100 + 物理 80。
    assert total_after["max_score"] == 180.0
    bands_after = {
        band["label"]: band["count"] for band in total_after["stats"]["bands"]
    }
    # 183/180、125/180、100/180。
    assert bands_after["90-100%"] == 1
    assert bands_after["60-69%"] == 1
    assert bands_after["60%以下"] == 1
    assert bands_after["未定标"] == 0

    trend = client.get(
        "/api/class-teacher/evidence/class-trend", headers=HEADERS
    ).json()
    trend_total = next(
        item
        for item in trend["sessions"][0]["subjects"]
        if item["subject_name"] == "总分"
    )
    assert trend_total["max_score"] == 180.0


def test_max_scores_endpoints_ignore_total_key(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    _build_class_session(service, token)
    session_id = _session_id(service, token, "合成期末")
    client = _client(service)

    # 单场端点：「总分」键被忽略，其余科目正常更新。
    single = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={
            "operation_id": "csr-ignore-total",
            "max_scores": {"总分": 500, "物理": 80},
        },
    )
    assert single.status_code == 200
    assert single.json()["subjects"] == ["物理"]

    # 全局端点同样忽略「总分」。
    global_response = client.patch(
        "/api/class-teacher/evidence/max-scores/global",
        headers=HEADERS,
        json={
            "operation_id": "csr-ignore-total-global",
            "max_scores": {"总分": 999, "数学": 150},
        },
    )
    assert global_response.status_code == 200
    assert global_response.json()["subjects"] == {"数学": 1}

    # 总分满分仍按其余展示科目求和：150 + 80。
    results = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()
    total = next(
        item for item in results["subjects"] if item["subject_name"] == "总分"
    )
    assert total["max_score"] == 230.0

    # 只带「总分」键相当于空更新。
    empty = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}/max-scores",
        headers=HEADERS,
        json={"operation_id": "csr-ignore-total-empty", "max_scores": {"总分": 500}},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "assessment_session_update_empty"


# ---------- 等级采集与分布 ----------

def _grade_session(service: VaultService, token: str) -> tuple[str, str]:
    """两名学生一场考试：数学带等级（含前后空格与未识别值），语文不带等级。"""
    alpha = _subject(service, token, "csr-grd-subject-a", "synthetic-csr-601", "合成等级甲")
    beta = _subject(service, token, "csr-grd-subject-b", "synthetic-csr-602", "合成等级乙")
    session = _session("等级场", occurred_on="2026-06-10", source_reference="ref-csr-grade")
    _confirm(service, token, "csr-confirm-grade", [
        {
            "title": "等级场",
            "subject_name": "数学",
            "occurred_on": "2026-06-10",
            "max_score": 100,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 95, "grade_level": " A+ "},
                {"subject_id": beta, "result_state": "normal", "score": 70, "grade_level": "优秀"},
            ],
        },
        {
            "title": "等级场",
            "subject_name": "语文",
            "occurred_on": "2026-06-10",
            "max_score": 100,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 80},
                {"subject_id": beta, "result_state": "normal", "score": 60},
            ],
        },
    ])
    return alpha, beta


def test_grade_levels_stored_counted_and_null_without_grades(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    token = ""
    alpha, _beta = _grade_session(service, token)
    session_id = _session_id(service, token, "等级场")
    client = _client(service)

    payload = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()

    subjects = {item["subject_name"]: item for item in payload["subjects"]}
    math_grades = {
        item["label"]: item["count"] for item in subjects["数学"]["stats"]["grade_counts"]
    }
    # 段序固定 A+…C+其他；「 A+ 」trim 后精确匹配入 A+，未识别的「优秀」入其他。
    assert list(math_grades) == ["A+", "A", "B+", "B", "C+", "C", "其他"]
    assert math_grades == {"A+": 1, "A": 0, "B+": 0, "B": 0, "C+": 0, "C": 0, "其他": 1}
    # 语文无人带等级：grade_counts 为 null，供前端回退得分率分段。
    assert subjects["语文"]["stats"]["grade_counts"] is None

    alpha_row = next(
        item for item in payload["students"] if item["subject_id"] == alpha
    )
    assert alpha_row["results"]["数学"]["grade_level"] == "A+"
    assert alpha_row["results"]["语文"]["grade_level"] is None


def test_grade_level_rejects_overlong_value(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    alpha = _subject(service, token, "csr-grd-subject-c", "synthetic-csr-603", "合成等级丙")
    session = _session("超长等级场", occurred_on="2026-06-11", source_reference="ref-csr-grade-long")

    with pytest.raises(VaultError) as excinfo:
        _confirm(service, token, "csr-confirm-grade-long", [{
            "title": "超长等级场",
            "subject_name": "数学",
            "occurred_on": "2026-06-11",
            "session": session,
            "results": [
                {
                    "subject_id": alpha,
                    "result_state": "normal",
                    "score": 90,
                    "grade_level": "这是一个远超上限的非常长的一个等级标识字符串啊啊啊",
                },
            ],
        }])

    assert excinfo.value.code == "assessment_grade_level_invalid"


def test_grade_counts_follow_canonical_hiding_and_variant_merge(
    tmp_path: Path,
) -> None:
    """英语变体的等级归入「英语」；被隐藏的原始「英语」与「英语听说」等级不进输出。"""
    service = _service(tmp_path)
    token = ""
    alpha = _subject(service, token, "csr-grd-subject-d", "synthetic-csr-604", "合成等级丁")
    beta = _subject(service, token, "csr-grd-subject-e", "synthetic-csr-605", "合成等级戊")
    session = _session("等级英语场", occurred_on="2026-06-12", source_reference="ref-csr-grade-eng")
    _confirm(service, token, "csr-confirm-grade-eng", [
        {
            "title": "等级英语场",
            "subject_name": "英语笔试加听力",
            "occurred_on": "2026-06-12",
            "max_score": 100,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 90, "grade_level": "A+"},
                {"subject_id": beta, "result_state": "normal", "score": 75, "grade_level": "A"},
            ],
        },
        {
            "title": "等级英语场",
            "subject_name": "英语听说",
            "occurred_on": "2026-06-12",
            "max_score": 25,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 22, "grade_level": "C+"},
            ],
        },
        {
            "title": "等级英语场",
            "subject_name": "英语",
            "occurred_on": "2026-06-12",
            "max_score": 100,
            "session": session,
            "results": [
                {"subject_id": alpha, "result_state": "normal", "score": 66, "grade_level": "B"},
            ],
        },
    ])
    session_id = _session_id(service, token, "等级英语场")
    client = _client(service)

    payload = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()

    subjects = {item["subject_name"]: item for item in payload["subjects"]}
    assert set(subjects) == {"英语"}
    english_grades = {
        item["label"]: item["count"]
        for item in subjects["英语"]["stats"]["grade_counts"]
    }
    # 只统计变体列的 A+/A；隐藏列的 C+、B 不混入。
    assert english_grades["A+"] == 1
    assert english_grades["A"] == 1
    assert english_grades["B"] == 0
    assert english_grades["C+"] == 0
    assert english_grades["其他"] == 0


# ---------- 场次短标签与同学期排序 ----------

def test_short_label_pure_function() -> None:
    from backend.class_teacher.session_label import (
        exam_phase,
        session_order,
        short_label,
    )

    assert short_label(grade="七年级", term="上学期", exam_type="期中考试") == "七上期中"
    assert short_label(grade="八年级", term="下学期", exam_type="期末考试") == "八下期末"
    # exam_type 缺省时从标题推断。
    assert short_label(grade="七年级", term="上学期", title="七上期末考试") == "七上期末"
    # 考试性质推不出时只显示「七上」。
    assert short_label(grade="七年级", term="上学期", exam_type="月考") == "七上"
    # 缺年级/学期时返回空串，由前端回退标题。
    assert short_label(grade=None, term="上学期", exam_type="期中") == ""
    assert short_label(grade="七年级", term=None) == ""

    assert exam_phase("期中考试", None) == "期中"
    assert exam_phase(None, "2025 期末统考") == "期末"
    assert exam_phase("常规检测", "周练") == ""

    # 学期链排序键：年级 → 学期 → 期中<期末，元数据不足返回 None。
    assert session_order({
        "grade": "七年级", "term": "上学期", "exam_type": "期中考试",
    }) == (7, 0, 0)
    assert session_order({
        "grade": "七年级", "term": "上学期", "exam_type": "期末考试",
    }) == (7, 0, 1)
    assert session_order({"grade": "七年级", "term": "下学期"}) == (7, 1, 2)
    assert session_order({"grade": "七年级"}) is None


def test_class_trend_orders_phase_within_same_term(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    alpha = _subject(service, token, "csr-phase-subject-a", "synthetic-csr-601", "合成期序甲")
    # 期中考试日期反而更晚：同学期内仍按期中<期末排，日期只作兜底。
    midterm = _session(
        "合成期中", occurred_on="2026-05-20", source_reference="ref-csr-mid",
    )
    final = {
        **_session("合成期末考", occurred_on="2026-04-01", source_reference="ref-csr-fin"),
        "exam_type": "期末",
    }
    _confirm(service, token, "csr-confirm-mid", [{
        "title": "合成期中",
        "subject_name": "数学",
        "occurred_on": "2026-05-20",
        "max_score": 100,
        "session": midterm,
        "results": [{"subject_id": alpha, "result_state": "normal", "score": 80}],
    }])
    _confirm(service, token, "csr-confirm-fin", [{
        "title": "合成期末考",
        "subject_name": "数学",
        "occurred_on": "2026-04-01",
        "max_score": 100,
        "session": final,
        "results": [{"subject_id": alpha, "result_state": "normal", "score": 90}],
    }])
    client = _client(service)

    sessions = client.get(
        "/api/class-teacher/evidence/class-trend", headers=HEADERS
    ).json()["sessions"]

    assert [item["short_label"] for item in sessions] == ["八下期中", "八下期末"]
    # 期中的 occurred_on 更晚，但排在期末之前。
    assert [item["occurred_on"] for item in sessions] == ["2026-05-20", "2026-04-01"]


def test_session_apis_expose_short_label(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    ids = _build_class_session(service, token)
    session_id = _session_id(service, token, "合成期末")
    client = _client(service)

    results = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/class-results",
        headers=HEADERS,
    ).json()
    # 「合成期末」标题含「期末」：exam_type 为「期中」时以 exam_type 为准。
    assert results["short_label"] == "八下期中"

    overview = service.class_overview.academic_overview(token=token)
    entry = next(item for item in overview["sessions"] if item["session_id"] == session_id)
    assert entry["short_label"] == "八下期中"
    assert overview["latest_session"]["short_label"] == "八下期中"

    analysis = service.academic.read(token=token, subject_id=ids["alpha"])
    session = next(
        item for item in analysis["sessions"] if item["session_id"] == session_id
    )
    assert session["short_label"] == "八下期中"
    profile = analysis["profile"]
    assert profile["current"]["short_label"] == "八下期中"
    assert profile["total_trend"][0]["short_label"] == "八下期中"
    math = next(
        item for item in profile["subjects"] if item["subject_name"] == "数学"
    )
    assert math["points"][0]["short_label"] == "八下期中"


def test_analysis_points_carry_grade_level(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = ""
    alpha = _subject(service, token, "csr-gl-subject-a", "synthetic-csr-701", "合成等级甲")
    session = _session("等级场", occurred_on="2026-04-25", source_reference="ref-csr-gl")
    _confirm(service, token, "csr-confirm-gl", [{
        "title": "等级场",
        "subject_name": "数学",
        "occurred_on": "2026-04-25",
        "max_score": 100,
        "rank_scope": "grade",
        "participant_count": 40,
        "session": session,
        "results": [
            {"subject_id": alpha, "result_state": "normal", "score": 95, "rank": 2, "grade_level": "A+"},
        ],
    }])

    analysis = service.academic.read(token=token, subject_id=alpha)

    point = analysis["sessions"][0]["evidence"][0]
    assert point["grade_level"] == "A+"
    math = next(
        item for item in analysis["profile"]["subjects"] if item["subject_name"] == "数学"
    )
    assert math["latest"]["grade_level"] == "A+"
    assert math["points"][0]["grade_level"] == "A+"
