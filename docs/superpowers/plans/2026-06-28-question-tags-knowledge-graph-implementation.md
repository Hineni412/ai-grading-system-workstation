# Question Tags Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make current question-bank `question_tags` the sole semantic source for grading context, knowledge-graph aggregation, and training recommendation, while preserving the optional paper-intake workflow and one-version read-only rollback compatibility for the old skill catalog.

**Architecture:** Keep grading facts in `grading_system.db` and question/tag facts in `question_bank.db`. A new read-only integration projection resolves only confirmed `grading_question_links`, lets rubric subparts inherit their parent question link, and reads the latest exact tag values at query time. Intake performs one controlled tagging stage and deterministic source linking; grading receives tag context but never generates knowledge identities; graph and recommendation consume the same projection.

**Tech Stack:** Python 3.12, SQLite, Streamlit, pandas, pytest, existing bundled runtime and existing question-bank services. No new external dependency.

## Global Constraints

- Work only in `C:/Users/89418/Desktop/AI阅卷系统_工作机版_v1.5.0/.worktrees/grading-paper-skill-workflow` on branch `codex/grading-paper-skill-workflow`.
- Preserve the dirty `user_data` databases, backup, imported paper, extracted images, rich-content files, and template files. Never stage or rewrite them.
- `question_tags.tag_type = 'knowledge_point'` is the graph node identity and the mandatory recommendation match. Identity is the literal prefix `knowledge_point:` followed by the exact trimmed tag value; there is no alias, fuzzy, semantic, catalog, or AI matching.
- Continue storing all existing tag dimensions. Use `sub_skill`, `method`, `model`, and `prerequisite` only as recommendation boosts; use `ability`, `error_type`, `exam_scope`, `student_level`, difficulty, and reason for their approved display/filter/context roles.
- The only new persisted business field is `session_details.secondary_errors_json`. Do not add another knowledge-point, skill, error identity, or denormalized tag field.
- Keep `grading_details` unchanged. Keep old `knowledge_id`, `knowledge_ids`, `assessment_item_skills`, `question_skill_links`, skill services, and skill graph code readable for one release, but do not write to or read from them in the new active graph/recommendation workflow.
- Grading, review, score adjustment, and export must work when paper intake is `not_started`, `partial`, or `failed`.
- A full-score detail has no primary or secondary errors. A non-full-score detail has one primary error and zero to two secondary errors. Candidate-external errors use category/label `其他` and never update question-bank tags.
- Existing public callers remain compatible through optional keyword arguments, compatibility properties, or retained legacy methods; do not perform a destructive schema migration.
- The intake progress bar reaches 100% only after import, tagging, tag persistence, deterministic question linking, and final coverage recomputation have all finished.
- Every actual model request, including a bounded quality retry, must pass through one shared limiter and one request counter. The grading-paper workflow must not silently expand a failed batch into unrestricted per-question requests.
- Update `ARCHITECTURE.md` only after the implementation and active runtime path match the new design.
- Use the bundled runtime in PowerShell:

```powershell
$PROJECT_ROOT = Split-Path (git rev-parse --path-format=absolute --git-common-dir) -Parent
$PYTHON = Join-Path $PROJECT_ROOT 'runtime\python\python.exe'
$env:AI_GRADING_DATA_DIR = Join-Path (Get-Location) 'user_data'
```

---

## File and responsibility map

- `session_manager.py`: canonical rubric item and parent-question identities only.
- `question_bank/services/source_question_link_service.py`: deterministic grading-question to bank-question associations; legacy suggestion API stays available outside the new intake path.
- `integration/question_tag_projection_service.py`: sole cross-domain read projection from confirmed source links to current question tags and coverage.
- `question_bank/services/ai_tagging_service.py`: bounded, paced, observable AI tagging requests.
- `question_bank/services/grading_paper_intake_service.py`: import, tag missing questions, save all existing dimensions without skill resolution, and create deterministic links.
- `integration/grading_paper_skill_workflow_service.py`: cross-database orchestration and persisted stage/coverage state.
- `pages_shared/grading_paper_skill_workflow_component.py`: shared optional intake card and truthful progress display.
- `ai_grader.py`: grading prompt, tag context, primary/secondary error validation, and legacy knowledge-field compatibility only.
- `grading_service.py`, `hybrid_batch_grading_service.py`, `objective_batch_recognition_service.py`: pass tag context and preserve validated error results across all grading modes.
- `db_manager.py`: `secondary_errors_json` migration, persistence, readback, retry replacement, and compatibility defaults.
- `integration/diagnosis_profile_service.py`: aggregate grading evidence by exact current `knowledge_point` tags.
- `integration/skill_graph_projection.py`: retain legacy projection and add the active question-tag graph row projection.
- `question_bank/recommendation/practice_plan_service.py`: exact-knowledge eligibility plus approved tag-dimension ranking boosts.
- `web_app.py`, `pages/训练推荐.py`: route the active graph, details, grading context, and recommendation through question tags.

