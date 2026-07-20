from pathlib import Path


WEB_APP = Path("web_app.py")


def test_pdf_split_preview_uses_unified_question_image_state() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "pending_q_images" in page
    assert "pending_q_stem_images" not in page
    assert "pending_q_ans_images" not in page


def test_pdf_generation_never_falls_back_to_extracted_text() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "PDF 题目裁图失败，将退回纯文本模式" not in page
    assert "_validate_image_semantic_inputs(confirmed_blocks, q_images)" in page
    assert "generate_grading_config_in_batches" in page
    assert "generate_grading_config_from_images" not in page


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

def test_split_generation_ui_retries_only_failed_batches_and_scores_locally() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "pending_confirmed_blocks" in page
    assert "retry_failed_grading_config_batches" in page
    assert "仅重试失败批次" in page
    assert "retry_grading_config_score_allocation" not in page
    assert "总分已由本地程序统一分配" in page


def test_streamlit_batch_draft_is_reloaded_by_document_digest_and_reports_local_repair() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "_config_generation_checkpoint_path(document_bytes)" in page
    assert "_load_matching_config_generation_checkpoint" in page
    assert "已从本机恢复已保存批次，没有重新调用模型" in page
    assert "_local_json_repair_batch_ids" in page
    assert "未产生额外模型请求" in page


def test_whole_visual_mode_is_not_exposed_in_the_streamlit_ui() -> None:
    page = WEB_APP.read_text(encoding="utf-8")

    assert "config_generation_use_text_only" not in page
    assert "use_text_only" not in page
    assert "run_word_config_generation" not in page
    assert "整卷单次请求" not in page
    assert "当前统一使用小批次生成" in page
