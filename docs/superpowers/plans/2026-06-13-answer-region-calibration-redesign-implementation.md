# Answer Region Calibration Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unstable answer-region editor with a responsive, UUID-based focus editor that autosaves recoverable drafts and updates formal regions only through an atomic completion flow.

**Architecture:** Keep SQLite `answer_regions` as the only formal source of truth. Add a recoverable per-session JSON draft and a Streamlit Components v2 editor that owns high-frequency browser interactions while Python owns normalization, sequential question binding, validation, draft persistence, and formal commit semantics. Keep the current V3 editor behind an explicit legacy fallback until the new editor is proven in use.

**Tech Stack:** Python 3.12, SQLite, Streamlit 1.56 Components v2, SVG, browser JavaScript, Node built-in test runner, pytest.

---

## File Structure

Create or modify the following focused files:

```text
db_manager.py
  Migrate answer_regions, persist UUID/mapping metadata, and atomically replace formal regions.

answer_region_models.py
  Normalize regions, assign sequential question IDs, validate commit readiness, and define result models.

answer_region_draft_service.py
  Compute template fingerprints and atomically save/load/discard recoverable region drafts.

answer_region_commit_service.py
  Validate and commit formal regions, generate snapshots, and retain recoverable state on partial failure.

answer_region_editor_component.py
  Register and wrap the Streamlit Components v2 editor and cache template image data URLs.

answer_region_focus_page.py
  Render the focus-mode workflow and coordinate component events with drafts and commits.

components/answer_region_editor/editor.html
components/answer_region_editor/editor.css
components/answer_region_editor/editor.js
components/answer_region_editor/package.json
  Render the responsive SVG editor, toolbar, temporary drawer, and complete-operation event protocol.

components/answer_region_editor/editor_core.test.mjs
  Verify pure JavaScript coordinate and UUID reconciliation behavior.

web_app.py
  Add the focus-mode entry/route and keep the old V3 editor as an explicit fallback.

tests/test_answer_region_db.py
tests/test_answer_region_models.py
tests/test_answer_region_draft_service.py
tests/test_answer_region_commit_service.py
tests/test_answer_region_editor_component.py
tests/test_answer_region_focus_contract.py
  Verify migrations, data rules, draft recovery, transaction behavior, component contract, and page integration.
```

Do not remove `streamlit-drawable-canvas` or the old editor in this implementation. That cleanup is a later, separately approved task.

---

### Task 1: Persist Stable Region Identity and Atomic Formal Replacement

**Files:**
- Modify: `db_manager.py`
- Create: `tests/test_answer_region_db.py`

- [ ] **Step 1: Write the failing migration and persistence tests**

Create `tests/test_answer_region_db.py` with helpers that initialize a temporary database and verify:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager


def _make_session(db: DBManager, root: Path) -> tuple[int, int]:
    session_id = db.create_grading_session("region test", str(root / "rubric.json"), str(root / "answer.json"))
    template_id = db.upsert_session_template(session_id, str(root / "front.png"), str(root / "back.png"))
    return session_id, template_id


def test_initialize_backfills_stable_unique_region_uuid(tmp_path: Path) -> None:
    db_path = tmp_path / "grading.db"
    db = DBManager(db_path)
    db.initialize()
    session_id, template_id = _make_session(db, tmp_path)
    with db._connect() as conn:
        conn.execute("DROP TRIGGER answer_regions_region_uuid_required_insert")
        conn.execute(
            """
            INSERT INTO answer_regions (
                region_uuid, session_id, template_id, page, region_order, x, y, w, h,
                confidence, is_confirmed
            ) VALUES ('', ?, ?, 'front', 1, 1, 2, 30, 40, 0, 0)
            """,
            (session_id, template_id),
        )
        conn.commit()

    db.initialize()
    regions = db.list_answer_regions(session_id)

    assert regions[0]["region_uuid"]
    assert regions[0]["mapping_status"] == "unbound"
    assert regions[0]["multi_region_confirmed"] == 0


def test_replace_answer_regions_atomic_persists_metadata(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db, tmp_path)

    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [{
            "region_uuid": "region-a",
            "page": "front",
            "region_order": 1,
            "x": 10, "y": 20, "w": 300, "h": 120,
            "detected_question_id": "Q1",
            "mapped_question_id": "Q1",
            "mapping_status": "auto",
            "multi_region_confirmed": False,
            "confidence": 0.0,
            "is_confirmed": True,
        }],
        confirmed=True,
    )

    assert db.list_answer_regions(session_id)[0]["region_uuid"] == "region-a"
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1
    assert db.is_template_ready(session_id) is True


