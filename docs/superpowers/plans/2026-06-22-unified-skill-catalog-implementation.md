# Unified Skill Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace post-hoc knowledge-term mapping with one stable concrete-skill identity shared by rubrics, wrong-answer evidence, and question-bank items, while reducing an ordinary teacher's maintenance work to zero except for one explicit shortage choice.

**Architecture:** Store the catalog, assessment links, question links, conflicts, neighbors, rollout state, and migration audit in `question_bank.db`. Keep scores and original rubric evidence in `grading_system.db`; join them through `(grading_session_id, source_question_id)` without cross-database foreign keys. Introduce the new path behind a read-mode switch, dual-write new data, migrate history with dry-run/backup/idempotency, compare legacy and skill-based recommendations, then cut over only after the agreed coverage and precision gates pass.

**Tech Stack:** Python 3.12, SQLite, Streamlit, pytest, existing `LLMClient` JSON interface, bundled runtime at `runtime/python/python.exe`.

## Global Constraints

- Do not edit or delete existing `user_data` files while implementing Tasks 1–11. All automated tests must use `tmp_path` databases.
- Preserve the user's dirty working tree. Stage only files named by the current task; never use `git add -A`, `git reset --hard`, or `git checkout --`.
- Keep `knowledge_concepts`, `knowledge_source_mappings`, and `knowledge_relations` readable for one stable release, but stop using them for new recommendation decisions after cutover.
- Keep original `G7_XX`, `KP_*`, free-text labels, old tags, rubric JSON, score details, and historical training snapshots as evidence. Never rewrite them into the new ID.
- An exact recommendation requires the same resolved `skill_id` on both sides and `question_skill_links.role = 'measured'`. A topic match, fuzzy text match, `supporting` link, or neighbor is never exact.
- A neighbor question can enter a plan only after `related_fill_policy = 'allow_neighbors'`; the snapshot must retain the source skill, neighbor skill, kind, and reason.
- A resolved assessment item and a resolved question must each have at least one `measured` skill. An unresolved item gets an open conflict and no recommendation-eligible link.
- Auto-resolution thresholds are constants: first candidate `>= 0.92`, margin over second candidate `>= 0.15`, compatible grade/topic, no ambiguity rule, and active target skill.
- Auto-created local skills must be concrete, have evidence and one topic, have confidence `>= 0.92`, and have no competing candidate. Reject isolated broad labels such as `性质`, `计算`, `作图`, `综合`, and `应用`.
- AI failure must not block grading or import. Deterministic matching continues; all other cases become explicit conflicts and remain ineligible for recommendation.
- Use Chinese teacher-facing terms. Ordinary pages must not display `skill_id`, namespace, mapping, `suggested`, `confirmed`, confidence thresholds, graph edges, or weights.
- Run the smallest relevant test after each green step. Before any completion claim, run the complete verification suite listed in Task 12 and inspect the fresh output.

---

## Planned File Structure

### New production files

- `question_bank/models/skill_catalog.py` — enums and immutable request/result/link records.
- `question_bank/taxonomy/data/junior_math_skills_v1.json` — versioned grades 7–9 math topics, concrete skills, aliases, and built-in neighbors.
- `question_bank/taxonomy/skill_catalog_seed.py` — schema validation and deterministic seed loading.
- `question_bank/services/skill_catalog_service.py` — catalog CRUD, redirects, coverage, rollout settings, and conflict decisions.
- `question_bank/services/skill_resolution_service.py` — deterministic resolution, contextual ranking thresholds, local-skill creation, and conflict persistence.
- `question_bank/services/skill_link_service.py` — transactional assessment/question link replacement and read APIs.
- `question_bank/services/skill_context_ranker.py` — narrow adapter from the existing JSON LLM client to ranked skill candidates.
- `question_bank/services/skill_migration_service.py` — dry-run, apply, audit, idempotency, backup metadata, and report generation.
- `update_tools/migrate_skill_catalog.py` — guarded migration CLI.
- `migrations/question_bank/008_add_unified_skill_catalog.sql` — deployable schema migration.

### New tests and fixtures

- `tests/test_skill_catalog_schema.py`
- `tests/test_skill_catalog_seed.py`
- `tests/test_skill_resolution_service.py`
- `tests/test_skill_link_service.py`
- `tests/test_skill_migration_service.py`
- `tests/test_unified_skill_recommendation.py`
- `tests/test_skill_catalog_ui.py`
- `tests/fixtures/skill_resolution_cases.json` — deterministic boundary and critical math cases.
- `tests/fixtures/skill_migration_gold.json` — anonymized 100-item acceptance fixture with expected stable keys.

### Existing files to modify

- `question_bank/database/schema.py`
- `question_bank/models/tag_schema.py`
- `question_bank/services/ai_tagging_service.py`
- `question_bank/services/question_service.py`
- `question_bank/services/grading_paper_intake_service.py`
- `integration/diagnosis_profile_service.py`
- `db_manager.py`
- `session_manager.py`
- `web_app.py`
- `question_bank/recommendation/practice_plan_service.py`
- `question_bank/services/training_task_service.py`
- `pages/题库管理.py`
- `pages/训练推荐.py`
- `pages/知识点整理（高级）.py`
- Existing diagnosis, recommendation, tagging, schema, and UI tests named in the tasks below.

---

## Task 1: Add the unified skill schema and typed contracts

**Files:**

- Create: `migrations/question_bank/008_add_unified_skill_catalog.sql`
- Create: `question_bank/models/skill_catalog.py`
- Create: `tests/test_skill_catalog_schema.py`
- Modify: `question_bank/database/schema.py`
- Modify: `tests/test_knowledge_practice_schema.py`

- [ ] **Step 1: Write failing schema tests**

Add tests that initialize a temporary question-bank database and assert these tables exist:

```python
EXPECTED_SKILL_TABLES = {
    "skill_topics",
    "skills",
    "assessment_item_skills",
    "question_skill_links",
    "skill_resolution_conflicts",
    "skill_neighbors",
    "skill_system_settings",
    "skill_migration_runs",
}
```