---

### Task 1: Build deterministic source linking and the direct tag projection

**Files:**

- Modify: `session_manager.py:2896-2926`
- Modify: `question_bank/services/source_question_link_service.py:105-190`
- Create: `integration/question_tag_projection_service.py`
- Modify: `tests/test_source_question_link_service.py`
- Create: `tests/test_question_tag_projection_service.py`

**Interfaces:**

- Produces: `iter_effective_rubric_item_refs(payload) -> Iterator[tuple[item_ref, parent_ref, question, item]]`.
- Produces: `SourceQuestionLinkService.confirm_imported_questions_for_session(*, grading_session_id, source_questions, imported_bank_questions) -> dict[str, object]`.
- Produces: `QuestionTagProjectionService.project_session(*, grading_session_id, rubric) -> QuestionTagProjection`.
- Produces: `QuestionTagProjection.context_by_item() -> dict[str, dict[str, list[str]]]`.
- Retains: `SourceQuestionLinkService.link_questions_for_session(*, grading_session_id: str | int, source_questions: Iterable[Mapping[str, Any]], candidate_bank_questions: Iterable[Mapping[str, Any]] | None = None) -> dict[str, int]` for legacy/manual suggestion screens, but the grading-paper intake path must not call it.

- [ ] **Step 1: Write failing tests for exact linking, parent inheritance, and missing reasons**

Add tests that create two imported questions with unique `question_number` values, one ambiguous duplicate, and a multipart rubric. Assert direct links are confirmed only for explicit IDs or unique numbers, no `suggested` link is created, and both `Q2(1)` and `Q2(2)` inherit the confirmed `Q2` bank question.

```python
def test_projection_inherits_parent_link_and_reads_all_current_tags(qb_db, seeded_question) -> None:
    SourceQuestionLinkService(qb_db).confirm_link(
        grading_session_id=7,
        source_question_id="Q2",
        bank_question_id=seeded_question,
        link_method="paper_question_number",
    )
    projection = QuestionTagProjectionService(qb_db).project_session(
        grading_session_id=7,
        rubric={"questions": [{"question_id": "Q2", "parts": [
            {"part_id": "Q2(1)"}, {"part_id": "Q2(2)"},
        ]}]},
    )

    assert projection.covered_items == 2
    assert projection.context_by_item()["Q2(1)"]["knowledge_point"] == ["三角形全等"]
    assert projection.items[0].parent_ref == "Q2"
```

- [ ] **Step 2: Run the focused tests and confirm the new imports fail**

Run: `& $PYTHON -m pytest tests/test_source_question_link_service.py tests/test_question_tag_projection_service.py -q`

Expected: FAIL because `confirm_imported_questions_for_session`, `iter_effective_rubric_item_refs`, and `QuestionTagProjectionService` do not exist.

- [ ] **Step 3: Add the parent-aware rubric iterator without changing the existing iterator**

Implement `iter_effective_rubric_item_refs`; make the existing `iter_effective_rubric_items` delegate to it and drop only `parent_ref` from its public result. This keeps current skill rollback callers compatible.

```python
def iter_effective_rubric_items(payload):
    for item_ref, _parent_ref, question, item in iter_effective_rubric_item_refs(payload):
        yield item_ref, question, item
```

- [ ] **Step 4: Add the intake-only deterministic linking method**

The new method must use, in order: an explicit valid `bank_question_id`, then exactly one normalized question-number match within the questions returned by this import. Ambiguous/missing numbers go to `unresolved_question_ids`; it must not inspect question text or call `text_similarity`.

```python
return {
    "confirmed": confirmed,
    "unresolved": len(unresolved_ids),
    "unresolved_question_ids": unresolved_ids,
}
```

- [ ] **Step 5: Implement the read-only projection**

Use frozen dataclasses `ProjectedQuestionTags` and `QuestionTagProjection`. Load confirmed links once, load tags for all linked bank IDs in one SQL query, trim tag values, preserve database order while de-duplicating, and assign exactly one missing reason per effective rubric item: `missing_link`, `bank_question_missing`, or `missing_knowledge_point`.

```python
@dataclass(frozen=True, slots=True)
class ProjectedQuestionTags:
    item_ref: str
    parent_ref: str
    bank_question_id: int | None
    tags: Mapping[str, Sequence[str]]
    missing_reason: str = ""

    @property
    def is_graph_eligible(self) -> bool:
        return bool(self.bank_question_id and self.tags.get("knowledge_point"))
```

Do not cache tag values across calls: a teacher edit must be visible to the next graph/recommendation query without regrading.

- [ ] **Step 6: Run focused tests and commit**

Run: `& $PYTHON -m pytest tests/test_source_question_link_service.py tests/test_question_tag_projection_service.py -q`

Expected: PASS.

Commit: `git add session_manager.py question_bank/services/source_question_link_service.py integration/question_tag_projection_service.py tests/test_source_question_link_service.py tests/test_question_tag_projection_service.py && git commit -m "feat: project grading questions through current tags"`

