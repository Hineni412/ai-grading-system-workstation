"""mineru_parse._render_structured 的鸭子类型单测：不依赖真实 MinerU 模型。"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from question_bank.importers import mineru_parse


@dataclass
class FakeSpan:
    content: object = ""
    type: str = "text"


@dataclass
class FakeBlock:
    type: str
    bbox: tuple | None = None
    content: object = field(default_factory=list)
    image_base64: str = ""


@dataclass
class FakePage:
    page_idx: int
    blocks: list


class FakeResult:
    def __init__(self, blocks, markdown="FALLBACK"):
        self.pages = [FakePage(0, blocks)]
        self._markdown = markdown

    def markdown(self):
        return self._markdown


def text_block(text, y0, y1, x0=0.1, x1=0.9):
    return FakeBlock("text", (x0, y0, x1, y1), [FakeSpan(text)])


def image_block(x0, y0, x1, y1, captions=(), b64="QUJD"):
    children = [
        FakeBlock("image_body", (x0, y0, x1, y1), "", image_base64=b64)
    ]
    for caption in captions:
        children.append(
            FakeBlock(
                "image_caption",
                (x0, y1, x1, y1 + 0.02),
                [FakeSpan(caption)],
            )
        )
    return FakeBlock("image", (x0, y0, x1, y1), children)


def test_caption_assigns_image_to_named_question_forward_reference() -> None:
    result = FakeResult(
        [
            image_block(0.2, 0.1, 0.4, 0.3, captions=("第8题图",)),
            text_block("7. 第七题题干", 0.32, 0.4),
            text_block("8. 第八题题干", 0.42, 0.5),
            text_block("9. 第九题题干", 0.52, 0.6),
        ]
    )
    markdown = mineru_parse._render_structured(result)
    lines = [line for line in markdown.splitlines() if line.strip()]
    q8 = next(i for i, line in enumerate(lines) if line.startswith("8."))
    q9 = next(i for i, line in enumerate(lines) if line.startswith("9."))
    image_line = next(
        i for i, line in enumerate(lines) if "第8题图" in line and "![" in line
    )
    assert q8 < image_line < q9


def test_geometric_assignment_image_between_questions() -> None:
    result = FakeResult(
        [
            text_block("1. 第一题题干", 0.1, 0.2),
            image_block(0.1, 0.25, 0.3, 0.4),
            text_block("2. 第二题题干", 0.5, 0.6),
        ]
    )
    markdown = mineru_parse._render_structured(result)
    lines = [line for line in markdown.splitlines() if line.strip()]
    assert lines[1].startswith("![")
    assert lines[2].startswith("2.")


def test_footer_caption_rescued_onto_image_above() -> None:
    result = FakeResult(
        [
            text_block("3. 第三题题干", 0.1, 0.2),
            image_block(0.2, 0.3, 0.8, 0.8),
            FakeBlock("footer", (0.4, 0.86, 0.55, 0.89), [FakeSpan("第3题图")]),
            FakeBlock("footer", (0.2, 0.91, 0.7, 0.93), [FakeSpan("某某学校试卷")]),
            FakeBlock("page_number", (0.7, 0.91, 0.8, 0.93), [FakeSpan("第 1 页")]),
        ]
    )
    markdown = mineru_parse._render_structured(result)
    assert "![第3题图](" in markdown
    assert "某某学校试卷" not in markdown
    assert "第 1 页" not in markdown
    # 图注只出现在图片行，不再有独立行
    assert len([l for l in markdown.splitlines() if "第3题图" in l]) == 1


def test_option_image_pairing_emits_lettered_rows() -> None:
    blocks = [text_block("1. 如图所示，其俯视图为", 0.1, 0.2)]
    letters = ("A", "B", "C", "D")
    for i, letter in enumerate(letters):
        x0 = 0.1 + i * 0.2
        blocks.append(image_block(x0, 0.25, x0 + 0.12, 0.4, b64=f"aW1n{i}"))
        blocks.append(text_block(letter, 0.41, 0.44, x0=x0 + 0.04, x1=x0 + 0.06))
    result = FakeResult(blocks)
    markdown = mineru_parse._render_structured(result)
    lines = [line for line in markdown.splitlines() if line.strip()]
    for letter in letters:
        row = next((l for l in lines if l.startswith(f"{letter}.")), None)
        assert row is not None, letter
        assert row.startswith(f"{letter}. ![") and "data:image" in row
    # 单字母文本块已被选项行吸收
    assert not any(line.strip() in letters for line in lines)


def test_image_emitted_after_preceding_text_block() -> None:
    # 题内 T1(0.1-0.2)、图(y0=0.25)、T2(0.3-0.4)：图应插在 T1 与 T2 之间。
    result = FakeResult(
        [
            text_block("5. 题干第一段", 0.1, 0.2),
            image_block(0.2, 0.25, 0.5, 0.45),
            text_block("第二段文本", 0.3, 0.4),
            text_block("6. 下一题", 0.5, 0.6),
        ]
    )
    markdown = mineru_parse._render_structured(result)
    lines = [line for line in markdown.splitlines() if line.strip()]
    assert lines[0].startswith("5.")
    assert lines[1].startswith("![")
    assert lines[2] == "第二段文本"
    assert lines[3].startswith("6.")


def test_table_html_converted_to_pipe_table() -> None:
    html = "<table><tr><td>次数</td><td>50</td></tr><tr><td>频数</td><td>21</td></tr></table>"
    result = FakeResult(
        [
            text_block("4. 某试验统计如下", 0.1, 0.2),
            FakeBlock("table", (0.1, 0.25, 0.9, 0.4), html),
        ]
    )
    markdown = mineru_parse._render_structured(result)
    assert "|次数|50|" in markdown
    assert "|---|---|" in markdown
    assert "|频数|21|" in markdown


def test_structured_failure_falls_back_to_markdown(monkeypatch, tmp_path) -> None:
    fake_mineru = types.ModuleType("mineru")
    fake_config = types.ModuleType("mineru.config")
    fake_config.config = types.SimpleNamespace(
        model=types.SimpleNamespace(base_dir=str(tmp_path))
    )
    fake_mineru.config = fake_config

    result = FakeResult([text_block("1. 题干", 0.1, 0.2)])
    fake_mineru.parse = lambda *args, **kwargs: result

    monkeypatch.setitem(sys.modules, "mineru", fake_mineru)
    monkeypatch.setitem(sys.modules, "mineru.config", fake_config)
    monkeypatch.setattr(mineru_parse, "mineru_full_available", lambda: True)
    monkeypatch.setattr(
        mineru_parse,
        "_render_structured",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        mineru_parse, "MINERU_MODELS_DIR", tmp_path / "models"
    )

    out = mineru_parse.parse_pdf_full(tmp_path / "paper.pdf")
    assert out == "FALLBACK"
