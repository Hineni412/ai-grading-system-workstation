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
    document_pipeline: object | None = None,
) -> dict[str, object]:
    request_id = str(context.payload.get("request_id") or "").strip().casefold()
    context.raise_if_cancelled()
    try:
        resource = write_service.load_import_resource(request_id)
    except QuestionImportUploadNotFound:
        raise ValueError("question import request is unavailable") from None
    database_identity = Path(question_bank_db_path).resolve(strict=False)
    lock_keys = [
        f"question-import:{database_identity}:{request_id}",
        f"question-import-content:{database_identity}:{resource.sha256}",
    ]
    with keyed_execution_locks(
        lock_keys,
        cancel_check=context.raise_if_cancelled,
    ):
        return _run_question_import_job_locked(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            write_service=write_service,
            importer=importer,
            document_pipeline=document_pipeline,
        )


def _run_question_import_job_locked(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    write_service: QuestionBankWriteService,
    importer: ImportRunner,
    document_pipeline: object | None = None,
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
    raw_overrides = context.payload.get("asset_overrides")
    asset_overrides = raw_overrides if isinstance(raw_overrides, list) else None
    raw_type_overrides = context.payload.get("type_overrides")
    type_overrides = (
        {
            str(number): str(question_type)
            for number, question_type in raw_type_overrides.items()
            if str(number).strip() and str(question_type).strip()
        }
        if isinstance(raw_type_overrides, dict)
        else None
    )
    raw_confirmed = context.payload.get("confirmed_duplicates")
    confirmed_duplicates = (
        {
            str(number): int(bank_id)
            for number, bank_id in raw_confirmed.items()
            if str(number).strip() and str(bank_id).strip().isdigit()
            and int(bank_id) > 0
        }
        if isinstance(raw_confirmed, dict)
        else None
    )
    importer_kwargs: dict[str, object] = {}
    if document_pipeline is not None:
        importer_kwargs["document_pipeline"] = document_pipeline
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
            asset_overrides=asset_overrides,
            type_overrides=type_overrides,
            confirmed_duplicates=confirmed_duplicates,
            **importer_kwargs,
        )
    except Exception:
        raise RuntimeError("question import failed") from None
    context.raise_if_cancelled()

    successful_sources = [
        item.source_file
        for item in result.files
        if item.status not in {"failed", "duplicate_in_trash"}
        and str(item.source_file or "").strip()
    ]
    trash_collisions = [
        item for item in result.files if item.status == "duplicate_in_trash"
    ]
    question_ids = sorted(
        set(_active_question_ids(Path(question_bank_db_path), successful_sources))
        | set(
            _active_question_ids_by_content_fingerprint(
                Path(question_bank_db_path),
                resource.sha256,
            )
        )
    )
    imported_paper_ids = sorted(
        {
            int(item.paper_id)
            for item in result.files
            if item.paper_id is not None and int(item.paper_id) > 0
        }
    )
    context.report(0.9, "question_import", "indexing")
    failed_count = int(result.failed_files) + len(trash_collisions)
    if trash_collisions:
        outcome = "failed"
    elif failed_count == 0:
        outcome = "complete"
    else:
        outcome = "partial" if question_ids else "failed"
    context.report(1.0, "question_import", outcome)
    public_result: dict[str, object] = {
        "request_id": resource.request_id,
        "outcome": outcome,
        "imported_papers": int(result.imported_papers),
        "imported_paper_ids": imported_paper_ids,
        "question_count": len(question_ids),
        "failed_count": failed_count,
        "successful_question_ids": question_ids,
        "failed_question_ids": [],
        "failure_category": (
            "duplicate_in_trash"
            if trash_collisions
            else ("import" if failed_count else "")
        ),
        "retryable": bool(failed_count) and not trash_collisions,
        "exact_duplicate_count": int(result.exact_duplicate_count),
        "analysis_reused_count": int(result.analysis_reused_count),
        "near_duplicate_hints": [
            {
                "question_number": str(hint.get("question_number") or ""),
                "matched_question_id": int(hint.get("matched_question_id") or 0),
                "matched_paper_title": str(hint.get("matched_paper_title") or ""),
                "similarity": float(hint.get("similarity") or 0),
                "high": bool(hint.get("high")),
                "match_kind": str(hint.get("match_kind") or "suspected"),
                "requires_review": True,
                "reason": str(hint.get("reason") or "题面相似，需要核对"),
            }
            for hint in result.near_duplicate_hints
        ],
    }
    if trash_collisions:
        public_result.update(
            restore_required=True,
            restore_paper_id=trash_collisions[0].paper_id,
        )
    return public_result


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
            UNION
            SELECT occ.question_id AS id
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE COALESCE(p.import_status, '') <> 'deleted'
              AND p.source_file IN ({placeholders})
            ORDER BY id
            """,
            [*clean_sources, *clean_sources],
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _active_question_ids_by_content_fingerprint(
    db_path: Path,
    content_fingerprint: str,
) -> list[int]:
    clean_fingerprint = str(content_fingerprint or "").strip().casefold()
    if len(clean_fingerprint) != 64 or not db_path.exists():
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT questions.id
            FROM questions
            JOIN papers ON papers.id = questions.paper_id
            WHERE COALESCE(questions.is_deleted, 0) = 0
              AND COALESCE(papers.import_status, '') <> 'deleted'
              AND papers.content_fingerprint = ?
            UNION
            SELECT occ.question_id AS id
            FROM paper_question_occurrences occ
            JOIN papers ON papers.id = occ.paper_id
            WHERE COALESCE(papers.import_status, '') <> 'deleted'
              AND papers.content_fingerprint = ?
            ORDER BY id
            """,
            (clean_fingerprint, clean_fingerprint),
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
