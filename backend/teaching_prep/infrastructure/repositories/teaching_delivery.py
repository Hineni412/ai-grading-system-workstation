from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import (
    ClassVariant,
    PostLessonReview,
    UpClassPackage,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class TeachingDeliveryRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def create_class_variant(
        self,
        *,
        request_token: str,
        request_hash: str,
        base_resource_pack_id: str,
        resource_pack_id: str,
        lesson_node_id: str,
        class_name: str,
        prior_review_ids: tuple[str, ...],
    ) -> tuple[ClassVariant, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM class_variants WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different class variant"
                    )
                return _class_variant(existing), False
            variant_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO class_variants (
                    id, request_token, request_hash, base_resource_pack_id,
                    resource_pack_id, lesson_node_id, class_name,
                    prior_review_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant_id,
                    request_token,
                    request_hash,
                    base_resource_pack_id,
                    resource_pack_id,
                    lesson_node_id,
                    class_name,
                    _json(list(prior_review_ids)),
                ),
            )
            row = connection.execute(
                "SELECT * FROM class_variants WHERE id = ?",
                (variant_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("class variant could not be loaded")
        return _class_variant(row), True

    def get_class_variant_by_token(
        self,
        request_token: str,
    ) -> ClassVariant | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM class_variants WHERE request_token = ?",
                (request_token,),
            ).fetchone()
        return _class_variant(row) if row is not None else None

    def list_class_variants(
        self,
        lesson_node_id: str,
    ) -> tuple[ClassVariant, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM class_variants
                WHERE lesson_node_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (lesson_node_id,),
            ).fetchall()
        return tuple(_class_variant(row) for row in rows)

    def begin_package(
        self,
        *,
        request_token: str,
        request_hash: str,
        pptx_version_id: str,
        slide_plan_id: str,
        lesson_draft_id: str,
        resource_pack_id: str,
        lesson_node_id: str,
        class_name: str | None,
        class_scope_key: str,
    ) -> tuple[UpClassPackage, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id, request_hash FROM up_class_packages WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different up-class package"
                    )
                row = self._package_query(
                    connection,
                    "package.id = ?",
                    (str(existing["id"]),),
                )
                assert row is not None
                return _package(row), False
            duplicate = connection.execute(
                "SELECT id FROM up_class_packages WHERE pptx_version_id = ?",
                (pptx_version_id,),
            ).fetchone()
            if duplicate is not None:
                raise TeachingPrepConflictError(
                    "published PPTX already has an up-class package"
                )
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM up_class_packages
                    WHERE lesson_node_id = ? AND class_scope_key = ?
                    """,
                    (lesson_node_id, class_scope_key),
                ).fetchone()[0]
            )
            package_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO up_class_packages (
                    id, request_token, request_hash, pptx_version_id,
                    slide_plan_id, lesson_draft_id, resource_pack_id,
                    lesson_node_id, class_name, class_scope_key,
                    version_number, status, staging_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'building', ?)
                """,
                (
                    package_id,
                    request_token,
                    request_hash,
                    pptx_version_id,
                    slide_plan_id,
                    lesson_draft_id,
                    resource_pack_id,
                    lesson_node_id,
                    class_name,
                    class_scope_key,
                    version_number,
                    package_id,
                ),
            )
            row = self._package_query(
                connection,
                "package.id = ?",
                (package_id,),
            )
        if row is None:
            raise RuntimeError("up-class package could not be loaded")
        return _package(row), True

    def get_package(self, package_id: str) -> UpClassPackage:
        with self._database.connect() as connection:
            row = self._package_query(
                connection,
                "package.id = ?",
                (package_id,),
            )
        if row is None:
            raise TeachingPrepNotFoundError("up-class package was not found")
        return _package(row)

    def list_packages(
        self,
        lesson_node_id: str,
    ) -> tuple[UpClassPackage, ...]:
        with self._database.connect() as connection:
            rows = self._packages_query(
                connection,
                "package.lesson_node_id = ?",
                (lesson_node_id,),
                "package.created_at DESC, package.id DESC",
            )
        return tuple(_package(row) for row in rows)

    def set_package_publishing(
        self,
        package_id: str,
        *,
        output_relpath: str,
        output_filename: str,
        package_sha256: str,
        manifest: dict[str, object],
    ) -> UpClassPackage:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE up_class_packages
                SET status = 'publishing',
                    output_relpath = ?,
                    output_filename = ?,
                    package_sha256 = ?,
                    manifest_json = ?,
                    error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'building'
                """,
                (
                    output_relpath,
                    output_filename,
                    package_sha256,
                    _json(manifest),
                    package_id,
                ),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "up-class package is no longer being built"
                )
            row = self._package_query(
                connection,
                "package.id = ?",
                (package_id,),
            )
        assert row is not None
        return _package(row)

    def finish_package(self, package_id: str) -> UpClassPackage:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT lesson_node_id, class_scope_key
                FROM up_class_packages
                WHERE id = ? AND status IN ('publishing', 'interrupted')
                  AND output_relpath IS NOT NULL
                  AND package_sha256 IS NOT NULL
                  AND manifest_json IS NOT NULL
                """,
                (package_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepConflictError(
                    "up-class package is not ready to publish"
                )
            cursor = connection.execute(
                """
                UPDATE up_class_packages
                SET status = 'complete',
                    error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status IN ('publishing', 'interrupted')
                """,
                (package_id,),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "up-class package publication state changed"
                )
            self._insert_selection(
                connection,
                request_token=f"published:{package_id}",
                request_hash=str(
                    connection.execute(
                        "SELECT request_hash FROM up_class_packages WHERE id = ?",
                        (package_id,),
                    ).fetchone()[0]
                ),
                lesson_node_id=str(row["lesson_node_id"]),
                class_scope_key=str(row["class_scope_key"]),
                package_id=package_id,
                reason="published",
            )
            loaded = self._package_query(
                connection,
                "package.id = ?",
                (package_id,),
            )
        assert loaded is not None
        return _package(loaded)

    def fail_package(self, package_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE up_class_packages
                SET status = 'failed',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status IN ('building', 'publishing')
                """,
                (error_code, package_id),
            )

    def interrupt_package(self, package_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE up_class_packages
                SET status = 'interrupted',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'publishing'
                """,
                (error_code, package_id),
            )

    def mark_interrupted_packages(self) -> int:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE up_class_packages
                SET status = 'interrupted',
                    error_code = 'application_restarted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status IN ('building', 'publishing')
                """
            )
            return int(cursor.rowcount)

    def package_storage(self, package_id: str) -> tuple[str, str | None]:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT staging_name, output_relpath
                FROM up_class_packages WHERE id = ?
                """,
                (package_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("up-class package was not found")
        return (
            str(row["staging_name"]),
            str(row["output_relpath"])
            if row["output_relpath"] is not None
            else None,
        )

    def activate_package(
        self,
        package_id: str,
        *,
        request_token: str,
        request_hash: str,
    ) -> tuple[UpClassPackage, bool]:
        with self._database.connect(immediate=True) as connection:
            package = connection.execute(
                """
                SELECT lesson_node_id, class_scope_key, status
                FROM up_class_packages WHERE id = ?
                """,
                (package_id,),
            ).fetchone()
            if package is None or str(package["status"]) != "complete":
                raise TeachingPrepNotFoundError(
                    "complete up-class package was not found"
                )
            existing = connection.execute(
                """
                SELECT request_hash, package_id
                FROM up_class_package_selections
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["request_hash"]) != request_hash
                    or str(existing["package_id"]) != package_id
                ):
                    raise TeachingPrepConflictError(
                        "request token was reused for a different package selection"
                    )
                row = self._package_query(
                    connection,
                    "package.id = ?",
                    (package_id,),
                )
                assert row is not None
                return _package(row), False
            self._insert_selection(
                connection,
                request_token=request_token,
                request_hash=request_hash,
                lesson_node_id=str(package["lesson_node_id"]),
                class_scope_key=str(package["class_scope_key"]),
                package_id=package_id,
                reason="rollback",
            )
            row = self._package_query(
                connection,
                "package.id = ?",
                (package_id,),
            )
        assert row is not None
        return _package(row), True

    def create_review(
        self,
        *,
        request_token: str,
        request_hash: str,
        package_id: str,
        pptx_version_id: str,
        lesson_node_id: str,
        class_name: str | None,
        payload: dict[str, object],
        use_in_next_version: bool,
    ) -> tuple[PostLessonReview, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM post_lesson_reviews WHERE request_token = ?",
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different post-lesson review"
                    )
                return _review(existing), False
            review_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO post_lesson_reviews (
                    id, request_token, request_hash, package_id,
                    pptx_version_id, lesson_node_id, class_name,
                    payload_json, use_in_next_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    request_token,
                    request_hash,
                    package_id,
                    pptx_version_id,
                    lesson_node_id,
                    class_name,
                    _json(payload),
                    int(use_in_next_version),
                ),
            )
            row = connection.execute(
                "SELECT * FROM post_lesson_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("post-lesson review could not be loaded")
        return _review(row), True

    def get_reviews(
        self,
        review_ids: tuple[str, ...],
    ) -> tuple[PostLessonReview, ...]:
        if not review_ids:
            return ()
        placeholders = ",".join("?" for _ in review_ids)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM post_lesson_reviews WHERE id IN ({placeholders})",
                review_ids,
            ).fetchall()
        by_id = {str(row["id"]): _review(row) for row in rows}
        if set(by_id) != set(review_ids):
            raise TeachingPrepNotFoundError(
                "one or more post-lesson reviews were not found"
            )
        return tuple(by_id[item] for item in review_ids)

    def list_reviews(
        self,
        lesson_node_id: str,
    ) -> tuple[PostLessonReview, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM post_lesson_reviews
                WHERE lesson_node_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (lesson_node_id,),
            ).fetchall()
        return tuple(_review(row) for row in rows)

    @staticmethod
    def _insert_selection(
        connection: sqlite3.Connection,
        *,
        request_token: str,
        request_hash: str,
        lesson_node_id: str,
        class_scope_key: str,
        package_id: str,
        reason: str,
    ) -> None:
        previous = connection.execute(
            """
            SELECT package_id
            FROM up_class_package_selections
            WHERE lesson_node_id = ? AND class_scope_key = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (lesson_node_id, class_scope_key),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO up_class_package_selections (
                id, request_token, request_hash, lesson_node_id,
                class_scope_key, package_id, previous_package_id, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                request_token,
                request_hash,
                lesson_node_id,
                class_scope_key,
                package_id,
                str(previous["package_id"]) if previous is not None else None,
                reason,
            ),
        )

    @classmethod
    def _package_query(
        cls,
        connection: sqlite3.Connection,
        where: str,
        parameters: tuple[object, ...],
    ) -> sqlite3.Row | None:
        rows = cls._packages_query(
            connection,
            where,
            parameters,
            "package.created_at DESC",
        )
        return rows[0] if rows else None

    @staticmethod
    def _packages_query(
        connection: sqlite3.Connection,
        where: str,
        parameters: tuple[object, ...],
        order_by: str,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            f"""
            SELECT package.*,
                   CASE WHEN package.id = (
                       SELECT selection.package_id
                       FROM up_class_package_selections AS selection
                       WHERE selection.lesson_node_id = package.lesson_node_id
                         AND selection.class_scope_key = package.class_scope_key
                       ORDER BY selection.rowid DESC
                       LIMIT 1
                   ) THEN 1 ELSE 0 END AS is_current
            FROM up_class_packages AS package
            WHERE {where}
            ORDER BY {order_by}
            """,
            parameters,
        ).fetchall()


def _class_variant(row: sqlite3.Row) -> ClassVariant:
    return ClassVariant(
        id=str(row["id"]),
        base_resource_pack_id=str(row["base_resource_pack_id"]),
        resource_pack_id=str(row["resource_pack_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        class_name=str(row["class_name"]),
        prior_review_ids=tuple(json.loads(str(row["prior_review_ids_json"]))),
        created_at=str(row["created_at"]),
    )


def _package(row: sqlite3.Row) -> UpClassPackage:
    status = str(row["status"])
    return UpClassPackage(
        id=str(row["id"]),
        pptx_version_id=str(row["pptx_version_id"]),
        slide_plan_id=str(row["slide_plan_id"]),
        lesson_draft_id=str(row["lesson_draft_id"]),
        resource_pack_id=str(row["resource_pack_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        class_name=(
            str(row["class_name"]) if row["class_name"] is not None else None
        ),
        version_number=int(row["version_number"]),
        status=status,
        output_filename=(
            str(row["output_filename"])
            if row["output_filename"] is not None
            else None
        ),
        package_sha256=(
            str(row["package_sha256"])
            if row["package_sha256"] is not None
            else None
        ),
        manifest=(
            json.loads(str(row["manifest_json"]))
            if row["manifest_json"] is not None
            else None
        ),
        error_code=(
            str(row["error_code"]) if row["error_code"] is not None else None
        ),
        is_current=bool(row["is_current"]),
        staging_retained=False,
        recovery_actions=(
            ("resume_publish", "discard_staging")
            if status == "interrupted"
            else ("discard_staging",)
            if status == "failed"
            else ()
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=(
            str(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
    )


def _review(row: sqlite3.Row) -> PostLessonReview:
    return PostLessonReview(
        id=str(row["id"]),
        package_id=str(row["package_id"]),
        pptx_version_id=str(row["pptx_version_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        class_name=(
            str(row["class_name"]) if row["class_name"] is not None else None
        ),
        payload=json.loads(str(row["payload_json"])),
        use_in_next_version=bool(row["use_in_next_version"]),
        created_at=str(row["created_at"]),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["TeachingDeliveryRepository"]
