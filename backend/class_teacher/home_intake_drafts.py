from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from .crypto import canonical_json
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .ordinary_database import OrdinaryWorkDatabase
from .secure_repository import EncryptedObjectRepository


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class HomeIntakeDrafts:
    """Durable, versioned home-intake drafts before formal adoption."""

    _SUPPORTED_KINDS = {
        "ordinary_plan",
        "affair_recommendation",
        "student_support_recommendation",
    }

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        ordinary_database: OrdinaryWorkDatabase,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.ordinary_database = ordinary_database

    def capture(self, *, token: str, operation: dict[str, object]) -> dict[str, object]:
        """Attach a saved draft to one operation without replacing a good version on failure."""

        source_operation_id = str(operation.get("operation_id") or "")
        prior = self._prior_operations(operation)
        root_operation_id = prior[0] if prior else source_operation_id
        if not source_operation_id or not root_operation_id:
            return operation

        result = operation.get("result")
        result_kind = str(operation.get("result_kind") or "")
        if (
            operation.get("state") == "succeeded"
            and result_kind in self._SUPPORTED_KINDS
            and isinstance(result, dict)
        ):
            self.prepare_for_dispatch(
                token=token,
                route="ordinary" if result_kind == "ordinary_plan" else "sensitive",
            )
            capture = (
                self._capture_ordinary_success
                if result_kind == "ordinary_plan"
                else self._capture_success
            )
            snapshot = capture(
                token=token,
                operation=operation,
                root_operation_id=root_operation_id,
                parent_operation_id=prior[-1] if prior else None,
            )
            return {**operation, **self._operation_draft_fields(snapshot)}

        preserved = self.find_by_root(token=token, root_operation_id=root_operation_id)
        if preserved is None:
            return operation
        return {
            **operation,
            **self._operation_draft_fields(preserved),
            "previous_result_preserved": True,
            "preserved_result_kind": preserved["result_kind"],
            "preserved_result": preserved["operation"]["result"],
        }

    def prepare_for_dispatch(self, *, token: str, route: str) -> None:
        """Upgrade the matching draft store before a request can incur model cost."""

        if route == "ordinary":
            self.ordinary_database.initialize_schema()
            return
        self._key_provider(token)
        self.database.initialize_schema()

    def capture_for_response(
        self,
        *,
        token: str,
        operation: dict[str, object],
    ) -> dict[str, object]:
        """Keep a parsed model result visible when draft persistence fails."""

        try:
            return self.capture(token=token, operation=operation)
        except (VaultError, sqlite3.Error, OSError) as exc:
            code = exc.code if isinstance(exc, VaultError) else "home_intake_draft_save_failed"
            return {
                **operation,
                "draft_persistence_error": code,
                "draft_persistence_message": (
                    "AI 方案已经返回，但自动保存草稿失败。请保留当前页面并重试保存，"
                    "不要重新发起模型请求。"
                ),
            }

    def list_open(self, *, token: str) -> dict[str, object]:
        items: list[dict[str, object]] = []
        if self.ordinary_database.exists:
            with closing(self.ordinary_database.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT draft_id FROM ordinary_home_intake_drafts
                    WHERE state IN ('open', 'adopting')
                    ORDER BY updated_at DESC, draft_id
                    """
                ).fetchall()
                items.extend(
                    self._ordinary_snapshot(connection, draft_id=str(row["draft_id"]))
                    for row in rows
                )
        try:
            vmk = self._key_provider(token)
        except VaultError:
            vmk = None
        if vmk is not None and self.database.exists:
            with closing(self.database.connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT draft_id FROM home_intake_drafts
                    WHERE state IN ('open', 'adopting')
                    ORDER BY updated_at DESC, draft_id
                    """
                ).fetchall()
                items.extend(
                    self._snapshot(connection, vmk=vmk, draft_id=str(row["draft_id"]))
                    for row in rows
                )
        items.sort(key=lambda item: str(item["updated_at"]), reverse=True)
        return {"items": [self._summary(item) for item in items]}

    def get(self, *, token: str, draft_id: str) -> dict[str, object]:
        ordinary = self._find_ordinary(draft_id=draft_id)
        if ordinary is not None:
            return ordinary
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            return self._snapshot(connection, vmk=vmk, draft_id=draft_id)

    def find_by_root(
        self,
        *,
        token: str,
        root_operation_id: str,
    ) -> dict[str, object] | None:
        ordinary = self._find_ordinary(root_operation_id=root_operation_id)
        if ordinary is not None:
            return ordinary
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT draft_id FROM home_intake_drafts WHERE root_operation_id = ?",
                (root_operation_id,),
            ).fetchone()
            if row is None:
                return None
            return self._snapshot(connection, vmk=vmk, draft_id=str(row["draft_id"]))

    def discard(self, *, token: str, draft_id: str, expected_version: int) -> dict[str, object]:
        if self._find_ordinary(draft_id=draft_id) is not None:
            return self._discard_ordinary(draft_id=draft_id, expected_version=expected_version)
        self._key_provider(token)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    """
                    UPDATE home_intake_drafts
                    SET state = 'discarded', updated_at = ?
                    WHERE draft_id = ? AND state = 'open' AND current_version = ?
                    """,
                    (timestamp, draft_id, expected_version),
                ).rowcount
                if changed != 1:
                    raise VaultError(
                        "home_intake_draft_revision_conflict",
                        "草案已经变化，请刷新后再操作",
                        status_code=409,
                    )
        return {"draft_id": draft_id, "version": expected_version, "state": "discarded"}

    def begin_adoption(
        self,
        *,
        token: str,
        draft_id: str,
        version: int,
        source_operation_id: str,
        result_fingerprint: str,
    ) -> dict[str, object]:
        snapshot = self.get(token=token, draft_id=draft_id)
        if (
            int(snapshot["version"]) != int(version)
            or str(snapshot["source_operation_id"]) != source_operation_id
            or str(snapshot["content_fingerprint"]) != result_fingerprint
            or str(snapshot["state"]) == "discarded"
        ):
            raise VaultError(
                "home_intake_draft_revision_conflict",
                "草案已经变化，请刷新后核对最新版本",
                status_code=409,
            )
        if snapshot["state"] in {"adopting", "adopted"}:
            return snapshot
        timestamp = _iso()
        if snapshot["result_kind"] == "ordinary_plan":
            with closing(self.ordinary_database.connect()) as connection:
                with connection:
                    changed = connection.execute(
                        """
                        UPDATE ordinary_home_intake_drafts
                        SET state = 'adopting', updated_at = ?
                        WHERE draft_id = ? AND current_version = ? AND state = 'open'
                        """,
                        (timestamp, draft_id, version),
                    ).rowcount
        else:
            self._key_provider(token)
            with closing(self.database.connect()) as connection:
                with connection:
                    changed = connection.execute(
                        """
                        UPDATE home_intake_drafts
                        SET state = 'adopting', updated_at = ?
                        WHERE draft_id = ? AND current_version = ? AND state = 'open'
                        """,
                        (timestamp, draft_id, version),
                    ).rowcount
        if changed != 1:
            raise VaultError(
                "home_intake_draft_revision_conflict",
                "草案已经被其他页面处理，请刷新后继续",
                status_code=409,
            )
        return self.get(token=token, draft_id=draft_id)

    def require_current(
        self,
        *,
        token: str,
        draft_id: str,
        version: int,
        source_operation_id: str,
        result_fingerprint: str,
    ) -> dict[str, object]:
        snapshot = self.get(token=token, draft_id=draft_id)
        if (
            snapshot["state"] not in {"open", "adopting", "adopted"}
            or int(snapshot["version"]) != int(version)
            or str(snapshot["source_operation_id"]) != source_operation_id
            or str(snapshot["content_fingerprint"]) != result_fingerprint
        ):
            raise VaultError(
                "home_intake_draft_revision_conflict",
                "草案已经变化，请刷新后核对最新版本",
                status_code=409,
            )
        return snapshot

    def mark_adopted(
        self,
        *,
        token: str,
        draft_id: str,
        version: int,
        affair_id: str | None,
    ) -> None:
        snapshot = self.get(token=token, draft_id=draft_id)
        if snapshot["result_kind"] == "ordinary_plan":
            self._mark_ordinary_adopted(draft_id=draft_id, version=version)
            return
        self._key_provider(token)
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    """
                    UPDATE home_intake_drafts
                    SET state = 'adopted', adopted_affair_id = ?, adopted_at = ?, updated_at = ?
                    WHERE draft_id = ? AND current_version = ? AND state IN ('adopting', 'adopted')
                    """,
                    (affair_id, timestamp, timestamp, draft_id, version),
                ).rowcount
                if changed != 1:
                    raise VaultError(
                        "home_intake_draft_revision_conflict",
                        "草案已经变化，正式保存结果未覆盖最新草案",
                        status_code=409,
                    )

    def _capture_success(
        self,
        *,
        token: str,
        operation: dict[str, object],
        root_operation_id: str,
        parent_operation_id: str | None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        source_operation_id = str(operation["operation_id"])
        result_kind = str(operation["result_kind"])
        fingerprint = str(operation.get("result_fingerprint") or "")
        if not fingerprint:
            fingerprint = hashlib.sha256(canonical_json(operation["result"])).hexdigest()
        timestamp = _iso()

        with closing(self.database.connect()) as connection:
            with connection:
                existing_version = connection.execute(
                    """
                    SELECT draft_id FROM home_intake_draft_versions
                    WHERE source_operation_id = ?
                    """,
                    (source_operation_id,),
                ).fetchone()
                if existing_version is not None:
                    return self._snapshot(
                        connection,
                        vmk=vmk,
                        draft_id=str(existing_version["draft_id"]),
                    )

                header = connection.execute(
                    "SELECT * FROM home_intake_drafts WHERE root_operation_id = ?",
                    (root_operation_id,),
                ).fetchone()
                if header is None:
                    draft_id = uuid4().hex
                    version = 1
                    connection.execute(
                        """
                        INSERT INTO home_intake_drafts (
                            draft_id, root_operation_id, route, result_kind,
                            current_version, state, adopted_affair_id,
                            created_at, updated_at, adopted_at
                        ) VALUES (?, ?, ?, ?, 1, 'open', NULL, ?, ?, NULL)
                        """,
                        (
                            draft_id,
                            root_operation_id,
                            str(operation.get("route") or "ordinary"),
                            result_kind,
                            timestamp,
                            timestamp,
                        ),
                    )
                else:
                    draft_id = str(header["draft_id"])
                    if str(header["state"]) != "open":
                        raise VaultError(
                            "home_intake_draft_closed",
                            "该草案已经正式保存或放弃，不能继续覆盖",
                            status_code=409,
                        )
                    current_version = int(header["current_version"])
                    current_source = connection.execute(
                        """
                        SELECT source_operation_id FROM home_intake_draft_versions
                        WHERE draft_id = ? AND version = ?
                        """,
                        (draft_id, current_version),
                    ).fetchone()
                    if (
                        parent_operation_id is not None
                        and current_source is not None
                        and str(current_source["source_operation_id"]) != parent_operation_id
                    ):
                        raise VaultError(
                            "home_intake_draft_revision_conflict",
                            "草案已由另一轮调整更新，请刷新后继续",
                            status_code=409,
                        )
                    version = current_version + 1

                object_id = f"home-intake-draft-{draft_id}-v{version}"
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="home_intake_draft_version",
                    payload={"operation": operation},
                )
                connection.execute(
                    """
                    INSERT INTO home_intake_draft_versions (
                        draft_id, version, payload_object_id, source_operation_id,
                        parent_version, content_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft_id,
                        version,
                        object_id,
                        source_operation_id,
                        None if version == 1 else version - 1,
                        fingerprint,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    UPDATE home_intake_drafts
                    SET current_version = ?, result_kind = ?, updated_at = ?
                    WHERE draft_id = ?
                    """,
                    (version, result_kind, timestamp, draft_id),
                )
                return self._snapshot(connection, vmk=vmk, draft_id=draft_id)

    def _capture_ordinary_success(
        self,
        *,
        token: str,
        operation: dict[str, object],
        root_operation_id: str,
        parent_operation_id: str | None,
    ) -> dict[str, object]:
        _ = token
        self.ordinary_database.initialize_schema()
        source_operation_id = str(operation["operation_id"])
        fingerprint = str(operation.get("result_fingerprint") or "")
        if not fingerprint:
            fingerprint = hashlib.sha256(canonical_json(operation["result"])).hexdigest()
        timestamp = _iso()
        serialized = json.dumps(
            operation,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with closing(self.ordinary_database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT draft_id FROM ordinary_home_intake_draft_versions
                    WHERE source_operation_id = ?
                    """,
                    (source_operation_id,),
                ).fetchone()
                if existing is not None:
                    snapshot = self._ordinary_snapshot(
                        connection, draft_id=str(existing["draft_id"])
                    )
                    connection.commit()
                    return snapshot
                header = connection.execute(
                    """
                    SELECT * FROM ordinary_home_intake_drafts
                    WHERE root_operation_id = ?
                    """,
                    (root_operation_id,),
                ).fetchone()
                if header is None:
                    draft_id = uuid4().hex
                    version = 1
                    connection.execute(
                        """
                        INSERT INTO ordinary_home_intake_drafts (
                            draft_id, root_operation_id, result_kind, current_version,
                            state, created_at, updated_at, adopted_at
                        ) VALUES (?, ?, 'ordinary_plan', 1, 'open', ?, ?, NULL)
                        """,
                        (draft_id, root_operation_id, timestamp, timestamp),
                    )
                else:
                    draft_id = str(header["draft_id"])
                    if str(header["state"]) != "open":
                        raise VaultError(
                            "home_intake_draft_closed",
                            "该草案正在保存、已经采用或已经放弃，不能继续覆盖",
                            status_code=409,
                        )
                    current_version = int(header["current_version"])
                    current = connection.execute(
                        """
                        SELECT source_operation_id
                        FROM ordinary_home_intake_draft_versions
                        WHERE draft_id = ? AND version = ?
                        """,
                        (draft_id, current_version),
                    ).fetchone()
                    if (
                        parent_operation_id is not None
                        and current is not None
                        and str(current["source_operation_id"]) != parent_operation_id
                    ):
                        raise VaultError(
                            "home_intake_draft_revision_conflict",
                            "草案已由另一轮调整更新，请刷新后继续",
                            status_code=409,
                        )
                    version = current_version + 1
                connection.execute(
                    """
                    INSERT INTO ordinary_home_intake_draft_versions (
                        draft_id, version, source_operation_id, parent_version,
                        content_fingerprint, operation_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft_id,
                        version,
                        source_operation_id,
                        None if version == 1 else version - 1,
                        fingerprint,
                        serialized,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    UPDATE ordinary_home_intake_drafts
                    SET current_version = ?, updated_at = ?
                    WHERE draft_id = ?
                    """,
                    (version, timestamp, draft_id),
                )
                snapshot = self._ordinary_snapshot(connection, draft_id=draft_id)
                connection.commit()
                return snapshot
            except Exception:
                connection.rollback()
                raise

    def _find_ordinary(
        self,
        *,
        draft_id: str | None = None,
        root_operation_id: str | None = None,
    ) -> dict[str, object] | None:
        if not self.ordinary_database.exists:
            return None
        field = "draft_id" if draft_id is not None else "root_operation_id"
        value = draft_id if draft_id is not None else root_operation_id
        with closing(self.ordinary_database.connect()) as connection:
            row = connection.execute(
                f"SELECT draft_id FROM ordinary_home_intake_drafts WHERE {field} = ?",
                (value,),
            ).fetchone()
            if row is None:
                return None
            return self._ordinary_snapshot(connection, draft_id=str(row["draft_id"]))

    def _discard_ordinary(
        self, *, draft_id: str, expected_version: int
    ) -> dict[str, object]:
        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    """
                    UPDATE ordinary_home_intake_drafts
                    SET state = 'discarded', updated_at = ?
                    WHERE draft_id = ? AND current_version = ? AND state = 'open'
                    """,
                    (_iso(), draft_id, expected_version),
                ).rowcount
                if changed != 1:
                    raise VaultError(
                        "home_intake_draft_revision_conflict",
                        "草案已经变化，请刷新后再操作",
                        status_code=409,
                    )
        return {"draft_id": draft_id, "version": expected_version, "state": "discarded"}

    def _mark_ordinary_adopted(self, *, draft_id: str, version: int) -> None:
        timestamp = _iso()
        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                changed = connection.execute(
                    """
                    UPDATE ordinary_home_intake_drafts
                    SET state = 'adopted', adopted_at = ?, updated_at = ?
                    WHERE draft_id = ? AND current_version = ?
                      AND state IN ('adopting', 'adopted')
                    """,
                    (timestamp, timestamp, draft_id, version),
                ).rowcount
                if changed != 1:
                    raise VaultError(
                        "home_intake_draft_revision_conflict",
                        "草案已经变化，正式保存结果未覆盖最新草案",
                        status_code=409,
                    )

    @staticmethod
    def _ordinary_snapshot(connection, *, draft_id: str) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT d.*, v.source_operation_id, v.content_fingerprint,
                   v.operation_json
            FROM ordinary_home_intake_drafts d
            JOIN ordinary_home_intake_draft_versions v
              ON v.draft_id = d.draft_id AND v.version = d.current_version
            WHERE d.draft_id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "home_intake_draft_not_found", "事务草案不存在", status_code=404
            )
        try:
            operation = json.loads(str(row["operation_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise VaultError(
                "home_intake_draft_integrity_error",
                "事务草案未通过完整性检查",
                status_code=409,
            ) from exc
        if not isinstance(operation, dict):
            raise VaultError(
                "home_intake_draft_integrity_error",
                "事务草案未通过完整性检查",
                status_code=409,
            )
        operation = {
            **operation,
            "draft_id": str(row["draft_id"]),
            "draft_version": int(row["current_version"]),
            "draft_saved_at": str(row["updated_at"]),
        }
        return {
            "draft_id": str(row["draft_id"]),
            "version": int(row["current_version"]),
            "state": str(row["state"]),
            "route": "ordinary",
            "result_kind": "ordinary_plan",
            "source_operation_id": str(row["source_operation_id"]),
            "content_fingerprint": str(row["content_fingerprint"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "adopted_affair_id": None,
            "operation": operation,
        }

    def _snapshot(
        self,
        connection,
        *,
        vmk: bytes,
        draft_id: str,
    ) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT d.*, v.payload_object_id, v.source_operation_id,
                   v.content_fingerprint, v.created_at AS version_created_at
            FROM home_intake_drafts d
            JOIN home_intake_draft_versions v
              ON v.draft_id = d.draft_id AND v.version = d.current_version
            WHERE d.draft_id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "home_intake_draft_not_found",
                "事务草案不存在",
                status_code=404,
            )
        payload, _revision = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        operation = payload.get("operation")
        if not isinstance(operation, dict):
            raise VaultError(
                "home_intake_draft_integrity_error",
                "事务草案未通过完整性检查",
                status_code=409,
            )
        operation = {
            **operation,
            "draft_id": str(row["draft_id"]),
            "draft_version": int(row["current_version"]),
            "draft_saved_at": str(row["updated_at"]),
        }
        return {
            "draft_id": str(row["draft_id"]),
            "version": int(row["current_version"]),
            "state": str(row["state"]),
            "route": str(row["route"]),
            "result_kind": str(row["result_kind"]),
            "source_operation_id": str(row["source_operation_id"]),
            "content_fingerprint": str(row["content_fingerprint"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "adopted_affair_id": row["adopted_affair_id"],
            "operation": operation,
        }

    @staticmethod
    def _prior_operations(operation: dict[str, object]) -> list[str]:
        context = operation.get("local_context")
        if not isinstance(context, dict):
            return []
        return [str(item) for item in list(context.get("prior_operations") or []) if str(item)]

    @staticmethod
    def _operation_draft_fields(snapshot: dict[str, object]) -> dict[str, object]:
        return {
            "draft_id": snapshot["draft_id"],
            "draft_version": snapshot["version"],
            "draft_saved_at": snapshot["updated_at"],
            "previous_result_preserved": False,
            "preserved_result_kind": None,
            "preserved_result": None,
        }

    @staticmethod
    def _summary(snapshot: dict[str, object]) -> dict[str, object]:
        operation = snapshot["operation"]
        result = operation.get("result") if isinstance(operation, dict) else None
        result = result if isinstance(result, dict) else {}
        local_context = operation.get("local_context") if isinstance(operation, dict) else None
        local_context = local_context if isinstance(local_context, dict) else {}
        title = str(result.get("title") or "")
        if not title and isinstance(result.get("nodes"), list) and result["nodes"]:
            first = result["nodes"][0]
            if isinstance(first, dict):
                title = str(first.get("title") or "")
        return {
            "draft_id": snapshot["draft_id"],
            "version": snapshot["version"],
            "route": snapshot["route"],
            "result_kind": snapshot["result_kind"],
            "title": title or "未命名事务草案",
            "source_text": str(local_context.get("source_text") or ""),
            "student_aliases": [
                str(item) for item in list(result.get("student_aliases") or []) if str(item)
            ],
            "updated_at": snapshot["updated_at"],
        }


__all__ = ["HomeIntakeDrafts"]
