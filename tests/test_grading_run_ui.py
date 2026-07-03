from pathlib import Path


def test_page_exposes_pause_resume_and_identity_outcomes() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")

    for text in (
        "安全暂停",
        "正在安全暂停",
        "继续批改",
        "已跳过",
        "冲突",
        "实际整卷大图并发",
    ):
        assert text in source, f"缺少页面文案: {text}"

    assert 'event["event"] == "paper_skipped"' in source
    assert 'event["event"] == "paper_conflict"' in source
    assert 'event["event"] == "session_paused"' in source


def test_page_wires_resume_run_id_and_run_controls() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")
    assert "_render_grading_run_controls" in source
    assert "resume_run_id=st.session_state.pop(_grading_resume_key(" in source