Also test the database rules:

Use the test names `test_exact_links_require_measured_or_supporting_role`,
`test_skill_redirect_cannot_point_to_itself`,
`test_assessment_link_identity_is_unique`,
`test_question_link_identity_is_unique`, and
`test_neighbor_identity_is_unique`.

Use these identities:

- `assessment_item_skills`: unique `(grading_session_id, source_question_id, skill_id, role)`.
- `question_skill_links`: unique `(question_id, skill_id, role)`.
- `skill_neighbors`: unique `(source_skill_id, target_skill_id, kind)`.
- `skill_resolution_conflicts`: unique open source `(source_type, source_ref, raw_label, state)` through service-level upsert; SQLite does not need a partial unique index.

- [ ] **Step 2: Run the new tests and confirm red**

Run:

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_schema.py -q
```

Expected: failures because the new tables and model module do not exist.

- [ ] **Step 3: Define exact model contracts**

Create `question_bank/models/skill_catalog.py` with:

```python
class SkillRole(str, Enum):
    MEASURED = "measured"
    SUPPORTING = "supporting"

class ResolutionOutcome(str, Enum):
    RESOLVED_EXISTING = "resolved_existing"
    CREATED_LOCAL = "created_local"
    CONFLICT = "conflict"

class SkillOrigin(str, Enum):
    BUILTIN = "builtin"
    LOCAL = "local"

class SkillSourceType(str, Enum):
    ASSESSMENT_ITEM = "assessment_item"
    QUESTION_BANK_ITEM = "question_bank_item"
    LEGACY_TERM = "legacy_term"

@dataclass(frozen=True, slots=True)
class SkillResolutionRequest:
    source_type: SkillSourceType
    source_ref: str
    raw_label: str
    stable_key_hint: str = ""
    grade: str = ""
    topic_hint: str = ""
    question_text: str = ""
    answer_text: str = ""
    rubric_text: str = ""
    existing_tags: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class RankedSkillCandidate:
    skill_id: int
    confidence: float
    reason: str

@dataclass(frozen=True, slots=True)
class SkillResolution:
    outcome: ResolutionOutcome
    skill_id: int | None
    confidence: float
    reason: str
    candidates: tuple[RankedSkillCandidate, ...] = ()
```

Normalize NFKC whitespace, clamp confidence to `[0, 1]`, and reject a non-conflict result without `skill_id`.

- [ ] **Step 4: Add schema in both bootstrap paths**

Implement the same DDL in migration `008` and `_create_knowledge_practice_tables()` so fresh installations and upgrades converge. Required fields and checks:

- `skill_topics(stable_key UNIQUE, name, subject, grade_min, grade_max, status)`.
- `skills(stable_key UNIQUE, topic_id FK, name, aliases_json, grade_min, grade_max, origin, status, redirect_skill_id FK, timestamps)`.
- Link tables with raw evidence fields, source, confidence, `status IN ('resolved','conflict')`, and timestamps.
- Conflict table with candidate JSON, reason, state, resolution audit, and timestamps.
- Neighbor table with `kind IN ('same_topic','prerequisite','advanced','co_assessed')`, weight, source, and enabled flag.
- Settings table initialized to `recommendation_read_mode = legacy` and `catalog_version = junior_math_v1`.
- Migration-runs table with unique batch ID, mode, status, source counts, result counts, report path, backup JSON, and timestamps.

Add indexes for skill name/topic/status, both link lookup directions, open conflicts, enabled neighbors, and migration batch.

- [ ] **Step 5: Run schema and legacy schema tests**

Run:

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_schema.py tests/test_knowledge_practice_schema.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add migrations/question_bank/008_add_unified_skill_catalog.sql question_bank/database/schema.py question_bank/models/skill_catalog.py tests/test_skill_catalog_schema.py tests/test_knowledge_practice_schema.py
git commit -m "feat: add unified skill catalog schema"
```

---

## Task 2: Seed a concrete grades 7–9 math catalog

**Files:**

- Create: `question_bank/taxonomy/data/junior_math_skills_v1.json`
- Create: `question_bank/taxonomy/skill_catalog_seed.py`
- Create: `question_bank/services/skill_catalog_service.py`
- Create: `tests/test_skill_catalog_seed.py`
- Modify: `question_bank/database/schema.py`

- [ ] **Step 1: Write failing catalog validation tests**

The seed test must assert:

- Catalog version is `junior_math_v1`.
- It contains at least 20 teacher-readable topics and 120 concrete skills covering grades 7–9.
- Every stable key is unique and ASCII-lowercase dotted form.
- Every skill belongs to one existing topic and has at least one applicable grade.
- Names and aliases are unique after NFKC/casefold within the catalog unless an explicit ambiguity entry exists.
- Isolated broad names `性质`, `计算`, `作图`, `综合`, `应用`, `概念`, `方法` are forbidden.
- Critical skills exist as separate identities: `角平分线性质`, `三角形外心作图`, `最短路径作图`, `轴对称作图`, `一次函数图像应用`, `全等三角形判定`.
- All current `canonical_knowledge_seed_rows()` values are covered by a topic, a skill alias, or an explicit legacy alias map.
- Seeding twice leaves row counts unchanged.

