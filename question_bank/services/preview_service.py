from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from question_bank.database.paths import project_data_root
from question_bank.services.asset_path_service import resolve_question_bank_asset_path
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import QuestionBankWriteService

LOGGER = logging.getLogger(__name__)
ANSWER_HEADING_PATTERN = re.compile(r"(参考答案|答案与解析|试题答案|答案解析|解析|评分标准)")


@dataclass(frozen=True)
class PreviewResult:
    question_id: int
    question_status: str
    answer_status: str
    message: str = ""


@dataclass(frozen=True)
class _Line:
    page_index: int
    text: str
    bbox: tuple[float, float, float, float]


def generate_question_previews(
    db_path: str | Path,
    question_ids: list[int],
    *,
    output_root: str | Path | None = None,
) -> list[PreviewResult]:
    data_root = project_data_root()
    reader = QuestionBankReadService(Path(db_path), data_root=data_root)
    writer = QuestionBankWriteService(Path(db_path), data_root=data_root)
    output_dir = Path(output_root or data_root / "question_bank" / "previews")
    results: list[PreviewResult] = []
    for question_id in question_ids:
        question = reader.get_question_for_preview(int(question_id))
        if question is None:
            results.append(PreviewResult(int(question_id), "missing", "missing", "题目不存在"))
            continue
        try:
            result = _generate_one(writer, question, output_dir)
        except Exception as exc:
            LOGGER.exception("Failed to generate preview for question %s", question_id)
            _save_failed(writer, int(question_id), question, "question", str(exc))
            _save_failed(writer, int(question_id), question, "answer", str(exc))
            result = PreviewResult(int(question_id), "failed", "failed", str(exc))
        results.append(result)
    return results


def _generate_one(
    service: QuestionBankWriteService,
    question: dict[str, Any],
    output_dir: Path,
) -> PreviewResult:
    source_file = _resolve_source_file(question)
    stored_source_file = str(question.get("source_file") or source_file)
    if not source_file.exists():
        message = f"源文件不存在：{source_file}"
        _save_failed(service, int(question["id"]), question, "question", message)
        _save_failed(service, int(question["id"]), question, "answer", message)
        return PreviewResult(int(question["id"]), "failed", "failed", message)

    with _preview_pdf(source_file) as pdf_path:
        if pdf_path is None:
            message = "暂不支持该文件预览，或本机未找到可用的 DOCX 转 PDF 工具"
            _save_failed(service, int(question["id"]), question, "question", message)
            _save_failed(service, int(question["id"]), question, "answer", message)
            return PreviewResult(int(question["id"]), "unsupported", "unsupported", message)
        with fitz.open(pdf_path) as document:
            lines = _extract_lines(document)
            answer_heading = _answer_heading_position(lines)
            question_status = _render_preview(
                service,
                document,
                question,
                lines,
                output_dir,
                preview_type="question",
                source_file=stored_source_file,
                answer_heading=answer_heading,
            )
            answer_status = _render_preview(
                service,
                document,
                question,
                lines,
                output_dir,
                preview_type="answer",
                source_file=stored_source_file,
                answer_heading=answer_heading,
            )
    return PreviewResult(int(question["id"]), question_status, answer_status)


def _resolve_source_file(
    question: dict[str, Any],
    *,
    data_root: str | Path | None = None,
) -> Path:
    return resolve_question_bank_asset_path(
        question.get("source_file"),
        data_root=data_root,
        search_subdirs=("question_bank/raw_papers",),
    )


class _preview_pdf:
    def __init__(self, source_file: Path) -> None:
        self.source_file = source_file
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path | None:
        suffix = self.source_file.suffix.lower()
        if suffix == ".pdf":
            return self.source_file
        if suffix != ".docx":
            return None
        return self._convert_docx()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _convert_docx(self) -> Path | None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        output_dir = Path(self.temp_dir.name)
        pdf_path = output_dir / f"{self.source_file.stem}.pdf"
        if os.name == "nt":
            word_pdf = _convert_docx_with_word(self.source_file, pdf_path)
            if word_pdf is not None:
                return word_pdf

        executable = _find_soffice()
        if executable is None:
            return None
        completed = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(self.source_file),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if completed.returncode != 0:
            LOGGER.warning("DOCX preview conversion failed: %s", completed.stderr or completed.stdout)
            return None
        return pdf_path if pdf_path.exists() else None


def _convert_docx_with_word(source_file: Path, pdf_path: Path) -> Path | None:
    try:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except Exception:
        return None

    word = None
    document = None
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source_file.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
        )
        document.ExportAsFixedFormat(
            OutputFileName=str(pdf_path),
            ExportFormat=17,
            OpenAfterExport=False,
            OptimizeFor=0,
            Range=0,
            Item=0,
            IncludeDocProps=True,
            KeepIRM=True,
            CreateBookmarks=1,
            DocStructureTags=True,
            BitmapMissingFonts=True,
            UseISO19005_1=False,
        )
        return pdf_path if pdf_path.exists() else None
    except Exception as exc:
        LOGGER.warning("DOCX preview conversion with Microsoft Word failed: %s", exc)
        return None
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _find_soffice() -> str | None:
    for candidate in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _extract_lines(document: fitz.Document) -> list[_Line]:
    lines: list[_Line] = []
    for page_index, page in enumerate(document):
        payload = page.get_text("dict")
        for block in payload.get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if not text:
                    continue
                bbox = tuple(float(value) for value in line.get("bbox", (0, 0, page.rect.width, 0)))
                lines.append(_Line(page_index=page_index, text=text, bbox=bbox))  # type: ignore[arg-type]
    return lines


