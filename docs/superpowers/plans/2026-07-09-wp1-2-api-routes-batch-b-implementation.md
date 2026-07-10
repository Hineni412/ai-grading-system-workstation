# WP1.2 API Routes Batch B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe write API endpoints for session and student management so the future Vue app can create, rename, soft-delete, restore, import, update, and delete records through FastAPI.

**Architecture:** Continue the thin-wrapper approach from Batch A. Session writes call existing `DBManager` methods and return the same response schemas as read routes. Student writes reuse `DBManager.upsert_students()`, `update_student()`, and `delete_student_hard()` with API-level request validation and unified `ApiError` responses.

**Tech Stack:** FastAPI 0.139.0, Pydantic 2.13.4, SQLite through `DBManager`, pytest, FastAPI `TestClient`.

## Global Constraints

- Tests must use `tmp_path` SQLite databases and never mutate real `user_data/`.
- Do not introduce authentication, remote binding, or multi-user assumptions in WP1.2.
- Do not redefine grading/session/student business semantics; API endpoints delegate to existing `DBManager`.
- Destructive student delete uses existing `DBManager.delete_student_hard()` behavior, including its database backup side effect.
- Do not submit or stage `user_data/`.

---

## File Structure

- Modify: `backend/api/schemas/sessions.py`：add create/rename request schemas.
- Modify: `backend/api/schemas/students.py`：add upsert/update/delete response schemas.
- Modify: `backend/api/routers/sessions.py`：add session create, rename, soft-delete, restore endpoints.
- Modify: `backend/api/routers/students.py`：add student upsert, update, hard-delete endpoints.
- Create: `tests/test_api_write_routes.py`：Batch B contract tests.
- Modify: `docs/superpowers/plans/2026-07-09-wp1-2-api-routes-batch-b-implementation.md`：mark execution progress.
- Modify: `AGENTS.md` and `ARCHITECTURE.md` after verified behavior lands.

## Batch B Routes

- `POST /api/sessions`
- `PATCH /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/restore`
- `POST /api/students`
- `PATCH /api/students/{student_id}`
- `DELETE /api/students/{student_id}`

---

### Task 1: Session Write Contracts

**Files:**
- Modify: `tests/test_api_write_routes.py`
- Modify: `backend/api/schemas/sessions.py`
- Modify: `backend/api/routers/sessions.py`

**Interfaces:**
- Produces: `CreateSessionRequest`, `RenameSessionRequest`.
- Produces: session routes returning `SessionDetail`.

- [x] **Step 1: Write failing tests**

Create tests that seed an isolated database, call `POST /api/sessions`, `PATCH /api/sessions/{id}`, `DELETE /api/sessions/{id}`, and `POST /api/sessions/{id}/restore`, then assert the records are created, renamed, hidden by default after soft delete, and visible again after restore.

- [x] **Step 2: Run tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_write_routes.py -q`

Expected: FAIL because Batch B routes are missing.

- [x] **Step 3: Implement session write routes**

Use existing `DBManager` calls:

```python
db.create_grading_session(...)
db.rename_grading_session(...)
db.soft_delete_grading_session(...)
db.restore_grading_session(...)
```

Use `_require_session()` before update/delete/restore and return `_session_detail(...)`.

- [x] **Step 4: Run session write tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_write_routes.py::test_session_write_routes_create_rename_soft_delete_and_restore -q`

Expected: PASS.

### Task 2: Student Write Contracts

**Files:**
- Modify: `tests/test_api_write_routes.py`
- Modify: `backend/api/schemas/students.py`
- Modify: `backend/api/routers/students.py`

**Interfaces:**
- Produces: `StudentUpsertRequest`, `StudentUpdateRequest`, `StudentUpsertResponse`, `StudentDeleteResponse`.

- [x] **Step 1: Write failing tests**

Add tests for `POST /api/students`, `PATCH /api/students/{id}`, duplicate update conflict, and `DELETE /api/students/{id}` against a temporary database.

- [x] **Step 2: Run tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_write_routes.py -q`

Expected: FAIL until student write endpoints are implemented.

- [x] **Step 3: Implement student write routes**

Use existing `DBManager` calls:

```python
db.upsert_students([StudentRecord(...)])
db.update_student(...)
db.delete_student_hard(...)
```

Map validation errors to `ApiError` with stable codes: `invalid_student`, `student_not_found`, `student_code_conflict`.

- [x] **Step 4: Run write tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_write_routes.py -q`

Expected: PASS.

### Task 3: Verification And Handoff

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`

- [x] **Step 1: Run API regression tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py -q`

Expected: all selected tests PASS.

- [x] **Step 2: Run quick smoke**

Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

Expected: static compile OK and both database-copy idempotency checks OK.

- [x] **Step 3: Inspect diff**

Run: `git diff --stat -- . ':!user_data'` and `git status --short -- . ':!user_data'`.

Expected: only API code, tests, plans, and docs changed; no `user_data/` files staged.

- [x] **Step 4: Handoff next batch**

State that Batch C should move into config generation and template upload/analysis endpoints, while long-running execution remains for WP1.3 JobManager.