- [ ] **Step 2: Run the seed test and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_seed.py -q
```

Expected: import/file-not-found failures.

- [ ] **Step 3: Author the versioned seed file**

Use this top-level shape:

```json
{
  "version": "junior_math_v1",
  "subject": "math",
  "topics": [
    {"stable_key": "math.geometry.triangle", "name": "三角形", "grade_min": 7, "grade_max": 9}
  ],
  "skills": [
    {
      "stable_key": "math.geometry.triangle.angle_bisector_property",
      "topic_key": "math.geometry.triangle",
      "name": "角平分线性质",
      "aliases": ["角平分线的性质", "角平分线定理"],
      "grade_min": 7,
      "grade_max": 9,
      "legacy_keys": ["kp_line_angle"]
    }
  ],
  "neighbors": [
    {
      "source_key": "math.geometry.triangle.angle_bisector_property",
      "target_key": "math.geometry.triangle.angle_bisector_construction",
      "kind": "same_topic",
      "weight": 0.78
    }
  ]
}
```

Topics are teacher navigation groups, not recommendation targets. Skills must describe a specific mathematical object plus action/property, not a chapter-sized bucket.

- [ ] **Step 4: Implement loader, validation, and idempotent seed**

Expose `load_builtin_catalog() -> BuiltinSkillCatalog` and
`validate_builtin_catalog(catalog) -> tuple[str, ...]`. `SkillCatalogService`
must expose `seed_builtin_catalog() -> dict[str, int]`, `list_topics()`,
`list_skills(topic_id=None, include_merged=False)`,
`get_skill(skill_id, follow_redirect=True)`, `find_by_stable_key(stable_key)`,
and `merge_skill(source_skill_id, target_skill_id, actor=...)` with the return
types implied by the stored records.

`merge_skill()` must reject cycles, redirect all future reads, retain historical links, and disable self-neighbors after redirect resolution.

- [ ] **Step 5: Seed on fresh database initialization without rewriting local skills**

After DDL creation, call the idempotent seed. Updating the built-in JSON may update names/aliases for `origin='builtin'`; it must never overwrite `origin='local'`, administrator decisions, or merged redirects.

- [ ] **Step 6: Run tests**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_seed.py tests/test_skill_catalog_schema.py -q
```

Expected: all pass, including double-seed and redirect tests.

- [ ] **Step 7: Commit Task 2**

```powershell
git add question_bank/taxonomy/data/junior_math_skills_v1.json question_bank/taxonomy/skill_catalog_seed.py question_bank/services/skill_catalog_service.py question_bank/database/schema.py tests/test_skill_catalog_seed.py
git commit -m "feat: seed concrete junior math skills"
```

---

## Task 3: Implement deterministic resolution and conflict persistence

**Files:**

- Create: `question_bank/services/skill_resolution_service.py`
- Create: `question_bank/services/skill_link_service.py`
- Create: `tests/test_skill_resolution_service.py`
- Create: `tests/test_skill_link_service.py`
- Create: `tests/fixtures/skill_resolution_cases.json`

- [ ] **Step 1: Write failing deterministic resolver tests**

Cover these cases:

Use the test names `test_stable_key_match_wins_without_ai`,
`test_exact_name_match_wins_without_ai`, `test_exact_alias_match_wins_without_ai`,
`test_redirected_skill_resolves_to_active_target`,
`test_ambiguous_alias_becomes_conflict`, `test_broad_label_becomes_conflict`,
`test_unknown_label_becomes_conflict_when_ranker_is_absent`, and
`test_same_source_conflict_is_upserted_not_duplicated`.

The fixture must include the six critical skills plus boundary variants such as `角平分线的性质`, `三角形外接圆圆心作图`, and bare `作图`.

- [ ] **Step 2: Run and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_resolution_service.py tests/test_skill_link_service.py -q
```

- [ ] **Step 3: Implement deterministic resolution in the required order**

`SkillResolutionService` defines `AUTO_ACCEPT_MIN = 0.92` and
`AUTO_ACCEPT_MARGIN = 0.15`, and exposes
`resolve(request, persist_conflict=True) -> SkillResolution` plus
`resolve_many(requests, persist_conflicts=True) -> list[SkillResolution]`.

Order:

1. Stable key or legacy key points to exactly one active/redirectable skill.
2. Normalized name points to exactly one skill.
3. Normalized alias points to exactly one skill.
4. A prior resolved conflict for the same raw source reuses its active target.
5. Otherwise invoke the optional contextual ranker in Task 4; without one, create/open a conflict.

Do not use substring, edit distance, broad parent topic, or old confirmed mapping as an automatic exact match.

- [ ] **Step 4: Implement transactional link APIs**

`SkillLinkService` exposes
`replace_assessment_links(grading_session_id, source_question_id, links) -> None`,
`replace_question_links(question_id, links) -> None`,
`assessment_links_for_sessions(session_ids) -> list[dict[str, object]]`, and
`question_links_for_skills(skill_ids, role=None) -> list[dict[str, object]]`.
The `links` argument is `Sequence[ResolvedSkillLink]`; session IDs are strings,
question IDs are integers, and role filters use `SkillRole`.

Validate the full replacement before deleting old rows. Reject an empty resolved set or a set with no `measured` link. Resolve redirects on reads, but preserve the originally stored historical ID in evidence.

- [ ] **Step 5: Run tests**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_resolution_service.py tests/test_skill_link_service.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add question_bank/services/skill_resolution_service.py question_bank/services/skill_link_service.py tests/test_skill_resolution_service.py tests/test_skill_link_service.py tests/fixtures/skill_resolution_cases.json
git commit -m "feat: resolve and link concrete skills"
```

---

## Task 4: Add guarded contextual AI resolution and local-skill creation

**Files:**

- Create: `question_bank/services/skill_context_ranker.py`
- Modify: `question_bank/services/skill_resolution_service.py`
- Modify: `question_bank/services/skill_catalog_service.py`
- Modify: `tests/test_skill_resolution_service.py`
- Modify: `tests/fixtures/skill_resolution_cases.json`

- [ ] **Step 1: Add failing threshold and outage tests**

Test exact boundaries:

- Candidate `0.9199` conflicts; `0.92` may pass.
- Margin `0.1499` conflicts; `0.15` may pass.
- Grade mismatch conflicts even at `0.99`.
- Topic mismatch conflicts even at `0.99`.
- Ambiguity-rule hit conflicts even at `0.99`.
- Archived candidate conflicts; merged candidate follows redirect and revalidates.
- Ranker exception creates one conflict and returns normally.
- A concrete new skill at `0.92` can be created under one existing topic.
- Bare broad terms and duplicate aliases cannot create local skills.

