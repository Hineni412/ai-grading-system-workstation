from __future__ import annotations

import io
from pathlib import Path
import zipfile

import fitz
import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Inches
from PIL import Image


def _docx_with_duplicate_and_table() -> bytes:
    document = Document()
    document.add_paragraph("Question one")
    document.add_paragraph("Question one")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = "Right"
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _two_page_pdf() -> bytes:
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "Alpha page")
    second = document.new_page()
    second.insert_text((72, 72), "Beta page")
    payload = document.tobytes()
    document.close()
    return payload


def _docx_with_ordered_header_footer_parts() -> bytes:
    payload = io.BytesIO(_docx_with_duplicate_and_table())
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(payload, "a") as archive:
        for name, text in [
            ("word/header2.xml", "HEADER-2"),
            ("word/footer2.xml", "FOOTER-2"),
            ("word/header1.xml", "HEADER-1"),
            ("word/footer1.xml", "FOOTER-1"),
        ]:
            archive.writestr(
                name,
                (
                    f'<w:root xmlns:w="{namespace}"><w:p><w:r>'
                    f"<w:t>{text}</w:t></w:r></w:p></w:root>"
                ),
            )
    return payload.getvalue()


def _docx_with_multiline_proof_answer() -> bytes:
    document = Document()
    document.add_paragraph("1. 证明：若 a=b，则 a+c=b+c。")
    document.add_paragraph("参考答案")
    document.add_paragraph("1. 【答案】结论成立")
    document.add_paragraph("由 a=b，等式两边同时加 c，得到 a+c=b+c。")
    document.add_paragraph("所以原命题得证。")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_text_extractors_preserve_repeated_body_text_and_pdf_pages() -> None:
    from backend.document_parsing import extract_docx_text, extract_pdf_text

    extracted = extract_docx_text(_docx_with_duplicate_and_table())
    assert extracted.count("Question one") == 2
    assert extracted.count("Left") == extracted.count("Right") == 1
    assert extracted.index("Question one") < extracted.index("Left") < extracted.index("Right")
    assert extract_pdf_text(_two_page_pdf()) == "Alpha page\n\nBeta page\n"


def test_docx_page_headers_and_footers_do_not_become_question_content() -> None:
    from backend.document_parsing import extract_docx_text

    extracted = extract_docx_text(_docx_with_ordered_header_footer_parts())

    assert "HEADER" not in extracted
    assert "FOOTER" not in extracted


def test_plain_parser_preserves_inline_split_and_answer_mapping() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "一、选择题",
                "10. Tenth question.",
                "【答案】A",
                "Continuation. 11. Eleventh question.",
                "【答案】B",
                "12. Twelfth question.",
                "【答案】C",
            ]
        )
    )

    assert [block["question_id"] for block in blocks] == ["Q10", "Q11", "Q12"]
    assert blocks[0]["canonical_answer"] == ""
    assert blocks[1]["canonical_answer"] == "B"
    assert blocks[2]["canonical_answer"] == "C"


def test_plain_parser_trusts_an_explicitly_mapped_bare_single_blank_answer() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "二、填空题",
                "5. 求这个角的度数：____。",
                "参考答案",
                "5. 70°",
            ]
        )
    )

    assert len(blocks) == 1
    assert blocks[0]["question_id"] == "Q5"
    assert blocks[0]["canonical_answer"] == "70"
    assert blocks[0]["local_answer_trusted"] is True
    assert blocks[0]["needs_review"] is False


def test_plain_parser_does_not_flatten_subparted_worked_question_into_fill_blank() -> None:
    from backend.document_parsing import parse_plain_question_blocks

    blocks = parse_plain_question_blocks(
        "\n".join(
            [
                "三、解答题",
                "11. 如图，在△ABC中，∠ACB＝90°，点D在斜边AB上，AD＝AC，"
                "设∠A＝x°，∠BCD＝y°。",
                "（1）填写表格：x 取 20、40、60、80 时，y 分别为 ____。",
                "（2）猜想y与x的数量关系，并说明理由。",
                "（3）在图1的条件下，点E在AB边上，且BE＝BC，求∠DCE的度数。",
            ]
        )
    )

    assert len(blocks) == 1
    assert blocks[0]["question_type"] != "fill_blank"
    assert blocks[0]["question_type"] in {"calculation", "proof", "comprehensive"}


