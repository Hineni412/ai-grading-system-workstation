from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.schemas.jobs import JobResponse


class ScoreExcelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hide_bottom_enabled: bool = True
    hide_bottom_n: int = Field(default=8, ge=0, le=100)
    manual_hidden_student_ids: list[int] = Field(
        default_factory=list,
        max_length=500,
    )

    @field_validator("manual_hidden_student_ids")
    @classmethod
    def _normalize_student_ids(cls, value: list[int]) -> list[int]:
        if any(
            not isinstance(student_id, int)
            or isinstance(student_id, bool)
            or student_id <= 0
            for student_id in value
        ):
            raise ValueError("manual hidden student ids must be positive integers")
        return sorted(set(value))

    @model_validator(mode="after")
    def _normalize_disabled_bottom_rule(self) -> ScoreExcelOptions:
        if not self.hide_bottom_enabled:
            self.hide_bottom_n = 0
        return self


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: Literal[
        "score_excel",
        "annotated_original_pdf",
        "personal_analysis_html",
    ] = "score_excel"
    force_regenerate: bool = False
    excel_options: ScoreExcelOptions | None = None
    student_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)
    publish: bool = True

    @model_validator(mode="after")
    def _validate_report_options(self) -> ReportExportRequest:
        if self.student_ids is not None:
            if any(s <= 0 for s in self.student_ids):
                raise ValueError("student ids must be positive")
            self.student_ids = sorted(set(self.student_ids))
        if self.report_type != "personal_analysis_html" and (self.student_ids is not None or not self.publish):
            raise ValueError("student selection and publish are only valid for personal reports")
        if self.report_type != "score_excel":
            if self.excel_options is not None:
                raise ValueError("excel options are only valid for score_excel")
            return self
        if self.excel_options is None:
            self.excel_options = ScoreExcelOptions()
        return self


class PersonalReportBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_ids: list[int] = Field(min_length=1, max_length=20)
    student_ids: list[int] = Field(min_length=1, max_length=500)
    scope_label: str = Field(default="指定学生", min_length=1, max_length=80)

    @field_validator("session_ids", "student_ids")
    @classmethod
    def _positive_ids(cls, value):
        if any(v <= 0 for v in value):
            raise ValueError("ids must be positive")
        return sorted(set(value))


class AnalysisPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: Literal["personal_analysis_html"]
    configured: bool
    service_name: str | None
    model_name: str | None
    call_count: int = Field(ge=0)
    estimated_total_tokens: int = Field(ge=0, description="文本输入和输出上限的粗估，不含服务商另计的图片用量")
    cache_hits: int = Field(ge=0)
    cause_call_count: int = Field(default=0, ge=0, description="前置错因整理预计新增调用次数；失败题不自动重发")
    cause_total_questions: int = Field(default=0, ge=0, description="本场需要错因整理的失分题总数")
    cause_estimated_tokens: int = Field(default=0, ge=0)


class ClassAnalysisResponse(BaseModel):
    """成绩中心「班级分析」内嵌页的读取契约（字段名与前端冻结，不得改名）。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["no_data", "ready", "generating"]
    auto_generate: bool
    small_sample: bool
    data: dict[str, Any] | None
    narrative: dict[str, Any] | None
    narrative_failed: bool
    generated_at: str | None
    stale: bool
    narrative_state: Literal["current", "stale", "old_prompt", "missing"] | None = None
    active_job_id: int | None
    class_names: list[str] = Field(default_factory=list)
    selected_class: str | None = None
    cause_analysis: dict[str, Any] | None = None


class CausePatternEditRequest(BaseModel):
    """教师可选地修改一条错法的名称/大类（错因体系 P5，非必经步骤）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=40)
    kind: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=80)
    new_reason: str = Field(min_length=1, max_length=40)
    category: str | None = Field(default=None, max_length=20)
    operation_token: str | None = Field(default=None, max_length=80)


class CausePatternEditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


class ClassAnalysisSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_generate: bool


class ClassAnalysisSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_generate: bool


class ReportPipelineCausesStatus(BaseModel):
    """错因整理待补情况；call_count/estimated_tokens 按手动口径（含失败重试与旧版升级）预估。"""

    model_config = ConfigDict(extra="forbid")

    pending_questions: int = Field(ge=0)
    total_questions: int = Field(ge=0)
    call_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)


class ReportPipelineClassesStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    total: int = Field(ge=0)


class ReportPipelinePersonalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    total: int = Field(ge=0)
    call_count: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)


class ReportPipelineStatusResponse(BaseModel):
    """「AI 整理」统一管线状态：三阶段待补数量与是否有进行中任务。"""

    model_config = ConfigDict(extra="forbid")

    auto_generate: bool
    configured: bool
    service_name: str | None
    model_name: str | None
    active_job_id: int | None
    review_pending: int = Field(ge=0)
    causes: ReportPipelineCausesStatus
    class_reports: ReportPipelineClassesStatus
    personal_reports: ReportPipelinePersonalStatus
    complete: bool


class AnalysisReviewNoteItem(BaseModel):
    """个人报告"建议核对"条目；review_item_id 为复核页实际条目标识。"""

    model_config = ConfigDict(extra="forbid")

    student_id: int
    student_code: str | None = None
    student_name: str
    class_name: str | None = None
    question_id: str
    display_label: str
    note: str
    lock_revision: int = Field(ge=0)
    status: Literal["pending", "confirmed"]
    review_item_id: str | None = None


class AnalysisReviewNotesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    generated_at: str | None = None
    items: list[AnalysisReviewNoteItem]


class ReportFileDeleteResponse(BaseModel):
    """删除一份留存的本机报告文件的结果。"""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    deleted: bool
    freed_bytes: int = Field(ge=0)


class ReportExportHistoryItem(JobResponse):
    is_current_revision: bool
    file_status: Literal[
        "pending",
        "available",
        "expired",
        "failed",
        "cancelled",
        "unavailable",
    ]


class ReportExportContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_revision: str
    has_results: bool
    jobs: list[ReportExportHistoryItem]
    total: int
    page: int
    page_size: int
    total_pages: int