---

### Task 2: Make every tagging request paced, counted, and bounded

**Files:**

- Modify: `question_bank/services/ai_tagging_service.py:308-366,759-849,899-917,1000-1037`
- Modify: `tests/test_question_bank_ai_tagging_quality.py`

**Interfaces:**

- Produces: frozen `TaggingRequestEvent(request_number, request_kind, question_ids)`.
- Extends: `AITaggingService.analyze_questions(contexts: Mapping[int, TaggingContext], *, max_workers: int | None = None, requests_per_minute: int | None = None, progress_callback: Callable[[int, int, int, AITaggingResult], None] | None = None, request_callback: Callable[[TaggingRequestEvent], None] | None = None, allow_batch_fallback: bool = True, quality_retry_limit: int = 1, enable_review: bool = True) -> dict[int, AITaggingResult]`.
- Guarantees: every batch, fallback, quality retry, and review request uses the same `_RateLimiter` and increments the same request counter before network I/O.

- [ ] **Step 1: Write failing request-accounting tests**

Add a counting fake client and assert a two-question successful batch emits one event; a malformed batch with `allow_batch_fallback=False` emits one event and returns failures; an invalid question with `quality_retry_limit=1` emits at most one additional counted event. Also assert `quality_retry_limit=0` disables that retry.

```python
events: list[TaggingRequestEvent] = []
results = service.analyze_questions(
    contexts,
    max_workers=2,
    requests_per_minute=600,
    request_callback=events.append,
    allow_batch_fallback=False,
    quality_retry_limit=0,
    enable_review=False,
)
assert client.call_count == len(events) == 1
```

- [ ] **Step 2: Run the quality suite and confirm the signature/event failures**

Run: `& $PYTHON -m pytest tests/test_question_bank_ai_tagging_quality.py -q`

Expected: FAIL because request events and bounded policies are absent.

- [ ] **Step 3: Route calls through one request executor**

Create a thread-safe `_TaggingRequestController` that owns the existing limiter and request number. Its `run(kind, question_ids, fn)` method acquires the limiter, increments under a lock, emits the event, then calls `fn`. Pass this controller into batch analysis, single fallback, quality retry, and review helpers.

```python
def run(self, kind: str, question_ids: Iterable[int], operation: Callable[[], T]) -> T:
    self._limiter.acquire()
    with self._lock:
        self._request_number += 1
        event = TaggingRequestEvent(self._request_number, kind, tuple(question_ids))
    if self._callback is not None:
        self._callback(event)
    return operation()
```

- [ ] **Step 4: Enforce workflow-safe fallback behavior**

When `allow_batch_fallback=False`, a batch exception produces one failed `AITaggingResult` per batch item. Do not call `analyze_question`. Respect `quality_retry_limit` per question, and route review calls through the same controller. Preserve current defaults for the ordinary question-bank screen.

- [ ] **Step 5: Run focused tests and commit**

Run: `& $PYTHON -m pytest tests/test_question_bank_ai_tagging_quality.py tests/test_question_bank_ai_tagging_ui.py -q`

Expected: PASS.

Commit: `git add question_bank/services/ai_tagging_service.py tests/test_question_bank_ai_tagging_quality.py && git commit -m "fix: bound and expose tagging requests"`

---

### Task 3: Replace skill resolution with a truthful five-stage intake workflow

**Files:**

- Modify: `question_bank/services/grading_paper_intake_service.py`
- Modify: `integration/grading_paper_skill_workflow_service.py`
- Modify: `pages_shared/grading_paper_skill_workflow_component.py`
- Modify: `web_app.py:2792-2794,3167-3168,4133-4135`
- Modify: `tests/test_grading_paper_archive_intake.py`
- Modify: `tests/test_grading_paper_skill_workflow.py`
- Modify: `tests/test_grading_paper_skill_workflow_ui.py`

**Interfaces:**

- Produces: frozen `GradingPaperWorkflowEvent(stage, completed, total, request_count, failed_questions, question_id='')`.
- Changes active status fields to `imported_question_total`, `complete_tag_question_total`, `linked_source_total`, `source_question_total`, `missing_items`; keep compatibility properties for old UI/tests during this branch.
- Intake calls `QuestionService.save_tag_analysis(question_id, analysis, model_name=result.model_name, confidence=result.analysis.confidence, resolve_skills=False)` and `SourceQuestionLinkService.confirm_imported_questions_for_session(grading_session_id=grading_session_id, source_questions=grading_source_questions, imported_bank_questions=questions)`.

- [ ] **Step 1: Write failing workflow tests**

Cover: existing complete tags are not resubmitted; imported incomplete questions alone are submitted; `SkillResolutionService` and `resolve_rubric` are never called; request count matches fake client calls; events are ordered `import`, `tag`, `save`, `link`, `complete`; only the final event reports 100%; a partial retry submits only missing tags/links.

