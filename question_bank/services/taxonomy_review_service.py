from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
    QuestionWriteNotFound,
)
from question_bank.taxonomy.governance import (
    TaxonomyGovernance,
    TaxonomyProposalNotFound,
)


_DIMENSION_TAG_TYPES = {
    "curriculum": "exam_scope",
    "knowledge": "knowledge_point",
    "ability": "ability",
    "method": "method",
    "model": "model",
    "special_type": "special_type",
}
_STATE_LOCK = threading.RLock()


class TaxonomyReviewRequestConflict(RuntimeError):
    """The same request token was reused for a different command."""


class TaxonomyReviewSelectionInvalid(ValueError):
    """Selected questions are not part of the proposal evidence."""


class TaxonomyReviewApplicationNotFound(LookupError):
    """A requested application receipt does not exist."""


class TaxonomyReviewService:
    """Resolve a proposal and idempotently apply the decision to selected questions."""

    def __init__(
        self,
        *,
        review_state_path: Path,
        governance: TaxonomyGovernance,
        write_service: QuestionBankWriteService,
    ) -> None:
        self.review_state_path = Path(review_state_path)
        self.governance = governance
        self.write_service = write_service

    def review_proposal(
        self,
        *,
        proposal_id: str,
        decision: str,
        expected_revision: int,
        request_token: str,
        edited_name: str | None = None,
        target_term_ids: Sequence[str] = (),
        question_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        proposal_id = str(proposal_id or "").strip()
        target_ids = _unique_strings(target_term_ids)
        selected_input = (
            None
            if question_ids is None
            else _unique_positive_ids(question_ids)
        )
        command = {
            "kind": "review",
            "proposal_id": proposal_id,
            "decision": str(decision or "").strip(),
            "expected_revision": int(expected_revision),
            "edited_name": str(edited_name or "").strip(),
            "target_term_ids": target_ids,
            "question_ids": selected_input,
        }
        fingerprint = _fingerprint(command)
        replay = self._receipt_replay(token, fingerprint)
        if replay is not None:
            return replay

        proposal = self.governance.get_proposal(proposal_id)
        if proposal is None:
            raise TaxonomyProposalNotFound("Proposal does not exist")
        available = _unique_positive_ids(proposal.get("question_refs", []))
        selected = available if selected_input is None else selected_input
        if not set(selected).issubset(set(available)):
            raise TaxonomyReviewSelectionInvalid(
                "Selected questions must belong to the proposal evidence"
            )

        reviewed = self.governance.review_proposal(
            proposal_id=proposal_id,
            decision=command["decision"],
            edited_name=command["edited_name"] or None,
            target_term_ids=target_ids,
            expected_revision=int(expected_revision),
            request_token=token,
            include_extended=True,
        )
        approved_terms = [
            dict(item)
            for item in reviewed.get("approved_terms", [])
            if isinstance(item, Mapping)
        ]
        application = self._apply_terms(
            approved_terms,
            selected_question_ids=selected,
        )
        result = {
            **reviewed,
            "approved_terms": approved_terms,
            "application": application,
            "request_token": token,
        }
        self._remember_receipt(
            token,
            fingerprint=fingerprint,
            command=command,
            result=result,
        )
        return copy.deepcopy(result)

    def retry_application(
        self,
        *,
        application_token: str,
        request_token: str,
        question_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        source_token = _request_token(application_token)
        retry_token = _request_token(request_token)
        state = self._read_state()
        source = state["receipts"].get(source_token)
        if not isinstance(source, Mapping):
            raise TaxonomyReviewApplicationNotFound(
                "Taxonomy review application does not exist"
            )
        source_result = source.get("result")
        if not isinstance(source_result, Mapping):
            raise TaxonomyReviewApplicationNotFound(
                "Taxonomy review application is unavailable"
            )
        source_application = source_result.get("application")
        if not isinstance(source_application, Mapping):
            raise TaxonomyReviewApplicationNotFound(
                "Taxonomy review application is unavailable"
            )
        failed_ids = _unique_positive_ids(
            item.get("question_id")
            for item in source_application.get("failures", [])
            if isinstance(item, Mapping)
        )
        selected = (
            failed_ids
            if question_ids is None
            else _unique_positive_ids(question_ids)
        )
        if not set(selected).issubset(set(failed_ids)):
            raise TaxonomyReviewSelectionInvalid(
                "Only failed question applications can be retried"
            )
        command = {
            "kind": "retry_application",
            "application_token": source_token,
            "question_ids": selected,
        }
        fingerprint = _fingerprint(command)
        replay = self._receipt_replay(retry_token, fingerprint)
        if replay is not None:
            return replay

        terms = [
            dict(item)
            for item in source_result.get("approved_terms", [])
            if isinstance(item, Mapping)
        ]
        retried = self._apply_terms(terms, selected_question_ids=selected)
        previously_applied = _unique_positive_ids(
            source_application.get("applied_question_ids", [])
        )
        untouched_failures = [
            dict(item)
            for item in source_application.get("failures", [])
            if isinstance(item, Mapping)
            and int(item.get("question_id") or 0) not in set(selected)
        ]
        failures = [*untouched_failures, *retried["failures"]]
        applied = _unique_positive_ids(
            [*previously_applied, *retried["applied_question_ids"]]
        )
        all_selected = _unique_positive_ids(
            source_application.get("selected_question_ids", [])
        )
        application = {
            "status": _application_status(
                selected_count=len(all_selected),
                applied_count=len(applied),
                failure_count=len(failures),
            ),
            "selected_question_ids": all_selected,
            "applied_question_ids": applied,
            "failures": failures,
        }
        result = {
            **copy.deepcopy(dict(source_result)),
            "application": application,
            "request_token": retry_token,
            "retry_of": source_token,
        }
        self._remember_receipt(
            retry_token,
            fingerprint=fingerprint,
            command=command,
            result=result,
        )
        return copy.deepcopy(result)

    def _apply_terms(
        self,
        terms: Sequence[Mapping[str, Any]],
        *,
        selected_question_ids: Sequence[int],
    ) -> dict[str, Any]:
        selected = _unique_positive_ids(selected_question_ids)
        tags: list[ConfirmedQuestionTag] = []
        for term in terms:
            tag_type = _DIMENSION_TAG_TYPES.get(
                str(term.get("dimension") or "").strip()
            )
            name = str(term.get("name") or "").strip()
            if tag_type and name:
                tags.append(ConfirmedQuestionTag(tag_type, name))
        if not selected or not tags:
            return {
                "status": "not_requested",
                "selected_question_ids": selected,
                "applied_question_ids": [],
                "failures": [],
            }
        applied: list[int] = []
        failures: list[dict[str, Any]] = []
        for question_id in selected:
            try:
                self.write_service.add_tags(question_id, tags=tags)
            except QuestionWriteNotFound:
                failures.append(
                    {
                        "question_id": question_id,
                        "category": "question_not_found",
                        "message": "关联题目不存在或已移出题库。",
                    }
                )
            except Exception:
                failures.append(
                    {
                        "question_id": question_id,
                        "category": "write_failed",
                        "message": "题目标签暂时未能写入，可单独重试。",
                    }
                )
            else:
                applied.append(question_id)
        return {
            "status": _application_status(
                selected_count=len(selected),
                applied_count=len(applied),
                failure_count=len(failures),
            ),
            "selected_question_ids": selected,
            "applied_question_ids": applied,
            "failures": failures,
        }

    def _receipt_replay(
        self,
        token: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        state = self._read_state()
        receipt = state["receipts"].get(token)
        if receipt is None:
            return None
        if receipt.get("fingerprint") != fingerprint:
            raise TaxonomyReviewRequestConflict(
                "Request token was already used for a different review command"
            )
        result = receipt.get("result")
        if not isinstance(result, Mapping):
            raise TaxonomyReviewRequestConflict(
                "Stored review receipt is incomplete"
            )
        return copy.deepcopy(dict(result))

    def _remember_receipt(
        self,
        token: str,
        *,
        fingerprint: str,
        command: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            existing = state["receipts"].get(token)
            if existing is not None:
                if existing.get("fingerprint") != fingerprint:
                    raise TaxonomyReviewRequestConflict(
                        "Request token was already used for a different review command"
                    )
                return
            state["receipts"][token] = {
                "fingerprint": fingerprint,
                "command": copy.deepcopy(dict(command)),
                "result": copy.deepcopy(dict(result)),
            }
            _write_json_atomic(self.review_state_path, state)

    def _read_state(self) -> dict[str, Any]:
        with _STATE_LOCK:
            return self._read_state_unlocked()

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.review_state_path.exists():
            return {"schema_version": 1, "receipts": {}}
        payload = json.loads(self.review_state_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("receipts"), Mapping)
        ):
            raise RuntimeError("Taxonomy review receipt state is invalid")
        return {
            "schema_version": 1,
            "receipts": copy.deepcopy(dict(payload["receipts"])),
        }


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("request_token must contain 32 hexadecimal characters")
    return token


def _unique_strings(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _unique_positive_ids(values) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id > 0 and question_id not in result:
            result.append(question_id)
    return result


def _application_status(
    *,
    selected_count: int,
    applied_count: int,
    failure_count: int,
) -> str:
    if selected_count <= 0:
        return "not_requested"
    if failure_count <= 0 and applied_count == selected_count:
        return "applied"
    if applied_count > 0:
        return "partial"
    return "failed"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
