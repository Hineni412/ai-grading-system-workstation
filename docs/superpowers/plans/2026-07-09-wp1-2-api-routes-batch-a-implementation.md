# WP1.2 API Routes Batch A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first read-only API slice for WP1.2 so the future Vue app can list sessions, students, session progress, templates, and answer regions without touching Streamlit internals.

**Architecture:** Keep FastAPI as a thin local wrapper over the existing `DBManager`; no business rules are redefined in the API layer. Routes depend on an injectable `get_grading_db()` provider so tests can use isolated temporary SQLite databases and runtime can use `PathManager.db_path`. Later WP1.2 batches will add config generation, scan, grading, review, reports, question-bank, training, graph, and ops routes.

**Tech Stack:** FastAPI 0.139.0, Pydantic 2.13.4, SQLite through the existing `DBManager`, pytest, FastAPI `TestClient`.

## Global Constraints

- Bind only to `127.0.0.1`; do not add LAN or public API behavior.
- Phase 1 API routes are thin shells over existing services and `DBManager`; do not rewrite grading, scoring, question-bank, or Streamlit workflows.
- Do not mutate real `user_data/` during tests; use `tmp_path` databases.
- Do not commit `user_data/` databases, images, reports, exports, or backups.
- Each batch must have API contract tests before implementation and run `runtime\python\python.exe tools\smoke_check.py --skip-tests` at minimum before handoff.

---

## File Structure

- Create: `backend/api/dependencies.py`：runtime dependency providers, starting with `get_grading_db()`.
- Create: `backend/api/schemas/__init__.py`：schema package exports.
- Create: `backend/api/schemas/core.py`：shared `ApiListResponse` helper.
- Create: `backend/api/schemas/sessions.py`：session, template, region, and progress response models.
- Create: `backend/api/schemas/students.py`：student response models.
- Create: `backend/api/routers/__init__.py`：router package exports.
- Create: `backend/api/routers/sessions.py`：read-only session/template/region/progress endpoints.
- Create: `backend/api/routers/students.py`：read-only student endpoints.
- Modify: `backend/api/app.py`：include the new routers under `/api`.
- Modify: `tests/test_api_app.py`：keep existing health/error tests.
- Create: `tests/test_api_read_routes.py`：contract tests for Batch A routes.
- Modify: `ARCHITECTURE.md` and `AGENTS.md` only if route behavior changes project structure in a way future agents need to know.

## Batch A Routes

- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `GET /api/sessions/{session_id}/progress`
- `GET /api/sessions/{session_id}/template`
- `GET /api/sessions/{session_id}/regions`
- `GET /api/students`

## Later WP1.2 Batches

- Batch B: session create/rename/soft-delete/restore and student import/update/delete.
- Batch C: config generation and template upload/analysis endpoints.
- Batch D: scan, grading, pause/resume, retry, review, and report endpoints; long-running work stays synchronous until WP1.3 JobManager.
- Batch E: question-bank, training, graph, and ops read/write endpoints.

---

### Task 1: Dependency Provider And Router Registration

**Files:**
- Create: `backend/api/dependencies.py`
- Modify: `backend/api/app.py`
- Test: `tests/test_api_read_routes.py`

**Interfaces:**
- Produces: `get_grading_db() -> DBManager`.
- Produces: `include_api_routers(api: FastAPI) -> None` through imports in `backend/api/routers`.

- [x] **Step 1: Write failing import and OpenAPI route test**

Create `tests/test_api_read_routes.py` with:

```python
from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def test_batch_a_routes_are_registered_in_openapi() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    paths = client.get("/api/openapi.json").json()["paths"]

    assert "/api/sessions" in paths
    assert "/api/sessions/{session_id}" in paths
    assert "/api/sessions/{session_id}/progress" in paths
    assert "/api/sessions/{session_id}/template" in paths
    assert "/api/sessions/{session_id}/regions" in paths
    assert "/api/students" in paths
```

- [x] **Step 2: Run test to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py::test_batch_a_routes_are_registered_in_openapi -q`

Expected: FAIL because the new API paths are not registered.

- [x] **Step 3: Add dependency provider and empty routers**

Create `backend/api/dependencies.py`:

```python
from __future__ import annotations

from db_manager import DBManager
from path_manager import get_path_manager


def get_grading_db() -> DBManager:
    return DBManager(get_path_manager().db_path)
```

Create router modules with `APIRouter(prefix="/api")` and placeholder path functions that will be filled by Tasks 2-3.

- [x] **Step 4: Run route registration test to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py::test_batch_a_routes_are_registered_in_openapi -q`

Expected: PASS.

### Task 2: Sessions Read API

**Files:**
- Create: `backend/api/schemas/core.py`
- Create: `backend/api/schemas/sessions.py`
- Modify: `backend/api/routers/sessions.py`
- Test: `tests/test_api_read_routes.py`