- [ ] **Step 2: Run the focused tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_resolution_service.py -q
```

- [ ] **Step 3: Implement a narrow ranker protocol and strict JSON parser**

```python
class SkillContextRanker(Protocol):
    def rank(
        self,
        request: SkillResolutionRequest,
        candidates: Sequence[Mapping[str, object]],
    ) -> ContextRanking: ...

@dataclass(frozen=True, slots=True)
class ContextRanking:
    candidates: tuple[RankedSkillCandidate, ...]
    proposed_local_name: str = ""
    proposed_topic_key: str = ""
    proposed_aliases: tuple[str, ...] = ()
    local_confidence: float = 0.0
    ambiguity_flags: tuple[str, ...] = ()
```

`LLMSkillContextRanker` may call only `json_from_text()`. Prompt candidates are limited to active, grade-compatible skills in the hinted or inferred topic, plus a small cross-topic fallback list. Require candidate IDs from the supplied list; ignore invented IDs.

- [ ] **Step 4: Apply acceptance gates and atomic local creation**

The resolver must evaluate scores itself; never trust an AI-provided `auto_accept` boolean. A local skill stable key is deterministic:

```python
stable_key = f"local.math.{topic_slug}.{sha256(normalized_name.encode('utf-8')).hexdigest()[:12]}"
```

Insert-or-reuse by stable key in one transaction, then bind the source. Persist prompt-independent evidence: source fields, candidate scores, threshold decisions, and ambiguity flags. Do not persist secrets or full LLM responses.

- [ ] **Step 5: Run resolver tests**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_resolution_service.py tests/test_skill_catalog_seed.py -q
```

- [ ] **Step 6: Commit Task 4**

```powershell
git add question_bank/services/skill_context_ranker.py question_bank/services/skill_resolution_service.py question_bank/services/skill_catalog_service.py tests/test_skill_resolution_service.py tests/fixtures/skill_resolution_cases.json
git commit -m "feat: guard contextual skill resolution"
```

---

## Task 5: Dual-write question-bank skills on import and retagging

**Files:**

- Modify: `question_bank/models/tag_schema.py`
- Modify: `question_bank/services/ai_tagging_service.py`
- Modify: `question_bank/services/question_service.py`
- Modify: `question_bank/services/grading_paper_intake_service.py`
- Modify: `pages/题库管理.py`
- Modify: `tests/test_question_bank_service.py`
- Modify: `tests/test_question_bank_ai_tagging_quality.py`
- Modify: `tests/test_question_bank_ai_tagging_ui.py`

- [ ] **Step 1: Write failing question-link tests**

Add tests proving:

- Saving a resolved analysis creates at least one `measured` question link.
- Retagging atomically replaces prior skill links and retains raw tags as evidence.
- A contextual conflict leaves the old raw tags intact, creates an open conflict, and produces no eligible link for that resolution attempt.
- Duplicate/imported questions reuse the same resolved stable skill identity.
- AI outage still saves ordinary analysis tags and deterministic skill links.

- [ ] **Step 2: Run focused tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_question_bank_service.py tests/test_question_bank_ai_tagging_quality.py -q
```

- [ ] **Step 3: Extend tagging output with concrete skill intents**

Add to `TagAnalysis`:

```python
measured_skills: list[str] = field(default_factory=list)
supporting_skills: list[str] = field(default_factory=list)
```

Update single-item, batch, plain-output, response-format, review-merge, and mock
serialization paths so all modes produce the same fields. Prompt rules:

- `measured_skills` names the concrete skills the question directly trains.
- `supporting_skills` names prerequisites or methods used but not directly assessed.
- At least one measured skill is required for an auto-saveable result.
- Do not output chapter-sized categories or repeat generic `knowledge_points` values when a more concrete action/object is visible.

Retain `canonical_knowledge_id` and existing tag fields as legacy evidence.

- [ ] **Step 4: Resolve and write links after successful tag save**

Add to `QuestionService.save_tag_analysis()` optional keyword arguments:

```python
skill_resolver: SkillResolutionService | None = None,
resolve_skills: bool = True,
```

Build one request per measured/supporting name using the stored question text, answer, paper grade, and all tags. Resolve all requests first. Replace links only when the final resolved set contains a measured skill. If any named skill conflicts, persist that conflict independently; do not downgrade another successfully measured skill.

- [ ] **Step 5: Wire bulk import and retag entry points**

Create one resolver/ranker per batch in `grading_paper_intake_service.py` and `pages/题库管理.py`; do not instantiate an LLM client per question. Ordinary question-bank UI displays `训练技能` Chinese names and `待处理问题` count, not canonical IDs or confidence.

- [ ] **Step 6: Run tagging and service tests**

```powershell
runtime\python\python.exe -m pytest tests/test_question_bank_service.py tests/test_question_bank_ai_tagging_quality.py tests/test_question_bank_ai_tagging_ui.py -q
```

- [ ] **Step 7: Commit Task 5**

```powershell
git add question_bank/models/tag_schema.py question_bank/services/ai_tagging_service.py question_bank/services/question_service.py question_bank/services/grading_paper_intake_service.py pages/题库管理.py tests/test_question_bank_service.py tests/test_question_bank_ai_tagging_quality.py tests/test_question_bank_ai_tagging_ui.py
git commit -m "feat: link question bank items to concrete skills"
```

---

## Task 6: Dual-write assessment skills when a rubric is saved

**Files:**

- Modify: `session_manager.py`
- Modify: `web_app.py`
- Modify: `question_bank/services/skill_link_service.py`
- Create: `tests/test_assessment_skill_intake.py`
- Modify: `tests/test_grading_config_generation_policy.py`

- [ ] **Step 1: Write failing assessment intake tests**

Test generated and imported rubrics with whole questions and parts:

Use the test names `test_saved_rubric_links_each_effective_item_to_measured_skill`,
`test_question_part_uses_stable_source_ref`,
`test_multiple_measured_skills_are_preserved`,
`test_rubric_conflict_does_not_block_session_creation`, and
`test_raw_g7_ids_remain_in_rubric_evidence`.

Canonical source references are exactly the IDs already used in score details: `Q1`, `Q1.1`, and equivalent existing part IDs after `_normalize_question_knowledge_points()`.

- [ ] **Step 2: Run focused tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_assessment_skill_intake.py tests/test_grading_config_generation_policy.py -q
```

