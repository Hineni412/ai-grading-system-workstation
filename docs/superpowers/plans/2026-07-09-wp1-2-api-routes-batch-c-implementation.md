# WP1.2 API Routes Batch C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add synchronous config read/write API endpoints for the future Vue exam workspace. This batch saves already-generated rubric/answer-key JSON and binds it to an existing grading session. Model-driven generation remains out of scope until WP1.3 JobManager exists.

**Architecture:** Keep the API as a thin wrapper. Validate config payloads with `session_manager.validate_generated_config()` and persist files with `session_manager.save_generated_config()`. Use dependency injection for the upload-config directory so tests never write into real `user_data/`.

**Routes:**
- `GET /api/sessions/{session_id}/config`
- `PUT /api/sessions/{session_id}/config`

**Out of scope for this batch:**
- `POST /api/sessions/{session_id}/config/generate`
- DOCX/PDF upload parsing
- LLM calls
- template mapping refresh after config replacement

---

## Task 1: Config Contracts

**Files:**
- Create: `backend/api/routers/config.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/dependencies.py`
- Create: `backend/api/schemas/config.py`
- Modify: `backend/api/schemas/__init__.py`
- Create: `tests/test_api_config_routes.py`

- [x] **Step 1: Write failing tests**

Cover reading a session config from existing JSON files, saving a replacement payload to an isolated upload directory, updating the session paths, validating bad payloads, and returning unified 404 errors for missing sessions.

- [x] **Step 2: Run tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_config_routes.py -q`

Expected: FAIL because config routes do not exist.

- [x] **Step 3: Implement schemas and dependencies**

Add request/response schemas and `get_upload_config_dir()` dependency. The runtime dependency returns `get_path_manager().upload_config_dir`; tests override it with `tmp_path`.

- [x] **Step 4: Implement config router**

Use `_require_session()` and `_session_detail()` from the sessions router. Resolve stored file paths with `resolve_stored_file_path()`. On save, call `save_generated_config()` and `db.update_grading_session_config()`.

## Task 2: Verification And Handoff

- [x] **Step 1: Run focused tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_config_routes.py -q`

- [x] **Step 2: Run API regression tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py -q`

- [x] **Step 3: Run quick smoke**

Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

- [x] **Step 4: Update handoff docs**

Update `AGENTS.md` and `ARCHITECTURE.md` to show Batch C complete and Batch D next: template upload/analysis and answer-region draft/commit routes.
