from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from question_bank.database.paths import project_data_root, question_bank_db_path
from question_bank.database.schema import connect
from question_bank.importers.batch_importer import (
    BatchImportResult,
    PaperMetadata,
    ScannedPaper,
    import_scanned_papers,
    infer_metadata_from_filename,
)
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import AITaggingService, is_auto_saveable_result
from question_bank.services.question_service import QuestionService
from question_bank.services.source_question_link_service import SourceQuestionLinkService


@dataclass(frozen=True, slots=True)
class GradingPaperIntakeResult:
    saved_file: Path
    import_result: BatchImportResult
    tagged_questions: int = 0
    failed_tagging: int = 0
    confirmed_links: int = 0
    suggested_links: int = 0
    unresolved_links: int = 0


def save_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    if not content:
        raise ValueError("上传的试卷文件内容为空，无法同步到题库。")
    destination_dir = Path(raw_papers_dir) if raw_papers_dir is not None else project_data_root() / "question_bank" / "raw_papers"
    destination_dir.mkdir(parents=True, exist_ok=True)
    source_name = Path(filename or "grading_paper.docx").name
    stem = Path(source_name).stem or "grading_paper"
    suffix = Path(source_name).suffix.lower() or ".docx"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"{stem}_{timestamp}{suffix}"
    destination.write_bytes(content)
    return destination


def intake_grading_paper_to_question_bank(
    *,
    source_file: str | Path,
    db_path: str | Path | None = None,
    metadata: PaperMetadata | None = None,
    run_ai_tagging: bool = True,
    ai_service: AITaggingService | None = None,
    tagging_max_workers: int | None = None,
    tagging_requests_per_minute: int | None = None,
    tagging_progress_callback: Callable[[int, int, int, Any], None] | None = None,
    grading_session_id: str | int | None = None,
    grading_source_questions: list[dict[str, Any]] | None = None,
) -> GradingPaperIntakeResult:
    paper_path = Path(source_file)
    database_path = Path(db_path) if db_path is not None else question_bank_db_path()
    inferred = infer_metadata_from_filename(paper_path.name)
    merged_metadata = _merge_metadata(metadata or PaperMetadata(exam_type="阶段练习"), inferred)
    import_result = import_scanned_papers(
        [ScannedPaper(source_file=str(paper_path), file_type=paper_path.suffix.lower().lstrip("."), metadata=inferred)],
        database_path,
        default_metadata=merged_metadata,
    )

    questions = _questions_for_source(database_path, str(paper_path))
    tagged_questions = 0
    failed_tagging = 0
    if run_ai_tagging and import_result.question_count:
        service = QuestionService(database_path)
        contexts = {int(item["id"]): _tagging_context(item) for item in questions}
        tagger = ai_service or AITaggingService()
        results = tagger.analyze_questions(
            contexts,
            max_workers=tagging_max_workers,
            requests_per_minute=tagging_requests_per_minute,
            progress_callback=tagging_progress_callback,
        )
        for question_id, result in results.items():
            if is_auto_saveable_result(result):
                if service.save_tag_analysis(
                    question_id,
                    result.analysis,
                    model_name=result.model_name,
                    confidence=result.analysis.confidence,
                ):
                    tagged_questions += 1
                else:
                    failed_tagging += 1
            else:
                failed_tagging += 1

    link_summary = {"confirmed": 0, "suggested": 0, "unresolved": 0}
    if grading_session_id is not None and grading_source_questions:
        link_summary = SourceQuestionLinkService(database_path).link_questions_for_session(
            grading_session_id=grading_session_id,
            source_questions=grading_source_questions,
        )

    return GradingPaperIntakeResult(
        saved_file=paper_path,
        import_result=import_result,
        tagged_questions=tagged_questions,
        failed_tagging=failed_tagging,
        confirmed_links=link_summary["confirmed"],
        suggested_links=link_summary["suggested"],
        unresolved_links=link_summary["unresolved"],
    )


def copy_and_intake_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    raw_papers_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    metadata: PaperMetadata | None = None,
    run_ai_tagging: bool = True,
    ai_service: AITaggingService | None = None,
    tagging_max_workers: int | None = None,
    tagging_requests_per_minute: int | None = None,
    tagging_progress_callback: Callable[[int, int, int, Any], None] | None = None,
    grading_session_id: str | int | None = None,
    grading_source_questions: list[dict[str, Any]] | None = None,
) -> GradingPaperIntakeResult:
    saved = save_uploaded_grading_paper(filename=filename, content=content, raw_papers_dir=raw_papers_dir)
    try:
        return intake_grading_paper_to_question_bank(
            source_file=saved,
            db_path=db_path,
            metadata=metadata,
            run_ai_tagging=run_ai_tagging,
            ai_service=ai_service,
            tagging_max_workers=tagging_max_workers,
            tagging_requests_per_minute=tagging_requests_per_minute,
            tagging_progress_callback=tagging_progress_callback,
            grading_session_id=grading_session_id,
            grading_source_questions=grading_source_questions,
        )
    except Exception:
        if saved.exists() and saved.stat().st_size == 0:
            saved.unlink(missing_ok=True)
        raise


def copy_existing_grading_paper_to_raw_dir(
    source_file: str | Path,
    *,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    source = Path(source_file)
    destination_dir = Path(raw_papers_dir) if raw_papers_dir is not None else project_data_root() / "question_bank" / "raw_papers"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = destination_dir / f"{source.stem}_{timestamp}{source.suffix}"
    shutil.copy2(source, destination)
    return destination


def _questions_for_source(db_path: Path, source_file: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT q.*, p.year, p.district, p.exam_type, p.grade, p.semester
            FROM questions q
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE COALESCE(q.is_deleted, 0) = 0
              AND q.source_file = ?
            ORDER BY q.id ASC
            """,
            (source_file,),
        ).fetchall()
    return [dict(row) for row in rows]


def _tagging_context(question: dict[str, Any]) -> TaggingContext:
    return TaggingContext(
        question_text=str(question.get("question_text") or ""),
        answer_text=str(question.get("answer_text") or ""),
        question_number=str(question.get("question_number") or ""),
        question_type=str(question.get("question_type") or ""),
        grade=str(question.get("grade") or ""),
        semester=str(question.get("semester") or ""),
        exam_type=str(question.get("exam_type") or ""),
        district=str(question.get("district") or ""),
    )


def _merge_metadata(primary: PaperMetadata, fallback: PaperMetadata) -> PaperMetadata:
    return PaperMetadata(
        year=primary.year or fallback.year,
        province=primary.province or fallback.province,
        city=primary.city or fallback.city,
        district=primary.district or fallback.district,
        exam_type=primary.exam_type or fallback.exam_type,
        grade=primary.grade or fallback.grade,
        semester=primary.semester or fallback.semester,
        textbook_version=primary.textbook_version or fallback.textbook_version,
    )