def test_replace_answer_regions_atomic_rolls_back_on_duplicate_uuid(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db, tmp_path)
    original = [{
        "region_uuid": "original",
        "page": "front", "region_order": 1,
        "x": 1, "y": 2, "w": 30, "h": 40,
        "mapped_question_id": "Q1", "mapping_status": "manual",
        "multi_region_confirmed": False, "is_confirmed": True,
    }]
    db.replace_answer_regions_atomic(session_id, template_id, original, confirmed=True)

    with pytest.raises(sqlite3.IntegrityError):
        db.replace_answer_regions_atomic(
            session_id,
            template_id,
            [dict(original[0], region_uuid="duplicate"), dict(original[0], region_uuid="duplicate", region_order=2)],
            confirmed=True,
        )

    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == ["original"]
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_answer_region_db.py -q
```

Expected: failures because the new columns and `replace_answer_regions_atomic` do not exist.

- [ ] **Step 3: Add the schema migration and enforcement**

In `db_manager.py`:

1. Import `uuid4` from `uuid`.
2. Define the new columns in the fresh `answer_regions` table:

```sql
region_uuid TEXT NOT NULL UNIQUE,
mapping_status TEXT NOT NULL DEFAULT 'unbound',
multi_region_confirmed INTEGER NOT NULL DEFAULT 0,
```

3. Add `regions_snapshot_pending INTEGER NOT NULL DEFAULT 0` to fresh `session_templates`.
4. For existing databases, call `_ensure_column` for all four columns.
5. Backfill missing `region_uuid` values once:

```python
rows = conn.execute(
    "SELECT id FROM answer_regions WHERE COALESCE(TRIM(region_uuid), '') = ''"
).fetchall()
for row in rows:
    conn.execute(
        "UPDATE answer_regions SET region_uuid = ? WHERE id = ?",
        (str(uuid4()), int(row["id"])),
    )
```

6. Backfill `mapping_status` to `manual` when a mapped question exists and `unbound` otherwise.
7. Create a unique index on `answer_regions(region_uuid)`.
8. Add insert and update triggers that reject null or blank UUID values.

- [ ] **Step 4: Update all existing answer-region reads and writes**

Update `save_answer_regions`, `list_answer_regions`, and `add_answer_region` so they read/write:

```text
region_uuid
mapping_status
multi_region_confirmed
```

When a legacy caller omits `region_uuid`, generate `str(uuid4())`. When it omits `mapping_status`, use `manual` for mapped rows and `unbound` for unmapped rows.

- [ ] **Step 5: Implement atomic formal replacement methods**

Add:

```python
def replace_answer_regions_atomic(
    self,
    session_id: int,
    template_id: int,
    regions: list[dict[str, Any]],
    *,
    confirmed: bool,
) -> None:
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM answer_regions WHERE session_id = ?", (session_id,))
        for region in regions:
            self._insert_answer_region_conn(conn, session_id, template_id, region)
        conn.execute(
            """
            UPDATE session_templates
            SET is_confirmed = ?,
                regions_snapshot_pending = 1,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
            """,
            (1 if confirmed else 0, session_id),
        )
        conn.commit()


def mark_region_snapshot_complete(self, session_id: int) -> None:
    with self._connect() as conn:
        conn.execute(
            """
            UPDATE session_templates
            SET regions_snapshot_pending = 0,
                updated_at = datetime('now','localtime')
            WHERE session_id = ?
            """,
            (session_id,),
        )
        conn.commit()
```

Use a private `_insert_answer_region_conn` helper so `save_answer_regions`, `add_answer_region`, and atomic replacement share one insert contract.

- [ ] **Step 6: Run focused and cleanup regression tests**

Run:

```powershell
python -m pytest tests/test_answer_region_db.py tests/test_session_cleanup.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add db_manager.py tests/test_answer_region_db.py
git commit -m "feat: persist stable answer region identity"
```

---

### Task 2: Build Region Normalization, Sequential Binding, and Validation

**Files:**
- Create: `answer_region_models.py`
- Create: `tests/test_answer_region_models.py`

- [ ] **Step 1: Write failing model and validation tests**

Create tests covering stable normalization, sequential binding, deletion independence, multi-region confirmation, bounds, size, and duplicate UUID:

```python
from answer_region_models import (
    assign_sequential_question_ids,
    normalize_regions,
    validate_regions,
)


def test_assigns_next_unused_scoring_unit_without_rebinding_existing() -> None:
    regions = normalize_regions([
        {"region_uuid": "a", "page": "front", "x": 0, "y": 0, "w": 100, "h": 50,
         "mapped_question_id": "Q1", "mapping_status": "manual"},
        {"region_uuid": "b", "page": "front", "x": 0, "y": 60, "w": 100, "h": 50,
         "mapped_question_id": None, "mapping_status": "unbound"},
    ])

    assigned = assign_sequential_question_ids(regions, ["Q1", "Q2", "Q3(1)"])

    assert assigned[0]["mapped_question_id"] == "Q1"
    assert assigned[0]["mapping_status"] == "manual"
    assert assigned[1]["mapped_question_id"] == "Q2"
    assert assigned[1]["mapping_status"] == "auto"


