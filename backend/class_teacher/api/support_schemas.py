from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)


class SubjectCreateRequest(OperationRequest):
    source_student_id: str = Field(min_length=1, max_length=240)
    display_name: str = Field(min_length=1, max_length=240)
    class_label: str | None = Field(default=None, max_length=240)


class SubjectUpdateRequest(OperationRequest):
    revision: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=240)
    class_label: str | None = Field(default=None, max_length=240)


class SubjectDeleteRequest(OperationRequest):
    confirmation_phrase: str
    preview_version: str | None = Field(default=None, min_length=64, max_length=64)


class RecordFields(BaseModel):
    record_kind: Literal[
        "fact",
        "student_statement",
        "reported_statement",
        "teacher_observation",
        "provisional_judgment",
        "professional_conclusion",
        "ai_draft",
    ]
    content: str = Field(min_length=1, max_length=12_000)
    scene: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    basis: str | None = None
    counterexample: str | None = None
    category: str | None = None
    observed_at: str
    review_at: str | None = None
    expires_at: str | None = None


class RecordCreateRequest(OperationRequest, RecordFields):
    # 可选归属方案：行动日志挂到这名学生已有的支持方案下（服务端校验归属）。
    plan_id: str | None = Field(default=None, min_length=1, max_length=64)


class RecordReviseRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12_000)
    scene: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    basis: str | None = None
    counterexample: str | None = None
    category: str | None = None
    observed_at: str
    review_at: str | None = None
    expires_at: str | None = None
    revision_reason: str = Field(min_length=1, max_length=1000)


class RecordStateRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    state: Literal["active", "withdrawn", "archived"]
    reason: str = Field(min_length=1, max_length=1000)


class AiDraftConfirmRequest(OperationRequest):
    confirmed_kind: str


class EvidenceLinkRequest(OperationRequest):
    observation_record_id: str
    evidence_record_id: str
    relation_kind: Literal["supports", "counterexample", "context"]


class SupportPlanCreateRequest(OperationRequest):
    goal: str = Field(min_length=1, max_length=1000)
    support_actions: list[str] = Field(min_length=1, max_length=30)
    review_at: str
    action_id: str | None = None


class SupportPlanAiDraftRequest(OperationRequest):
    # AI 起草支持方案：只需幂等操作号，草稿不落库。
    pass


class SupportPlanCompleteRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    result: str = Field(min_length=1, max_length=4000)
    # 效果评价。缺省（null）为普通完成：只记录结果，不评价效果、不回写档案；
    # effective 会把方案行动并入档案「已验证有效」支持重点；ineffective/continue
    # 只留痕评价，不改档案。
    outcome: Literal["effective", "ineffective", "continue"] | None = None


class AffairProjectionRequest(OperationRequest):
    affair_id: str


class FollowUpPostponeRequest(BaseModel):
    due_date: str = Field(min_length=10, max_length=10)


class QuickTextRequest(OperationRequest):
    text: str = Field(min_length=1, max_length=12_000)
    subject_id: str | None = None


class QuickFragment(BaseModel):
    fragment_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)
    suggested_kind: str = "unclassified"


class QuickUpdateRequest(OperationRequest):
    revision: int = Field(ge=1)
    fragments: list[QuickFragment] = Field(min_length=1, max_length=50)


class QuickConfirmRequest(OperationRequest):
    fragment_id: str
    target_kind: Literal["support_record", "action", "sop"]
    target_options: dict[str, Any] = Field(default_factory=dict)


class EvidenceBatchRequest(OperationRequest):
    batch: dict[str, Any]


class SpreadsheetPreviewRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=500)
    content_base64: str = Field(min_length=1, max_length=14_000_000)
    sheet_name: str | None = Field(default=None, max_length=500)


class EvidenceSupersedeRequest(OperationRequest):
    reason: str = Field(min_length=1, max_length=1000)


class EvidenceSessionUpdateRequest(OperationRequest):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    grade: str | None = Field(default=None, min_length=1, max_length=40)
    term: str | None = Field(default=None, min_length=1, max_length=40)
    exam_type: str | None = Field(default=None, min_length=1, max_length=120)
    occurred_on: str | None = Field(default=None, min_length=1, max_length=40)
    academic_year: str | None = Field(default=None, min_length=1, max_length=40)


class EvidenceSessionDeleteRequest(OperationRequest):
    confirmation_phrase: str
    preview_version: str | None = Field(default=None, min_length=64, max_length=64)


class EvidenceSessionMaxScoresRequest(OperationRequest):
    # 按学科名更正满分；服务端再校验学科属于场次且满分为正数。
    # 允许空 dict：只补填年级人数时不带满分，空更新由服务端拒绝。
    max_scores: dict[str, float] = Field(default_factory=dict, max_length=40)
    # 可选年级人数；正整数校验在服务端，便于返回专用错误码。
    participant_count: int | None = None


class EvidenceGlobalMaxScoresRequest(OperationRequest):
    # 全年级统一更正：键为归一展示科目名，服务端反查各场次原始列名。
    max_scores: dict[str, float] = Field(default_factory=dict, max_length=40)
    participant_count: int | None = None


class AttentionCreateRequest(OperationRequest):
    evidence_version_id: str
    observed_fact: str = Field(min_length=1, max_length=4000)
    comparability: Literal[
        "directly_comparable",
        "reference_only",
        "not_comparable",
        "insufficient_information",
    ]
    limitations: list[str] = Field(default_factory=list, max_length=20)
    verification_question: str = Field(min_length=1, max_length=2000)
    low_risk_next_step: str = Field(min_length=1, max_length=2000)
    evidence_sufficiency: str = Field(min_length=1, max_length=1000)
    review_suggestion: str = Field(min_length=1, max_length=2000)


class AttentionResolveRequest(OperationRequest):
    revision: int = Field(ge=1)
    decision: Literal["follow_up", "observe", "no_action"]
    reason: str | None = None
    plan_id: str | None = None
    review_at: str | None = None


class AttentionDecisionRequest(AttentionResolveRequest):
    source_version: str = Field(min_length=64, max_length=64)


__all__ = [
    "AffairProjectionRequest",
    "AiDraftConfirmRequest",
    "AttentionCreateRequest",
    "AttentionDecisionRequest",
    "AttentionResolveRequest",
    "EvidenceBatchRequest",
    "EvidenceGlobalMaxScoresRequest",
    "EvidenceLinkRequest",
    "EvidenceSupersedeRequest",
    "FollowUpPostponeRequest",
    "SpreadsheetPreviewRequest",
    "OperationRequest",
    "QuickConfirmRequest",
    "QuickTextRequest",
    "QuickUpdateRequest",
    "RecordCreateRequest",
    "RecordReviseRequest",
    "RecordStateRequest",
    "SubjectCreateRequest",
    "SubjectDeleteRequest",
    "SubjectUpdateRequest",
    "SupportPlanAiDraftRequest",
    "SupportPlanCompleteRequest",
    "SupportPlanCreateRequest",
]
