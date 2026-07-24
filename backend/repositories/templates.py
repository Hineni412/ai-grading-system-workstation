"""Template and answer-region persistence boundaries."""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from backend.repositories.base import (
    RepositorySession,
    RepositorySessionProvider,
)
from backend.status_contracts import validate_status


class TemplateRepository:
    """Session-bound template SQL without transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def upsert_session_template(
        self,
        session_id: int,
        front_template_path: str,
        back_template_path: str,
    ) -> int:
        existing = self.session.connection.execute(
            "SELECT id FROM session_templates WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
        if existing:
            template_id = int(existing["id"])
            self.session.connection.execute(
                """
                UPDATE session_templates
                SET front_template_path = ?,
                    back_template_path = ?,
                    is_confirmed = 0,
                    regions_snapshot_pending = 0,
                    regions_snapshot_token = NULL,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (front_template_path, back_template_path, template_id),
            )
            return template_id
        cursor = self.session.connection.execute(
            """
            INSERT INTO session_templates (
                session_id, front_template_path, back_template_path,
                is_confirmed
            ) VALUES (?, ?, ?, 0)
            """,
            (int(session_id), front_template_path, back_template_path),
        )
        return int(cursor.lastrowid)

    def activate_session_template(
        self,
        session_id: int,
        *,
        front_template_path: str,
        back_template_path: str,
        ai_analysis_path: str,
        template_config_path: str,
        regions_path: str,
    ) -> int:
        existing = self.session.connection.execute(
            "SELECT id FROM session_templates WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
        values = (
            front_template_path,
            back_template_path,
            ai_analysis_path,
            template_config_path,
            regions_path,
        )
        if existing:
            template_id = int(existing["id"])
            self.session.connection.execute(
                """
                UPDATE session_templates
                SET front_template_path = ?,
                    back_template_path = ?,
                    ai_analysis_path = ?,
                    template_config_path = ?,
                    regions_path = ?,
                    is_confirmed = 0,
                    regions_snapshot_pending = 0,
                    regions_snapshot_token = NULL,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (*values, template_id),
            )
            return template_id
        cursor = self.session.connection.execute(
            """
            INSERT INTO session_templates (
                session_id, front_template_path, back_template_path,
                ai_analysis_path, template_config_path, regions_path,
                is_confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (int(session_id), *values),
        )
        return int(cursor.lastrowid)

    def get_session_template(
        self,
        session_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT id, session_id, front_template_path, back_template_path,
                   ai_analysis_path, template_config_path, regions_path,
                   is_confirmed, regions_snapshot_pending,
                   regions_snapshot_token, created_at, updated_at
            FROM session_templates
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchone()
        return dict(row) if row else None

    def update_session_template_analysis(
        self,
        session_id: int,
        *,
        ai_analysis_path: str | None,
        template_config_path: str | None,
        regions_path: str | None,
    ) -> None:
        self.session.connection.execute(
            """
            UPDATE session_templates
            SET ai_analysis_path = ?,
                template_config_path = ?,
                regions_path = ?,
                is_confirmed = 0,
                regions_snapshot_pending = 0,
                regions_snapshot_token = NULL,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
            """,
            (
                ai_analysis_path,
                template_config_path,
                regions_path,
                int(session_id),
            ),
        )

    def owns_template(self, session_id: int, template_id: int) -> bool:
        row = self.session.connection.execute(
            """
            SELECT 1
            FROM session_templates
            WHERE id = ? AND session_id = ?
            """,
            (int(template_id), int(session_id)),
        ).fetchone()
        return row is not None

    def set_region_snapshot_generation(
        self,
        session_id: int,
        *,
        confirmed: bool,
        snapshot_token: str,
    ) -> None:
        self.session.connection.execute(
            """
            UPDATE session_templates
            SET is_confirmed = ?,
                regions_snapshot_pending = 1,
                regions_snapshot_token = ?,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
            """,
            (
                1 if confirmed else 0,
                str(snapshot_token),
                int(session_id),
            ),
        )

    def mark_region_snapshot_complete(
        self,
        session_id: int,
        *,
        expected_token: str,
    ) -> bool:
        cursor = self.session.connection.execute(
            """
            UPDATE session_templates
            SET regions_snapshot_pending = 0,
                regions_snapshot_token = NULL,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
              AND regions_snapshot_pending = 1
              AND regions_snapshot_token = ?
            """,
            (int(session_id), expected_token),
        )
        return cursor.rowcount > 0

    def mark_template_confirmed(
        self,
        session_id: int,
        confirmed: bool = True,
    ) -> None:
        self.session.connection.execute(
            """
            UPDATE session_templates
            SET is_confirmed = ?,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
            """,
            (1 if confirmed else 0, int(session_id)),
        )

    def get_session_storage_path_row(
        self,
        session_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT front_template_path, back_template_path, ai_analysis_path,
                   template_config_path, regions_path
            FROM session_templates
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchone()
        return dict(row) if row else None

    def delete_session_templates(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM session_templates WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))


class RegionRepository:
    """Session-bound answer-region SQL without transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def insert_answer_region(
        self,
        session_id: int,
        template_id: int,
        region: dict[str, Any],
    ) -> int:
        raw_region_uuid = region.get("region_uuid")
        region_uuid = (
            str(raw_region_uuid)
            if raw_region_uuid is not None and str(raw_region_uuid).strip()
            else str(uuid4())
        )
        mapped_question_id = region.get("mapped_question_id")
        raw_mapping_status = region.get("mapping_status")
        mapping_status = (
            str(raw_mapping_status)
            if raw_mapping_status is not None
            and str(raw_mapping_status).strip()
            else (
                "manual"
                if mapped_question_id is not None
                and str(mapped_question_id).strip()
                else "unbound"
            )
        )
        mapping_status = validate_status(
            "answer_regions.mapping_status",
            mapping_status,
        )
        cursor = self.session.connection.execute(
            """
            INSERT INTO answer_regions (
                region_uuid, session_id, template_id, page, region_order,
                x, y, w, h, detected_question_id, mapped_question_id,
                confidence, is_confirmed, mapping_status,
                multi_region_confirmed, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now','localtime')
            )
            """,
            (
                region_uuid,
                int(session_id),
                int(template_id),
                region.get("page", "front"),
                int(region.get("region_order", 0)),
                int(region.get("x", 0)),
                int(region.get("y", 0)),
                int(region.get("w", 0)),
                int(region.get("h", 0)),
                region.get("detected_question_id"),
                mapped_question_id,
                float(region.get("confidence", 0.0)),
                1 if region.get("is_confirmed") else 0,
                mapping_status,
                1 if region.get("multi_region_confirmed") else 0,
            ),
        )
        return int(cursor.lastrowid)

    def replace_answer_regions(
        self,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        *,
        confirmed: bool = False,
    ) -> None:
        self.session.connection.execute(
            "DELETE FROM answer_regions WHERE session_id = ?",
            (int(session_id),),
        )
        for raw_region in regions:
            region = dict(raw_region)
            if confirmed:
                region["is_confirmed"] = True
            self.insert_answer_region(session_id, template_id, region)

    def list_answer_regions(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT id, region_uuid, session_id, template_id, page,
                   region_order, x, y, w, h, detected_question_id,
                   mapped_question_id, confidence, is_confirmed,
                   mapping_status, multi_region_confirmed,
                   created_at, updated_at
            FROM answer_regions
            WHERE session_id = ?
            ORDER BY
                CASE page WHEN 'front' THEN 1 ELSE 2 END,
                region_order ASC
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def bulk_update_answer_region_mapping(
        self,
        session_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            mapped_question_id = row.get("mapped_question_id")
            raw_mapping_status = row.get("mapping_status")
            if (
                raw_mapping_status is None
                or not str(raw_mapping_status).strip()
            ):
                mapping_status = (
                    "manual"
                    if mapped_question_id is not None
                    and str(mapped_question_id).strip()
                    else "unbound"
                )
            else:
                mapping_status = validate_status(
                    "answer_regions.mapping_status",
                    str(raw_mapping_status),
                )
            self.session.connection.execute(
                """
                UPDATE answer_regions
                SET mapped_question_id = ?,
                    mapping_status = ?,
                    is_confirmed = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND session_id = ?
                """,
                (
                    mapped_question_id,
                    mapping_status,
                    1 if row.get("is_confirmed") else 0,
                    int(row.get("id")),
                    int(session_id),
                ),
            )

    def delete_answer_region(self, region_id: int) -> None:
        self.session.connection.execute(
            "DELETE FROM answer_regions WHERE id = ?",
            (int(region_id),),
        )

    def update_answer_region_bbox(
        self,
        region_id: int,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> None:
        self.session.connection.execute(
            """
            UPDATE answer_regions
            SET x = ?, y = ?, w = ?, h = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (int(x), int(y), int(w), int(h), int(region_id)),
        )

    def readiness_counts(self, session_id: int) -> tuple[int, int]:
        total = self.session.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM answer_regions
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchone()["count"]
        confirmed = self.session.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM answer_regions
            WHERE session_id = ?
              AND is_confirmed = 1
              AND COALESCE(mapped_question_id, '') <> ''
            """,
            (int(session_id),),
        ).fetchone()["count"]
        return int(total or 0), int(confirmed or 0)

    def delete_session_regions(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM answer_regions WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))


class TemplateRegionRepositoryGateway:
    """Coordinate template and answer-region repositories."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def upsert_session_template(
        self,
        session_id: int,
        front_template_path: str,
        back_template_path: str,
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return TemplateRepository(session).upsert_session_template(
                    session_id,
                    front_template_path,
                    back_template_path,
                )

    def activate_session_template(
        self,
        session_id: int,
        *,
        front_template_path: str,
        back_template_path: str,
        ai_analysis_path: str,
        template_config_path: str,
        regions_path: str,
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return TemplateRepository(
                    session
                ).activate_session_template(
                    session_id,
                    front_template_path=front_template_path,
                    back_template_path=back_template_path,
                    ai_analysis_path=ai_analysis_path,
                    template_config_path=template_config_path,
                    regions_path=regions_path,
                )

    def get_session_template(
        self,
        session_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return TemplateRepository(session).get_session_template(session_id)

    def update_session_template_analysis(
        self,
        session_id: int,
        *,
        ai_analysis_path: str | None,
        template_config_path: str | None,
        regions_path: str | None,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                TemplateRepository(
                    session
                ).update_session_template_analysis(
                    session_id,
                    ai_analysis_path=ai_analysis_path,
                    template_config_path=template_config_path,
                    regions_path=regions_path,
                )

    def save_answer_regions(
        self,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                RegionRepository(session).replace_answer_regions(
                    session_id,
                    template_id,
                    regions,
                )

    def list_answer_regions(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return RegionRepository(session).list_answer_regions(session_id)

    def bulk_update_answer_region_mapping(
        self,
        session_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                RegionRepository(
                    session
                ).bulk_update_answer_region_mapping(session_id, rows)

    def add_answer_region(
        self,
        session_id: int,
        template_id: int,
        region: dict[str, Any],
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return RegionRepository(session).insert_answer_region(
                    session_id,
                    template_id,
                    region,
                )

    def replace_answer_regions_atomic(
        self,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        *,
        confirmed: bool,
    ) -> str:
        snapshot_token = uuid4().hex
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                templates = TemplateRepository(session)
                if not templates.owns_template(session_id, template_id):
                    raise sqlite3.IntegrityError(
                        f"template_id {template_id} does not belong to "
                        f"session_id {session_id}"
                    )
                RegionRepository(session).replace_answer_regions(
                    session_id,
                    template_id,
                    regions,
                    confirmed=confirmed,
                )
                templates.set_region_snapshot_generation(
                    session_id,
                    confirmed=confirmed,
                    snapshot_token=snapshot_token,
                )
        return snapshot_token

    def mark_region_snapshot_complete(
        self,
        session_id: int,
        *,
        expected_token: str,
    ) -> bool:
        if not isinstance(expected_token, str) or not expected_token.strip():
            raise ValueError("expected_token must be nonblank")
        with self._sessions.session() as session:
            with session.transaction():
                return TemplateRepository(
                    session
                ).mark_region_snapshot_complete(
                    session_id,
                    expected_token=expected_token,
                )

    def delete_answer_region(self, region_id: int) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                RegionRepository(session).delete_answer_region(region_id)

    def update_answer_region_bbox(
        self,
        region_id: int,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                RegionRepository(session).update_answer_region_bbox(
                    region_id,
                    x,
                    y,
                    w,
                    h,
                )

    def mark_template_confirmed(
        self,
        session_id: int,
        confirmed: bool = True,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                TemplateRepository(session).mark_template_confirmed(
                    session_id,
                    confirmed,
                )

    def is_template_ready(self, session_id: int) -> bool:
        with self._sessions.session(read_only=True) as session:
            template = TemplateRepository(session).get_session_template(
                session_id
            )
            if template is None or int(template["is_confirmed"]) != 1:
                return False
            total, confirmed = RegionRepository(session).readiness_counts(
                session_id
            )
            return total > 0 and total == confirmed

    def get_session_storage_path_row(
        self,
        session_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return TemplateRepository(
                session
            ).get_session_storage_path_row(session_id)
