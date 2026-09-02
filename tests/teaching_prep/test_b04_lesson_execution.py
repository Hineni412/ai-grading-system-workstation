from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)

from .test_a01_foundation import _migrated_service
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze,
    _freeze_ready_setup,
)
from .test_b01_question_selection import _add_question, _bank
from .test_b02_pptx_editor import _deck


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_reference_deck(tmp_path: Path) -> Path:
    """把合成的参考课件替换为 python-pptx 真包（本机执行要能打开它）。

    a05 的手工 zip 课件只覆盖解析链路；pptx_editor 用 python-pptx
    读写，需要完整的 OOXML 包。页数保持 2 页，与已解析页索引一致。
    """
    target = tmp_path / "a05-reference.pptx"
    _deck(target, ["新授引入", "例题讲解"])
    return target


def _approved_plan(service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    pack, _created = _freeze(
        service,
        token="b04-resource-pack",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="b04-local-draft",
        mode="local_template",
        confirmed=True,
    )
    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="b04-confirm-draft",
        payload=local.payload,
        confirmed=True,
    )
    plan, _created = service.create_slide_plan(
        confirmed.id,
        request_token="b04-create-plan",
    )
    reviews = [
        {
            "operation_id": item["operation_id"],
            "decision": (
                "rejected"
                if item["execution_mode"] == "manual_only"
                else "approved"
            ),
            "reason": item["reason"],
            "planned_minutes": item["planned_minutes"],
            "teacher_note": "合成 B04 审批",
        }
        for item in plan.payload["operations"]
    ]
    approved, _created = service.revise_slide_plan(
        plan.id,
        request_token="b04-approve-plan",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="合成执行前审批完成",
    )
    assert approved.status == "approved"
    return lesson_id, reference_link, pack, approved


def _question_bank(tmp_path: Path) -> Path:
    bank = _bank(tmp_path / "questions.db")
    _add_question(
        bank,
        101,
        stem="求解 x<sup>2</sup> - 4 = 0",
        frequency=(1.0, 0.0, 0.0),
    )
    return bank


def test_local_execution_produces_output_audit_and_optional_worksheet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _link, _pack, plan = _approved_plan(
        service, tmp_path, monkeypatch
    )
    service.question_bank_path = _question_bank(tmp_path)
    source = _real_reference_deck(tmp_path)
    source_before = _sha256(source)

    row = service.execute_slide_plan_locally(
        plan.id,
        request_token="b04-execute-0001",
        confirmed=True,
    )

    assert row["status"] == "ready"
    assert row["version_number"] == 1
    assert row["lesson_node_id"] == lesson_id
    assert row["slide_plan_id"] == plan.id
    assert row["source_page_count"] == 2
    assert row["final_page_count"] == 3
    assert row["source_file_name"] == "a05-reference.pptx"
    output = service.paths["outputs"] / str(row["output_filename"])
    assert output.is_file()
    assert row["output_sha256"] == _sha256(output)
    assert _sha256(source) == source_before

    execution = service.pptx_outputs.execution_report(row)
    statuses = {
        item["kind"]: item["status"] for item in execution["operations"]
    }
    assert statuses["add_question_slide"] == "applied"
    assert execution["inserted_question_pages"] == [
        {"question_id": 101, "final_position": 3}
    ]

    audit = service.pptx_outputs.audit_report(row)
    assert audit["passed"] is True
    assert audit["page_budget"]["final_page_count"] == 3
    assert audit["question_pages"]["pages"] == {"3": 101}

    listed = service.list_pptx_local_outputs(lesson_id)
    assert [item["id"] for item in listed] == [row["id"]]
    fetched = service.get_pptx_local_output(row["id"])
    assert fetched["output_sha256"] == row["output_sha256"]
    download_path, filename = service.pptx_local_output_file(row["id"])
    assert download_path == output
    assert filename == row["output_filename"]

    # 学案 DOCX 可选生成，且幂等。
    with_worksheet = service.generate_worksheet_for_output(row["id"])
    assert with_worksheet["worksheet_relpath"]
    worksheet_path, worksheet_name = service.worksheet_download(row["id"])
    assert worksheet_path.is_file()
    assert worksheet_name.endswith(".docx")
    assert worksheet_path.read_bytes()[:2] == b"PK"
    repeated = service.generate_worksheet_for_output(row["id"])
    assert repeated["worksheet_relpath"] == with_worksheet["worksheet_relpath"]

    # 再次执行产生新版本，旧版本被替换。
    second = service.execute_slide_plan_locally(
        plan.id,
        request_token="b04-execute-0002",
        confirmed=True,
    )
    assert second["version_number"] == 2
    assert second["status"] == "ready"
    refreshed = {
        item["id"]: item["status"]
        for item in service.list_pptx_local_outputs(lesson_id)
    }
    assert refreshed[row["id"]] == "superseded"
    assert refreshed[second["id"]] == "ready"


def test_local_execution_requires_confirmation_and_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    _lesson_id, _link, _pack, plan = _approved_plan(
        service, tmp_path, monkeypatch
    )
    service.question_bank_path = _question_bank(tmp_path)
    _real_reference_deck(tmp_path)
    with pytest.raises(TeachingPrepValidationError):
        service.execute_slide_plan_locally(
            plan.id,
            request_token="b04-unconfirmed-execution",
            confirmed=False,
        )

    # 未审批的计划不能执行：用同一课时再造一张 in_review 计划。
    pack, _created = _freeze(
        service,
        token="b04-second-pack",
        lesson_id=_lesson_id,
        reference_link_id=_link.id,
    )
    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="b04-second-draft",
        mode="local_template",
        confirmed=True,
    )
    confirmed, _created = service.revise_lesson_draft(
        local.id,
        request_token="b04-second-confirm",
        payload=local.payload,
        confirmed=True,
    )
    pending, _created = service.create_slide_plan(
        confirmed.id,
        request_token="b04-pending-plan",
    )
    assert pending.status == "in_review"
    with pytest.raises(TeachingPrepConflictError):
        service.execute_slide_plan_locally(
            pending.id,
            request_token="b04-pending-execution",
            confirmed=True,
        )


def test_local_execution_fails_when_inserted_question_left_the_bank(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    _lesson_id, _link, _pack, plan = _approved_plan(
        service, tmp_path, monkeypatch
    )
    # 题库存在但没有计划引用的 101 题。
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 202)
    service.question_bank_path = bank

    with pytest.raises(TeachingPrepConflictError, match="101"):
        service.execute_slide_plan_locally(
            plan.id,
            request_token="b04-missing-question",
            confirmed=True,
        )
    assert service.list_pptx_local_outputs(_lesson_id) == ()
