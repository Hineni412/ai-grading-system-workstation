from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成学生目录密码-足够长-001"


def _service(tmp_path: Path) -> tuple[VaultService, str]:
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    initialized = service.initialize(password=PASSWORD, operation_id="directory-vault-init-001")
    return service, str(initialized["session_token"])


def test_directory_reads_identity_and_counts_without_decrypting_record_body(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject = service.support.create_subject(
        token=token,
        operation_id="directory-subject-create-001",
        source_student_id="SYN-001",
        display_name="合成目录学生",
        class_label="合成一班",
    )
    record = service.support.create_record(
        token=token,
        operation_id="directory-record-create-001",
        subject_id=str(subject["subject_id"]),
        record_kind="teacher_observation",
        content="这段支持记录正文不能由目录读取。",
        scene="合成课堂",
        source="教师观察",
        basis=None,
        category="learning",
        observed_at="2026-08-01T08:00:00+00:00",
        review_at="2026-08-10T08:00:00+00:00",
        expires_at="2026-09-01T08:00:00+00:00",
        counterexample=None,
    )
    with closing(service.database.connect()) as connection:
        record_object_id = str(connection.execute(
            "SELECT payload_object_id FROM record_revisions WHERE record_id = ?",
            (record["record_id"],),
        ).fetchone()[0])
    opened: list[str] = []
    original_get = service.repository.get

    def recording_get(*args, **kwargs):
        opened.append(str(kwargs["object_id"]))
        return original_get(*args, **kwargs)

    service.repository.get = recording_get  # type: ignore[method-assign]

    directory = service.student_directory.search(token=token)

    assert directory["total"] == 1
    assert directory["items"][0]["support_record_count"] == 1
    assert "text" not in directory["items"][0]
    assert record_object_id not in opened
