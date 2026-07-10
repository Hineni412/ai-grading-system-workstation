# WP1.2 Batch E - Grading Run Job Implementation

## Goal

Expose the existing session grading flow as a FastAPI-backed background job while preserving the current Streamlit grading behavior and the existing `grading_runs` pause/resume ledger.

## Scope

- Reuse `GradingService.run_session_grading()` as the source of truth.
- Read `scan_analysis_latest.json` and `scan_manual_decisions_latest.json` from the existing session template/work directory for normal runs.
- For `failed_only=true`, let `GradingService` use the existing failed-paper retry path and do not require a scan analysis file.
- Register `grading_run` as a default `JobManager` handler.
- Add `POST /api/sessions/{session_id}/grading/run` as the session-oriented API entrypoint.
- Keep detailed grading pause/resume state in the existing `grading_runs` and `grading_run_items` tables; the generic `jobs` row remains a unified start/query/cancel wrapper.
- Do not change scoring prompts, AI model logic, batch algorithms, or Streamlit controls in this batch.
- Do not store API keys in `jobs.payload_json`; the handler reads the active local API profile/environment through a provider.

## Steps

1. Add failing tests for grading job service argument mapping, missing scan validation, handler registration, and route submission.
2. Add `backend/jobs/grading_run.py` with a testable `run_grading_job()` function.
3. Extend default job registration with a `grading_run` handler.
4. Add `backend/api/routers/grading.py` and register it in the app.
5. Run focused tests, API regression, full tests, and quick smoke check.

## Acceptance Checks

- A grading job invokes `GradingService.run_session_grading()` with existing scan payload/manual decisions and returns a compact summary.
- `POST /api/sessions/{session_id}/grading/run` queues `grading_run` without requiring or persisting API keys in request/job payload.
- Missing sessions still return the unified `session_not_found` API error.
- Normal grading runs fail early if `scan_analysis_latest.json` is absent; failed-only retries do not require it.