```python
assert [event.stage for event in events][-5:] == ["import", "tag", "save", "link", "complete"]
assert all(event.completed < event.total for event in events[:-1])
assert events[-1].completed == events[-1].total
assert status.request_count == fake_client.call_count
```

- [ ] **Step 2: Run focused tests and confirm failures against the skill-based status**

Run: `& $PYTHON -m pytest tests/test_grading_paper_archive_intake.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: FAIL because intake resolves skills, completeness uses skill links, and progress ends after tagging.

- [ ] **Step 3: Select pending questions by existing core-tag completeness**

Reuse `question_bank.services.question_service.CORE_ANALYSIS_TAG_TYPES` (`knowledge_point`, `ability`, `exam_scope`, `student_level`) in one SQL coverage query. Do not use `question_skill_links` as a pending predicate. Save every `TagAnalysis` dimension through the existing map with `resolve_skills=False`.

- [ ] **Step 4: Remove the second semantic stage from the active workflow**

Delete active construction of `SkillResolutionService`, `build_skill_context_ranker`, `SkillLinkService.resolve_rubric`, and assessment-skill coverage. Use the Task 1 direct-link summary and tag projection to derive final state. A caught exception must persist `partial` if any imported/tagged/linked work exists, otherwise `failed`.

- [ ] **Step 5: Emit and render all five stages**

Translate tagging callbacks and request events into one workflow event stream. The Streamlit card shows current stage, imported/tagged/linked counts, request count, failed count, and missing question IDs/reasons. Use these real saved session-state keys at all three entry points:

```python
max_workers=int(st.session_state.get("tagging_max_workers_input", 4) or 4)
requests_per_minute=int(st.session_state.get("tagging_requests_per_minute_input", 1000) or 1000)
```

- [ ] **Step 6: Run tests, scan the active workflow, and commit**

Run: `& $PYTHON -m pytest tests/test_grading_paper_archive_intake.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py -q`

Run: `rg -n "SkillResolutionService|resolve_rubric|question_skill_links|assessment_item_skills|qb_tagging_workers|qb_tagging_rpm" question_bank/services/grading_paper_intake_service.py integration/grading_paper_skill_workflow_service.py pages_shared/grading_paper_skill_workflow_component.py web_app.py`

Expected: tests PASS; matches may remain only in explicitly named legacy helpers outside the active component path, and obsolete `qb_tagging_*` session keys have no matches.

Commit: `git add question_bank/services/grading_paper_intake_service.py integration/grading_paper_skill_workflow_service.py pages_shared/grading_paper_skill_workflow_component.py web_app.py tests/test_grading_paper_archive_intake.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py && git commit -m "refactor: make paper intake tag native"`

---

### Task 4: Send current question tags to grading and validate one primary plus two secondary errors

**Files:**

- Modify: `ai_grader.py:40-50,180-360,408-512,748-825`
- Modify: `grading_service.py:37-57,75-135`
- Modify: `hybrid_batch_grading_service.py`
- Modify: `web_app.py:3333-3335`
- Modify: `tests/test_grading_completeness.py`
- Modify: `tests/test_hybrid_grading_regressions.py`
- Create: `tests/test_grading_tag_context_and_errors.py`

**Interfaces:**

- Produces: frozen `SecondaryError(category, summary, evidence='')`.
- Extends: `QuestionGradingDetail.secondary_errors: list[SecondaryError] = field(default_factory=list)`.
- Extends: `AIGrader(rubric_path: Path, llm_client: LLMClient, answer_key_path: Path | None = None, grading_model: str | None = None, target_question_ids: list[str] | None = None, answer_regions: list[dict[str, Any]] | None = None, question_tag_context: Mapping[str, Mapping[str, Sequence[str]]] | None = None)`.
- Extends: `GradingService(db_manager: DBManager, llm_client: LLMClient, question_bank_db_path: Path | None = None)`.
- Keeps `knowledge_id='UNKNOWN'` and `knowledge_ids=[]` only as deprecated local persistence compatibility defaults; they are not requested from the model and are not graph inputs.

- [ ] **Step 1: Write failing prompt and validation tests**

Assert the emitted prompt contains current `knowledge_point`, `sub_skill`, `method`, `model`, `prerequisite`, and `error_type` context; does not request `knowledge_id`, `knowledge_ids`, or skill fields; accepts one primary plus two secondary errors; rejects/truncates a third secondary error deterministically; clears all errors for full score; and maps labels outside the question's `error_type` candidates to `其他` without mutating tags.

```python
assert "三角形全等" in prompt
assert "辅助线思路缺失" in prompt
assert "knowledge_id" not in prompt
assert len(result.grading_details[0].secondary_errors) == 2
```

- [ ] **Step 2: Run the grading tests and confirm they fail**

Run: `& $PYTHON -m pytest tests/test_grading_tag_context_and_errors.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py -q`

Expected: FAIL because tag context and secondary errors are absent and the prompt requires knowledge fields.

- [ ] **Step 3: Add compact per-question tag context to the grader**

Serialize only the approved tag dimensions, keyed by exact rubric item ID. Build it in `GradingService` using `QuestionTagProjectionService.project_session`; when the question-bank path or link is absent, pass an empty mapping and continue grading. `web_app.py` passes `question_bank_db_path()` explicitly.

- [ ] **Step 4: Replace model-generated knowledge fields with controlled error output**

The output contract keeps score, deduction reason, confidence, primary `error_category`/`error_summary`, and `secondary_errors`. Prompt rules:

```text
- Choose error_summary from this question's error_type candidates when one fits.
- Otherwise use error_category="其他" and give the concrete explanation in error_summary.
- Return no more than two secondary_errors; each has category, summary, evidence.
- Full-score questions return null primary error and an empty secondary_errors array.
```

Remove `_normalize_knowledge_ids` and `_rubric_knowledge_ids_for_question` from model-response validation. Construct deprecated compatibility values locally (`knowledge_id="UNKNOWN"`, empty `knowledge_ids`) so existing NOT NULL columns and old call sites remain valid until a separate cleanup migration.

- [ ] **Step 5: Normalize error results in one helper used by full-paper and hybrid paths**

Implement `_normalize_grading_errors(item, *, full_score, error_candidates) -> tuple[category, summary, list[SecondaryError]]`. Preserve hard-rule categories such as prompt injection, discarded answer, and human review. Deduplicate identical primary/secondary summaries and cap secondary errors at two.

- [ ] **Step 6: Run focused tests and commit**

Run: `& $PYTHON -m pytest tests/test_grading_tag_context_and_errors.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py tests/test_prompt_injection_guard.py tests/test_solution_answer_guard.py -q`

Expected: PASS.

Commit: `git add ai_grader.py grading_service.py hybrid_batch_grading_service.py web_app.py tests/test_grading_tag_context_and_errors.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py && git commit -m "feat: grade with question tag error context"`

---

### Task 5: Persist secondary errors through normal save and retry replacement

**Files:**

- Modify: `db_manager.py:237-254,1439-1454,1660-1690,1798-1860,2080-2110`
- Modify: `grading_service.py:37-57`
- Modify: `tests/test_atomic_major_retry.py`
- Modify: `tests/test_retry_failed_grading.py`
- Create: `tests/test_secondary_error_persistence.py`

**Interfaces:**

- Adds: `session_details.secondary_errors_json TEXT NOT NULL DEFAULT '[]'` via `_ensure_column`.
- Produces private helpers `_serialize_secondary_errors(errors: Sequence[SecondaryError]) -> str` and `_parse_secondary_errors(raw: object) -> list[SecondaryError]`.
- Does not alter `grading_details`.

- [ ] **Step 1: Write failing schema, round-trip, and retry tests**

Create a session result with two secondary errors, read it back through the same API used by retry, replace one major question atomically, and assert unrelated details retain their arrays while the replacement uses the new array. Assert malformed historical JSON reads as `[]` and does not crash.

```python
columns = {row[1] for row in conn.execute("PRAGMA table_info(session_details)")}
assert "secondary_errors_json" in columns
assert [item.summary for item in rows[0]["secondary_errors"]] == ["条件遗漏", "辅助线错误"]
```

- [ ] **Step 2: Run focused persistence tests and confirm schema failure**

Run: `& $PYTHON -m pytest tests/test_secondary_error_persistence.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q`

Expected: FAIL because the column and serializers do not exist.

- [ ] **Step 3: Add the additive migration and every session-detail write path**

Call `_ensure_column` after the `session_details` table creation. Include `secondary_errors_json` in normal result inserts and `replace_result_details_atomic`. Serialize only `category`, `summary`, and optional `evidence`, with at most two entries.

- [ ] **Step 4: Add defensive readback**

Update session detail SELECTs used by results, retry, review, and export. Parse arrays in `_detail_from_row`; invalid JSON, non-list roots, and invalid members become an empty list with no invented values.

- [ ] **Step 5: Run tests and commit**

Run: `& $PYTHON -m pytest tests/test_secondary_error_persistence.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q`

Expected: PASS.

Commit: `git add db_manager.py grading_service.py tests/test_secondary_error_persistence.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py && git commit -m "feat: persist secondary grading errors"`

---

### Task 6: Aggregate diagnosis and graph rows from current exact knowledge tags

**Files:**

- Modify: `integration/diagnosis_profile_service.py`
- Modify: `integration/skill_graph_projection.py`
- Modify: `tests/test_diagnosis_profile_service.py`
- Modify: `tests/test_skill_graph_projection.py`
- Create: `tests/test_question_tag_diagnosis.py`

**Interfaces:**

- Produces: `DiagnosisProfileService.build_tag_profiles(*, scope, exam_scope) -> dict[str, Any]`.
- Produces: `DiagnosisProfileService.tag_evidence(*, knowledge_point, student_ids=(), session_ids=()) -> list[dict[str, Any]]`.
- Produces: `build_question_tag_graph_rows(profile) -> list[dict[str, Any]]`.
- Active profile includes `diagnosis_identity='question_tag'`, `knowledge_key`, `knowledge_point`, current supporting tag dimensions, score/loss/error aggregates, and coverage/missing items.
- Retains legacy `build_skill_profiles` and `skill_evidence` only for rollback.

- [ ] **Step 1: Write failing exact-tag diagnosis tests**

Seed two sessions linked to bank questions sharing exact `knowledge_point='三角形全等'`; assert one graph node aggregates both. Edit one question tag to `轴对称` and call the service again without changing grading rows; assert the historical evidence immediately moves to the new node. Add an unlinked item and an item without a knowledge point; assert partial coverage lists both reasons.

```python
first = service.build_tag_profiles(scope=scope, exam_scope=exam_scope)
update_question_tag(qb_db, question_id, "knowledge_point", "轴对称")
second = service.build_tag_profiles(scope=scope, exam_scope=exam_scope)
assert {p["knowledge_point"] for p in second["weak_points"]} == {"轴对称", "三角形全等"}
```

- [ ] **Step 2: Run diagnosis/graph tests and confirm missing APIs**

Run: `& $PYTHON -m pytest tests/test_question_tag_diagnosis.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py -q`

Expected: FAIL because tag profiles and tag graph rows do not exist.

- [ ] **Step 3: Build evidence from grading details plus the Task 1 projection**

Reuse existing scope/session/student selection and score-weighting code. For each selected session, project effective rubric items, map detail IDs to projected items, and group only graph-eligible evidence by exact trimmed knowledge tag. When a question has multiple knowledge points, contribute that detail once to each exact point and expose that fact in evidence; do not split or invent score weights.

- [ ] **Step 4: Aggregate current detail tags and errors**

For each knowledge point aggregate distinct `sub_skill`, `method`, `ability`, `model`, and `prerequisite` values from the currently linked bank questions. Count primary plus secondary errors from grading details, keeping primary/secondary roles. The graph row contains only a knowledge node; supporting tags/errors remain detail fields.

```python
row = {
    "node_id": f"knowledge_point:{knowledge_point}",
    "knowledge_point": knowledge_point,
    "mastery_rate": mastery_rate,
    "question_count": len(question_refs),
    "error_counts": error_counts,
}
```

- [ ] **Step 5: Switch `build_profiles` default to the tag profile while retaining explicit rollback methods**

The active default must not read `skill_system_settings`, `assessment_item_skills`, `question_skill_links`, or concept alignment. Do not mix tag rows and skill rows in one response.

- [ ] **Step 6: Run focused tests and commit**

Run: `& $PYTHON -m pytest tests/test_question_tag_diagnosis.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py -q`

Expected: PASS, including legacy explicit-method tests.

Commit: `git add integration/diagnosis_profile_service.py integration/skill_graph_projection.py tests/test_question_tag_diagnosis.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py && git commit -m "feat: aggregate graph from current question tags"`

---

### Task 7: Recommend only questions with exact shared knowledge tags

**Files:**

- Modify: `question_bank/recommendation/practice_plan_service.py:145-253,255-430,672-690,1249-1270`
- Modify: `tests/test_practice_plan_service.py`
- Modify: `tests/test_knowledge_practice_end_to_end.py`
- Create: `tests/test_question_tag_recommendation.py`

**Interfaces:**

- Produces: `_is_question_tag_diagnosis(profile) -> bool`.
- Produces: `PracticePlanService._generate_question_tag_variant(diagnosis_profile: Mapping[str, Any], *, count: int, stage_counts: Mapping[str, int], stage_ratios: Mapping[str, object] | None, weights: Mapping[str, object], exclude_question_ids: Iterable[int] | None, include_historical_wrong_questions: bool, similarity_threshold: float, related_fill_policy: str) -> dict[str, Any]`.
- Eligibility: candidate and target share at least one exact `knowledge_point` value.
- Ranking boosts: exact overlaps in `sub_skill`, `method`, `model`, and `prerequisite`; retain existing difficulty, student-level, frequency, diversity, dedupe, and confirmed-original exclusion logic.

- [ ] **Step 1: Write failing eligibility, boost, and exclusion tests**

Seed candidates where: one shares only a semantically similar phrase, one shares exact knowledge, one shares exact knowledge plus method, and one is the confirmed source original. Assert only exact knowledge candidates are eligible, the method-overlap candidate ranks first, and the original is excluded. Assert no skill neighbor or broad semantic fallback fills a shortage.

```python
assert all(
    set(item["tags"]["knowledge_point"]) & {"三角形全等"}
    for item in plan["items"]
)
assert original_question_id not in {item["question_id"] for item in plan["items"]}
assert plan["items"][0]["question_id"] == exact_with_method_id
```

- [ ] **Step 2: Run recommendation tests and confirm the skill branch is selected today**

Run: `& $PYTHON -m pytest tests/test_question_tag_recommendation.py tests/test_practice_plan_service.py tests/test_knowledge_practice_end_to_end.py -q`

Expected: FAIL because `diagnosis_identity='question_tag'` has no dedicated generator.

- [ ] **Step 3: Add the tag diagnosis branch before legacy branches**

In `generate_variant`, dispatch `question_tag` profiles first. Load all candidate tags with the existing `_load_tags`; derive target knowledge and boost sets directly from weak-point fields.

```python
shared_knowledge = target_knowledge.intersection(candidate_tags.get("knowledge_point", ()))
if not shared_knowledge:
    continue
