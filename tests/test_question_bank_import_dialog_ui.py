from pathlib import Path


def test_question_bank_import_dialog_has_no_manual_path_scan() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert '手动输入路径扫描' not in page
    assert '手动输入文件夹路径' not in page
    assert '扫描该路径下的文件' not in page
    assert 'qb_import_folder' not in page


def test_question_bank_import_dialog_has_close_and_success_prompt() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert 'qb_import_dialog_close' in page
    assert 'qb_import_success_message' in page
    assert '导入完成' in page


def test_question_bank_tagging_runtime_controls_live_only_in_sidebar() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert 'qb_paper_tag_workers_unified' not in page
    assert 'qb_paper_tag_rpm_unified' not in page
    assert 'qb_tagging_workers' not in page
    assert 'qb_tagging_rpm' not in page
    assert 'tagging_max_workers_input' in page
    assert '必须先在左侧配置并保存打标签 API' in page


def test_question_list_has_collapsed_answers_and_quick_pagination() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert 'def _render_pagination_controls' in page
    assert '"首页"' in page
    assert '"尾页"' in page
    assert '查看参考答案与解析' in page
    assert 'expanded=False' in page


def test_question_basket_jump_targets_composition_page() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert 'def _go_to_assembly_composition' in page
    assert 'st.session_state["assembly_page"] = "composition"' in page


def test_question_card_uses_imported_question_type_detector_before_page_execution() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert 'detected_type = detect_question_type(' in page
    assert 'def _detect_question_type' not in page


def test_import_result_warns_about_suspicious_question_counts() -> None:
    page = Path('pages/题库管理.py').read_text(encoding='utf-8')

    assert '缺失题号' in page
    assert '题数异常' in page
