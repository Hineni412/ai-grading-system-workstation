from __future__ import annotations

from typing import Any, Mapping, Sequence


def project_question_states(
    question_ids: Sequence[str],
    payload: Mapping[str, Any] | None,
    *,
    job_status: str = "",
) -> list[dict[str, Any]]:
    """Project public per-question state from durable generation checkpoints."""

    ordered = list(dict.fromkeys(str(item).strip() for item in question_ids if str(item).strip()))
    data = dict(payload or {})
    meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
    explicit = meta.get("question_states") if isinstance(meta, Mapping) else None
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(explicit, list):
        for raw in explicit:
            if not isinstance(raw, Mapping):
                continue
            qid = str(raw.get("question_id") or "").strip()
            state = str(raw.get("state") or "").strip()
            if qid and state in {"pending", "running", "passed", "blocked", "failed"}:
                by_id[qid] = _public_state(qid, state, raw.get("reason"), raw.get("retryable"))

    batches = meta.get("batches") if isinstance(meta, Mapping) else None
    if isinstance(batches, list):
        for batch in batches:
            if not isinstance(batch, Mapping):
                continue
            status = str(batch.get("status") or "").strip()
            state = "passed" if status == "succeeded" else "failed" if status == "failed" else "running" if status == "running" else "pending"
            reason = str(batch.get("category") or batch.get("error") or "")[:160]
            for qid in batch.get("question_ids") or []:
                clean = str(qid).strip()
                if clean:
                    by_id[clean] = _public_state(clean, state, reason, state == "failed")

    failed = set(str(item).strip() for item in meta.get("failed_question_ids", []) if str(item).strip()) if isinstance(meta, Mapping) else set()
    for qid in failed:
        if qid not in by_id:
            by_id[qid] = _public_state(qid, "blocked", "local_validation", True)
    rubric = data.get("rubric") if isinstance(data.get("rubric"), Mapping) else {}
    generated = {
        str(item.get("question_id") or "").strip()
        for item in rubric.get("questions", [])
        if isinstance(item, Mapping)
    }
    for qid in generated - failed:
        by_id.setdefault(qid, _public_state(qid, "passed", "", False))

    if meta.get("exam_intake_incomplete"):
        for qid in meta.get("exam_intake_failed_question_ids") or []:
            if str(qid) in ordered:
                by_id[str(qid)] = _public_state(
                    str(qid), "blocked", "question_bank_intake",
                    bool(meta.get("exam_intake_retryable")),
                )

    default_state = "failed" if job_status in {"failed", "cancelled"} else "pending"
    return [by_id.get(qid, _public_state(qid, default_state, "", default_state == "failed")) for qid in ordered]


def _public_state(question_id: str, state: str, reason: object, retryable: object) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "state": state,
        "reason": str(reason or "")[:160],
        "retryable": bool(retryable),
    }


__all__ = ["project_question_states"]
