# WP1.2 Batch E - Scan Analysis Job Implementation

## Goal

Expose the existing scan precheck flow as a FastAPI-backed background job while keeping Streamlit's current upload and grading behavior unchanged.

## Scope

- Reuse `Scanner.analyze()` as the source of truth for PDF/image precheck.
- Add a small backend scan service that:
  - validates the session exists and template mapping is confirmed,
  - reads scan files from the existing session upload directory by default,
  - reads students from `DBManager.list_students()`,
  - persists `scan_analysis_latest.json` under the existing session template/work directory,
  - returns a compact summary in the job result.
- Register `scan_analysis` as a default `JobManager` handler.
- Add `POST /api/sessions/{session_id}/scan/analyze` as a session-oriented API entrypoint.
- Do not add scan upload, manual exception editing, grading start, PDF source deletion changes, or LLM Gateway refactors in this batch.
- Do not store API keys in `jobs.payload_json`; the handler reads the active local API profile/environment through a provider.

## Steps

1. Add failing tests for scan service persistence and session scan route submission.
2. Add `backend/jobs/scan_analysis.py` with a testable `run_scan_analysis()` function.
3. Extend default job registration with a `scan_analysis` handler.
4. Add `backend/api/routers/scan.py` and register it in the app.
5. Run focused tests, API regression, and quick smoke check.

## Acceptance Checks

- A scan job writes `scan_analysis_latest.json` and returns `{session_id, scan_analysis_path, summary}`.
- The route queues `scan_analysis` without requiring API keys in the request body.
- Missing sessions still return the unified `session_not_found` API error.
- Jobs API can poll the completed scan job result.