def test_deleting_one_uuid_never_changes_other_region_identity() -> None:
    regions = normalize_regions([
        {"region_uuid": "a", "page": "front", "x": 1, "y": 2, "w": 30, "h": 40},
        {"region_uuid": "b", "page": "front", "x": 50, "y": 60, "w": 70, "h": 80},
    ])

    remaining = normalize_regions([region for region in regions if region["region_uuid"] != "a"])

    assert remaining == [dict(regions[1], region_order=1)]


def test_unconfirmed_multi_region_group_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([
            {"region_uuid": "a", "page": "front", "x": 1, "y": 2, "w": 30, "h": 40,
             "mapped_question_id": "Q1", "multi_region_confirmed": False},
            {"region_uuid": "b", "page": "back", "x": 1, "y": 2, "w": 30, "h": 40,
             "mapped_question_id": "Q1", "multi_region_confirmed": False},
        ]),
        image_sizes={"front": (1000, 1400), "back": (1000, 1400)},
        template_matches=True,
    )

    assert result.can_commit is False
    assert "unconfirmed_multi_region" in {issue.code for issue in result.issues}
```

Also add tests for:

- blank UUID normalization
- an unbound region
- a region smaller than 12x12
- a region clearly outside the image
- a lightly out-of-bounds region being clamped
- duplicate UUID
- confirmed same-question regions passing validation
- front/back ordering and `region_order` renumbering
- automatic smallest-unit candidates plus manually selectable whole-parent questions
- the special `__student_name__` manual option labeled “姓名识别区域”, excluded from automatic binding

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_models.py -q
```

Expected: import failure because `answer_region_models.py` does not exist.

- [ ] **Step 3: Implement the result models and normalization contract**

Create `answer_region_models.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

MIN_REGION_SIZE = 12
EDGE_SNAP_TOLERANCE = 8
MappingStatus = Literal["auto", "manual", "unbound"]


@dataclass(frozen=True)
class RegionIssue:
    code: str
    message: str
    region_uuid: str | None = None
    question_id: str | None = None


@dataclass(frozen=True)
class RegionValidationResult:
    issues: tuple[RegionIssue, ...]

    @property
    def can_commit(self) -> bool:
        return not self.issues
```

Implement:

```python
def normalize_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]
def assign_sequential_question_ids(regions: list[dict[str, Any]], question_candidates: list[str]) -> list[dict[str, Any]]
def load_question_binding_catalog(rubric_path: Path) -> QuestionBindingCatalog
def validate_regions(
    regions: list[dict[str, Any]],
    *,
    image_sizes: dict[str, tuple[int, int]],
    template_matches: bool,
) -> RegionValidationResult
```

Normalization must copy input dictionaries, preserve UUIDs, create missing UUIDs, use original-image integer coordinates, set mapping status, and renumber front regions before back regions without using list position as identity.

`QuestionBindingCatalog` must expose:

```python
@dataclass(frozen=True)
class QuestionBindingOption:
    value: str
    label: str


@dataclass(frozen=True)
class QuestionBindingCatalog:
    automatic_candidates: tuple[str, ...]
    manual_options: tuple[QuestionBindingOption, ...]
    parent_question_ids: frozenset[str]
```

`load_question_binding_catalog` must preserve the current behavior: automatic candidates use the smallest scoring units and exclude `__student_name__`, while manual options include blank/unbound, “姓名识别区域”, whole parent questions such as `Q10`, and their smallest scoring units with current labels.

- [ ] **Step 4: Implement sequential binding and validation**

Sequential binding must only bind rows whose `mapped_question_id` is blank. It must preserve manual and existing automatic mappings.

Validation must return the following exact issue codes:

```text
template_mismatch
duplicate_uuid
unbound_question
region_too_small
region_out_of_bounds
unconfirmed_multi_region
```

Treat a same-question group as confirmed only when every region in that group has `multi_region_confirmed=True`.

- [ ] **Step 5: Run the model tests**

Run:

```powershell
python -m pytest tests/test_answer_region_models.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add answer_region_models.py tests/test_answer_region_models.py
git commit -m "feat: add answer region validation model"
```

---

### Task 3: Add Recoverable Atomic Draft Storage

**Files:**
- Create: `answer_region_draft_service.py`
- Create: `tests/test_answer_region_draft_service.py`

- [ ] **Step 1: Write failing draft service tests**

Create tests for template fingerprinting, atomic save/load, incompatibility, discard, and corrupt-file quarantine:

