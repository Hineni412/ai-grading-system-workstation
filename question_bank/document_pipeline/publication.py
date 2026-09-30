from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from question_bank.database.schema import connect

from .contracts import (
    DocumentSnapshot,
    PublishCommand,
    PublishedQuestion,
    PublishReceipt,
    PublishState,
    QuestionDraft,
    ReviewState,
    canonical_hash,
    sha256_bytes,
)
from .pipeline import DocumentPipelineConflict, DocumentPipelineValidationError


class QuestionBankPublicationAdapter:
    """Publishes a verified snapshot without exposing a DB/sidecar split state.

    The filesystem manifest is written first, all required files are made durable
    while the SQLite write lock is held, and only then is the database committed.
    `reconcile` can repair the two crash windows without guessing file ownership.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        data_root: str | Path,
        pipeline_workspace: str | Path,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.data_root = Path(data_root).resolve()
        self.pipeline_workspace = Path(pipeline_workspace).resolve()
        self.manifest_root = self.data_root / "question_bank" / "document_publications"

    def publish(
        self,
        snapshot: DocumentSnapshot,
        command: PublishCommand,
        questions: tuple[QuestionDraft, ...],
    ) -> PublishReceipt:
        if snapshot.operation_id != command.operation_id:
            raise DocumentPipelineValidationError("publish operation does not match snapshot")
        if snapshot.snapshot_revision != command.expected_snapshot_revision:
            raise DocumentPipelineConflict("publish snapshot revision is stale")
        self._assert_schema_available()
        existing = self._existing_receipt(snapshot)
        if existing is not None:
            self._reuse_duplicate_analysis(existing)
            return existing

        manifest_sha = canonical_hash(
            {
                "operation_id": snapshot.operation_id,
                "source_revision": snapshot.source_revision,
                "snapshot_revision": snapshot.snapshot_revision,
                "questions": [
                    {
                        "question_id": item.question_id,
                        "content_revision": item.content_revision,
                    }
                    for item in questions
                ],
            }
        )
        manifest_path = self._manifest_path(snapshot.operation_id)
        if manifest_path.is_file():
            self.reconcile(snapshot.operation_id)
            existing = self._existing_receipt(snapshot)
            if existing is not None:
                return existing
        staging_root = self._staging_root(snapshot.operation_id)
        staging_manifest: dict[str, object] | None = None
        owned_sidecars: list[str] = []
        moved_assets: list[str] = []
        moved_sidecars: list[str] = []
        receipt: PublishReceipt | None = None
        with connect(self.db_path) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                concurrent = self._existing_receipt_in_connection(conn, snapshot)
                if concurrent is not None:
                    conn.rollback()
                    return concurrent

                self._remove_staging_tree(snapshot.operation_id)
                staging_root.mkdir(parents=True, exist_ok=False)
                raw_source, source_stage = self._stage_source_asset(
                    snapshot,
                    staging_root,
                )
                page_assets, page_stages = self._stage_page_assets(
                    snapshot,
                    staging_root,
                )
                staged_files: list[dict[str, str]] = [
                    source_stage,
                    *page_stages,
                ]
                staging_manifest = {
                    "version": 2,
                    "operation_id": snapshot.operation_id,
                    "source_revision": snapshot.source_revision,
                    "snapshot_revision": snapshot.snapshot_revision,
                    "manifest_sha256": manifest_sha,
                    "state": "staging",
                    "required_files": [raw_source, *page_assets],
                    "owned_assets": [raw_source, *page_assets],
                    "owned_sidecars": [],
                    "staging_directory": self._relative_data_path(staging_root),
                    "staged_files": staged_files,
                }
                self._write_validated_manifest(manifest_path, staging_manifest)

                paper_cursor = conn.execute(
                    """
                    INSERT INTO papers (
                        title, source_file, import_status, content_fingerprint
                    ) VALUES (?, ?, 'success', ?)
                    """,
                    (snapshot.source_filename, raw_source, snapshot.source_revision),
                )
                paper_id = int(paper_cursor.lastrowid)
                published: list[PublishedQuestion] = []
                item_rows: list[tuple[QuestionDraft, int, str]] = []
                for question in questions:
                    question_cursor = conn.execute(
                        """
                        INSERT INTO questions (
                            paper_id, question_number, question_text, answer_text,
                            source_file, page_range, image_paths, needs_review,
                            has_images, needs_image_review
                        ) VALUES (?, ?, ?, ?, ?, ?, '[]', 0, 0, 0)
                        """,
                        (
                            paper_id,
                            question.question_number,
                            question.question_text,
                            (
                                question.answer.text
                                if question.answer.status == ReviewState.TEACHER_VERIFIED
                                else None
                            ),
                            raw_source,
                            self._page_range(question),
                        ),
                    )
                    bank_id = int(question_cursor.lastrowid)
                    sidecar = self._sidecar_path(bank_id)
                    relative_sidecar = self._relative_data_path(sidecar)
                    owned_sidecars.append(relative_sidecar)
                    staged_sidecar = staging_root / f"sidecar-{bank_id}.json"
                    self._write_owned_sidecar(
                        staged_sidecar,
                        self._sidecar_payload(snapshot, question, bank_id, page_assets),
                        operation_id=snapshot.operation_id,
                    )
                    staged_files.append(
                        self._staged_file_record(
                            staged=staged_sidecar,
                            final=sidecar,
                            expected_hash=sha256_bytes(staged_sidecar.read_bytes()),
                            kind="sidecar",
                        )
                    )
                    item_rows.append((question, bank_id, relative_sidecar))
                    published.append(
                        PublishedQuestion(
                            source_question_id=question.question_id,
                            bank_question_id=bank_id,
                            content_revision=question.content_revision,
                        )
                    )

                receipt = PublishReceipt(
                    operation_id=snapshot.operation_id,
                    source_revision=snapshot.source_revision,
                    snapshot_revision=snapshot.snapshot_revision,
                    state=PublishState.PUBLISHED,
                    questions=tuple(published),
                    manifest_sha256=manifest_sha,
                    asset_paths=tuple([raw_source, *page_assets, *owned_sidecars]),
                )
                receipt_json = json.dumps(
                    asdict(receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                conn.execute(
                    """
                    INSERT INTO question_document_publications (
                        operation_id, source_revision, snapshot_revision, state,
                        manifest_sha256, paper_id, question_ids_json, receipt_json
                    ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
                    """,
                    (
                        snapshot.operation_id,
                        snapshot.source_revision,
                        snapshot.snapshot_revision,
                        manifest_sha,
                        paper_id,
                        json.dumps([item.bank_question_id for item in published]),
                        receipt_json,
                    ),
                )
                for question, bank_id, relative_sidecar in item_rows:
                    regions_json = json.dumps(
                        [asdict(item) for item in question.source_regions],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    assets_json = json.dumps(page_assets, ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO question_document_items (
                            operation_id, source_question_id, content_revision,
                            bank_question_id, rich_content_path, asset_paths_json, state
                        ) VALUES (?, ?, ?, ?, ?, ?, 'published')
                        """,
                        (
                            snapshot.operation_id,
                            question.question_id,
                            question.content_revision,
                            bank_id,
                            relative_sidecar,
                            assets_json,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO question_content_revisions (
                            question_id, content_revision, source_revision,
                            source_regions_json, rich_content_path, state
                        ) VALUES (?, ?, ?, ?, ?, 'active')
                        """,
                        (
                            bank_id,
                            question.content_revision,
                            snapshot.source_revision,
                            regions_json,
                            relative_sidecar,
                        ),
                    )
                outbox_payload = {
                    "operation_id": snapshot.operation_id,
                    "snapshot_revision": snapshot.snapshot_revision,
                    "paper_id": paper_id,
                    "question_ids": [item.bank_question_id for item in published],
                    "required_files": [raw_source, *page_assets, *owned_sidecars],
                }
                payload_json = json.dumps(
                    outbox_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                payload_sha = sha256_bytes(payload_json.encode("utf-8"))
                outbox_id = canonical_hash(
                    {"operation_id": snapshot.operation_id, "payload_sha256": payload_sha}
                )
                conn.execute(
                    """
                    INSERT INTO question_document_publication_outbox (
                        outbox_id, operation_id, payload_sha256, payload_json,
                        status, delivered_at
                    ) VALUES (?, ?, ?, ?, 'delivered', datetime('now','localtime'))
                    """,
                    (outbox_id, snapshot.operation_id, payload_sha, payload_json),
                )
                staging_manifest["required_files"] = [
                    raw_source,
                    *page_assets,
                    *owned_sidecars,
                ]
                staging_manifest["owned_sidecars"] = list(owned_sidecars)
                staging_manifest["staged_files"] = staged_files
                staging_manifest["state"] = "publishing"
                self._write_validated_manifest(manifest_path, staging_manifest)
                moved = self._publish_staged_files(staged_files)
                moved_sidecars = [item for item in moved if item in owned_sidecars]
                moved_assets = [item for item in moved if item not in owned_sidecars]
                from question_bank.services.duplicate_analysis_copy_service import (
                    exact_identity_map,
                    link_new_question_duplicate,
                )
                identities = exact_identity_map(conn, data_root=self.data_root)
                for published_question in published:
                    link_new_question_duplicate(conn, question_id=published_question.bank_question_id,
                                                data_root=self.data_root, identities=identities)
                conn.commit()
            except BaseException:
                conn.rollback()
                self._remove_owned_sidecars(snapshot.operation_id, moved_sidecars)
                self._remove_owned_assets(snapshot.operation_id, moved_assets)
                try:
                    self._remove_staging_tree(snapshot.operation_id)
                except OSError:
                    pass
                if staging_manifest is not None:
                    rolled_back = dict(staging_manifest)
                    rolled_back["state"] = "rolled_back"
                    try:
                        self._write_validated_manifest(manifest_path, rolled_back)
                    except Exception:
                        pass
                raise

        assert receipt is not None
        try:
            self._remove_staging_tree(snapshot.operation_id)
        except OSError:
            pass
        completed = dict(staging_manifest)
        completed["state"] = "published"
        completed["receipt"] = asdict(receipt)
        try:
            self._write_validated_manifest(manifest_path, completed)
        except (
            OSError,
            ValueError,
            DocumentPipelineConflict,
            DocumentPipelineValidationError,
        ):
            # The committed receipt remains authoritative; reconcile repairs this marker.
            pass
        self._reuse_duplicate_analysis(receipt)
        return receipt

    def _reuse_duplicate_analysis(self, receipt: PublishReceipt) -> None:
        from question_bank.services.duplicate_analysis_copy_service import (
            copy_duplicate_analysis,
        )
        with connect(self.db_path) as conn:
            links = [conn.execute("SELECT duplicate_of_question_id FROM question_duplicate_links WHERE question_id=?",
                                  (item.bank_question_id,)).fetchone() for item in receipt.questions]
        for item, link in zip(receipt.questions, links):
            if link is not None:
                copy_duplicate_analysis(self.db_path, source_question_id=int(link[0]),
                                        target_question_id=item.bank_question_id, data_root=self.data_root)

    def reconcile(self, operation_id: str) -> str:
        """Reconcile one explicitly named publication; never scans unrelated files."""

        manifest_path = self._manifest_path(operation_id)
        if not manifest_path.is_file():
            staging_root = self._staging_root(operation_id)
            if staging_root.exists():
                self._remove_staging_tree(operation_id)
                return "rolled_back"
            return "missing_manifest"
        manifest = self._read_json(manifest_path)
        if manifest.get("operation_id") != operation_id:
            raise DocumentPipelineConflict("publication manifest operation mismatch")
        self._validate_manifest(manifest)
        self._assert_schema_available()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT state, source_revision, snapshot_revision, receipt_json
                FROM question_document_publications
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                self._remove_owned_sidecars(
                    operation_id,
                    tuple(str(item) for item in manifest.get("owned_sidecars") or ()),
                )
                self._remove_owned_assets(
                    operation_id,
                    tuple(str(item) for item in manifest.get("owned_assets") or ()),
                )
                self._remove_staging_tree(operation_id)
                manifest["state"] = "rolled_back"
                manifest["owned_sidecars"] = []
                manifest["owned_assets"] = []
                manifest["staged_files"] = []
                self._write_validated_manifest(manifest_path, manifest)
                return "rolled_back"

            if row["receipt_json"]:
                receipt = self._receipt_from_payload(
                    json.loads(str(row["receipt_json"]))
                )
                required = tuple(receipt.asset_paths)
            else:
                receipt = None
                required = tuple(
                    str(item) for item in manifest.get("required_files") or ()
                )
            staged_files = manifest.get("staged_files")
            if isinstance(staged_files, list) and staged_files:
                self._publish_staged_files(
                    [dict(item) for item in staged_files if isinstance(item, Mapping)],
                    allow_missing=True,
                )
            missing = [item for item in required if not self._resolve_data_path(item).is_file()]
            if missing:
                conn.execute(
                    """
                    UPDATE question_document_publications
                    SET state = 'recovery_required', updated_at = datetime('now','localtime')
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
                conn.execute(
                    """
                    UPDATE question_document_items
                    SET state = 'recovery_required', updated_at = datetime('now','localtime')
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
                manifest["state"] = "recovery_required"
                manifest["missing_files"] = missing
                self._write_validated_manifest(manifest_path, manifest)
                return "recovery_required"

            if str(row["state"]) == "recovery_required":
                conn.execute(
                    """
                    UPDATE question_document_publications
                    SET state = 'published', updated_at = datetime('now','localtime')
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
                conn.execute(
                    """
                    UPDATE question_document_items
                    SET state = 'published', updated_at = datetime('now','localtime')
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
            self._remove_staging_tree(operation_id)
            if receipt is not None:
                manifest = self._completed_manifest(receipt)
            else:
                manifest["state"] = "published"
                manifest.pop("missing_files", None)
            self._write_validated_manifest(manifest_path, manifest)
            return "published"

    def _existing_receipt(self, snapshot: DocumentSnapshot) -> PublishReceipt | None:
        with connect(self.db_path) as conn:
            return self._existing_receipt_in_connection(conn, snapshot)

    def _existing_receipt_in_connection(
        self,
        conn: sqlite3.Connection,
        snapshot: DocumentSnapshot,
    ) -> PublishReceipt | None:
        row = conn.execute(
            """
            SELECT source_revision, snapshot_revision, state, receipt_json
            FROM question_document_publications
            WHERE operation_id = ?
            """,
            (snapshot.operation_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["source_revision"]) != snapshot.source_revision
            or str(row["snapshot_revision"]) != snapshot.snapshot_revision
        ):
            raise DocumentPipelineConflict(
                "operation_id is already published from a different document revision"
            )
        if str(row["state"]) != "published" or not row["receipt_json"]:
            raise DocumentPipelineValidationError("publication requires reconciliation")
        return self._receipt_from_payload(json.loads(str(row["receipt_json"])))

    def _stage_source_asset(
        self,
        snapshot: DocumentSnapshot,
        staging_root: Path,
    ) -> tuple[str, dict[str, str]]:
        source = self._resolve_pipeline_asset(snapshot.source_asset)
        suffix = Path(snapshot.source_filename).suffix.casefold() or ".bin"
        destination = (
            self.data_root
            / "question_bank"
            / "raw_papers"
            / (
                f"document-{self._operation_digest(snapshot.operation_id)}-"
                f"{snapshot.source_sha256}{suffix}"
            )
        )
        staged = staging_root / f"source{suffix}"
        self._copy_content_addressed(source, staged, snapshot.source_sha256)
        return (
            self._relative_data_path(destination),
            self._staged_file_record(
                staged=staged,
                final=destination,
                expected_hash=snapshot.source_sha256,
                kind="asset",
            ),
        )

    def _stage_page_assets(
        self,
        snapshot: DocumentSnapshot,
        staging_root: Path,
    ) -> tuple[list[str], list[dict[str, str]]]:
        destinations: list[str] = []
        staged_files: list[dict[str, str]] = []
        for page in snapshot.pages:
            source = self._resolve_pipeline_asset(page.rendered_asset)
            destination = (
                self.data_root
                / "question_bank"
                / "document_pages"
                / (
                    f"{self._operation_digest(snapshot.operation_id)}-"
                    f"page-{page.page_number:04d}-{page.rendered_sha256}.png"
                )
            )
            staged = staging_root / f"page-{page.page_number:04d}.png"
            self._copy_content_addressed(source, staged, page.rendered_sha256)
            destinations.append(self._relative_data_path(destination))
            staged_files.append(
                self._staged_file_record(
                    staged=staged,
                    final=destination,
                    expected_hash=page.rendered_sha256,
                    kind="asset",
                )
            )
        return destinations, staged_files

    def _staged_file_record(
        self,
        *,
        staged: Path,
        final: Path,
        expected_hash: str,
        kind: str,
    ) -> dict[str, str]:
        return {
            "staged_path": self._relative_data_path(staged),
            "final_path": self._relative_data_path(final),
            "sha256": str(expected_hash),
            "kind": str(kind),
        }

    def _publish_staged_files(
        self,
        staged_files: list[dict[str, str]],
        *,
        allow_missing: bool = False,
    ) -> list[str]:
        moved: list[str] = []
        for item in staged_files:
            staged_relative = str(item.get("staged_path") or "")
            final_relative = str(item.get("final_path") or "")
            expected_hash = str(item.get("sha256") or "")
            staged = self._resolve_data_path(staged_relative)
            final = self._resolve_data_path(final_relative)
            if final.is_file():
                if sha256_bytes(final.read_bytes()) != expected_hash:
                    raise DocumentPipelineConflict(
                        f"immutable publication asset hash mismatch: {final.name}"
                    )
                if staged.is_file():
                    staged.unlink()
                continue
            if not staged.is_file():
                if allow_missing:
                    continue
                raise DocumentPipelineValidationError(
                    f"staged publication asset is missing: {staged.name}"
                )
            if sha256_bytes(staged.read_bytes()) != expected_hash:
                raise DocumentPipelineValidationError(
                    "staged publication asset failed hash validation"
                )
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, final)
            if sha256_bytes(final.read_bytes()) != expected_hash:
                raise DocumentPipelineValidationError(
                    "published asset failed hash validation"
                )
            moved.append(final_relative)
        return moved

    def _sidecar_payload(
        self,
        snapshot: DocumentSnapshot,
        question: QuestionDraft,
        bank_question_id: int,
        page_assets: list[str],
    ) -> dict[str, object]:
        # Use explicitly recognised illustration/table regions when available.
        # Unclassified source pages retain the complete question crop so a
        # missing figure classification can never cause a text-only merge.
        identity_regions = []
        for region in question.source_regions:
            page = next((item for item in snapshot.pages if item.page_number == region.page_number), None)
            if page is None or not page.blocks:
                identity_regions.append(asdict(region))
                continue
            x1, y1, x2, y2 = region.bbox
            for block in page.blocks:
                bx1, by1, bx2, by2 = block.region.bbox
                if str(block.kind.value) in {"figure", "table"} and max(x1, bx1) < min(x2, bx2) and max(y1, by1) < min(y2, by2):
                    identity_regions.append(asdict(block.region))
        return {
            "version": 3,
            "question_id": bank_question_id,
            "operation_id": snapshot.operation_id,
            "source_revision": snapshot.source_revision,
            "content_revision": question.content_revision,
            "source_regions": [asdict(item) for item in question.source_regions],
            "identity_regions": identity_regions,
            "source_page_assets": page_assets,
            "math_expressions": [asdict(item) for item in question.math_expressions],
            "question_blocks": [],
            "answer_blocks": [],
        }

    def _write_owned_sidecar(
        self,
        path: Path,
        payload: Mapping[str, object],
        *,
        operation_id: str,
    ) -> None:
        if path.is_file():
            existing = self._read_json(path)
            if existing.get("operation_id") != operation_id:
                raise DocumentPipelineConflict(
                    f"question sidecar path is already owned: {path.name}"
                )
        self._write_json(path, payload)

    def _remove_owned_sidecars(
        self,
        operation_id: str,
        relative_paths: Iterable[str],
    ) -> None:
        for relative in relative_paths:
            try:
                path = self._resolve_data_path(relative)
                if not path.is_file():
                    continue
                payload = self._read_json(path)
                if payload.get("operation_id") == operation_id:
                    path.unlink()
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    def _remove_owned_assets(
        self,
        operation_id: str,
        relative_paths: Iterable[str],
    ) -> None:
        digest = self._operation_digest(operation_id)
        allowed_parents = {
            (self.data_root / "question_bank" / "raw_papers").resolve(),
            (self.data_root / "question_bank" / "document_pages").resolve(),
        }
        for relative in relative_paths:
            try:
                path = self._resolve_data_path(relative)
                if path.parent.resolve() not in allowed_parents or digest not in path.name:
                    continue
                if path.is_file():
                    path.unlink()
            except (OSError, ValueError):
                continue

    def _assert_schema_available(self) -> None:
        try:
            with connect(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'question_document_publications'
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise DocumentPipelineValidationError("question bank database is unavailable") from exc
        if row is None:
            raise DocumentPipelineValidationError(
                "question document publication schema is not installed"
            )

    def _resolve_pipeline_asset(self, relative_path: str) -> Path:
        candidate = self._safe_join(self.pipeline_workspace, relative_path)
        if not candidate.is_file():
            raise DocumentPipelineValidationError(
                f"pipeline asset is missing: {relative_path}"
            )
        return candidate

    def _resolve_data_path(self, relative_path: str) -> Path:
        return self._safe_join(self.data_root, relative_path)

    @staticmethod
    def _safe_join(root: Path, relative_path: str) -> Path:
        pure = PurePosixPath(str(relative_path or ""))
        if pure.is_absolute() or not pure.parts or ".." in pure.parts:
            raise DocumentPipelineValidationError("asset path is not controlled")
        candidate = (root / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DocumentPipelineValidationError("asset path escapes its root") from exc
        return candidate

    def _relative_data_path(self, path: Path) -> str:
        candidate = path.resolve()
        try:
            return candidate.relative_to(self.data_root).as_posix()
        except ValueError as exc:
            raise DocumentPipelineValidationError("publication path escapes data root") from exc

    def _manifest_path(self, operation_id: str) -> Path:
        digest = canonical_hash({"operation_id": operation_id})
        return self.manifest_root / f"publication-{digest}.json"

    def _staging_root(self, operation_id: str) -> Path:
        return self.manifest_root / f".staging-{self._operation_digest(operation_id)}"

    def _remove_staging_tree(self, operation_id: str) -> None:
        staging = self._staging_root(operation_id)
        expected_parent = self.manifest_root.resolve()
        if staging.parent.resolve() != expected_parent or staging.is_symlink():
            raise DocumentPipelineValidationError(
                "publication staging path is not controlled"
            )
        if staging.is_dir():
            shutil.rmtree(staging)
        elif staging.exists():
            raise DocumentPipelineValidationError(
                "publication staging path is not a directory"
            )

    def _write_validated_manifest(
        self,
        path: Path,
        payload: Mapping[str, object],
    ) -> None:
        self._write_json(path, payload)
        persisted = self._read_json(path)
        self._validate_manifest(persisted)
        expected = json.loads(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if persisted != expected:
            raise DocumentPipelineConflict(
                "publication manifest changed while it was being written"
            )

    def _validate_manifest(self, manifest: Mapping[str, object]) -> None:
        version = int(manifest.get("version") or 0)
        operation_id = str(manifest.get("operation_id") or "")
        if version not in {1, 2} or not operation_id:
            raise DocumentPipelineValidationError("publication manifest is invalid")
        for field in ("source_revision", "snapshot_revision", "manifest_sha256"):
            value = str(manifest.get(field) or "")
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise DocumentPipelineValidationError(
                    f"publication manifest {field} is invalid"
                )
        if str(manifest.get("state") or "") not in {
            "staging",
            "publishing",
            "published",
            "rolled_back",
            "recovery_required",
        }:
            raise DocumentPipelineValidationError("publication manifest state is invalid")
        path_fields = ("required_files", "owned_assets", "owned_sidecars")
        normalized: dict[str, list[str]] = {}
        for field in path_fields:
            raw = manifest.get(field)
            if not isinstance(raw, list) or not all(
                isinstance(item, str) and bool(item) for item in raw
            ):
                raise DocumentPipelineValidationError(
                    f"publication manifest {field} is invalid"
                )
            values = [str(item) for item in raw]
            if len(values) != len(set(values)):
                raise DocumentPipelineValidationError(
                    f"publication manifest {field} contains duplicates"
                )
            for value in values:
                self._resolve_data_path(value)
            normalized[field] = values
        if str(manifest.get("state")) not in {"rolled_back"} and set(
            normalized["owned_assets"] + normalized["owned_sidecars"]
        ) != set(normalized["required_files"]):
            raise DocumentPipelineValidationError(
                "publication manifest ownership is incomplete"
            )
        if version == 1:
            return
        staging_relative = str(manifest.get("staging_directory") or "")
        expected_staging = self._staging_root(operation_id).resolve()
        if self._resolve_data_path(staging_relative).resolve() != expected_staging:
            raise DocumentPipelineValidationError(
                "publication manifest staging directory is invalid"
            )
        raw_staged = manifest.get("staged_files")
        if not isinstance(raw_staged, list):
            raise DocumentPipelineValidationError(
                "publication manifest staged files are invalid"
            )
        seen_final: set[str] = set()
        seen_staged: set[str] = set()
        for item in raw_staged:
            if not isinstance(item, Mapping) or set(item) != {
                "staged_path",
                "final_path",
                "sha256",
                "kind",
            }:
                raise DocumentPipelineValidationError(
                    "publication manifest staged file is invalid"
                )
            staged_relative = str(item.get("staged_path") or "")
            final_relative = str(item.get("final_path") or "")
            expected_hash = str(item.get("sha256") or "")
            if (
                not staged_relative
                or not final_relative
                or final_relative not in normalized["required_files"]
                or str(item.get("kind") or "") not in {"asset", "sidecar"}
                or len(expected_hash) != 64
                or any(ch not in "0123456789abcdef" for ch in expected_hash)
            ):
                raise DocumentPipelineValidationError(
                    "publication manifest staged file fields are invalid"
                )
            staged_path = self._resolve_data_path(staged_relative).resolve()
            try:
                staged_path.relative_to(expected_staging)
            except ValueError as exc:
                raise DocumentPipelineValidationError(
                    "publication manifest staged file escapes staging"
                ) from exc
            self._resolve_data_path(final_relative)
            if final_relative in seen_final or staged_relative in seen_staged:
                raise DocumentPipelineValidationError(
                    "publication manifest staged file is duplicated"
                )
            seen_final.add(final_relative)
            seen_staged.add(staged_relative)

    def _completed_manifest(self, receipt: PublishReceipt) -> dict[str, object]:
        sidecars = [
            item
            for item in receipt.asset_paths
            if PurePosixPath(item).parts[:2] == ("question_bank", "rich_content")
        ]
        assets = [item for item in receipt.asset_paths if item not in sidecars]
        return {
            "version": 2,
            "operation_id": receipt.operation_id,
            "source_revision": receipt.source_revision,
            "snapshot_revision": receipt.snapshot_revision,
            "manifest_sha256": receipt.manifest_sha256,
            "state": "published",
            "required_files": list(receipt.asset_paths),
            "owned_assets": assets,
            "owned_sidecars": sidecars,
            "staging_directory": self._relative_data_path(
                self._staging_root(receipt.operation_id)
            ),
            "staged_files": [],
            "receipt": asdict(receipt),
        }

    @staticmethod
    def _operation_digest(operation_id: str) -> str:
        return canonical_hash({"operation_id": operation_id})[:24]

    def _sidecar_path(self, bank_question_id: int) -> Path:
        return (
            self.data_root
            / "question_bank"
            / "rich_content"
            / f"question_{bank_question_id}.json"
        )

    @staticmethod
    def _copy_content_addressed(source: Path, destination: Path, expected_hash: str) -> None:
        if destination.is_file():
            if sha256_bytes(destination.read_bytes()) != expected_hash:
                raise DocumentPipelineConflict(
                    f"content-addressed publication asset hash mismatch: {destination.name}"
                )
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            staging = Path(handle.name)
        try:
            shutil.copyfile(source, staging)
            if sha256_bytes(staging.read_bytes()) != expected_hash:
                raise DocumentPipelineValidationError("publication asset failed hash validation")
            os.replace(staging, destination)
        finally:
            if staging.exists():
                staging.unlink()

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            staging = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(staging, path)
        finally:
            if staging.exists():
                staging.unlink()

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _page_range(question: QuestionDraft) -> str:
        pages = sorted({item.page_number for item in question.source_regions})
        if not pages:
            return ""
        if len(pages) == 1:
            return str(pages[0])
        if pages == list(range(pages[0], pages[-1] + 1)):
            return f"{pages[0]}-{pages[-1]}"
        return ",".join(str(item) for item in pages)

    @staticmethod
    def _receipt_from_payload(payload: Mapping[str, object]) -> PublishReceipt:
        return PublishReceipt(
            operation_id=str(payload["operation_id"]),
            source_revision=str(payload["source_revision"]),
            snapshot_revision=str(payload["snapshot_revision"]),
            state=PublishState(str(payload["state"])),
            questions=tuple(
                PublishedQuestion(
                    source_question_id=str(item["source_question_id"]),
                    bank_question_id=int(item["bank_question_id"]),
                    content_revision=str(item["content_revision"]),
                )
                for item in payload.get("questions") or ()  # type: ignore[union-attr]
            ),
            manifest_sha256=str(payload["manifest_sha256"]),
            asset_paths=tuple(str(item) for item in payload.get("asset_paths") or ()),
        )


__all__ = ["QuestionBankPublicationAdapter"]
