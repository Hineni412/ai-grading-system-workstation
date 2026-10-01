from __future__ import annotations

from question_bank.atomic_files import replace_with_retry

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect
from question_bank.services.question_revision import question_revision
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
    "thought": "thought",
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


class TaxonomyReviewUndoConflict(RuntimeError):
    """A later taxonomy or question edit prevents safe automatic undo."""


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

    def preview_suggestion_batch(
        self,
        run: Mapping[str, Any],
        *,
        base_revision: int,
        policy_version: str = "strict-exact-v1",
    ) -> dict[str, Any]:
        snapshot = self.governance.observation_snapshot()
        if (
            int(base_revision) != int(snapshot["taxonomy_revision"])
            or int(run.get("taxonomy_revision") or -1) != int(base_revision)
            or int(run.get("evidence_revision") or 0)
            != int(snapshot["evidence_revision"])
            or str(run.get("graph_release_id") or "")
            != str(snapshot.get("graph_release_id") or "")
        ):
            raise TaxonomyReviewSelectionInvalid(
                "Suggestion versions or current evidence changed"
            )
        terms = {
            term["id"]: term
            for values in self.governance.catalog()["dimensions"].values()
            for term in values
        }
        current_refs = snapshot["current_question_refs"]
        active_ids = self._active_question_ids(
            question_id
            for refs in current_refs.values()
            for question_id in refs
        )
        items: list[dict[str, Any]] = []
        for item in run.get("items", []):
            if not isinstance(item, Mapping) or item.get("status") != "suggested":
                continue
            proposal = self.governance.get_proposal(str(item.get("proposal_id") or ""))
            suggestion = item.get("suggestion")
            if proposal is None or not isinstance(suggestion, Mapping):
                continue
            refs = [
                question_id
                for question_id in current_refs.get(proposal["id"], [])
                if question_id in active_ids
            ]
            targets = _unique_strings(suggestion.get("target_term_ids", []))
            target = terms.get(targets[0]) if len(targets) == 1 else None
            evidence_matches = set(refs) == set(
                _unique_positive_ids(suggestion.get("evidence_question_ids", []))
            )
            automatic = bool(
                refs
                and suggestion.get("relation_kind") == "exact"
                and not suggestion.get("legacy_format")
                and float(suggestion.get("confidence") or 0) >= 0.80
                and len(targets) == 1
                and target is not None
                and target.get("status") == "active"
                and target.get("dimension") == proposal["dimension"]
                and evidence_matches
                and int(suggestion.get("taxonomy_revision") or -1)
                == int(base_revision)
                and str(suggestion.get("graph_release_id") or "")
                == str(snapshot.get("graph_release_id") or "")
            )
            reasons: list[str] = []
            if not refs:
                reasons.append("current_evidence_missing")
            if suggestion.get("legacy_format"):
                reasons.append("legacy_suggestion_format")
            if suggestion.get("relation_kind") != "exact":
                reasons.append("teacher_judgement_required")
            if float(suggestion.get("confidence") or 0) < 0.80:
                reasons.append("confidence_below_threshold")
            if len(targets) != 1:
                reasons.append("target_count_not_one")
            if target is None or target.get("status") != "active":
                reasons.append("target_not_active")
            elif target.get("dimension") != proposal["dimension"]:
                reasons.append("target_dimension_mismatch")
            if not evidence_matches:
                reasons.append("evidence_changed")
            items.append(
                {
                    "proposal_id": proposal["id"],
                    "dimension": proposal["dimension"],
                    "proposed_name": proposal["proposed_name"],
                    "question_ids": refs,
                    "suggestion": copy.deepcopy(dict(suggestion)),
                    "automatic": automatic,
                    "reasons": reasons,
                }
            )
        policy_fingerprint = _fingerprint(
            {
                "run_id": run.get("run_id"),
                "base_revision": base_revision,
                "evidence_revision": snapshot["evidence_revision"],
                "graph_release_id": snapshot.get("graph_release_id"),
                "policy_version": policy_version,
                "items": items,
            }
        )
        return {
            "run_id": str(run.get("run_id") or ""),
            "base_revision": int(base_revision),
            "evidence_revision": int(snapshot["evidence_revision"]),
            "graph_release_id": str(snapshot.get("graph_release_id") or ""),
            "policy_version": policy_version,
            "policy_fingerprint": policy_fingerprint,
            "items": items,
            "counts": {
                "automatic": sum(bool(item["automatic"]) for item in items),
                "manual": sum(not bool(item["automatic"]) for item in items),
                "total": len(items),
            },
        }

    def apply_suggestion_batch(
        self,
        run: Mapping[str, Any],
        *,
        base_revision: int,
        request_token: str,
        accepted_manual_decisions: Sequence[Mapping[str, Any]] = (),
        policy_version: str = "strict-exact-v1",
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        command = {
            "kind": "suggestion_batch",
            "run_id": str(run.get("run_id") or ""),
            "base_revision": int(base_revision),
            "policy_version": policy_version,
            "accepted_manual_decisions": [
                dict(item) for item in accepted_manual_decisions
            ],
        }
        fingerprint = _fingerprint(command)
        replay = self._receipt_replay(token, fingerprint)
        if replay is not None:
            return self._resume_receipt(replay)
        preview = self.preview_suggestion_batch(
            run,
            base_revision=base_revision,
            policy_version=policy_version,
        )
        manual = {
            str(item.get("proposal_id") or ""): dict(item)
            for item in accepted_manual_decisions
            if isinstance(item, Mapping)
        }
        commands: list[dict[str, Any]] = []
        automated: list[str] = []
        teacher_confirmed: list[str] = []
        skipped: list[dict[str, str]] = []
        for item in preview["items"]:
            proposal_id = item["proposal_id"]
            decision = manual.get(proposal_id)
            if decision is not None:
                if decision.get("decision") == "defer":
                    skipped.append(
                        {"proposal_id": proposal_id, "reason": "teacher_deferred"}
                    )
                    continue
                commands.append(
                    {
                        "proposal_id": proposal_id,
                        "decision": str(decision.get("decision") or ""),
                        "target_term_ids": _unique_strings(
                            decision.get("target_term_ids", [])
                        ),
                        "edited_name": str(decision.get("edited_name") or ""),
                    }
                )
                teacher_confirmed.append(proposal_id)
                continue
            if item["automatic"]:
                commands.append(
                    {
                        "proposal_id": proposal_id,
                        "decision": "merge",
                        "target_term_ids": item["suggestion"]["target_term_ids"],
                    }
                )
                automated.append(proposal_id)
                continue
            skipped.append(
                {"proposal_id": proposal_id, "reason": "teacher_deferred"}
            )
        if not commands:
            raise TaxonomyReviewSelectionInvalid("Batch has no accepted decisions")
        receipt = {
            "operation_id": "",
            "request_token": token,
            "status": "prepared",
            "base_revision": int(base_revision),
            "taxonomy_revision": None,
            "automated_proposal_ids": automated,
            "teacher_confirmed_proposal_ids": teacher_confirmed,
            "skipped": skipped,
            "remaining_count": len(preview["items"]) - len(commands),
            "governance_commands": commands,
            "outbox": [],
            "undo_status": "available",
        }
        self._remember_receipt(
            token,
            fingerprint=fingerprint,
            command=command,
            result=receipt,
        )
        return self._resume_receipt(receipt)

    def read_operation(self, operation_id: str) -> dict[str, Any]:
        wanted = str(operation_id or "").strip()
        state = self._read_state()
        for receipt in state["receipts"].values():
            result = receipt.get("result") if isinstance(receipt, Mapping) else None
            if isinstance(result, Mapping) and result.get("operation_id") == wanted:
                return self._resume_receipt(copy.deepcopy(dict(result)))
        raise TaxonomyReviewApplicationNotFound(
            "Taxonomy review operation does not exist"
        )

    def undo_operation(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        receipt = self.read_operation(operation_id)
        if receipt.get("undo_status") == "completed":
            return receipt
        if int(receipt["taxonomy_revision"]) != int(expected_revision):
            raise TaxonomyReviewUndoConflict("A later taxonomy revision exists")
        self._preflight_undo(receipt)
        receipt["status"] = "undoing"
        self._replace_receipt_result(receipt["request_token"], receipt)
        undone = self.governance.undo_review_batch(
            operation_id=operation_id,
            expected_revision=int(expected_revision),
            request_token=_request_token(request_token),
        )
        receipt["undo_taxonomy_revision"] = int(undone["taxonomy_revision"])
        self._drain_undo(receipt)
        receipt["status"] = "undone"
        receipt["undo_status"] = "completed"
        self._replace_receipt_result(receipt["request_token"], receipt)
        return copy.deepcopy(receipt)

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

    def _active_question_ids(self, values) -> set[int]:
        ids = _unique_positive_ids(values)
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        with connect(self.write_service.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT q.id
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id IN ({placeholders})
                  AND COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """,
                ids,
            ).fetchall()
        return {int(row[0]) for row in rows}

    def _build_outbox(
        self,
        decisions: Sequence[Mapping[str, Any]],
        operation_id: str,
    ) -> list[dict[str, Any]]:
        refs = self.governance.observation_snapshot()["current_question_refs"]
        by_question: dict[int, dict[str, Any]] = {}
        for decision in decisions:
            proposal = decision.get("proposal")
            if not isinstance(proposal, Mapping):
                continue
            approved = [
                item
                for item in decision.get("approved_terms", [])
                if isinstance(item, Mapping)
            ]
            for question_id in refs.get(str(proposal.get("id") or ""), []):
                group = by_question.setdefault(
                    int(question_id),
                    {
                        "question_id": int(question_id),
                        "status": "pending",
                        "before_revision": None,
                        "after_revision": None,
                        "error": None,
                        "tags": [],
                    },
                )
                for term in approved:
                    tag_type = _DIMENSION_TAG_TYPES.get(
                        str(term.get("dimension") or "")
                    )
                    tag_value = str(term.get("name") or "").strip()
                    if not tag_type or not tag_value:
                        raise TaxonomyReviewSelectionInvalid(
                            "A taxonomy dimension has no question-tag mapping"
                        )
                    key = (tag_type, tag_value)
                    if any(
                        (item["tag_type"], item["tag_value"]) == key
                        for item in group["tags"]
                    ):
                        continue
                    group["tags"].append(
                        {
                            "proposal_id": str(proposal.get("id") or ""),
                            "tag_type": tag_type,
                            "tag_value": tag_value,
                            "tag_id": None,
                            "preexisting": None,
                        }
                    )
        return [by_question[key] for key in sorted(by_question)]

    def _resume_receipt(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(receipt))
        if result.get("status") == "prepared":
            reviewed = self.governance.review_batch(
                commands=result["governance_commands"],
                expected_revision=int(result["base_revision"]),
                request_token=result["request_token"],
            )
            operation_id = str(reviewed["operation_id"])
            result["operation_id"] = operation_id
            result["taxonomy_revision"] = int(
                reviewed["taxonomy_revision"]
            )
            result["outbox"] = self._build_outbox(
                reviewed["decisions"], operation_id
            )
            result["status"] = "applying"
            self._replace_receipt_result(result["request_token"], result)
        if result.get("status") == "applying":
            for group in result.get("outbox", []):
                if isinstance(group, dict) and group.get("status") != "applied":
                    self._apply_outbox_group(result["operation_id"], group)
                    self._replace_receipt_result(result["request_token"], result)
            failures = [
                group
                for group in result.get("outbox", [])
                if isinstance(group, Mapping) and group.get("status") != "applied"
            ]
            result["status"] = "partial" if failures else "applied"
            result["application"] = {
                "status": result["status"],
                "applied_question_ids": [
                    int(group["question_id"])
                    for group in result.get("outbox", [])
                    if isinstance(group, Mapping) and group.get("status") == "applied"
                ],
                "failures": [
                    {
                        "question_id": int(group["question_id"]),
                        "category": "write_failed",
                        "message": str(group.get("error") or "标签待补写"),
                    }
                    for group in failures
                ],
            }
            self._replace_receipt_result(result["request_token"], result)
        elif result.get("status") == "undoing":
            self._drain_undo(result)
            result["status"] = "undone"
            result["undo_status"] = "completed"
            self._replace_receipt_result(result["request_token"], result)
        return copy.deepcopy(result)

    def _apply_outbox_group(
        self,
        operation_id: str,
        group: dict[str, Any],
    ) -> None:
        question_id = int(group["question_id"])
        try:
            with connect(self.write_service.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT q.id
                    FROM questions q
                    LEFT JOIN papers p ON p.id = q.paper_id
                    WHERE q.id = ?
                      AND COALESCE(q.is_deleted, 0) = 0
                      AND COALESCE(p.import_status, '') <> 'deleted'
                    """,
                    (question_id,),
                ).fetchone()
                if row is None:
                    raise QuestionWriteNotFound("Question is not active")
                before_revision = question_revision(conn, question_id)
                if before_revision is None:
                    raise QuestionWriteNotFound("Question not found")
                stored_before = group.get("before_revision")
                if stored_before and stored_before != before_revision:
                    raise TaxonomyReviewUndoConflict(
                        "Question changed before taxonomy tags were applied"
                    )
                group["before_revision"] = before_revision
                for tag in group["tags"]:
                    existing = conn.execute(
                        """
                        SELECT id, source, model_name
                        FROM question_tags
                        WHERE question_id = ? AND tag_type = ? AND tag_value = ?
                        ORDER BY id LIMIT 1
                        """,
                        (question_id, tag["tag_type"], tag["tag_value"]),
                    ).fetchone()
                    if existing is not None:
                        inserted_by_operation = (
                            str(existing["source"] or "") == "taxonomy_review"
                            and str(existing["model_name"] or "") == operation_id
                        )
                        tag["tag_id"] = int(existing["id"])
                        tag["preexisting"] = not inserted_by_operation
                        continue
                    cursor = conn.execute(
                        """
                        INSERT INTO question_tags (
                            question_id, tag_type, tag_value, confidence,
                            source, model_name
                        ) VALUES (?, ?, ?, NULL, 'taxonomy_review', ?)
                        """,
                        (
                            question_id,
                            tag["tag_type"],
                            tag["tag_value"],
                            operation_id,
                        ),
                    )
                    tag["tag_id"] = int(cursor.lastrowid)
                    tag["preexisting"] = False
                conn.execute(
                    "UPDATE questions SET updated_at = datetime('now','localtime') WHERE id = ?",
                    (question_id,),
                )
                group["after_revision"] = question_revision(conn, question_id)
                self._write_application_ledger(conn, operation_id, group)
            group["status"] = "applied"
            group["error"] = None
        except Exception as exc:
            group["status"] = "pending"
            group["error"] = type(exc).__name__

    @staticmethod
    def _write_application_ledger(
        conn: Any,
        operation_id: str,
        group: Mapping[str, Any],
    ) -> None:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_review_applications'"
        ).fetchone()
        if exists is None:
            return
        for tag in group.get("tags", []):
            conn.execute(
                """
                INSERT INTO taxonomy_review_applications (
                    operation_id, proposal_id, question_id, question_tag_id,
                    tag_type, tag_value, preexisting, before_revision,
                    after_revision, write_status, undo_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', 'available')
                ON CONFLICT(operation_id, proposal_id, question_id, tag_type, tag_value)
                DO UPDATE SET
                    question_tag_id=excluded.question_tag_id,
                    preexisting=excluded.preexisting,
                    before_revision=excluded.before_revision,
                    after_revision=excluded.after_revision,
                    write_status='applied'
                """,
                (
                    operation_id,
                    tag["proposal_id"],
                    int(group["question_id"]),
                    tag.get("tag_id"),
                    tag["tag_type"],
                    tag["tag_value"],
                    1 if tag.get("preexisting") else 0,
                    group.get("before_revision"),
                    group.get("after_revision"),
                ),
            )

    def _preflight_undo(self, receipt: Mapping[str, Any]) -> None:
        if int(self.governance.snapshot()["revision"]) != int(
            receipt["taxonomy_revision"]
        ):
            raise TaxonomyReviewUndoConflict("A later taxonomy revision exists")
        with connect(self.write_service.db_path) as conn:
            for group in receipt.get("outbox", []):
                if not isinstance(group, Mapping) or group.get("status") != "applied":
                    continue
                question_id = int(group["question_id"])
                if question_revision(conn, question_id) != group.get("after_revision"):
                    raise TaxonomyReviewUndoConflict(
                        "A question changed after this taxonomy batch"
                    )
                for tag in group.get("tags", []):
                    if tag.get("preexisting"):
                        continue
                    row = conn.execute(
                        "SELECT source, model_name FROM question_tags WHERE id = ?",
                        (tag.get("tag_id"),),
                    ).fetchone()
                    if (
                        row is None
                        or str(row["source"] or "") != "taxonomy_review"
                        or str(row["model_name"] or "") != receipt["operation_id"]
                    ):
                        raise TaxonomyReviewUndoConflict(
                            "A taxonomy tag changed after this batch"
                        )

    def _drain_undo(self, receipt: dict[str, Any]) -> None:
        with connect(self.write_service.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            has_ledger = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taxonomy_review_applications'"
            ).fetchone() is not None
            for group in receipt.get("outbox", []):
                if not isinstance(group, dict) or group.get("undo_status") == "completed":
                    continue
                question_id = int(group["question_id"])
                if has_ledger:
                    conn.execute(
                        """
                        UPDATE taxonomy_review_applications
                        SET question_tag_id = CASE
                                WHEN preexisting = 0 THEN NULL
                                ELSE question_tag_id
                            END,
                            undo_status = CASE
                                WHEN preexisting = 1 THEN 'preserved'
                                ELSE 'removed'
                            END,
                            updated_at = datetime('now','localtime')
                        WHERE operation_id = ? AND question_id = ?
                        """,
                        (receipt["operation_id"], question_id),
                    )
                deleted_any = False
                for tag in group.get("tags", []):
                    if tag.get("preexisting"):
                        continue
                    cursor = conn.execute(
                        """
                        DELETE FROM question_tags
                        WHERE id = ? AND source = 'taxonomy_review' AND model_name = ?
                        """,
                        (tag.get("tag_id"), receipt["operation_id"]),
                    )
                    deleted_any = deleted_any or cursor.rowcount > 0
                if deleted_any:
                    conn.execute(
                        "UPDATE questions SET updated_at = datetime('now','localtime') WHERE id = ?",
                        (question_id,),
                    )
                group["undo_status"] = "completed"

    def _replace_receipt_result(
        self,
        token: str,
        result: Mapping[str, Any],
    ) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            receipt = state["receipts"].get(token)
            if receipt is None:
                return
            receipt["result"] = copy.deepcopy(dict(result))
            _write_json_atomic(self.review_state_path, state)

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
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
