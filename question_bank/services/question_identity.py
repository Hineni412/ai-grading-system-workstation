"""同题判定的唯一入口：导入合并、考试配置查重与评分依据复用、组卷卷内去重和近期原题排除都调用这里。"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from question_bank.models.question import EXACT_QUESTION_KEY_PREFIX
from question_bank.services.duplicate_analysis_copy_service import (
    canonical_question_ranks,
    content_index_lookup,
    exam_original_key,
    exam_original_text_key,
    exact_identity_map,
    exact_question_key,
    reusable_analysis,
)

TEXT_IDENTITY_PREFIX = "text-v1:"
_IDENTITY_IMAGE_MARKER = re.compile(r"\[\[IMAGE:[^\]]*\]\]", re.I)

_ACTIVE_INDEX_SQL = """SELECT idx.content_key AS content_key, idx.question_id AS question_id
    FROM question_content_index idx JOIN questions q ON q.id = idx.question_id
    LEFT JOIN papers p ON p.id = q.paper_id
    WHERE COALESCE(q.is_deleted, 0) = 0 AND COALESCE(p.import_status, '') <> 'deleted'"""


def text_identity_from_exact_key(key: str) -> str:
    """Same stem text, options and formulas; picture encodings are ignored.

    Short illustrated stems carry their content in the figure and keep no
    text identity.
    """
    if not str(key or "").startswith(EXACT_QUESTION_KEY_PREFIX):
        return ""
    payload = json.loads(key[len(EXACT_QUESTION_KEY_PREFIX):])
    raw = str(payload.get("text") or "")
    text = _IDENTITY_IMAGE_MARKER.sub("", raw)
    if (payload.get("images") or text != raw) and len(re.sub(r"\W", "", text)) < 20:
        return ""
    if not text:
        return ""
    return TEXT_IDENTITY_PREFIX + json.dumps(
        {"text": text, "options": payload.get("options") or [], "formulas": payload.get("formulas") or []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_index_present(conn: Any) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_content_index'"
    ).fetchone() is not None


def _text_identity_map(conn: Any, rows, *, wanted: set[str] | None = None) -> dict[str, int]:
    """Best canonical question id per text identity, ranked like exact keys."""
    grouped: dict[str, list[int]] = {}
    for row in rows:
        identity = text_identity_from_exact_key(str(row["content_key"] or ""))
        if identity and (wanted is None or identity in wanted):
            grouped.setdefault(identity, []).append(int(row["question_id"]))
    if not grouped:
        return {}
    ranks = canonical_question_ranks(conn, [qid for ids in grouped.values() for qid in ids])
    return {identity: min(ids, key=lambda qid: ranks[qid]) for identity, ids in grouped.items()}


@dataclass(frozen=True)
class SameQuestionMatch:
    question_id: int
    kind: str  # "exact" | "text"


@dataclass
class SameQuestionIndex:
    exact: dict[str, int] = field(default_factory=dict)
    text: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, conn, *, keys=None, data_root=None) -> "SameQuestionIndex":
        """keys=None loads the whole bank; otherwise only identities of these exact keys."""
        index = cls()
        if not _content_index_present(conn):
            return index
        if keys is None:
            wanted_text = None
        else:
            wanted = {str(key) for key in keys if str(key or "").strip()}
            index.exact.update(content_index_lookup(conn, wanted, data_root=data_root))
            wanted_text = {
                identity
                for key in wanted
                if (identity := text_identity_from_exact_key(key))
            }
            if not wanted_text:
                return index
        rows = conn.execute(_ACTIVE_INDEX_SQL).fetchall()
        if keys is None:
            index.exact.update(content_index_lookup(
                conn, {str(row["content_key"]) for row in rows}, data_root=data_root))
        index.text.update(_text_identity_map(conn, rows, wanted=wanted_text))
        return index

    def match(self, key: str) -> SameQuestionMatch | None:
        """Exact key first; a different picture encoding still text-matches."""
        key = str(key or "")
        if not key:
            return None
        question_id = self.exact.get(key)
        if question_id is not None:
            return SameQuestionMatch(question_id=int(question_id), kind="exact")
        identity = text_identity_from_exact_key(key)
        if not identity:
            return None
        question_id = self.text.get(identity)
        if question_id is None:
            return None
        return SameQuestionMatch(question_id=int(question_id), kind="text")

    def register(self, key: str, question_id: int) -> None:
        key = str(key or "")
        if not key:
            return
        self.exact.setdefault(key, int(question_id))
        identity = text_identity_from_exact_key(key)
        if identity:
            self.text.setdefault(identity, int(question_id))

    def refresh(self, conn, keys, *, data_root=None) -> None:
        """Drop these keys' exact and text entries, then reload them from the DB."""
        wanted = {str(key) for key in keys if str(key or "").strip()}
        for key in wanted:
            self.exact.pop(key, None)
        wanted_text = {
            identity
            for key in wanted
            if (identity := text_identity_from_exact_key(key))
        }
        for identity in wanted_text:
            self.text.pop(identity, None)
        if not wanted or not _content_index_present(conn):
            return
        self.exact.update(content_index_lookup(conn, wanted, data_root=data_root))
        if not wanted_text:
            return
        rows = conn.execute(_ACTIVE_INDEX_SQL).fetchall()
        self.text.update(_text_identity_map(conn, rows, wanted=wanted_text))


