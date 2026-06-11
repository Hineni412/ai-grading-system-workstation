from __future__ import annotations

import io
import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from equivalence_engine import merge_equivalent_forms
from llm_client import LLMClient
from question_bank.services.ai_tagging_service import KNOWLEDGE_POINT_OPTIONS
from score_policy import (
    enforce_integer_scores_by_type,
    OBJECTIVE_TYPES,
    _normalize_type,
)


DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS = 600.0


def _config_generation_extra_kwargs() -> dict[str, float]:
    raw_timeout = os.getenv("AI_GRADING_CONFIG_TIMEOUT_SECONDS")
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS
    return {"timeout": max(120.0, timeout)}


def save_uploaded_json(upload_dir: Path, file_name: str, file_bytes: bytes) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_path = upload_dir / file_name

    parsed = json.load(io.BytesIO(file_bytes))
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    return output_path


def extract_docx_text(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    lines: list[str] = []
    seen_lines: set[str] = set()

    for block in _iter_doc_blocks(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text and text not in seen_lines:
                lines.append(text)
                seen_lines.add(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    cell_text = "\n".join(
                        para.text.strip()
                        for para in cell.paragraphs
                        if para.text.strip()
                    )
                    if cell_text:
                        cells.append(cell_text)
                row_text = " | ".join(cells)
                if row_text and row_text not in seen_lines:
                    lines.append(row_text)
                    seen_lines.add(row_text)

    # Some exam files put questions in Word text boxes/shapes. python-docx
    # does not expose those as normal paragraphs, so read raw XML text too.
    for text in _extract_docx_xml_text(file_bytes):
        if text and text not in seen_lines:
            lines.append(text)
            seen_lines.add(text)

    if not lines:
        raise ValueError("Word 鏂囨。鏈В鏋愬埌鏈夋晥鏂囨湰")

    return "\n".join(lines)


def _iter_doc_blocks(doc: Document):
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)


def _extract_docx_xml_text(file_bytes: bytes) -> list[str]:
    """Extract text from XML streams: main doc, headers/footers, footnotes, and text boxes."""
    xml_names = [
        "word/document.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
    ]
    result: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            xml_names.extend(
                name
                for name in archive.namelist()
                if re.match(r"word/(header|footer)\d+\.xml$", name)
            )
            for name in xml_names:
                if name not in archive.namelist():
                    continue
                raw_xml = archive.read(name)
                root = ElementTree.fromstring(raw_xml)
                # ① Extract OMML math formula as readable placeholder before iterating paragraphs
                # OMML uses namespace http://schemas.openxmlformats.org/officeDocument/2006/math
                for omath in root.iter():
                    tag = omath.tag
                    if not (tag.endswith("}oMath") or tag.endswith("}oMathPara")):
                        continue
                    # Collect all literal text runs inside the formula
                    math_chunks: list[str] = []
                    for node in omath.iter():
                        if node.tag.endswith("}t") and node.text:
                            math_chunks.append(node.text)
                        elif node.tag.endswith("}r") and node.text:
                            math_chunks.append(node.text)
                    formula_text = "".join(math_chunks).strip()
                    if formula_text:
                        placeholder = f"[公式: {formula_text}]"
                    else:
                        placeholder = "[数学公式]"
                    if placeholder not in result:
                        result.append(placeholder)

                # ② Extract paragraphs from main body AND from text boxes (txbxContent)
                for paragraph in root.iter():
                    if not paragraph.tag.endswith("}p"):
                        continue
                    chunks: list[str] = []
                    for node in paragraph.iter():
                        if node.tag.endswith("}t") and node.text:
                            chunks.append(node.text)
                        elif node.tag.endswith("}tab"):
                            chunks.append("\t")
                        elif node.tag.endswith("}br"):
                            chunks.append("\n")
                    text = re.sub(r"[ \t]+", " ", "".join(chunks)).strip()
                    if text and text not in result:
                        result.append(text)
    except Exception:
        return []
    return result


def generate_grading_config_from_docx_text(
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, str] = None,
) -> dict[str, Any]:
    if report:
        report(0.18, "旧版整卷生成", "直接把整份 Word 文本发送给模型生成评分标准。")
    prompt = _build_generation_prompt(doc_text)
    payload = llm_client.json_from_text(prompt, model=model_name, extra_kwargs=_config_generation_extra_kwargs())
    try:
        validate_generated_config(payload)
    except Exception:
        _dump_failed_generated_payload(payload)
        raise
    if _needs_objective_repair(payload, doc_text):
        if report:
            report(0.70, "旧版整卷修复", "检测到客观题可能缺失，正在请求模型修复。")
        repair_prompt = _build_objective_repair_prompt(doc_text, payload)
        payload = llm_client.json_from_text(repair_prompt, model=model_name, extra_kwargs=_config_generation_extra_kwargs())
        try:
            validate_generated_config(payload)
        except Exception:
            _dump_failed_generated_payload(payload)
            raise
        if _needs_objective_repair(payload, doc_text):
            raise ValueError("AI 二次修复后仍疑似漏掉选择题/填空题，请检查 Word 提取文本或换用更强模型。")
    return payload


def generate_grading_config_from_docx_text_legacy(
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, str] = None,
) -> dict[str, Any]:
    if report:
        report(0.18, "旧版整卷生成", "直接把整份 Word 文本发送给模型生成评分标准。")
    prompt = _build_generation_prompt(doc_text)
    payload = llm_client.json_from_text(prompt, model=model_name, extra_kwargs=_config_generation_extra_kwargs())
    validate_generated_config(payload)
    if _needs_objective_repair(payload, doc_text):
        if report:
            report(0.70, "旧版整卷修复", "检测到客观题可能缺失，正在请求模型修复。")
        payload = llm_client.json_from_text(
            _build_objective_repair_prompt(doc_text, payload),
            model=model_name,
            extra_kwargs=_config_generation_extra_kwargs(),
        )
        validate_generated_config(payload)
    return payload


def _generate_grading_config_by_question_blocks(
    question_blocks: list[dict[str, str]],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, str] = None,
) -> dict[str, Any]:
    max_workers = _bounded_int(
        os.getenv("AI_GRADING_CONFIG_WORKERS") or os.getenv("AI_GRADING_MAX_WORKERS"),
        8,
        1,
        1000,
    )
    worker_count = min(max_workers, len(question_blocks))
    results: list[dict[str, Any] | None] = [None] * len(question_blocks)
    failures: list[str] = []
    
    if report:
        qids = ", ".join(str(block.get("question_id") or "") for block in question_blocks if block.get("question_id"))
        report(0.18, "Split paper", f"Parsed {len(question_blocks)} questions; workers={worker_count}; qids: {qids}")
        

    import base64
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="config-q") as executor:
        future_map = {}
        for index, block in enumerate(question_blocks):
            qid = str(block.get("question_id"))
            prompt = _build_single_question_generation_prompt(block, doc_text)
            
            # If we have an image for this QID, send the image to LLM instead of just text!
            if q_images and qid in q_images:
                try:
                    img_data = q_images[qid]
                    img_list = []
                    if isinstance(img_data, dict):
                        if img_data.get("question"):
                            img_list.append(base64.b64decode(img_data["question"]))
                        if img_data.get("answer"):
                            img_list.append(base64.b64decode(img_data["answer"]))
                    else:
                        img_list.append(base64.b64decode(img_data))
                    
                    if img_list:
                        future = executor.submit(llm_client.json_from_images, prompt, img_list, model=model_name)
                    else:
                        future = executor.submit(llm_client.json_from_text, prompt, model=model_name)
                except Exception:
                    # fallback to text if base64 decoding fails
                    future = executor.submit(llm_client.json_from_text, prompt, model=model_name)
            else:
                future = executor.submit(llm_client.json_from_text, prompt, model=model_name)
            
            future_map[future] = index
        if report:
            qids = ", ".join(str(block.get("question_id") or "") for block in question_blocks if block.get("question_id"))
            report(0.20, "Submit per-question requests", f"Submitted {len(future_map)} question requests; waiting for model: {qids}")
        completed = 0
        total = len(question_blocks)
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                qid = str(question_blocks[index].get("question_id") or f"Q{index + 1}")
                failures.append(f"{qid}: {exc}")
            completed += 1
            if report:
                progress = 0.18 + 0.7 * (completed / total)
                report(progress, "Parse question", f"Question {index + 1} parsed (progress: {completed}/{total})")

    if report:
        report(0.90, "Assemble rubric", "All questions parsed; assembling final scoring rubric...")

    merged = _merge_single_question_payloads(results, question_blocks)
    _attach_parallel_generation_meta(merged, question_blocks, results, failures, worker_count)
    _ensure_question_blocks_covered(merged, question_blocks)
    _apply_local_question_facts(merged, question_blocks)
    normalize_generated_config_schema(merged)

    # ① Phase 2: dedicated score-allocation AI call
    # All per-question prompts set score placeholders (=1); this step assigns real scores.
    _assign_scores_to_merged_config(merged, doc_text, llm_client, model_name, report=report)

    # Post-scoring cleanup: re-apply local facts (they survive score allocation)
    _ensure_question_blocks_covered(merged, question_blocks)
    _apply_local_question_facts(merged, question_blocks)
    normalize_generated_config_schema(merged)
    merged.setdefault("meta", {})["score_allocation_mode"] = "dedicated_ai_scoring"
    force_payload_total_score(merged, target_total=100.0)
    return merged


def preview_question_blocks_from_docx_text(doc_text: str) -> list[dict[str, Any]]:
    """本地拆题预览，不调用任何 AI。

    供 UI 在发送 AI 之前展示拆题结果，让人工确认每道题拆分是否正确、答案是否靠谱、
    题型判断是否准确（可在预览中删题或切换题型）。直接复用本地拆题逻辑。
    """
    return _extract_local_question_blocks(doc_text)


_INLINE_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")


def preview_question_blocks_from_docx_bytes(
    file_bytes: bytes,
    *,
    fallback_doc_text: str = "",
) -> list[dict[str, Any]]:
    """富文本拆题预览（复用题库 import_docx），保留公式 HTML 与图片，不调用 AI。

    解析失败时回退到纯文本拆题（preview_question_blocks_from_docx_text）。
    """
    try:
        rich_blocks = _extract_rich_question_blocks(file_bytes)
    except Exception:
        rich_blocks = None
    if rich_blocks:
        return rich_blocks
    return preview_question_blocks_from_docx_text(fallback_doc_text or "")


def _extract_rich_question_blocks(file_bytes: bytes) -> list[dict[str, Any]] | None:
    """用题库 import_docx + map_rich_content_by_number 提取每题富文本块。"""
    from question_bank.importers.docx_importer import import_docx
    from question_bank.importers.batch_importer import map_rich_content_by_number

    tmp_dir = Path(_resolve_upload_config_dir())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    tmp_path = tmp_dir / f"_rich_split_{ts}.docx"
    tmp_path.write_bytes(file_bytes)
    try:
        extracted = import_docx(tmp_path)
        content = map_rich_content_by_number(
            extracted.rich_paragraphs, source_file=str(tmp_path)
        )
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    question_map = content.get("question") if isinstance(content, dict) else {}
    answer_map = content.get("answer") if isinstance(content, dict) else {}
    if not isinstance(question_map, dict) or not question_map:
        return None

    # 题号按数值升序
    def _num_key(k: str) -> int:
        try:
            return int(str(k).strip())
        except (TypeError, ValueError):
            return 999

    choice_answers = _extract_choice_answer_sequence(
        _rich_blocks_plain_text(_flatten_answer_blocks(answer_map))
    )

    blocks: list[dict[str, Any]] = []
    for num_key in sorted(question_map.keys(), key=_num_key):
        number = _num_key(num_key)
        if not (1 <= number <= 99):
            continue
        q_blocks = question_map.get(num_key) or []
        a_blocks = answer_map.get(num_key) if isinstance(answer_map, dict) else None
        block = _parse_rich_question_blocks(number, q_blocks, a_blocks, choice_answers)
        if block:
            blocks.append(block)
    return blocks or None