- [ ] **Step 3: Add a pure rubric-to-request extractor**

Expose from `session_manager.py` the typed API
`iter_rubric_skill_requests(payload: Mapping[str, object], grading_session_id: str) -> Iterator[tuple[str, SkillRole, SkillResolutionRequest]]`.

For each effective question/part, include `knowledge_name`, raw IDs, stem, answer/criteria text, grade, and neighboring rubric labels. Treat the primary/explicit assessed knowledge as `measured`; prerequisites or methods are `supporting` only when explicitly present.

- [ ] **Step 4: Resolve after session ID exists**

In `web_app.py`, immediately after `create_grading_session()` and after `update_grading_session_config()`, call a new `SkillLinkService.resolve_rubric(...)`. Session creation/update succeeds even when this call returns conflicts. Display only:

- `已自动识别 N 个训练技能` when complete.
- `有 N 个技能名称需要稍后处理；不影响阅卷` when conflicts exist.

Construct one contextual ranker from the LLM client already used for rubric
generation and reuse it for the whole rubric. If that client is absent or fails,
the resolver performs deterministic matching and persists the remaining conflicts.

Do not require confirmation before grading.

- [ ] **Step 5: Keep rubric prompt IDs as local source IDs, not cross-system IDs**

Update rubric prompt wording so `knowledge_id` is described as a question-local reference and `knowledge_name` as a concrete skill hint. It must not claim `G7_XX` is a universal identity. The shared `skill_id` is assigned only after normalization and remains internal.

- [ ] **Step 6: Run tests**

```powershell
runtime\python\python.exe -m pytest tests/test_assessment_skill_intake.py tests/test_grading_config_generation_policy.py tests/test_unified_rubric_rows.py -q
```

- [ ] **Step 7: Commit Task 6**

```powershell
git add session_manager.py web_app.py question_bank/services/skill_link_service.py tests/test_assessment_skill_intake.py tests/test_grading_config_generation_policy.py
git commit -m "feat: link rubric items to concrete skills"
```

---

## Task 7: Aggregate student weakness directly by assessment skill ID

**Files:**

- Modify: `db_manager.py`
- Modify: `integration/diagnosis_profile_service.py`
- Modify: `tests/test_diagnosis_profile_service.py`
- Create: `tests/test_unified_skill_recommendation.py`

- [ ] **Step 1: Write failing diagnosis tests**

Create a temporary grading DB with two sessions whose local IDs differ but whose assessment links share one skill. Assert:

- Evidence aggregates into one weak skill by `skill_id`.
- `skill_name`, `topic_name`, mastery, evidence count, source question refs, and error categories are present.
- A supporting skill does not become a weak target by itself.
- An open conflict is reported as `unresolved_count` but does not block resolved skills.
- A merged skill aggregates under its active redirect target.
- No legacy `mapping_status` is required in skill mode.

- [ ] **Step 2: Run diagnosis tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_diagnosis_profile_service.py tests/test_unified_skill_recommendation.py -q
```

- [ ] **Step 3: Add a detail-level grading evidence query**

Add to `DBManager` the API
`get_active_assessment_evidence(student_ids: Sequence[str] = (), session_ids: Sequence[int] = ()) -> list[dict[str, Any]]`.

Return one row per active `session_details` item with session ID, student, class, question ID, score awarded, full score from rubric maps, deduction/error fields, and raw knowledge IDs. Do not pre-group by knowledge text.

- [ ] **Step 4: Add the skill-based diagnosis path**

`DiagnosisProfileService.build_profiles()` reads `skill_system_settings.recommendation_read_mode`:

- `legacy`: current behavior unchanged.
- `shadow`: return skill-based diagnosis and a non-UI `legacy_comparison` summary.
- `skill`: return only skill-based diagnosis.

Skill-mode weak point shape:

```python
{
    "skill_id": 42,
    "skill_name": "角平分线性质",
    "topic_name": "三角形",
    "mastery": 0.0,
    "evidence_count": 3,
    "source_question_refs": [{"session_id": "1", "question_id": "Q1"}],
    "error_types": ["概念理解不清"],
    "eligible_for_recommendation": True,
}
```

Keep output students/scope fields compatible with `TrainingTaskService` snapshots.

- [ ] **Step 5: Run diagnosis tests in both modes**

```powershell
runtime\python\python.exe -m pytest tests/test_diagnosis_profile_service.py tests/test_unified_skill_recommendation.py tests/test_knowledge_practice_end_to_end.py -q
```

- [ ] **Step 6: Commit Task 7**

```powershell
git add db_manager.py integration/diagnosis_profile_service.py tests/test_diagnosis_profile_service.py tests/test_unified_skill_recommendation.py
git commit -m "feat: diagnose weakness by shared skill id"
```

---

## Task 8: Recommend exact skills first and require one explicit shortage decision

**Files:**

- Modify: `question_bank/recommendation/practice_plan_service.py`
- Modify: `question_bank/services/training_task_service.py`
- Modify: `tests/test_practice_plan_service.py`
- Modify: `tests/test_practice_candidate_scoring.py`
- Modify: `tests/test_knowledge_practice_end_to_end.py`
- Modify: `tests/test_unified_skill_recommendation.py`

- [ ] **Step 1: Write failing exactness and shortage tests**

Cover:

- Same `skill_id` + measured link is exact.
- Same topic but different skill is not exact.
- Same `skill_id` + supporting link is not exact.
- Textually similar old tags cannot qualify without a question skill link.
- Default `related_fill_policy='ask'` returns fewer exact questions and `decision_required=True`.
- `exact_only` keeps the smaller set.
- `allow_neighbors` may fill only from enabled `skill_neighbors`, labels every fill `match_kind='neighbor'`, and records reason.
- A neighbor may never be displayed or snapshotted as exact.
- The current-exam original/near-duplicate exclusion still applies.

- [ ] **Step 2: Run focused recommendation tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py tests/test_unified_skill_recommendation.py -q
```

