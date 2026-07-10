# WP1.2 Batch E - Report Export Job Implementation

## Goal

Expose the existing Excel session report export as a FastAPI-backed background job without changing the current Streamlit report export flow.

## Scope

- Keep `ReportGenerator.export_session(session_id)` as the source of truth.
- Add a default `report_export` job handler that records the generated file path in the job result.
- Add `POST /api/sessions/{session_id}/reports/export` as a session-oriented API entrypoint.
- Preserve the generic `/api/jobs/{job_type}` API for low-level job submission.
- Do not implement original-paper PDF export, scanning, AI grading, pause/resume, or report download streaming in this batch.

## Steps

1. Add failing tests for persisted job results, report handler registration, and the session report export route.
2. Extend the jobs table/runtime store with `result_json`.
3. Let `JobManager` persist handler return values as job results.
4. Add default report export handler registration.
5. Register the report export route and app router.
6. Run focused tests and a quick smoke check.

## Acceptance Checks

- A report handler can return a generated file path and the completed job exposes it through API responses.
- `POST /api/sessions/{session_id}/reports/export` returns `202` and queues `report_export`.
- Missing sessions still return the unified `session_not_found` API error.
- Migration SQL and runtime schema stay aligned.
