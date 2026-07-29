from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from question_bank.database.schema import connect
from question_bank.importers.batch_importer import (
    BatchImportResult,
    PaperMetadata,
    ScannedPaper,
    infer_metadata_from_filename,
    import_scanned_papers,
)
from question_bank.services.question_write_service import (
    QuestionBankWriteService,
    QuestionImportUploadNotFound,
)

from .execution_locks import keyed_execution_locks
from .manager import JobContext


ImportRunner = Callable[..., BatchImportResult]


def run_question_import_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    write_service: QuestionBankWriteService,
    importer: ImportRunner = import_scanned_papers,
) -> dict[str, object]:
    request_id = str(context.payload.get("request_id") or "").strip().casefold()
    lock_key = (
        f"question-import:{Path(question_bank_db_path).resolve(strict=False)}:"
        f"{request_id}"
    )
    with keyed_execution_locks(
        [lock_key],
        cancel_check=context.raise_if_cancelled,
    ):
        return _run_question_import_job_locked(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            write_service=write_service,
            importer=importer,
        )


def _run_question_import_job_locked(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    write_service: QuestionBankWriteService,
    importer: ImportRunner,
) -> dict[str, object]:
    request_id = str(context.payload.get("request_id") or "").strip().casefold()
    context.raise_if_cancelled()
    context.report(0.05, "question_import", "loading")
    try:
        resource = write_service.load_import_resource(request_id)
    except QuestionImportUploadNotFound:
        raise ValueError("question import request is unavailable") from None

    context.raise_if_cancelled()
    context.report(0.15, "question_import", "importing")
    metadata = _metadata_with_defaults(
        infer_metadata_from_filename(resource.filename),
        context.payload.get("paper_defaults"),
    )
    try:
        result = importer(
            [
                ScannedPaper(
                    source_file=str(resource.source_path),
                    file_type=resource.suffix.lstrip("."),
                    metadata=metadata,
                    title=Path(resource.filename).stem,
                )
            ],
            Path(question_bank_db_path),
            data_root=Path(data_root),
        )
    except Exception:
        raise RuntimeError("question import failed") from None
    context.raise_if_cancelled()

    successful_sources = [
        item.source_file
        for item in result.files
        if item.status != "failed" and str(item.source_file or "").strip()
    ]
    question_ids = _active_question_ids(Path(question_bank_db_path), successful_sources)
    context.report(0.9, "question_import", "indexing")
    failed_count = int(result.failed_files)
    outcome = "complete" if failed_count == 0 else (
        "partial" if question_ids else "failed"
    )
    context.report(1.0, "question_import", outcome)
    return {
        "request_id": resource.request_id,
        "outcome": outcome,
        "imported_papers": int(result.imported_papers),
        "question_count": len(question_ids),
        "failed_count": failed_count,
        "successful_question_ids": question_ids,
        "failed_question_ids": [],
        "failure_category": "import" if failed_count else "",
        "retryable": bool(failed_count),
    }


def _active_question_ids(db_path: Path, source_files: list[str]) -> list[int]:
    clean_sources = sorted({str(item) for item in source_files if str(item).strip()})
    if not clean_sources or not db_path.exists():
        return []
    placeholders = ",".join("?" for _ in clean_sources)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id FROM questions
            WHERE COALESCE(is_deleted, 0) = 0
              AND source_file IN ({placeholders})
            ORDER BY id
            """,
            clean_sources,
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _metadata_with_defaults(
    inferred: PaperMetadata,
    raw_defaults: object,
) -> PaperMetadata:
    defaults = raw_defaults if isinstance(raw_defaults, dict) else {}

    def fallback(field: str) -> str | None:
        value = str(defaults.get(field) or "").strip()
        return value or None

    return PaperMetadata(
        year=inferred.year or fallback("year"),
        province=inferred.province or fallback("province"),
        city=inferred.city or fallback("city"),
        district=inferred.district or fallback("district"),
        exam_type=inferred.exam_type or fallback("exam_type"),
        grade=inferred.grade or fallback("grade"),
        semester=inferred.semester or fallback("semester"),
        textbook_version=(
            inferred.textbook_version or fallback("textbook_version")
        ),
    )