- [ ] **Step 3: Replace skill-mode candidate loading**

In skill mode, `_load_candidates_and_relations()` reads:

- `question_skill_links` for active question links.
- `skills`/`skill_topics` for labels.
- `skill_neighbors` only when the policy explicitly permits it.

Do not read `knowledge_source_mappings` or infer exactness from `question_tags`. Keep the legacy loader callable only in `legacy`/`shadow` comparison.

- [ ] **Step 4: Add an explicit policy contract**

Define `RelatedFillPolicy = Literal["ask", "exact_only", "allow_neighbors"]`
and add `related_fill_policy: RelatedFillPolicy = "ask"` to the existing
`PracticePlanService.generate(...) -> dict[str, Any]` signature.

Each shortage entry contains `skill_id`, `skill_name`, `requested`, `exact_found`, `missing`, `decision_required`, and available neighbor count. `allow_neighbors` entries also contain selected neighbor names and relation kinds.

- [ ] **Step 5: Persist the decision and explanation**

Ensure `TrainingTaskService.create_task()` stores `related_fill_policy` in generation config and each selected item's recommendation snapshot contains:

```python
{
    "match_kind": "exact" | "neighbor",
    "target_skill_id": 42,
    "matched_skill_id": 42,
    "neighbor_kind": "",
    "reason": "直接训练：角平分线性质",
}
```

For neighbor fill, `matched_skill_id` differs and `neighbor_kind` is non-empty.

- [ ] **Step 6: Run recommendation and end-to-end tests**

```powershell
runtime\python\python.exe -m pytest tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py tests/test_unified_skill_recommendation.py tests/test_knowledge_practice_end_to_end.py -q
```

- [ ] **Step 7: Commit Task 8**

```powershell
git add question_bank/recommendation/practice_plan_service.py question_bank/services/training_task_service.py tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py tests/test_knowledge_practice_end_to_end.py tests/test_unified_skill_recommendation.py
git commit -m "feat: recommend exact skills before neighbors"
```

---

## Task 9: Build dry-run, backup, idempotent migration, and rollback controls

**Files:**

- Create: `question_bank/services/skill_migration_service.py`
- Create: `update_tools/migrate_skill_catalog.py`
- Create: `tests/test_skill_migration_service.py`
- Create: `tests/fixtures/skill_migration_gold.json`
- Modify: `question_bank/services/skill_catalog_service.py`
- Modify: `update_tools/backup_core.py`

- [ ] **Step 1: Write failing migration safety tests**

Tests must prove:

- Dry-run leaves both database hashes and rubric file hashes unchanged.
- Apply creates backups before the first write.
- Applying the same batch ID twice creates no extra skills, links, conflicts, or run rows.
- A failed run rolls back its question-bank transaction and leaves read mode `legacy`.
- Question count, grading result count, student count, historical task count, and original tags are unchanged.
- All effective source items end in either a measured link or an open conflict.
- Gold precision calculation uses at least 100 fixture rows and reports pass only at `>= 0.98`.
- Coverage reports pass only at `>= 0.95`.
- Rollback restores both database backups and records rollback status.

- [ ] **Step 2: Run migration tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_migration_service.py -q
```

- [ ] **Step 3: Implement a report-first migration service**

Create immutable `SkillMigrationConfig` with fields `grading_db: Path`,
`question_bank_db: Path`, `report_dir: Path`, `batch_id: str`, and
`gold_file: Path | None = None`. `SkillMigrationService` exposes
`dry_run(config) -> dict[str, object]`, `apply(config) -> dict[str, object]`, and
`rollback(question_bank_db: Path, batch_id: str) -> dict[str, object]`.

The report has four source result classes: `resolved_existing`, `created_local`, `conflict`, `insufficient_evidence`. Include per-source evidence, counts, coverage, gold precision, before/after invariants, and readiness reasons. Never include API keys. Dry-run copies both databases and required rubric files into a temporary directory, initializes and resolves only the copies, writes only the JSON report to the requested report directory, and compares original hashes before returning.

- [ ] **Step 4: Implement complete source enumeration**

Question-bank migration enumerates every non-deleted question and uses question text, answer, paper grade, `knowledge_point`, `canonical_knowledge_id`, `sub_skill`, prerequisite, method, and model tags.

Assessment migration enumerates every non-deleted grading session whose rubric exists, then every effective question/part from `iter_rubric_skill_requests()`. Missing rubric files become `insufficient_evidence`, never silent omissions. Enumerate current active weak terms in the report and show which assessment links account for all 17 observed terms.

- [ ] **Step 5: Add guarded CLI commands**

Required commands:

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog dry-run --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json

runtime\python\python.exe -m update_tools.migrate_skill_catalog apply --batch-id skill-v1-20260622 --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json

runtime\python\python.exe -m update_tools.migrate_skill_catalog rollback --batch-id skill-v1-20260622 --question-bank-db user_data/databases/question_bank.db
```

`apply` must refuse to start if dry-run coverage is below 95%, gold set has fewer than 100 reviewed rows, precision is below 98%, a backup fails, or another run is active. It writes links but leaves read mode `legacy`; cutover is a separate Task 12 command.