```python
from pathlib import Path

from answer_region_draft_service import AnswerRegionDraftService


def test_save_and_load_compatible_draft(tmp_path: Path) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    front.write_bytes(b"front-v1")
    back.write_bytes(b"back-v1")
    service = AnswerRegionDraftService(tmp_path / "session_1")
    fingerprint = service.compute_template_fingerprint(front, back)

    service.save(
        session_id=1,
        template_fingerprint=fingerprint,
        revision=3,
        regions=[{"region_uuid": "a"}],
    )
    result = service.load(expected_template_fingerprint=fingerprint)

    assert result.status == "compatible"
    assert result.draft["revision"] == 3
    assert not service.temp_path.exists()


def test_template_change_marks_draft_incompatible(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session_1")
    service.save(session_id=1, template_fingerprint="old", revision=1, regions=[])

    assert service.load(expected_template_fingerprint="new").status == "incompatible"


def test_corrupt_draft_is_quarantined_without_deleting_formal_data(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session_1")
    service.draft_path.parent.mkdir(parents=True)
    service.draft_path.write_text("{broken", encoding="utf-8")

    result = service.load(expected_template_fingerprint="expected")

    assert result.status == "corrupt"
    assert not service.draft_path.exists()
    assert result.quarantined_path is not None
    assert result.quarantined_path.exists()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_draft_service.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement draft result and service**

Create:

```python
@dataclass(frozen=True)
class DraftLoadResult:
    status: Literal["missing", "compatible", "incompatible", "corrupt"]
    draft: dict[str, Any] | None = None
    quarantined_path: Path | None = None


class AnswerRegionDraftService:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.draft_path = self.session_dir / "region_draft.json"
        self.temp_path = self.session_dir / "region_draft.json.tmp"
```

Add these exact public methods:

```text
compute_template_fingerprint(front_path: Path, back_path: Path) -> str
save(*, session_id: int, template_fingerprint: str, revision: int, regions: list[dict[str, Any]]) -> Path
load(*, expected_template_fingerprint: str) -> DraftLoadResult
discard() -> None
```

Use SHA-256 over page labels and file bytes. `save` writes JSON with:

```text
schema_version
session_id
template_fingerprint
revision
updated_at
regions
```

Write the temporary file with UTF-8 JSON, then call `temp_path.replace(draft_path)`.

- [ ] **Step 4: Implement incompatible and corrupt handling**

- An incompatible draft remains untouched for explicit teacher action.
- A corrupt draft is renamed to `region_draft.corrupt-<timestamp>.json`.
- `discard()` removes the compatible or incompatible draft and any stale temp file, but not quarantined files.

- [ ] **Step 5: Run draft tests**

Run:

```powershell
python -m pytest tests/test_answer_region_draft_service.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add answer_region_draft_service.py tests/test_answer_region_draft_service.py
git commit -m "feat: add recoverable answer region drafts"
```

---

### Task 4: Add Formal Commit and Snapshot Recovery Service

**Files:**
- Create: `answer_region_commit_service.py`
- Create: `tests/test_answer_region_commit_service.py`

- [ ] **Step 1: Write failing commit service tests**

Create a `_make_commit_context(tmp_path)` helper returning a temporary DB, session ID, template ID, compatible draft service, image sizes, and one valid normalized region. Write these tests with exact assertions:

- `test_commit_replaces_formal_regions_and_removes_draft`: formal UUIDs equal the draft UUIDs, `regions_snapshot_pending` is `0`, both snapshot files exist, and `region_draft.json` is gone.
- `test_commit_preserves_existing_workflow_extra_fields`: an existing `front_page_parity` value remains after the commit service updates the workflow stage.
- `test_validation_failure_leaves_formal_regions_and_draft_untouched`: an unbound region returns `committed=False`; formal UUIDs and draft bytes are unchanged.
- `test_database_failure_rolls_back_and_preserves_draft`: monkeypatch `replace_answer_regions_atomic` to raise `sqlite3.OperationalError`; formal UUIDs and draft bytes are unchanged.
- `test_snapshot_failure_keeps_committed_regions_and_pending_recovery`: monkeypatch the atomic JSON writer to raise `OSError`; formal UUIDs change, the draft remains, and `regions_snapshot_pending` is `1`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_commit_service.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement the service and result contract**

Create:

```python
@dataclass(frozen=True)
class AnswerRegionCommitResult:
    committed: bool
    snapshot_pending: bool
    validation: RegionValidationResult
    snapshot_path: Path | None = None
    error: str | None = None


class AnswerRegionCommitService:
    def __init__(self, db: DBManager, session_dir: Path, draft_service: AnswerRegionDraftService) -> None:
        self.db = db
        self.session_dir = Path(session_dir)
        self.draft_service = draft_service
