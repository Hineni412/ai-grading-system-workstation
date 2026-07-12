from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from question_bank.services.training_export_service import TrainingExportService

from .manager import JobCancellationRequested, JobContext


TrainingExportServiceFactory = Callable[[Path, Path], TrainingExportService]


def run_training_export_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    output_root: Path,
    service_factory: TrainingExportServiceFactory = TrainingExportService,
) -> dict[str, object]:
    task_id = _required_positive_int(context.payload, "task_id")
    variant_id = _optional_positive_int(context.payload, "variant_id")
    export_format = str(context.payload.get("format") or "docx").strip().lower()
    if export_format not in {"docx", "markdown"}:
        raise ValueError("unsupported training export format")
    audience = str(context.payload.get("audience") or "").strip().lower() or None
    if variant_id is None and audience is not None:
        raise ValueError("audience is only valid for a variant export")
    if variant_id is not None and audience not in {"student", "teacher"}:
        raise ValueError("variant export audience is required")

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    final_root = output_root / f"job-{context.job_id}"
    if final_root.exists():
        raise FileExistsError("training export destination already exists")
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".job-{context.job_id}-", dir=output_root)
    )
    service = service_factory(Path(question_bank_db_path), staging_root)
    export_ids: list[int] = []
    variant_ids: list[int] = []
    try:
        context.raise_if_cancelled()
        context.report(0.05, "training_export", "starting")
        if variant_id is None:
            result = service.export_task_bundle(task_id, formats=[export_format])
            records = [*result.get("variant_exports", []), result.get("export", {})]
            primary = dict(result.get("export") or {})
            variant_ids = sorted(
                {
                    int(record["variant_id"])
                    for record in records
                    if isinstance(record, dict) and record.get("variant_id") is not None
                }
            )
        else:
            result = service.export_variant(
                task_id,
                variant_id,
                formats=[export_format],
                audiences=[str(audience)],
            )
            records = list(result.get("exports", []))
            primary = dict(records[0]) if records else {}
            variant_ids = [variant_id]
        export_ids = [
            int(record["id"])
            for record in records
            if isinstance(record, dict) and record.get("id") is not None
        ]
        if not primary or primary.get("status") != "succeeded":
            raise RuntimeError("training export failed")
        primary_path = _controlled_output(primary.get("output_path"), staging_root)
        for record in records:
            if not isinstance(record, dict) or record.get("status") != "succeeded":
                continue
            _controlled_output(record.get("output_path"), staging_root)

        context.report(0.9, "training_export", "publishing")
        context.raise_if_cancelled()
        primary_relative = primary_path.relative_to(staging_root.resolve())
        os.replace(staging_root, final_root)
        try:
            relocated = service.relocate_export_outputs(
                export_ids,
                old_root=staging_root,
                new_root=final_root,
            )
        except Exception:
            shutil.rmtree(final_root, ignore_errors=True)
            raise
        relocated_ids = {int(record["id"]) for record in relocated}
        if set(export_ids) != relocated_ids:
            shutil.rmtree(final_root, ignore_errors=True)
            raise RuntimeError("training export records could not be published")
        published_path = final_root / primary_relative
        if not published_path.is_file():
            shutil.rmtree(final_root, ignore_errors=True)
            raise RuntimeError("training export output was not published")
        context.report(0.98, "training_export", published_path.name)
        return {
            "task_id": task_id,
            "variant_id": variant_id,
            "export_ids": export_ids,
            "file_path": str(published_path),
            "filename": published_path.name,
        }
    except JobCancellationRequested:
        service.abort_unpublished_exports(
            export_ids,
            task_id=task_id,
            variant_ids=variant_ids,
        )
        shutil.rmtree(final_root, ignore_errors=True)
        raise
    except Exception as exc:
        service.abort_unpublished_exports(
            export_ids,
            task_id=task_id,
            variant_ids=variant_ids,
        )
        shutil.rmtree(final_root, ignore_errors=True)
        raise RuntimeError("training export failed") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _controlled_output(value: object, root: Path) -> Path:
    path = Path(str(value or ""))
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("training export output escaped staging") from exc
    if not resolved.is_file():
        raise FileNotFoundError("training export output was not created")
    return resolved


def _required_positive_int(payload: dict[str, Any], field_name: str) -> int:
    value = _optional_positive_int(payload, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    return value


def _optional_positive_int(payload: dict[str, Any], field_name: str) -> int | None:
    raw = payload.get(field_name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


__all__ = ["run_training_export_job"]
