from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from backend.jobs.manager import JobContext
from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
from question_bank.exporters.paper_markdown_exporter import (
    export_question_paper_markdown,
)
from question_bank.services.assembly_basket_state import SectionSpec
from question_bank.services.assembly_workspace_service import (
    AssemblyRecordCreate,
    AssemblyWorkspaceService,
)
from question_bank.services.question_read_service import QuestionBankReadService


def run_assembly_export_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    docx_exporter: Callable = export_question_paper_docx,
    markdown_exporter: Callable = export_question_paper_markdown,
) -> dict[str, object]:
    workspace = AssemblyWorkspaceService(data_root)
    raw_draft = context.payload.get("draft")
    if not isinstance(raw_draft, dict):
        raise ValueError("assembly draft is required")
    draft = workspace.normalize_draft(raw_draft)
    draft_revision = str(context.payload.get("draft_revision") or "")
    if draft_revision != draft.revision:
        raise ValueError("assembly draft revision does not match payload")
    if not draft.order_ids:
        raise ValueError("assembly draft is empty")
    if draft.practice_rules:
        from question_bank.recommendation.personalized import (
            PersonalizedRecommendationModule,
        )
        PersonalizedRecommendationModule(db_path=question_bank_db_path, data_root=data_root).validate_paper_questions(draft.order_ids)
    export_format = str(context.payload.get("format") or "").strip().casefold()
    if export_format not in {"docx", "markdown"}:
        raise ValueError("assembly export format is not supported")
    raw_source = context.payload.get("source")
    source = (
        str(raw_source).strip().casefold()
        if raw_source is not None
        else None
    )

    question_rows = QuestionBankReadService(Path(question_bank_db_path)).get_questions(
        draft.order_ids
    )
    found_ids = {int(row["id"]) for row in question_rows}
    missing_ids = [
        question_id for question_id in draft.order_ids if question_id not in found_ids
    ]
    if missing_ids:
        raise ValueError("assembly draft contains unavailable questions")

    context.raise_if_cancelled()
    context.report(0.05, "assembly_export", "starting")
    workspace.exports_root.mkdir(parents=True, exist_ok=True)
    sections = [
        SectionSpec(title=section.title, question_ids=list(section.question_ids))
        for section in draft.sections
    ]
    title = draft.title or f"{datetime.now().strftime('%Y-%m-%d')}习题"
    exporter = docx_exporter if export_format == "docx" else markdown_exporter
    published: Path | None = None
    record = None
    with tempfile.TemporaryDirectory(
        dir=workspace.exports_root,
        prefix=f".job-{context.job_id}-",
    ) as staging_value:
        staging_root = Path(staging_value)
        staged = Path(
            exporter(
                Path(question_bank_db_path),
                list(draft.order_ids),
                staging_root,
                title=title,
                include_answer=draft.include_answer,
                grouped_by_type=draft.layout_mode == "grouped_by_type",
                header_text=draft.header_text or None,
                sections=sections if draft.layout_mode == "sections" else None,
            )
        )
        try:
            staged.resolve(strict=True).relative_to(staging_root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(
                "assembly export must stay inside the job staging directory"
            ) from exc
        expected_suffix = ".docx" if export_format == "docx" else ".md"
        if staged.suffix.casefold() != expected_suffix or not staged.is_file():
            raise ValueError("assembly exporter did not create the requested file")

        context.report(0.9, "assembly_export", staged.name)
        context.raise_if_cancelled()
        published = workspace.exports_root / (
            f"{staged.stem}_job-{context.job_id}{staged.suffix}"
        )
        os.replace(staged, published)
        try:
            record = workspace.create_record(
                AssemblyRecordCreate(
                    title=title,
                    draft=draft,
                    output_path=published,
                    export_format=export_format,
                    question_type_summary=_question_type_summary(question_rows),
                    source=source,
                )
            )
        except BaseException:
            published.unlink(missing_ok=True)
            raise

    draft_cleared = workspace.clear_if_revision(draft.revision)
    return {
        "record_id": record.id,
        "format": export_format,
        "question_count": len(draft.order_ids),
        "draft_cleared": draft_cleared,
        "file_path": str(published),
        "filename": published.name,
    }


def _question_type_summary(rows: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        raw_type = str(row.get("question_type") or "")
        if "选择" in raw_type:
            label = "选择题"
        elif "填空" in raw_type:
            label = "填空题"
        else:
            label = "解答题"
        summary[label] = summary.get(label, 0) + 1
    return summary


__all__ = ["run_assembly_export_job"]