```

Add `commit -> AnswerRegionCommitResult` with keyword-only arguments `session_id`, `template_id`, `regions`, `image_sizes`, and `template_matches`. Add `retry_pending_snapshot -> AnswerRegionCommitResult` with keyword-only `session_id`.

The commit flow must:

1. Normalize and validate.
2. Return without writing if validation fails.
3. Call `replace_answer_regions_atomic`.
4. Read formal regions back from the DB.
5. Write `regions_confirmed_<timestamp>.json` and `workflow_state.json` atomically in the session directory. Preserve the current workflow-state contract: session identity, stage, active paths, `template_ready`, region count, progress, and merged `extra` fields.
6. Clear snapshot pending and discard draft only after both files succeed.
7. Return `committed=True, snapshot_pending=True` if formal DB commit succeeds but snapshot writing fails.

- [ ] **Step 4: Run commit and DB tests**

Run:

```powershell
python -m pytest tests/test_answer_region_commit_service.py tests/test_answer_region_db.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add answer_region_commit_service.py tests/test_answer_region_commit_service.py
git commit -m "feat: commit answer regions atomically"
```

---

### Task 5: Build and Test the Dedicated Browser Editor Core

**Files:**
- Create: `components/answer_region_editor/editor.html`
- Create: `components/answer_region_editor/editor.css`
- Create: `components/answer_region_editor/editor.js`
- Create: `components/answer_region_editor/package.json`
- Create: `components/answer_region_editor/editor_core.test.mjs`

- [ ] **Step 1: Create failing Node tests for pure editor operations**

Create `components/answer_region_editor/editor_core.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import {
  clampRegion,
  imagePointFromClient,
  reconcileRegionsByUuid,
  removeRegionByUuid,
} from "./editor.js";

test("reconcile uses region_uuid instead of object order", () => {
  const previous = [
    { region_uuid: "a", x: 1, y: 2, w: 30, h: 40 },
    { region_uuid: "b", x: 50, y: 60, w: 70, h: 80 },
  ];
  const incoming = [
    { region_uuid: "b", x: 55, y: 65, w: 70, h: 80 },
    { region_uuid: "a", x: 1, y: 2, w: 30, h: 40 },
  ];
  const result = reconcileRegionsByUuid(previous, incoming);
  assert.equal(result.find((item) => item.region_uuid === "b").x, 55);
  assert.equal(result.find((item) => item.region_uuid === "a").x, 1);
});

test("deleting one UUID never rewrites another region", () => {
  const result = removeRegionByUuid(
    [{ region_uuid: "a" }, { region_uuid: "b", x: 20 }],
    "a",
  );
  assert.deepEqual(result, [{ region_uuid: "b", x: 20 }]);
});
```

Also test:

- screen-to-image coordinate conversion
- edge snapping
- clear out-of-bounds preservation for validation
- front/back filtering
- undo and redo snapshots remaining independent copies

- [ ] **Step 2: Run Node tests and verify failure**

Run:

```powershell
node --test components/answer_region_editor/editor_core.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement exported pure functions first**

Create `package.json` so Node treats `editor.js` as an ES module:

```json
{"type":"module"}
```

At the top of `editor.js`, export these exact pure functions:

```text
reconcileRegionsByUuid(previous, incoming)
removeRegionByUuid(regions, regionUuid)
clampRegion(region, imageSize, tolerance = 8)
imagePointFromClient(clientPoint, svgRect, viewBox)
pushHistory(history, regions, limit = 30)
```

Every function must return new arrays/objects and never mutate its input. Reconciliation must use `region_uuid` only and must never infer identity from array position.

- [ ] **Step 4: Implement the Components v2 editor UI**

Use `editor.html` for stable root elements:

```html
<div class="region-editor">
  <header class="toolbar"></header>
  <main class="workspace">
    <div class="canvas-shell"><svg class="canvas"></svg></div>
    <aside class="drawer" hidden></aside>
  </main>
  <footer class="statusbar"></footer>
</div>
```

Use `editor.css` to:

- fill the available width
- keep the toolbar sticky
- make the drawer overlay the right side instead of changing canvas width
- show selected, normal, warning, and invalid region states
- keep controls usable at 1024px and wider

Implement the default `answerRegionEditor(component)` export. It must initialize from `component.data.editor_state`, render SVG image and UUID-keyed rectangles, handle pointer interactions locally, and return a cleanup function that removes all listeners and disconnects its `ResizeObserver`.

Persist one state property after complete operations:

```javascript
setStateValue("editor_state", {
  revision,
  last_operation,
  active_page,
  regions,
  undo_stack,
  redo_stack,
  drawer_open,
});
```

Allowed `last_operation` values:

```text
regions_changed
mapping_changed
multi_region_confirmed
```

Limit both history stacks to 30 snapshots. Python must echo the returned `editor_state` back in the next component data payload so undo/redo survives Streamlit reruns.

Use trigger values, not persistent state, for:

```javascript
setTriggerValue("finish_requested", { revision });
setTriggerValue("exit_requested", { revision });
```

