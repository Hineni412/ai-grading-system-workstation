from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from .content_policy import RedactionResult, SensitiveContentPolicy
from .crypto import canonical_json
from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .ordinary_database import OrdinaryWorkDatabase
from .secure_repository import EncryptedObjectRepository


_SAFE_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{1,63}")
_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_PREVIEW_SECONDS = 5 * 60


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class ModelDispatchDisabled(RuntimeError):
    pass


class ModelResultUnknown(RuntimeError):
    pass


class ModelDestinationChanged(RuntimeError):
    """The configured provider/endpoint/model no longer matches a preview."""

    pass


class ApprovedModelGateway(Protocol):
    model_name: str

    def is_available(self) -> bool: ...

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "student_support_note",
        data_classification: str = "restricted_anonymized",
        expected_destination_fingerprint: str | None = None,
    ) -> str: ...


def _physical_request_count(
    gateway: object,
    operation_id: str,
    *,
    default: int,
) -> int:
    lookup = getattr(gateway, "physical_request_count", None)
    if callable(lookup):
        try:
            return max(0, int(lookup(operation_id)))
        except (TypeError, ValueError):
            pass
    return max(0, int(default))


class DisabledModelGateway:
    model_name = "未启用真实模型"

    def is_available(self) -> bool:
        return False

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "student_support_note",
        data_classification: str = "restricted_anonymized",
        expected_destination_fingerprint: str | None = None,
    ) -> str:
        _ = (
            payload,
            operation_id,
            purpose,
            data_classification,
            expected_destination_fingerprint,
        )
        raise ModelDispatchDisabled("live model dispatch is disabled")


class FakeApprovedModelGateway:
    model_name = "synthetic-fake-model"

    def __init__(self, *, result: str = "合成模型草稿", unknown: bool = False) -> None:
        self.result = result
        self.unknown = unknown
        self.calls: list[dict[str, object]] = []

    def is_available(self) -> bool:
        return True

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "student_support_note",
        data_classification: str = "restricted_anonymized",
        expected_destination_fingerprint: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "payload": payload,
                "operation_id": operation_id,
                "purpose": purpose,
                "data_classification": data_classification,
                "expected_destination_fingerprint": expected_destination_fingerprint,
            }
        )
        if self.unknown:
            raise ModelResultUnknown("synthetic result unknown")
        return self.result


