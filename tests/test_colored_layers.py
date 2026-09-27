"""彩色答案层拆分与答案归题的单测。"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import pytest

from question_bank.importers import mineru_parse
from question_bank.importers.pdf_importer import split_colored_answer_layers

MAGENTA = (236 / 255, 0, 140 / 255)
BLACK = (0, 0, 0)


def _make_teacher_pdf(path: Path, *, colored: bool = True) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((30, 60), "1. if a=3, b=4, then c=", fontsize=11, color=BLACK)
    if colored:
        page.insert_text((160, 60), "5", fontsize=11, color=MAGENTA)
    else:
        page.insert_text((160, 60), "5", fontsize=11, color=BLACK)
    page.insert_text(
        (30, 90), "2. which is right A.1 B.2 C.3 D.4", fontsize=11, color=BLACK
    )
    if colored:
        page.insert_text((215, 90), "(D)", fontsize=11, color=MAGENTA)
    page.insert_text((30, 130), "3. find the hypotenuse of the triangle", fontsize=11, color=BLACK)
    if colored:
        page.insert_text(
            (30, 150),
            "jie: use the pythagorean theorem to compute the side",
            fontsize=11,
            color=MAGENTA,
        )
    page2 = doc.new_page(width=400, height=600)
    page2.insert_text((30, 60), "4. plain black page text only", fontsize=11, color=BLACK)
    doc.save(path)
    doc.close()
    return path


def _spans(pdf: Path) -> list[tuple[int, tuple[int, int, int], str]]:
    out = []
    with fitz.open(pdf) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        color = int(span.get("color", 0))
                        rgb = ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)
                        out.append((rgb, span.get("text", "")))
    return out


def test_split_colored_layers_produces_two_pdfs(tmp_path: Path) -> None:
    pdf = _make_teacher_pdf(tmp_path / "teacher.pdf")
    layers = split_colored_answer_layers(pdf, tmp_path / "layers")
    assert layers is not None
    assert layers.answer_color == (236, 0, 140)

    student_spans = _spans(layers.student_pdf)
    assert student_spans  # 黑字仍在
    assert all(max(rgb) - min(rgb) <= 100 for rgb, _ in student_spans)

    answer_spans = _spans(layers.answers_pdf)
    assert answer_spans  # 红字仍在
    assert all(max(rgb) - min(rgb) > 100 for rgb, _ in answer_spans)
    texts = " ".join(text for _, text in answer_spans)
    assert "(D)" in texts and "5" in texts

    kinds = sorted(region.kind for region in layers.regions)
    assert kinds == ["block", "inline", "inline"]


def test_split_colored_layers_none_for_plain_pdf(tmp_path: Path) -> None:
    pdf = _make_teacher_pdf(tmp_path / "plain.pdf", colored=False)
    assert split_colored_answer_layers(pdf, tmp_path / "layers") is None


# --- 答案块归题与分类（鸭子类型） --------------------------------------------


def test_classify_answer_pieces() -> None:
    assert mineru_parse._classify_answer_pieces(["（D）"]) == ("choice", "D")
    # 短碎片兜底提取唯一 A–D 字母；多字母/单位不误判。
    assert mineru_parse._piece_choice_letter("(C") == "C"
    assert mineru_parse._piece_choice_letter("C") == "C"
    assert mineru_parse._piece_choice_letter("BD") == ""
    assert mineru_parse._piece_choice_letter("13 cm") == ""
    kind, text = mineru_parse._classify_answer_pieces(
        ["解: 由勾股定理", "得 c = 5"]
    )
    assert kind == "solution"
    assert "解" in text and "c = 5" in text
    assert mineru_parse._classify_answer_pieces(["5", "16", "24"]) == (
        "fill",
        "5；16；24",
    )


def test_answer_block_target_uses_page_geometry() -> None:
    # 学生版：page0 有题1(y.1-.2)、题2(y.3-.4)；page1 有题3(y.1-.2)。
    q1 = mineru_parse._Question(1)
    q1.items.append(mineru_parse._Record(0, 0, "text", (0.1, 0.1, 0.9, 0.2), text="1. a"))
    q2 = mineru_parse._Question(2)
    q2.items.append(mineru_parse._Record(0, 1, "text", (0.1, 0.3, 0.9, 0.4), text="2. b"))
    q3 = mineru_parse._Question(3)
    q3.items.append(mineru_parse._Record(1, 0, "text", (0.1, 0.1, 0.9, 0.2), text="3. c"))
    questions = [q1, q2, q3]
    anchors, last_on_page = mineru_parse._question_page_anchors(questions)
    known = {1, 2, 3}

    def target(page, y0, y1):
        record = mineru_parse._Record(page, 99, "text", (0.2, y0, 0.3, y1), text="x")
        return mineru_parse._answer_block_target(
            record, anchors=anchors, last_on_page=last_on_page, known_questions=known
        )

    assert target(0, 0.25, 0.28) == 1  # 题1、题2之间 → 题1
    assert target(0, 0.5, 0.55) == 2   # 题2之下 → 题2
    assert target(0, 0.02, 0.05) is None  # page0 第一题之前且无前页 → 丢弃
    # page1 第一题之上 → 上一页末题（page0 的题2）
    assert target(1, 0.02, 0.05) == 2
    assert target(1, 0.3, 0.35) == 3


def test_inject_inline_blanks_positional() -> None:
    """inline 答案区域的 bbox 决定 " ____" 注入位置；已有空格形态不重复注入。"""

    def _region(x0: float, y0: float = 0.205) -> object:
        return type(
            "R",
            (),
            {
                "page_idx": 0,
                "bbox_norm": (x0, y0, x0 + 0.03, y0 + 0.03),
                "kind": "inline",
            },
        )

    # 同一文本块上的三处空格：MinerU 把占位符整段丢弃的情形。
    item = mineru_parse._Record(
        0,
        0,
        "text",
        (0.05, 0.20, 0.95, 0.24),
        text="(1)若 a=3,b=4 ，则c= (2)若 a=12,c=20 ,则b= (3)若 a=7,c=25 ,则b=",
    )
    q3 = mineru_parse._Question(3)
    q3.items.append(item)
    mineru_parse._inject_inline_blanks([q3], {3: [_region(0.42), _region(0.65), _region(0.88)]})
    assert item.text.count("____") == 3
    positions = [item.text.index("____")]
    assert item.text.index("____") < item.text.rindex("____")

    # MinerU 已读出空格（____）时不注入。
    item2 = mineru_parse._Record(
        0, 1, "text", (0.05, 0.30, 0.95, 0.34), text="则 c= ____"
    )
    q4 = mineru_parse._Question(4)
    q4.items.append(item2)
    mineru_parse._inject_inline_blanks([q4], {4: [_region(0.30, 0.305)]})
    assert item2.text.count("____") == 1

    # MinerU 读出的是乱码公式组（_{ \mathrm{~__~} ... }）时同样不注入。
    item3 = mineru_parse._Record(
        0,
        2,
        "text",
        (0.05, 0.40, 0.95, 0.44),
        text=r"则 $c = _ { \mathrm { ~____~ } \mathrm { ~____~ } }$ 其余",
    )
    q5 = mineru_parse._Question(5)
    q5.items.append(item3)
    mineru_parse._inject_inline_blanks([q5], {5: [_region(0.30, 0.405)]})
    assert item3.text.count(" ____ ") == 0 or item3.text.count("____") == 2


def test_normalize_ocr_blanks() -> None:
    from question_bank.importers import batch_importer

    assert batch_importer._normalize_ocr_blanks(r"则 c = \_\_\_\_") == "则 c = ____"
    assert batch_importer._normalize_ocr_blanks("则 c = ______") == "则 c = ____"
    assert batch_importer._normalize_ocr_blanks(r"$\underline{\quad}$") == "____"
    assert "____" in batch_importer._normalize_ocr_blanks("则 c = ————")
    # 正文普通破折号与公式单下标不受影响。
    assert batch_importer._normalize_ocr_blanks("回答——即答") == "回答——即答"
    assert batch_importer._normalize_ocr_blanks("$x_{1}$") == "$x_{1}$"
    # 学生版 □ 占位串：文本形态与公式内 \Box 形态都归一到 "____"。
    assert batch_importer._normalize_ocr_blanks("则 c = □□□□") == "则 c = ____"
    assert batch_importer._normalize_ocr_blanks("则 c = $____$") == "则 c = ____"
    assert (
        batch_importer._normalize_ocr_blanks(r"则 $b = \Box\Box\Box$")
        == "则 $b =$ ____"
    )


def test_extract_paper_appends_reference_answers(monkeypatch, tmp_path: Path) -> None:
    # 不跑 MinerU：stub 层拆分与双 PDF 解析，验证 Markdown 拼接与题型透传。
    from question_bank.importers import batch_importer
    from question_bank.importers import mineru_parse as mp
    from question_bank.importers import pdf_importer

    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(blank)
    doc.close()

    # 拆分结果会作为 bytes 读取进 layout_out，stub 路径需要真实文件。
    for name in ("s.pdf", "a.pdf"):
        layer_pdf = tmp_path / name
        layer_doc = fitz.open()
        layer_doc.new_page()
        layer_doc.save(layer_pdf)
        layer_doc.close()

    fake_layers = type(
        "L",
        (),
        {
            "student_pdf": tmp_path / "s.pdf",
            "answers_pdf": tmp_path / "a.pdf",
            "regions": [],
        },
    )
    monkeypatch.setattr(
        pdf_importer, "split_colored_answer_layers", lambda *a, **k: fake_layers
    )
    monkeypatch.setattr(
        mp,
        "parse_pdf_full_with_answers",
        lambda *a, **k: ("1. 若a=3则c=\n\n2. 计算", {1: "5", 2: "解: x"}, {1: "填空题", 2: "解答题"}),
    )
    extracted = batch_importer._extract_paper(blank, asset_root=tmp_path / "assets")
    assert "参考答案" in extracted.text
    assert "1. 5" in extracted.text
    assert "2. 解: x" in extracted.text
    assert extracted.type_overrides == {"1": "填空题", "2": "解答题"}
