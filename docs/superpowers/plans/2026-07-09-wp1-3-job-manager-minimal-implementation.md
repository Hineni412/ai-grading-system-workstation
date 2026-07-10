# WP1.3 JobManager Minimal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal persistent JobManager so future scan, grading, report, tagging, and training export API endpoints can start background work, poll status, and request cancellation without blocking FastAPI requests.

**Architecture:** Add a new `jobs` table in the grading database with a matching runtime initializer. Keep grading-specific detailed progress in the existing `grading_runs` ledger; `jobs` is the common index and status surface. Use an in-process `ThreadPoolExecutor`; process restart does not resume jobs and will mark stale `queued/running` jobs as failed on manager startup.

**Tech Stack:** FastAPI 0.139.0, Pydantic 2.13.4, SQLite, `ThreadPoolExecutor`, pytest, FastAPI `TestClient`.

## Global Constraints

- Do not start real scan/grading/report work in this batch; only the generic job shell and API contract land here.
- Tests must use temporary SQLite databases and dependency overrides.
- Add a SQL migration for the `jobs` table and a runtime initializer with the same schema.
- Do not submit or stage `user_data/`.
- Supported job statuses: `queued`, `running`, `paused`, `succeeded`, `failed`, `cancelled`.

---

## File Structure

- Create: `backend/jobs/store.py` for persistent job CRUD and schema initialization.
- Create: `backend/jobs/manager.py` for handler registration, background execution, progress updates, and cancellation requests.
- Create: `backend/jobs/__init__.py`.
- Create: `backend/api/routers/jobs.py` for `POST /api/jobs/{job_type}`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel`.
- Create: `backend/api/schemas/jobs.py` for request/response models.
- Modify: `backend/api/dependencies.py` to provide a singleton job manager.
- Modify: `backend/api/routers/__init__.py` and `backend/api/app.py` to include job routes.
- Create: `migrations/grading/003_add_jobs.sql`.
- Modify: `tools/smoke_check.py`, `tools/generate_schema_baseline.py`, and `tools/migration_rehearsal.py` so runtime schema checks include `JobStore.initialize()`.
- Create: `tests/test_job_store.py`, `tests/test_job_manager.py`, and `tests/test_api_jobs.py`.
- Modify: `AGENTS.md` and `ARCHITECTURE.md` after verification.

## Task 1: Persistent Job Store

- [x] **Step 1: Write failing store tests**

Create `tests/test_job_store.py` covering create/get, progress update, cancellation, completion, stale queued/running cleanup, and unsupported status rejection.

- [x] **Step 2: Run store tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_job_store.py -q`

Expected: FAIL because `backend.jobs.store` does not exist.

- [x] **Step 3: Implement `backend/jobs/store.py` and migration**

Add `JobRecord`, `JobStore`, schema initializer, `migrations/grading/003_add_jobs.sql`, and methods:
`create_job()`, `get_job()`, `mark_running()`, `update_progress()`, `finish()`, `request_cancel()`, `is_cancel_requested()`, `fail_interrupted_jobs()`.

- [x] **Step 4: Run store tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_job_store.py -q`

## Task 2: In-Process JobManager

- [x] **Step 1: Write failing manager tests**

Create `tests/test_job_manager.py` covering a registered handler that reports progress and succeeds, a handler that fails, unsupported job type rejection, and cancellation request visibility.

- [x] **Step 2: Run manager tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_job_manager.py -q`

- [x] **Step 3: Implement `backend/jobs/manager.py`**

Add `JobContext`, `JobManager`, `UnsupportedJobTypeError`, and handler registration/submission.

- [x] **Step 4: Run manager tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_job_manager.py -q`

## Task 3: Jobs API

- [x] **Step 1: Write failing API tests**

Create `tests/test_api_jobs.py` with dependency-overridden manager. Cover `POST /api/jobs/{job_type}`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel`, missing job 404, and unsupported type 404/422 with unified error body.

- [x] **Step 2: Run API tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_jobs.py -q`

- [x] **Step 3: Implement schemas, dependency, and router**

Add `backend/api/schemas/jobs.py`, `get_job_manager()`, and `backend/api/routers/jobs.py`. Keep the default app manager with no registered business handlers yet.

- [x] **Step 4: Run API tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_jobs.py -q`

## Task 4: Schema Tooling And Verification

- [x] **Step 1: Update schema tooling**

Include `JobStore.initialize()` alongside `DBManager.initialize()` and `GradingRunStore.initialize()` in smoke/schema rehearsal utilities.

- [x] **Step 2: Run focused tests**

Run: `runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_jobs.py -q`

- [x] **Step 3: Run API regression tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py tests\test_api_jobs.py -q`

- [x] **Step 4: Run quick smoke**

Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

- [x] **Step 5: Update handoff docs**

Update `AGENTS.md` and `ARCHITECTURE.md` to show WP1.3 minimal JobManager complete and next work as registering scan/grading/report handlers.
