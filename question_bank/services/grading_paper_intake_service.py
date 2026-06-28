from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from question_bank.database.paths import question_bank_db_path
from question_bank.database.schema import connect
from question_bank.importers.batch_importer import (
    BatchImportResult,
    PaperMetadata,
    ScannedPaper,
    import_scanned_papers,
    infer_metadata_from_filename,
)
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingService,
    TaggingRequestEvent,
    is_auto_saveable_result,
)
from question_bank.services.asset_path_service import resolve_question_bank_asset_path
from question_bank.services.question_service import CORE_ANALYSIS_TAG_TYPES, QuestionService
from question_bank.services.source_paper_archive_service import (
    ArchivedSourcePaper,
    archive_source_bytes,
    archive_source_paper,
)
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
    imported_question_total: int = 0
    request_count: int = 0
    failed_question_ids: tuple[str, ...] = ()
    unresolved_question_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GradingPaperWorkflowEvent:
    stage: str
    completed: int
    total: int
    request_count: int = 0
    failed_questions: int = 0
    question_id: str = ""


def archive_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    return archive_source_bytes(
        filename=filename,
        content=content,
        data_root=data_root,
        raw_papers_dir=raw_papers_dir,
    )


def save_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    return archive_uploaded_grading_paper(
        filename=filename,
        content=content,
        raw_papers_dir=raw_papers_dir,
    ).physical_path


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
    workflow_progress_callback: Callable[[GradingPaperWorkflowEvent], None] | None = None,
    grading_session_id: str | int | None = None,
    grading_source_questions: list[dict[str, Any]] | None = None,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> GradingPaperIntakeResult:
    paper_path = Path(source_file)
    database_path = Path(db_path) if db_path is not None else question_bank_db_path()
    inferred = infer_metadata_from_filename(paper_path.name)
    merged_metadata = _merge_metadata(metadata or PaperMetadata(exam_type="阶段练习"), inferred)
    import_result = import_scanned_papers(
        [ScannedPaper(source_file=str(paper_path), file_type=paper_path.suffix.lower().lstrip("."), metadata=inferred)],
        database_path,
        default_metadata=merged_metadata,
        data_root=data_root,
        raw_papers_dir=raw_papers_dir,
    )

    imported_sources = {
        item.source_file
        for item in import_result.files
        if item.status != "failed" and item.source_file
    }
    questions = [
        question
        for imported_source in imported_sources
        for question in _questions_for_source(database_path, imported_source)
    ]
    request_count = 0
    failed_question_ids: list[str] = []

    def emit(
        stage: str,
        completed: int,
        *,
        failed_questions: int = 0,
        question_id: object = "",
    ) -> None:
        if workflow_progress_callback is not None:
            workflow_progress_callback(
                GradingPaperWorkflowEvent(
                    stage=stage,
                    completed=completed,
                    total=5,
                    request_count=request_count,
                    failed_questions=failed_questions,
                    question_id=str(question_id or ""),
                )
            )

    emit("import", 1)
    tagged_questions = 0
    failed_tagging = 0
    pending_questions = _questions_needing_complete_tags(database_path, questions)
    if run_ai_tagging and pending_questions:
        service = QuestionService(database_path)
        contexts = {int(item["id"]): _tagging_context(item) for item in pending_questions}
        tagger = ai_service or AITaggingService()

        def on_request(event: TaggingRequestEvent) -> None:
            nonlocal request_count
            request_count = max(request_count, int(event.request_number))
            emit("tag", 1)

        def on_tag_progress(done: int, total: int, question_id: int, result: Any) -> None:
            if tagging_progress_callback is not None:
                tagging_progress_callback(done, total, question_id, result)
            emit("tag", 1, question_id=question_id)

        results = tagger.analyze_questions(
            contexts,
            max_workers=tagging_max_workers,
            requests_per_minute=tagging_requests_per_minute,
            progress_callback=on_tag_progress,
            request_callback=on_request,
            allow_batch_fallback=False,
            quality_retry_limit=1,
            enable_review=False,
        )
        for question_id, result in results.items():
            if is_auto_saveable_result(result):
                if service.save_tag_analysis(
                    question_id,
                    result.analysis,
                    model_name=result.model_name,
                    confidence=result.analysis.confidence,
                    resolve_skills=False,
                ):
                    tagged_questions += 1
                else:
                    failed_tagging += 1
                    failed_question_ids.append(str(question_id))
            else:
                failed_tagging += 1
                failed_question_ids.append(str(question_id))
    emit("tag", 2, failed_questions=failed_tagging)
    emit("save", 3, failed_questions=failed_tagging)

    link_summary = {"confirmed": 0, "suggested": 0, "unresolved": 0}
    if grading_session_id is not None and grading_source_questions:
        direct_summary = SourceQuestionLinkService(database_path).confirm_imported_questions_for_session(
            grading_session_id=grading_session_id,
            source_questions=grading_source_questions,
            imported_bank_questions=questions,
        )
        link_summary = {
            "confirmed": int(direct_summary["confirmed"]),
            "suggested": 0,
            "unresolved": int(direct_summary["unresolved"]),
            "unresolved_question_ids": list(direct_summary["unresolved_question_ids"]),
        }
    emit("link", 4, failed_questions=failed_tagging)

    stored_source = next(iter(imported_sources), str(paper_path))
    saved_file = resolve_question_bank_asset_path(stored_source, data_root=data_root)
    return GradingPaperIntakeResult(
        saved_file=saved_file,
        import_result=import_result,
        tagged_questions=tagged_questions,
        failed_tagging=failed_tagging,
        confirmed_links=link_summary["confirmed"],
        suggested_links=link_summary["suggested"],
        unresolved_links=link_summary["unresolved"],
        imported_question_total=len(questions),
        request_count=request_count,
        failed_question_ids=tuple(failed_question_ids),
        unresolved_question_ids=tuple(link_summary.get("unresolved_question_ids", [])),
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
    workflow_progress_callback: Callable[[GradingPaperWorkflowEvent], None] | None = None,
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
            workflow_progress_callback=workflow_progress_callback,
            grading_session_id=grading_session_id,
            grading_source_questions=grading_source_questions,
            raw_papers_dir=raw_papers_dir,
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
    return archive_source_paper(
        source_file,
        raw_papers_dir=raw_papers_dir,
    ).physical_path


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


def _questions_needing_complete_tags(
    db_path: Path,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not questions:
        return []
    question_ids = [int(item["id"]) for item in questions]
    placeholders = ",".join("?" for _ in question_ids)
    with connect(db_path) as conn:
        tag_types_by_question: dict[int, set[str]] = {}
        for row in conn.execute(
            f"""
            SELECT question_id, tag_type FROM question_tags
            WHERE question_id IN ({placeholders})
              AND COALESCE(tag_value, '') <> ''
            """,
            question_ids,
        ).fetchall():
            tag_types_by_question.setdefault(int(row["question_id"]), set()).add(
                str(row["tag_type"])
            )
    required = set(CORE_ANALYSIS_TAG_TYPES)
    return [
        item
        for item in questions
        if not required.issubset(tag_types_by_question.get(int(item["id"]), set()))
    ]


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
