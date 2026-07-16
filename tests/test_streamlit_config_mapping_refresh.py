from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

import pytest

import web_app
from backend.config_workspace.publish import publish_legacy_config_and_refresh_mapping
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


MappingStatus = Literal["not_present", "refreshed", "reconfirm_required"]


@pytest.mark.parametrize(
    ("service_status", "expected_message"),
    (
        ("not_present", "评分依据已保存，并已同步到当前考试批改。"),
        (
            "refreshed",
            "评分依据已保存，并已同步到当前考试批改；样卷映射表已按新评分标准刷新，请重新确认题框映射。",
        ),
        (
            "reconfirm_required",
            "评分依据已保存，并已同步到当前考试批改；已有样卷映射需要重新确认后才能继续批改。",
        ),
    ),
)
def test_streamlit_mapping_refresh_keeps_three_state_contract_and_truthful_save_message(
    monkeypatch: pytest.MonkeyPatch,
    service_status: MappingStatus,
    expected_message: str,
) -> None:
    def fake_service(*_args: object, **_kwargs: object) -> MappingStatus:
        return service_status

    monkeypatch.setattr(web_app, "_refresh_template_mapping_service", fake_service)

    status = web_app._refresh_template_mapping_from_session(object(), 17)

    assert status == service_status
    assert web_app._template_mapping_save_confirmation(status) == expected_message


def test_streamlit_mapping_refresh_audit_keeps_bool_and_status() -> None:
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")

    assert '"template_mapping_refreshed": mapping_status == "refreshed"' in source
    assert '"template_mapping_status": mapping_status' in source


def test_streamlit_save_uses_partial_success_wrapper() -> None:
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")

    assert "mapping_result = publish_legacy_config_and_refresh_mapping(" in source
    assert "mapping_status = mapping_result.mapping_status" in source


def test_streamlit_saved_config_survives_workflow_state_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_workflow_state(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic workflow-state failure")

    monkeypatch.setattr(web_app, "_write_session_workflow_state", fail_workflow_state)

    saved = web_app._record_config_save_workflow_state(
        object(),
        17,
        rubric_path="rubric.json",
        answer_key_path="answer.json",
        mapping_status="reconfirm_required",
    )

    assert saved is False
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")
    assert "附属工作状态暂未更新，可稍后重建；已保存的评分依据不受影响" in source


def test_streamlit_post_publish_state_failure_reports_saved_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_remember(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic remembered-state failure")

    monkeypatch.setattr(web_app, "remember_saved_config_for_new_session", fail_remember)

    remembered = web_app._remember_published_config_state(
        {},
        selected_session_id=17,
        rubric_path="rubric.json",
        answer_key_path="answer.json",
        source_paper_path="papers/source.docx",
        source_paper_sha256="a" * 64,
        source_paper_name="source.docx",
        settings_store=object(),
    )
    level, message = web_app._post_publish_config_save_notice(
        "refreshed",
        remembered_state_saved=remembered,
        workflow_state_saved=True,
    )

    assert remembered is False
    assert level == "warning"
    assert "评分依据已保存" in message
    assert "样卷映射表已按新评分标准刷新" in message
    assert "附属工作状态暂未更新，可稍后重建" in message


def test_legacy_publish_binds_config_and_source_atomically(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text("{}", encoding="utf-8")
    old_answer.write_text("{}", encoding="utf-8")
    session_id = db.create_grading_session("Exam", str(old_rubric), str(old_answer))

    with db._connect() as connection:
        connection.execute(
            f"""
            CREATE TRIGGER fail_legacy_source_binding
            BEFORE UPDATE OF source_paper_path ON grading_sessions
            WHEN NEW.id = {int(session_id)}
            BEGIN
                SELECT RAISE(ABORT, 'synthetic source failure');
            END
            """
        )

    with pytest.raises(Exception, match="synthetic source failure"):
        publish_legacy_config_and_refresh_mapping(
            db,
            tmp_path / "uploaded",
            session_id=session_id,
            rubric_path=str(tmp_path / "new-rubric.json"),
            answer_key_path=str(tmp_path / "new-answer.json"),
            source_paper_path="papers/new.docx",
            source_paper_sha256="a" * 64,
            mapping_output_dir=tmp_path / "templates",
        )

    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["rubric_path"] == str(old_rubric)
    assert current["answer_key_path"] == str(old_answer)
    assert not current["source_paper_path"]


def test_legacy_mapping_blocks_new_config_job_submission(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text(json.dumps({"questions": []}), encoding="utf-8")
    old_answer.write_text(json.dumps({"questions": []}), encoding="utf-8")
    session_id = db.create_grading_session("Exam", str(old_rubric), str(old_answer))
    upload_root = tmp_path / "uploaded"
    manager = JobManager(
        JobStore(db.db_path),
        max_workers=1,
        interrupted_input_root=upload_root,
    )
    manager.register("config_generation", lambda context: {"session_id": session_id})
    mapping_started = threading.Event()
    release_mapping = threading.Event()
    publish_finished = threading.Event()
    submit_finished = threading.Event()

    def mapping() -> MappingStatus:
        mapping_started.set()
        assert release_mapping.wait(timeout=5)
        return "not_present"

    def publish() -> None:
        publish_legacy_config_and_refresh_mapping(
            db,
            upload_root,
            session_id=session_id,
            rubric_path=str(tmp_path / "new-rubric.json"),
            answer_key_path=str(tmp_path / "new-answer.json"),
            source_paper_path="papers/new.docx",
            source_paper_sha256="b" * 64,
            mapping_output_dir=tmp_path / "templates",
            job_store=manager.store,
            mapping_refresher=mapping,
        )
        publish_finished.set()

    publish_thread = threading.Thread(target=publish, daemon=True)
    publish_thread.start()
    assert mapping_started.wait(timeout=5)

    def submit() -> None:
        manager.submit("config_generation", {"session_id": session_id, "mode": "generate"})
        submit_finished.set()

    submit_thread = threading.Thread(target=submit, daemon=True)
    submit_thread.start()
    assert not submit_finished.wait(timeout=0.2)
    manager.store.assert_config_session_idle(session_id)

    release_mapping.set()
    publish_thread.join(timeout=5)
    submit_thread.join(timeout=5)
    assert publish_finished.is_set()
    assert submit_finished.is_set()
    manager.shutdown()