boost_overlap = sum(
    bool(target_tags.get(kind, set()) & set(candidate_tags.get(kind, ())))
    for kind in ("sub_skill", "method", "model", "prerequisite")
)
```

- [ ] **Step 4: Reuse existing ranking and exclusion components without skill joins**

Use `_resolved_exclusions` for confirmed source originals. Reuse difficulty/stage allocation, frequency, source diversity, duplicate filtering, shortage reporting, and output formatting. Do not query `question_skill_links`, skill neighbors, aliases, or concept mappings in this branch.

- [ ] **Step 5: Run tests and commit**

Run: `& $PYTHON -m pytest tests/test_question_tag_recommendation.py tests/test_practice_plan_service.py tests/test_knowledge_practice_end_to_end.py tests/test_practice_grouping.py tests/test_practice_candidate_scoring.py tests/test_recommendation_frequency_scoring.py -q`

Expected: PASS.

Commit: `git add question_bank/recommendation/practice_plan_service.py tests/test_question_tag_recommendation.py tests/test_practice_plan_service.py tests/test_knowledge_practice_end_to_end.py && git commit -m "feat: recommend by exact question tags"`

---

### Task 8: Switch active graph, detail, and training pages to question tags

**Files:**

- Modify: `web_app.py:120-140,4137-4170,4665-4700,9525-9531`
- Modify: `pages/训练推荐.py:760-880`
- Modify: `tests/test_training_recommendation_ui.py`
- Modify: `tests/test_grading_paper_skill_workflow_ui.py`
- Create: `tests/test_question_tag_graph_ui_contract.py`

**Interfaces:**

- Global graph uses `DiagnosisProfileService.build_tag_profiles` and `build_question_tag_graph_rows`.
- Detail route uses URL/session payload `knowledge_key` and `knowledge_point`, never `skill_id`.
- Training page passes the same question-tag diagnosis profile to `PracticePlanService`.
- Missing coverage message lists linked/tagged/total counts and missing question IDs/reasons; it does not display unresolved skill terms.

- [ ] **Step 1: Write failing source-contract tests**

Assert the active graph and training sections call the new APIs; graph click payload contains `knowledge_point`; active detail rendering calls `tag_evidence`; and the obsolete `_sync_session_skill_links` helper is absent. Keep these tests narrow enough to distinguish active-path code from retained rollback methods.

- [ ] **Step 2: Run UI contract tests and confirm active skill references fail them**

Run: `& $PYTHON -m pytest tests/test_question_tag_graph_ui_contract.py tests/test_training_recommendation_ui.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: FAIL because global graph/detail/training still use `skill_id`.

