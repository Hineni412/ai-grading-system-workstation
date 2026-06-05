from pathlib import Path


def test_question_bank_preview_has_display_settings_controls() -> None:
    page = Path("pages/题库管理.py").read_text(encoding="utf-8")

    assert "显示设置" in page
    assert "预览密度" in page
    assert "图片缩放" in page
    assert "resolve_preview_density" in page
    assert "image_display_width" in page


def test_question_bank_preview_keeps_rich_text_tag_recovery() -> None:
    page = Path("pages/题库管理.py").read_text(encoding="utf-8")

    assert 'allowed_tags = ["sub", "sup", "u", "table", "tbody", "tr", "td", "th"]' in page
    assert 'escaped = re.sub(r"&lt;br\\s*/?&gt;", "<br>", escaped, flags=re.IGNORECASE)' in page
    assert 'IMAGE_MARKER_PATTERN = re.compile(r"\\[\\[IMAGE:' in page


def test_question_pages_do_not_use_unbounded_question_image_width() -> None:
    bank_page = Path("pages/题库管理.py").read_text(encoding="utf-8")
    assembly_page = Path("pages/组卷.py").read_text(encoding="utf-8")

    assert "st.image(str(valid_paths[0]), use_container_width=True)" not in bank_page
    assert "st.image(str(path), use_container_width=True)" not in bank_page
    assert "st.image(str(valid_paths[0]), use_container_width=True)" not in assembly_page
    assert "st.image(str(path), use_container_width=True)" not in assembly_page
