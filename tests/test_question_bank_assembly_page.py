from pathlib import Path

from question_bank.services import assembly_basket_state
from question_bank.services.assembly_basket_state import (
    SectionSpec,
    merge_question_ids,
    normalize_question_ids,
    order_for_basket,
    save_basket_draft,
)


def test_order_for_basket_keeps_basket_items_when_saved_order_is_stale() -> None:
    assert order_for_basket([101, 102], [101]) == [101, 102]


def test_basket_helpers_normalize_and_merge_ids_without_duplicates() -> None:
    assert normalize_question_ids(["7", 7, "bad", 8, None, "8"]) == [7, 8]
    assert merge_question_ids([7, 8], [8, "9", "bad"]) == [7, 8, 9]


def test_legacy_basket_save_preserves_vue_assembly_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(assembly_basket_state, "project_data_root", lambda: tmp_path)
    path = tmp_path / "question_bank" / "assembly_draft.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            '{"basket_ids":[1],"order_ids":[1],"title":"保留标题",'
            '"header_text":"保留页眉","include_answer":false,'
            '"layout_mode":"sections","preview_mode":"student"}'
        ),
        encoding="utf-8",
    )

    save_basket_draft(
        [2, 2, 3],
        [3, 2],
        [SectionSpec(title="手动分节", question_ids=[3])],
    )

    payload = path.read_text(encoding="utf-8")
    assert '"title": "保留标题"' in payload
    assert '"include_answer": false' in payload
    assert '"basket_ids": [\n    2,\n    3\n  ]' in payload
    assert '"手动分节"' in payload


def test_assembly_page_uses_basket_snapshot_for_composition_links() -> None:
    page = Path("pages/组卷.py").read_text(encoding="utf-8")

    assert "def _go_to_composition" in page
    assert "_basket_ids().append" not in page
    assert "qb_ids=" in page
    assert 'st.query_params.pop("qb_ids")' in page


def test_assembly_export_uses_cached_download_instead_of_folder_picker() -> None:
    page = Path("pages/组卷.py").read_text(encoding="utf-8")

    assert "OUTPUT_DIR_KEY" not in page
    assert "选择文件夹" not in page
    assert "导出位置" not in page
    assert "assembly_exports" in page
    assert "qb_last_assembly_export" in page
    assert "download_button" in page
    assert "恢复试题篮" in page


def test_assembly_teacher_reason_html_is_not_indented_markdown_code() -> None:
    page = Path("pages/组卷.py").read_text(encoding="utf-8")

    assert "def _teacher_reason_html" in page
    assert "_teacher_reason_html(reason_text)" in page
    assert 'reason_html = f"""' not in page