- [ ] **Step 3: Replace active graph and detail routing**

Render knowledge-point node labels directly. Detail panels show score/loss/mastery, linked question evidence, supporting tags, primary/secondary error distributions, and recommendation count. Remove `SkillCatalogService.get_skill` from active details. Preserve the existing page shell, scopes, and student/session filters.

- [ ] **Step 4: Switch training profile construction**

Call `build_tag_profiles` explicitly and feed its `diagnosis_identity='question_tag'` payload to `PracticePlanService.generate`. Keep task snapshot/export behavior unchanged.

- [ ] **Step 5: Run UI and end-to-end tests, then commit**

Run: `& $PYTHON -m pytest tests/test_question_tag_graph_ui_contract.py tests/test_training_recommendation_ui.py tests/test_grading_paper_skill_workflow_ui.py tests/test_knowledge_practice_end_to_end.py -q`

Expected: PASS.

Commit: `git add web_app.py pages/训练推荐.py tests/test_question_tag_graph_ui_contract.py tests/test_training_recommendation_ui.py tests/test_grading_paper_skill_workflow_ui.py && git commit -m "refactor: route graph and training through tags"`

---

### Task 9: Verify compatibility boundaries, update architecture, and perform browser QA

**Files:**

- Modify: `ARCHITECTURE.md`
- Modify: `README.md` only if it currently documents the old skill-based user workflow.
- Modify: tests only if verification exposes a real regression; do not weaken assertions.