def _answer_heading_position(lines: list[_Line]) -> tuple[int, float] | None:
    for line in lines:
        if ANSWER_HEADING_PATTERN.search(line.text):
            return line.page_index, line.bbox[1]
    return None


def _render_preview(
    service: QuestionBankWriteService,
    document: fitz.Document,
    question: dict[str, Any],
    lines: list[_Line],
    output_dir: Path,
    *,
    preview_type: str,
    source_file: str,
    answer_heading: tuple[int, float] | None,
) -> str:
    number = str(question.get("question_number") or "").strip()
    if not number:
        _save_failed(service, int(question["id"]), question, preview_type, "题号为空")
        return "failed"
    start_line = _find_marker_line(lines, number, preview_type=preview_type, answer_heading=answer_heading)
    if start_line is None:
        if preview_type == "answer" and not str(question.get("answer_text") or "").strip():
            _save_failed(service, int(question["id"]), question, preview_type, "暂无答案文本")
            return "missing"
        _save_failed(service, int(question["id"]), question, preview_type, "未能定位题号")
        return "needs_review"
    end_line = _find_next_marker_line(lines, start_line, answer_heading=answer_heading, preview_type=preview_type)
    page = document[start_line.page_index]
    page_rect = page.rect
    y0 = max(0, start_line.bbox[1] - 8)
    y1 = page_rect.height
    if end_line is not None and end_line.page_index == start_line.page_index:
        y1 = max(y0 + 24, end_line.bbox[1] - 6)
    elif preview_type == "question" and answer_heading and answer_heading[0] == start_line.page_index:
        y1 = min(y1, max(y0 + 24, answer_heading[1] - 6))
    clip = fitz.Rect(0, y0, page_rect.width, min(page_rect.height, y1))
    output_path = _preview_path(output_dir, int(question["id"]), preview_type)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
    pixmap.save(str(output_path))
    service.save_question_preview(
        int(question["id"]),
        preview_type=preview_type,
        source_file=source_file,
        page_number=start_line.page_index + 1,
        image_path=str(output_path),
        bbox={"x0": clip.x0, "y0": clip.y0, "x1": clip.x1, "y1": clip.y1},
        status="ready",
    )
    return "ready"


def _find_marker_line(
    lines: list[_Line],
    number: str,
    *,
    preview_type: str,
    answer_heading: tuple[int, float] | None,
) -> _Line | None:
    pattern = _marker_pattern(number)
    candidates = [line for line in lines if pattern.match(line.text)]
    if preview_type == "answer":
        if answer_heading is not None:
            candidates = [line for line in candidates if _is_after(line, answer_heading)]
        return candidates[0] if candidates else None
    if answer_heading is not None:
        before_answer = [line for line in candidates if not _is_after(line, answer_heading)]
        if before_answer:
            return before_answer[0]
    return candidates[0] if candidates else None


def _find_next_marker_line(
    lines: list[_Line],
    start_line: _Line,
    *,
    answer_heading: tuple[int, float] | None,
    preview_type: str,
) -> _Line | None:
    for line in lines:
        if not _is_after(line, (start_line.page_index, start_line.bbox[1] + 1)):
            continue
        if preview_type == "question" and answer_heading and _is_after(line, answer_heading):
            return None
        if re.match(r"^\s*\d{1,3}\s*[\.、．]\s*", line.text):
            return line
    return None


def _marker_pattern(number: str) -> re.Pattern[str]:
    escaped = re.escape(number)
    return re.compile(rf"^\s*(?:第\s*)?{escaped}\s*(?:[\.、．]|题|\)|）)\s*")


def _is_after(line: _Line, position: tuple[int, float]) -> bool:
    page_index, y = position
    return line.page_index > page_index or (line.page_index == page_index and line.bbox[1] >= y)


def _save_failed(
    service: QuestionBankWriteService,
    question_id: int,
    question: dict[str, Any],
    preview_type: str,
    message: str,
) -> None:
    service.save_question_preview(
        question_id,
        preview_type=preview_type,
        source_file=str(question.get("source_file") or ""),
        status="failed",
        message=message,
    )


def _preview_path(output_dir: Path, question_id: int, preview_type: str) -> Path:
    return output_dir / f"question_{question_id}" / f"{preview_type}.png"


def preview_recommendation(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": int(question["id"]),
        "source_paper": _source_label(question),
        "question_number": question.get("question_number") or "",
        "knowledge_points": [
            tag["tag_value"]
            for tag in question.get("tags", [])
            if tag.get("tag_type") in ("knowledge_point", "skill")
            and tag.get("tag_value")
        ],
        "difficulty": question.get("difficulty") or "",
        "recommend_reason": "题库手动组卷",
        "suggested_order": 0,
        "training_stage": "基础回补",
    }


def _source_label(question: dict[str, Any]) -> str:
    parts = [question.get("year"), question.get("district"), question.get("exam_type")]
    label = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return label or str(question.get("paper_title") or question.get("source_file") or "")


__all__ = ["PreviewResult", "generate_question_previews", "preview_recommendation"]