def test_broken_rich_docx_falls_back_without_model_or_files(tmp_path: Path) -> None:
    from backend.document_parsing import parse_docx_question_blocks

    blocks = parse_docx_question_blocks(
        b"not-a-docx",
        fallback_doc_text="1. Local fallback question.",
        temporary_root=tmp_path / "parser",
        asset_root=tmp_path / "assets",
    )

    assert [block["question_id"] for block in blocks] == ["Q1"]
    assert blocks[0]["question_text"] == "Local fallback question."
    assert not (tmp_path / "parser").exists()
    assert not (tmp_path / "assets").exists()


def test_docx_parser_keeps_unheaded_proof_steps_after_short_answer(
    tmp_path: Path,
) -> None:
    from backend.document_parsing import parse_docx_question_blocks

    blocks = parse_docx_question_blocks(
        _docx_with_multiline_proof_answer(),
        temporary_root=tmp_path / "parser",
        asset_root=tmp_path / "assets",
    )

    assert len(blocks) == 1
    complete_answer = "\n".join(
        [
            str(blocks[0].get("answer_text") or ""),
            str(blocks[0].get("analysis") or ""),
        ]
    )
    assert "由 a=b，等式两边同时加 c" in complete_answer
    assert "所以原命题得证" in complete_answer


def test_legacy_imports_delegate_to_the_document_parser() -> None:
    import rubric_auto_cropper
    import session_manager
    from backend.document_parsing import (
        extract_docx_text,
        extract_pdf_text,
        parse_plain_question_blocks,
    )

    assert session_manager.extract_docx_text is extract_docx_text
    assert (
        session_manager.preview_question_blocks_from_docx_text
        is parse_plain_question_blocks
    )
    assert rubric_auto_cropper.extract_pdf_text is extract_pdf_text


def test_local_type_keeps_choice_ahead_of_blank_and_does_not_guess_calculation_or_proof() -> None:
    from backend.document_parsing.question_blocks import _infer_local_question_type

    choice = _infer_local_question_type(
        "一块三角形玻璃被分成四块，应选(______)\nA．第1块 B．第2块 C．第3块 D．第4块",
        "B",
        "2",
    )
    fill_fullwidth = _infer_local_question_type(
        "若 x^2 - ax + 25 是完全平方式，则 a = ＿＿＿＿．",
        "±10",
        "6",
    )
    fill_required = _infer_local_question_type(
        "按要求填空：若 a + b = 3，则 a^2 + b^2 = ＿＿＿＿．",
        "5",
        "7",
    )
    application = _infer_local_question_type(
        "根据以下信息，探索完成任务：如何选择合适的通话套餐。"
        "请说明哪一种更合适。\n任务一 任务二 任务三",
        "任务一：88，93.7",
        "14",
    )
    application_table = _infer_local_question_type(
        "<p>根据以下信息，探索完成任务：如何选择合适的话费套餐</p>"
        "<table><tr><td>素材1</td><td>A套餐 50元</td><td>&nbsp;&nbsp;</td></tr>"
        "<tr><td>任务一</td><td>____</td></tr>"
        "<tr><td>任务二</td><td><u>    </u></td></tr>"
        "<tr><td>任务三</td><td>150</td></tr></table>",
        "任务一：88，93.7",
        "14",
    )
    closing_fill = _infer_local_question_type(
        "如图，三角形ABC中，D、E、F为边上的点，求AC=__________.",
        "12",
        "15",
    )
    html_underline_fill = _infer_local_question_type(
        "若 $x^2 - mx + 25$ 是完全平方式，则 $m = <u>          </u>.$",
        "±10",
        "6",
    )
    explicit_proof = _infer_local_question_type(
        "求证：等腰三角形两底角相等。",
        "",
        "16",
    )

    assert choice == "choice"
    assert fill_fullwidth == "fill_blank"
    assert fill_required == "fill_blank"
    assert application == "comprehensive"
    assert application_table == "comprehensive"
    assert closing_fill == "fill_blank"
    assert html_underline_fill == "fill_blank"
    assert explicit_proof == "proof"