**Interfaces and documentation facts:**

- `question_tags` is the active semantic source.
- `grading_question_links` is the only cross-domain identity relation.
- Old skill tables/fields remain read-only rollback compatibility for one release.
- `secondary_errors_json` is the only new field.
- The diagram must show intake, grading context, graph, and recommendation all reading the same current tags.

- [ ] **Step 1: Run an active-path dependency audit**

Run:

```powershell
rg -n "resolve_rubric|SkillResolutionService|LLMSkillContextRanker|assessment_item_skills|question_skill_links|build_skill_profiles|skill_evidence" integration/grading_paper_skill_workflow_service.py question_bank/services/grading_paper_intake_service.py web_app.py pages/训练推荐.py
rg -n "knowledge_id|knowledge_ids|measured_skills|supporting_skills" ai_grader.py
```

Expected: no active-path matches. Matches are allowed only in clearly marked rollback functions or compatibility dataclass/database handling; the grading prompt contains none of the forbidden model output fields.

- [ ] **Step 2: Run the full automated suite**

Run: `& $PYTHON -m pytest -q`

Expected: PASS. If pre-existing unrelated failures occur, rerun the failing test individually and record exact evidence before deciding whether it is in scope.

- [ ] **Step 3: Run syntax and compile validation**

Run: `& $PYTHON -m compileall -q ai_grader.py grading_service.py db_manager.py integration question_bank pages pages_shared web_app.py`