Selection, zoom, pan, pointer movement, and drawer open/close must remain browser-local and must not call `setStateValue` or `setTriggerValue`.

The component must implement:

- front/back switching
- create, select, move, resize, and delete
- undo/redo
- fit width, 100%, zoom in/out, space-drag pan
- sequential mapping display passed from Python
- temporary drawer with mapping select controls and multi-region confirmation
- finish and exit events

When a teacher changes a mapping, set `mapping_status="manual"` for a nonblank value and `mapping_status="unbound"` for the blank option. Confirming a same-question multi-region group must set `multi_region_confirmed=True` on every current region in that question group.

- [ ] **Step 5: Run Node tests**

Run:

```powershell
node --test components/answer_region_editor/editor_core.test.mjs
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 5**

```powershell
git add components/answer_region_editor
git commit -m "feat: add dedicated answer region editor core"
```

---

### Task 6: Register the Streamlit Components v2 Wrapper

**Files:**
- Create: `answer_region_editor_component.py`
- Create: `tests/test_answer_region_editor_component.py`

- [ ] **Step 1: Write failing wrapper contract tests**

Create tests that verify assets load, images become data URLs, and component data is JSON serializable:

```python
import json
from pathlib import Path

from PIL import Image

from answer_region_editor_component import (
    build_answer_region_editor_data,
    load_template_image_data_url,
)


def test_template_image_data_url_is_cached_serializable_png(tmp_path: Path) -> None:
    path = tmp_path / "front.png"
    Image.new("RGB", (100, 200), "white").save(path)

    value = load_template_image_data_url(str(path), path.stat().st_mtime_ns)

    assert value.startswith("data:image/png;base64,")


def test_component_payload_is_json_serializable(tmp_path: Path) -> None:
    payload = build_answer_region_editor_data(
        session_id=1,
        front_image_data_url="data:image/png;base64,front",
        back_image_data_url="data:image/png;base64,back",
        image_sizes={"front": (1000, 1400), "back": (1000, 1400)},
        editor_state={
            "revision": 1,
            "handled_revision": 1,
            "active_page": "front",
            "regions": [],
            "undo_stack": [],
            "redo_stack": [],
            "drawer_open": False,
        },
        automatic_candidates=["Q1", "Q2"],
        manual_question_options=[
            {"value": "", "label": "未绑定"},
            {"value": "__student_name__", "label": "姓名识别区域"},
            {"value": "Q1", "label": "Q1"},
            {"value": "Q10", "label": "Q10（整道大题）"},
        ],
        validation_issues=[],
        draft_revision=1,
        save_status="saved",
        drawer_open_requested=False,
    )

    json.dumps(payload)
```

Also assert `editor.html`, `editor.css`, and `editor.js` are non-empty and the JS contains only one exported default component initializer.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_editor_component.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement component registration and payload builder**

Create `answer_region_editor_component.py`:

```python
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v2 as components_v2

_ASSET_DIR = Path(__file__).resolve().parent / "components" / "answer_region_editor"

_answer_region_editor = components_v2.component(
    "answer_region_editor",
    html=(_ASSET_DIR / "editor.html").read_text(encoding="utf-8"),
    css=(_ASSET_DIR / "editor.css").read_text(encoding="utf-8"),
    js=(_ASSET_DIR / "editor.js").read_text(encoding="utf-8"),
)


@st.cache_data(show_spinner=False)
def load_template_image_data_url(path_value: str, mtime_ns: int) -> str:
    path = Path(path_value)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{encoded}"
```

Implement `build_answer_region_editor_data` with explicit arguments for session ID, two image data URLs, image sizes, the complete `editor_state`, automatic candidates, manual question options, validation issues, draft revision, save status, and whether the drawer should open.

Implement `render_answer_region_editor(*, key: str, data: dict[str, Any]) -> ComponentResult` and mount with state and trigger callbacks:

```python
result = _answer_region_editor(
    key=key,
    data=data,
    default={"editor_state": None},
    height=860,
    on_editor_state_change=lambda: None,
    on_finish_requested_change=lambda: None,
    on_exit_requested_change=lambda: None,
)
return result
```

The focus page must read `result.editor_state`, `result.finish_requested`, and `result.exit_requested`. The next data payload must include the current full `editor_state`, including its undo and redo stacks.

- [ ] **Step 4: Run Python wrapper and Node core tests**

Run:

```powershell
python -m pytest tests/test_answer_region_editor_component.py -q
node --test components/answer_region_editor/editor_core.test.mjs
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 6**

```powershell
git add answer_region_editor_component.py tests/test_answer_region_editor_component.py
git commit -m "feat: wrap answer region editor component"
```

---

### Task 7: Build the Focus-Mode Workflow

