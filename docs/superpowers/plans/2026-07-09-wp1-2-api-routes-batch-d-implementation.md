# WP1.2 API Routes Batch D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add synchronous template and answer-region API endpoints for the future Vue exam workspace. This batch binds already-prepared template image paths, persists answer-region drafts, loads compatible drafts, and commits formal answer regions through the existing commit service.

**Architecture:** Keep this as a thin API layer. Do not implement PDF/image upload parsing or AI template analysis here. Reuse `DBManager.upsert_session_template()`, `AnswerRegionDraftService`, and `AnswerRegionCommitService`.

**Routes:**
- `PUT /api/sessions/{session_id}/template`
- `GET /api/sessions/{session_id}/regions/draft`
- `PUT /api/sessions/{session_id}/regions/draft`
- `POST /api/sessions/{session_id}/regions/commit`

**Out of scope for this batch:**
- Multipart upload handling
- PDF page rendering
- AI template analysis
- Region editor frontend behavior

---

## Task 1: Template And Region Contracts

**Files:**
- Create: `backend/api/routers/templates.py`
- Create: `backend/api/schemas/templates.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/schemas/__init__.py`
- Create: `tests/test_api_template_region_routes.py`

- [x] **Step 1: Write failing tests**

Cover template path binding, draft save/load, successful commit with snapshot completion, invalid commit validation, and unified 404 for sessions without a template.

- [x] **Step 2: Run tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_template_region_routes.py -q`

Expected: FAIL because Batch D routes and template-directory dependency do not exist.

- [x] **Step 3: Implement schemas and dependencies**

Add template request schema, draft request/response schemas, commit request/response schemas, and `get_templates_dir()` dependency.

- [x] **Step 4: Implement template/regions router**

Compute the current template fingerprint from front/back template files. Use `AnswerRegionDraftService` for draft save/load and `AnswerRegionCommitService` for formal commit.

## Task 2: Verification And Handoff

- [x] **Step 1: Run focused tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_template_region_routes.py -q`

- [x] **Step 2: Run API regression tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py -q`

- [x] **Step 3: Run quick smoke**

Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

- [x] **Step 4: Update handoff docs**

Update `AGENTS.md` and `ARCHITECTURE.md` to show Batch D complete and Batch E next: scan/grading routes, with execution endpoints deferred to WP1.3 JobManager where needed.
