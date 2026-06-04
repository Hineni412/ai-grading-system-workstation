from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.importers.batch_importer import map_rich_content_by_number
from question_bank.importers.docx_importer import import_docx
from question_bank.services.rich_content_service import is_question_rich_content_current, save_question_rich_content


LOGGER = logging.getLogger(__name__)
LEADING_QUESTION_NUMBER_PATTERN = re.compile(
    r"^\s*(?:第\s*\d{1,3}\s*[题題][：:、.．]?\s*|\d{1,3}\s*[.．、]\s*|[（(]\s*\d{1,3}\s*[）)]\s*)"
)


@dataclass(frozen=True)
class RichContentBackfillResult:
    scanned_questions: int = 0
    created_sidecars: int = 0
    updated_preview_texts: int = 0
    skipped_existing: int = 0
    skipped_missing_source: int = 0
    skipped_no_match: int = 0
    failed_sources: int = 0
    errors: list[str] = field(default_factory=list)


def backfill_missing_rich_content(
    db_path: str | Path,
    *,
    overwrite: bool = False,
    update_preview_text: bool = True,
) -> RichContentBackfillResult:
    database_path = Path(db_path)
    initialize_database(database_path)
    rows = _docx_question_rows(database_path)
    grouped_rows: dict[Path, list[dict[str, object]]] = {}
    skipped_existing = 0
    skipped_missing_source = 0

    for row in rows:
        question_id = int(row["id"])
        rich_content_current = is_question_rich_content_current(question_id)
        if not overwrite and rich_content_current and not update_preview_text:
            skipped_existing += 1
            continue

        source_file = str(row.get("source_file") or row.get("paper_source_file") or "").strip()
        source_path = Path(source_file).expanduser()
        if not source_path.exists():
            # Try searching under local raw papers directories
            filename = source_path.name
            possible_paths = [
                Path('user_data/question_bank/raw_papers') / filename,
                Path('question_bank/raw_papers') / filename,
            ]
            for p in possible_paths:
                if p.exists():
                    source_path = p
                    break

        if source_path.suffix.lower() != ".docx" or not source_path.exists():
            skipped_missing_source += 1
            continue

        row_payload = dict(row)
        row_payload["_rich_content_current"] = rich_content_current
        grouped_rows.setdefault(source_path, []).append(row_payload)

    created_sidecars = 0
    updated_preview_texts = 0
    skipped_no_match = 0
    failed_sources = 0
    errors: list[str] = []

    for source_path, question_rows in grouped_rows.items():
        try:
            extracted = import_docx(source_path)
            rich_content = map_rich_content_by_number(getattr(extracted, "rich_paragraphs", []), source_file=str(source_path))
        except Exception as exc:  # noqa: BLE001
            failed_sources += 1
            message = f"{source_path}: {exc}"
            errors.append(message)
            LOGGER.exception("Failed to backfill rich content from %s", source_path)
            continue

        for row in question_rows:
            question_id = int(row["id"])
            question_number = str(row.get("question_number") or "").strip()
            question_blocks = rich_content["question"].get(question_number, [])
            answer_blocks = rich_content["answer"].get(question_number, [])
            if not question_blocks and not answer_blocks:
                skipped_no_match += 1
                continue
            if overwrite or not bool(row.get("_rich_content_current")):
                try:
                    save_question_rich_content(
                        question_id,
                        question_blocks=question_blocks,
                        answer_blocks=answer_blocks,
                    )
                    created_sidecars += 1
                except OSError as exc:
                    errors.append(f"question {question_id}: {exc}")
                    LOGGER.exception("Failed to save backfilled rich content for question %s", question_id)
            else:
                skipped_existing += 1
            if update_preview_text:
                try:
                    if _update_question_preview_text(database_path, row, question_blocks, answer_blocks):
                        updated_preview_texts += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"question {question_id}: {exc}")
                    LOGGER.exception("Failed to update backfilled preview text for question %s", question_id)

    return RichContentBackfillResult(
        scanned_questions=len(rows),
        created_sidecars=created_sidecars,
        updated_preview_texts=updated_preview_texts,
        skipped_existing=skipped_existing,
        skipped_missing_source=skipped_missing_source,
        skipped_no_match=skipped_no_match,
        failed_sources=failed_sources,
        errors=errors,
    )


def _docx_question_rows(db_path: Path) -> list[dict[str, object]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                q.id,
                q.question_number,
                q.question_text,
                q.answer_text,
                q.source_file,
                p.source_file AS paper_source_file
            FROM questions q
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE q.is_deleted = 0
              AND lower(COALESCE(NULLIF(q.source_file, ''), NULLIF(p.source_file, ''), '')) LIKE '%.docx'
            ORDER BY q.id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _update_question_preview_text(
    db_path: Path,
    row: dict[str, object],
    question_blocks: list[dict[str, object]],
    answer_blocks: list[dict[str, object]],
) -> bool:
    question_text = _blocks_to_preview_text(question_blocks, strip_leading_number=True)
    answer_text = _blocks_to_preview_text(answer_blocks, strip_leading_number=True)
    assignments: list[str] = []
    params: list[object] = []
    if question_text and question_text != str(row.get("question_text") or ""):
        assignments.append("question_text = ?")
        params.append(question_text)
    if answer_text and answer_text != str(row.get("answer_text") or ""):
        assignments.append("answer_text = ?")
        params.append(answer_text)
    if not assignments:
        return False
    params.append(int(row["id"]))
    with connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE questions
            SET {", ".join(assignments)},
                updated_at = datetime('now','localtime')
            WHERE id = ? AND is_deleted = 0
            """,
            params,
        )
        conn.commit()
    return cursor.rowcount > 0


def _blocks_to_preview_text(blocks: list[dict[str, object]], *, strip_leading_number: bool = False) -> str:
    lines = [str(block.get("text") or "").strip() for block in blocks]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    if strip_leading_number:
        first = LEADING_QUESTION_NUMBER_PATTERN.sub("", lines[0], count=1).strip()
        if first:
            lines[0] = first
        else:
            lines = lines[1:]
    return "\n".join(lines).strip()


__all__ = ["RichContentBackfillResult", "backfill_missing_rich_content"]
