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
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 确认时间不参与场次指纹（指纹只覆盖元数据字段），固定取值只为结果可重复。
CONFIRMED_AT = "2026-03-01T08:00:00+00:00"
DELETE_PHRASE = "确认删除本场考试"


def _service(tmp_path: Path) -> tuple[VaultService, str, str]:
    service = VaultService(WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
    ))
    service.ensure_plaintext_ready()
    token = ""
    subject = service.support.create_subject(
        token=token,
        operation_id="session-admin-subject",
        source_student_id="session-admin-student-001",
        display_name="合成场次学生",
        class_label="合成一班",
    )
    return service, token, str(subject["subject_id"])


def _session_block(
    title: str,
    *,
    occurred_on: str,
    source_reference: str,
    grade: str = "八年级",
    term: str = "下学期",
    exam_type: str = "单元测验",
    academic_year: str = "2025-2026",
    comparison_series: str = "数学单元",
) -> dict[str, object]:
    return {
        "title": title,
        "academic_year": academic_year,
        "term": term,
        "grade": grade,
        "exam_type": exam_type,
        "comparison_series": comparison_series,
        "occurred_on": occurred_on,
        "source_reference": source_reference,
        "teacher_confirmed_at": CONFIRMED_AT,
    }


def _confirm(
    service: VaultService,
    token: str,
    subject_id: str,
    operation_id: str,
    sessions: list[dict[str, object]],
) -> dict[str, object]:
    assessments = []
    for index, session in enumerate(sessions):
        assessments.append({
            "title": str(session["title"]),
            "subject_name": "数学",
            "occurred_on": str(session["occurred_on"]),
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": 40,
            "session": session,
            "results": [{
                "subject_id": subject_id,
                "result_state": "normal",
                "score": 80 + index,
                "rank": index + 1,
            }],
        })
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        # 来源标签随操作编号变化，避免触发导入级整批去重。
        "source_label": f"合成来源-{operation_id}",
        "assessments": assessments,
    })
    return service.evidence.confirm_batch(
        token=token, operation_id=operation_id, batch=batch
    )


def _sessions(service: VaultService, token: str) -> list[dict[str, object]]:
    return list(service.class_overview.academic_overview(token=token)["sessions"])


def _session_id(service: VaultService, token: str, title: str) -> str:
    matches = [
        str(item["session_id"])
        for item in _sessions(service, token)
        if item["title"] == title
    ]
    assert len(matches) == 1
    return matches[0]


def _count(service: VaultService, sql: str, params: tuple = ()) -> int:
    with closing(service.database.connect()) as connection:
        return int(connection.execute(sql, params).fetchone()[0])


def test_overview_sessions_expose_semester_fields(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id, "session-admin-c1", [
        _session_block("合成月考", occurred_on="2026-03-01", source_reference="ref-1"),
    ])

    entry = _sessions(service, token)[0]
    assert entry["grade"] == "八年级"
    assert entry["term"] == "下学期"
    assert entry["exam_type"] == "单元测验"
    assert entry["academic_year"] == "2025-2026"


def test_update_session_metadata_reorders_term_chain(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id, "session-admin-c2a", [
        _session_block("上学期晚场", occurred_on="2026-03-01", source_reference="ref-a", term="上学期"),
        _session_block("下学期早场", occurred_on="2026-02-01", source_reference="ref-b", term="下学期"),
    ])

    analysis = service.academic.read(token=token, subject_id=subject_id)
    points = analysis["series"][0]["points"]
    # 学期链优先于考试日期：八上排在八下之前。
    assert [point["occurred_on"] for point in points] == ["2026-03-01", "2026-02-01"]

    session_id = _session_id(service, token, "下学期早场")
    updated = service.evidence.update_session_metadata(
        token=token,
        operation_id="session-admin-u2",
        session_id=session_id,
        fields={"term": "上学期"},
    )
    assert updated["updated"] is True
    assert updated["session"]["term"] == "上学期"

    analysis = service.academic.read(token=token, subject_id=subject_id)
    points = analysis["series"][0]["points"]
    # 更正后两场同为八上，退化为按考试日期排序。
    assert [point["occurred_on"] for point in points] == ["2026-02-01", "2026-03-01"]
    patched = [
        item for item in analysis["sessions"]
        if str(item["session_id"]) == session_id
    ][0]
    assert patched["term"] == "上学期"