def reusable_same_question_analysis(db_path, *, match: SameQuestionMatch,
                                    target_question: Any, data_root) -> dict[str, Any] | None:
    # This module already decided the questions are identical; reusable_analysis
    # still checks usable status, the canonical content hash and answer conflict.
    return reusable_analysis(
        db_path,
        bank_question_id=match.question_id,
        target_question=target_question,
        data_root=data_root,
        teacher_confirmed_same=(match.kind == "text"),
    )


def stored_exact_keys(conn, question_ids, *, data_root) -> dict[int, str]:
    """Exact keys read from the content index while each question's files are
    unchanged; changed or unindexed questions are computed. Never writes."""
    return exact_identity_map(conn, data_root=Path(data_root), question_ids=list(question_ids), persist=False)


def question_identities(row: Mapping[str, Any], *, data_root, image_cache,
                        exact_key: str | None = None) -> dict[str, str]:
    """Duplicate, text and printed-original identities for one bank row."""
    exact = exact_key if exact_key is not None else exact_question_key(
        dict(row), data_root=data_root, image_cache=image_cache)
    return {
        "duplicate_identity": exact or str(row["id"]),
        "text_identity": text_identity_from_exact_key(exact),
        "practice_identity": exam_original_key(dict(row), data_root=data_root, image_cache=image_cache),
    }


def same_question(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Identity fields on practice candidates; empty fields never match."""
    practice = a.get("practice_identity")
    if practice and practice == b.get("practice_identity"):
        return True
    if a.get("duplicate_identity", a["question_id"]) == b.get("duplicate_identity", b["question_id"]):
        return True
    text_identity = a.get("text_identity")
    return bool(text_identity) and text_identity == b.get("text_identity")


def same_question_ids(conn, question_ids, *, data_root) -> dict[int, frozenset[int]]:
    """All active bank ids that are the same question as each given id."""
    wanted = {int(qid) for qid in question_ids}
    if not wanted:
        return {}
    buckets: dict[tuple[str, str], set[int]] = {}
    questions = conn.execute("SELECT * FROM questions WHERE is_deleted=0").fetchall()
    texts = {
        exam_original_text_key(dict(row))
        for row in questions
        if int(row["id"]) in wanted
    }
    image_cache: dict[str, str] = {}
    for row in questions:
        if exam_original_text_key(dict(row)) not in texts:
            continue
        key = exam_original_key(dict(row), data_root=Path(data_root), image_cache=image_cache)
        if key:
            buckets.setdefault(("original", key), set()).add(int(row["id"]))
    if _content_index_present(conn):
        for row in conn.execute(_ACTIVE_INDEX_SQL):
            qid = int(row["question_id"])
            key = str(row["content_key"] or "")
            if key:
                buckets.setdefault(("exact", key), set()).add(qid)
            identity = text_identity_from_exact_key(key)
            if identity:
                buckets.setdefault(("text", identity), set()).add(qid)
    result: dict[int, set[int]] = {qid: {qid} for qid in wanted}
    for bucket in buckets.values():
        for qid in bucket & wanted:
            result[qid] |= bucket
    return {qid: frozenset(ids) for qid, ids in result.items()}


__all__ = [
    "SameQuestionIndex",
    "SameQuestionMatch",
    "TEXT_IDENTITY_PREFIX",
    "question_identities",
    "reusable_same_question_analysis",
    "same_question",
    "same_question_ids",
    "stored_exact_keys",
    "text_identity_from_exact_key",
]
