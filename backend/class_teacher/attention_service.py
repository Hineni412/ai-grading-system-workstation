from __future__ import annotations

import json
import hashlib
from contextlib import closing
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .action_ledger_service import ActionLedgerService
from .assessment_evidence_service import AssessmentEvidenceService
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository


_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_due(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise VaultError(
            "attention_datetime_invalid",
            f"{label}无效",
            status_code=422,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TIMEZONE)
    return parsed.astimezone(UTC).isoformat()


class AttentionService:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        evidence: AssessmentEvidenceService,
        actions: ActionLedgerService,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self.evidence = evidence
        self.actions = actions

    def create_from_evidence(
        self,
        *,
        token: str,
        operation_id: str,
        evidence_version_id: str,
        observed_fact: str,
        comparability: str,
        limitations: list[str],
        verification_question: str,
        low_risk_next_step: str,
        evidence_sufficiency: str,
        review_suggestion: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "attention.create")
        if replay is not None:
            return self.get(
                token=token,
                attention_card_id=str(replay["attention_card_id"]),
            )
        evidence = self.evidence.get_evidence(
            token=token,
            evidence_version_id=evidence_version_id,
        )
        if evidence["state"] != "active":
            raise VaultError(
                "attention_evidence_inactive",
                "已修订或删除的证据不能生成关注卡",
                status_code=422,
            )
        prior_items = [
            item
            for item in self.evidence.list_subject_evidence(
                token=token,
                subject_id=str(evidence["subject_id"]),
            )["items"]
            if item["subject_name"] == evidence["subject_name"]
            and item["evidence_version_id"] != evidence_version_id
            and item["occurred_on"] <= evidence["occurred_on"]
        ]
        if prior_items:
            derived = self.evidence.compare(
                token=token,
                older_evidence_version_id=str(
                    prior_items[-1]["evidence_version_id"]
                ),
                newer_evidence_version_id=evidence_version_id,
            )
            comparability = str(derived["comparability"])
            limitations = list(derived["limitations"])
        else:
            comparability = "insufficient_information"
            limitations = ["当前只有单条证据，不能形成稳定结论"]
        if comparability not in {
            "directly_comparable",
            "reference_only",
            "not_comparable",
            "insufficient_information",
        }:
            raise VaultError(
                "attention_comparability_invalid",
                "关注卡可比性无效",
                status_code=422,
            )
        card_id = uuid4().hex
        object_id = f"attention-card-{card_id}"
        timestamp = _iso()
        payload = {
            "observed_fact": (
                f"{evidence['title']}记录为{evidence['result_state']}"
                + (
                    f" {evidence['score']} 分"
                    if evidence.get("score") is not None else ""
                )
            ),
            "evidence_source": {
                "evidence_version_id": evidence_version_id,
                "subject_name": evidence["subject_name"],
                "assessment_title": evidence["title"],
                "occurred_on": evidence["occurred_on"],
                "result_state": evidence["result_state"],
            },
            "comparability": comparability,
            "limitations": [
                self._text(value, "证据局限", 1000)
                for value in limitations
            ],
            "verification_question": self._text(
                verification_question,
                "需要核实的问题",
                2000,
            ),
            "low_risk_next_step": "建议教师先了解近期情况",
            "evidence_sufficiency": self._text(
                evidence_sufficiency,
                "证据充分程度",
                1000,
            ),
            "review_suggestion": self._text(
                review_suggestion,
                "复查建议",
                2000,
            ),
            "teacher_decision_reason": None,
            "review_at": None,
            "model_enabled": False,
            "physical_request_count": 0,
            "risk_score": None,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                existing = connection.execute(
                    """
                    SELECT attention_card_id FROM attention_cards
                    WHERE evidence_version_id = ?
                    """,
                    (evidence_version_id,),
                ).fetchone()
                if existing is not None:
                    return self.get(
                        token=token,
                        attention_card_id=str(
                            existing["attention_card_id"]
                        ),
                    )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="attention_card",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO attention_cards (
                        attention_card_id, subject_id,
                        evidence_version_id, payload_object_id,
                        state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'draft', ?, ?)
                    """,
                    (
                        card_id,
                        evidence["subject_id"],
                        evidence_version_id,
                        object_id,
                        timestamp,
                        timestamp,
                    ),
                )
                self._remember(
                    connection,
                    operation_id,
                    "attention.create",
                    {"attention_card_id": card_id},
                )
        return self.get(token=token, attention_card_id=card_id)

    def get(
        self,
        *,
        token: str,
        attention_card_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = self._row(connection, attention_card_id)
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        return {
            "attention_card_id": attention_card_id,
            "revision": revision,
            "subject_id": str(row["subject_id"]),
            "evidence_version_id": str(row["evidence_version_id"]),
            "state": str(row["state"]),
            "decision": row["decision"],
            "action_id": row["action_id"],
            **payload,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_for_subject(
        self,
        *,
        token: str,
        subject_id: str,
    ) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT attention_card_id FROM attention_cards
                    WHERE subject_id = ? ORDER BY created_at DESC
                    """,
                    (subject_id,),
                ).fetchall()
            ]
        return {
            "items": [
                self.get(token=token, attention_card_id=card_id)
                for card_id in ids
            ]
        }

    def resolve(
        self,
        *,
        token: str,
        attention_card_id: str,
        operation_id: str,
        revision: int,
        decision: str,
        reason: str | None,
        plan_id: str | None,
        review_at: str | None,
    ) -> dict[str, object]:
        replay = self._idempotent(operation_id, "attention.resolve")
        if replay is not None:
            return replay
        if decision not in {"follow_up", "observe", "no_action"}:
            raise VaultError(
                "attention_decision_invalid",
                "关注卡处置无效",
                status_code=422,
            )
        normalized_review = _normalize_due(review_at, "复查时间")
        if decision in {"follow_up", "observe"} and normalized_review is None:
            raise VaultError(
                "attention_review_required",
                "需要跟进或暂时观察都必须设置复查时间",
                status_code=422,
            )
        clean_reason = str(reason or "").strip() or None
        if decision == "no_action" and clean_reason is None:
            raise VaultError(
                "attention_reason_required",
                "无需处理必须记录教师原因",
                status_code=422,
            )
        card = self.get(
            token=token,
            attention_card_id=attention_card_id,
        )
        if card["state"] != "draft":
            raise VaultError(
                "attention_not_draft",
                "关注卡已处置或已失效",
                status_code=409,
            )
        if int(card["revision"]) != revision:
            raise VaultError(
                "vault_revision_conflict",
                "关注卡已经变化，请刷新后再操作",
                status_code=409,
            )
        claim = self._claim_resolution(
            attention_card_id=attention_card_id,
            operation_id=operation_id,
            decision=decision,
        )
        action_id = (
            str(claim["target_id"])
            if claim.get("state") == "target_created"
            and claim.get("target_id")
            else None
        )
        if decision in {"follow_up", "observe"} and action_id is None:
            if not plan_id:
                raise VaultError(
                    "attention_plan_required",
                    "创建跟进行动前必须选择工作目标",
                    status_code=422,
                )
            action = self.actions.create_action(
                token=token,
                operation_id=(
                    "attention-target-"
                    + hashlib.sha256(
                        attention_card_id.encode("utf-8")
                    ).hexdigest()[:32]
                ),
                plan_id=plan_id,
                title=(
                    "了解近期情况"
                    if decision == "follow_up"
                    else "复查近期情况"
                ),
                details=(
                    f"{card['observed_fact']}\n"
                    f"核实问题：{card['verification_question']}\n"
                    f"建议：{card['low_risk_next_step']}"
                ),
                due_at=normalized_review,
                depends_on_action_ids=[],
            )
            action_id = str(action["action_id"])
            self._mark_resolution_target(
                attention_card_id=attention_card_id,
                operation_id=operation_id,
                action_id=action_id,
            )
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._row(connection, attention_card_id)
                if str(row["state"]) != "draft":
                    raise VaultError(
                        "attention_not_draft",
                        "关注卡已处置或已失效",
                        status_code=409,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    raise VaultError(
                        "vault_revision_conflict",
                        "关注卡已经变化，请刷新后再操作",
                        status_code=409,
                    )
                payload["teacher_decision_reason"] = clean_reason
                payload["review_at"] = normalized_review
                payload["resolved_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="attention_card",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE attention_cards
                    SET state = 'resolved', decision = ?,
                        action_id = ?, updated_at = ?
                    WHERE attention_card_id = ?
                    """,
                    (decision, action_id, _iso(), attention_card_id),
                )
                result = {
                    "attention_card_id": attention_card_id,
                    "decision": decision,
                    "action_id": action_id,
                    "action_created": action_id is not None,
                }
                self._remember(
                    connection,
                    operation_id,
                    "attention.resolve",
                    result,
                )
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'completed', updated_at = ?
                    WHERE entity_kind = 'attention'
                      AND entity_id = ? AND operation_id = ?
                    """,
                    (_iso(), attention_card_id, operation_id),
                )
        return result

    def _claim_resolution(
        self,
        *,
        attention_card_id: str,
        operation_id: str,
        decision: str,
    ) -> dict[str, object]:
        with closing(self.database.connect()) as connection:
            with connection:
                existing = connection.execute(
                    """
                    SELECT operation_id, state, target_kind, target_id
                    FROM confirmation_claims
                    WHERE entity_kind = 'attention'
                      AND entity_id = ?
                    """,
                    (attention_card_id,),
                ).fetchone()
                if existing is not None:
                    state = str(existing["state"])
                    if state == "completed":
                        raise VaultError(
                            "attention_resolution_claimed",
                            "关注卡已经处置，请刷新后查看",
                            status_code=409,
                        )
                    claimed_decision = str(existing["target_kind"] or "")
                    if claimed_decision and claimed_decision != decision:
                        raise VaultError(
                            "attention_decision_changed",
                            "恢复处置时不能更改原决定，请刷新后按原决定继续",
                            status_code=409,
                        )
                    connection.execute(
                        """
                        UPDATE confirmation_claims
                        SET operation_id = ?, updated_at = ?
                        WHERE entity_kind = 'attention'
                          AND entity_id = ?
                        """,
                        (operation_id, _iso(), attention_card_id),
                    )
                    return {
                        "state": state,
                        "target_id": existing["target_id"],
                    }
                connection.execute(
                    """
                    INSERT INTO confirmation_claims (
                        entity_kind, entity_id, operation_id,
                        state, target_kind, created_at, updated_at
                    ) VALUES ('attention', ?, ?, 'claimed', ?, ?, ?)
                    """,
                    (
                        attention_card_id,
                        operation_id,
                        decision,
                        _iso(),
                        _iso(),
                    ),
                )
        return {"state": "claimed", "target_id": None}

    def _mark_resolution_target(
        self,
        *,
        attention_card_id: str,
        operation_id: str,
        action_id: str,
    ) -> None:
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE confirmation_claims
                    SET state = 'target_created', target_id = ?,
                        updated_at = ?
                    WHERE entity_kind = 'attention'
                      AND entity_id = ? AND operation_id = ?
                    """,
                    (
                        action_id,
                        _iso(),
                        attention_card_id,
                        operation_id,
                    ),
                )

    @staticmethod
    def _row(connection: object, attention_card_id: str) -> object:
        row = connection.execute(
            "SELECT * FROM attention_cards WHERE attention_card_id = ?",
            (attention_card_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "attention_card_not_found",
                "关注卡不存在",
                status_code=404,
            )
        return row

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json FROM idempotency_ledger
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "vault_operation_conflict",
                "此操作编号已经用于另一项操作",
                status_code=409,
            )
        return dict(json.loads(str(row["result_json"])))

    @staticmethod
    def _remember(
        connection: object,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_ledger (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "attention_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean


__all__ = ["AttentionService"]