def test_update_session_metadata_recomputes_fingerprint(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    original = _session_block("指纹场次", occurred_on="2026-03-01", source_reference="ref-f", term="下学期")
    _confirm(service, token, subject_id, "session-admin-c3a", [original])
    session_id = _session_id(service, token, "指纹场次")

    service.evidence.update_session_metadata(
        token=token,
        operation_id="session-admin-u3",
        session_id=session_id,
        fields={"term": "上学期"},
    )

    # 原参数指纹已漂移，重新登记会新建场次。
    _confirm(service, token, subject_id, "session-admin-c3b", [original])
    assert len(_sessions(service, token)) == 2

    # 新参数与更正后的指纹一致，复用原场次。
    _confirm(service, token, subject_id, "session-admin-c3c", [
        _session_block("指纹场次", occurred_on="2026-03-01", source_reference="ref-f", term="上学期"),
    ])
    sessions = _sessions(service, token)
    assert len(sessions) == 2
    reused = [item for item in sessions if str(item["session_id"]) == session_id][0]
    assert reused["member_count"] == 2


def test_update_session_metadata_conflict(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    shared = {
        "occurred_on": "2026-03-01",
        "source_reference": "ref-shared",
        "term": "下学期",
    }
    _confirm(service, token, subject_id, "session-admin-c4", [
        _session_block("场次甲", **shared),
        _session_block("场次乙", **shared),
    ])
    session_id = _session_id(service, token, "场次乙")

    with pytest.raises(VaultError) as error:
        service.evidence.update_session_metadata(
            token=token,
            operation_id="session-admin-u4",
            session_id=session_id,
            fields={"title": "场次甲"},
        )
    assert error.value.code == "assessment_session_conflict"
    assert error.value.status_code == 409


def test_confirm_batch_merges_multi_subject_into_one_session(tmp_path: Path) -> None:
    """一次 confirm_batch 含多学科：只能落一个 assessment_sessions 行。"""
    service, token, subject_id = _service(tmp_path)
    # 不传 teacher_confirmed_at：每个学科登记时各生成确认时刻，
    # 回归“多学科宽表因确认时间戳不同被拆成多个场次”的 bug。
    session = {
        "title": "多学科统考",
        "academic_year": "2025-2026",
        "term": "下学期",
        "grade": "八年级",
        "exam_type": "期中",
        "comparison_series": "学期大考",
        "occurred_on": "2026-04-15",
        "source_reference": "ref-multi",
    }
    assessments = [
        {
            "title": "多学科统考",
            "subject_name": subject_name,
            "occurred_on": "2026-04-15",
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": 40,
            "session": session,
            "results": [{
                "subject_id": subject_id,
                "result_state": "normal",
                "score": score,
                "rank": 1,
            }],
        }
        for subject_name, score in (("数学", 90), ("英语", 85))
    ]
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        "source_label": "合成来源-多学科",
        "assessments": assessments,
    })
    result = service.evidence.confirm_batch(
        token=token, operation_id="session-admin-c9", batch=batch
    )
    assert result["created_assessments"] == 2

    assert _count(service, "SELECT COUNT(*) FROM assessment_sessions") == 1
    sessions = _sessions(service, token)
    assert len(sessions) == 1
    # 两个科目的证据都是该场次的成员（学科名取自成员证据 payload）。
    assert sessions[0]["member_count"] == 2
    assert sorted(sessions[0]["subject_names"]) == ["数学", "英语"]


def test_confirm_batches_merge_despite_distinct_confirmed_at(tmp_path: Path) -> None:
    """元数据相同的两个批次仅确认时间不同：归并为一个场次。"""
    service, token, subject_id = _service(tmp_path)
    first = _session_block(
        "跨批次场次", occurred_on="2026-03-01", source_reference="ref-merge"
    )
    second = _session_block(
        "跨批次场次", occurred_on="2026-03-01", source_reference="ref-merge"
    )
    first["teacher_confirmed_at"] = "2026-03-01T08:00:00+00:00"
    second["teacher_confirmed_at"] = "2026-03-02T09:30:00+00:00"
    _confirm(service, token, subject_id, "session-admin-c10a", [first])
    _confirm(service, token, subject_id, "session-admin-c10b", [second])

    sessions = _sessions(service, token)
    assert len(sessions) == 1
    assert sessions[0]["member_count"] == 2


