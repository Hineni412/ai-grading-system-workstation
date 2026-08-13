"""Application-setting persistence."""

from __future__ import annotations

from backend.repositories.base import (
    RepositorySession,
    RepositorySessionProvider,
)


class SettingsRepository:
    """Session-bound application-setting SQL."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def set_app_setting(self, key: str, value: str) -> None:
        self.session.connection.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = datetime('now','localtime')
            """,
            (str(key), str(value)),
        )

    def get_app_setting(
        self,
        key: str,
        default: str | None = None,
    ) -> str | None:
        row = self.session.connection.execute(
            """
            SELECT setting_value
            FROM app_settings
            WHERE setting_key = ?
            """,
            (str(key),),
        ).fetchone()
        return str(row["setting_value"]) if row else default


class SettingsRepositoryGateway:
    """Open one repository session per setting operation."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def set_app_setting(self, key: str, value: str) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                SettingsRepository(session).set_app_setting(key, value)

    def get_app_setting(
        self,
        key: str,
        default: str | None = None,
    ) -> str | None:
        with self._sessions.session(read_only=True) as session:
            return SettingsRepository(session).get_app_setting(key, default)