def test_production_source_uses_parser_module_and_parser_has_no_model_dependency() -> None:
    source = Path("backend/config_workspace/sources.py").read_text(encoding="utf-8")
    package_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("backend/document_parsing").glob("*.py"))
    )

    assert "backend.document_parsing" in source
    assert "import session_manager" not in source
    assert "from session_manager import preview_question_blocks_from_docx_text" not in source
    assert "llm_client" not in package_sources
    assert "backend.llm" not in package_sources
    assert "LLMClient" not in package_sources
    assert "session_manager" not in package_sources


def test_floating_image_between_adjacent_questions_becomes_manual_candidate() -> None:
    from question_bank.importers.batch_importer import (
        partition_ambiguous_floating_images,
    )

    paragraphs = [
        {"text": "4. 第一题"},
        {
            "text": "[[IMAGE:C:/controlled/floating.png]]",
            "xml": "<w:p><wp:anchor /></w:p>",
        },
        {"text": "5. 第二题"},
        {"text": "[[IMAGE:C:/controlled/inline.png]]", "xml": "<w:p />"},
    ]

    retained, candidates = partition_ambiguous_floating_images(paragraphs)

    assert [item["text"] for item in retained] == [
        "4. 第一题",
        "5. 第二题",
        "[[IMAGE:C:/controlled/inline.png]]",
    ]
    assert candidates == [{
        "path": "C:/controlled/floating.png",
        "previous_question_id": "Q4",
        "next_question_id": "Q5",
        "source_section": "question",
    }]


