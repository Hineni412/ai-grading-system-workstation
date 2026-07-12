from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect, initialize_database
from question_bank.exporters.docx_exporter import export_training_docx
from question_bank.exporters.markdown_exporter import export_training_markdown
from question_bank.services.training_task_service import TrainingTaskService


Exporter = Callable[..., Path]


class TrainingExportService:
    def __init__(
        self,
        db_path: str | Path,
        output_dir: str | Path | None = None,
        *,
        exporters: Mapping[str, Exporter] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.output_dir = Path(output_dir) if output_dir is not None else self.db_path.parent / "training_exports"
        self.exporters = dict(
            exporters
            or {
                "docx": export_training_docx,
                "markdown": export_training_markdown,
            }
        )
        self.tasks = TrainingTaskService(self.db_path)

    def export_variant(
        self,
        task_id: int,
        variant_id: int,
        *,
        formats: Iterable[str] = ("docx",),
        audiences: Iterable[str] = ("student", "teacher"),
    ) -> dict[str, Any]:
        task = self.tasks.get_task(task_id)
        variant = _variant(task, variant_id)
        if task["status"] == "cancelled":
            raise ValueError("cancelled training task cannot be exported")
        if not variant.get("items"):
            raise ValueError("training variant has no exportable items")

        resolved_formats = _unique(formats)
        resolved_audiences = _unique(audiences)
        _validate_formats(resolved_formats, self.exporters)
        _validate_audiences(resolved_audiences)
        self.tasks.mark_export_state(task_id, status="exporting", variant_id=variant_id)
        records = [
            self._run_variant_export(task, variant, export_format, audience)
            for export_format in resolved_formats
            for audience in resolved_audiences
        ]
        final_status = "completed" if all(item["status"] == "succeeded" for item in records) else "failed"
        self.tasks.mark_export_state(task_id, status=final_status, variant_id=variant_id)
        return {"task_id": int(task_id), "variant_id": int(variant_id), "exports": records}

    def retry_export(self, export_id: int) -> dict[str, Any]:
        record = self._export_record(export_id)
        if record["status"] != "failed":
            raise ValueError("only failed exports can be retried")
        if record["audience"] == "bundle":
            raise ValueError("bundle retries must be regenerated from their saved task")
        if record.get("variant_id") is None:
            raise ValueError("variant export record has no variant")
        task = self.tasks.get_task(int(record["task_id"]))
        variant = _variant(task, int(record["variant_id"]))
        retried = self._run_variant_export(
            task,
            variant,
            str(record["export_format"]),
            str(record["audience"]),
            existing_export_id=int(record["id"]),
        )
        final_status = "completed" if retried["status"] == "succeeded" else "failed"
        self.tasks.mark_export_state(
            int(record["task_id"]),
            status=final_status,
            variant_id=int(record["variant_id"]),
        )
        return retried

    def export_task_bundle(
        self,
        task_id: int,
        *,
        formats: Iterable[str] = ("docx",),
    ) -> dict[str, Any]:
        task = self.tasks.get_task(task_id)
        resolved_formats = _unique(formats)
        _validate_formats(resolved_formats, self.exporters)
        bundle_record = self._create_record(task_id, None, "bundle", "zip")
        files: list[Path] = []
        variant_exports: list[dict[str, Any]] = []
        try:
            for variant in task["variants"]:
                result = self.export_variant(
                    task_id,
                    int(variant["id"]),
                    formats=resolved_formats,
                )
                variant_exports.extend(result["exports"])
                files.extend(
                    Path(item["output_path"])
                    for item in result["exports"]
                    if item["status"] == "succeeded" and item.get("output_path")
                )
            failed_exports = [item for item in variant_exports if item["status"] == "failed"]
            if failed_exports:
                raise RuntimeError(
                    f"{len(failed_exports)} variant exports failed; bundle was not created"
                )
            if not files:
                raise ValueError("no successful variant exports available for bundle")
            bundle_path = self.output_dir / f"{task['task_code']}_bundle.zip"
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
        except Exception as exc:
            final = self._finish_record(int(bundle_record["id"]), status="failed", error_message=str(exc))
        else:
            final = self._finish_record(
                int(bundle_record["id"]),
                status="succeeded",
                output_path=bundle_path,
            )
        self.tasks.mark_export_state(
            task_id,
            status="completed" if final["status"] == "succeeded" else "failed",
        )
        return {"task_id": int(task_id), "export": final, "variant_exports": variant_exports}

    def list_exports(self, task_id: int) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM training_exports WHERE task_id = ? ORDER BY id",
                (int(task_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def relocate_export_outputs(
        self,
        export_ids: Iterable[int],
        *,
        old_root: Path,
        new_root: Path,
    ) -> list[dict[str, Any]]:
        ids = sorted({int(value) for value in export_ids})
        if not ids:
            return []
        old_resolved = Path(old_root).resolve()
        new_root = Path(new_root)
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT id, output_path FROM training_exports WHERE id IN ({placeholders}) "
                "AND status = 'succeeded' AND output_path IS NOT NULL",
                ids,
            ).fetchall()
            updates: list[tuple[str, int]] = []
            for row in rows:
                path = Path(str(row["output_path"])).resolve()
                try:
                    relative = path.relative_to(old_resolved)
                except ValueError as exc:
                    raise ValueError("training export record escaped staging") from exc
                updates.append((str(new_root / relative), int(row["id"])))
            conn.executemany(
                """
                UPDATE training_exports
                SET output_path = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                updates,
            )
        return [self._export_record(export_id) for _, export_id in updates]

    def abort_unpublished_exports(
        self,
        export_ids: Iterable[int],
        *,
        task_id: int,
        variant_ids: Iterable[int] = (),
    ) -> None:
        ids = sorted({int(value) for value in export_ids})
        variants = sorted({int(value) for value in variant_ids})
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    UPDATE training_exports
                    SET status = 'failed', output_path = NULL,
                        error_message = 'Export was not published.',
                        updated_at = datetime('now','localtime')
                    WHERE id IN ({placeholders})
                    """,
                    ids,
                )
            conn.execute(
                """
                UPDATE training_tasks
                SET status = 'ready', updated_at = datetime('now','localtime')
                WHERE id = ? AND status <> 'cancelled'
                """,
                (int(task_id),),
            )
            if variants:
                placeholders = ",".join("?" for _ in variants)
                conn.execute(
                    f"""
                    UPDATE training_variants
                    SET status = 'ready', updated_at = datetime('now','localtime')
                    WHERE task_id = ? AND id IN ({placeholders})
                    """,
                    [int(task_id), *variants],
                )

    def _run_variant_export(
        self,
        task: Mapping[str, Any],
        variant: Mapping[str, Any],
        export_format: str,
        audience: str,
        *,
        existing_export_id: int | None = None,
    ) -> dict[str, Any]:
        record = (
            self._create_record(int(task["id"]), int(variant["id"]), audience, export_format)
            if existing_export_id is None
            else self._prepare_retry(existing_export_id)
        )
        exporter = self.exporters[export_format]
        display_name, student_id, class_id = _variant_display(variant)
        try:
            output_path = exporter(
                self.db_path,
                _export_items(task, variant),
                self.output_dir,
                audience=audience,
                display_name=display_name,
                student_id=student_id,
                class_id=class_id,
                use_real_name=False,
                task_code=str(task["task_code"]),
                variant_code=str(variant["variant_key"]),
            )
        except Exception as exc:
            return self._finish_record(int(record["id"]), status="failed", error_message=str(exc))
        return self._finish_record(int(record["id"]), status="succeeded", output_path=output_path)

    def _create_record(
        self,
        task_id: int,
        variant_id: int | None,
        audience: str,
        export_format: str,
    ) -> dict[str, Any]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO training_exports (
                    task_id, variant_id, audience, export_format, status
                ) VALUES (?, ?, ?, ?, 'pending')
                """,
                (int(task_id), variant_id, audience, export_format),
            )
            export_id = int(cursor.lastrowid)
        return self._export_record(export_id)

    def _prepare_retry(self, export_id: int) -> dict[str, Any]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE training_exports
                SET status = 'pending', output_path = NULL, error_message = NULL,
                    retry_count = retry_count + 1,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (int(export_id),),
            )
        return self._export_record(export_id)

    def _finish_record(
        self,
        export_id: int,
        *,
        status: str,
        output_path: str | Path | None = None,
        error_message: object | None = None,
    ) -> dict[str, Any]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE training_exports
                SET status = ?, output_path = ?, error_message = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (
                    status,
                    str(output_path) if output_path is not None else None,
                    str(error_message) if error_message else None,
                    int(export_id),
                ),
            )
        return self._export_record(export_id)

    def _export_record(self, export_id: int) -> dict[str, Any]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM training_exports WHERE id = ?",
                (int(export_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"training export not found: {export_id}")
        return dict(row)


def _variant(task: Mapping[str, Any], variant_id: int) -> dict[str, Any]:
    for variant in task.get("variants", []):
        if int(variant["id"]) == int(variant_id):
            return dict(variant)
    raise KeyError(f"training variant not found: {variant_id}")


def _export_items(task: Mapping[str, Any], variant: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in variant.get("items", []):
        recommendation = dict(item.get("recommendation_snapshot") or {})
        recommendation.update(
            {
                "question_id": item.get("question_id"),
                "question_snapshot": dict(item.get("question_snapshot") or {}),
                "task_item_code": item.get("task_item_code"),
                "task_code": task.get("task_code"),
                "variant_code": variant.get("variant_key"),
                "suggested_order": item.get("item_order"),
                "training_stage": item.get("stage"),
            }
        )
        result.append(recommendation)
    return result


def _variant_display(variant: Mapping[str, Any]) -> tuple[str, str | None, str | None]:
    students = list(variant.get("students") or [])
    if len(students) == 1:
        student = students[0]
        return (
            str(student.get("student_name_snapshot") or student.get("student_id") or ""),
            str(student.get("student_id") or "") or None,
            str(student.get("class_id_snapshot") or "") or None,
        )
    return str(variant.get("variant_key") or "group"), None, None


def _validate_formats(formats: list[str], exporters: Mapping[str, Exporter]) -> None:
    if not formats:
        raise ValueError("at least one export format is required")
    unsupported = [value for value in formats if value not in exporters]
    if unsupported:
        raise ValueError(f"unsupported export formats: {', '.join(unsupported)}")


def _validate_audiences(audiences: list[str]) -> None:
    if not audiences:
        raise ValueError("at least one audience is required")
    unsupported = [value for value in audiences if value not in {"student", "teacher"}]
    if unsupported:
        raise ValueError(f"unsupported audiences: {', '.join(unsupported)}")


def _unique(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if text and text not in result:
            result.append(text)
    return result


__all__ = ["TrainingExportService"]