class ModelApproval:
    """Exact-preview, one-confirmation model interface for restricted text."""

    def __init__(
        self,
        ordinary_database: OrdinaryWorkDatabase,
        sensitive_database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        session_key,
        gateway: ApprovedModelGateway | None = None,
    ) -> None:
        self.ordinary_database = ordinary_database
        self.sensitive_database = sensitive_database
        self.repository = repository
        self.session_key = session_key
        self.gateway = gateway or DisabledModelGateway()

    def prepare(
        self,
        *,
        token: str,
        purpose: str,
        source_text: str,
        context: dict[str, object] | None = None,
        identity_terms: tuple[str, ...] = (),
        redaction: RedactionResult | None = None,
        route_hint: str | None = None,
        output_contract: dict[str, object] | None = None,
        request_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if _SAFE_TOKEN.fullmatch(str(purpose or "")) is None:
            raise VaultError(
                "class_teacher_model_purpose_invalid",
                "模型用途无效",
                status_code=422,
            )
        vmk = self.session_key(token)
        redaction = redaction or SensitiveContentPolicy.prepare_model_text(
            source_text,
            identity_terms=identity_terms,
        )
        if redaction.blocked_categories:
            raise VaultError(
                "class_teacher_model_content_blocked",
                "这段内容含有禁止发送的字段，只能留在本机处理",
                status_code=422,
                details={"categories": list(redaction.blocked_categories)},
            )
        if not redaction.outbound_text:
            raise VaultError(
                "class_teacher_model_text_required",
                "没有可以发送的最小内容",
                status_code=422,
            )
        preview_id = uuid4().hex
        payload_object_id = f"model-preview-{preview_id}"
        expires_at = _now() + timedelta(seconds=_PREVIEW_SECONDS)
        alias_labels = [alias for _term, alias in redaction.identity_aliases]
        exact_payload: dict[str, object] = {
            "purpose": purpose,
            "student_alias": alias_labels[0] if alias_labels else "学生A",
            "task_text": redaction.outbound_text,
            "instructions": (
                "只返回有效的 json 对象，生成待教师复核的中性草稿；"
                "不得诊断、认定、惩戒、外发或结案。"
            ),
        }
        if alias_labels:
            exact_payload["student_aliases"] = alias_labels
        if route_hint is not None:
            exact_payload["route_hint"] = str(route_hint)
        if output_contract is not None:
            exact_payload["output_contract"] = dict(output_contract)
        if request_context:
            exact_payload["context"] = dict(request_context)
        fingerprint = hashlib.sha256(canonical_json(exact_payload)).hexdigest()
        with closing(self.sensitive_database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=payload_object_id,
                    object_type="model_send_preview",
                    payload={
                        "exact_payload": exact_payload,
                        "context": dict(context or {}),
                    },
                )
                subject_id = str((context or {}).get("subject_id") or "")
                if subject_id:
                    timestamp = _iso()
                    connection.execute(
                        """
                        INSERT INTO student_model_artifacts (
                            preview_id, subject_id, preview_payload_object_id,
                            result_payload_object_id, created_at, updated_at
                        ) VALUES (?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            preview_id,
                            subject_id,
                            payload_object_id,
                            timestamp,
                            timestamp,
                        ),
                    )
        self.ordinary_database.initialize_schema() if not self.ordinary_database.exists else None
        timestamp = _iso()
        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO model_approval_operations (
                        preview_id, purpose, classification, payload_object_id,
                        fingerprint, state, removed_categories_json,
                        physical_request_count, expires_at, created_at, updated_at
                    ) VALUES (?, ?, 'restricted', ?, ?, 'previewed', ?, 0, ?, ?, ?)
                    """,
                    (
                        preview_id,
                        purpose,
                        payload_object_id,
                        fingerprint,
                        json.dumps(list(redaction.removed_categories), ensure_ascii=False),
                        _iso(expires_at),
                        timestamp,
                        timestamp,
                    ),
                )
        return {
            "preview_id": preview_id,
            "purpose": purpose,
            "classification": "restricted",
            "exact_payload": exact_payload,
            "removed_categories": list(redaction.removed_categories),
            "fingerprint": fingerprint,
            "expires_at": _iso(expires_at),
            "model_name": self.gateway.model_name,
            "model_enabled": self.gateway.is_available(),
            "max_physical_requests": 1,
            "estimated_cost": None,
        }

    def confirm(
        self,
        *,
        token: str,
        preview_id: str,
        fingerprint: str,
        operation_id: str,
        expected_destination_fingerprint: str | None = None,
    ) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        vmk = self.session_key(token)
        row = self._metadata(preview_id)
        if row["operation_id"] is not None:
            if str(row["operation_id"]) != operation_id:
                raise VaultError(
                    "class_teacher_model_preview_already_confirmed",
                    "这份发送预览已经确认，不能再次调用",
                    status_code=409,
                )
            return self.status(token=token, operation_id=operation_id)
        if str(row["fingerprint"]) != fingerprint:
            raise VaultError(
                "class_teacher_model_preview_changed",
                "发送内容已经变化，请重新预览",
                status_code=409,
            )
        if datetime.fromisoformat(str(row["expires_at"])) <= _now():
            self._set_state(preview_id, "cancelled_before_send", "preview_expired", 0)
            raise VaultError(
                "class_teacher_model_preview_expired",
                "发送预览已过期，请重新生成",
                status_code=409,
            )
        with closing(self.sensitive_database.connect()) as connection:
            protected, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        exact_payload = protected.get("exact_payload")
        if not isinstance(exact_payload, dict):
            raise VaultError(
                "class_teacher_model_preview_invalid",
                "发送预览未通过完整性校验",
                status_code=409,
            )

        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                duplicate = connection.execute(
                    """
                    SELECT preview_id FROM model_approval_operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if duplicate is not None:
                    if str(duplicate["preview_id"]) == preview_id:
                        return self.status(token=token, operation_id=operation_id)
                    raise VaultError(
                        "class_teacher_model_operation_conflict",
                        "同一操作编号不能用于不同的发送预览",
                        status_code=409,
                    )
                changed = connection.execute(
                    """
                    UPDATE model_approval_operations
                    SET operation_id = ?, state = 'claimed', updated_at = ?
                    WHERE preview_id = ? AND state = 'previewed'
                    """,
                    (operation_id, _iso(), preview_id),
                ).rowcount
                if changed != 1:
                    raise VaultError(
                        "class_teacher_model_preview_already_confirmed",
                        "这份发送预览已经确认，不能再次调用",
                        status_code=409,
                    )

        try:
            result_text = self.gateway.invoke(
                payload=exact_payload,
                operation_id=operation_id,
                purpose=str(row["purpose"]),
                data_classification="restricted_anonymized",
                expected_destination_fingerprint=expected_destination_fingerprint,
            )
        except ModelDestinationChanged:
            self._set_state(
                preview_id,
                "failed_before_send",
                "destination_changed",
                0,
            )
            return self.status(token=token, operation_id=operation_id)
        except ModelDispatchDisabled:
            self._set_state(
                preview_id,
                "failed_before_send",
                "model_disabled",
                _physical_request_count(self.gateway, operation_id, default=0),
            )
            return self.status(token=token, operation_id=operation_id)
        except ModelResultUnknown:
            self._set_state(
                preview_id,
                "result_unknown",
                "result_unknown",
                _physical_request_count(self.gateway, operation_id, default=1),
            )
            return self.status(token=token, operation_id=operation_id)
        except Exception:
            self._set_state(
                preview_id,
                "result_unknown",
                "dispatch_error",
                _physical_request_count(self.gateway, operation_id, default=1),
            )
            return self.status(token=token, operation_id=operation_id)

        result_object_id = f"model-result-{preview_id}"
        with closing(self.sensitive_database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=result_object_id,
                    object_type="model_result_draft",
                    payload={"draft_text": str(result_text), "confirmed": False},
                )
                connection.execute(
                    """
                    UPDATE student_model_artifacts
                    SET result_payload_object_id = ?, updated_at = ?
                    WHERE preview_id = ?
                    """,
                    (result_object_id, _iso(), preview_id),
                )
        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE model_approval_operations
                    SET state = 'succeeded', result_object_id = ?,
                        physical_request_count = ?, updated_at = ?
                    WHERE preview_id = ?
                    """,
                    (
                        result_object_id,
                        _physical_request_count(self.gateway, operation_id, default=1),
                        _iso(),
                        preview_id,
                    ),
                )
        return self.status(token=token, operation_id=operation_id)

    def status(self, *, token: str, operation_id: str) -> dict[str, object]:
        self._validate_operation_id(operation_id)
        self.session_key(token)
        row = self._operation(operation_id)
        state = str(row["state"])
        if (
            state == "failed_before_send"
            and row["error_category"] == "destination_changed"
        ):
            state = "destination_changed"
        if state == "claimed":
            # A restarted process cannot prove whether the external provider
            # received the request.  Fail closed and never retry automatically.
            self._set_state(
                str(row["preview_id"]),
                "result_unknown",
                "interrupted_after_claim",
                1,
            )
            row = self._operation(operation_id)
            state = "result_unknown"
        draft_text = None
        response_kind = None
        follow_up_questions: list[str] = []
        proposal = None
        if state == "succeeded" and row["result_object_id"] is not None:
            vmk = self.session_key(token)
            with closing(self.sensitive_database.connect()) as connection:
                result, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["result_object_id"]),
                )
            draft_text = str(result.get("draft_text") or "")
            response_kind, follow_up_questions, proposal = self._interpret_result(
                draft_text
            )
        return {
            "preview_id": str(row["preview_id"]),
            "operation_id": operation_id,
            "state": state,
            "physical_request_count": int(row["physical_request_count"]),
            "error_category": (
                None if row["error_category"] is None else str(row["error_category"])
            ),
            "draft_text": draft_text,
            "response_kind": response_kind,
            "follow_up_questions": follow_up_questions,
            "proposal": proposal,
            "teacher_confirmation_required": state == "succeeded",
        }

    def operation_context(self, *, token: str, operation_id: str) -> dict[str, object]:
        """Return protected orchestration context without resending a request."""

        vmk = self.session_key(token)
        row = self._operation(operation_id)
        with closing(self.sensitive_database.connect()) as connection:
            preview_payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        context = preview_payload.get("context")
        return dict(context) if isinstance(context, dict) else {}

    def result_context(self, *, token: str, operation_id: str) -> dict[str, object]:
        """Return encrypted workflow context only after one successful result."""

        status = self.status(token=token, operation_id=operation_id)
        if status["state"] != "succeeded":
            raise VaultError(
                "class_teacher_model_result_not_confirmable",
                "模型结果尚未成功，不能写入学生卡",
                status_code=409,
            )
        row = self._operation(operation_id)
        vmk = self.session_key(token)
        with closing(self.sensitive_database.connect()) as connection:
            preview_payload, _preview_revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
        context = preview_payload.get("context")
        if not isinstance(context, dict):
            context = {}
        return {
            "preview_id": str(row["preview_id"]),
            "operation_id": operation_id,
            "context": dict(context),
            "draft_text": status["draft_text"],
            "response_kind": status["response_kind"],
            "follow_up_questions": status["follow_up_questions"],
            "proposal": status["proposal"],
        }

    def _metadata(self, preview_id: str):
        if not self.ordinary_database.exists:
            raise VaultError(
                "class_teacher_model_preview_not_found",
                "发送预览不存在",
                status_code=404,
            )
        with closing(self.ordinary_database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM model_approval_operations WHERE preview_id = ?",
                (preview_id,),
            ).fetchone()
        if row is None:
            raise VaultError(
                "class_teacher_model_preview_not_found",
                "发送预览不存在",
                status_code=404,
            )
        return row

    def _operation(self, operation_id: str):
        if not self.ordinary_database.exists:
            row = None
        else:
            with closing(self.ordinary_database.connect()) as connection:
                row = connection.execute(
                    """
                    SELECT * FROM model_approval_operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
        if row is None:
            raise VaultError(
                "class_teacher_model_operation_not_found",
                "模型操作不存在",
                status_code=404,
            )
        return row

    def _set_state(
        self,
        preview_id: str,
        state: str,
        error_category: str | None,
        physical_request_count: int,
    ) -> None:
        with closing(self.ordinary_database.connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE model_approval_operations
                    SET state = ?, error_category = ?, physical_request_count = ?,
                        updated_at = ?
                    WHERE preview_id = ?
                    """,
                    (state, error_category, physical_request_count, _iso(), preview_id),
                )

    @staticmethod
    def _interpret_result(
        draft_text: str,
    ) -> tuple[str, list[str], dict[str, object] | None]:
        try:
            decoded = json.loads(draft_text)
        except (json.JSONDecodeError, TypeError):
            return "proposal", [], {"summary": draft_text}
        if not isinstance(decoded, dict):
            return "proposal", [], {"summary": draft_text}
        kind = str(decoded.get("kind") or "proposal")
        if kind == "follow_up":
            raw_questions = decoded.get("questions")
            questions = (
                [str(item) for item in raw_questions if str(item).strip()]
                if isinstance(raw_questions, list)
                else []
            )
            return "follow_up", questions, None
        raw_proposal = decoded.get("proposal", decoded)
        if not isinstance(raw_proposal, dict):
            raw_proposal = {"summary": str(raw_proposal)}
        return "proposal", [], dict(raw_proposal)

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if _OPERATION_ID.fullmatch(str(operation_id or "")) is None:
            raise VaultError(
                "class_teacher_model_operation_id_invalid",
                "模型操作编号无效",
                status_code=422,
            )


__all__ = [
    "ApprovedModelGateway",
    "DisabledModelGateway",
    "FakeApprovedModelGateway",
    "ModelApproval",
    "ModelDispatchDisabled",
    "ModelDestinationChanged",
    "ModelResultUnknown",
]
