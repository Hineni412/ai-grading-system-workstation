# Grading Paper Skill Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every grading paper source, let teachers optionally intake and AI-tag it before or after grading, project the knowledge graph through shared `skill_id` values, and reduce the conflict inbox to source-level blocking work.

**Architecture:** Keep grading facts in `grading_system.db` and skill/question-bank facts in `question_bank.db`. Add one integration coordinator that calls existing archive, intake, source-link, skill-link, and diagnosis services; UI code only renders coordinator results. Derive readiness from actual links after each run because the two SQLite databases cannot share a transaction.

**Tech Stack:** Python 3.12, SQLite, Streamlit, pandas, pytest, existing bundled runtime and existing question-bank services. No new external dependency.

## Global Constraints

- Work only in the isolated branch/worktree; do not modify the dirty `main` checkout or its live databases.
- Saving a source paper is local-only: it must not import questions or call AI until the teacher clicks `入库并打标签`.
- Grading, review, score adjustment, and export must remain usable for `not_started`, `partial`, and `failed` states.
- Existing manual links, raw tags, score results, historical task snapshots, and migration evidence must not be deleted.
- Knowledge graph rendering reads stored `skill_id` links and must not submit a second AI matching request.
- A source with one resolved `measured` skill is covered; additional unresolved labels are advisory, not blocking.
- Question-bank source references must accept both `<id>` and `question:<id>` without creating duplicate source groups.
- Preserve existing public call compatibility by adding optional keyword arguments or new methods instead of changing required positional arguments.
- Update `ARCHITECTURE.md` only after implementation reflects the new schema and runtime flow.
- Use the bundled runtime in PowerShell:

```powershell
$PROJECT_ROOT = Split-Path (git rev-parse --path-format=absolute --git-common-dir) -Parent
$PYTHON = Join-Path $PROJECT_ROOT 'runtime\python\python.exe'
$env:AI_GRADING_DATA_DIR = Join-Path (Get-Location) 'user_data'
```

---

## File and responsibility map

- `db_manager.py`: grading-session source metadata and persisted workflow state only.
- `session_config_state.py`: preserve pending source metadata across Streamlit reruns/restarts before an exam exists.
- `question_bank/services/grading_paper_intake_service.py`: archive source bytes and intake one already-saved source without owning exam workflow state.
- `question_bank/services/source_question_link_service.py`: deterministic, paper-scoped source-question linking.
- `session_manager.py`: one shared iterator for effective rubric item identities.
- `integration/grading_paper_skill_workflow_service.py`: cross-database orchestration, state derivation, retries, and run result.
- `integration/skill_conflict_inbox_service.py`: source-level blocking/advisory/historical grouping and coverage.
- `integration/skill_graph_projection.py`: pure conversion of diagnosis profiles into graph rows and source references.
- `integration/diagnosis_profile_service.py`: skill evidence lookup and blocking-only warnings.
- `pages_shared/grading_paper_skill_workflow_component.py`: reusable non-blocking Streamlit status card/button.
- `web_app.py`: bind the saved source, place the shared component, and render the skill graph.
- `pages/知识点整理（高级）.py`: render grouped inbox sections; retain existing resolution actions.

---

### Task 1: Persist the original paper and per-session workflow state

**Files:**

- Modify: `db_manager.py:125-158,612-714`
- Modify: `session_config_state.py:1-55`
- Modify: `question_bank/services/grading_paper_intake_service.py:21-50`
- Modify: `tests/test_session_config_state.py`
- Modify: `tests/test_grading_paper_archive_intake.py`
- Create: `tests/test_grading_paper_source_state.py`

**Interfaces:**

- Produces: `archive_uploaded_grading_paper(filename, content, data_root=None, raw_papers_dir=None) -> ArchivedSourcePaper`.
- Produces: `DBManager.bind_grading_session_source(session_id, *, source_paper_path, source_paper_sha256) -> None`.
- Produces: `DBManager.update_question_bank_sync_state(session_id, *, state, details=None, error=None) -> None`.
- Extends: `DBManager.create_grading_session(..., *, source_paper_path='', source_paper_sha256='') -> int`.
- Extends: `remember_saved_config_for_new_session(..., source_paper_path='', source_paper_sha256='', source_paper_name='') -> bool`.

- [ ] **Step 1: Write failing source-state tests**

Add `tests/test_grading_paper_source_state.py`:

```python
from __future__ import annotations

import json

from db_manager import DBManager


def test_session_source_and_sync_state_are_persistent(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试",
        "rubric.json",
        "answer.json",
        source_paper_path="question_bank/raw_papers/paper_abc.docx",
        source_paper_sha256="a" * 64,
    )

    db.update_question_bank_sync_state(
        session_id,
        state="partial",
        details={"assessment_total": 18, "assessment_resolved": 16},
        error="2 道题缺少技能",
    )

    row = db.get_grading_session(session_id)
    assert row["source_paper_path"] == "question_bank/raw_papers/paper_abc.docx"
    assert row["source_paper_sha256"] == "abc"
    assert row["question_bank_sync_state"] == "partial"
    assert json.loads(row["question_bank_sync_details_json"])["assessment_resolved"] == 16
    assert row["question_bank_sync_error"] == "2 道题缺少技能"


def test_rebinding_changed_source_resets_sync_but_same_hash_preserves_it(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试", "rubric.json", "answer.json",
        source_paper_path="question_bank/raw_papers/first.docx",
        source_paper_sha256="a" * 64,
    )
    db.update_question_bank_sync_state(session_id, state="ready", details={"confirmed": 18})

    db.bind_grading_session_source(
        session_id,
        source_paper_path="question_bank/raw_papers/renamed.docx",
        source_paper_sha256="a" * 64,
    )
    assert db.get_grading_session(session_id)["question_bank_sync_state"] == "ready"

    db.bind_grading_session_source(
        session_id,
        source_paper_path="question_bank/raw_papers/replacement.docx",
        source_paper_sha256="b" * 64,
    )
    row = db.get_grading_session(session_id)
    assert row["question_bank_sync_state"] == "not_started"
    assert row["question_bank_sync_details_json"] == "{}"
    assert row["question_bank_sync_error"] is None


def test_shared_source_archive_is_not_collected_as_session_owned_file(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试",
        "rubric.json",
        "answer.json",
        source_paper_path="question_bank/raw_papers/shared.docx",
        source_paper_sha256="c" * 64,
    )
    assert "question_bank/raw_papers/shared.docx" not in db.collect_session_storage_paths(session_id)
```

Extend `tests/test_session_config_state.py` with a restart assertion for `latest_source_paper_path`, `latest_source_paper_sha256`, and `latest_source_paper_name`. Extend `tests/test_grading_paper_archive_intake.py` to assert the new archive API returns the portable `stored_path`, full SHA-256, and the same physical path for identical bytes.