def _decide_observe(
    service: VaultService,
    token: str,
    subject_id: str,
    evidence_version_id: str,
) -> str:
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="session-admin-card",
        evidence_version_id=evidence_version_id,
        observed_fact="合成证据",
        comparability="insufficient_information",
        limitations=[],
        verification_question="是否需要核实？",
        low_risk_next_step="先了解情况",
        evidence_sufficiency="单条证据",
        review_suggestion="一周后复查",
    )
    analysis = service.academic.read(token=token, subject_id=subject_id)
    service.academic.decide(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
        operation_id="session-admin-decide",
        revision=int(card["revision"]),
        decision="observe",
        reason="需要人工复查。",
        plan_id=None,
        review_at="2026-09-01",
        source_version=str(analysis["source_version"]),
    )
    return str(card["attention_card_id"])


def test_delete_session_removes_rows_and_respects_import_ownership(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _service(tmp_path)
    # 导入批次 X 含两场考试（共享），批次 Y 只有一场（完全归属）。
    _confirm(service, token, subject_id, "session-admin-c5a", [
        _session_block("共享场次一", occurred_on="2026-03-01", source_reference="ref-x1"),
        _session_block("共享场次二", occurred_on="2026-04-01", source_reference="ref-x2"),
    ])
    _confirm(service, token, subject_id, "session-admin-c5b", [
        _session_block("独立场次", occurred_on="2026-05-01", source_reference="ref-y"),
    ])
    session_one = _session_id(service, token, "共享场次一")
    session_solo = _session_id(service, token, "独立场次")

    analysis = service.academic.read(token=token, subject_id=subject_id)
    evidence_id = next(
        str(item["evidence"][0]["evidence_version_id"])
        for item in analysis["sessions"]
        if str(item["session_id"]) == session_one
    )
    card_id = _decide_observe(service, token, subject_id, evidence_id)
    assert _count(
        service,
        "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_id = ?",
        (card_id,),
    ) == 1

    preview = service.evidence.preview_delete_session(token=token, session_id=session_one)
    assert preview["title"] == "共享场次一"
    assert preview["counts"] == {
        "results": 1,
        "assessments": 1,
        "imports": 0,
        "attention_cards": 1,
    }
    assert len(str(preview["preview_version"])) == 64
    assert preview["confirmation_phrase"] == DELETE_PHRASE

    deleted = service.evidence.delete_session(
        token=token,
        operation_id="session-admin-d5a",
        session_id=session_one,
        preview_version=str(preview["preview_version"]),
        confirmation_phrase=DELETE_PHRASE,
    )
    assert deleted["deleted"] is True

    # 场次、成员、证据、成绩、排名、考试与加密对象全部清除。
    assert _count(service, "SELECT COUNT(*) FROM assessment_sessions WHERE session_id = ?", (session_one,)) == 0
    assert _count(service, "SELECT COUNT(*) FROM assessment_session_members WHERE session_id = ?", (session_one,)) == 0
    assert _count(service, "SELECT COUNT(*) FROM evidence_versions WHERE evidence_version_id = ?", (evidence_id,)) == 0
    assert _count(service, "SELECT COUNT(*) FROM subject_results") == 2
    assert _count(service, "SELECT COUNT(*) FROM rank_contexts") == 2
    assert _count(service, "SELECT COUNT(*) FROM assessments") == 2
    assert _count(service, "SELECT COUNT(*) FROM attention_cards WHERE attention_card_id = ?", (card_id,)) == 0
    assert _count(service, "SELECT COUNT(*) FROM attention_card_evidence_links WHERE attention_card_id = ?", (card_id,)) == 0
    assert _count(service, "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_id = ?", (card_id,)) == 0
    assert _count(
        service,
        "SELECT COUNT(*) FROM encrypted_objects WHERE object_id LIKE 'evidence-version-%'",
    ) == 2
    assert _count(
        service,
        "SELECT COUNT(*) FROM encrypted_objects WHERE object_id = ?",
        (f"assessment-session-{session_one}",),
    ) == 0
    # 共享导入批次及其指纹保留。
    assert _count(service, "SELECT COUNT(*) FROM assessment_imports") == 2
    assert _count(service, "SELECT COUNT(*) FROM source_fingerprints") == 2

    # 完全归属的导入批次随场次一起删除。
    service.evidence.delete_session(
        token=token,
        operation_id="session-admin-d5b",
        session_id=session_solo,
        preview_version=None,
        confirmation_phrase=DELETE_PHRASE,
    )
    assert _count(service, "SELECT COUNT(*) FROM assessment_imports") == 1
    assert _count(service, "SELECT COUNT(*) FROM source_fingerprints") == 1
    assert _count(service, "SELECT COUNT(*) FROM subject_results") == 1


def test_delete_session_allows_reimport(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    session = _session_block("重登场次", occurred_on="2026-03-01", source_reference="ref-r")
    _confirm(service, token, subject_id, "session-admin-c6", [session])
    session_id = _session_id(service, token, "重登场次")
    service.evidence.delete_session(
        token=token,
        operation_id="session-admin-d6",
        session_id=session_id,
        preview_version=None,
        confirmation_phrase=DELETE_PHRASE,
    )

    # 同一文件内容重新登记：导入指纹已随批次删除，不再判重。
    reconfirmed = _confirm(service, token, subject_id, "session-admin-c6b", [session])
    assert reconfirmed["duplicate"] is False
    assert reconfirmed["created_results"] == 1
    assert len(_sessions(service, token)) == 1


def test_delete_session_guards_and_idempotent_replay(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id, "session-admin-c7", [
        _session_block("守卫场次", occurred_on="2026-03-01", source_reference="ref-g"),
    ])
    session_id = _session_id(service, token, "守卫场次")
    preview = service.evidence.preview_delete_session(token=token, session_id=session_id)

    with pytest.raises(VaultError) as stale:
        service.evidence.delete_session(
            token=token,
            operation_id="session-admin-d7a",
            session_id=session_id,
            preview_version="0" * 64,
            confirmation_phrase=DELETE_PHRASE,
        )
    assert stale.value.code == "assessment_session_delete_preview_changed"
    assert stale.value.status_code == 409

    with pytest.raises(VaultError) as phrase:
        service.evidence.delete_session(
            token=token,
            operation_id="session-admin-d7b",
            session_id=session_id,
            preview_version=str(preview["preview_version"]),
            confirmation_phrase="随便写写",
        )
    assert phrase.value.code == "assessment_session_delete_confirmation_required"
    assert phrase.value.status_code == 422

    deleted = service.evidence.delete_session(
        token=token,
        operation_id="session-admin-d7c",
        session_id=session_id,
        preview_version=str(preview["preview_version"]),
        confirmation_phrase=DELETE_PHRASE,
    )
    replayed = service.evidence.delete_session(
        token=token,
        operation_id="session-admin-d7c",
        session_id=session_id,
        preview_version=None,
        confirmation_phrase="随便写写",
    )
    assert replayed == deleted

    with pytest.raises(VaultError) as missing:
        service.evidence.preview_delete_session(token=token, session_id=session_id)
    assert missing.value.code == "assessment_session_not_found"
    assert missing.value.status_code == 404


def test_session_admin_endpoints(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id, "session-admin-c8", [
        _session_block("接口场次", occurred_on="2026-03-01", source_reference="ref-api"),
    ])
    session_id = _session_id(service, token, "接口场次")

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code}},
        )

    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    headers = {"x-class-teacher-client": "class-teacher-browser-v1"}

    updated = client.patch(
        f"/api/class-teacher/evidence/sessions/{session_id}",
        headers=headers,
        json={"operation_id": "session-admin-u8", "term": "上学期"},
    )
    assert updated.status_code == 200
    assert updated.json()["updated"] is True
    assert updated.json()["session"]["term"] == "上学期"

    missing = client.patch(
        "/api/class-teacher/evidence/sessions/session-missing",
        headers=headers,
        json={"operation_id": "session-admin-u8b", "term": "上学期"},
    )
    assert missing.status_code == 404

    preview = client.get(
        f"/api/class-teacher/evidence/sessions/{session_id}/delete-preview",
        headers=headers,
    )
    assert preview.status_code == 200
    assert preview.json()["counts"]["results"] == 1

    deleted = client.request(
        "DELETE",
        f"/api/class-teacher/evidence/sessions/{session_id}",
        headers=headers,
        json={
            "operation_id": "session-admin-d8",
            "preview_version": preview.json()["preview_version"],
            "confirmation_phrase": DELETE_PHRASE,
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert _count(service, "SELECT COUNT(*) FROM assessment_sessions") == 0
