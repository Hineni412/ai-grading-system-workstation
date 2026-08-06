from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExportConfig:
    # Page settings
    page_size: str = "A4"
    orientation: str = "portrait"
    margin_top_cm: float = 1.3
    margin_bottom_cm: float = 1.3
    margin_left_cm: float = 1.45
    margin_right_cm: float = 1.45

    # Font settings
    title_font: str = "黑体"
    title_size_pt: float = 16.0
    body_font: str = "宋体"
    body_font_ascii: str = "Times New Roman"
    body_size_pt: float = 10.5
    line_spacing: float = 1.1

    # Numbering and Choices
    numbering_mode: str = "global"  # "global" or "per_section"
    choice_columns: str = "auto"    # "auto", "1", "2", "4"

    # LaTeX Rendering
    latex_dpi: int = 300
    latex_cache_dir: str = "user_data/question_bank/cache/latex"

    # Other settings
    show_page_numbers: bool = True
    show_answer_key: bool = True
    answer_key_position: str = "end"