- [ ] **Step 6: Run migration tests**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_migration_service.py tests/test_skill_resolution_service.py tests/test_skill_link_service.py -q
```

- [ ] **Step 7: Commit Task 9**

```powershell
git add question_bank/services/skill_migration_service.py question_bank/services/skill_catalog_service.py update_tools/migrate_skill_catalog.py update_tools/backup_core.py tests/test_skill_migration_service.py tests/fixtures/skill_migration_gold.json
git commit -m "feat: migrate skill links with safety gates"
```

---

## Task 10: Replace the advanced graph workbench with a skill catalog and conflict inbox

**Files:**

- Modify: `pages/知识点整理（高级）.py`
- Modify: `question_bank/services/skill_catalog_service.py`
- Create: `tests/test_skill_catalog_ui.py`
- Modify: `tests/test_knowledge_alignment_ui.py`

- [ ] **Step 1: Write failing administrator UI contract tests**

Read the page source and/or exercise pure helpers to assert:

- Page title is `技能目录与冲突`.
- It has sections `技能目录`, `待处理问题`, `覆盖情况`, and `迁移记录`.
- It does not expose ordinary mapping workbench controls, focus filtering, relation editor, namespace, English mapping states, confidence sliders, or weight sliders.
- Conflict decisions offer Chinese skill candidates plus `新建本校技能` and `暂不处理`.
- Merging requires source, target, evidence preview, and explicit confirmation.
- Coverage separates rubric items and question-bank items into resolved/conflict counts.

- [ ] **Step 2: Run UI tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_ui.py tests/test_knowledge_alignment_ui.py -q
```

- [ ] **Step 3: Replace page internals while keeping the file path stable**

Retain `pages/知识点整理（高级）.py` so Streamlit bookmarks do not break, but remove calls to `ConceptAlignmentService`, `AlignmentReviewService`, and the graph relation editor. Use `SkillCatalogService` to render:

- Topic filter and searchable concrete skill table.
- Local-skill evidence and merge action.
- Open conflict cards showing source context and 2–3 candidates.
- Coverage cards and migration-run status.
- Read-only neighbor preview with a disable action for clearly wrong links.

Never show raw IDs unless the administrator opens a collapsed `技术详情` expander.

- [ ] **Step 4: Implement conflict decisions transactionally**

Add service APIs `resolve_conflict(conflict_id, skill_id, actor) -> None`,
`create_local_from_conflict(conflict_id, name, topic_id, actor) -> int`,
`ignore_conflict(conflict_id, actor) -> None`, and
`set_neighbor_enabled(neighbor_id, enabled, actor) -> None`.

Resolving a conflict must create/refresh the appropriate assessment or question link and close the conflict in one transaction.

- [ ] **Step 5: Run UI/service tests**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_ui.py tests/test_knowledge_alignment_ui.py tests/test_skill_catalog_seed.py tests/test_skill_link_service.py -q
```

- [ ] **Step 6: Commit Task 10**

```powershell
git add pages/知识点整理（高级）.py question_bank/services/skill_catalog_service.py tests/test_skill_catalog_ui.py tests/test_knowledge_alignment_ui.py
git commit -m "feat: replace graph workbench with skill inbox"
```

---

## Task 11: Simplify the ordinary teacher training flow

**Files:**

- Modify: `pages/训练推荐.py`
- Modify: `pages/题库管理.py`
- Modify: `tests/test_training_recommendation_ui.py`
- Modify: `tests/test_question_bank_ai_tagging_ui.py`
- Modify: `tests/test_knowledge_practice_end_to_end.py`

- [ ] **Step 1: Write failing teacher UI tests**

Assert the ordinary training page:

- Shows `薄弱技能`, `所属主题`, `掌握率`, `证据题数`, and `精确题数`.
- Does not show standard knowledge points, mapping state, confirmation workbench, focus mode, graph, namespace, confidence, canonical IDs, or broad-fallback checkbox.
- Does not contain a link requiring the teacher to open the advanced page before generating.
- Shows no confirmation action when all selected evidence is resolved.
- For unresolved current evidence, shows a compact Chinese choice only for that ambiguity and allows skipping it for this plan.
- For shortage, shows one decision: `补入相近题` or `保持较少的精确题`.
- Labels every neighbor result `相近补入` with both concrete skill names.

- [ ] **Step 2: Run UI tests and confirm red**

```powershell
runtime\python\python.exe -m pytest tests/test_training_recommendation_ui.py tests/test_question_bank_ai_tagging_ui.py -q
```

- [ ] **Step 3: Replace diagnosis rendering**

Remove `ALIGNMENT_FOCUS_SESSION_KEY`, `ConceptAlignmentService`, `AlignmentReviewService`, focus-item cards, and `打开知识点整理（高级）`. Render the skill-mode diagnosis as a compact table and heatmap keyed by `skill_name`.

When a selected scope has open conflicts, show only source-local choices returned by the conflict service. A teacher decision resolves that source; `本次跳过` excludes it from this plan without changing catalog history.

- [ ] **Step 4: Implement one shortage decision**

Generate initially with `related_fill_policy='ask'`. If no shortages, continue directly. If shortages exist, render one radio group for the whole task:

```text
精确题数量不足：
○ 补入相近技能题，并在练习中标明
○ 保持较少的精确题
```

Regenerate once using `allow_neighbors` or `exact_only`; store the selected policy in session state and task snapshot. Remove `允许仅大类匹配的题目补足（不推荐）`.

- [ ] **Step 5: Simplify question-bank feedback**

After import/tagging, show `已识别 N 个训练技能；M 个问题进入后台待处理`. Do not ask an ordinary teacher to approve IDs or open a mapping page. Existing raw analysis fields may remain in an administrator-only expander.

- [ ] **Step 6: Run UI and end-to-end tests**

```powershell
runtime\python\python.exe -m pytest tests/test_training_recommendation_ui.py tests/test_question_bank_ai_tagging_ui.py tests/test_knowledge_practice_end_to_end.py tests/test_unified_skill_recommendation.py -q
```

- [ ] **Step 7: Commit Task 11**

```powershell
git add pages/训练推荐.py pages/题库管理.py tests/test_training_recommendation_ui.py tests/test_question_bank_ai_tagging_ui.py tests/test_knowledge_practice_end_to_end.py
git commit -m "feat: simplify training around concrete skills"
```

---

## Task 12: Shadow comparison, production migration, cutover, and full verification

**Files:**

- Modify: `question_bank/services/skill_catalog_service.py`
- Modify: `question_bank/services/skill_migration_service.py`
- Modify: `update_tools/migrate_skill_catalog.py`
- Modify: `tests/test_skill_migration_service.py`
- Modify: `README_工作机使用说明.md`
- Modify: `README_私人便携版_v1.5.0.md`

- [ ] **Step 1: Add failing rollout-state tests**

Test allowed transitions only:

```text
legacy -> shadow -> skill
skill -> legacy  (emergency rollback only, with audit reason)
```

Refuse `shadow -> skill` unless the latest applied batch passes coverage, gold precision, count invariants, zero silent unknowns, and shadow comparison has no unexplained exact-match divergence.

- [ ] **Step 2: Implement audited read-mode commands**

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode shadow --question-bank-db user_data/databases/question_bank.db --reason "compare unified skill recommendations"

runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode skill --question-bank-db user_data/databases/question_bank.db --batch-id skill-v1-20260622 --reason "acceptance gates passed"

runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode legacy --question-bank-db user_data/databases/question_bank.db --reason "emergency rollback"
```