**Files:**
- Create: `answer_region_focus_page.py`
- Create: `tests/test_answer_region_focus_contract.py`
- Modify: `web_app.py`

- [ ] **Step 1: Write failing focus-page contract tests**

Create source-level integration tests that prevent regression to the old inline editor:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_app_routes_focus_mode_before_normal_tabs() -> None:
    source = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert "render_answer_region_focus_page" in source
    assert "region_focus_session_id" in source


def test_focus_page_never_calls_immediate_region_delete_methods() -> None:
    source = (ROOT / "answer_region_focus_page.py").read_text(encoding="utf-8")
    assert "delete_answer_region(" not in source
    assert "save_answer_regions(" not in source
    assert "AnswerRegionDraftService" in source
    assert "AnswerRegionCommitService" in source
```

Also test the pure state coordinator:

- Passing one new unbound UUID with automatic candidates `Q1/Q2` maps it to `Q1`, marks it `auto`, preserves the component undo/redo stacks, and does not request the drawer.
- Passing a third new unbound UUID after `Q1/Q2` are used leaves it unbound and requests the drawer.
- Passing the same revision twice returns `handled=False` the second time.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_focus_contract.py -q
```

Expected: import/source assertion failure.

- [ ] **Step 3: Implement the event coordinator**

In `answer_region_focus_page.py`, define a pure coordinator:

```python
@dataclass(frozen=True)
class EditorEventResult:
    regions: list[dict[str, Any]]
    active_page: str
    revision: int
    drawer_open_requested: bool
    handled: bool
    editor_state: dict[str, Any]


def process_editor_state(
    editor_state: dict[str, Any],
    *,
    previous_regions: list[dict[str, Any]],
    question_candidates: list[str],
    image_sizes: dict[str, tuple[int, int]],
    template_matches: bool,
) -> EditorEventResult
```

Rules:

- Compare `revision` with `handled_revision` and return `handled=False` when it was already processed.
- Normalize by UUID.
- Apply sequential binding only to unbound new regions.
- Validate after every complete operation.
- Request drawer opening only for `unbound_question` or `unconfirmed_multi_region`.
- Preserve and echo `undo_stack`, `redo_stack`, active page, and drawer state from the component state.
- Write `handled_revision=revision` into the echoed `editor_state` after successful processing.
- Never write the formal DB.

- [ ] **Step 4: Implement the focus page**

Implement `render_answer_region_focus_page(db: DBManager, *, session_id: int, templates_dir: Path) -> None`.

The page must:

1. Inject focus CSS that hides the global sidebar and expands the main container.
2. Load session/template paths and compute the current fingerprint.
3. Handle draft states:
   - missing: seed a new draft from formal regions
   - compatible: ask once whether to restore draft or reload formal data
   - incompatible: show explicit discard/view actions and do not mount editor
   - corrupt: explain quarantine and load formal data
4. Build image data URLs and image sizes.
5. Load `QuestionBindingCatalog` from the current rubric and pass automatic candidates plus manual parent-question options to the component.
6. Mount the component with a stable session key.
7. Process each new component revision once and write the processed `editor_state` back into the next payload.
8. Autosave only the resulting regions and revision after each complete operation; do not persist undo/redo stacks.
9. On the finish trigger, call `AnswerRegionCommitService.commit`.
10. On the exit trigger, clear `region_focus_session_id` and rerun without deleting the draft.
11. Show a retry action when `regions_snapshot_pending=1`.

- [ ] **Step 5: Route focus mode and add the entry point**

In `web_app.py`:

1. Import `render_answer_region_focus_page`.
2. In `main()`, after DB/sidebar setup and before the normal app tabs:

```python
focus_session_id = st.session_state.get("region_focus_session_id")
if isinstance(focus_session_id, int):
    render_answer_region_focus_page(db, session_id=focus_session_id, templates_dir=TEMPLATE_DIR)
    return
```

3. Replace the normal inline `_render_region_editor_v3(...)` call with:

```python
if st.button("进入专注题框标定", key=f"open_region_focus_{selected_session_id}", type="primary"):
    st.session_state["region_focus_session_id"] = int(selected_session_id)
    st.rerun()
```

4. Keep the V3 editor available only when:

```python
os.getenv("AI_REGION_EDITOR_LEGACY", "").strip() == "1"
```

Render it inside a clearly labeled “旧版题框编辑器（紧急回退）” expander.

- [ ] **Step 6: Run focus, service, and existing source contract tests**

Run:

