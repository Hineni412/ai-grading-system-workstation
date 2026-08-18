from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository


_STEP_TERMINAL = {"completed", "waived", "superseded"}


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class SopWorkflowService:
    """Versioned, AI-independent SOP engine for synthetic B05 workflows."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider

    def publish_template(
        self,
        *,
        token: str,
        operation_id: str,
        template_key: str,
        version: int,
        title: str,
        steps: list[dict[str, object]],
        workflow_scope: str = "personal_checklist",
        risk_level: str = "ordinary",
        emergency_prompt: str | None = None,
        school_config_gaps: list[str] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.template.publish")
        if replay is not None:
            return self.get_template(
                token=token,
                template_version_id=str(replay["template_version_id"]),
            )
        clean_key = self._code(template_key, "模板代码")
        clean_title = self._text(title, "模板名称", 240)
        if version < 1:
            raise VaultError(
                "sop_template_version_invalid",
                "模板版本必须从 1 开始",
                status_code=422,
            )
        normalized_steps = self._validate_template_steps(steps)
        if workflow_scope not in {"personal_checklist", "school_confirmed"}:
            raise VaultError(
                "sop_workflow_scope_invalid",
                "流程用途范围无效",
                status_code=422,
            )
        if risk_level not in {"ordinary", "elevated", "emergency"}:
            raise VaultError(
                "sop_risk_level_invalid",
                "流程风险等级无效",
                status_code=422,
            )
        template_version_id = uuid4().hex
        object_id = f"sop-template-{template_version_id}"
        timestamp = _iso()
        payload = {
            "template_key": clean_key,
            "version": version,
            "title": clean_title,
            "steps": normalized_steps,
            "workflow_scope": workflow_scope,
            "risk_level": risk_level,
            "emergency_prompt": (
                self._optional_text(
                    emergency_prompt,
                    "紧急提示",
                    2000,
                )
            ),
            "school_config_gaps": [
                self._text(item, "学校配置缺口", 500)
                for item in list(school_config_gaps or [])
            ],
            "model_enabled": False,
            "physical_request_count": 0,
            "frozen": True,
            "created_at": timestamp,
        }
        with closing(self.database.connect()) as connection:
            with connection:
                if connection.execute(
                    """
                    SELECT 1 FROM sop_template_versions
                    WHERE template_key = ? AND version = ?
                    """,
                    (clean_key, version),
                ).fetchone():
                    raise VaultError(
                        "sop_template_version_exists",
                        "此模板版本已经冻结，不能覆盖",
                        status_code=409,
                    )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="sop_template_version",
                    payload=payload,
                )
                connection.execute(
                    """
                    INSERT INTO sop_template_versions (
                        template_version_id, template_key, version,
                        definition_object_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        template_version_id,
                        clean_key,
                        version,
                        object_id,
                        timestamp,
                    ),
                )
                self._remember(
                    connection,
                    operation_id,
                    "sop.template.publish",
                    {"template_version_id": template_version_id},
                )
        return {
            "template_version_id": template_version_id,
            "revision": 1,
            **payload,
        }

    def get_template(
        self,
        *,
        token: str,
        template_version_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT template_version_id, definition_object_id
                FROM sop_template_versions
                WHERE template_version_id = ?
                """,
                (template_version_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "sop_template_not_found",
                    "SOP 模板版本不存在",
                    status_code=404,
                )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["definition_object_id"]),
            )
        return {
            "template_version_id": template_version_id,
            "revision": revision,
            **payload,
        }

    def list_templates(self, *, token: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT template_version_id, definition_object_id
                FROM sop_template_versions
                ORDER BY template_key, version DESC
                """
            ).fetchall()
            items = []
            for row in rows:
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["definition_object_id"]),
                )
                items.append(
                    {
                        "template_version_id": str(row["template_version_id"]),
                        "revision": revision,
                        **payload,
                    }
                )
        return {"items": items}

    def create_affair(
        self,
        *,
        token: str,
        operation_id: str,
        template_version_id: str,
        title: str,
        summary: str | None,
        participant_refs: list[str],
        subject_ids: list[str] | None = None,
        verified_current_subject_ids: list[str] | None = None,
        step_text_overrides: dict[str, dict[str, str]] | None = None,
        idempotency_fingerprint: str | None = None,
        transaction_hook: Callable[[Any, bytes, str, str], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.affair.create")
        if replay is not None:
            if idempotency_fingerprint and replay.get("request_fingerprint") != idempotency_fingerprint:
                raise VaultError(
                    "vault_operation_conflict",
                    "同一方案的保存内容已经变化，请恢复原选择或重新生成方案",
                    status_code=409,
                )
            return self.get_affair(
                token=token,
                affair_id=str(replay["affair_id"]),
            )
        clean_title = self._text(title, "事务名称", 240)
        clean_summary = self._optional_text(summary, "事务说明", 8000)
        participants = [
            self._text(item, "参与引用", 240)
            for item in list(dict.fromkeys(participant_refs))
        ]
        unique_subject_ids = list(dict.fromkeys(str(item) for item in list(subject_ids or [])))
        verified_subject_ids = set(
            str(item) for item in list(verified_current_subject_ids or [])
        )
        if not verified_subject_ids.issubset(set(unique_subject_ids)):
            raise VaultError(
                "sop_verified_subject_invalid",
                "已核对学生引用与本次事务不一致",
                status_code=422,
            )
        if (not participants and not unique_subject_ids) or len(participants) + len(unique_subject_ids) > 50:
            raise VaultError(
                "sop_participants_invalid",
                "事务需要 1 至 50 个匿名参与引用",
                status_code=422,
            )

        affair_id = uuid4().hex
        plan_id = uuid4().hex
        affair_object_id = f"affair-{affair_id}"
        plan_object_id = f"plan-{plan_id}"
        occurrence_id = uuid4().hex
        occurrence_object_id = f"affair-occurrence-{occurrence_id}"
        timestamp = _iso()
        with closing(self.database.connect()) as connection:
            with connection:
                template, _template_revision = self._template_in_connection(
                    connection,
                    vmk,
                    template_version_id,
                )
                if step_text_overrides:
                    revised_steps: list[dict[str, Any]] = []
                    for definition in list(template["steps"]):
                        revised = dict(definition)
                        override = step_text_overrides.get(str(definition["key"]))
                        if override:
                            title_override = str(override.get("title") or "").strip()
                            details_override = str(override.get("details") or "").strip()
                            if title_override:
                                revised["title"] = self._text(
                                    title_override,
                                    "步骤名称",
                                    240,
                                )
                            if details_override:
                                revised["details"] = self._text(
                                    details_override,
                                    "步骤说明",
                                    4000,
                                )
                        revised_steps.append(revised)
                    template = {**template, "steps": revised_steps}
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=plan_object_id,
                    object_type="work_plan",
                    payload={
                        "title": clean_title,
                        "description": "由无 AI SOP 引擎生成",
                        "final_deadline": None,
                    },
                )
                connection.execute(
                    """
                    INSERT INTO work_plans (
                        plan_id, payload_object_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (plan_id, plan_object_id, timestamp, timestamp),
                )
                affair_payload = {
                    "title": clean_title,
                    "summary": clean_summary,
                    "state": "active",
                    "template_key": template["template_key"],
                    "template_version": template["version"],
                    "workflow_scope": template.get(
                        "workflow_scope",
                        "personal_checklist",
                    ),
                    "risk_level": template.get("risk_level", "ordinary"),
                    "emergency_prompt": template.get("emergency_prompt"),
                    "school_config_gaps": list(
                        template.get("school_config_gaps") or []
                    ),
                    "model_enabled": False,
                    "physical_request_count": 0,
                    "current_occurrence_sequence": 1,
                    "closure_summary": None,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=affair_object_id,
                    object_type="affair",
                    payload=affair_payload,
                )
                connection.execute(
                    """
                    INSERT INTO affairs (
                        affair_id, template_version_id, payload_object_id,
                        plan_id, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        affair_id,
                        template_version_id,
                        affair_object_id,
                        plan_id,
                        timestamp,
                        timestamp,
                    ),
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=occurrence_object_id,
                    object_type="affair_occurrence",
                    payload={
                        "sequence": 1,
                        "reason": "initial",
                        "created_at": timestamp,
                    },
                )
                connection.execute(
                    """
                    INSERT INTO affair_occurrences (
                        occurrence_id, affair_id, sequence,
                        payload_object_id, created_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (
                        occurrence_id,
                        affair_id,
                        occurrence_object_id,
                        timestamp,
                    ),
                )
                participant_inputs: list[tuple[str, str | None]] = [
                    (participant, None) for participant in participants
                ]
                for subject_id in unique_subject_ids:
                    if subject_id in verified_subject_ids:
                        subject_row = connection.execute(
                            """
                            SELECT payload_object_id
                            FROM student_subject_links
                            WHERE subject_id=? AND state='active'
                            """,
                            (subject_id,),
                        ).fetchone()
                    else:
                        subject_row = connection.execute(
                            """
                            SELECT s.payload_object_id
                            FROM student_subject_links s
                            JOIN class_roster_memberships m ON m.source_student_key=s.source_fingerprint
                            WHERE s.subject_id=? AND s.state='active' AND m.state='active'
                            """,
                            (subject_id,),
                        ).fetchone()
                    if subject_row is None:
                        raise VaultError(
                            "sop_subject_not_current_roster",
                            "所选学生已不在当前我班学生名单中，请刷新后重试",
                            status_code=409,
                        )
                    identity, _ = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(subject_row["payload_object_id"]),
                    )
                    participant_inputs.append(
                        (str(identity.get("display_name") or "学生"), subject_id)
                    )
                for participant, subject_id in participant_inputs:
                    participant_id = uuid4().hex
                    participant_object_id = f"affair-participant-{participant_id}"
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=participant_object_id,
                        object_type="affair_participant",
                        payload={"reference": participant},
                    )
                    connection.execute(
                        """
                        INSERT INTO affair_participants (
                            participant_id, affair_id,
                            payload_object_id, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            participant_id,
                            affair_id,
                            participant_object_id,
                            timestamp,
                        ),
                    )
                    if subject_id is not None:
                        connection.execute(
                            """
                            INSERT INTO affair_student_links (
                                affair_id, subject_id, participant_id, created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (affair_id, subject_id, participant_id, timestamp),
                        )
                self._instantiate_steps(
                    connection,
                    vmk=vmk,
                    affair_id=affair_id,
                    occurrence_id=occurrence_id,
                    plan_id=plan_id,
                    template=template,
                )
                self._event(connection, affair_id, None, "affair.created")
                self._remember(
                    connection,
                    operation_id,
                    "sop.affair.create",
                    {
                        "affair_id": affair_id,
                        **(
                            {"request_fingerprint": idempotency_fingerprint}
                            if idempotency_fingerprint
                            else {}
                        ),
                    },
                )
                if transaction_hook is not None:
                    transaction_hook(
                        connection,
                        vmk,
                        affair_id,
                        occurrence_id,
                    )
        return self.get_affair(token=token, affair_id=affair_id)

    def get_affair(self, *, token: str, affair_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT affair_id, template_version_id, payload_object_id,
                       plan_id, state, created_at, updated_at, closed_at
                FROM affairs WHERE affair_id = ?
                """,
                (affair_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "sop_affair_not_found",
                    "事务不存在",
                    status_code=404,
                )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            occurrence = connection.execute(
                """
                SELECT occurrence_id, sequence
                FROM affair_occurrences
                WHERE affair_id = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (affair_id,),
            ).fetchone()
            participant_rows = connection.execute(
                """
                SELECT p.participant_id, p.payload_object_id, l.subject_id
                FROM affair_participants p
                LEFT JOIN affair_student_links l ON l.participant_id=p.participant_id
                WHERE p.affair_id = ?
                ORDER BY p.created_at
                """,
                (affair_id,),
            ).fetchall()
            participants = []
            for participant in participant_rows:
                protected, _ = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(participant["payload_object_id"]),
                )
                participants.append(
                    {
                        "participant_id": str(participant["participant_id"]),
                        "reference": protected["reference"],
                        "subject_id": (
                            None
                            if participant["subject_id"] is None
                            else str(participant["subject_id"])
                        ),
                    }
                )
            steps = self._steps_for_occurrence(
                connection,
                vmk,
                str(occurrence["occurrence_id"]),
            )
            decisions = self._decisions(connection, vmk, affair_id)
        return {
            "affair_id": affair_id,
            "revision": revision,
            "template_version_id": str(row["template_version_id"]),
            "plan_id": str(row["plan_id"]),
            **payload,
            "occurrence_id": str(occurrence["occurrence_id"]),
            "occurrence_sequence": int(occurrence["sequence"]),
            "participants": participants,
            "current_steps": [
                item
                for item in steps
                if item["state"] in {"ready", "in_progress", "waiting"}
            ],
            "completed_steps": [
                item for item in steps if item["state"] in _STEP_TERMINAL
            ],
            "preview_steps": [
                item for item in steps if item["state"] == "blocked"
            ],
            "decisions": decisions,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "closed_at": (
                str(row["closed_at"]) if row["closed_at"] is not None else None
            ),
        }

    def list_affairs(self, *, token: str) -> dict[str, object]:
        self._key_provider(token)
        with closing(self.database.connect()) as connection:
            affair_ids = [
                str(row["affair_id"])
                for row in connection.execute(
                    """
                    SELECT affair_id FROM affairs
                    ORDER BY updated_at DESC
                    """
                ).fetchall()
            ]
        return {
            "items": [
                self.get_affair(token=token, affair_id=affair_id)
                for affair_id in affair_ids
            ]
        }

    def complete_step(
        self,
        *,
        token: str,
        affair_id: str,
        step_instance_id: str,
        operation_id: str,
        revision: int,
        outcome: str,
        result: str,
        transaction_hook: Callable[[Any, bytes], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.step.complete")
        if replay is not None:
            return self.get_affair(token=token, affair_id=affair_id)
        if outcome not in {"completed", "waived"}:
            raise VaultError(
                "sop_step_outcome_invalid",
                "步骤结果只能是完成或豁免",
                status_code=422,
            )
        clean_result = self._text(result, "处理结果", 8000)
        with closing(self.database.connect()) as connection:
            with connection:
                affair_row = self._active_affair(connection, affair_id)
                step_row = connection.execute(
                    """
                    SELECT * FROM step_instances
                    WHERE step_instance_id = ? AND affair_id = ?
                    """,
                    (step_instance_id, affair_id),
                ).fetchone()
                if step_row is None:
                    raise VaultError(
                        "sop_step_not_found",
                        "事务步骤不存在",
                        status_code=404,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(step_row["payload_object_id"]),
                )
                if current_revision != revision:
                    self._revision_conflict("步骤")
                if str(step_row["state"]) not in {"ready", "in_progress", "waiting"}:
                    raise VaultError(
                        "sop_step_not_actionable",
                        "当前步骤不能再次完成",
                        status_code=409,
                    )
                if outcome == "waived" and (
                    bool(step_row["is_safety_required"])
                    or not bool(payload.get("waivable"))
                ):
                    raise VaultError(
                        "sop_safety_step_cannot_be_waived",
                        "此步骤是不可跳过的必做步骤",
                        status_code=422,
                    )
                timestamp = _iso()
                payload.update(
                    {
                        "state": outcome,
                        "result": clean_result,
                        "completed_at": timestamp,
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(step_row["payload_object_id"]),
                    object_type="affair_step",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE step_instances
                    SET state = ?, updated_at = ?, completed_at = ?
                    WHERE step_instance_id = ?
                    """,
                    (outcome, timestamp, timestamp, step_instance_id),
                )
                action_id = str(step_row["action_id"] or "")
                if action_id:
                    self._finish_action(
                        connection,
                        vmk=vmk,
                        action_id=action_id,
                        outcome=outcome,
                        result=clean_result,
                    )
                self._event(
                    connection,
                    affair_id,
                    step_instance_id,
                    f"step.{outcome}",
                )
                self._activate_ready(
                    connection,
                    vmk=vmk,
                    affair_id=affair_id,
                    occurrence_id=str(step_row["occurrence_id"]),
                    plan_id=str(affair_row["plan_id"]),
                    template_version_id=str(
                        affair_row["template_version_id"]
                    ),
                )
                connection.execute(
                    "UPDATE affairs SET updated_at = ? WHERE affair_id = ?",
                    (timestamp, affair_id),
                )
                self._remember(
                    connection,
                    operation_id,
                    "sop.step.complete",
                    {
                        "affair_id": affair_id,
                        "step_instance_id": step_instance_id,
                    },
                )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk)
        return self.get_affair(token=token, affair_id=affair_id)

    def record_decision(
        self,
        *,
        token: str,
        affair_id: str,
        operation_id: str,
        decision_kind: str,
        summary: str,
        step_instance_id: str | None,
        decision_key: str | None = None,
        selected_option: str | None = None,
        expected_workspace_revision: int | None = None,
        transaction_hook: Callable[[Any, bytes], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.decision.record")
        if replay is not None:
            return self.get_affair(token=token, affair_id=affair_id)
        if decision_kind not in {"teacher", "school", "ai_suggestion"}:
            raise VaultError(
                "sop_decision_kind_invalid",
                "决定来源无效",
                status_code=422,
            )
        clean_summary = self._text(summary, "决定内容", 8000)
        clean_decision_key = (
            self._code(decision_key, "决定键") if decision_key else None
        )
        clean_selected_option = (
            self._code(selected_option, "决定选项")
            if selected_option
            else None
        )
        if bool(clean_decision_key) != bool(clean_selected_option):
            raise VaultError(
                "sop_decision_option_incomplete",
                "结构化决定需要同时提供决定键和选项",
                status_code=422,
            )
        with closing(self.database.connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if expected_workspace_revision is not None:
                    current_workspace_revision = int(connection.execute(
                        "SELECT COUNT(*) FROM affair_events WHERE affair_id = ?",
                        (affair_id,),
                    ).fetchone()[0])
                    if current_workspace_revision != int(expected_workspace_revision):
                        raise VaultError(
                            "sop_affair_revision_conflict",
                            "事务已经变化，请刷新后再继续",
                            status_code=409,
                        )
                self._affair(connection, affair_id)
                if step_instance_id and connection.execute(
                    """
                    SELECT 1 FROM step_instances
                    WHERE step_instance_id = ? AND affair_id = ?
                    """,
                    (step_instance_id, affair_id),
                ).fetchone() is None:
                    raise VaultError(
                        "sop_step_not_found",
                        "事务步骤不存在",
                        status_code=404,
                    )
                decision_id = uuid4().hex
                object_id = f"affair-decision-{decision_id}"
                timestamp = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=object_id,
                    object_type="affair_decision",
                    payload={
                        "summary": clean_summary,
                        "decision_key": clean_decision_key,
                        "selected_option": clean_selected_option,
                        "can_drive_high_impact_branch": (
                            decision_kind in {"teacher", "school"}
                        ),
                    },
                )
                connection.execute(
                    """
                    INSERT INTO decision_records (
                        decision_id, affair_id, step_instance_id,
                        decision_kind, payload_object_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        affair_id,
                        step_instance_id,
                        decision_kind,
                        object_id,
                        timestamp,
                    ),
                )
                self._event(
                    connection,
                    affair_id,
                    step_instance_id,
                    f"decision.{decision_kind}",
                )
                if (
                    decision_kind in {"teacher", "school"}
                    and clean_decision_key
                ):
                    affair_row = self._active_affair(
                        connection,
                        affair_id,
                    )
                    occurrence = connection.execute(
                        """
                        SELECT occurrence_id FROM affair_occurrences
                        WHERE affair_id = ?
                        ORDER BY sequence DESC LIMIT 1
                        """,
                        (affair_id,),
                    ).fetchone()
                    self._activate_ready(
                        connection,
                        vmk=vmk,
                        affair_id=affair_id,
                        occurrence_id=str(occurrence["occurrence_id"]),
                        plan_id=str(affair_row["plan_id"]),
                        template_version_id=str(
                            affair_row["template_version_id"]
                        ),
                    )
                self._remember(
                    connection,
                    operation_id,
                    "sop.decision.record",
                    {"affair_id": affair_id, "decision_id": decision_id},
                )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk)
        return self.get_affair(token=token, affair_id=affair_id)

    def close_affair(
        self,
        *,
        token: str,
        affair_id: str,
        operation_id: str,
        revision: int,
        closure_summary: str,
        transaction_hook: Callable[[Any, bytes], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.affair.close")
        if replay is not None:
            return self.get_affair(token=token, affair_id=affair_id)
        clean_summary = self._text(closure_summary, "结案说明", 8000)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._active_affair(connection, affair_id)
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    self._revision_conflict("事务")
                occurrence = connection.execute(
                    """
                    SELECT occurrence_id FROM affair_occurrences
                    WHERE affair_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (affair_id,),
                ).fetchone()
                steps = connection.execute(
                    """
                    SELECT state, is_required, is_safety_required
                    FROM step_instances WHERE occurrence_id = ?
                    """,
                    (str(occurrence["occurrence_id"]),),
                ).fetchall()
                blockers = [
                    step for step in steps
                    if (
                        bool(step["is_safety_required"])
                        and str(step["state"]) not in {
                            "completed",
                            "superseded",
                        }
                    )
                    or (
                        bool(step["is_required"])
                        and str(step["state"]) not in {
                            "completed",
                            "waived",
                            "superseded",
                        }
                    )
                ]
                if blockers:
                    raise VaultError(
                        "sop_required_steps_incomplete",
                        "仍有必做步骤未完成，不能结案",
                        status_code=422,
                    )
                timestamp = _iso()
                self._supersede_optional_steps(
                    connection,
                    vmk,
                    str(occurrence["occurrence_id"]),
                )
                payload.update(
                    {
                        "state": "closed",
                        "closure_summary": clean_summary,
                        "updated_at": timestamp,
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="affair",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE affairs
                    SET state = 'closed', updated_at = ?, closed_at = ?
                    WHERE affair_id = ?
                    """,
                    (timestamp, timestamp, affair_id),
                )
                self._event(connection, affair_id, None, "affair.closed")
                self._remember(
                    connection,
                    operation_id,
                    "sop.affair.close",
                    {"affair_id": affair_id},
                )
                if transaction_hook is not None:
                    transaction_hook(
                        connection,
                        vmk,
                        affair_id,
                        occurrence_id,
                    )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk)
        return self.get_affair(token=token, affair_id=affair_id)

    def reopen_affair(
        self,
        *,
        token: str,
        affair_id: str,
        operation_id: str,
        revision: int,
        reason: str,
        transaction_hook: Callable[[Any, bytes], None] | None = None,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.affair.reopen")
        if replay is not None:
            return self.get_affair(token=token, affair_id=affair_id)
        clean_reason = self._text(reason, "重开原因", 4000)
        with closing(self.database.connect()) as connection:
            with connection:
                row = self._affair(connection, affair_id)
                if str(row["state"]) != "closed":
                    raise VaultError(
                        "sop_affair_not_closed",
                        "只有已结案事务可以重开",
                        status_code=409,
                    )
                payload, current_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                if current_revision != revision:
                    self._revision_conflict("事务")
                latest = connection.execute(
                    """
                    SELECT MAX(sequence) FROM affair_occurrences
                    WHERE affair_id = ?
                    """,
                    (affair_id,),
                ).fetchone()[0]
                sequence = int(latest) + 1
                occurrence_id = uuid4().hex
                occurrence_object_id = f"affair-occurrence-{occurrence_id}"
                timestamp = _iso()
                template, _ = self._template_in_connection(
                    connection,
                    vmk,
                    str(row["template_version_id"]),
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=occurrence_object_id,
                    object_type="affair_occurrence",
                    payload={
                        "sequence": sequence,
                        "reason": clean_reason,
                        "created_at": timestamp,
                    },
                )
                connection.execute(
                    """
                    INSERT INTO affair_occurrences (
                        occurrence_id, affair_id, sequence,
                        payload_object_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        occurrence_id,
                        affair_id,
                        sequence,
                        occurrence_object_id,
                        timestamp,
                    ),
                )
                self._instantiate_steps(
                    connection,
                    vmk=vmk,
                    affair_id=affair_id,
                    occurrence_id=occurrence_id,
                    plan_id=str(row["plan_id"]),
                    template=template,
                )
                payload.update(
                    {
                        "state": "active",
                        "current_occurrence_sequence": sequence,
                        "closure_summary": None,
                        "updated_at": timestamp,
                    }
                )
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="affair",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE affairs
                    SET state = 'active', updated_at = ?, closed_at = NULL
                    WHERE affair_id = ?
                    """,
                    (timestamp, affair_id),
                )
                self._event(connection, affair_id, None, "affair.reopened")
                self._remember(
                    connection,
                    operation_id,
                    "sop.affair.reopen",
                    {"affair_id": affair_id, "occurrence_id": occurrence_id},
                )
                if transaction_hook is not None:
                    transaction_hook(connection, vmk)
        return self.get_affair(token=token, affair_id=affair_id)

    def sop_snapshot_for_model(self, *, token: str, affair_id: str) -> dict[str, object]:
        """事务当前状态的安全快照，只用于发送给已配置模型的流程修订请求。"""
        affair = self.get_affair(token=token, affair_id=affair_id)
        steps = [
            *list(affair.get("current_steps") or []),
            *list(affair.get("completed_steps") or []),
            *list(affair.get("preview_steps") or []),
        ]
        return {
            "title": str(affair.get("title") or ""),
            "summary": str(affair.get("summary") or ""),
            "template_key": str(affair.get("template_key") or ""),
            "state": str(affair.get("state") or ""),
            "steps": [
                {
                    "key": str(step.get("key") or ""),
                    "title": str(step.get("title") or ""),
                    "details": str(step.get("details") or ""),
                    "state": str(step.get("state") or ""),
                    "safety_required": bool(step.get("safety_required")),
                    "is_decision_point": bool(step.get("decision_key")),
                    "depends_on": [
                        str(item) for item in list(step.get("depends_on") or [])
                    ],
                    "result": (
                        None
                        if step.get("result") is None
                        else str(step.get("result"))[:2000]
                    ),
                }
                for step in steps
            ],
            "decisions": [
                {
                    "decision_key": str(item.get("decision_key") or ""),
                    "selected_option": str(item.get("selected_option") or ""),
                }
                for item in list(affair.get("decisions") or [])
                if isinstance(item, dict)
            ],
        }

    @staticmethod
    def _workspace_revision(connection: Any, affair_id: str) -> int:
        count = connection.execute(
            "SELECT COUNT(*) FROM affair_events WHERE affair_id = ?",
            (affair_id,),
        ).fetchone()[0]
        return max(1, int(count))

    def create_sync_request(
        self,
        *,
        token: str,
        affair_id: str,
        expected_revision: int,
        text: str,
        operation_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.affair.sync_update")
        if replay is not None:
            return dict(replay)
        clean_text = self._text(text, "同步内容", 2000)
        with closing(self.database.connect()) as connection:
            with connection:
                affair_row = self._active_affair(connection, affair_id)
                if self._workspace_revision(connection, affair_id) != int(expected_revision):
                    self._revision_conflict("事务")
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                )
                sync_id = uuid4().hex
                requests = list(payload.get("sync_requests") or [])
                requests.append({
                    "sync_id": sync_id,
                    "text": clean_text,
                    "state": "queued",
                    "created_at": _iso(),
                })
                payload["sync_requests"] = requests[-20:]
                payload["updated_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                    object_type="affair",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    "UPDATE affairs SET updated_at = ? WHERE affair_id = ?",
                    (_iso(), affair_id),
                )
                self._event(connection, affair_id, None, "affair.sync_requested")
                result = {
                    "sync_id": sync_id,
                    "affair_revision": self._workspace_revision(connection, affair_id),
                }
                self._remember(
                    connection,
                    operation_id,
                    "sop.affair.sync_update",
                    result,
                )
        return result

    def sync_request_text(
        self,
        *,
        token: str,
        affair_id: str,
        sync_id: str,
    ) -> str:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            affair_row = self._active_affair(connection, affair_id)
            payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(affair_row["payload_object_id"]),
            )
        for item in list(payload.get("sync_requests") or []):
            if str(item.get("sync_id") or "") == sync_id:
                return str(item.get("text") or "")
        raise VaultError("sop_sync_not_found", "同步请求不存在", status_code=404)

    def persist_flow_revision(
        self,
        *,
        token: str,
        affair_id: str,
        sync_id: str,
        assistant_message: str,
        items: list[dict[str, object]],
    ) -> dict[str, object]:
        """把模型流程修订作为待审草稿写入事务；安全过滤在这里强制执行。"""
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            with connection:
                affair_row = self._active_affair(connection, affair_id)
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                )
                revisions = list(payload.get("flow_revisions") or [])
                existing = next(
                    (item for item in revisions if str(item.get("sync_id") or "") == sync_id),
                    None,
                )
                if existing is not None:
                    return dict(existing)
                sync_requests = list(payload.get("sync_requests") or [])
                sync = next(
                    (item for item in sync_requests if str(item.get("sync_id") or "") == sync_id),
                    None,
                )
                if sync is None or str(sync.get("state") or "") != "queued":
                    raise VaultError(
                        "sop_sync_not_found",
                        "同步请求不存在或已处理",
                        status_code=404,
                    )
                occurrence = connection.execute(
                    """
                    SELECT occurrence_id FROM affair_occurrences
                    WHERE affair_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (affair_id,),
                ).fetchone()
                steps = self._steps_for_occurrence(
                    connection,
                    vmk,
                    str(occurrence["occurrence_id"]),
                )
                by_key = {str(step.get("key") or ""): step for step in steps}
                sanitized: list[dict[str, object]] = []
                dropped: list[dict[str, str]] = []
                new_keys: set[str] = set()
                for item in items:
                    kind = str(item.get("kind") or "")
                    item_id = str(item.get("item_id") or "")
                    if kind == "note":
                        text = str(item.get("text") or "").strip()
                        if not text:
                            dropped.append({"item_id": item_id, "reason": "空核对建议"})
                            continue
                        sanitized.append({
                            "item_id": item_id,
                            "kind": "note",
                            "text": text[:2000],
                            "reason": str(item.get("reason") or "")[:800],
                            "state": "pending",
                        })
                        continue
                    if kind == "add_step":
                        title = str(item.get("title") or "").strip()
                        if not title:
                            dropped.append({"item_id": item_id, "reason": "新步骤缺少名称"})
                            continue
                        depends_on = [
                            str(dependency) for dependency in list(item.get("depends_on") or [])
                        ]
                        unknown = [
                            dependency
                            for dependency in depends_on
                            if dependency not in by_key and dependency not in new_keys
                        ]
                        if unknown:
                            dropped.append({
                                "item_id": item_id,
                                "reason": f"依赖了不存在的步骤：{'、'.join(unknown)}",
                            })
                            continue
                        step_key = f"ai-{item_id}"
                        if step_key in by_key or step_key in new_keys:
                            dropped.append({"item_id": item_id, "reason": "步骤编号冲突"})
                            continue
                        new_keys.add(step_key)
                        sanitized.append({
                            "item_id": item_id,
                            "kind": "add_step",
                            "step_key": step_key,
                            "title": title[:240],
                            "details": str(item.get("details") or "")[:4000],
                            "depends_on": depends_on,
                            "reason": str(item.get("reason") or "")[:800],
                            "state": "pending",
                        })
                        continue
                    if kind == "revise_step":
                        target_key = str(item.get("target_step_key") or "")
                        target = by_key.get(target_key)
                        if target is None:
                            dropped.append({"item_id": item_id, "reason": "目标步骤不存在"})
                            continue
                        if (
                            str(target.get("state") or "") in _STEP_TERMINAL
                            or str(target.get("state") or "") == "in_progress"
                        ):
                            dropped.append({"item_id": item_id, "reason": "目标步骤已开始或已结束"})
                            continue
                        if bool(target.get("safety_required")) or target.get("decision_key"):
                            dropped.append({
                                "item_id": item_id,
                                "reason": "安全必做步骤和教师分流步骤不能由 AI 修改",
                            })
                            continue
                        title = str(item.get("title") or "").strip()
                        details = str(item.get("details") or "").strip()
                        if not title and not details:
                            dropped.append({"item_id": item_id, "reason": "没有修改内容"})
                            continue
                        sanitized.append({
                            "item_id": item_id,
                            "kind": "revise_step",
                            "target_step_key": target_key,
                            "title": title[:240],
                            "details": details[:4000],
                            "reason": str(item.get("reason") or "")[:800],
                            "state": "pending",
                        })
                        continue
                    dropped.append({"item_id": item_id, "reason": "未知的修订类型"})
                entry = {
                    "revision_id": uuid4().hex,
                    "sync_id": sync_id,
                    "source_text": str(sync.get("text") or ""),
                    "assistant_message": str(assistant_message or "")[:2000],
                    "items": sanitized,
                    "dropped_items": dropped,
                    "state": "pending_review",
                    "created_at": _iso(),
                    "decided_at": None,
                    "accepted_item_ids": [],
                }
                revisions.append(entry)
                payload["flow_revisions"] = revisions[-10:]
                sync["state"] = "answered"
                payload["sync_requests"] = sync_requests
                payload["updated_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                    object_type="affair",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    "UPDATE affairs SET updated_at = ? WHERE affair_id = ?",
                    (_iso(), affair_id),
                )
                self._event(connection, affair_id, None, "affair.flow_revision_persisted")
                return dict(entry)

    def flow_revision_for_sync(
        self,
        *,
        token: str,
        affair_id: str,
        sync_id: str,
    ) -> dict[str, object] | None:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            affair_row = connection.execute(
                "SELECT payload_object_id FROM affairs WHERE affair_id = ?",
                (affair_id,),
            ).fetchone()
            if affair_row is None:
                return None
            payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(affair_row["payload_object_id"]),
            )
        for item in list(payload.get("flow_revisions") or []):
            if str(item.get("sync_id") or "") == sync_id:
                return dict(item)
        return None

    def mark_sync_request_failed(
        self,
        *,
        token: str,
        affair_id: str,
        sync_id: str,
        outcome: str,
    ) -> None:
        """模型失败或结果无效时，把同步请求标记为失败，保留原文供再次同步。"""
        if outcome not in {"failed", "invalid_result"}:
            raise ValueError("sync outcome is invalid")
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            with connection:
                affair_row = connection.execute(
                    "SELECT payload_object_id FROM affairs WHERE affair_id = ?",
                    (affair_id,),
                ).fetchone()
                if affair_row is None:
                    return
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                )
                sync_requests = list(payload.get("sync_requests") or [])
                sync = next(
                    (item for item in sync_requests if str(item.get("sync_id") or "") == sync_id),
                    None,
                )
                if sync is None or str(sync.get("state") or "") != "queued":
                    return
                sync["state"] = outcome
                payload["sync_requests"] = sync_requests
                payload["updated_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                    object_type="affair",
                    payload=payload,
                    expected_revision=revision,
                )

    def decide_flow_revision(
        self,
        *,
        token: str,
        affair_id: str,
        revision_id: str,
        accepted_item_ids: list[str],
        expected_revision: int,
        operation_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "sop.affair.flow_revision.decide")
        if replay is not None:
            return self.get_affair(token=token, affair_id=affair_id)
        accepted = list(dict.fromkeys(
            self._text(item, "条目编号", 64) for item in list(accepted_item_ids)[:20]
        ))
        with closing(self.database.connect()) as connection:
            with connection:
                affair_row = self._active_affair(connection, affair_id)
                if self._workspace_revision(connection, affair_id) != int(expected_revision):
                    self._revision_conflict("事务")
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair_row["payload_object_id"]),
                )
                revisions = list(payload.get("flow_revisions") or [])
                entry = next(
                    (item for item in revisions if str(item.get("revision_id") or "") == revision_id),
                    None,
                )
                if entry is None:
                    raise VaultError(
                        "sop_flow_revision_not_found",
                        "流程修订草稿不存在",
                        status_code=404,
                    )
                already_decided = str(entry.get("state") or "") != "pending_review"
                if not already_decided:
                    occurrence = connection.execute(
                        """
                        SELECT occurrence_id FROM affair_occurrences
                        WHERE affair_id = ? ORDER BY sequence DESC LIMIT 1
                        """,
                        (affair_id,),
                    ).fetchone()
                    occurrence_id = str(occurrence["occurrence_id"])
                    step_rows = connection.execute(
                        """
                        SELECT * FROM step_instances WHERE occurrence_id = ?
                        """,
                        (occurrence_id,),
                    ).fetchall()
                    row_by_key = {
                        str(row["template_step_key"]): row for row in step_rows
                    }
                    timestamp = _iso()
                    for item in list(entry.get("items") or []):
                        if str(item.get("item_id") or "") not in accepted:
                            item["state"] = "discarded"
                            continue
                        kind = str(item.get("kind") or "")
                        if kind == "note":
                            item["state"] = "applied"
                            continue
                        if kind == "add_step":
                            depends_on = [
                                str(dependency) for dependency in list(item.get("depends_on") or [])
                            ]
                            dependency_rows = [row_by_key.get(key) for key in depends_on]
                            if any(row is None for row in dependency_rows):
                                item["state"] = "discarded"
                                continue
                            ready = all(
                                str(row["state"]) in _STEP_TERMINAL for row in dependency_rows
                            )
                            step_id = uuid4().hex
                            object_id = f"affair-step-{step_id}"
                            definition = {
                                "key": str(item.get("step_key") or ""),
                                "title": str(item.get("title") or ""),
                                "details": str(item.get("details") or ""),
                                "required": False,
                                "waivable": True,
                                "safety_required": False,
                                "depends_on": depends_on,
                                "activation": None,
                                "decision_key": None,
                                "decision_prompt": None,
                                "decision_options": [],
                                "origin": "ai_flow_revision",
                            }
                            action_id = None
                            if ready:
                                action_id = self._create_step_action(
                                    connection,
                                    vmk=vmk,
                                    plan_id=str(affair_row["plan_id"]),
                                    definition=definition,
                                    dependency_action_ids=[
                                        str(row["action_id"])
                                        for row in dependency_rows
                                        if row is not None and row["action_id"]
                                    ],
                                )
                            self.repository.put(
                                connection,
                                vmk=vmk,
                                object_id=object_id,
                                object_type="affair_step",
                                payload={
                                    **definition,
                                    "state": "ready" if ready else "blocked",
                                    "result": None,
                                    "completed_at": None,
                                },
                            )
                            connection.execute(
                                """
                                INSERT INTO step_instances (
                                    step_instance_id, affair_id, occurrence_id,
                                    template_step_key, action_id, payload_object_id,
                                    state, is_required, is_safety_required,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                                """,
                                (
                                    step_id,
                                    affair_id,
                                    occurrence_id,
                                    str(item.get("step_key") or ""),
                                    action_id,
                                    object_id,
                                    "ready" if ready else "blocked",
                                    timestamp,
                                    timestamp,
                                ),
                            )
                            self._event(
                                connection,
                                affair_id,
                                step_id,
                                "step.created_from_revision",
                            )
                            item["state"] = "applied"
                            continue
                        if kind == "revise_step":
                            target_key = str(item.get("target_step_key") or "")
                            target_row = row_by_key.get(target_key)
                            if target_row is None:
                                item["state"] = "discarded"
                                continue
                            target_payload, target_revision = self.repository.get(
                                connection,
                                vmk=vmk,
                                object_id=str(target_row["payload_object_id"]),
                            )
                            if (
                                str(target_row["state"]) in _STEP_TERMINAL
                                or str(target_row["state"]) == "in_progress"
                                or bool(target_row["is_safety_required"])
                                or target_payload.get("decision_key")
                            ):
                                item["state"] = "discarded"
                                continue
                            if str(item.get("title") or "").strip():
                                target_payload["title"] = str(item["title"]).strip()
                            if str(item.get("details") or "").strip():
                                target_payload["details"] = str(item["details"]).strip()
                            self.repository.put(
                                connection,
                                vmk=vmk,
                                object_id=str(target_row["payload_object_id"]),
                                object_type="affair_step",
                                payload=target_payload,
                                expected_revision=target_revision,
                            )
                            action_id = str(target_row["action_id"] or "")
                            if action_id:
                                action_row = connection.execute(
                                    "SELECT payload_object_id FROM actions WHERE action_id = ?",
                                    (action_id,),
                                ).fetchone()
                                if action_row is not None:
                                    action_payload, action_revision = self.repository.get(
                                        connection,
                                        vmk=vmk,
                                        object_id=str(action_row["payload_object_id"]),
                                    )
                                    action_payload["title"] = target_payload["title"]
                                    action_payload["details"] = target_payload.get("details")
                                    self.repository.put(
                                        connection,
                                        vmk=vmk,
                                        object_id=str(action_row["payload_object_id"]),
                                        object_type="action_item",
                                        payload=action_payload,
                                        expected_revision=action_revision,
                                    )
                            self._event(
                                connection,
                                affair_id,
                                str(target_row["step_instance_id"]),
                                "step.revised_from_revision",
                            )
                            item["state"] = "applied"
                            continue
                        item["state"] = "discarded"
                    entry["state"] = "applied" if accepted else "discarded"
                    entry["accepted_item_ids"] = accepted
                    entry["decided_at"] = timestamp
                    payload["flow_revisions"] = revisions
                    payload["updated_at"] = timestamp
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=str(affair_row["payload_object_id"]),
                        object_type="affair",
                        payload=payload,
                        expected_revision=revision,
                    )
                    connection.execute(
                        "UPDATE affairs SET updated_at = ? WHERE affair_id = ?",
                        (timestamp, affair_id),
                    )
                    self._event(connection, affair_id, None, "affair.flow_revision_decided")
                self._remember(
                    connection,
                    operation_id,
                    "sop.affair.flow_revision.decide",
                    {"affair_id": affair_id, "revision_id": revision_id},
                )
        return self.get_affair(token=token, affair_id=affair_id)

    def _instantiate_steps(
        self,
        connection: Any,
        *,
        vmk: bytes,
        affair_id: str,
        occurrence_id: str,
        plan_id: str,
        template: dict[str, Any],
    ) -> None:
        timestamp = _iso()
        for definition in list(template["steps"]):
            step_id = uuid4().hex
            object_id = f"affair-step-{step_id}"
            payload = {
                **dict(definition),
                "state": "blocked",
                "result": None,
                "completed_at": None,
            }
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=object_id,
                object_type="affair_step",
                payload=payload,
            )
            connection.execute(
                """
                INSERT INTO step_instances (
                    step_instance_id, affair_id, occurrence_id,
                    template_step_key, action_id, payload_object_id,
                    state, is_required, is_safety_required,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, ?, 'blocked', ?, ?, ?, ?)
                """,
                (
                    step_id,
                    affair_id,
                    occurrence_id,
                    str(definition["key"]),
                    object_id,
                    1 if definition["required"] else 0,
                    1 if definition["safety_required"] else 0,
                    timestamp,
                    timestamp,
                ),
            )
        self._activate_ready(
            connection,
            vmk=vmk,
            affair_id=affair_id,
            occurrence_id=occurrence_id,
            plan_id=plan_id,
            template_version_id=None,
            template=template,
        )

    def _activate_ready(
        self,
        connection: Any,
        *,
        vmk: bytes,
        affair_id: str,
        occurrence_id: str,
        plan_id: str,
        template_version_id: str | None,
        template: dict[str, Any] | None = None,
    ) -> None:
        if template is None:
            self._template_in_connection(
                connection,
                vmk,
                str(template_version_id),
            )
        rows = connection.execute(
            """
            SELECT * FROM step_instances WHERE occurrence_id = ?
            """,
            (occurrence_id,),
        ).fetchall()
        by_key = {str(row["template_step_key"]): row for row in rows}
        for key, row in by_key.items():
            if str(row["state"]) != "blocked":
                continue
            # 步骤自身保存的定义（含流程修订后的最新文案；AI 新增步骤不在模板中）
            definition, _definition_revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            activation = definition.get("activation")
            if activation:
                selected = self._decision_value(
                    connection,
                    vmk,
                    affair_id,
                    str(activation["decision_key"]),
                )
                if selected is None:
                    continue
                if selected not in set(activation["allowed_values"]):
                    payload, revision = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(row["payload_object_id"]),
                    )
                    payload["state"] = "superseded"
                    payload["result"] = "未命中教师/学校确认的分支"
                    self.repository.put(
                        connection,
                        vmk=vmk,
                        object_id=str(row["payload_object_id"]),
                        object_type="affair_step",
                        payload=payload,
                        expected_revision=revision,
                    )
                    connection.execute(
                        """
                        UPDATE step_instances
                        SET state = 'superseded', updated_at = ?
                        WHERE step_instance_id = ?
                        """,
                        (_iso(), str(row["step_instance_id"])),
                    )
                    self._event(
                        connection,
                        affair_id,
                        str(row["step_instance_id"]),
                        "step.superseded_by_decision",
                    )
                    continue
            dependencies = list(definition.get("depends_on") or [])
            if not all(
                str(by_key[dependency]["state"]) in _STEP_TERMINAL
                for dependency in dependencies
            ):
                continue
            action_id = self._create_step_action(
                connection,
                vmk=vmk,
                plan_id=plan_id,
                definition=definition,
                dependency_action_ids=[
                    str(by_key[dependency]["action_id"])
                    for dependency in dependencies
                    if by_key[dependency]["action_id"]
                ],
            )
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            payload["state"] = "ready"
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
                object_type="affair_step",
                payload=payload,
                expected_revision=revision,
            )
            connection.execute(
                """
                UPDATE step_instances
                SET state = 'ready', action_id = ?, updated_at = ?
                WHERE step_instance_id = ?
                """,
                (action_id, _iso(), str(row["step_instance_id"])),
            )
            self._event(
                connection,
                affair_id,
                str(row["step_instance_id"]),
                "step.ready",
            )

    def _create_step_action(
        self,
        connection: Any,
        *,
        vmk: bytes,
        plan_id: str,
        definition: dict[str, Any],
        dependency_action_ids: list[str],
    ) -> str:
        action_id = uuid4().hex
        object_id = f"action-{action_id}"
        timestamp = _iso()
        payload = {
            "title": definition["title"],
            "details": definition.get("details"),
            "status": "pending",
            "due_at": None,
            "waiting_for_kind": None,
            "review_at": None,
            "completion_result": None,
            "completed_at": None,
            "reopened_count": 0,
            "transition_history": [
                {
                    "from": None,
                    "to": "pending",
                    "at": timestamp,
                    "reason": "activated_from_sop_step",
                }
            ],
        }
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="action_item",
            payload=payload,
        )
        connection.execute(
            """
            INSERT INTO actions (
                action_id, plan_id, payload_object_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (action_id, plan_id, object_id, timestamp, timestamp),
        )
        for dependency in dependency_action_ids:
            connection.execute(
                """
                INSERT INTO action_dependencies (
                    action_id, depends_on_action_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (action_id, dependency, timestamp),
            )
        connection.execute(
            """
            INSERT INTO action_audit_events (
                event_id, action_id, event_type, created_at
            ) VALUES (?, ?, 'created.from_sop', ?)
            """,
            (uuid4().hex, action_id, timestamp),
        )
        return action_id

    def _finish_action(
        self,
        connection: Any,
        *,
        vmk: bytes,
        action_id: str,
        outcome: str,
        result: str,
    ) -> None:
        row = connection.execute(
            "SELECT payload_object_id FROM actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        payload, revision = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        previous = str(payload["status"])
        next_status = "completed" if outcome == "completed" else "cancelled"
        payload.update(
            {
                "status": next_status,
                "completion_result": result if outcome == "completed" else None,
                "completed_at": _iso() if outcome == "completed" else None,
            }
        )
        history = list(payload.get("transition_history") or [])
        history.append(
            {
                "from": previous,
                "to": next_status,
                "at": _iso(),
                "reason": f"sop_step_{outcome}",
            }
        )
        payload["transition_history"] = history
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
            object_type="action_item",
            payload=payload,
            expected_revision=revision,
        )
        connection.execute(
            "UPDATE actions SET updated_at = ? WHERE action_id = ?",
            (_iso(), action_id),
        )
        connection.execute(
            """
            INSERT INTO action_audit_events (
                event_id, action_id, event_type, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (uuid4().hex, action_id, f"sop.{outcome}", _iso()),
        )

    def _supersede_optional_steps(
        self,
        connection: Any,
        vmk: bytes,
        occurrence_id: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM step_instances
            WHERE occurrence_id = ? AND is_required = 0
              AND state NOT IN ('completed', 'waived', 'superseded')
            """,
            (occurrence_id,),
        ).fetchall()
        for row in rows:
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            payload["state"] = "superseded"
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
                object_type="affair_step",
                payload=payload,
                expected_revision=revision,
            )
            connection.execute(
                """
                UPDATE step_instances
                SET state = 'superseded', updated_at = ?
                WHERE step_instance_id = ?
                """,
                (_iso(), str(row["step_instance_id"])),
            )
            if row["action_id"]:
                action = connection.execute(
                    "SELECT payload_object_id FROM actions WHERE action_id = ?",
                    (str(row["action_id"]),),
                ).fetchone()
                action_payload, action_revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(action["payload_object_id"]),
                )
                action_payload["status"] = "superseded"
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(action["payload_object_id"]),
                    object_type="action_item",
                    payload=action_payload,
                    expected_revision=action_revision,
                )

    def _steps_for_occurrence(
        self,
        connection: Any,
        vmk: bytes,
        occurrence_id: str,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT * FROM step_instances
            WHERE occurrence_id = ? ORDER BY created_at
            """,
            (occurrence_id,),
        ).fetchall()
        items = []
        for row in rows:
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            items.append(
                {
                    "step_instance_id": str(row["step_instance_id"]),
                    "revision": revision,
                    "action_id": (
                        str(row["action_id"])
                        if row["action_id"] is not None
                        else None
                    ),
                    **payload,
                }
            )
        return items

    def _decisions(
        self,
        connection: Any,
        vmk: bytes,
        affair_id: str,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT * FROM decision_records
            WHERE affair_id = ? ORDER BY created_at
            """,
            (affair_id,),
        ).fetchall()
        items = []
        for row in rows:
            payload, revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            items.append(
                {
                    "decision_id": str(row["decision_id"]),
                    "revision": revision,
                    "decision_kind": str(row["decision_kind"]),
                    "step_instance_id": (
                        str(row["step_instance_id"])
                        if row["step_instance_id"] is not None
                        else None
                    ),
                    **payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return items

    def _decision_value(
        self,
        connection: Any,
        vmk: bytes,
        affair_id: str,
        decision_key: str,
    ) -> str | None:
        rows = connection.execute(
            """
            SELECT payload_object_id FROM decision_records
            WHERE affair_id = ?
              AND decision_kind IN ('teacher', 'school')
            ORDER BY created_at DESC
            """,
            (affair_id,),
        ).fetchall()
        for row in rows:
            payload, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            if payload.get("decision_key") == decision_key:
                return (
                    str(payload["selected_option"])
                    if payload.get("selected_option")
                    else None
                )
        return None

    def _template_in_connection(
        self,
        connection: Any,
        vmk: bytes,
        template_version_id: str,
    ) -> tuple[dict[str, Any], int]:
        row = connection.execute(
            """
            SELECT definition_object_id FROM sop_template_versions
            WHERE template_version_id = ?
            """,
            (template_version_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "sop_template_not_found",
                "SOP 模板版本不存在",
                status_code=404,
            )
        return self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["definition_object_id"]),
        )

    @staticmethod
    def _affair(connection: Any, affair_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM affairs WHERE affair_id = ?",
            (affair_id,),
        ).fetchone()
        if row is None:
            raise VaultError(
                "sop_affair_not_found",
                "事务不存在",
                status_code=404,
            )
        return row

    def _active_affair(self, connection: Any, affair_id: str) -> Any:
        row = self._affair(connection, affair_id)
        if str(row["state"]) != "active":
            raise VaultError(
                "sop_affair_closed",
                "事务已经结案，请先重开",
                status_code=409,
            )
        return row

    @staticmethod
    def _validate_template_steps(
        steps: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not steps or len(steps) > 100:
            raise VaultError(
                "sop_template_steps_invalid",
                "模板需要 1 至 100 个步骤",
                status_code=422,
            )
        normalized = []
        keys: set[str] = set()
        for item in steps:
            key = SopWorkflowService._code(item.get("key"), "步骤代码")
            if key in keys:
                raise VaultError(
                    "sop_template_step_duplicate",
                    "模板步骤代码不能重复",
                    status_code=422,
                )
            keys.add(key)
            safety = bool(item.get("safety_required"))
            required = bool(item.get("required")) or safety
            waivable = bool(item.get("waivable")) and not safety
            normalized.append(
                {
                    "key": key,
                    "title": SopWorkflowService._text(
                        item.get("title"),
                        "步骤名称",
                        240,
                    ),
                    "details": SopWorkflowService._optional_text(
                        item.get("details"),
                        "步骤说明",
                        8000,
                    ),
                    "required": required,
                    "waivable": waivable,
                    "safety_required": safety,
                    "depends_on": list(
                        dict.fromkeys(
                            str(value).strip()
                            for value in list(item.get("depends_on") or [])
                            if str(value).strip()
                        )
                    ),
                    "activation": SopWorkflowService._activation(
                        item.get("activation")
                    ),
                    "decision_key": (
                        SopWorkflowService._code(
                            item.get("decision_key"),
                            "步骤决定键",
                        )
                        if item.get("decision_key")
                        else None
                    ),
                    "decision_prompt": SopWorkflowService._optional_text(
                        item.get("decision_prompt"),
                        "步骤决定提示",
                        1000,
                    ),
                    "decision_options": [
                        {
                            "value": SopWorkflowService._code(
                                option.get("value"),
                                "决定选项值",
                            ),
                            "label": SopWorkflowService._text(
                                option.get("label"),
                                "决定选项名称",
                                240,
                            ),
                        }
                        for option in list(
                            item.get("decision_options") or []
                        )
                    ],
                    "communication_templates": [
                        {
                            "kind": SopWorkflowService._code(
                                draft.get("kind"),
                                "沟通模板类型",
                            ),
                            "audience": SopWorkflowService._text(
                                draft.get("audience"),
                                "沟通对象",
                                240,
                            ),
                            "content": SopWorkflowService._text(
                                draft.get("content"),
                                "沟通模板正文",
                                4000,
                            ),
                            "basis": SopWorkflowService._text(
                                draft.get("basis"),
                                "沟通模板依据",
                                1000,
                            ),
                            "unknowns": [
                                SopWorkflowService._text(
                                    unknown,
                                    "沟通不确定项",
                                    500,
                                )
                                for unknown in list(
                                    draft.get("unknowns") or []
                                )
                            ],
                            "status": "unsent",
                        }
                        for draft in list(
                            item.get("communication_templates") or []
                        )
                    ],
                }
            )
        graph = {
            str(item["key"]): set(item["depends_on"])
            for item in normalized
        }
        if any(not dependencies.issubset(keys) for dependencies in graph.values()):
            raise VaultError(
                "sop_template_dependency_missing",
                "模板步骤依赖了不存在的步骤",
                status_code=422,
            )
        resolved: set[str] = set()
        while len(resolved) < len(graph):
            ready = {
                key for key, dependencies in graph.items()
                if key not in resolved and dependencies.issubset(resolved)
            }
            if not ready:
                raise VaultError(
                    "sop_template_dependency_cycle",
                    "模板步骤存在循环依赖",
                    status_code=422,
                )
            resolved.update(ready)
        return normalized

    @staticmethod
    def _activation(value: object) -> dict[str, object] | None:
        if not value:
            return None
        if not isinstance(value, dict):
            raise VaultError(
                "sop_activation_invalid",
                "步骤分支条件无效",
                status_code=422,
            )
        decision_key = SopWorkflowService._code(
            value.get("decision_key"),
            "分支决定键",
        )
        allowed_values = [
            SopWorkflowService._code(item, "分支选项")
            for item in list(value.get("allowed_values") or [])
        ]
        if not allowed_values:
            raise VaultError(
                "sop_activation_invalid",
                "步骤分支至少需要一个允许选项",
                status_code=422,
            )
        return {
            "decision_key": decision_key,
            "allowed_values": list(dict.fromkeys(allowed_values)),
        }

    @staticmethod
    def _code(value: object, label: str) -> str:
        clean = str(value or "").strip()
        if (
            not clean
            or len(clean) > 80
            or any(not (character.isalnum() or character in "._-") for character in clean)
        ):
            raise VaultError(
                "sop_code_invalid",
                f"{label}无效",
                status_code=422,
            )
        return clean

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "sop_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean

    @staticmethod
    def _optional_text(value: object, label: str, maximum: int) -> str | None:
        clean = str(value or "").strip()
        if len(clean) > maximum:
            raise VaultError(
                "sop_text_invalid",
                f"{label}不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean or None

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json
                FROM idempotency_ledger WHERE operation_id = ?
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
        connection: Any,
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
    def _event(
        connection: Any,
        affair_id: str,
        step_instance_id: str | None,
        event_type: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO affair_events (
                event_id, affair_id, step_instance_id, event_type, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                affair_id,
                step_instance_id,
                event_type,
                _iso(),
            ),
        )

    @staticmethod
    def _revision_conflict(label: str) -> None:
        raise VaultError(
            "vault_revision_conflict",
            f"{label}已经变化，请刷新后再操作",
            status_code=409,
        )


__all__ = ["SopWorkflowService"]
