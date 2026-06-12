from pathlib import Path


WEB_APP = Path("web_app.py")


def test_pdf_split_preview_uses_unified_question_image_state() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "pending_q_images" in page
    assert "pending_q_stem_images" not in page
    assert "pending_q_ans_images" not in page


def test_pdf_generation_never_falls_back_to_extracted_text() -> None:
    page = WEB_APP.read_text(encoding="utf-8")
    whole_start = page.index("def run_word_config_generation")
    whole_end = page.index("use_text_only = st.checkbox", whole_start)
    whole_section = page[whole_start:whole_end]

    assert "PDF 题目裁图失败，将退回纯文本模式" not in page
    assert "PDF 整卷视觉单次请求" in page
    assert "extract_pdf_text(word_bytes)" not in whole_section
    assert "extract_pdf_images(word_bytes)" in whole_section
    assert "_validate_image_semantic_inputs(confirmed_blocks, q_images)" in page
    assert "AI 仅接收裁切出的题目/答案高清图和少量规则" in page


def test_pdf_crop_state_is_captured_before_background_generation() -> None:
    page = WEB_APP.read_text(encoding="utf-8")
    start = page.index("def run_confirmed_generation()")
    end = page.index("with session_col:", start)
    section = page[start:end]
    worker_start = section.index("def generate_work(report)")
    worker_end = section.index("payload = _run_with_stage_progress", worker_start)
    worker_section = section[worker_start:worker_end]

    assert 'st.session_state.get("pending_q_images")' in section[:worker_start]
    assert 'st.session_state.get("pending_q_images")' not in worker_section

    retry_start = section.index("def run_failed_question_retry()")
    retry_worker_start = section.index("def retry_work(report)", retry_start)
    retry_worker_end = section.index("payload = _run_with_stage_progress", retry_worker_start)
    assert 'st.session_state.get("pending_q_images")' in section[retry_start:retry_worker_start]
    assert 'st.session_state.get("pending_q_images")' not in section[retry_worker_start:retry_worker_end]

    score_start = section.index("def run_score_allocation_retry()")
    score_worker_start = section.index("def score_work(report)", score_start)
    score_worker_end = section.index("payload = _run_with_stage_progress", score_worker_start)
    assert 'st.session_state.get("pending_q_images")' in section[score_start:score_worker_start]
    assert 'st.session_state.get("pending_q_images")' not in section[score_worker_start:score_worker_end]


def test_split_generation_ui_can_retry_only_failed_questions_and_rescore() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "pending_confirmed_blocks" in page
    assert "retry_failed_grading_config_questions" in page
    assert "仅重试失败题目" in page
    assert "retry_grading_config_score_allocation" in page
    assert "重新整体赋分" in page


def test_pdf_whole_visual_mode_is_enabled_and_has_manual_retry() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "generate_grading_config_from_images" in page
    assert "extract_pdf_images" in page
    assert "PDF 整卷视觉单次请求" in page
    assert "PDF 整卷纯文本模式已禁用" not in page
    assert "whole_config_generation_failed" in page
    assert "重试整卷生成" in page
