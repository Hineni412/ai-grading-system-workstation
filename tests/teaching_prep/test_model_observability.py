from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.routers.jobs import public_job_error
from backend.jobs.store import JobRecord
from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.teaching_prep.api.router import _semester_mapping_job_response


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_diagnostic_journal_distinguishes_truncated_json(tmp_path: Path) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "llm_diagnostics.jsonl")
    journal.record_response(
        operation_id="semester-mapping-truncated-diagnostic-0001",
        request_id="semester-mapping-truncated-diagnostic-0001",
        attempt=1,
        request_kind="workspace",
        protocol="chat_completions",
        model="synthetic-model",
        endpoint_host="model.invalid",
        response={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"tree":['},
                }
            ]
        },
        elapsed_ms=123,
    )

    listed = journal.list_calls(limit=10)
    call = journal.get_call(str(listed["items"][0]["call_id"]))
    assert call is not None
    assert call["parse_status"] == "truncated_json"
    assert "长度上限" in str(call["parse_error"])


@pytest.mark.parametrize(
    ("internal_error", "public_error"),
    [
        (
            "semester mapping model response text is unavailable",
            "模型已返回，但没有可读取的正文；可重新检查后手动生成。",
        ),
        (
            "semester mapping model returned invalid JSON",
            "模型已返回，但目录格式不是有效 JSON；可重新检查后手动生成。",
        ),
        (
            "semester mapping model output was truncated",
            "模型输出达到长度上限，目录没有完整返回；系统未自动重试。",
        ),
        (
            "semester mapping model response must be an object",
            "模型已返回，但目录顶层结构不是对象；可重新检查后手动生成。",
        ),
        (
            "semester mapping response failed local validation",
            "模型目录未通过页码和结构校验；可重新检查后手动生成。",
        ),
        (
            "semester mapping model attempted to replace the existing "
            "lesson tree",
            "模型尝试重建已有课时目录，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model referred to a lesson outside the "
            "existing tree",
            "模型引用了当前目录中不存在的课时，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model omitted uncertainty for unmapped pages",
            "模型没有说明未映射的资料页，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model configuration is unavailable",
            "当前备课模型配置不可用，请先检查“大模型 API”设置。",
        ),
        (
            "semester mapping model request parameter is incompatible",
            "当前模型不接受目录请求参数，请检查模型配置后手动生成。",
        ),
    ],
)
def test_mapping_job_exposes_safe_specific_failure_reason(
    internal_error: str,
    public_error: str,
) -> None:
    job = JobRecord(
        id=1,
        job_type="teaching_prep.semester_mapping",
        payload={},
        result={},
        status="failed",
        progress=0.35,
        stage="calling_model",
        detail="模型请求已开始，正在等待返回",
        error=internal_error,
        cancel_requested=False,
        created_at="2026-08-03 00:00:00",
        started_at="2026-08-03 00:00:00",
        updated_at="2026-08-03 00:00:01",
        finished_at="2026-08-03 00:00:01",
    )

    assert _semester_mapping_job_response(job).error == public_error
    assert public_job_error(job) == public_error
