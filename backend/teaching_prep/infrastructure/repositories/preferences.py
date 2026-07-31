from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from backend.teaching_prep.application.preferences import (
    normalize_teaching_preferences,
)
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.domain.models import TeachingPreferences
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class TeachingPreferencesRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def get(self) -> TeachingPreferences:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT revision, payload_json, updated_at
                FROM teaching_preferences
                WHERE profile_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("default teaching preferences are unavailable")
        return _preferences(row)

    def update(
        self,
        *,
        expected_revision: int,
        payload: Mapping[str, object],
    ) -> TeachingPreferences:
        clean_payload = normalize_teaching_preferences(payload)
        payload_json = json.dumps(
            clean_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT revision
                FROM teaching_preferences
                WHERE profile_key = 'default'
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("default teaching preferences are unavailable")
            if int(row["revision"]) != expected_revision:
                raise TeachingPrepConflictError(
                    "teaching preferences changed; reload before saving"
                )
            connection.execute(
                """
                UPDATE teaching_preferences
                SET revision = revision + 1,
                    payload_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE profile_key = 'default'
                """,
                (payload_json,),
            )
            updated = connection.execute(
                """
                SELECT revision, payload_json, updated_at
                FROM teaching_preferences
                WHERE profile_key = 'default'
                """
            ).fetchone()
        if updated is None:
            raise RuntimeError("updated teaching preferences are unavailable")
        return _preferences(updated)


def _preferences(row: sqlite3.Row) -> TeachingPreferences:
    payload = normalize_teaching_preferences(
        json.loads(str(row["payload_json"]))
    )
    return TeachingPreferences(
        revision=int(row["revision"]),
        payload=payload,
        updated_at=str(row["updated_at"]),
    )


__all__ = ["TeachingPreferencesRepository"]