- [ ] **Step 2: Run the new tests and verify red**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_source_state.py tests/test_session_config_state.py tests/test_grading_paper_archive_intake.py -q
```

Expected: FAIL because the session columns, new DB methods, source-state arguments, and `archive_uploaded_grading_paper` do not exist.

- [ ] **Step 3: Add backward-compatible grading-session fields and methods**

In `DBManager.initialize()`, add:

```python
self._ensure_column(conn, "grading_sessions", "source_paper_path", "TEXT")
self._ensure_column(conn, "grading_sessions", "source_paper_sha256", "TEXT")
self._ensure_column(
    conn,
    "grading_sessions",
    "question_bank_sync_state",
    "TEXT NOT NULL DEFAULT 'not_started'",
)
self._ensure_column(
    conn,
    "grading_sessions",
    "question_bank_sync_details_json",
    "TEXT NOT NULL DEFAULT '{}'",
)
self._ensure_column(conn, "grading_sessions", "question_bank_sync_error", "TEXT")
self._ensure_column(conn, "grading_sessions", "question_bank_sync_updated_at", "TEXT")
```

Extend both grading-session `SELECT` lists with those six columns. Do not add `source_paper_path` to `collect_session_storage_paths()`: the hash archive can be shared by multiple sessions and is owned by question-bank storage, so deleting one grading session must not delete it. Implement:

```python
QUESTION_BANK_SYNC_STATES = {"not_started", "running", "ready", "partial", "failed"}