def _flatten_answer_blocks(answer_map: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(answer_map, dict):
        for _k, blocks in answer_map.items():
            if isinstance(blocks, list):
                out.extend(b for b in blocks if isinstance(b, dict))
    return out


def _rich_block_text(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return str(block or "")


def _rich_blocks_plain_text(blocks: list[Any]) -> str:
    parts = [_rich_block_text(b) for b in blocks]
    return _strip_inline_html(_INLINE_IMAGE_MARKER.sub("", "\n".join(parts)))


def _strip_inline_html(value: str) -> str:
    """去掉公式 HTML 标签，得到可用于答案/题型推断的纯文本。"""
    text = re.sub(r"</?(?:sub|sup|u|table|tbody|tr|td|th|br)\b[^>]*>", "", str(value or ""), flags=re.IGNORECASE)
    return text


def _image_paths_from_rich_text(value: str) -> list[str]:
    seen: list[str] = []
    for m in _INLINE_IMAGE_MARKER.finditer(str(value or "")):
        p = m.group("path").strip()
        if p and p not in seen:
            seen.append(p)
    return seen


def _parse_answer_section_blocks(answer_blocks: list[Any]) -> tuple[str, str]:
    """从卷末"参考答案"块中提取（最终答案, 解答过程）。

    答案块通常重列了题干/选项，需跳过；只保留 【分析】/【解答】 后、【点评】 前的解答内容，
    并优先抽取"故选X""故答案为：X"作为最终答案。
    """
    in_solution = False
    solution_lines: list[str] = []
    final_answer = ""
    for blk in answer_blocks:
        text = _rich_block_text(blk)
        for raw in (text.splitlines() if "\n" in text else [text]):
            line = str(raw or "").strip()
            if not line:
                continue
            if "【点评】" in line or "【点睛】" in line:
                in_solution = False
                continue
            if "【解答】" in line or "【分析】" in line or "【解析】" in line:
                in_solution = True
                marker = next(m for m in ("【解答】", "【分析】", "【解析】") if m in line)
                after = line.split(marker, 1)[1].strip()
                if after:
                    solution_lines.append(after)
                continue
            if line.startswith("【") and "】" in line:
                # 其他标记（如【答案】）后内容并入解答
                in_solution = True
                after = line.split("】", 1)[1].strip()
                if after:
                    solution_lines.append(after)
                continue
            if in_solution:
                solution_lines.append(line)

    solution_text = "\n".join(solution_lines).strip()
    # 抽取最终答案：故选X / 故答案为：X
    plain = _strip_inline_html(_INLINE_IMAGE_MARKER.sub("", solution_text))
    m = re.search(r"故\s*选\s*[:：]?\s*([A-Da-d]+)", plain)
    if m:
        final_answer = m.group(1).upper()
    else:
        m = re.search(r"故\s*答\s*案\s*为\s*[:：]?\s*([^。\n．]+)", plain)
        if m:
            final_answer = m.group(1).strip().rstrip("．.")
    return final_answer, solution_text


def _parse_rich_question_blocks(
    number: int,
    question_blocks: list[Any],
    answer_blocks: list[Any] | None,
    choice_answers: dict[str, str],
) -> dict[str, Any] | None:
    """把单题的富文本块（题干区，含内联【答案】【解析】）拆成 题干/答案/解析 富文本。"""
    # 题干区逐行送入分桶（沿用纯文本分桶逻辑），保留 HTML 公式与 [[IMAGE:]] 标记
    lines: list[str] = []
    for blk in question_blocks:
        text = _rich_block_text(blk)
        lines.extend(text.splitlines() if "\n" in text else [text])

    bucket = "stem"
    stem_lines: list[str] = []
    answer_lines: list[str] = []
    analysis_lines: list[str] = []
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if "【答案】" in line:
            bucket = "answer"
            after = line.split("【答案】", 1)[1].strip()
            if after:
                answer_lines.append(after)
            continue
        if ("【解析】" in line) or ("【点睛】" in line):
            bucket = "analysis"
            marker = "【解析】" if "【解析】" in line else "【点睛】"
            after = line.split(marker, 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if line.startswith("【") and "】" in line:
            bucket = "analysis"
            after = line.split("】", 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if bucket == "stem":
            stem_lines.append(line)
        elif bucket == "answer":
            answer_lines.append(line)
        else:
            analysis_lines.append(line)

    # 若题干区没有内联答案，但存在独立答案块（卷末"参考答案"区），从中解析。
    # 答案块结构通常为：[重列题干/选项] 【分析】… 【解答】…故选C/故答案为：X 【点评】…
    # 需跳过重列的题干/选项，只取标记后的解答与最终答案。
    if answer_blocks and not answer_lines:
        ans_final, ana_text = _parse_answer_section_blocks(answer_blocks)
        if ans_final:
            answer_lines.append(ans_final)
        if ana_text and not analysis_lines:
            analysis_lines.append(ana_text)

    question_html = _strip_leading_question_number(number, "\n".join(stem_lines).strip())
    answer_html = "\n".join(answer_lines).strip()
    analysis_html = "\n".join(analysis_lines).strip()

    image_paths = _image_paths_from_rich_text(question_html)

    question_text = _strip_inline_html(_INLINE_IMAGE_MARKER.sub("", question_html)).strip()
    answer_text = _strip_inline_html(_INLINE_IMAGE_MARKER.sub("", answer_html)).strip()

    if not question_text and not answer_text and not analysis_html and not image_paths:
        return None

    num_str = str(number)
    qtype = _infer_local_question_type(question_text, answer_text, num_str)
    canonical = _extract_canonical_answer_for_local_question(
        number=num_str,
        qtype=qtype,
        question_text=question_text,
        answer_text=answer_text,
        choice_answers=choice_answers,
    )
    accepted = _local_accepted_forms(canonical, qtype)
    has_answer = bool(answer_text or canonical)
    return {
        "question_id": f"Q{number}",
        "text": question_text,
        "question_text": question_text,
        "question_html": question_html,
        "answer_text": answer_text,
        "answer_html": answer_html,
        "analysis": _strip_inline_html(analysis_html).strip(),
        "analysis_html": analysis_html,
        "image_paths": image_paths,
        "question_type": qtype,
        "canonical_answer": canonical,
        "accepted_forms": accepted,
        "local_answer_trusted": has_answer,
        "needs_review": (not bool(canonical)) if qtype in {"choice", "fill_blank"} else False,
    }


def _resolve_upload_config_dir() -> str:
    try:
        from path_manager import PathManager  # type: ignore

        return str(PathManager().upload_config_dir)
    except Exception:
        return str(Path.cwd() / "user_data" / "config" / "uploaded")


def generate_grading_config_from_confirmed_blocks(
    confirmed_blocks: list[dict[str, Any]],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, str] = None,
) -> dict[str, Any]:
    """用人工确认后的题块直接进入单题并发生成 + 统一赋分管线，跳过重新拆题。

    confirmed_blocks 应来自 preview_question_blocks_from_docx_text 的输出，
    经人工删除/题型修正后传入。
    """
    return _generate_grading_config_by_question_blocks(
        confirmed_blocks,
        doc_text,
        llm_client,
        model_name=model_name,
        report=report,
        q_images=q_images,
    )


def _parse_inline_answer_blocks(doc_text: str) -> list[dict[str, Any]] | None:
    """解析"题干 + 【答案】 + 【解析】逐题穿插"格式的试卷（纯本地，不调 AI）。

    返回 None 表示该文本不是内联格式（无【答案】/【解析】标记），交回上层兜底逻辑。
    通过"题号必须单调递增"截断文件后半段重复的答案详解区，避免重复拆题。
    """
    text = str(doc_text or "")
    if ("【答案】" not in text) and ("【解析】" not in text):
        return None

    lines = text.splitlines()
    # 1. 找题号边界：行首 数字. / 数字．，题号单调递增；回退即停止
    markers: list[tuple[int, int]] = []  # (line_index, number)
    last_number = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r"^(\d{1,2})\s*[.．]", stripped)
        if not m:
            continue
        number = int(m.group(1))
        if not (1 <= number <= 99):
            continue
        if markers and number <= last_number:
            break  # 题号回退 → 进入答案详解重复区，停止切题
        markers.append((idx, number))
        last_number = number

    if not markers:
        return None

    answer_section_for_choice = _local_answer_section_text(text)
    choice_answers = _extract_choice_answer_sequence(answer_section_for_choice)

    blocks: list[dict[str, Any]] = []
    for pos, (start, number) in enumerate(markers):
        end = markers[pos + 1][0] if pos + 1 < len(markers) else len(lines)
        segment_lines = lines[start:end]
        parsed = _parse_inline_segment(number, segment_lines, choice_answers)
        if parsed:
            blocks.append(parsed)

    return blocks or None


def _parse_inline_segment(
    number: int,
    segment_lines: list[str],
    choice_answers: dict[str, str],
) -> dict[str, Any] | None:
    """把单道题的文本段拆成 题干 / 答案 / 解析。"""
    # 分类收集：题干 / 【答案】 / 【解析】(含【点睛】)
    bucket = "stem"  # stem | answer | analysis
    stem_lines: list[str] = []
    answer_lines: list[str] = []
    analysis_lines: list[str] = []

    for raw in segment_lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if line.startswith("[公式:") or line.startswith("[公式："):
            continue  # 忽略公式占位行
        if "【答案】" in line:
            bucket = "answer"
            after = line.split("【答案】", 1)[1].strip()
            if after:
                answer_lines.append(after)
            continue
        if ("【解析】" in line) or ("【点睛】" in line):
            bucket = "analysis"
            marker = "【解析】" if "【解析】" in line else "【点睛】"
            after = line.split(marker, 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if line.startswith("【") and "】" in line:
            # 其他【…】标记（如【难度】），归入解析区尾部，避免污染题干
            bucket = "analysis"
            after = line.split("】", 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if bucket == "stem":
            stem_lines.append(line)
        elif bucket == "answer":
            answer_lines.append(line)
        else:
            analysis_lines.append(line)

    question_text = _strip_leading_question_number(number, "\n".join(stem_lines).strip())
    answer_raw = "\n".join(answer_lines).strip()
    analysis_raw = "\n".join(analysis_lines).strip()

    if not question_text and not answer_raw and not analysis_raw:
        return None

    num_str = str(number)
    qtype = _infer_local_question_type(question_text, answer_raw, num_str)
    canonical = _extract_canonical_answer_for_local_question(
        number=num_str,
        qtype=qtype,
        question_text=question_text,
        answer_text=answer_raw,
        choice_answers=choice_answers,
    )
    accepted = _local_accepted_forms(canonical, qtype)
    has_answer = bool(answer_raw or canonical)
    return {
        "question_id": f"Q{number}",
        "text": question_text,
        "question_text": question_text,
        "answer_text": answer_raw,
        "analysis": analysis_raw,
        "question_type": qtype,
        "canonical_answer": canonical,
        "accepted_forms": accepted,
        "local_answer_trusted": has_answer,
        "needs_review": (not bool(canonical)) if qtype in {"choice", "fill_blank"} else False,
    }


def _strip_leading_question_number(number: int, text: str) -> str:
    """去掉题干开头的"N." / "N．" / "N.(本小题X分)"等题号前缀。"""
    value = str(text or "").lstrip()
    value = re.sub(rf"^{number}\s*[.．]\s*", "", value, count=1)
    return value.strip()


def _extract_local_question_blocks(doc_text: str) -> list[dict[str, Any]]:
    inline_blocks = _parse_inline_answer_blocks(doc_text)
    if inline_blocks:
        return inline_blocks

    parsed_questions: list[Any] = []
    try:
        from question_bank.importers.batch_importer import parse_paper_text

        parsed = parse_paper_text(str(doc_text or ""), source_file="grading_config.docx", page_range="document")
        parsed_questions = list(parsed.questions)
    except Exception:
        parsed_questions = []

    # 第二道防线：题号必须单调递增；一旦回退（如卷末答案详解区从1重列）即截断，避免重复拆题。
    if parsed_questions:
        truncated: list[Any] = []
        last_number = 0
        for item in parsed_questions:
            number_str = str(getattr(item, "question_number", "") or "").strip()
            if not number_str.isdigit():
                truncated.append(item)
                continue
            number = int(number_str)
            if truncated and number <= last_number:
                break
            truncated.append(item)
            last_number = number
        parsed_questions = truncated

    answer_section = _local_answer_section_text(doc_text)
    answer_blocks = _local_answer_blocks(answer_section)
    choice_answers = _extract_choice_answer_sequence(answer_section)

    blocks: list[dict[str, Any]] = []
    if parsed_questions:
        for item in parsed_questions:
            number = str(getattr(item, "question_number", "") or "").strip()
            if not number.isdigit():
                continue
            qid = f"Q{int(number)}"
            question_text = str(getattr(item, "question_text", "") or "").strip()
            answer_from_map = str(answer_blocks.get(number, "") or "").strip()
            parsed_answer = str(getattr(item, "answer_text", "") or "").strip()
            raw_answer = str(answer_from_map or parsed_answer).strip()
            qtype = _infer_local_question_type(question_text, raw_answer, number)
            canonical = _extract_canonical_answer_for_local_question(
                number=number,
                qtype=qtype,
                question_text=question_text,
                answer_text=raw_answer or answer_blocks.get(number, ""),
                choice_answers=choice_answers,
            )
            accepted = _local_accepted_forms(canonical, qtype)
            blocks.append(
                {
                    "question_id": qid,
                    "text": question_text,
                    "question_text": question_text,
                    "answer_text": raw_answer,
                    "question_type": qtype,
                    "canonical_answer": canonical,
                    "accepted_forms": accepted,
                    "local_answer_trusted": bool(answer_from_map)
                    or any(token in raw_answer for token in ["故答案为", "故选", "答案为"]),
                    "needs_review": not bool(canonical) if qtype in {"choice", "fill_blank"} else False,
                }
            )
    else:
        for block in _split_doc_text_into_question_blocks(doc_text):
            qid = str(block.get("question_id") or "")
            number = qid.removeprefix("Q")
            question_text = str(block.get("text") or "")
            raw_answer = answer_blocks.get(number, "")
            qtype = _infer_local_question_type(question_text, raw_answer, number)
            canonical = _extract_canonical_answer_for_local_question(
                number=number,
                qtype=qtype,
                question_text=question_text,
                answer_text=raw_answer,
                choice_answers=choice_answers,
            )
            block.update(
                {
                    "question_text": question_text,
                    "answer_text": raw_answer,
                    "question_type": qtype,
                    "canonical_answer": canonical,
                    "accepted_forms": _local_accepted_forms(canonical, qtype),
                    "local_answer_trusted": bool(raw_answer)
                    or any(token in raw_answer for token in ["故答案为", "故选", "答案为"]),
                    "needs_review": not bool(canonical) if qtype in {"choice", "fill_blank"} else False,
                }
            )
            blocks.append(block)

    return blocks


def _local_answer_section_text(doc_text: str) -> str:
    text = str(doc_text or "")
    try:
        from question_bank.importers.batch_importer import _split_answer_text

        _, answer_text = _split_answer_text(text)
        if str(answer_text or "").strip():
            return str(answer_text or "")
    except Exception:
        pass

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if _looks_like_answer_section_heading(line):
            return "\n".join(lines[idx + 1 :]).strip()
    return ""


def _local_answer_blocks(answer_section: str) -> dict[str, str]:
    section = str(answer_section or "")
    if not section.strip():
        return {}
    try:
        from question_bank.importers.batch_importer import _split_numbered_blocks

        blocks = _split_numbered_blocks(section)
        result: dict[str, str] = {}
        for block in blocks:
            number = str(getattr(block, "number", "") or "").strip()
            if number.isdigit() and 1 <= int(number) <= 99:
                result.setdefault(str(int(number)), str(getattr(block, "text", "") or "").strip())
        if result:
            return result
    except Exception:
        pass

    lines = section.splitlines()
    markers: list[tuple[int, str]] = []
    last_number = 0
    for idx, line in enumerate(lines):
        if "声明" in line or "菁优网" in line:
            break
        number = _extract_question_marker_number(line)
        if number is None:
            continue
        if markers and number <= last_number:
            break
        markers.append((idx, str(number)))
        last_number = number

    result: dict[str, str] = {}
    for pos, (start, number) in enumerate(markers):
        end = markers[pos + 1][0] if pos + 1 < len(markers) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            result[number] = block
    return result


def _extract_choice_answer_sequence(answer_section: str) -> dict[str, str]:
    """Extract choice answers (A/B/C/D) from the answer section in order.

    Supports common Chinese exam answer formats:
    - 故选A / 故选：A
    - 选A / 选：A
    - 答案是A / 答案为A / 答：A
    - 答案选A
    - 独立的单字母行（上下文暗示选择题）
    """
    section = str(answer_section or "")
    found: list[str] = []
    # Broad pattern: any of the common phrasings followed by A/B/C/D
    pattern = (
        r"(?:"
        r"故\s*选"
        r"|选择"
        r"|选\s*[:：]"
        r"|答\s*案\s*(?:是|为|选)?\s*[:：]?"
        r"|答\s*[:：]"
        r")\s*([A-Da-d])"
    )
    for match in re.finditer(pattern, section):
        found.append(match.group(1).upper())
    if not found:
        # Fallback: numbered answers like "1.A" or "1、A" or "(1)A"
        for match in re.finditer(
            r"(?:^|\n)\s*(?:\(\s*)?\d{1,2}\s*[.．、)）]\s*([A-Da-d])(?:\s|$)", section
        ):
            found.append(match.group(1).upper())
    if not found:
        # Last fallback: solo letter on its own line that looks like a choice answer
        for match in re.finditer(r"(?:^|\n)\s*([A-D])\s*(?:\n|$)", section):
            found.append(match.group(1).upper())
    return {str(index): value for index, value in enumerate(found[:20], start=1)}


def _infer_local_question_type(question_text: str, answer_text: str, number: str) -> str:  # noqa: ARG001
    """Infer question type from content only — never by question number position."""
    value = f"{question_text}\n{answer_text}"
    # Choice: has A/B/C/D option lines
    option_line = any(
        re.search(r"[Aa][.．、]?\s*.{0,80}[Bb][.．、]?\s*.{0,80}[Cc][.．、]?\s*.{0,80}[Dd]", line)
        for line in value.splitlines()
    )
    if option_line:
        return "choice"
    # Also choice: answer is a single letter A-D
    if re.fullmatch(r"\s*[A-Da-d]\s*", str(answer_text or "")):
        return "choice"
    # Fill-blank: has explicit blank markers or answer-intro phrases
    has_blank = bool(
        re.search(
            r"_{2,}|　{1,}|（\s*）|\(\s*\)|\b填空\b|故答案为|答案(?:是|为)",
            value,
        )
    )
    if has_blank:
        return "fill_blank"
    # Proof: has proof-specific keywords
    if any(token in value for token in ["证明", "理由", "说明", "求证", "全等", "证得"]):
        return "proof"
    # Calculation: has sub-parts or calculation keywords
    has_subparts = bool(re.search(r"[（(]\s*[1-9]\s*[）)]", value))
    if has_subparts or any(token in value for token in ["计算", "求", "解答", "解："]):
        return "calculation"
    return _infer_question_type_from_block_text(question_text)


def _extract_canonical_answer_for_local_question(
    *,
    number: str,
    qtype: str,
    question_text: str,
    answer_text: str,
    choice_answers: dict[str, str],
) -> str:
    if qtype == "choice":
        # First try the pre-extracted sequence (ordered by question number)
        answer = choice_answers.get(str(int(number))) if str(number or "").isdigit() else ""
        if answer:
            return answer
        # Then try in-line answer patterns
        choice_pattern = (
            r"(?:故\s*选|选择|选\s*[:：]|答\s*案\s*(?:是|为|选)?\s*[:：]?|答\s*[:：])\s*([A-Da-d])"
        )
        match = re.search(choice_pattern, str(answer_text or ""))
        if match:
            return match.group(1).upper()
        # Last: bare single letter
        bare = re.fullmatch(r"\s*([A-Da-d])\s*", str(answer_text or ""))
        return bare.group(1).upper() if bare else ""

    if qtype != "fill_blank":
        return ""

    text = str(answer_text or "")
    # Extended patterns for fill-blank answer extraction
    patterns = [
        r"故答案为\s*[:：]?\s*([^。\n；;]{1,120})",
        r"答案(?:是|为)\s*[:：]?\s*([^。\n；;]{1,120})",
        r"答案\s*[:：]\s*([^。\n；;]{1,120})",
        r"答\s*[:：]\s*([^。\n；;]{1,120})",
        r"故答案为\s*[:：]?\s*([\s\S]{1,80}?)[。．\n]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = _clean_local_answer_text(match.group(1))
            if value:
                return value

    blank_values = re.findall(r"\u3000\s*([^\u3000\n]{1,40}?)\s*\u3000", text)
    for value in blank_values:
        cleaned = _clean_local_answer_text(value)
        if cleaned:
            return cleaned
    return ""


def _clean_local_answer_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[：:，,、;\s]+|[。．，,、;\s]+$", "", text)
    text = text.replace("°", "").strip() if re.fullmatch(r"[\d.]+°", text) else text
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text in {"解", "如下", "见解析"}:
        return ""
    return text[:120]


def _local_accepted_forms(canonical: str, qtype: str) -> list[str]:
    value = str(canonical or "").strip()
    if not value:
        return []
    if qtype == "choice":
        return [value.upper()]
    candidates = [value]
    for chunk in re.split(r"[；;、，,]|\s+或\s+|\bor\b", value):
        cleaned = _clean_local_answer_text(chunk)
        if cleaned:
            candidates.append(cleaned)
    return merge_equivalent_forms([], *candidates, max_forms=16)


def _apply_local_question_facts(payload: dict[str, Any], question_blocks: list[dict[str, Any]]) -> None:
    if not isinstance(payload, dict):
        return
    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    if not isinstance(rubric, dict) or not isinstance(answer_key, dict):
        return
    questions = rubric.setdefault("questions", [])
    answers = answer_key.setdefault("questions", [])
    if not isinstance(questions, list) or not isinstance(answers, list):
        return

    facts = {
        str(block.get("question_id") or "").strip(): block
        for block in question_blocks
        if str(block.get("question_id") or "").strip()
    }
    answer_map = {
        str(answer.get("question_id") or "").strip(): answer
        for answer in answers
        if isinstance(answer, dict)
    }

    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        fact = facts.get(qid)
        if not fact:
            continue
        local_type = str(fact.get("question_type") or "").strip()
        if local_type in {"choice", "fill_blank"}:
            question["question_type"] = local_type
            question["grading_mode"] = "direct_answer"
        if str(fact.get("question_text") or "").strip() and not str(question.get("stem_summary") or "").strip():
            question["stem_summary"] = str(fact.get("question_text") or "").strip().splitlines()[0][:120]

        answer = answer_map.get(qid)
        if answer is None:
            answer = {"question_id": qid, "canonical_answer": "", "accepted_forms": [], "method_variants": [], "parts": []}
            answers.append(answer)
            answer_map[qid] = answer
        canonical = str(fact.get("canonical_answer") or "").strip()
        accepted = [str(item).strip() for item in fact.get("accepted_forms") or [] if str(item).strip()]
        if canonical:
            current_canonical = str(answer.get("canonical_answer") or "").strip()
            if bool(fact.get("local_answer_trusted")) or not current_canonical:
                answer["canonical_answer"] = canonical
                base_answer = canonical
            else:
                base_answer = current_canonical
            answer["accepted_forms"] = merge_equivalent_forms(answer.get("accepted_forms"), *accepted, base_answer, max_forms=32)
            parts = answer.get("parts")
            if not isinstance(parts, list) or not parts:
                answer["parts"] = [{"part_id": qid, "answer": base_answer, "analysis": str(fact.get("answer_text") or ""), "step_milestones": []}]
            else:
                for part in parts:
                    if isinstance(part, dict) and not str(part.get("answer") or "").strip():
                        part["answer"] = base_answer


def _restore_precalibration_objective_answers(payload: dict[str, Any], previous_payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or not isinstance(previous_payload, dict):
        return
    previous_questions = previous_payload.get("rubric", {}).get("questions", [])
    previous_answers = previous_payload.get("answer_key", {}).get("questions", [])
    current_answers = payload.get("answer_key", {}).get("questions", [])
    if not isinstance(previous_questions, list) or not isinstance(previous_answers, list) or not isinstance(current_answers, list):
        return

    previous_types = {
        str(question.get("question_id") or "").strip(): str(question.get("question_type") or "").strip()
        for question in previous_questions
        if isinstance(question, dict)
    }
    previous_answer_map = {
        str(answer.get("question_id") or "").strip(): answer
        for answer in previous_answers
        if isinstance(answer, dict)
    }
    for answer in current_answers:
        if not isinstance(answer, dict):
            continue
        qid = str(answer.get("question_id") or "").strip()
        if previous_types.get(qid) not in {"choice", "fill_blank"}:
            continue
        previous = previous_answer_map.get(qid)
        if not isinstance(previous, dict):
            continue
        previous_canonical = str(previous.get("canonical_answer") or "").strip()
        if not previous_canonical:
            continue
        answer["canonical_answer"] = previous_canonical
        answer["accepted_forms"] = merge_equivalent_forms(previous.get("accepted_forms"), previous_canonical, max_forms=32)
        previous_parts = previous.get("parts")
        if isinstance(previous_parts, list) and previous_parts:
            answer["parts"] = previous_parts


def _split_doc_text_into_question_blocks(doc_text: str) -> list[dict[str, str]]:
    lines = [line.rstrip() for line in str(doc_text or "").splitlines()]
    markers: list[tuple[int, str]] = []
    last_number = 0
    for idx, line in enumerate(lines):
        if _looks_like_answer_section_heading(line) and markers:
            break
        number = _extract_question_marker_number(line)
        if number is None:
            continue
        if markers and number <= last_number:
            break
        markers.append((idx, f"Q{number}"))
        last_number = number
    blocks: list[dict[str, str]] = []
    for pos, (start, qid) in enumerate(markers):
        end = markers[pos + 1][0] if pos + 1 < len(markers) else len(lines)
        text = "\n".join(lines[start:end]).strip()
        if text:
            blocks.append({"question_id": qid, "text": text})
    return blocks


def _extract_question_marker_number(line: str) -> int | None:
    value = str(line or "").strip()
    if not value:
        return None
    patterns = [
        r"^(?:Q|q)\s*(\d{1,2})(?:\b|[\s:：.．、)])",
        r"^第\s*(\d{1,2})\s*[题題]",
        r"^(\d{1,2})\s*[.．、)]",
    ]
    for pattern in patterns:
        match = re.match(pattern, value)
        if not match:
            continue
        number = int(match.group(1))
        if 1 <= number <= 99:
            return number
    return None

def _looks_like_answer_section_heading(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    lower = value.lower().strip(":： ")
    chinese_markers = [
        "\u53c2\u8003\u7b54\u6848",
        "\u8bd5\u9898\u89e3\u6790",
        "\u7b54\u6848\u4e0e\u89e3\u6790",
    ]
    if any(marker in value for marker in chinese_markers):
        return True
    return lower in {"answer", "answers", "solution", "solutions", "answer key"}

def _build_single_question_generation_prompt(block: dict[str, str], doc_text: str) -> str:
    """Build a prompt for a single question's rubric structure.

    IMPORTANT: Scores are NOT requested here — they are allocated in a dedicated
    second AI call (_assign_scores_to_merged_config) after all questions are merged.
    All score fields must be set to 1 as a placeholder.
    """
    qid = str(block.get("question_id") or "").strip()
    q_text = str(block.get("text") or "").strip()
    answer_text = str(block.get("answer_text") or "").strip()
    analysis_text = str(block.get("analysis") or "").strip()

    # Build structured per-question context (replaces the old 6000-char full-doc dump)
    context_parts: list[str] = [f"题目文本：\n{q_text}"]
    if answer_text:
        context_parts.append(f"参考答案：\n{answer_text}")
    if analysis_text:
        context_parts.append(f"解析/证明过程：\n{analysis_text}")
    if not answer_text and not analysis_text:
        # Fallback: include a limited window of the full doc text to help locate the answer
        context_parts.append(
            "（未能提取到配套答案，以下是试卷原文供参考，请自行定位该题答案区域）：\n"
            + str(doc_text or "")[:4000]
        )
    question_context = "\n\n".join(context_parts)

    return (
        "你正在为【单道】初中数学题目生成评分标准大纲结构。请仅返回严格的 JSON 数据。\n"
        f"题目 ID (QUESTION_ID): {qid}\n"
        "重要：请将所有评分相关的分值字段（包括 max_score, part_score, step_score 等）全部设为占位符 1。"
        "具体分值会在后续步骤中统一分配，请勿尝试在此猜测真实分值。\n"
        "规则：仅为该道题构建相应的 rubric.questions 和 answer_key.questions。\n"
        "如果是选择题或填空题，应给出标准答案以及 accepted_forms（等价接受形式）。\n"
        "如果是计算题、证明题、综合题，必须包含 parts、steps、proof_obligations（证明证据点）、"
        "deduction_policy（扣分策略）、证据要求以及仅写出答案的上限得分（answer_only_max_score）。\n"
        "如果该题包含多个空格、表格单元格或子小问，必须将其拆分为不同的 parts 以给与步骤/部分分。\n"
        "accepted_forms 必须仅包含在数学上完全等价的答案形式。\n\n"
        f"{question_context}"
    )


def _merge_single_question_payloads(
    payloads: list[dict[str, Any] | None],
    question_blocks: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    merged = {
        "rubric": {"exam_title": "generated", "total_score": 100, "questions": []},
        "answer_key": {"questions": []},
        "meta": {"source": "word_docx_parallel", "warnings": []},
    }
    seen_questions: set[str] = set()
    seen_answers: set[str] = set()
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue
        expected_qid = ""
        if question_blocks and index < len(question_blocks):
            expected_qid = str(question_blocks[index].get("question_id") or "").strip()
        _coerce_single_question_payload_schema(payload, expected_qid, merged["meta"]["warnings"])
        normalize_generated_config_schema(payload)
        rubric = payload.get("rubric") if isinstance(payload, dict) else {}
        answer_key = payload.get("answer_key") if isinstance(payload, dict) else {}
        for question in rubric.get("questions", []) if isinstance(rubric, dict) else []:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "").strip()
            if not qid or qid in seen_questions:
                continue
            seen_questions.add(qid)
            merged["rubric"]["questions"].append(question)
        for answer in answer_key.get("questions", []) if isinstance(answer_key, dict) else []:
            if not isinstance(answer, dict):
                continue
            qid = str(answer.get("question_id") or "").strip()
            if not qid or qid in seen_answers:
                continue
            seen_answers.add(qid)
            merged["answer_key"]["questions"].append(answer)
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        warnings = meta.get("warnings") if isinstance(meta, dict) else []
        if isinstance(warnings, list):
            merged["meta"]["warnings"].extend(str(item) for item in warnings if str(item).strip())
    return merged


def _coerce_single_question_payload_schema(
    payload: dict[str, Any],
    expected_qid: str,
    warnings: list[str],
) -> None:
    """Accept common one-question schemas returned by LLMs before strict normalization."""
    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    if not isinstance(rubric, dict):
        rubric = {}
        payload["rubric"] = rubric
    if not isinstance(answer_key, dict):
        answer_key = {}
        payload["answer_key"] = answer_key

    questions = rubric.get("questions")
    if not isinstance(questions, list):
        questions = []
        rubric["questions"] = questions
    answers = answer_key.get("questions")
    if not isinstance(answers, list):
        answers = []
        answer_key["questions"] = answers

    question = next((item for item in questions if isinstance(item, dict)), None)
    if question is None:
        question = _extract_single_question_candidate(payload, rubric)
        if isinstance(question, dict):
            questions.append(question)
            if expected_qid:
                warnings.append(f"{expected_qid} normalized singular rubric.question schema")

    answer = next((item for item in answers if isinstance(item, dict)), None)
    if answer is None:
        answer = _extract_single_answer_candidate(payload, answer_key, question)
        if isinstance(answer, dict):
            answers.append(answer)
            if expected_qid:
                warnings.append(f"{expected_qid} normalized singular answer_key.question schema")

    if expected_qid:
        for item in [question, answer]:
            if isinstance(item, dict):
                raw_qid = item.get("question_id") or item.get("id") or item.get("number")
                item["question_id"] = _canonical_question_id(raw_qid, expected_qid)

    if question is None and expected_qid:
        keys = ", ".join(sorted(str(key) for key in payload.keys()))
        warnings.append(f"{expected_qid} unmergeable single-question schema; top-level keys: {keys}")


def _extract_single_question_candidate(payload: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        rubric.get("question"),
        payload.get("question"),
        payload.get("rubric_question"),
    ]
    if _looks_like_question_dict(rubric):
        candidates.append(rubric)
    if _looks_like_question_dict(payload):
        candidates.append(payload)
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None


def _extract_single_answer_candidate(
    payload: dict[str, Any],
    answer_key: dict[str, Any],
    question: dict[str, Any] | None,
) -> dict[str, Any] | None:
    candidates = [
        answer_key.get("question"),
        payload.get("answer_key_question"),
        payload.get("answer"),
        payload.get("correct_answer"),
    ]
    if _looks_like_answer_dict(answer_key):
        candidates.append(answer_key)
    if isinstance(question, dict) and _looks_like_answer_dict(question):
        candidates.append(question)
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
        if candidate is not None:
            return {"canonical_answer": str(candidate)}
    return None


def _looks_like_question_dict(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return any(
        key in item
        for key in [
            "question_id",
            "id",
            "number",
            "question_type",
            "type",
            "max_score",
            "score",
            "points",
            "parts",
            "stem_summary",
        ]
    )


def _looks_like_answer_dict(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return any(
        key in item
        for key in [
            "canonical_answer",
            "answer",
            "correct_answer",
            "direct_answer",
            "accepted_forms",
            "equivalent_answers",
            "parts",
        ]
    )


def _attach_parallel_generation_meta(
    payload: dict[str, Any],
    question_blocks: list[dict[str, str]],
    results: list[dict[str, Any] | None],
    failures: list[str],
    worker_count: int,
) -> None:
    meta = payload.setdefault("meta", {})
    if not isinstance(meta, dict):
        payload["meta"] = meta = {}
    warnings = meta.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        meta["warnings"] = warnings

    meta["source"] = "word_docx_parallel"
    meta["question_block_count"] = len(question_blocks)
    meta["single_question_success_count"] = sum(1 for result in results if isinstance(result, dict))
    meta["single_question_failure_count"] = len(failures)
    meta["config_generation_workers"] = int(worker_count)

    existing = {str(item) for item in warnings}
    for failure in failures:
        warning = f"parallel generation failed for {failure}"
        if warning not in existing:
            warnings.append(warning)
            existing.add(warning)
    if failures:
        summary = "parallel generation completed with failed question blocks; final calibration attempted to preserve usable results."
        if summary not in existing:
            warnings.append(summary)


def _config_final_calibration_enabled(default: bool = False) -> bool:  # noqa: FBT001,FBT002
    """Final model calibration is OFF by default to avoid extra cost and failure risk.

    Set env var AI_GRADING_CONFIG_FINAL_CALIBRATION=1 to re-enable it.
    """
    raw = str(os.getenv("AI_GRADING_CONFIG_FINAL_CALIBRATION", "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return raw in {"1", "true", "yes", "on", "enabled"}


def _ensure_question_blocks_covered(payload: dict[str, Any], question_blocks: list[dict[str, str]]) -> None:
    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    if not isinstance(rubric, dict) or not isinstance(answer_key, dict):
        return
    questions = rubric.setdefault("questions", [])
    answers = answer_key.setdefault("questions", [])
    if not isinstance(questions, list) or not isinstance(answers, list):
        return

    existing_qids = {
        str(question.get("question_id") or "").strip()
        for question in questions
        if isinstance(question, dict)
    }
    existing_answer_qids = {
        str(answer.get("question_id") or "").strip()
        for answer in answers
        if isinstance(answer, dict)
    }
    warnings = payload.setdefault("meta", {}).setdefault("warnings", [])

    for block in question_blocks:
        qid = str(block.get("question_id") or "").strip()
        if not qid:
            continue
        if qid not in existing_qids:
            questions.append(_placeholder_question_from_block(block))
            existing_qids.add(qid)
            if isinstance(warnings, list):
                warnings.append(f"{qid} placeholder added because per-question generation did not return a mergeable rubric item")
        if qid not in existing_answer_qids:
            answers.append({"question_id": qid, "canonical_answer": "", "accepted_forms": [], "method_variants": [], "parts": []})
            existing_answer_qids.add(qid)

    qid_order = {
        str(block.get("question_id") or "").strip(): index
        for index, block in enumerate(question_blocks)
        if str(block.get("question_id") or "").strip()
    }
    questions.sort(key=lambda item: qid_order.get(str(item.get("question_id") or "").strip(), 10_000) if isinstance(item, dict) else 10_001)
    answers.sort(key=lambda item: qid_order.get(str(item.get("question_id") or "").strip(), 10_000) if isinstance(item, dict) else 10_001)


def _placeholder_question_from_block(block: dict[str, str]) -> dict[str, Any]:
    qid = str(block.get("question_id") or "").strip()
    text = str(block.get("text") or "").strip()
    qtype = _infer_question_type_from_block_text(text)
    return {
        "question_id": qid,
        "question_type": qtype,
        "max_score": 1,
        "knowledge_id": "UNKNOWN",
        "stem_summary": text.splitlines()[0][:120] if text else qid,
        "parts": [
            {
                "part_id": qid,
                "part_score": 1,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": 1,
                        "core_goal": "Manual rubric confirmation required",
                        "required_elements": ["AI did not return a mergeable question; confirm rubric manually"],
                        "allow_alternative_methods": True,
                    }
                ],
                "presentation_rules": [],
            }
        ],
    }


def _infer_question_type_from_block_text(text: str) -> str:
    value = str(text or "")
    option_line = any(re.search(r"A.{0,80}B.{0,80}C.{0,80}D", line) for line in value.splitlines())
    if option_line:
        return "choice"
    has_blank = bool(re.search(r"_{2,}|[ \t]{3,}|　{1,}|（\s*）|\(\s*\)", value))
    if has_blank:
        return "fill_blank"
    has_subparts = bool(re.search(r"[（(]\s*[1-9]\s*[）)]", value))
    if has_subparts:
        return "calculation"
    if any(token in value.lower() for token in ["proof", "why", "explain", "relationship"]):
        return "comprehensive"
    return "fill_blank"


def _attach_generation_fallback_warning(
    payload: dict[str, Any],
    question_blocks: list[dict[str, str]],
    parallel_error: Exception | None,
) -> None:
    if parallel_error is None:
        return
    meta = payload.setdefault("meta", {})
    if not isinstance(meta, dict):
        payload["meta"] = meta = {}
    warnings = meta.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        meta["warnings"] = warnings
    meta["source"] = "word_docx_single_fallback"
    meta["question_block_count"] = len(question_blocks)
    meta["parallel_generation_fallback"] = True
    warnings.append(f"parallel generation failed; fell back to single full-document generation: {parallel_error}")


def _assign_scores_to_merged_config(
    merged: dict[str, Any],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
) -> None:
    """Phase-2 AI call: allocate real scores to all questions so they sum to 100.

    Per-question prompts set all score fields to the placeholder value 1.
    This function sends the merged structure + original Word text to the model,
    which reads score hints like （3分）, 共X分 from the document and distributes
    the remaining points proportionally to question complexity.

    On failure, falls back to local proportional scaling via force_payload_total_score.
    """
    rubric_questions: list[Any] = merged.get("rubric", {}).get("questions", [])
    if not isinstance(rubric_questions, list) or not rubric_questions:
        force_payload_total_score(merged, target_total=100.0)
        return

    # Build a lightweight structure summary: only IDs, types, part/step counts
    structure_summary: list[dict[str, Any]] = []
    for q in rubric_questions:
        if not isinstance(q, dict):
            continue
        parts = q.get("parts") or []
        part_list = []
        for p in parts if isinstance(parts, list) else []:
            if not isinstance(p, dict):
                continue
            steps = p.get("steps") or []
            part_list.append({
                "part_id": str(p.get("part_id") or ""),
                "step_ids": [str(s.get("step_id") or "") for s in steps if isinstance(s, dict)],
            })
        structure_summary.append({
            "question_id": str(q.get("question_id") or ""),
            "question_type": str(q.get("question_type") or ""),
            "parts": part_list,
        })

    prompt = (
        "你正在分配试卷各题目的得分。试卷总分必须精确等于 100 分。\n\n"
        "硬性规则（请严格遵守）：\n"
        "1. 所有相同类型（选择题 choice / 填空题 fill_blank）的题目必须共享相同的 max_score（即同类题目的单题分值相同）。\n"
        "2. 对于每道题目：其下属各部分的 part_score 之和必须等于 max_score。对于每个 part：其下属各步骤的 step_score 之和必须等于 part_score。\n"
        "3. 所有分值必须是正整数。\n"
        "4. 试卷所有题目的 max_score 之和必须精确等于 100。\n"
        "5. 将未分配的分数，按照步骤的数量比例分配给主观大题。\n"
        "6. 关键限制 —— 单道大题 15% 分值上限约束（适用于解答题、计算题、证明题）：\n"
        "   - 每道独立题目的 max_score 必须 <= 15 分（即 100 分的 15%）。\n"
        "   - 所有共享相同大题号的子小问（例如 Q10_1 和 Q10_2 共享同一个大题前缀 Q10）的合并总分必须 <= 15 分。\n"
        "   - 若某解答题原有的标注分值超过 15 分，必须强行将该题总分（含所有小问）封顶在 15 分，并将其余分数分摊分配给选择题、填空题或其他大题。\n"
        "   - 你必须严格保持输入中提供的题目结构和 question_id 列表，绝对不允许新增、删除、修改或拆分任何 question_id 题号。\n\n"
        "Return ONLY the following JSON (no markdown, no explanation):\n"
        "{\n"
        "  \"question_scores\": [\n"
        "    {\n"
        "      \"question_id\": \"Q1\",\n"
        "      \"max_score\": 3,\n"
        "      \"parts\": [\n"
        "        {\"part_id\": \"Q1\", \"part_score\": 3,\n"
        "         \"steps\": [{\"step_id\": \"S1\", \"step_score\": 3}]}\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Word document (for score hints, first 6000 chars):\n{str(doc_text or '')[:6000]}\n\n"
        f"Question structure to score:\n{json.dumps(structure_summary, ensure_ascii=False)}"
    )

    if report:
        q_count = len(structure_summary)
        report(0.88, "AI 赋分", f"正在为合并后的 {q_count} 道题统一分配分值（共 100 分）...")

    try:
        score_data = llm_client.json_from_text(prompt, model=model_name)
        _apply_score_allocation(merged, score_data)
        merged.setdefault("meta", {})["score_allocation_ai_success"] = True
        if report:
            report(0.91, "AI 赋分完成", "已将 AI 分配的分值写入各题评分细则。")
    except Exception as exc:
        if report:
            report(0.91, "AI 赋分失败，使用本地均分兜底", str(exc))
        warnings = merged.setdefault("meta", {}).setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(f"score allocation AI call failed; fell back to local scaling: {exc}")
        merged.setdefault("meta", {})["score_allocation_ai_success"] = False
    # Always do a local normalization pass to guarantee score consistency
    force_payload_total_score(merged, target_total=100.0)


def _apply_score_allocation(merged: dict[str, Any], score_data: dict[str, Any]) -> None:
    """Write AI-assigned scores from the scoring step back into the merged rubric."""
    question_scores = score_data.get("question_scores")
    if not isinstance(question_scores, list):
        raise ValueError("score_data must contain a 'question_scores' list")

    rubric_questions: list[Any] = merged.get("rubric", {}).get("questions", [])
    if not isinstance(rubric_questions, list):
        return

    score_map: dict[str, dict[str, Any]] = {
        str(qs.get("question_id") or "").strip(): qs
        for qs in question_scores
        if isinstance(qs, dict) and str(qs.get("question_id") or "").strip()
    }

    for q in rubric_questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("question_id") or "").strip()
        score_entry = score_map.get(qid)
        if not score_entry:
            continue

        # Apply max_score
        raw_max = score_entry.get("max_score")
        if raw_max is not None:
            try:
                q["max_score"] = max(1, int(round(float(raw_max))))
            except (TypeError, ValueError):
                pass

        # Apply part and step scores
        parts: list[Any] = q.get("parts") or []
        score_parts: list[Any] = score_entry.get("parts") or []
        if not isinstance(parts, list) or not score_parts:
            continue

        part_score_map: dict[str, dict[str, Any]] = {
            str(sp.get("part_id") or "").strip(): sp
            for sp in score_parts
            if isinstance(sp, dict) and str(sp.get("part_id") or "").strip()
        }

        for part in parts:
            if not isinstance(part, dict):
                continue
            pid = str(part.get("part_id") or "").strip()
            sp = part_score_map.get(pid)
            if not sp:
                continue

            raw_part = sp.get("part_score")
            if raw_part is not None:
                try:
                    part["part_score"] = max(1, int(round(float(raw_part))))
                except (TypeError, ValueError):
                    pass

            steps: list[Any] = part.get("steps") or []
            score_steps: list[Any] = sp.get("steps") or []
            if not isinstance(steps, list) or not score_steps:
                continue

            step_score_map: dict[str, dict[str, Any]] = {
                str(ss.get("step_id") or "").strip(): ss
                for ss in score_steps
                if isinstance(ss, dict) and str(ss.get("step_id") or "").strip()
            }

            for step in steps:
                if not isinstance(step, dict):
                    continue
                sid = str(step.get("step_id") or "").strip()
                ss = step_score_map.get(sid)
                if ss:
                    raw_step = ss.get("step_score")
                    if raw_step is not None:
                        try:
                            step["step_score"] = max(1, int(round(float(raw_step))))
                        except (TypeError, ValueError):
                            pass


def _calibrate_merged_config(
    payload: dict[str, Any],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    *,
    strict: bool = False,
) -> None:
    prompt = (
        "final calibration: You are a rubric calibration assistant. "
        "Check the merged per-question rubric against the full document. "
        "Only fix missing question ids, empty answers, obvious question type errors, and total score allocation. "
        "Do not remove existing proof_obligations, steps, or parts. "
        "accepted_forms must keep only strictly equivalent answers. Return complete JSON with the same schema.\n\n"
        f"Full document text:\n{str(doc_text or '')[:8000]}\n\n"
        f"Merged result:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        calibrated = llm_client.json_from_text(prompt, model=model_name)
        validate_generated_config(calibrated)
    except Exception as exc:
        if strict:
            raise RuntimeError(f"final calibration failed: {exc}") from exc
        meta = payload.setdefault("meta", {})
        if isinstance(meta, dict):
            warnings = meta.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(f"final calibration failed; kept merged per-question config: {exc}")
        return
    payload.clear()
    payload.update(calibrated)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        resolved = int(default)
    return max(minimum, min(maximum, resolved))


def refine_grading_config_from_manual_structure(
    payload: dict[str, Any],
    llm_client: LLMClient,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Ask AI to complete answers/rubrics while preserving teacher-edited parts."""
    working_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    validate_generated_config(working_payload)
    prompt = _build_manual_structure_refinement_prompt(working_payload)
    refined = llm_client.json_from_text(prompt, model=model_name)
    try:
        validate_generated_config(refined)
    except Exception:
        _dump_failed_generated_payload(refined)
        raise
    return refined


def _dump_failed_generated_payload(payload: dict[str, Any]) -> None:
    try:
        try:
            from path_manager import get_path_manager
            out_dir = get_path_manager().upload_config_dir
        except Exception:
            out_dir = Path(__file__).resolve().parent / "config" / "uploaded"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (out_dir / f"failed_generated_config_normalized_{ts}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def normalize_generated_config_schema(payload: dict[str, Any]) -> None:
    """Convert common LLM shorthand fields into the strict internal schema."""
    if not isinstance(payload, dict):
        return

    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    payload.setdefault("meta", {}).setdefault("warnings", [])
    if not isinstance(rubric, dict) or not isinstance(answer_key, dict):
        return

    rubric_questions = rubric.setdefault("questions", [])
    answer_questions = answer_key.setdefault("questions", [])
    if not isinstance(rubric_questions, list):
        rubric["questions"] = []
        rubric_questions = rubric["questions"]
    if not isinstance(answer_questions, list):
        answer_key["questions"] = []
        answer_questions = answer_key["questions"]

    for answer in answer_questions:
        if isinstance(answer, dict):
            answer["question_id"] = _canonical_question_id(
                answer.get("question_id")
                or answer.get("id")
                or answer.get("number")
                or answer.get("棰樺彿")
                or "",
                "",
            )

    answer_map = {
        _canonical_question_id(q.get("question_id") or q.get("id") or q.get("number") or "", ""): q
        for q in answer_questions
        if isinstance(q, dict)
    }

    for idx, question in enumerate(rubric_questions, start=1):
        if not isinstance(question, dict):
            continue
        qid = _canonical_question_id(
            question.get("question_id")
            or question.get("id")
            or question.get("number")
            or f"Q{idx}",
            f"Q{idx}",
        )
        question["question_id"] = qid
        qtype = _normalize_question_type(
            question.get("question_type")
            or question.get("type")
        )
        question["question_type"] = qtype
        question["max_score"] = _safe_float(
            question.get("max_score")
            or question.get("score")
            or question.get("points"),
            0.0,
        )
        question["knowledge_id"] = str(question.get("knowledge_id") or question.get("knowledge") or "UNKNOWN")
        question["knowledge_name"] = str(
            question.get("knowledge_name")
            or question.get("knowledge_text")
            or question.get("knowledge_label")
            or question.get("stem_summary")
            or question.get("knowledge")
            or ""
        ).strip()
        _normalize_question_knowledge_fields(question)
        question["stem_summary"] = str(question.get("stem_summary") or question.get("棰樺共鎽樿") or "").strip()
        question["grading_mode"] = str(
            question.get("grading_mode")
            or ("deductive_obligation" if qtype in {"proof", "calculation", "comprehensive"} else "direct_answer")
        )

        answer_item = answer_map.get(qid)
        if not isinstance(answer_item, dict):
            answer_item = {"question_id": qid}
            answer_questions.append(answer_item)
            answer_map[qid] = answer_item
        _normalize_answer_item(answer_item, question)
        if _should_treat_as_direct_answer_question(qtype, question, answer_item):
            qtype = "fill_blank"
            question["question_type"] = qtype
            question["grading_mode"] = "direct_answer"
        _augment_answer_equivalences(answer_item, qtype)
        _normalize_rubric_question(question, answer_item)
        _ensure_solution_hard_rules(question)

    rubric["total_score"] = _safe_float(
        rubric.get("total_score"),
        sum(_safe_float(q.get("max_score"), 0.0) for q in rubric_questions if isinstance(q, dict)),
    )


def _canonical_question_id(value: Any, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return str(fallback or "").strip()
    match = re.match(r"^(?:Q|q)\s*(\d{1,2})$", raw)
    if match:
        return f"Q{int(match.group(1))}"
    match = re.match(r"^(\d{1,2})$", raw)
    if match:
        return f"Q{int(match.group(1))}"
    match = re.search(r"(\d{1,2})", raw)
    if re.match(r"^\\D+\\d{1,2}", raw) and match:
        return f"Q{int(match.group(1))}"
    return raw


def _normalize_question_knowledge_fields(question: dict[str, Any]) -> None:
    raw_points = question.get("knowledge_points")
    points: list[dict[str, str]] = []
    if isinstance(raw_points, list):
        for item in raw_points:
            if isinstance(item, dict):
                kid = str(item.get("knowledge_id") or item.get("id") or "").strip()
                name = str(item.get("knowledge_name") or item.get("name") or item.get("label") or "").strip()
            else:
                kid = str(item or "").strip()
                name = ""
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": name})

    raw_ids = question.get("knowledge_ids")
    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            kid = str(raw_id or "").strip()
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": ""})

    primary_id = str(question.get("knowledge_id") or "UNKNOWN").strip()
    primary_name = str(question.get("knowledge_name") or "").strip()
    if primary_id:
        points.append({"knowledge_id": primary_id, "knowledge_name": primary_name})

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for point in points:
        kid = point["knowledge_id"]
        if not kid or kid in seen:
            continue
        seen.add(kid)
        normalized.append(point)

    if not normalized:
        normalized = [{"knowledge_id": "UNKNOWN", "knowledge_name": ""}]

    question["knowledge_points"] = normalized
    question["knowledge_ids"] = [point["knowledge_id"] for point in normalized]
    question["knowledge_id"] = normalized[0]["knowledge_id"]
    if normalized[0].get("knowledge_name"):
        question["knowledge_name"] = normalized[0]["knowledge_name"]


def force_payload_total_score(payload: dict[str, Any], target_total: float = 100.0) -> None:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    if not isinstance(rubric, dict):
        return
    questions = rubric.get("questions")
    if not isinstance(questions, list) or not questions:
        rubric["total_score"] = target_total
        return

    current_total = sum(_safe_float(q.get("max_score"), 0.0) for q in questions if isinstance(q, dict))
    if current_total <= 0:
        each = round(target_total / len(questions), 2)
        for question in questions:
            if isinstance(question, dict):
                _scale_question_to_score(question, each)
        _fix_question_sum(questions, target_total)
    elif abs(current_total - target_total) > 0.01:
        ratio = target_total / current_total
        for question in questions:
            if isinstance(question, dict):
                _scale_question_scores(question, ratio)
        _fix_question_sum(questions, target_total)

    enforce_integer_scores_by_type(questions, target_total=int(target_total), max_question_score=12)
    # Second pass: fix any +-1 rounding drift produced by integer allocation
    _fix_question_sum(questions, target_total)
    for question in questions:
        if isinstance(question, dict):
            _ensure_solution_hard_rules(question)
    rubric["total_score"] = target_total
    payload.setdefault("meta", {}).setdefault("warnings", [])


def _scale_question_scores(question: dict[str, Any], ratio: float) -> None:
    new_score = round(_safe_float(question.get("max_score"), 0.0) * ratio, 2)
    _scale_question_to_score(question, new_score)


def _scale_question_to_score(question: dict[str, Any], new_score: float) -> None:
    old_score = _safe_float(question.get("max_score"), 0.0)
    question["max_score"] = round(float(new_score), 2)
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        _normalize_rubric_question(question, {})
        return

    ratio = (float(new_score) / old_score) if old_score > 0 else (1.0 / len(parts))
    for part in parts:
        if not isinstance(part, dict):
            continue
        part["part_score"] = round(_safe_float(part.get("part_score"), 0.0) * ratio, 2)
        steps = part.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    step["step_score"] = round(_safe_float(step.get("step_score"), 0.0) * ratio, 2)
        _force_step_total(part)
    _force_part_total(question)
    _scale_deduction_policy(question, ratio)


def _scale_deduction_policy(question: dict[str, Any], ratio: float) -> None:
    policies = question.get("deduction_policy")
    if isinstance(policies, list):
        for policy in policies:
            if isinstance(policy, dict) and "max_deduction" in policy:
                policy["max_deduction"] = round(_safe_float(policy.get("max_deduction"), 0.0) * ratio, 2)


def _fix_question_sum(questions: list[Any], target_total: float) -> None:
    valid_questions = [q for q in questions if isinstance(q, dict)]
    if not valid_questions:
        return
    total = sum(_safe_float(q.get("max_score"), 0.0) for q in valid_questions)
    diff = round(target_total - total, 2)
    if abs(diff) > 0.001:
        last = valid_questions[-1]
        last["max_score"] = round(_safe_float(last.get("max_score"), 0.0) + diff, 2)
        _force_part_total(last)


def _normalize_answer_item(answer_item: dict[str, Any], rubric_question: dict[str, Any]) -> None:
    qid = str(rubric_question.get("question_id") or answer_item.get("question_id") or "")
    answer_item["question_id"] = qid
    direct_answers = _extract_direct_answer_values(answer_item)
    canonical = (
        answer_item.get("canonical_answer")
        or answer_item.get("answer")
        or answer_item.get("correct_answer")
        or answer_item.get("绛旀")
        or (direct_answers[0] if direct_answers else "")
        or rubric_question.get("canonical_answer")
        or rubric_question.get("answer")
        or rubric_question.get("correct_answer")
        or ""
    )
    answer_item["canonical_answer"] = str(canonical)
    if bool(answer_item.get("_manual_accepted_forms")):
        answer_item["accepted_forms"] = _string_list(answer_item.get("accepted_forms"))
    else:
        answer_item["accepted_forms"] = _string_list(
            answer_item.get("accepted_forms")
            or answer_item.get("equivalent_answers")
            or answer_item.get("aliases")
            or direct_answers
            or rubric_question.get("accepted_forms")
            or rubric_question.get("equivalent_answers")
            or [canonical]
        )
    answer_item["method_variants"] = _method_variants(
        answer_item.get("method_variants") or rubric_question.get("method_variants") or []
    )
    if not isinstance(answer_item.get("parts"), list) or not answer_item["parts"]:
        answer_item["parts"] = [
            {
                "part_id": qid,
                "answer": str(canonical),
                "analysis": str(answer_item.get("analysis") or rubric_question.get("analysis") or ""),
                "step_milestones": _string_list(answer_item.get("step_milestones") or rubric_question.get("step_milestones")),
            }
        ]
    else:
        for idx, part in enumerate(answer_item["parts"], start=1):
            if not isinstance(part, dict):
                continue
            part_direct_answers = _extract_direct_answer_values(part)
            part.setdefault("part_id", qid if len(answer_item["parts"]) == 1 else f"{qid}({idx})")
            if not str(part.get("answer") or "").strip() and part_direct_answers:
                part["answer"] = str(part_direct_answers[0])
            else:
                part.setdefault("answer", str(canonical))
            if part_direct_answers and not part.get("accepted_forms"):
                part["accepted_forms"] = _string_list(part_direct_answers)
            part.setdefault("analysis", "")
            if not isinstance(part.get("step_milestones"), list):
                part["step_milestones"] = _string_list(part.get("step_milestones"))


def _extract_direct_answer_values(item: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    direct = item.get("direct_answer")
    if isinstance(direct, dict):
        for key in ["accepted_forms", "answers", "answer", "value", "values"]:
            raw = direct.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                values.extend(raw)
            else:
                values.append(raw)
    elif isinstance(direct, list):
        values.extend(direct)
    elif direct is not None:
        values.append(direct)
    return values


def _should_treat_as_direct_answer_question(
    qtype: str,
    question: dict[str, Any],
    answer_item: dict[str, Any],
) -> bool:
    normalized_type = str(qtype or "").strip().lower()
    if normalized_type in {"choice", "fill_blank", "judgement", "true_false"}:
        return False
    mode_text = " ".join(
        str(question.get(key) or "")
        for key in ["grading_mode", "scoring_type", "scoring_policy", "scoring_rule", "description"]
    ).lower()
    has_direct_mode = "direct_answer" in mode_text or "鍏ㄥ鍏ㄩ敊" in mode_text
    has_direct_answer = bool(_extract_direct_answer_values(answer_item) or _string_list(answer_item.get("accepted_forms")))
    has_solution_structure = bool(question.get("proof_obligations")) or any(
        isinstance(part, dict) and len(part.get("steps") or []) > 1
        for part in (question.get("parts") if isinstance(question.get("parts"), list) else [])
    )
    return has_direct_answer and has_direct_mode and not has_solution_structure


def _augment_answer_equivalences(answer_item: dict[str, Any], qtype: str) -> None:
    """Add deterministic local equivalents for objective/short-answer items."""
    if qtype == "choice":
        return

    canonical = str(answer_item.get("canonical_answer") or "").strip()
    answers = [canonical]
    top_level_manually_edited = bool(answer_item.get("_manual_accepted_forms"))
    parts = answer_item.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_answer = str(part.get("answer") or "").strip()
            if part_answer:
                answers.append(part_answer)
                if not bool(part.get("_manual_accepted_forms")):
                    part["accepted_forms"] = merge_equivalent_forms(part.get("accepted_forms"), part_answer, max_forms=16)

    if not top_level_manually_edited:
        answer_item["accepted_forms"] = merge_equivalent_forms(answer_item.get("accepted_forms"), *answers, max_forms=32)


def _normalize_rubric_question(question: dict[str, Any], answer_item: dict[str, Any]) -> None:
    qid = str(question.get("question_id") or "")
    qtype = str(question.get("question_type") or "comprehensive")
    max_score = _safe_float(question.get("max_score"), 0.0)
    _align_solution_parts_with_answer_parts(question, answer_item, qtype, max_score)
    if not isinstance(question.get("parts"), list) or not question["parts"]:
        question["parts"] = [
            {
                "part_id": qid,
                "part_score": max_score,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": max_score,
                        "core_goal": _default_core_goal(qtype, answer_item),
                        "required_elements": _default_required_elements(qtype, answer_item),
                        "allow_alternative_methods": qtype not in {"choice"},
                    }
                ],
                "presentation_rules": [],
            }
        ]
    else:
        part_count = len(question["parts"])
        for idx, part in enumerate(question["parts"], start=1):
            if not isinstance(part, dict):
                part = {"part_id": qid if part_count == 1 else f"{qid}({idx})", "answer": str(part)}
                question["parts"][idx - 1] = part
            part_score = _safe_float(
                part.get("part_score")
                or part.get("score")
                or part.get("points"),
                max_score / max(part_count, 1),
            )
            part["part_id"] = str(part.get("part_id") or (qid if part_count == 1 else f"{qid}({idx})"))
            part["part_score"] = part_score
            step_alias = (
                part.get("steps")
                or part.get("scoring_steps")
                or part.get("criteria")
                or part.get("rubric")
                or part.get("score_points")
            )
            if step_alias is not part.get("steps"):
                part["steps"] = step_alias

            if not isinstance(part.get("steps"), list) or not part["steps"]:
                part["steps"] = [
                    {
                        "step_id": "S1",
                        "step_score": part_score,
                        "core_goal": _default_core_goal(qtype, answer_item),
                        "required_elements": _default_required_elements(qtype, answer_item),
                        "allow_alternative_methods": qtype not in {"choice"},
                    }
                ]
            else:
                for sidx, step in enumerate(part["steps"], start=1):
                    if not isinstance(step, dict):
                        step = {"core_goal": str(step)}
                        part["steps"][sidx - 1] = step
                    step["step_id"] = str(step.get("step_id") or f"S{sidx}")
                    # Avoid overriding explicit 0.0 with fallback by using explicit None checks
                    raw_score = step.get("step_score")
                    if raw_score is None:
                        raw_score = step.get("score")
                    if raw_score is None:
                        raw_score = step.get("points")
                    
                    step["step_score"] = _safe_float(
                        raw_score,
                        part_score / max(len(part["steps"]), 1),
                    )
                    step["core_goal"] = str(
                        step.get("core_goal")
                        or step.get("goal")
                        or step.get("criterion")
                        or step.get("description")
                        or step.get("step_description")
                        or _default_core_goal(qtype, answer_item)
                    )
                    if not isinstance(step.get("required_elements"), list):
                        step["required_elements"] = _string_list(step.get("required_elements")) or _default_required_elements(qtype, answer_item)
                    step["allow_alternative_methods"] = bool(step.get("allow_alternative_methods", qtype not in {"choice"}))
            if not isinstance(part.get("presentation_rules"), list):
                part["presentation_rules"] = []
            _force_step_total(part)
    _force_part_total(question)


def _align_solution_parts_with_answer_parts(
    question: dict[str, Any],
    answer_item: dict[str, Any],
    qtype: str,
    max_score: float,
) -> None:
    if qtype not in {"calculation", "proof", "comprehensive"}:
        return
    answer_parts = answer_item.get("parts")
    if not isinstance(answer_parts, list) or len(answer_parts) <= 1:
        return
    rubric_parts = question.get("parts")
    if isinstance(rubric_parts, list) and len(rubric_parts) > 1:
        return
    part_scores = _allocate_scores(max_score, len(answer_parts))
    next_parts: list[dict[str, Any]] = []
    for idx, (answer_part, part_score) in enumerate(zip(answer_parts, part_scores), start=1):
        if not isinstance(answer_part, dict):
            continue
        part_id = str(answer_part.get("part_id") or f"{question.get('question_id')}({idx})")
        answer = str(answer_part.get("answer") or "").strip()
        next_parts.append(
            {
                "part_id": part_id,
                "part_score": part_score,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": part_score,
                        "core_goal": f"完成 {part_id} 的答题要求",
                        "required_elements": [answer] if answer else ["合理的推理过程", "正确的结论"],
                        "allow_alternative_methods": True,
                    }
                ],
                "presentation_rules": [],
            }
        )
    if next_parts:
        question["parts"] = next_parts


def _allocate_scores(total: float, count: int) -> list[float]:
    if count <= 0:
        return []
    total_int = int(round(total))
    base = total_int // count
    remainder = total_int - base * count
    return [base + (1 if idx < remainder else 0) for idx in range(count)]


def _ensure_solution_hard_rules(question: dict[str, Any]) -> None:
    """Persist teacher-editable process/answer rules for solution questions."""
    qtype = str(question.get("question_type") or "")
    if qtype not in {"proof", "calculation", "comprehensive"}:
        return

    max_score = _safe_float(question.get("max_score"), 0.0)
    default_answer_only = max(1, int(round(max_score * 0.25))) if max_score > 0 else 1

    if "require_final_answer" not in question:
        # Proof and pure calculation questions usually do not need an extra "绛?;
        # general comprehensive answers often do.
        question["require_final_answer"] = qtype == "comprehensive"
    question["require_final_answer"] = bool(question.get("require_final_answer"))

    answer_only_raw = question.get("answer_only_max_score")
    answer_only_max = int(round(_safe_float(answer_only_raw, default_answer_only)))
    if max_score > 0:
        answer_only_max = max(0, min(answer_only_max, int(round(max_score))))
    question["answer_only_max_score"] = answer_only_max

    policies = question.get("deduction_policy")
    if not isinstance(policies, list):
        policies = []
        question["deduction_policy"] = policies

    _upsert_policy(
        policies,
        {
            "policy_id": "answer_only_process_missing",
            "issue": f"仅有最终答案但缺乏有效过程；最高只给 {answer_only_max} 分，扣除过程分",
            "max_deduction": max(0, int(round(max_score)) - answer_only_max),
            "severity": "major",
        },
    )
    _upsert_policy(
        policies,
        {
            "policy_id": "core_process_missing",
            "issue": "缺失关键步骤、证明逻辑或推理链条；扣除对应的步骤分",
            "max_deduction": int(round(max_score)),
            "severity": "fatal",
        },
    )

    question["answer_presentation_policy"] = {
        "require_final_answer": question["require_final_answer"],
        "answer_only_max_score": answer_only_max,
        "note": "教师可根据具体题目微调此项；解答/证明题仍需过程证据以获取满分。",
    }

    parts = question.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            
            part_score = _safe_float(part.get("part_score") or part.get("max_score"), max_score)
            part_default_answer_only = max(1, int(round(part_score * 0.25))) if part_score > 0 else 1
            
            if "require_final_answer" not in part:
                part["require_final_answer"] = question["require_final_answer"]
            part["require_final_answer"] = bool(part.get("require_final_answer"))
            
            if "answer_only_max_score" not in part:
                part["answer_only_max_score"] = int(round(_safe_float(question.get("answer_only_max_score"), part_default_answer_only)))
            part["answer_only_max_score"] = max(0, min(int(round(_safe_float(part.get("answer_only_max_score"), part_default_answer_only))), int(round(part_score))))
            
            rules = part.get("presentation_rules")
            if not isinstance(rules, list):
                rules = []
                part["presentation_rules"] = rules
            _remove_rule_by_id(rules, "final_answer_required")
            if part["require_final_answer"]:
                rules.append(
                    {
                        "rule_id": "final_answer_required",
                        "rule": "启用此策略时，必须包含最终答案或明确的结论；如果缺失或不完整则扣分。",
                        "max_deduction": 1,
                    }
                )


def _upsert_policy(policies: list[Any], new_policy: dict[str, Any]) -> None:
    policy_id = str(new_policy.get("policy_id") or "")
    for idx, policy in enumerate(policies):
        if isinstance(policy, dict) and str(policy.get("policy_id") or "") == policy_id:
            policies[idx] = {**policy, **new_policy}
            return
    policies.append(new_policy)


def _remove_rule_by_id(rules: list[Any], rule_id: str) -> None:
    rules[:] = [
        rule
        for rule in rules
        if not (isinstance(rule, dict) and str(rule.get("rule_id") or "") == rule_id)
    ]


def _default_core_goal(qtype: str, answer_item: dict[str, Any]) -> str:
    if qtype == "choice":
        return "选择正确的选项"
    if qtype == "fill_blank":
        return "填写正确或等价的答案"
    return "完成必要的推理或计算步骤"


def _default_required_elements(qtype: str, answer_item: dict[str, Any]) -> list[str]:
    canonical = str(answer_item.get("canonical_answer") or "").strip()
    if qtype in {"choice", "fill_blank"} and canonical:
        return [canonical]
    return ["合理的推理过程", "正确的结论"]


def _force_step_total(part: dict[str, Any]) -> None:
    steps = part.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    part_score = _safe_float(part.get("part_score"), 0.0)
    step_sum = sum(_safe_float(step.get("step_score"), 0.0) for step in steps if isinstance(step, dict))
    diff = round(part_score - step_sum, 2)
    if abs(diff) > 0.01 and isinstance(steps[-1], dict):
        steps[-1]["step_score"] = round(_safe_float(steps[-1].get("step_score"), 0.0) + diff, 2)


def _force_part_total(question: dict[str, Any]) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        return
    max_score = _safe_float(question.get("max_score"), 0.0)
    part_sum = sum(_safe_float(part.get("part_score"), 0.0) for part in parts if isinstance(part, dict))
    diff = round(max_score - part_sum, 2)
    if abs(diff) > 0.01 and isinstance(parts[-1], dict):
        parts[-1]["part_score"] = round(_safe_float(parts[-1].get("part_score"), 0.0) + diff, 2)
        _force_step_total(parts[-1])


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.replace(",", ";").split(";") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _method_variants(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, dict):
            result.append(
                {
                    "name": str(item.get("name") or f"鏂规硶{idx}"),
                    "outline": str(item.get("outline") or item.get("description") or ""),
                }
            )
        elif str(item).strip():
            result.append({"name": f"鏂规硶{idx}", "outline": str(item).strip()})
    return result


def _normalize_question_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "comprehensive"
    if raw in {"fill_blank", "fill-in", "fill_in_blank", "blank", "short_answer", "short-answer"}:
        return "fill_blank"
    if raw in {"choice", "single_choice", "multiple_choice", "select", "option"}:
        return "choice"
    if raw in {"calculation", "calculate", "solve", "solution", "problem_solving"}:
        return "calculation"
    if raw in {"proof", "prove"}:
        return "proof"
    if raw in {"comprehensive", "subjective", "constructed_response"}:
        return "comprehensive"
    if any(token in raw for token in ["choice", "select", "option"]):
        return "choice"
    if any(token in raw for token in ["blank", "short_answer", "fill"]):
        return "fill_blank"
    if any(token in raw for token in ["proof", "prove"]):
        return "proof"
    if any(token in raw for token in ["calculation", "calculate", "solve", "solution"]):
        return "calculation"
    return "comprehensive"


def _needs_objective_repair(payload: dict[str, Any], doc_text: str) -> bool:
    questions = payload.get("rubric", {}).get("questions", [])
    qtypes = {
        str(q.get("question_type"))
        for q in questions
        if isinstance(q, dict)
    } if isinstance(questions, list) else set()
    text = str(doc_text or "")
    doc_has_choice = any(re.search(r"A.{0,80}B.{0,80}C.{0,80}D", line) for line in text.splitlines())
    doc_has_fill = bool(re.search(r"_{2,}|[ \t]{3,}|　{1,}|（\s*）|\(\s*\)", text))
    if doc_has_choice and "choice" not in qtypes:
        return True
    if doc_has_fill and "fill_blank" not in qtypes:
        return True
    return False


def _build_objective_repair_prompt(doc_text: str, bad_payload: dict[str, Any]) -> str:
    return (
        "你上一次生成的评分标准漏掉了选择题/填空题或客观题，这是严重错误。\n"
        "请重新从 Word 原文中生成完整评分标准，必须包含全部题目。\n\n"
        "修复要求：\n"
        "1) 不要只输出 Q12/Q13 等解答题；必须补齐选择题、填空题、客观题。\n"
        "2) 选择题 question_type=choice；填空题 question_type=fill_blank。\n"
        "3) 客观题也必须在 rubric.questions 和 answer_key.questions 中逐题列出。\n"
        "4) 如果题干前半部分是题目、后半部分是答案解析，请把二者按题号匹配。\n"
        "5) 如果分值没有逐题标明，请根据总分/题型合理分配，并在 meta.warnings 写明。\n"
        "6) 不能再输出“选择题与填空题未包含在详细评分逻辑中”或类似 warning。\n\n"
        "输出格式仍必须与原要求完全一致：严格 JSON 对象，包含 rubric、answer_key、meta。\n\n"
        f"Word 原文：\n{doc_text}\n\n"
        f"上一次错误输出（供你定位遗漏，不要照抄）：\n{json.dumps(bad_payload, ensure_ascii=False)}"
    )


def _build_manual_structure_refinement_prompt(payload: dict[str, Any]) -> str:
    return (
        "您正在对教师编辑过的评分标准进行精修/对齐。请仅返回严格的 JSON 数据。\n"
        "硬性要求：\n"
        "1) 必须保留每一个已有的 rubric.questions[].question_id。\n"
        "2) 必须保留每一个已有的 parts[].part_id；绝对不能合并、删除或修改教师创建 the parts 部分。\n"
        "3) answer_key.questions[].parts 必须通过 part_id 与 rubric 中的 parts 保持一致对齐。\n"
        "4) 补全缺失的答案、accepted_forms、解析、步骤分、证明扣分项和证据链规则。\n"
        "5) 保持整张试卷总分 total_score 和各题 max_score 的总和精确等于 100。\n"
        "6) 对于证明题或计算解答题，按证据步骤步骤分进行细化，并保守地给与仅有答案无过程的得分限制。\n\n"
        f"当前评分标准 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def validate_generated_config(payload: dict[str, Any]) -> None:
    normalize_generated_config_schema(payload)
    force_payload_total_score(payload, target_total=100.0)
    if not isinstance(payload, dict):
        raise ValueError("Generated config must be a JSON object")
    for top_key in ["rubric", "answer_key", "meta"]:
        if top_key not in payload:
            raise ValueError(f"Generated config is missing top-level field: {top_key}")

    rubric = payload["rubric"]
    answer_key = payload["answer_key"]
    meta = payload["meta"]
    if not isinstance(rubric, dict):
        raise ValueError("rubric must be an object")
    if not isinstance(answer_key, dict):
        raise ValueError("answer_key must be an object")
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object")

    rubric_questions = rubric.get("questions")
    answer_questions = answer_key.get("questions")
    if not isinstance(rubric_questions, list) or not rubric_questions:
        raise ValueError("rubric.questions must be a non-empty list")
    if not isinstance(answer_questions, list) or not answer_questions:
        raise ValueError("answer_key.questions must be a non-empty list")

    rubric_q_map: dict[str, dict[str, Any]] = {}
    scores_by_type: dict[str, float] = {}
    for idx, item in enumerate(rubric_questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"rubric.questions[{idx - 1}] must be an object, got {type(item).__name__}: {item!r}")
        for key in ["question_id", "question_type", "max_score", "knowledge_id", "parts"]:
            if key not in item:
                raise ValueError(f"rubric.questions[{idx - 1}] is missing field: {key}")
        qid = str(item["question_id"])
        rubric_q_map[qid] = item
        qtype = str(item.get("question_type") or "comprehensive")
        max_score = float(item["max_score"])
        if not max_score.is_integer():
            raise ValueError(f"rubric.questions[{idx - 1}].max_score must be an integer")
        if max_score > 12:
            raise ValueError(f"rubric.questions[{idx - 1}].max_score must not exceed 12")
        # 同类同分仅约束客观题（choice/fill_blank/judgement/true_false）；
        # 解答类大题（calculation/proof/comprehensive）允许各题分值不同。
        if _normalize_type(qtype) in OBJECTIVE_TYPES:
            if qtype in scores_by_type and abs(scores_by_type[qtype] - max_score) > 1e-6:
                raise ValueError(f"All questions with question_type={qtype} must use the same max_score")
            scores_by_type[qtype] = max_score

        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"rubric.questions[{idx - 1}].parts must be a non-empty list")
        part_total = 0.0
        for pidx, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] must be an object")
            for key in ["part_id", "part_score", "steps"]:
                if key not in part:
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] is missing field: {key}")
            part_score = float(part["part_score"])
            if not part_score.is_integer():
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].part_score must be an integer")
            part_total += part_score
            steps = part.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps must be a non-empty list")
            step_total = 0.0
            for sidx, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}] must be an object")
                for key in ["step_id", "step_score", "core_goal", "required_elements", "allow_alternative_methods"]:
                    if key not in step:
                        raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}] is missing field: {key}")
                step_score = float(step["step_score"])
                if not step_score.is_integer():
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}].step_score must be an integer")
                step_total += step_score
            if abs(step_total - part_score) > 1e-6:
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] step total does not equal part_score")
            if not isinstance(part.get("presentation_rules", []), list):
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].presentation_rules must be a list")
        if abs(part_total - max_score) > 1e-6:
            raise ValueError(f"rubric.questions[{idx - 1}] part total does not equal max_score")

    answer_q_map: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(answer_questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"answer_key.questions[{idx - 1}] must be an object")
        for key in ["question_id", "canonical_answer", "accepted_forms", "method_variants", "parts"]:
            if key not in item:
                raise ValueError(f"answer_key.questions[{idx - 1}] is missing field: {key}")
        qid = str(item["question_id"])
        answer_q_map[qid] = item
        if not isinstance(item["accepted_forms"], list):
            raise ValueError(f"answer_key.questions[{idx - 1}].accepted_forms must be a list")
        if not isinstance(item["method_variants"], list):
            raise ValueError(f"answer_key.questions[{idx - 1}].method_variants must be a list")
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"answer_key.questions[{idx - 1}].parts must be a non-empty list")
        for pidx, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}] must be an object")
            for key in ["part_id", "answer", "analysis", "step_milestones"]:
                if key not in part:
                    raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}] is missing field: {key}")
            if not isinstance(part["step_milestones"], list):
                raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}].step_milestones must be a list")

    if set(rubric_q_map) != set(answer_q_map):
        raise ValueError("rubric.questions and answer_key.questions must have identical question_id sets")
    total_score = sum(float(item.get("max_score") or 0) for item in rubric_q_map.values())
    if abs(total_score - 100.0) > 0.02:
        raise ValueError(f"rubric question total must be 100, got {total_score:.2f}")
    rubric["total_score"] = 100.0


    if "warnings" not in meta or not isinstance(meta["warnings"], list):
        raise ValueError("meta.warnings must exist and be a list")


def save_generated_config(upload_dir: Path, payload: dict[str, Any], ts: str) -> tuple[Path, Path]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    validate_generated_config(payload)
    rubric_path = upload_dir / f"rubric_{ts}.json"
    answer_key_path = upload_dir / f"answer_key_{ts}.json"

    with rubric_path.open("w", encoding="utf-8") as f:
        json.dump(payload["rubric"], f, ensure_ascii=False, indent=2)

    with answer_key_path.open("w", encoding="utf-8") as f:
        json.dump(payload["answer_key"], f, ensure_ascii=False, indent=2)

    return rubric_path, answer_key_path


def _build_generation_prompt(doc_text: str) -> str:
    return (
        "你是中学数学教研评分设计助手。\n"
        "请从给定Word文本中抽取‘题目、分值、答案与解析’，并生成可执行评分标准。\n"
        "核心目标：支持填空题等价答案判分、解答题/证明题按证明义务扣分、不同正确解法给分。\n"
        "硬性总分：本系统所有考试批改统一按 100 分制设计。rubric.total_score 必须等于 100，所有题目 max_score 之和必须等于 100；若原卷不是 100 分制，请按原始分值比例换算。\n"
        "单题上限：每一道题 question.max_score 不能超过 12 分，即不能超过总分的 12%。解答题可以拆成多个小问 parts 分别赋分，但整道题 max_score 仍不得超过 12。\n"
        "硬性赋分：所有 max_score、part_score、step_score、proof_obligations.weight、deduction_policy.max_deduction 都必须是整数，不能出现 2.5、3.33 这类小数。\n"
        "同类同分（仅客观题）：相同 question_type 的客观题必须分值完全相同。例如所有 choice 题同分，所有 fill_blank 题同分，不能出现有的选择题3分、有的选择题4分。解答类大题（calculation/proof/comprehensive）不要求同类同分，可按题目难度与工作量赋予不同分值。\n"
        "分值层级：选择题(choice)单题分值必须小于或等于填空题(fill_blank)，且两者差距不要超过50%（即 choice_score >= fill_blank_score * 0.5）；choice/fill_blank 的单题分值必须小于或等于解答类题目的单题分值。\n"
        "题型细分：解答类题目不要全部写成一种类型，可按实际任务分为 calculation（计算/求解）、proof（证明）、comprehensive（一般综合解答）等多种 question_type。\n"
        "题型纠偏：只要题目要求证明、求证、说明理由、说明某结论成立、补全证明过程、判断并说明、添加条件使结论能够推出，就应标为 proof；如果题目主要要求求角度、求长度、求周长、求面积、求值、计算、化简或解方程，且不要求证明/说明理由，才标为 calculation。comprehensive 用于同时包含证明、计算、作图或开放论述的混合型题。\n"
        "若原卷分值与“100分制、整数、客观题同类同分、选择题≤填空题且差距不超过50%、客观题不高于解答题”冲突，请优先按这些规则重新设计赋分。\n"
        "特别要求：证明题/解答题不要把参考答案路径当作唯一标准；应抽象成 proof_obligations（证明义务）和 deduction_policy（扣分规则）。\n"
        "必须覆盖 Word 中出现的全部题目，包括选择题、填空题、判断题、客观题和解答题；禁止只生成大题或只生成答案解析部分。\n"
        "如果 Word 中有选择题/填空题，即使评分逻辑简单，也必须在 rubric.questions 和 answer_key.questions 中逐题列出。\n"
        "选择题/填空题不能写成“仅抽象化处理”或只放在 meta.warnings；如果答案明确，应生成 direct_answer 评分标准。\n"
        "如果信息缺失，不要臆造，把不确定项写入 meta.warnings。\n\n"
        "仅输出严格 JSON，不要 markdown。输出必须尽量压缩为单行 JSON，不要漂亮打印，不要添加解释性文字，避免输出被截断。\n"
        "可以省略空数组字段中的多余说明，但 JSON 顶层和必需字段必须完整闭合。\n"
        "JSON 结构必须是:\n"
        "{\n"
        "  \"rubric\": {\n"
        "    \"exam_title\": \"...\",\n"
        "    \"total_score\": 100,\n"
        "    \"questions\": [\n"
        "      {\n"
        "        \"question_id\": \"Q1\",\n"
        "        \"question_type\": \"fill_blank|choice|calculation|proof|comprehensive\",\n"
        "        \"max_score\": 8,\n"
        "        \"knowledge_id\": \"C2_01\",\n"
        "        \"knowledge_ids\": [\"C2_01\", \"C2_03\"],\n"
        "        \"knowledge_points\": [\n"
        "          {\"knowledge_id\": \"C2_01\", \"knowledge_name\": \"主要知识点名称\"},\n"
        "          {\"knowledge_id\": \"C2_03\", \"knowledge_name\": \"相关知识点名称\"}\n"
        "        ],\n"
        "        \"knowledge_name\": \"简短知识点名称，例如三角形内角和/全等三角形判定/等价代数化简\",\n"
        "        \"stem_summary\": \"题干摘要，用一句话概括本题考查内容\",\n"
        "        \"grading_mode\": \"direct_answer|deductive_obligation\",\n"
        "        \"require_final_answer\": false,\n"
        "        \"answer_only_max_score\": 2,\n"
        "        \"proof_obligations\": [\n"
        "          {\"obligation_id\": \"O1\", \"description\": \"需要证明/建立的关键数学义务\", \"weight\": 3, \"acceptable_evidence\": [\"等价完成方式\"]}\n"
        "        ],\n"
        "        \"deduction_policy\": [\n"
        "          {\"issue\": \"关键逻辑断裂/条件缺失/循环论证\", \"max_deduction\": 2, \"severity\": \"minor|major|fatal\"}\n"
        "        ],\n"
        "        \"parts\": [\n"
        "          {\n"
        "            \"part_id\": \"Q1(1)\",\n"
        "            \"part_score\": 4,\n"
        "            \"steps\": [\n"
        "              {\n"
        "                \"step_id\": \"S1\",\n"
        "                \"step_score\": 2,\n"
        "                \"core_goal\": \"...\",\n"
        "                \"required_elements\": [\"...\"],\n"
        "                \"allow_alternative_methods\": true\n"
        "              }\n"
        "            ],\n"
        "            \"presentation_rules\": [\n"
        "              {\"rule\": \"过程规范/结论完整\", \"max_deduction\": 1}\n"
        "            ]\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    ]\n"
        "  },\n"
        "  \"answer_key\": {\n"
        "    \"questions\": [\n"
        "      {\n"
        "        \"question_id\": \"Q1\",\n"
        "        \"canonical_answer\": \"...\",\n"
        "        \"accepted_forms\": [\"...\", \"...\"],\n"
        "        \"method_variants\": [\n"
        "          {\"name\": \"方法A\", \"outline\": \"...\"},\n"
        "          {\"name\": \"方法B\", \"outline\": \"...\"}\n"
        "        ],\n"
        "        \"parts\": [\n"
        "          {\n"
        "            \"part_id\": \"Q1(1)\",\n"
        "            \"answer\": \"...\",\n"
        "            \"analysis\": \"...\",\n"
        "            \"step_milestones\": [\"关键中间结论1\", \"关键中间结论2\"]\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    ]\n"
        "  },\n"
        "  \"meta\": {\n"
        "    \"source\": \"word_docx\",\n"
        "    \"warnings\": [\"...\"]\n"
        "  }\n"
        "}\n\n"
        "硬性要求：\n"
        "1) question_id 在 rubric 与 answer_key 中一一对应。\n"
        "1.1) knowledge_id 不能只有编号；必须同时给出 knowledge_name 或 stem_summary，方便教师在知识图谱中看懂考查内容。\n"
        "1.2) 一道题可以涉及多个知识点。若题目同时考查多个数学概念，必须输出 knowledge_ids 数组和 knowledge_points 数组；knowledge_id 只作为主知识点，取 knowledge_ids 的第一项。\n"
        "2) max_score、part_score、step_score 必须为整数且层级总分一致；不得输出小数。\n"
        "2.1) 整张试卷总分必须严格为 100 分，不能返回 10 分、120 分或其他总分。\n"
        "2.2) 相同 question_type 的客观题（choice/fill_blank）max_score 必须完全一致；解答类大题（calculation/proof/comprehensive）允许不同分值。\n"
        "2.3) choice 的 max_score 必须小于或等于 fill_blank，且不得低于 fill_blank 的 50%；choice/fill_blank 的 max_score 必须小于或等于 calculation/proof/comprehensive 的 max_score。\n"
        "2.4) 任意 question.max_score 必须小于或等于 12；若大题有多问，请在 parts 中拆分小问分值，不要让整题超过 12。\n"
        "3) 对选择题/填空题，question_type 必须分别是 choice 或 fill_blank，parts 可只有一个，steps 可只有一个 direct-answer 步骤。\n"
        "4) 对解答题，必须按题目特征细分为 calculation/proof/comprehensive，必须有 parts 与 steps，不能只给最终答案；同时必须给出 proof_obligations 与 deduction_policy。明确要求证明/求证/说明结论成立的题使用 proof；主要求数值、角度、长度、面积、化简或方程结果的题使用 calculation。\n"
        "4.1) 对 proof 与 calculation，默认 require_final_answer=false，不因未额外写“答”单独扣分；对 comprehensive，默认 require_final_answer=true，未写最终答/结论完整性可小扣分。若题目本身明确要求写结论，可按题意调整。\n"
        "4.2) 对 proof/calculation/comprehensive，必须给出 answer_only_max_score：学生只写最终答案但没有有效过程时最多得分。该分值只能是少量结论分，不得超过本题 max_score 的 30%。\n"
        "5) 对选择题/填空题/direct_answer 题，必须执行全对全错规则：学生答案与 canonical_answer 或 accepted_forms 等价才给该题/该空满分；不符合答案及等价答案时该评分单元必须给 0 分，不得因为接近、猜测方向正确或过程片段给一半分。\n"
        "5.1) 对填空题，必须尽可能提供 accepted_forms（等价表达），例如 1/4、0.25、25% 这类数值等价形式，或 OB=OC、OC=OB、BO=CO 这类几何关系等价写法。\n"
        "6) 对可接受的不同解法，在 method_variants 中列出。\n"
        "7) 若题号或分值不确定，使用连续编号并写入 warnings，但不能因此漏掉题目。\n"
        "8) meta.warnings 不能出现“选择题与填空题未包含在详细评分逻辑中”这类遗漏；遇到这种情况必须回到 questions 中补齐。\n\n"
        "证明题/解答题生成准则：\n"
        "- 答案结论只作为小部分得分依据，过程分主要来自 proof_obligations 是否完成。\n"
        "- 证明题和纯计算题通常不要求额外写“答”；一般综合解答题可要求结论完整，但这应作为 presentation_rules 的小扣分项，不应替代过程分判断。\n"
        "- 只写最终答案但没有有效过程时，只能获得 answer_only_max_score 以内的结论分；不能获得主要过程分。\n"
        "- proof_obligations 描述学生必须完成的数学责任，例如建立辅助条件、证明全等/相似、推出角度/线段关系、说明定理前提等。\n"
        "- deduction_policy 描述扣分项，例如缺少前提、跳步严重、逻辑循环、结论与过程断裂、符号/对象指代不清。\n"
        "- steps 可以对应证明义务的评分权重，但不能要求学生过程与参考答案逐句一致。\n\n"
        f"Word原文:\n{doc_text}"
    )



def generate_grading_config_from_text(
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
) -> dict[str, Any]:
    if report:
        report(0.20, "Submit text prompt", "Submitted text prompt; waiting for model to parse all questions...")
    
    prompt = _build_generation_prompt(doc_text)
    prompt = "这是一份试卷及其标准答案的提取文本。请你仔细阅读文本内容，提取出所有题目的评分标准（题号、分值、步骤分、标准答案等）。\n\n" + prompt

    payload = llm_client.json_from_text(prompt, model=model_name, extra_kwargs={"timeout": 300.0})
    
    try:
        validate_generated_config(payload)
    except Exception:
        _dump_failed_generated_payload(payload)
        raise
        
    if _needs_objective_repair(payload, doc_text):
        repair_prompt = _build_objective_repair_prompt(doc_text, payload)
        payload = llm_client.json_from_text(repair_prompt, model=model_name)
        try:
            validate_generated_config(payload)
        except Exception:
            pass

    return payload


def generate_grading_config_from_images(
    image_blobs: list[bytes],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
) -> dict[str, Any]:
    if report:
        report(0.20, "全卷图像解析", f"发送 {len(image_blobs)} 张图片等待全局解析（耗时较长）...")
    prompt = (
        "这是一份试卷及其标准答案的高清原图。请你仔细阅读图片内容，提取出所有题目的评分标准"
        "（题号、分值、步骤分、标准答案等）。请务必精准识别图片中的 LaTeX 公式、表格和几何图形。\n\n"
    ) + _build_generation_prompt(doc_text)

    payload = llm_client.json_from_images_with_options(
        prompt, image_blobs, model=model_name,
        extra_kwargs={"timeout": 600.0},
        use_config_client=True,
    )
    
    try:
        validate_generated_config(payload)
    except Exception:
        _dump_failed_generated_payload(payload)
        raise
        
    if _needs_objective_repair(payload, doc_text):
        repair_prompt = _build_objective_repair_prompt(doc_text, payload)
        payload = llm_client.json_from_text(repair_prompt, model=model_name, extra_kwargs={"timeout": 300.0})
        try:
            validate_generated_config(payload)
        except Exception:
            pass

    return payload

