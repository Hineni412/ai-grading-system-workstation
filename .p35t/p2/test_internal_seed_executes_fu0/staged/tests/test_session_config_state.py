from __future__ import annotations

from db_manager import DBManager
from session_config_state import (
    clear_pending_config_for_new_session,
    remember_saved_config_for_new_session,
    restore_persistent_config_state,
)


def test_saved_config_and_selected_session_survive_new_page_state(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("期中测试", "session-rubric.json", "session-answer.json")

    state: dict[str, object] = {}
    remember_saved_config_for_new_session(
        state,
        selected_session_id=None,
        rubric_path="latest-rubric.json",
        answer_key_path="latest-answer.json",
        source_paper_path="question_bank/raw_papers/latest.docx",
        source_paper_sha256="a" * 64,
        source_paper_name="期中试卷.docx",
        settings_store=db,
    )
    db.set_app_setting("last_selected_session_id", str(session_id))

    restored: dict[str, object] = {}
    restore_persistent_config_state(restored, db)

    assert restored["latest_rubric_path"] == "latest-rubric.json"
    assert restored["latest_answer_path"] == "latest-answer.json"
    assert restored["latest_source_paper_path"] == "question_bank/raw_papers/latest.docx"
    assert restored["latest_source_paper_sha256"] == "a" * 64
    assert restored["latest_source_paper_name"] == "期中试卷.docx"
    assert restored["selected_session_id"] == session_id


def test_existing_installation_uses_latest_active_session_as_persistence_fallback(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    older_id = db.create_grading_session("旧考试", "old-rubric.json", "old-answer.json")
    latest_id = db.create_grading_session("新考试", "new-rubric.json", "new-answer.json")

    restored: dict[str, object] = {}
    restore_persistent_config_state(restored, db)

    assert restored["latest_rubric_path"] == "new-rubric.json"
    assert restored["latest_answer_path"] == "new-answer.json"
    assert restored["selected_session_id"] == latest_id
    assert restored["selected_session_id"] != older_id


def test_clear_pending_source_state_clears_memory_and_persistent_settings(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    state = {
        "generated_config_payload": {"rubric": {}},
        "latest_source_paper_path": "question_bank/raw_papers/latest.docx",
        "latest_source_paper_sha256": "a" * 64,
        "latest_source_paper_name": "期中试卷.docx",
    }
    for setting_key, value in (
        ("latest_confirmed_source_paper_path", state["latest_source_paper_path"]),
        ("latest_confirmed_source_paper_sha256", state["latest_source_paper_sha256"]),
        ("latest_confirmed_source_paper_name", state["latest_source_paper_name"]),
    ):
        db.set_app_setting(setting_key, str(value))

    clear_pending_config_for_new_session(state, settings_store=db)

    assert "generated_config_payload" not in state
    assert "latest_source_paper_path" not in state
    assert "latest_source_paper_sha256" not in state
    assert "latest_source_paper_name" not in state
    assert db.get_app_setting("latest_confirmed_source_paper_path") == ""
    assert db.get_app_setting("latest_confirmed_source_paper_sha256") == ""
    assert db.get_app_setting("latest_confirmed_source_paper_name") == ""