Expected: exit code 0.

- [ ] **Step 4: Update `ARCHITECTURE.md` from verified implementation**

Replace the active skill-ID graph/recommendation claims with the exact tag projection. Document both SQLite boundaries, stage progress, current-tag historical behavior, partial coverage, error persistence, and rollback-only legacy path. Mark the verification date `2026-06-28` and cite the implemented modules/tests as evidence.

- [ ] **Step 5: Run the app and perform real browser verification**

Use the browser-control skill during execution. Start the existing app with the bundled runtime and verify the exam workbench, grading page, global graph, and training recommendation at 1366×768, 1440×900, and 1920×1080. Exercise `not_started`, `running`, `partial`, `failed`, retry, and completed card states with test data. Confirm:

- no progress reaches 100% before final recomputation;
- request count stops increasing when the workflow finishes or fails;
- missing question IDs/reasons remain readable;
- knowledge-point details show current tags and primary/secondary errors;
- no horizontal overflow or blocking dialog;
- browser console has no related errors.

- [ ] **Step 6: Review the complete diff and user-data boundary**

Run:

```powershell
git status --short
git diff --check
git diff --stat bbc7d63..HEAD
git diff bbc7d63..HEAD -- . ':(exclude)user_data'
```

Expected: only intended source, tests, and docs are staged/committed; the pre-existing `user_data` changes remain unstaged and untouched; `git diff --check` prints nothing.

- [ ] **Step 7: Commit documentation and final verification adjustments**

Commit: `git add ARCHITECTURE.md README.md && git commit -m "docs: describe question tag graph architecture"`

If `README.md` was correctly unchanged, stage only `ARCHITECTURE.md`.

---

## Specification coverage checklist

- [ ] Intake performs one question-bank tag pass and no skill disambiguation pass.
- [ ] All existing tag dimensions remain persisted and manageable.
- [ ] Graph node identity is the latest exact `knowledge_point` value.
- [ ] Grading questions relate only to bank questions; subparts inherit parent links.
- [ ] Historical graph/recommendation results follow later tag edits without regrading.
- [ ] Grading prompt receives tags but requests no knowledge or skill output.
- [ ] Each non-full-score detail has one primary and at most two secondary errors; full score has none.
- [ ] Candidate-external errors remain `其他` in grading results and never update bank tags.
- [ ] Partial data produces a partial graph with exact coverage and missing reasons.
- [ ] Retry submits only incomplete tags or links and never rewrites completed items.
- [ ] Every actual tagging request is paced and counted; failed batches do not silently fan out in the paper workflow.
- [ ] Progress reaches 100% only after import, tagging, save, link, and recomputation.
- [ ] Recommendation requires exact knowledge overlap and uses only approved tag boosts.
- [ ] Confirmed source originals are excluded from recommendation.
- [ ] New active paths do not read skill IDs, skill neighbors, aliases, concept mappings, or legacy grading knowledge IDs.
- [ ] Only `secondary_errors_json` is added; old tables/fields remain rollback-only and no data is deleted.
- [ ] Grading/review/export remain non-blocking when intake is absent or incomplete.

## Plan self-review checklist

- [x] Every acceptance criterion in `docs/superpowers/specs/2026-06-28-question-tags-knowledge-graph-design.md` maps to a task or the coverage checklist above.
- [x] Every task names exact files, public interfaces, a failing test, expected failure, minimal implementation, passing command, and commit.
- [x] No placeholder marker, invented API field, or unresolved high-impact product decision remains.
- [x] Dataclass, JSON, database, profile, UI route, and recommendation field names are consistent across tasks.
- [x] Legacy compatibility is explicitly isolated from the active path and never mixed into one graph/profile.