def bind_grading_session_source(
    self,
    session_id: int,
    *,
    source_paper_path: str,
    source_paper_sha256: str,
) -> None:
    path = str(source_paper_path or "").strip()
    digest = str(source_paper_sha256 or "").strip().lower()
    if not path or len(digest) != 64:
        raise ValueError("source paper path and full SHA-256 are required")
    with self._connect() as conn:
        current = conn.execute(
            "SELECT source_paper_sha256 FROM grading_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        if current is None:
            raise KeyError(f"grading session not found: {session_id}")
        changed = str(current["source_paper_sha256"] or "") != digest
        conn.execute(
            """
            UPDATE grading_sessions
            SET source_paper_path = ?, source_paper_sha256 = ?,
                question_bank_sync_state = CASE WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                question_bank_sync_details_json = CASE WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                question_bank_sync_error = CASE WHEN ? THEN NULL ELSE question_bank_sync_error END,
                question_bank_sync_updated_at = CASE WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (path, digest, changed, changed, changed, changed, int(session_id)),
        )


def update_question_bank_sync_state(
    self,
    session_id: int,
    *,
    state: str,
    details: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    normalized = str(state or "").strip().casefold()
    if normalized not in QUESTION_BANK_SYNC_STATES:
        raise ValueError(f"unsupported question-bank sync state: {state}")
    with self._connect() as conn:
        cursor = conn.execute(
            """
            UPDATE grading_sessions
            SET question_bank_sync_state = ?, question_bank_sync_details_json = ?,
                question_bank_sync_error = ?,
                question_bank_sync_updated_at = datetime('now','localtime'),
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (
                normalized,
                json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True),
                str(error).strip() if error else None,
                int(session_id),
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"grading session not found: {session_id}")
```

Change `create_grading_session` to accept optional keyword-only source values, insert them, and leave existing three-argument calls valid. Validate source values by calling `bind_grading_session_source` after the insert when both values are present.

- [ ] **Step 4: Preserve pending source metadata across restart**

Use these exact state and app-setting keys in `session_config_state.py`:

```python
SOURCE_STATE_KEYS = {
    "latest_source_paper_path": "latest_confirmed_source_paper_path",
    "latest_source_paper_sha256": "latest_confirmed_source_paper_sha256",
    "latest_source_paper_name": "latest_confirmed_source_paper_name",
}
```

Extend `remember_saved_config_for_new_session()` with optional source arguments, write all non-empty values to both state and `app_settings`, and extend `restore_persistent_config_state()` to restore them. Extend `clear_pending_config_for_new_session(state, settings_store=None)` to remove the three in-memory keys and write empty strings to the three app settings when a store is provided.

- [ ] **Step 5: Return archive evidence without breaking the old helper**

In `grading_paper_intake_service.py`, import `ArchivedSourcePaper` and add:

```python
def archive_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> ArchivedSourcePaper:
    return archive_source_bytes(
        filename=filename,
        content=content,
        data_root=data_root,
        raw_papers_dir=raw_papers_dir,
    )


def save_uploaded_grading_paper(
    *,
    filename: str,
    content: bytes,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    return archive_uploaded_grading_paper(
        filename=filename,
        content=content,
        raw_papers_dir=raw_papers_dir,
    ).physical_path
```

- [ ] **Step 6: Run Task 1 tests**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_source_state.py tests/test_session_config_state.py tests/test_grading_paper_archive_intake.py tests/test_session_cleanup.py tests/test_data_transfer_service.py -q
```

Expected: PASS. The cleanup/data-transfer tests confirm deleting one grading session does not delete a shared source archive and the question-bank data package still owns the raw paper.

- [ ] **Step 7: Commit Task 1**

```powershell
git add db_manager.py session_config_state.py question_bank/services/grading_paper_intake_service.py tests/test_grading_paper_source_state.py tests/test_session_config_state.py tests/test_grading_paper_archive_intake.py
git commit -m "feat: persist grading paper source state"
```

---

### Task 2: Restrict source-question matching to the imported paper

**Files:**

- Modify: `question_bank/services/source_question_link_service.py:128-215`
- Modify: `question_bank/services/grading_paper_intake_service.py:79-137`
- Modify: `tests/test_source_question_link_service.py`
- Modify: `tests/test_grading_paper_archive_intake.py`

**Interfaces:**

- Consumes: the existing `candidate_bank_questions` argument.
- Produces: question-number confirmation with `link_method='paper_question_number'` before exact text/similarity.
- Guarantees: intake passes only questions from the imported/reused source to the link service.

- [ ] **Step 1: Add failing paper-scoped matching tests**

Add these cases to `tests/test_source_question_link_service.py`:

```python
def test_unique_question_number_in_imported_paper_confirms_before_global_text(link_service) -> None:
    result = link_service.link_questions_for_session(
        grading_session_id=14,
        source_questions=[{"question_id": "Q17", "stem_summary": "摘要不等于完整题干"}],
        candidate_bank_questions=[
            {"id": 201, "question_number": "17", "question_text": "完整题干", "source_file": "paper-a.docx"},
        ],
    )
    assert result == {"confirmed": 1, "suggested": 0, "unresolved": 0}
    link = link_service.list_links(14)[0]
    assert link["bank_question_id"] == 201
    assert link["link_method"] == "paper_question_number"


def test_duplicate_number_inside_candidate_paper_does_not_auto_confirm(link_service) -> None:
    result = link_service.link_questions_for_session(
        grading_session_id=15,
        source_questions=[{"question_id": "Q17", "stem_summary": "没有可比较的完整题干"}],
        candidate_bank_questions=[
            {"id": 201, "question_number": "17", "question_text": "A"},
            {"id": 202, "question_number": "Q17", "question_text": "B"},
        ],
    )
    assert result == {"confirmed": 0, "suggested": 0, "unresolved": 1}
```

Strengthen `test_grading_paper_intake_creates_source_links` so an unrelated global question with the same text exists, then assert the imported question is linked.

Add a retry case where `import_scanned_papers` returns `status="duplicate"`, the existing source has two questions, one already has a resolved measured skill and one does not. Assert the tagger receives only the unresolved question even though `BatchImportResult.question_count == 0`.

- [ ] **Step 2: Run the source-link tests and verify red**

```powershell
& $PYTHON -m pytest tests/test_source_question_link_service.py tests/test_grading_paper_archive_intake.py -q
```

Expected: FAIL because question numbers are not considered, intake does not pass its `questions` list as the candidate scope, and duplicate intake does not retry unresolved questions.

- [ ] **Step 3: Implement deterministic number matching**

Add:

```python
def _normalize_question_number(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"^(?:第|q|题)+", "", text)
    text = re.sub(r"(?:题)$", "", text)
    return re.sub(r"[\s._、，,:：-]+", "", text)
```

Inside `link_questions_for_session`, after explicit `bank_question_id` handling and before text matching, add:

```python
source_number = _normalize_question_number(source_id)
number_matches = [
    item
    for item in candidates
    if source_number
    and _normalize_question_number(item.get("question_number")) == source_number
]
if len(number_matches) == 1:
    self.confirm_link(
        grading_session_id=grading_session_id,
        source_question_id=source_id,
        bank_question_id=int(number_matches[0]["id"]),
        link_method="paper_question_number",
        evidence={"normalized_question_number": source_number},
    )
    summary["confirmed"] += 1
    continue
```

- [ ] **Step 4: Scope intake linking to imported/reused source questions**

Change the call in `intake_grading_paper_to_question_bank()` to:

```python
link_summary = SourceQuestionLinkService(database_path).link_questions_for_session(
    grading_session_id=grading_session_id,
    source_questions=grading_source_questions,
    candidate_bank_questions=questions,
)
```

Keep the existing `if grading_session_id is not None and grading_source_questions` guard.

Replace the `import_result.question_count` tagging gate with actual source questions that lack a resolved measured skill:

```python
def _questions_needing_skill_links(db_path: Path, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not questions:
        return []
    question_ids = [int(item["id"]) for item in questions]
    placeholders = ",".join("?" for _ in question_ids)
    with connect(db_path) as conn:
        resolved = {
            int(row["question_id"])
            for row in conn.execute(
                f"""
                SELECT DISTINCT question_id FROM question_skill_links
                WHERE question_id IN ({placeholders})
                  AND role = 'measured' AND status = 'resolved'
                """,
                question_ids,
            ).fetchall()
        }
    return [item for item in questions if int(item["id"]) not in resolved]
```

Use `pending_questions = _questions_needing_skill_links(database_path, questions)` and run AI when `run_ai_tagging and pending_questions`. Build contexts from `pending_questions`. This makes a duplicate source import a retry instead of a no-op while avoiding repeat AI calls for resolved questions.

- [ ] **Step 5: Run Task 2 tests**

```powershell
& $PYTHON -m pytest tests/test_source_question_link_service.py tests/test_grading_paper_archive_intake.py tests/test_source_paper_archive_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add question_bank/services/source_question_link_service.py question_bank/services/grading_paper_intake_service.py tests/test_source_question_link_service.py tests/test_grading_paper_archive_intake.py
git commit -m "feat: link grading questions within imported paper"
```

---

### Task 3: Add the idempotent grading-paper workflow coordinator

**Files:**

- Modify: `session_manager.py:2896-2971`
- Modify: `question_bank/services/skill_link_service.py:71-135`
- Create: `integration/grading_paper_skill_workflow_service.py`
- Create: `tests/test_grading_paper_skill_workflow.py`
- Modify: `tests/test_assessment_skill_intake.py`

**Interfaces:**

- Produces: `iter_effective_rubric_items(payload) -> Iterator[tuple[str, Mapping[str, object], Mapping[str, object]]]`.
- Extends: `SkillLinkService.resolve_rubric(..., preserve_existing_measured=False)`.
- Produces: `GradingPaperSkillWorkflowService.status(session_id) -> GradingPaperWorkflowStatus`.
- Produces: `GradingPaperSkillWorkflowService.run(session_id, *, ai_service, max_workers, requests_per_minute, progress_callback=None) -> GradingPaperWorkflowStatus`.
- Produces: `GradingPaperSkillWorkflowService.save_source(session_id, *, filename, content) -> GradingPaperWorkflowStatus` for legacy sessions without a bound source.

- [ ] **Step 1: Extract and test effective rubric identities**

In `tests/test_assessment_skill_intake.py`, add:

```python
def test_effective_rubric_items_use_parts_or_question_fallback() -> None:
    from session_manager import iter_effective_rubric_items

    rows = list(iter_effective_rubric_items(_rubric()))
    assert [item_ref for item_ref, _question, _item in rows] == ["Q1.1", "Q1.2"]

    single = {"questions": [{"question_id": "Q2", "knowledge_name": "一次函数"}]}
    assert [item_ref for item_ref, _question, _item in iter_effective_rubric_items(single)] == ["Q2"]
```

Extract the existing traversal into:

```python
def iter_effective_rubric_items(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, Mapping[str, object], Mapping[str, object]]]:
    rubric = payload.get("rubric") if isinstance(payload.get("rubric"), Mapping) else payload
    questions = rubric.get("questions") if isinstance(rubric, Mapping) else None
    if not isinstance(questions, list):
        return
    for question_index, raw_question in enumerate(questions, start=1):
        if not isinstance(raw_question, Mapping):
            continue
        question_ref = str(
            raw_question.get("question_id")
            or raw_question.get("id")
            or raw_question.get("number")
            or f"Q{question_index}"
        ).strip()
        parts = raw_question.get("parts")
        emitted = False
        if isinstance(parts, list) and parts:
            for part_index, raw_part in enumerate(parts, start=1):
                if not isinstance(raw_part, Mapping):
                    continue
                part_ref = str(
                    raw_part.get("part_id")
                    or raw_part.get("question_id")
                    or f"{question_ref}.{part_index}"
                ).strip()
                emitted = True
                yield part_ref, raw_question, raw_part
        if not emitted:
            yield question_ref, raw_question, raw_question
```

Refactor `iter_rubric_skill_requests()` to loop over this iterator while preserving its existing output.

- [ ] **Step 2: Write failing workflow tests**

Create `tests/test_grading_paper_skill_workflow.py` with fixtures that initialize both temporary databases and a rubric containing `Q1` and `Q2`. Cover these exact cases:

```python
def test_status_is_not_started_when_source_exists_without_links(workflow_fixture) -> None:
    service, session_id, _tagger = workflow_fixture
    status = service.status(session_id)
    assert status.state == "not_started"
    assert status.source_available is True
    assert status.assessment_total == 2
    assert status.assessment_resolved == 2
    assert status.bank_question_total == 0


def test_run_becomes_ready_and_second_run_reuses_existing_rows(workflow_fixture) -> None:
    service, session_id, tagger = workflow_fixture
    first = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)
    second = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)
    assert first.state == "ready"
    assert second.state == "ready"
    assert second.confirmed_source_links == first.confirmed_source_links == 2
    assert service.counts_for_test() == {"papers": 1, "questions": 2, "source_links": 2}


def test_partial_results_survive_tagging_failure_and_are_retryable(workflow_fixture) -> None:
    service, session_id, tagger = workflow_fixture
    tagger.fail_question_numbers = {"2"}
    partial = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)
    assert partial.state == "partial"
    assert partial.bank_questions_resolved == 1
    tagger.fail_question_numbers.clear()
    assert service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60).state == "ready"


def test_interrupted_running_state_is_recovered_as_retryable_failure(workflow_fixture) -> None:
    service, session_id, _tagger = workflow_fixture
    service.grading_db.update_question_bank_sync_state(session_id, state="running")
    status = service.status(session_id)
    assert status.state == "failed"
    assert "中断" in status.error


def test_legacy_session_can_save_source_without_running_ai(workflow_without_source) -> None:
    service, session_id, tagger = workflow_without_source
    status = service.save_source(session_id, filename="补传试卷.docx", content=b"source-bytes")
    assert status.state == "not_started"
    assert status.source_available is True
    assert tagger.call_count == 0
```

The fixture may expose `counts_for_test()` through a test-only subclass; production code must not add that method.

- [ ] **Step 3: Run workflow tests and verify red**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_skill_workflow.py tests/test_assessment_skill_intake.py -q
```

Expected: FAIL because the iterator, coordinator, and `preserve_existing_measured` behavior do not exist.

- [ ] **Step 4: Preserve existing measured links during a backfill run**

Extend `SkillLinkService.resolve_rubric()` with `preserve_existing_measured: bool = False`. Before resolving an item, when preservation is enabled, query:

```python
existing_measured = conn.execute(
    """
    SELECT COUNT(*) FROM assessment_item_skills
    WHERE grading_session_id = ? AND source_question_id = ?
      AND role = 'measured' AND status = 'resolved'
    """,
    (str(grading_session_id), item_ref),
).fetchone()[0]
```

If nonzero, count the item as resolved and skip replacement. Default behavior remains unchanged for current callers. The workflow coordinator must call with `preserve_existing_measured=True` so an intake retry never overwrites manual/admin links.

- [ ] **Step 5: Implement the coordinator data model and state derivation**

Create `integration/grading_paper_skill_workflow_service.py` with these public types:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from db_manager import DBManager
from path_manager import resolve_stored_file_path
from question_bank.database.schema import connect
from question_bank.services.grading_paper_intake_service import (
    archive_uploaded_grading_paper,
    intake_grading_paper_to_question_bank,
)
from question_bank.services.skill_link_service import SkillLinkService
from session_manager import iter_effective_rubric_items


@dataclass(frozen=True, slots=True)
class GradingPaperWorkflowStatus:
    session_id: int
    session_name: str
    state: str
    source_available: bool
    source_paper_path: str
    source_paper_sha256: str
    source_question_total: int
    confirmed_source_links: int
    suggested_source_links: int
    bank_question_total: int
    bank_questions_resolved: int
    assessment_total: int
    assessment_resolved: int
    error: str = ""

    @property
    def missing_source_links(self) -> int:
        return max(0, self.source_question_total - self.confirmed_source_links)

    @property
    def missing_assessment_items(self) -> int:
        return max(0, self.assessment_total - self.assessment_resolved)

    def details(self) -> dict[str, object]:
        return asdict(self)


def _derive_state(
    *,
    source_available: bool,
    source_total: int,
    confirmed_links: int,
    bank_total: int,
    bank_resolved: int,
    assessment_total: int,
    assessment_resolved: int,
    persisted_state: str,
    persisted_error: str,
) -> tuple[str, str]:
    ready = (
        source_available
        and source_total > 0
        and confirmed_links == source_total
        and bank_total >= confirmed_links
        and bank_resolved == bank_total
        and assessment_total > 0
        and assessment_resolved == assessment_total
    )
    if ready:
        return "ready", ""
    progress = confirmed_links > 0 or bank_total > 0 or bank_resolved > 0
    if progress:
        return "partial", persisted_error
    if persisted_state == "running":
        return "failed", "上次题库处理被中断，请重试。"
    if persisted_state == "failed":
        return "failed", persisted_error
    return "not_started", ""
```

Implement `GradingPaperSkillWorkflowService.status()` by loading the session/rubric, collecting top-level source question IDs, collecting effective rubric item refs, reading `grading_question_links`, reading active questions whose `source_file` equals the stored portable source path, reading their resolved measured bank links, and reading resolved measured assessment links for the session. This makes pre-existing assessment links visible in graph completeness without falsely treating them as evidence that intake started. Resolve the stored source with `resolve_stored_file_path(..., data_root=self.data_root)` and never trust a missing path.

Implement legacy source binding without AI:

```python
def save_source(self, session_id: int, *, filename: str, content: bytes) -> GradingPaperWorkflowStatus:
    if self.grading_db.get_grading_session(int(session_id)) is None:
        raise KeyError(f"grading session not found: {session_id}")
    archived = archive_uploaded_grading_paper(
        filename=filename,
        content=content,
        data_root=self.data_root,
        raw_papers_dir=self.data_root / "question_bank" / "raw_papers",
    )
    self.grading_db.bind_grading_session_source(
        int(session_id),
        source_paper_path=archived.stored_path,
        source_paper_sha256=archived.sha256,
    )
    return self.status(int(session_id))
```

Implement `run()` with this exact control flow:

```python
def run(
    self,
    session_id: int,
    *,
    ai_service: Any,
    max_workers: int,
    requests_per_minute: int,
    progress_callback: Callable[[int, int, int, Any], None] | None = None,
) -> GradingPaperWorkflowStatus:
    session, rubric, source_path = self._required_inputs(session_id)
    self.grading_db.update_question_bank_sync_state(session_id, state="running")
    try:
        intake_grading_paper_to_question_bank(
            source_file=source_path,
            db_path=self.question_bank_db_path,
            run_ai_tagging=True,
            ai_service=ai_service,
            tagging_max_workers=max_workers,
            tagging_requests_per_minute=requests_per_minute,
            tagging_progress_callback=progress_callback,
            grading_session_id=session_id,
            grading_source_questions=list(rubric.get("questions") or []),
            data_root=self.data_root,
            raw_papers_dir=self.data_root / "question_bank" / "raw_papers",
        )
        SkillLinkService(self.question_bank_db_path).resolve_rubric(
            str(session_id),
            rubric,
            preserve_existing_measured=True,
        )
    except Exception as exc:
        actual = self._compute_status(session_id, persisted_state="failed", persisted_error=str(exc))
        fallback = "partial" if (
            actual.confirmed_source_links
            or actual.bank_question_total
            or actual.bank_questions_resolved
        ) else "failed"
        self.grading_db.update_question_bank_sync_state(
            session_id,
            state=fallback,
            details=actual.details(),
            error=str(exc),
        )
        return self.status(session_id)
    actual = self._compute_status(session_id, persisted_state="not_started", persisted_error="")
    self.grading_db.update_question_bank_sync_state(
        session_id,
        state=actual.state,
        details=actual.details(),
        error=actual.error or None,
    )
    return self.status(session_id)
```

Do not catch and discard input errors: `_required_inputs` must raise `KeyError` for a missing session and `FileNotFoundError` for a missing rubric/source. The UI will display those errors.

- [ ] **Step 6: Run Task 3 tests**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_skill_workflow.py tests/test_assessment_skill_intake.py tests/test_question_skill_dual_write.py tests/test_source_question_link_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add session_manager.py question_bank/services/skill_link_service.py integration/grading_paper_skill_workflow_service.py tests/test_grading_paper_skill_workflow.py tests/test_assessment_skill_intake.py
git commit -m "feat: coordinate grading paper skill intake"
```

---

### Task 4: Group conflict inbox work by source and severity

**Files:**

- Create: `integration/skill_conflict_inbox_service.py`
- Create: `tests/test_skill_conflict_inbox_service.py`
- Modify: `pages/知识点整理（高级）.py:112-272`
- Modify: `tests/test_skill_catalog_ui.py`

**Interfaces:**

- Produces: `SkillConflictInboxService.summary() -> ConflictInboxSummary`.
- Produces: `ConflictInboxSummary.blocking`, `.advisory`, `.historical`, and `.coverage`.
- Consumes: `SkillCatalogService` only for candidate display and existing resolution writes; it does not duplicate resolution logic.

- [ ] **Step 1: Write failing source-group classification tests**

Create `tests/test_skill_conflict_inbox_service.py`. Seed:

- active question 1 with a resolved measured link plus conflicts using `1` and `question:1`;
- active question 2 with conflicts and no measured link;
- deleted question 3 with a conflict;
- active assessment item `7:Q1` with a resolved measured link plus a conflict;
- active assessment item `7:Q2` with a conflict and no link;
- deleted grading session 8 with `8:Q1` conflict.

Assert:

```python
summary = SkillConflictInboxService(qb_path, grading_path).summary()

assert [group.source_key for group in summary.blocking] == ["assessment:7:Q2", "question:2"]
assert [group.source_key for group in summary.advisory] == ["assessment:7:Q1", "question:1"]
assert [group.source_key for group in summary.historical] == ["assessment:8:Q1", "question:3"]
assert len(next(group for group in summary.advisory if group.source_key == "question:1").conflicts) == 2
assert summary.coverage["question_bank"] == {
    "total": 2,
    "resolved": 1,
    "blocking": 1,
    "advisory": 1,
}
```

- [ ] **Step 2: Run the inbox service test and verify red**

```powershell
& $PYTHON -m pytest tests/test_skill_conflict_inbox_service.py -q
```

Expected: FAIL because `SkillConflictInboxService` does not exist.

- [ ] **Step 3: Implement normalized source groups**

Create immutable `ConflictSourceGroup` and `ConflictInboxSummary` dataclasses. Use these normalization rules:

```python
def _question_source_id(source_ref: object) -> int | None:
    for value in reversed(str(source_ref or "").split(":")):
        try:
            question_id = int(value)
        except ValueError:
            continue
        return question_id if question_id > 0 else None
    return None


def _assessment_source(source_ref: object) -> tuple[int, str] | None:
    left, separator, right = str(source_ref or "").partition(":")
    if not separator or not right:
        return None
    try:
        session_id = int(left)
    except ValueError:
        return None
    return (session_id, right) if session_id > 0 else None
```

Load all open conflicts once. Load active/deleted question IDs, resolved measured question IDs, active/deleted grading session IDs, and resolved measured assessment keys in batched queries. Classify a source as historical when its question/session is missing or deleted; otherwise classify it advisory when a resolved measured link exists and blocking when none exists. Sort groups by source type, session/question identity, then first conflict ID.

Coverage must use active questions and effective active assessment source keys. For every active grading session with a readable rubric, use `iter_effective_rubric_items()` to enumerate all source keys, including items that have neither a link nor a conflict. If a legacy rubric file is missing, fall back to the union of known assessment links/conflicts for that session and expose the missing-file fact in service warnings. Count advisory as a subset of resolved, never add it to total.

- [ ] **Step 4: Replace row-level inbox rendering**

In `pages/知识点整理（高级）.py`, keep the catalog service for resolution actions and instantiate:

```python
def _inbox_service() -> SkillConflictInboxService:
    paths = get_path_manager()
    return SkillConflictInboxService(paths.qb_db_path, paths.db_path)
```

Render three sections:

```python
summary = inbox_service.summary()
st.subheader(f"必须处理 · {len(summary.blocking)} 道题")
st.caption("这里只统计整道题还没有可用训练技能的来源。")
_render_conflict_groups(catalog_service, summary.blocking, key_prefix="blocking")

with st.expander(f"可选检查 · {len(summary.advisory)} 道题", expanded=False):
    st.caption("这些题已经有可用技能；附加词条不会阻断知识图谱或训练推荐。")
    _render_conflict_groups(catalog_service, summary.advisory, key_prefix="advisory")

with st.expander(f"历史记录 · {len(summary.historical)} 道题", expanded=False):
    _render_conflict_groups(catalog_service, summary.historical, key_prefix="historical", read_only=True)
```

One group card shows source context once, then all raw labels. Resolution buttons still call `SkillCatalogService.resolve_conflict`, `create_local_from_conflict`, or `ignore_conflict` with the selected conflict ID. Historical cards expose no write button.

- [ ] **Step 5: Update coverage cards and UI contract tests**

Use `summary.coverage` for the page cards and rename the metrics to `总数`, `已覆盖`, `必须处理`, and `可选检查`. Update `tests/test_skill_catalog_ui.py` to require those strings and forbid `待处理问题 · {len(conflicts)}` row-count rendering.

- [ ] **Step 6: Run Task 4 tests**

```powershell
& $PYTHON -m pytest tests/test_skill_conflict_inbox_service.py tests/test_skill_catalog_ui.py tests/test_skill_link_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add integration/skill_conflict_inbox_service.py pages/知识点整理（高级）.py tests/test_skill_conflict_inbox_service.py tests/test_skill_catalog_ui.py
git commit -m "feat: group skill conflicts by blocking source"
```

---

### Task 5: Project the global knowledge graph through shared skills

**Files:**

- Modify: `integration/diagnosis_profile_service.py:192-354`
- Create: `integration/skill_graph_projection.py`
- Create: `tests/test_skill_graph_projection.py`
- Modify: `tests/test_diagnosis_profile_service.py`

**Interfaces:**

- Produces: `DiagnosisProfileService.skill_evidence(*, skill_id, student_ids=(), session_ids=()) -> list[dict[str, Any]]`.
- Produces: `DiagnosisProfileService.build_skill_profiles(*, scope, exam_scope) -> dict[str, Any]`, an explicit public skill-mode read independent of legacy/shadow settings.
- Produces: `build_skill_graph_rows(profile) -> list[dict[str, Any]]`.
- Changes: unresolved warning count includes only assessment sources without a resolved measured link.

- [ ] **Step 1: Write failing diagnosis/projection tests**

In `tests/test_diagnosis_profile_service.py`, seed an assessment item that has both a resolved measured link and an open conflict, plus a second item with only an open conflict. Assert `unresolved_count == 1`, not 2. Add a test that `skill_evidence(skill_id=...)` returns only grading evidence whose `(session_id, question_id)` is linked to that skill and obeys student/session filters.

Also set `recommendation_read_mode` to `legacy` and assert `build_skill_profiles(...)` still returns `diagnosis_identity == "skill"`; the global graph must not silently fall back to legacy identities.

Create `tests/test_skill_graph_projection.py`:

```python
from integration.skill_graph_projection import build_skill_graph_rows


def test_projection_uses_skill_identity_and_keeps_source_refs() -> None:
    profile = {
        "students": [{
            "student_id": "5",
            "student_code": "S005",
            "student_name": "测试学生",
            "weak_points": [{
                "skill_id": 12,
                "skill_name": "角平分线性质",
                "topic_name": "三角形",
                "mastery": 0.625,
                "deduction_count": 2,
                "evidence_count": 3,
                "actionable_reasons": ["辅助线缺失"],
                "source_question_refs": [{"session_id": 7, "question_id": "Q12(1)"}],
            }],
        }],
    }

    assert build_skill_graph_rows(profile) == [{
        "student_id": 5,
        "student_code": "S005",
        "student_name": "测试学生",
        "skill_id": 12,
        "knowledge_id": "skill:12",
        "knowledge_label": "角平分线性质",
        "topic_name": "三角形",
        "weighted_score_rate": 62.5,
        "deduction_count": 2,
        "item_count": 3,
        "sample_reasons": "辅助线缺失",
        "source_question_refs": [{"session_id": 7, "question_id": "Q12(1)"}],
    }]
```

- [ ] **Step 2: Run projection tests and verify red**

```powershell
& $PYTHON -m pytest tests/test_skill_graph_projection.py tests/test_diagnosis_profile_service.py -q
```

Expected: FAIL because the projection module/evidence method do not exist and open conflicts are counted indiscriminately.

- [ ] **Step 3: Count only blocking assessment conflicts**

Replace `_open_assessment_conflict_count()` with a source-key comparison:

```python
def _open_assessment_conflict_count(self, session_ids: list[int]) -> int:
    if not session_ids:
        return 0
    resolved = {
        (int(link["grading_session_id"]), str(link["source_question_id"]))
        for link in self.skill_links.assessment_links_for_sessions([str(value) for value in session_ids])
        if str(link.get("role")) == "measured" and str(link.get("status")) == "resolved"
    }
    prefixes = tuple(f"{session_id}:" for session_id in session_ids)
    with connect(self.question_bank_db_path) as conn:
        refs = {
            str(row["source_ref"])
            for row in conn.execute(
                """
                SELECT DISTINCT source_ref FROM skill_resolution_conflicts
                WHERE source_type = 'assessment_item' AND state = 'open'
                """
            ).fetchall()
            if str(row["source_ref"]).startswith(prefixes)
        }
    blocking = set()
    for ref in refs:
        session_text, item_ref = ref.split(":", 1)
        key = (int(session_text), item_ref)
        if key not in resolved:
            blocking.add(key)
    return len(blocking)
```

Add the explicit public method:

```python
def build_skill_profiles(
    self,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
) -> dict[str, Any]:
    return self._build_skill_profiles(scope=scope, exam_scope=exam_scope)
```

- [ ] **Step 4: Add skill evidence lookup**

Implement `skill_evidence()` by collecting measured assessment links for the selected sessions, retaining keys whose `skill_id` matches, then filtering `DBManager.get_active_assessment_evidence()` by those keys. Return sorted rows with existing `max_score`, image paths, deduction fields, and score-rate enrichment unchanged.

- [ ] **Step 5: Implement the pure graph projection**

Create `integration/skill_graph_projection.py` with `build_skill_graph_rows()` exactly matching the test shape. Skip malformed students/weak points without a positive integer `skill_id`; do not manufacture an `UNKNOWN` skill. Join actionable reasons with `；` and round mastery percent to two decimals.

- [ ] **Step 6: Run Task 5 tests**

```powershell
& $PYTHON -m pytest tests/test_skill_graph_projection.py tests/test_diagnosis_profile_service.py tests/test_unified_skill_recommendation.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```powershell
git add integration/diagnosis_profile_service.py integration/skill_graph_projection.py tests/test_skill_graph_projection.py tests/test_diagnosis_profile_service.py
git commit -m "feat: project knowledge graph through shared skills"
```

---

### Task 6: Add the optional workflow button and partial graph UI

**Files:**

- Create: `pages_shared/grading_paper_skill_workflow_component.py`
- Modify: `web_app.py:54-121,2273-2920,3148-3420,4051-4192,4453-4588,9380-9415`
- Create: `tests/test_grading_paper_skill_workflow_ui.py`
- Modify: `tests/test_session_config_state.py`

**Interfaces:**

- Consumes: Task 1 source-state APIs and Task 3 workflow service.
- Produces: `render_grading_paper_skill_workflow_card(service, session_id, *, key_prefix, ai_service_factory, max_workers, requests_per_minute, compact=False) -> GradingPaperWorkflowStatus`.
- Uses: Task 5 diagnosis/profile projection for the global graph and skill evidence details.

- [ ] **Step 1: Write failing UI contract tests**

Create `tests/test_grading_paper_skill_workflow_ui.py` with AST/source assertions that require:

```python
def test_source_is_archived_on_config_save_without_running_intake() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    save_block = source[source.index('if st.button("确认保存评分依据"'):source.index("st.divider()", source.index('if st.button("确认保存评分依据"')))]
    assert "archive_uploaded_grading_paper" in save_block
    assert "copy_and_intake_uploaded_grading_paper" not in save_block
    assert "intake_grading_paper_to_question_bank" not in save_block


def test_optional_card_is_rendered_in_config_grading_and_graph_flows() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    assert source.count("render_grading_paper_skill_workflow_card(") >= 3
    component = COMPONENT.read_text(encoding="utf-8")
    for text in ("保存原始试卷", "入库并打标签", "可稍后处理，不影响批改", "部分完成", "重新处理"):
        assert text in component


def test_global_graph_uses_skill_profiles_not_legacy_weak_point_rows() -> None:
    section = WEB_APP.read_text(encoding="utf-8").split("def render_global_weak_points_tab", 1)[1].split("def _render_active_session_multiselect", 1)[0]
    assert "DiagnosisProfileService" in section
    assert "build_skill_graph_rows" in section
    assert "get_active_global_weak_points" not in section
    assert "知识图谱完整度" in section
```

Also assert graph links use `kg_skill_id`, and `_read_graph_detail_query()` returns `skill_id` as an integer.

- [ ] **Step 2: Run UI tests and verify red**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_skill_workflow_ui.py tests/test_session_config_state.py -q
```

Expected: FAIL because the component and new flow do not exist.

- [ ] **Step 3: Create the reusable Streamlit workflow card**

Create `pages_shared/grading_paper_skill_workflow_component.py`. The function must:

- call `service.status(session_id)` on every render;
- show `未处理`, `处理中`, `已完成`, `部分完成`, or `失败` in Chinese;
- display counts from the status object;
- use a primary button only for `not_started`; use `重新处理` for `partial`/`failed`;
- instantiate AI only inside the button branch via `ai_service_factory()`;
- pass a progress callback to `service.run()` and update `st.progress`;
- catch exceptions, display `st.error`, and keep the page usable;
- call `st.rerun()` after a completed attempt;
- never call `st.stop()`.

When `status.source_available` is false, render a `.docx`/`.pdf` uploader and `保存原始试卷` button. The save branch must call `service.save_source(...)`, show `原始试卷已保存，尚未入库`, and rerun. It must not construct an AI service.

Use this button condition:

```python
button_label = "入库并打标签" if status.state == "not_started" else "重新处理"
can_run = status.state in {"not_started", "partial", "failed"} and status.source_available
if st.button(button_label, key=f"{key_prefix}_run", disabled=not can_run, type="primary"):
    progress = st.progress(0.0, text="准备处理试卷…")

    def on_progress(done: int, total: int, question_id: int, result: object) -> None:
        ratio = done / max(total, 1)
        progress.progress(ratio, text=f"题库打标签 {done}/{total} · 题目 {question_id}")

    next_status = service.run(
        session_id,
        ai_service=ai_service_factory(),
        max_workers=max_workers,
        requests_per_minute=requests_per_minute,
        progress_callback=on_progress,
    )
    if next_status.state == "ready":
        st.success("试卷已入库，知识图谱可直接使用统一技能。")
    elif next_status.state == "partial":
        st.warning("已保留成功结果，仍有部分题目待补充。")
    else:
        st.error(next_status.error or "题库处理失败，请重试。")
    st.rerun()
```

- [ ] **Step 4: Save source bytes before creating/updating a session**

In the `确认保存评分依据` branch, immediately after `save_generated_config`, call:

```python
source_archive = archive_uploaded_grading_paper(
    filename=str(st.session_state.generated_doc_name or f"grading_paper_{ts}.docx"),
    content=bytes(st.session_state.get("generated_doc_bytes") or b""),
    data_root=APP_DATA_DIR,
    raw_papers_dir=APP_DATA_DIR / "question_bank" / "raw_papers",
)
```

Pass `source_archive.stored_path`, `.sha256`, and the original display name to `remember_saved_config_for_new_session`. Remove the old `sync_to_question_bank` checkbox and its immediate intake block.

When creating a session, pass the restored source path/hash to `DBManager.create_grading_session`. When updating an existing session, call `bind_grading_session_source`. Pass `settings_store=db` to `clear_pending_config_for_new_session` after successful creation.

- [ ] **Step 5: Place the same optional component in three flows**

Construct one service through a small helper:

```python
def _grading_paper_skill_workflow_service(db: DBManager) -> GradingPaperSkillWorkflowService:
    return GradingPaperSkillWorkflowService(
        grading_db_path=db.db_path,
        question_bank_db_path=question_bank_db_path(),
        data_root=APP_DATA_DIR,
    )
```

Render the component:

1. below the current-session card in `render_config_and_session_tab`;
2. below the readiness warning in `render_grading_tab` without changing grading-button disabled conditions;
3. once per incomplete selected session in `render_global_weak_points_tab`, with `compact=True` and unique keys.

Use the existing `qb_tagging_workers` and `qb_tagging_rpm` session settings. The component receives `AITaggingService` as the factory; it must not read API secrets itself.

- [ ] **Step 6: Replace legacy graph aggregation and detail routing**

In `render_global_weak_points_tab`, build the student scope from the current score-rate filter and selected student, then call:

```python
diagnosis_service = DiagnosisProfileService(db.db_path, question_bank_db_path())
diagnosis = diagnosis_service.build_skill_profiles(
    scope={
        "mode": "selected" if student_id is not None else "class",
        "student_ids": [str(student_id)] if student_id is not None else [str(value) for value in eligible_ids],
        "class_id": "",
    },
    exam_scope={"mode": "manual", "session_ids": selected_session_ids},
)
rows = build_skill_graph_rows(diagnosis)
```

Render completeness from `GradingPaperSkillWorkflowService.status()` for every selected session. Sum `assessment_total` and `assessment_resolved`, show `知识图谱完整度：X / Y 道评分题`, and list incomplete session names/reasons in a yellow warning.

Change graph leaf links to `kg_skill_id=<positive int>`. Extend `_read_graph_detail_query()` to parse it. Replace `_render_knowledge_wrong_detail` calls for skill nodes with `_render_skill_wrong_detail`, which calls `diagnosis_service.skill_evidence(skill_id=..., student_ids=..., session_ids=...)` and reuses the existing preview/card rendering loop. Display the catalog skill name, never `skill:<id>`.

- [ ] **Step 7: Run Task 6 tests**

```powershell
& $PYTHON -m pytest tests/test_grading_paper_skill_workflow_ui.py tests/test_session_config_state.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py tests/test_training_recommendation_ui.py tests/test_web_app_workflow_lock_contract.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 6**

```powershell
git add pages_shared/grading_paper_skill_workflow_component.py web_app.py tests/test_grading_paper_skill_workflow_ui.py tests/test_session_config_state.py
git commit -m "feat: add optional paper intake and partial skill graph"
```

---

### Task 7: Update architecture, run migrations safely, and verify the complete workflow

**Files:**

- Modify: `ARCHITECTURE.md`
- Modify: `README_工作机使用说明.md`
- Test: all files listed in Tasks 1-6 plus the project core suite

**Interfaces:**

- Documents the final grading-session fields, cross-database workflow, source archive, and skill graph read path.
- Does not describe planned behavior that the implemented code does not provide.

- [ ] **Step 1: Update the actual architecture document**

Add the following verified facts after implementation:

- grading sessions own portable source-paper path/hash and cached question-bank workflow state;
- source bytes are archived locally before optional intake;
- `GradingPaperSkillWorkflowService` coordinates the two databases without cross-database transaction claims;
- `grading_question_links` binds source questions to bank questions;
- `assessment_item_skills` is the graph identity source and `question_skill_links` is the recommendation candidate identity source;
- global knowledge graph uses `DiagnosisProfileService` and `skill_id`;
- conflict inbox counts blocking source items, not raw conflict rows;
- failure/retry behavior and non-blocking grading boundary.

Update the document verification date and evidence list only after the tests below pass.

Update `README_工作机使用说明.md` with the exact user flow: the original paper is saved with the scoring basis; `入库并打标签` is optional and available before/during/after grading; skipping it leaves grading/export available but the graph shows incomplete coverage; retry preserves successful questions.

- [ ] **Step 2: Run schema initialization against copied databases**

Use temporary copies, never live `user_data/databases`:

```powershell
$TEMP_DATA = Join-Path $env:TEMP "grading-skill-workflow-schema-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path (Join-Path $TEMP_DATA 'databases') -Force | Out-Null
$SOURCE_GRADING = Join-Path $PROJECT_ROOT 'user_data\databases\grading_system.db'
$SOURCE_QB = Join-Path $PROJECT_ROOT 'user_data\databases\question_bank.db'
$TARGET_GRADING = Join-Path $TEMP_DATA 'databases\grading_system.db'
$TARGET_QB = Join-Path $TEMP_DATA 'databases\question_bank.db'
& $PYTHON -c "import sqlite3,sys; src=sqlite3.connect('file:'+sys.argv[1].replace('\\','/')+'?mode=ro',uri=True); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" $SOURCE_GRADING $TARGET_GRADING
& $PYTHON -c "import sqlite3,sys; src=sqlite3.connect('file:'+sys.argv[1].replace('\\','/')+'?mode=ro',uri=True); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" $SOURCE_QB $TARGET_QB
$env:AI_GRADING_DATA_DIR = $TEMP_DATA
& $PYTHON -c "from db_manager import DBManager; from path_manager import get_path_manager; p=get_path_manager(); DBManager(p.db_path).initialize(); print('schema-ok')"
Remove-Item -LiteralPath $TEMP_DATA -Recurse -Force
$env:AI_GRADING_DATA_DIR = Join-Path (Get-Location) 'user_data'
```

Expected: `schema-ok`; the live databases remain unchanged.

- [ ] **Step 3: Run the focused regression suite**

```powershell
& $PYTHON -m pytest `
  tests/test_grading_paper_source_state.py `
  tests/test_grading_paper_archive_intake.py `
  tests/test_source_question_link_service.py `
  tests/test_grading_paper_skill_workflow.py `
  tests/test_skill_conflict_inbox_service.py `
  tests/test_skill_catalog_ui.py `
  tests/test_assessment_skill_intake.py `
  tests/test_diagnosis_profile_service.py `
  tests/test_skill_graph_projection.py `
  tests/test_grading_paper_skill_workflow_ui.py `
  tests/test_training_recommendation_ui.py `
  tests/test_session_cleanup.py `
  tests/test_data_transfer_service.py -q
```

Expected: PASS with no failed test.

- [ ] **Step 4: Run the project core suite**

```powershell
& $PYTHON -m pytest test_answer_normalizer.py tests/test_objective_batch_recognition_service.py tests/test_objective_escalation.py tests/test_prompt_injection_guard.py tests/test_portable_path_resolution.py -q
```

Expected: 41 tests pass. A pytest cache permission warning is acceptable only if no test fails.

- [ ] **Step 5: Run the full repository test suite**

```powershell
& $PYTHON -m pytest -q
```

Expected: PASS. If a pre-existing environment-only failure appears, record the exact test and error before deciding whether it is in scope; do not claim the suite passed.

- [ ] **Step 6: Perform real browser verification**

Use `build-web-apps:frontend-testing-debugging` and `browser:control-in-app-browser`. Start the app with the isolated data copy, then verify:

1. Save a DOCX/PDF scoring source: source saved notice appears and no intake/AI starts.
2. Create a session: `未处理` card and optional button appear.
3. Start grading without intake: grading controls remain enabled.
4. Open global graph: resolved skills display with a yellow `X / Y` completeness message.
5. Run intake before grading, during an uncompleted session, and after a completed session using separate test sessions.
6. Refresh/restart after each state and confirm persistence.
7. Force one tag failure and confirm `部分完成` plus retry.
8. Confirm conflict inbox primary count is grouped blocking sources and advisory entries are collapsed.
9. Check 1366×768, 1440×900, and 1920×1080 without horizontal overflow or off-screen buttons.

Capture screenshots for the status card, partial graph warning, completed graph, and grouped inbox in a temporary verification directory that is not committed.

- [ ] **Step 7: Inspect the final diff and commit documentation**

```powershell
git diff --check
git status --short
git diff --stat HEAD~6..HEAD
git add ARCHITECTURE.md README_工作机使用说明.md
git commit -m "docs: document grading paper skill workflow"
```

Expected final worktree status: clean.

---

## Final acceptance checklist

- [ ] Source save survives Streamlit rerun and app restart without AI execution.
- [ ] New and existing sessions can run the same optional intake action.
- [ ] Intake is idempotent and paper-scoped source matching excludes the current exam original.
- [ ] Manual measured links survive workflow retries.
- [ ] Knowledge graph identity is `skill_id`; graph rendering performs no AI call.
- [ ] Partial graph shows reliable skills plus explicit missing coverage.
- [ ] Main inbox count is grouped blocking sources; advisory conflicts do not inflate it.
- [ ] Live databases were never used for schema or destructive verification.
- [ ] Architecture documentation matches the final implementation.
- [ ] Focused, core, full, and browser verification results are reported honestly.