**Interfaces:**
- Consumes: `get_grading_db() -> DBManager`.
- Produces: `SessionSummary`, `SessionDetail`, `SessionProgress`, `TemplateResponse`, `AnswerRegionResponse`, and `ApiListResponse`.

- [x] **Step 1: Write failing sessions contract test**

Append tests that create an isolated `DBManager(tmp_path / "grading.db")`, seed one session, template, region, and paper, then override `get_grading_db`:

```python
def test_sessions_routes_return_existing_db_state(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager, StudentRecord

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = db.list_students()[0]["id"]
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    db.add_answer_region(
        session_id,
        template_id,
        {
            "page": "front",
            "region_order": 1,
            "x": 10,
            "y": 20,
            "w": 300,
            "h": 120,
            "mapped_question_id": "Q1",
            "is_confirmed": True,
        },
    )
    db.mark_template_confirmed(session_id, True)
    db.create_exam_paper(
        session_id,
        "front-paper.png",
        "back-paper.png",
        "Alice",
        student_id,
        "matched",
        "failed",
        "Request timed out.",
    )

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    sessions = client.get("/api/sessions").json()
    assert sessions["total"] == 1
    assert sessions["items"][0]["id"] == session_id
    assert sessions["items"][0]["name"] == "Exam A"

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["id"] == session_id
    assert detail["rubric_path"] == "rubric.json"

    progress = client.get(f"/api/sessions/{session_id}/progress").json()
    assert progress["total_papers"] == 1
    assert progress["failed_papers"] == 1
    assert progress["progress_percent"] == 100.0

    template = client.get(f"/api/sessions/{session_id}/template").json()
    assert template["id"] == template_id
    assert template["is_confirmed"] is True

    regions = client.get(f"/api/sessions/{session_id}/regions").json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"
```

- [x] **Step 2: Run sessions test to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py::test_sessions_routes_return_existing_db_state -q`

Expected: FAIL because schemas and route implementations are missing.

- [x] **Step 3: Implement minimal read routes**

Implement route handlers by calling:

```python
db.list_grading_sessions()
db.get_grading_session(session_id)
db.get_session_progress(session_id)
db.get_session_template(session_id)
db.list_answer_regions(session_id)
```

Return `ApiError(404, "session_not_found", ...)` when a session is missing.

- [x] **Step 4: Run sessions test to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py::test_sessions_routes_return_existing_db_state -q`

Expected: PASS.

### Task 3: Students Read API And Error Contracts

**Files:**
- Create: `backend/api/schemas/students.py`
- Modify: `backend/api/routers/students.py`
- Test: `tests/test_api_read_routes.py`

**Interfaces:**
- Consumes: `get_grading_db() -> DBManager`.
- Produces: `StudentResponse` and `GET /api/students`.

- [x] **Step 1: Write failing students and 404 tests**

Append tests:

```python
def test_students_route_returns_students_sorted_by_db(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager, StudentRecord

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    db.upsert_students([
        StudentRecord("S002", "Bob", "Class 2"),
        StudentRecord("S001", "Alice", "Class 1"),
    ])

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    response = client.get("/api/students")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["student_code"] for item in payload["items"]] == ["S001", "S002"]


def test_missing_session_routes_use_unified_404_error(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    response = client.get("/api/sessions/404", headers={"x-request-id": "rid-missing"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing"
```

- [x] **Step 2: Run students/error tests to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py::test_students_route_returns_students_sorted_by_db tests\test_api_read_routes.py::test_missing_session_routes_use_unified_404_error -q`

Expected: FAIL because student route and 404 handling are not implemented.

- [x] **Step 3: Implement student route and shared session guard**

Use `db.list_students()` for `GET /api/students`. Add a small private `_require_session(db, session_id)` helper in `routers/sessions.py` that raises `ApiError(404, "session_not_found", "Session not found", {"session_id": session_id})`.

- [x] **Step 4: Run tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_api_read_routes.py -q`

Expected: all tests in this file PASS.

### Task 4: Verification And Handoff

**Files:**
- Modify: none unless tests expose a defect.
- Test: `tests/test_api_app.py`, `tests/test_api_read_routes.py`, `tools/smoke_check.py`.

- [x] **Step 1: Run API tests**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py -q`

Expected: all selected tests PASS.

- [x] **Step 2: Run quick smoke**

Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

Expected: static compile OK and database-copy idempotency OK.

- [x] **Step 3: Inspect git diff**

Run: `git diff --stat` and `git status --short --branch`.

Expected: only API code, tests, and this plan are changed; no `user_data/` files staged or intentionally modified by this batch.

- [x] **Step 4: Record next batch**

In the handoff summary, state that Batch A read routes are complete and Batch B should add safe write routes for sessions/students only after another test-first pass.
