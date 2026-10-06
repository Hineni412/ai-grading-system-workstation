"""Post-splitting duplicate pre-check for a config source.

Runs the same bank matching the generation job performs before model calls
(exact content key, uncertain-image stem match, near-duplicate hint) and
reports per-question results without writing anything beyond the derived
content index that ``ensure_content_index`` already maintains.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from question_bank.models.question import normalize_identity_text
from question_bank.taxonomy.curriculum_catalog import infer_curriculum_volume_from_text

from .sources import ConfigSourceService

_IMAGE_MARKER = re.compile(r"\[\[IMAGE:[^\]|]*?(?:\|[^\]]*)?\]\]", re.I | re.S)
_SUP_TAG = re.compile(r"<sup[^>]*>(.*?)</sup\s*>", re.I | re.S)
_SUB_TAG = re.compile(r"<sub[^>]*>(.*?)</sub\s*>", re.I | re.S)
_HTML_TAG = re.compile(r"<[^>]+>")

_SAME_SESSION_REASON = "题库中的相同题目来自本场考试此前的入库，不算重复。"

_REASON_BY_KIND = {
    "exact_reusable": "题库已有相同题目，生成时将复用已有分析，不调用 AI。",
    "exact_needs_analysis": "题库已有相同题目，但已存分析缺失或失效；请先在题库补齐分析，再回到本场考试继续。",
    "image_uncertain": "题库存在相同题干的带图题，图片内容需人工核对。",
    "answer_conflict": "题面与题库相同，但参考答案不同，请核对答案。",
    "same_session": _SAME_SESSION_REASON,
}


def _plain_text(text: object, *, limit: int) -> str:
    """Reduce stored markup to plain text the UI can render as a string."""
    cleaned = _SUP_TAG.sub(lambda m: "^" + m.group(1), str(text or ""))
    cleaned = _SUB_TAG.sub(lambda m: "_" + m.group(1), cleaned)
    cleaned = _HTML_TAG.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _IMAGE_MARKER.sub("[图]", cleaned)
    cleaned = normalize_identity_text(cleaned)
    return cleaned[:limit]


def _plain_excerpt(text: object, *, limit: int = 80) -> str:
    return _plain_text(text, limit=limit)


def _session_linked_bank_ids(conn: Any, session_id: int) -> set[int]:
    return {
        int(row["bank_question_id"])
        for row in conn.execute(
            """SELECT DISTINCT bank_question_id FROM grading_question_links
               WHERE grading_session_id = ? AND status <> 'rejected'""",
            (str(session_id),),
        )
    }


def _bank_question_meta(conn: Any, question_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not question_ids:
        return {}
    marks = ",".join("?" for _ in question_ids)
    rows = conn.execute(
        f"""SELECT q.id, q.question_number, q.question_text, q.answer_text,
                   p.title AS paper_title
            FROM questions q LEFT JOIN papers p ON p.id = q.paper_id
            WHERE q.id IN ({marks})""",
        sorted(question_ids),
    ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def source_duplicate_preview(
    *,
    service: ConfigSourceService,
    session_id: int,
    session_name: str,
    question_bank_db_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    """Classify each active-source question against the question bank.

    Raises ``ConfigSourceError`` subclasses for source problems; any other
    failure propagates to the router which answers with an unavailable 5xx —
    duplicate checking is advisory and must never write bank data other than
    the derived content index.
    """
    # Deferred imports keep the heavy matching pipeline out of the request
    # module's import graph.
    from backend.jobs.config_generation import config_analysis_source_questions
    from question_bank.database.schema import connect, initialize_database
    from question_bank.importers.batch_importer import (
        ParsedQuestion,
        _DuplicateIndex,
        _load_near_duplicate_questions,
        _near_duplicate_hint,
    )
    from question_bank.services.duplicate_analysis_copy_service import (
        _final_choice_letters,
        analysis_source_exact_key,
        answers_conflict,
        ensure_content_index,
        uncertain_image_candidates,
    )
    from question_bank.services.question_identity import (
        SameQuestionIndex,
        reusable_same_question_analysis,
    )

    record = service.load_active_record(session_id=session_id)
    result: dict[str, Any] = {
        "source_id": record.source_id,
        "source_revision": record.source_revision,
        "items": [],
    }
    if not record.private_blocks:
        return result
    prepared = service.prepare_generation_input(record, (), "batched", ())
    # The exact key never reads volume metadata; it only feeds the analysis
    # input's tagging context, so an unresolved volume falls back to a neutral
    # id instead of blocking the pre-check.
    volume = infer_curriculum_volume_from_text(
        " ".join(
            item for item in (str(session_name or ""), record.safe_filename) if item
        )
    )
    volume_id = str(volume["id"]) if isinstance(volume, dict) else "unresolved"
    sources, blocks = config_analysis_source_questions(
        prepared.confirmed_blocks,
        prepared.question_images,
        curriculum_volume_id=volume_id,
        volume=volume if isinstance(volume, dict) else {},
    )
    bank_path = Path(question_bank_db_path)
    if not sources or not bank_path.is_file():
        return result
    keys: dict[str, str] = {}
    for source in sources:
        key = analysis_source_exact_key(source.question, data_root=data_root)
        if key:
            keys[source.source_question_ref] = key
    if not keys:
        return result

    initialize_database(bank_path)
    with connect(bank_path) as conn:
        ensure_content_index(conn, data_root=data_root)
        # Same stem text, options and formulas merge at import even when the
        # picture encodings differ; preview reports them like exact matches.
        same_questions = SameQuestionIndex.load(conn, keys=list(keys.values()))
        uncertain = uncertain_image_candidates(
            conn,
            tuple(
                source
                for source in sources
                if same_questions.match(keys.get(source.source_question_ref, "")) is None
            ),
        )
        duplicate_index = None
        linked_ids = _session_linked_bank_ids(conn, session_id)
        matched_ids = {
            int(question_id)
            for question_id in uncertain.values()
        } | {
            match.question_id
            for source in sources
            if (match := same_questions.match(keys.get(source.source_question_ref, ""))) is not None
        }
        meta = _bank_question_meta(conn, matched_ids)

        def item(
            source: Any,
            kind: str,
            bank_id: int,
            *,
            similarity: float = 1.0,
            reason: str = "",
            bank_answer: str | None = None,
        ) -> dict[str, Any]:
            info = meta.get(int(bank_id), {})
            entry = {
                "question_id": source.source_question_ref,
                "kind": kind,
                "matched_question_id": int(bank_id),
                "matched_paper_title": str(info.get("paper_title") or ""),
                "matched_question_number": str(info.get("question_number") or ""),
                "similarity": float(similarity),
                "matched_question_excerpt": _plain_excerpt(
                    info.get("question_text")
                ),
                "reason": reason or _REASON_BY_KIND.get(kind, ""),
            }
            if kind == "answer_conflict" and bank_answer is not None:
                entry["bank_answer_text"] = _plain_text(bank_answer, limit=300)
                letters = _final_choice_letters(bank_answer)
                # The override teachers apply when they pick the bank answer:
                # bare option letters when the bank marks a final choice,
                # otherwise the full cleaned answer text.
                entry["suggested_answer_override"] = (
                    "".join(sorted(letters))
                    if letters
                    else _plain_text(bank_answer, limit=20000)
                )
            return entry

        items: list[dict[str, Any]] = []
        for source, block in zip(sources, blocks, strict=True):
            reference = source.source_question_ref
            key = keys.get(reference)
            match = same_questions.match(key or "")
            exact_id = match.question_id if match is not None else None
            uncertain_id = uncertain.get(reference)
            if uncertain_id is not None and int(uncertain_id) in linked_ids:
                items.append(item(source, "same_session", int(uncertain_id)))
                continue
            if uncertain_id is not None:
                items.append(item(source, "image_uncertain", int(uncertain_id)))
                continue
            if exact_id is not None:
                bank_id = int(exact_id)
                if bank_id in linked_ids:
                    items.append(item(source, "same_session", bank_id))
                    continue
                bank_answer = str(meta.get(bank_id, {}).get("answer_text") or "")
                source_answer = str(
                    getattr(
                        source.question.tagging_context, "answer_text", ""
                    )
                    or ""
                )
                if bank_answer and answers_conflict(
                    bank_answer, source_answer, data_root=data_root
                ):
                    items.append(
                        item(
                            source,
                            "answer_conflict",
                            bank_id,
                            bank_answer=bank_answer,
                        )
                    )
                    continue
                if (
                    reusable_same_question_analysis(
                        bank_path,
                        match=match,
                        target_question=source.question,
                        data_root=data_root,
                    )
                    is not None
                ):
                    items.append(item(source, "exact_reusable", bank_id))
                else:
                    items.append(item(source, "exact_needs_analysis", bank_id))
                continue
            if duplicate_index is None:
                duplicate_index = _DuplicateIndex(
                    data_root=data_root, questions=_load_near_duplicate_questions(conn)
                )
            hint = _near_duplicate_hint(
                ParsedQuestion(
                    question_number=str(block.get("question_number") or reference),
                    question_text=str(block.get("question_text") or ""),
                    source_file=str(record.safe_filename),
                    page_range="",
                    answer_text=str(block.get("answer_text") or "") or None,
                    question_type=str(block.get("question_type") or "") or None,
                ),
                duplicate_index,
            )
            if hint is None:
                continue
            bank_id = int(hint["matched_question_id"])
            if bank_id in linked_ids:
                items.append(item(source, "same_session", bank_id))
                continue
            if bank_id not in meta:
                meta.update(_bank_question_meta(conn, {bank_id}))
            hint_kind = str(hint.get("match_kind") or "")
            items.append(
                item(
                    source,
                    hint_kind if hint_kind in {"variant", "suspected"} else "suspected",
                    bank_id,
                    similarity=float(hint.get("similarity") or 0.0),
                    reason=str(hint.get("reason") or ""),
                )
            )
    result["items"] = items
    return result


__all__ = ["source_duplicate_preview"]