```powershell
python -m pytest tests/test_answer_region_focus_contract.py tests/test_answer_region_models.py tests/test_answer_region_draft_service.py tests/test_answer_region_commit_service.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 7**

```powershell
git add answer_region_focus_page.py web_app.py tests/test_answer_region_focus_contract.py
git commit -m "feat: add focus answer region calibration flow"
```

---

### Task 8: Integrate Template Replacement and Legacy Compatibility

**Files:**
- Modify: `web_app.py`
- Modify: `tests/test_answer_region_focus_contract.py`
- Modify: `tests/test_session_cleanup.py`

- [ ] **Step 1: Add failing integration tests**

Add tests that verify:

- successful template upload no longer immediately deletes a draft file
- the next focus-page entry detects fingerprint incompatibility
- hard-deleting a session removes `region_draft.json` because it lives under the owned template session directory
- the legacy editor remains reachable only with `AI_REGION_EDITOR_LEGACY=1`

Use a temporary `user_data/templates/session_<id>` directory in cleanup tests.

- [ ] **Step 2: Run integration tests and verify failure**

Run:

```powershell
python -m pytest tests/test_answer_region_focus_contract.py tests/test_session_cleanup.py -q
```

Expected: at least one new assertion fails.

- [ ] **Step 3: Remove obsolete active-editor cache assumptions**

In the template upload success path, stop relying on:

```text
regions_<session_id>
sel_region_idx_<session_id>
region_canvas_version_<session_id>
```

Do not delete `region_draft.json` on upload. The changed template fingerprint must make the draft incompatible on the next focus entry.

Leave old cache clearing only inside the legacy fallback path if it remains necessary there.

- [ ] **Step 4: Verify cleanup and compatibility**

Run:

```powershell
python -m pytest tests/test_answer_region_focus_contract.py tests/test_session_cleanup.py tests/test_answer_region_db.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 8**

```powershell
git add web_app.py tests/test_answer_region_focus_contract.py tests/test_session_cleanup.py
git commit -m "fix: integrate region drafts with template lifecycle"
```

---

### Task 9: End-to-End Verification and Browser Acceptance

**Files:**
- Modify only if verification finds a defect in files from Tasks 1-8
- Do not remove the legacy editor or dependency

- [ ] **Step 1: Run all focused automated tests**

Run:

```powershell
python -m pytest tests/test_answer_region_db.py tests/test_answer_region_models.py tests/test_answer_region_draft_service.py tests/test_answer_region_commit_service.py tests/test_answer_region_editor_component.py tests/test_answer_region_focus_contract.py tests/test_session_cleanup.py -q
node --test components/answer_region_editor/editor_core.test.mjs
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete Python test suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass. If unrelated pre-existing failures exist, record their exact names and verify the focused suite remains green.

- [ ] **Step 3: Start the local app without modifying formal production regions**

Start the app using the existing workspace launcher. Enter the new focus editor for a session, but use only draft operations until the explicit formal-commit acceptance step.

Use the in-app Browser plugin to inspect the rendered page, browser console errors, and responsive behavior.

- [ ] **Step 4: Verify the primary interaction acceptance checklist**

In the browser:

1. Enter focus mode and confirm the global sidebar is hidden.
2. Create at least 10 regions continuously and confirm sequential question binding.
3. Confirm successful automatic binding does not open the drawer.
4. Exhaust available question candidates and confirm the unbound region opens the drawer.
5. Move, resize, delete, undo, and redo regions.
6. Delete the first region and confirm later UUID regions retain their coordinates and mappings.
7. Switch front/back pages and confirm each page retains its regions.
8. Open/close the drawer and resize the browser; confirm regions do not drift.
9. Refresh the page and confirm the recoverable draft prompt appears.
10. Exit focus mode and re-enter; confirm the draft is still recoverable.

- [ ] **Step 5: Verify formal commit and rollback behavior**

Using a disposable test session:

1. Confirm the formal `answer_regions` rows do not change before “完成标定”.
2. Resolve all validation issues and complete calibration.
3. Confirm formal rows now match UUIDs and coordinates from the draft.
4. Confirm the draft is removed and snapshot pending is cleared.
5. Use the automated failure tests as evidence for DB rollback and snapshot-pending recovery; do not deliberately corrupt the user’s live database.

- [ ] **Step 6: Check repository state and unintended data changes**

Run:

```powershell
git diff --check
git status --short --branch
```

Ensure implementation commits contain only intended code/tests/docs. Do not stage or revert unrelated user database changes.

- [ ] **Step 7: Commit any verification fixes**

If browser or full-suite verification required fixes:

```powershell
git add <only-files-fixed-for-this-feature>
git commit -m "fix: stabilize answer region focus editor"
```

If no fixes were required, do not create an empty commit.

---

## Completion Criteria

The implementation is complete only when:

- all focused Python and Node tests pass
- the full Python suite has been run and its result recorded
- the browser acceptance checklist has been completed on the local app
- formal DB rows remain unchanged until explicit completion
- deleting or reordering a region cannot alter another region
- refresh/restart draft recovery works
- responsive resizing and drawer/sidebar changes do not drift coordinates
- the old editor remains available only through the explicit legacy fallback flag
