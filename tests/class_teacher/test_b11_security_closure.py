from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from backend.class_teacher.assessment_evidence_service import (
    ConfirmedSpreadsheetAdapter,
)
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def test_b01_to_b10_payloads_remain_readable_in_current_plaintext_store(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspaces" / "class-teacher"
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=root,
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    service.ensure_plaintext_ready()
    token = ""
    identity_marker = "极敏感合成身份标记-B11"
    record_marker = "极敏感合成观察正文-B11"
    evidence_marker = "极敏感合成考试名称-B11"
    attention_marker = "极敏感合成关注事实-B11"

    subject = service.support.create_subject(
        token=token,
        operation_id="b11-create-subject",
        source_student_id="b11-secret-source-id",
        display_name=identity_marker,
        class_label="合成收口班",
    )
    subject_id = str(subject["subject_id"])
    service.support.create_record(
        token=token,
        operation_id="b11-create-record",
        subject_id=subject_id,
        record_kind="fact",
        content=record_marker,
        scene="合成安全检查",
        source="教师合成记录",
        basis=None,
        counterexample=None,
        category="general",
        observed_at="2026-08-03T09:00:00+08:00",
        review_at=None,
        expires_at=None,
    )
    batch = ConfirmedSpreadsheetAdapter().read(
        {
            "teacher_confirmed": True,
            "source_label": "合成收口内存预览",
            "assessments": [{
                "title": evidence_marker,
                "subject_name": "数学",
                "occurred_on": "2026-08-03",
                "max_score": 100,
                "rank_scope": "class",
                "participant_count": 40,
                "assessment_nature": "unit",
                "results": [{
                    "subject_id": subject_id,
                    "result_state": "normal",
                    "score": 0,
                    "rank": 40,
                }],
            }],
        }
    )
    service.evidence.confirm_batch(
        token=token,
        operation_id="b11-confirm-evidence",
        batch=batch,
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"][0]
    service.attention.create_from_evidence(
        token=token,
        operation_id="b11-create-attention",
        evidence_version_id=str(evidence["evidence_version_id"]),
        observed_fact=attention_marker,
        comparability="insufficient_information",
        limitations=["单条合成证据"],
        verification_question="合成核实问题",
        low_risk_next_step="建议教师先了解情况",
        evidence_sufficiency="不足",
        review_suggestion="后续复查",
    )
    with closing(service.database.connect()) as connection:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
    stored = service.database.database_path.read_bytes()
    for marker in (
        identity_marker,
        record_marker,
        evidence_marker,
        "b11-secret-source-id",
    ):
        assert marker.encode("utf-8") in stored