Store actor/time/reason in the migration audit. The `skill` command must enforce gates, not merely warn.

- [ ] **Step 3: Run the complete automated suite before touching production data**

```powershell
runtime\python\python.exe -m pytest tests/test_skill_catalog_schema.py tests/test_skill_catalog_seed.py tests/test_skill_resolution_service.py tests/test_skill_link_service.py tests/test_assessment_skill_intake.py tests/test_question_bank_service.py tests/test_question_bank_ai_tagging_quality.py tests/test_diagnosis_profile_service.py tests/test_practice_plan_service.py tests/test_unified_skill_recommendation.py tests/test_skill_migration_service.py tests/test_skill_catalog_ui.py tests/test_training_recommendation_ui.py tests/test_knowledge_practice_end_to_end.py -q
```

Expected: all pass with no skipped safety tests.

- [ ] **Step 4: Run production dry-run only and inspect the report**

First checkpoint command:

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog dry-run --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json
```

Inspect the newest report and verify:

- Every non-deleted question and every effective rubric item is counted.
- All 17 currently active weak terms are accounted for.
- Coverage is at least 95%.
- The reviewed 100-item gold set precision is at least 98%.
- Question, result, student, and historical task counts are unchanged.
- Critical cases resolve to their concrete skills rather than coarse topics.

If any gate fails, stop here, fix catalog/resolver code or resolve gold disagreements, rerun tests, and repeat dry-run. Do not apply or cut over.

- [ ] **Step 5: Apply one guarded production migration batch**

After every dry-run gate passes:

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog apply --batch-id skill-v1-20260622 --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json
```

Immediately rerun the same command and confirm it reports the existing successful batch without changing counts. Keep mode `legacy`.

- [ ] **Step 6: Enable shadow mode and compare outputs**

Set mode to `shadow`, select representative students/exams covering the six critical cases, and generate comparison output without exposing the old path to teachers. Require:

- Every skill-mode exact recommendation has the same skill ID on assessment and question links.
- No topic-only or supporting-only candidate appears as exact.
- Differences from legacy are either removal of coarse matches or a documented concrete-skill improvement.
- Shortages remain shortages until the explicit neighbor policy is selected.

- [ ] **Step 7: Browser-verify the ordinary and administrator flows**

Start the app if needed:

```powershell
runtime\python\python.exe -m streamlit run web_app.py --server.address 127.0.0.1 --server.port 8501
```

Use the in-app browser to verify at desktop width and a narrow width:

1. Select student and exam, analyze, and see concrete weak skills without mapping controls.
2. Generate enough exact questions without any confirmation click.
3. Trigger a shortage and make exactly one related-fill choice.
4. Confirm neighbor questions are visibly labeled `相近补入`.
5. Open `技能目录与冲突`, resolve one fixture conflict, and verify the link updates.
6. Refresh both pages and confirm state persists and no console/server traceback appears.

- [ ] **Step 8: Cut over only after shadow and browser acceptance**

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode skill --question-bank-db user_data/databases/question_bank.db --batch-id skill-v1-20260622 --reason "coverage, precision, shadow, and UI acceptance passed"
```

Run one final real diagnosis/preview without saving a new task, then verify the application health endpoint and logs.

- [ ] **Step 9: Update operator documentation**

Document:

- What ordinary teachers now see and why no mapping confirmation is needed.
- Meaning of exact vs `相近补入`.
- Administrator conflict workflow.
- Dry-run/apply/set-mode/rollback commands.
- Backup locations and emergency return to `legacy`.
- Old mapping tables are read-only compatibility data and must not be manually deleted.

- [ ] **Step 10: Run full regression and inspect git scope**

```powershell
runtime\python\python.exe -m pytest -q
git status --short
git diff --check
```

Expected: all tests pass; `git diff --check` is empty; only implementation/docs are staged or modified by this work. User databases, reports, backups, and other pre-existing files remain uncommitted.

- [ ] **Step 11: Commit Task 12 code and docs only**

```powershell
git add question_bank/services/skill_catalog_service.py question_bank/services/skill_migration_service.py update_tools/migrate_skill_catalog.py tests/test_skill_migration_service.py README_工作机使用说明.md README_私人便携版_v1.5.0.md
git commit -m "feat: cut over to unified skill recommendations"
```

Do not stage migration reports, database files, backups, runtime files, or `.superpowers/brainstorm`.

---

## Completion Evidence

The implementation is complete only when all of the following evidence is available:

- Fresh full-test output from `runtime\python\python.exe -m pytest -q`.
- A successful production dry-run report and applied migration report for one batch ID.
- Coverage `>= 95%`, 100-item gold precision `>= 98%`, and zero silent unknown source items.
- Before/after counts proving no question, score result, student, or historical task loss.
- Idempotency output from re-running the same apply batch.
- Shadow comparison explaining recommendation differences.
- Browser verification of normal, conflict, exact-shortage, and neighbor-fill paths.
- Audited cutover state `skill`, with a tested `legacy` rollback command.
- A clean implementation diff that excludes all user data and generated reports.