def _save_docx_bytes(document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _number_paragraph(paragraph, num_id: int, *, start: int | None = None, label: str = '%1.'):
    paragraph._element.get_or_add_pPr().append(parse_xml(
        f'<w:numPr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>'
    ))
    if start is not None:
        root = paragraph.part.numbering_part.element
        root.append(parse_xml(
            f'<w:abstractNum xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:abstractNumId="{num_id}">'
            f'<w:lvl w:ilvl="0"><w:start w:val="{start}"/><w:numFmt w:val="decimal"/><w:lvlText w:val="{label}"/></w:lvl></w:abstractNum>'
        ))
        root.append(parse_xml(
            f'<w:num xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:numId="{num_id}">'
            f'<w:abstractNumId w:val="{num_id}"/></w:num>'
        ))


def _parse_synthetic_docx(document, tmp_path):
    from backend.document_parsing import parse_docx_question_blocks
    from backend.config_workspace.sources import _strip_embedded_question_section_heading, _align_question_type_with_visible_blank
    blocks = parse_docx_question_blocks(_save_docx_bytes(document), temporary_root=tmp_path, asset_root=tmp_path / 'assets')
    for block in blocks:
        _strip_embedded_question_section_heading(block)
        _align_question_type_with_visible_blank(block)
    return {block['question_id']: block for block in blocks}


def _synthetic_picture(paragraph, color='navy'):
    data = io.BytesIO()
    Image.new('RGB', (80, 40), color).save(data, format='PNG')
    return paragraph.add_run().add_picture(io.BytesIO(data.getvalue()), width=Inches(1))


def test_disabled_word_lists_do_not_number_options_headings_or_answers(tmp_path):
    document = Document()
    for line in [
        '测试试卷', '一、选择题', '1. 选择正确数值（   ）', 'A. 1 B. 2 C. 3 D. 4',
        '二、填空题', '2. 比较大小：√(3) ____ √(2)', '三、计算题', '3. 计算以下各式。',
        '(1) √(8) + √(2)； (2) √(18) - √(2)。', '答案和解析', '1.【答案】B',
        '【解析】正确选项为 B。', '2.【答案】>', '【解析】因为 3 > 2。',
        '3.【答案】【小题1】', '解：√(8) + √(2) = 3√(2)。', '【小题2】', '解：√(18) - √(2) = 2√(2)。',
    ]:
        _number_paragraph(document.add_paragraph(line), 0)
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert list(blocks) == ['Q1', 'Q2', 'Q3']
    assert [b['question_type'] for b in blocks.values()] == ['choice', 'fill_blank', 'comprehensive']
    assert blocks['Q1']['canonical_answer'] == 'B'
    assert 'A. 1 B. 2 C. 3 D. 4' in blocks['Q1']['question_text']
    assert blocks['Q2']['answer_text'] == '>'
    assert '【小题1】' in blocks['Q3']['analysis'] and '【小题2】' in blocks['Q3']['analysis']
    assert not blocks['Q3']['answer_text'].startswith('【小题')
    assert all(not b.get('parse_warnings') for b in blocks.values())


def test_actual_word_subquestion_start_and_style_are_preserved(tmp_path):
    document = Document()
    document.add_paragraph('1. 选择正确答案。 A.甲 B.乙 C.丙 D.丁')
    document.add_paragraph('二、计算题')
    document.add_paragraph('12. 计算以下各式。')
    _number_paragraph(document.add_paragraph('√(50) - √(2)；'), 81, start=4, label='(%1)')
    _number_paragraph(document.add_paragraph('√(72) - √(8)。'), 82, start=8, label='(%1)')
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert list(blocks) == ['Q1', 'Q12']
    assert blocks['Q1']['question_type'] == 'choice'
    assert '(4)' in blocks['Q12']['question_text'] and '(8)' in blocks['Q12']['question_text']
    assert '1. √' not in blocks['Q12']['question_text']


def test_merged_questions_keep_current_numbers_and_report_old_answers(tmp_path):
    document = Document()
    for line in ['12. 计算：(1) 2 + 3；(2) 4 + 5。', '17. 求值：x + 1，其中 x=2。',
                 '答案和解析', '12.【答案】【小题1】', '2 + 3 = 5。', '13.【答案】【小题1】', '4 + 5 = 9。', '17.【答案】3']:
        document.add_paragraph(line)
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert list(blocks) == ['Q12', 'Q17']
    assert any('2 个小问' in w and '1 个小问' in w for w in blocks['Q12']['parse_warnings'])
    assert any('第 13 题' in w for w in blocks['Q12']['parse_warnings'])
    assert blocks['Q12']['local_answer_trusted'] is False
    assert '4 + 5 = 9' not in blocks['Q17']['analysis']


def test_root_arguments_and_comparisons_do_not_become_subquestions_or_html():
    from backend.document_parsing.question_blocks import rich_text_for_model, has_visible_subparts
    assert not has_visible_subparts('比较 √(3)、√(2) 和 ∛(5)')
    assert has_visible_subparts('(1) 求 √(3)；(2) 求 √(2)')
    assert rich_text_for_model('a < b\n下一题：x > y') == 'a < b\n下一题：x > y'
    assert rich_text_for_model('<sup> </sup>√(5) + x<sup>2</sup>') == '√(5) + x^(2)'


def test_word_underline_split_into_underscore_runs_is_one_blank() -> None:
    from backend.document_parsing.question_blocks import (
        _has_multiple_required_answers,
    )
    assert _has_multiple_required_answers('结果为 ____　      　____。') is False
    assert _has_multiple_required_answers('结果为 <u>　      　</u>。') is False
    assert _has_multiple_required_answers(
        '甲为 ____，乙为 ____。'
    ) is True
    assert _has_multiple_required_answers('（1）____ （2）____') is True
    assert _has_multiple_required_answers('结果为 ____。') is False


def test_fill_blank_option_letter_canonical_is_not_trusted() -> None:
    from backend.document_parsing.question_blocks import _is_local_answer_trusted

    kwargs = dict(
        qtype="fill_blank",
        question_text="4的平方根是____。",
        answer_text="B",
        explicitly_mapped=True,
    )
    assert _is_local_answer_trusted(canonical_answer="B", **kwargs) is False
    assert _is_local_answer_trusted(canonical_answer="b.", **kwargs) is False
    assert _is_local_answer_trusted(canonical_answer="±2", **kwargs) is True
    assert _is_local_answer_trusted(canonical_answer="7", **kwargs) is True
    letter_kwargs = dict(
        kwargs,
        question_text="其中____组有可能是青年组．（填‘A”或“B”）",
        answer_text="A",
    )
    assert _is_local_answer_trusted(canonical_answer="A", **letter_kwargs) is True


def test_inline_pictures_stay_on_both_sides_of_a_same_paragraph_question_boundary(tmp_path):
    document = Document()
    document.add_paragraph('1. 第一题的题干。')
    p = document.add_paragraph('接第一题。')
    _synthetic_picture(p, 'red')
    p.add_run(' 2. 第二题的题干。')
    _synthetic_picture(p, 'blue')
    document.add_paragraph('3. 第三题的题干。')
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert list(blocks) == ['Q1', 'Q2', 'Q3']
    for qid, expected in [('Q1', (255, 0, 0)), ('Q2', (0, 0, 255))]:
        assert len(blocks[qid]['image_paths']) == 1
        with Image.open(blocks[qid]['image_paths'][0]) as img:
            assert img.convert('RGB').getpixel((0, 0)) == expected
    assert not blocks['Q3']['image_paths']

    # The subsequent question-bank import must use the same boundaries and
    # retain the pictures reviewed in the exam configuration page.
    from question_bank.importers.batch_importer import _extract_paper, parse_paper_text, map_rich_content_by_number
    source = tmp_path / 'inline-questions.docx'
    source.write_bytes(_save_docx_bytes(document))
    extracted = _extract_paper(source, asset_root=tmp_path / 'bank-assets')
    parsed = parse_paper_text(extracted.text, source_file=str(source), page_range='document')
    assert [q.question_number for q in parsed.questions] == ['1', '2', '3']
    mapped = map_rich_content_by_number(extracted.rich_paragraphs)['question']
    for number, expected in [('1', (255, 0, 0)), ('2', (0, 0, 255))]:
        from backend.document_parsing.question_blocks import image_paths_from_rich_text
        images = image_paths_from_rich_text('\n'.join(p['text'] for p in mapped[number]))
        assert len(images) == 1
        with Image.open(images[0]) as img:
            assert img.convert('RGB').getpixel((0, 0)) == expected
        assert all('第二题' not in str(p.get('xml', '')) for p in mapped['1'])
        assert all('接第一题' not in str(p.get('xml', '')) for p in mapped['2'])


def test_legacy_vml_picture_and_compatibility_fallback_are_not_lost_or_duplicated(tmp_path):
    from copy import deepcopy
    document = Document()
    p = document.add_paragraph('1. 如图，求正方形面积。')
    shape = _synthetic_picture(p, 'green')
    inline = shape._inline
    rid = next(n for n in inline.iter() if n.tag.endswith('}blip')).get(qn('r:embed'))
    drawing = inline.getparent()
    run = drawing.getparent()
    run.remove(drawing)
    vml = parse_xml(f'<w:pict xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><v:shape><v:imagedata r:id="{rid}"/></v:shape></w:pict>')
    run.append(vml)
    second = document.add_paragraph('2. 如图，求长方形面积。')
    alternate = parse_xml('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice Requires="w14"/><mc:Fallback/></mc:AlternateContent>')
    alternate[0].append(deepcopy(drawing))
    alternate[1].append(deepcopy(vml))
    second.add_run()._r.append(alternate)
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert len(blocks['Q1']['image_paths']) == len(blocks['Q2']['image_paths']) == 1
    assert blocks['Q2']['question_html'].count('[[IMAGE:') == 1


def test_shared_image_relationship_with_different_crops_keeps_visible_variants(tmp_path):
    document = Document()
    for qid, crop_right in [(1, '50000'), (2, '0')]:
        p = document.add_paragraph(f'{qid}. 如图，求图形的面积。')
        shape = _synthetic_picture(p)
        fill = next(n for n in shape._inline.iter() if n.tag.endswith('}blipFill'))
        fill.append(parse_xml(f'<a:srcRect xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" r="{crop_right}"/>'))
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert blocks['Q1']['image_paths'] != blocks['Q2']['image_paths']
    with Image.open(blocks['Q1']['image_paths'][0]) as first, Image.open(blocks['Q2']['image_paths'][0]) as second:
        assert first.size == (40, 40)
        assert second.size == (80, 40)


def test_floating_picture_before_a_subpart_stays_with_parent_question():
    from question_bank.importers.batch_importer import partition_ambiguous_floating_images
    paragraphs = [{'text': '4. 如图，完成下列问题。'}, {'text': '[[IMAGE:diagram.png]]', 'xml': '<wp:anchor />'},
                  {'text': '(1) 求边长；'}, {'text': '(2) 求面积。'}, {'text': '5. 下一题。'}]
    kept, candidates = partition_ambiguous_floating_images(paragraphs)
    assert kept == paragraphs
    assert candidates == []


def test_table_preserves_distinct_crops_without_applying_them_twice(tmp_path):
    from question_bank.importers.docx_importer import import_docx
    document = Document()
    document.add_paragraph('1. 比较表中的两幅图。')
    table = document.add_table(rows=1, cols=2)
    for cell, crop_right in zip(table.rows[0].cells, ['50000', '0']):
        shape = _synthetic_picture(cell.paragraphs[0])
        fill = next(n for n in shape._inline.iter() if n.tag.endswith('}blipFill'))
        fill.append(parse_xml(f'<a:srcRect xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" r="{crop_right}"/>'))
    extracted = import_docx(io.BytesIO(_save_docx_bytes(document)), asset_root=tmp_path)
    record = next(p for p in extracted.rich_paragraphs if '<table' in p['text'])
    assert 'srcRect' not in record['xml']
    xml = parse_xml(record['xml'])
    sizes = []
    for image in (n for n in xml.iter() if n.tag.endswith('}blip')):
        with Image.open(record['image_relationships'][image.get(qn('r:embed'))]) as rendered:
            sizes.append(rendered.size)
    assert sizes == [(40, 40), (80, 40)]


def test_picture_in_question_table_retains_table_cells_and_image(tmp_path):
    document = Document()
    document.add_paragraph('1. 请根据表格及配图求阴影面积。')
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '图形'
    table.cell(0, 1).text = '边长'
    _synthetic_picture(table.cell(1, 0).paragraphs[0])
    table.cell(1, 1).text = '5 cm'
    document.add_paragraph('2. 求 3 + 4 的值。')
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert 'table' in blocks['Q1']['question_html'] and '5 cm' in blocks['Q1']['question_text']
    assert len(blocks['Q1']['image_paths']) == 1
    assert not blocks['Q2']['image_paths']


def test_picture_anchored_to_section_heading_is_kept_between_its_actual_neighbours():
    from question_bank.importers.batch_importer import partition_ambiguous_floating_images, map_rich_content_by_number
    paragraphs = [{'text': '9. 计算面积。'}, {'text': '10. 计算边长。'},
                  {'text': '[[IMAGE:geometry.png]]三、解答题', 'xml': '<wp:anchor />'}, {'text': '11. 证明三角形全等。'}]
    retained, candidates = partition_ambiguous_floating_images(paragraphs)
    assert candidates == [{'path': 'geometry.png', 'previous_question_id': 'Q10', 'next_question_id': 'Q11', 'source_section': 'question'}]
    assert list(map_rich_content_by_number(retained)['question']) == ['9', '10', '11']
    automatic = map_rich_content_by_number(paragraphs)['question']
    assert any('[[IMAGE:geometry.png]]' in p['text'] for p in automatic['10'])


def test_publisher_footer_pictures_are_not_imported_as_question_diagrams(tmp_path):
    document = Document()
    p = document.add_paragraph('1. 如图，计算正方形的面积。')
    _synthetic_picture(p, 'red')
    document.add_paragraph('参考答案')
    document.add_paragraph('1. 答案为 16。')
    document.add_paragraph('声明：试题解析著作权属菁优网所有，未经书面同意，不得复制。')
    _synthetic_picture(document.add_paragraph(), 'blue')
    blocks = _parse_synthetic_docx(document, tmp_path)
    assert len(blocks['Q1']['image_paths']) == 1
    assert not blocks['Q1'].get('_ambiguous_assets')
    assert not blocks['Q1'].get('parse_warnings')
    assert '声明' not in blocks['Q1']['analysis']
