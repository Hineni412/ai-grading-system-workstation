"""命题练习作品存储与母题快照。

拆解/改编作品保存在 ``authoring_works`` / ``authoring_work_versions``，
母题内容在创建时冻结为 ``source_snapshot_json``，题库之后的修改不影响
已建立的练习。作品不写题库正文表，也不进入题库列表、组卷候选、
训练推荐或掌握度。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from question_bank.authoring.task_cards import is_task_card_id
from question_bank.database.schema import connect
from question_bank.services.error_pattern_service import list_patterns
from question_bank.services.question_read_service import QuestionBankReadService

WORK_KINDS = ("decompose", "adapt")
SOLO_LEVELS = (
    "prestructural",
    "unistructural",
    "multistructural",
    "relational",
    "extended_abstract",
)
ADAPT_QUESTION_TYPES = ("选择题", "多选题", "填空题", "解答题")

_TOKEN = re.compile(r"^[0-9a-fA-F]{32}$")
_WORK_TABLE = "authoring_works"
_VERSION_TABLE = "authoring_work_versions"
_SNIPPET_LENGTH = 80
_MAX_TEXT = 20_000
_MAX_LIST = 50
_MAX_PARTS = 30

_DECOMPOSE_FIELDS = (
    "intent",
    "knowledge_points",
    "key_steps",
    "expected_errors",
    "predicted_difficulty",
    "predicted_solo",
    "parts",
)
_ADAPT_FIELDS = (
    "question_text",
    "answer_text",
    "question_type",
    "intent",
    "target_knowledge",
    "parts",
    "predicted_difficulty",
    "expected_errors",
    "case_list",
    "notes",
    "figure_asset_ids",
)


class AuthoringError(Exception):
    """命题练习的已分类失败；路由层映射为稳定错误码。"""


class AuthoringStorageUnavailable(AuthoringError):
    """题库尚未升级出命题练习表。"""


class AuthoringNotFound(AuthoringError):
    pass


class AuthoringSourceNotFound(AuthoringError):
    pass


class AuthoringVersionConflict(AuthoringError):
    def __init__(self, current_version: int) -> None:
        super().__init__("authoring work head changed")
        self.current_version = int(current_version)


class AuthoringTokenConflict(AuthoringError):
    pass


class AuthoringValidationError(AuthoringError):
    pass


class AuthoringService:
    def __init__(self, db_path: Path, *, data_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else None

    # ------------------------------------------------------------------ reads

    def list_works(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in WORK_KINDS:
            raise AuthoringValidationError("kind is invalid")
        sql = (
            "SELECT * FROM authoring_works WHERE is_deleted = 0"
        )
        params: list[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC, work_id"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._summary(dict(row)) for row in rows]

    def get_work(self, work_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM authoring_works WHERE work_id = ? AND is_deleted = 0",
                (_clean_work_id(work_id),),
            ).fetchone()
            if row is None:
                raise AuthoringNotFound(work_id)
            return self._detail_in(conn, dict(row))

    def get_version(self, work_id: str, version_no: int) -> dict[str, Any]:
        with self._connection() as conn:
            work = self._live_work(conn, work_id)
            row = conn.execute(
                "SELECT * FROM authoring_work_versions WHERE work_id = ? AND version_no = ?",
                (_clean_work_id(work_id), int(version_no)),
            ).fetchone()
        if row is None:
            raise AuthoringNotFound(f"version {version_no}")
        return {
            "work_id": str(row["work_id"]),
            "version_no": int(row["version_no"]),
            "content": json.loads(str(row["content_json"])),
            "created_at": str(row["created_at"]),
            "current_version": int(work["current_version"]),
        }

    # ----------------------------------------------------------------- writes

    def create_work(
        self,
        *,
        kind: str,
        source_question_id: int,
        task_card: Mapping[str, Any] | None = None,
        title: str = "",
        operation_token: str,
    ) -> dict[str, Any]:
        kind = str(kind or "").strip()
        if kind not in WORK_KINDS:
            raise AuthoringValidationError("kind is invalid")
        try:
            question_id = int(source_question_id)
        except (TypeError, ValueError):
            raise AuthoringValidationError("source_question_id is invalid")
        if isinstance(source_question_id, bool) or question_id <= 0:
            raise AuthoringValidationError("source_question_id is invalid")
        token = _clean_token(operation_token)
        card = (
            _normalize_task_card(task_card)
            if kind == "adapt"
            else {}
        )
        clean_title = str(title or "").strip()[:200]

        # 重放先查令牌，避免对已下线母题重复构建快照。
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT work_id FROM authoring_works WHERE create_token = ?",
                (token,),
            ).fetchone()
        if existing is not None:
            return self._replayed_work(str(existing["work_id"]), kind, question_id, token)

        snapshot = self._build_source_snapshot(question_id)
        work_id = uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT work_id FROM authoring_works WHERE create_token = ?",
                (token,),
            ).fetchone()
            if existing is not None:
                return self._replayed_work(
                    str(existing["work_id"]), kind, question_id, token,
                )
            conn.execute(
                """
                INSERT INTO authoring_works (
                    work_id, kind, source_question_id, source_snapshot_json,
                    task_card_json, title, current_version, is_deleted,
                    create_token
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    work_id,
                    kind,
                    question_id,
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(card, ensure_ascii=False),
                    clean_title,
                    token,
                ),
            )
            row = conn.execute(
                "SELECT * FROM authoring_works WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            return self._detail_in(conn, dict(row))

    def save_version(
        self,
        work_id: str,
        *,
        content: Mapping[str, Any],
        base_version: int,
        operation_token: str,
    ) -> dict[str, Any]:
        token = _clean_token(operation_token)
        if isinstance(base_version, bool) or int(base_version) < 0:
            raise AuthoringValidationError("base_version is invalid")
        work_key = _clean_work_id(work_id)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            work = self._live_work(conn, work_key)

            replayed = conn.execute(
                "SELECT * FROM authoring_work_versions WHERE operation_token = ?",
                (token,),
            ).fetchone()
            normalized = normalize_content(str(work["kind"]), content)
            encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if replayed is not None:
                if (
                    str(replayed["work_id"]) == work_key
                    and str(replayed["content_json"]) == encoded
                ):
                    return {
                        "work_id": work_key,
                        "version_no": int(replayed["version_no"]),
                        "content": normalized,
                        "created_at": str(replayed["created_at"]),
                        "current_version": int(work["current_version"]),
                    }
                raise AuthoringTokenConflict(token)

            current = int(work["current_version"])
            if int(base_version) != current:
                raise AuthoringVersionConflict(current)
            next_version = current + 1
            conn.execute(
                """
                INSERT INTO authoring_work_versions (
                    work_id, version_no, content_json, operation_token
                ) VALUES (?, ?, ?, ?)
                """,
                (work_key, next_version, encoded, token),
            )
            conn.execute(
                "UPDATE authoring_works SET current_version = ?,"
                " updated_at = datetime('now', 'localtime') WHERE work_id = ?",
                (next_version, work_key),
            )
        return {
            "work_id": work_key,
            "version_no": next_version,
            "content": normalized,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "current_version": next_version,
        }

    def delete_work(self, work_id: str) -> dict[str, Any]:
        work_key = _clean_work_id(work_id)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT work_id, is_deleted FROM authoring_works WHERE work_id = ?",
                (work_key,),
            ).fetchone()
            if row is None:
                raise AuthoringNotFound(work_id)
            if not int(row["is_deleted"]):
                conn.execute(
                    "UPDATE authoring_works SET is_deleted = 1,"
                    " updated_at = datetime('now', 'localtime') WHERE work_id = ?",
                    (work_key,),
                )
        return {"work_id": work_key, "deleted": True}

    # --------------------------------------------------------------- snapshot

    def _build_source_snapshot(self, question_id: int) -> dict[str, Any]:
        reader = QuestionBankReadService(self.db_path, data_root=self.data_root)
        item = reader.get_question(question_id)
        if item is None:
            raise AuthoringSourceNotFound(str(question_id))
        with self._connection() as conn:
            patterns = list_patterns(conn, [question_id]).get(question_id, [])
            judgment = _current_judgment_points(conn, question_id)
            part_assessments = _part_assessments(
                self.db_path,
                question_id,
                connection=conn,
                data_root=self.data_root,
            )
        return {
            "question_id": question_id,
            "question": item,
            "judgment_points": judgment,
            "part_assessments": part_assessments,
            "error_patterns": patterns,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _live_work(conn: sqlite3.Connection, work_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM authoring_works WHERE work_id = ? AND is_deleted = 0",
            (_clean_work_id(work_id),),
        ).fetchone()
        if row is None:
            raise AuthoringNotFound(work_id)
        return row

    def _replayed_work(
        self,
        work_id: str,
        kind: str,
        question_id: int,
        token: str,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM authoring_works WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is None or int(row["is_deleted"]):
                raise AuthoringNotFound(work_id)
            if (
                str(row["kind"]) != kind
                or int(row["source_question_id"] or 0) != question_id
            ):
                raise AuthoringTokenConflict(token)
            return self._detail_in(conn, dict(row))

    # ----------------------------------------------------------------- helpers

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            _require_storage(conn)
            yield conn

    @staticmethod
    def _summary(row: dict[str, Any]) -> dict[str, Any]:
        snapshot = _snapshot_payload(row.get("source_snapshot_json"))
        question = snapshot.get("question") if isinstance(snapshot, Mapping) else {}
        if not isinstance(question, Mapping):
            question = {}
        text = str(question.get("question_text") or "").strip()
        return {
            "work_id": str(row["work_id"]),
            "kind": str(row["kind"]),
            "title": str(row["title"] or ""),
            "source_question_id": row["source_question_id"],
            "source_question_number": str(question.get("question_number") or ""),
            "source_question_snippet": text[:_SNIPPET_LENGTH],
            "current_version": int(row["current_version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _detail_in(
        self,
        conn: sqlite3.Connection,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        work_id = str(row["work_id"])
        versions = conn.execute(
            "SELECT version_no, created_at FROM authoring_work_versions"
            " WHERE work_id = ? ORDER BY version_no",
            (work_id,),
        ).fetchall()
        latest = None
        current = int(row["current_version"])
        if current > 0:
            version_row = conn.execute(
                "SELECT content_json FROM authoring_work_versions"
                " WHERE work_id = ? AND version_no = ?",
                (work_id, current),
            ).fetchone()
            if version_row is not None:
                latest = json.loads(str(version_row["content_json"]))
        detail = self._summary(row)
        detail["source_snapshot"] = _snapshot_payload(row["source_snapshot_json"])
        detail["task_card"] = _json_object(row.get("task_card_json"))
        detail["versions"] = [
            {
                "version_no": int(version["version_no"]),
                "created_at": str(version["created_at"]),
            }
            for version in versions
        ]
        detail["latest_content"] = latest
        return detail


def _require_storage(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_WORK_TABLE,),
    ).fetchone()
    if exists is None:
        raise AuthoringStorageUnavailable(_WORK_TABLE)


def _snapshot_payload(raw: Any) -> dict[str, Any]:
    payload = _json_object(raw)
    return dict(payload)


def _json_object(raw: Any) -> Mapping[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _clean_work_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", text):
        raise AuthoringNotFound(str(value))
    return text


def _clean_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _TOKEN.fullmatch(text):
        raise AuthoringValidationError("operation_token is invalid")
    return text


def _current_judgment_points(
    conn: sqlite3.Connection,
    question_id: int,
) -> dict[str, Any] | None:
    heads = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table'"
        " AND name='training_criterion_heads'",
    ).fetchone()
    if heads is None:
        return None
    row = conn.execute(
        """
        SELECT v.version_id, v.version_number, v.status, v.criteria_json,
               v.created_at
        FROM training_criterion_heads head
        JOIN training_criterion_versions v
          ON v.version_id = head.current_version_id
        WHERE head.question_id = ?
        """,
        (int(question_id),),
    ).fetchone()
    if row is None:
        return None
    criteria = _json_object(row["criteria_json"])
    return {
        "version_id": str(row["version_id"]),
        "version_number": int(row["version_number"]),
        "status": str(row["status"]),
        "points": list(criteria.get("points") or []),
        "auxiliary_rules": list(criteria.get("auxiliary_rules") or []),
        "rationale": str(criteria.get("rationale") or ""),
    }


def _part_assessments(
    db_path: Path,
    question_id: int,
    *,
    connection: sqlite3.Connection,
    data_root: Path | None,
) -> list[dict[str, Any]]:
    from question_bank.solution_evidence.part_assessments import load_profiles

    try:
        profile = load_profiles(
            db_path,
            [int(question_id)],
            connection=connection,
            data_root=data_root,
        ).get(int(question_id))
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        return []
    if not profile or not profile.get("available"):
        return []
    labels = {
        str(part.get("part_id") or ""): str(part.get("label") or "")
        for part in (profile.get("evidence") or {}).get("parts") or []
        if isinstance(part, Mapping)
    }
    parts: list[dict[str, Any]] = []
    for part in profile.get("parts") or []:
        if not isinstance(part, Mapping):
            continue
        part_id = str(part.get("part_id") or "")
        parts.append(
            {
                "part_id": part_id,
                "label": labels.get(part_id, ""),
                "difficulty": part.get("difficulty"),
                "source": str(part.get("source") or ""),
                "rationale": str(part.get("rationale") or ""),
            }
        )
    return parts


def _normalize_task_card(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AuthoringValidationError("task_card is required for adapt works")
    method_id = str(raw.get("method_id") or raw.get("id") or "").strip()
    if not is_task_card_id(method_id):
        raise AuthoringValidationError("task_card method is unknown")
    targets = raw.get("targets")
    if targets is None:
        targets = {}
    if not isinstance(targets, Mapping):
        raise AuthoringValidationError("task_card targets are invalid")
    return {
        "method_id": method_id,
        "targets": {
            "keep_knowledge": bool(targets.get("keep_knowledge", True)),
            "target_difficulty": _difficulty(targets.get("target_difficulty")),
            "target_solo": _solo(targets.get("target_solo")),
            "note": _text(targets.get("note")),
        },
    }


def normalize_content(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AuthoringValidationError("content must be an object")
    if kind == "decompose":
        return {
            "intent": _text(raw.get("intent")),
            "knowledge_points": _text_list(raw.get("knowledge_points")),
            "key_steps": _text_list(raw.get("key_steps")),
            "expected_errors": _text_list(raw.get("expected_errors")),
            "predicted_difficulty": _difficulty(raw.get("predicted_difficulty")),
            "predicted_solo": _solo(raw.get("predicted_solo")),
            "parts": _parts(raw.get("parts")),
        }
    if kind == "adapt":
        question_text = _text(raw.get("question_text"))
        if not question_text:
            raise AuthoringValidationError("question_text is required")
        question_type = _text(raw.get("question_type"))
        if question_type not in ADAPT_QUESTION_TYPES:
            raise AuthoringValidationError("question_type is invalid")
        return {
            "question_text": question_text,
            "answer_text": _text(raw.get("answer_text")),
            "question_type": question_type,
            "intent": _text(raw.get("intent")),
            "target_knowledge": _text_list(raw.get("target_knowledge")),
            "parts": _parts(raw.get("parts")),
            "predicted_difficulty": _difficulty(raw.get("predicted_difficulty")),
            "expected_errors": _text_list(raw.get("expected_errors")),
            "case_list": _text_list(raw.get("case_list")),
            "notes": _text(raw.get("notes")),
            "figure_asset_ids": _id_list(raw.get("figure_asset_ids")),
        }
    raise AuthoringValidationError("kind is invalid")


def _text(value: Any, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if len(text) > _MAX_TEXT:
        raise AuthoringValidationError("text is too long")
    if required and not text:
        raise AuthoringValidationError("text is required")
    return text


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_LIST:
        raise AuthoringValidationError("list field is invalid")
    return [text for item in value if (text := _text(item))]


def _id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_LIST:
        raise AuthoringValidationError("asset list is invalid")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", text):
            raise AuthoringValidationError("asset id is invalid")
        result.append(text)
    return result


def _difficulty(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise AuthoringValidationError("difficulty must be an integer 1-10")
    return int(value)


def _solo(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text not in SOLO_LEVELS:
        raise AuthoringValidationError("solo level is invalid")
    return text


def _parts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_PARTS:
        raise AuthoringValidationError("parts are invalid")
    parts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise AuthoringValidationError("part is invalid")
        parts.append(
            {
                "part_label": _text(item.get("part_label")),
                "predicted_difficulty": _difficulty(
                    item.get("predicted_difficulty")
                ),
                "predicted_solo": _solo(item.get("predicted_solo")),
            }
        )
    return parts


__all__ = [
    "ADAPT_QUESTION_TYPES",
    "AuthoringError",
    "AuthoringNotFound",
    "AuthoringService",
    "AuthoringSourceNotFound",
    "AuthoringStorageUnavailable",
    "AuthoringTokenConflict",
    "AuthoringValidationError",
    "AuthoringVersionConflict",
    "SOLO_LEVELS",
    "WORK_KINDS",
    "normalize_content",
]
