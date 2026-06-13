# Knowledge Graph Question Bank Practice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有批改后的学生薄弱知识图谱与带标签题库连接起来，支持教师按单个学生、筛选后的多个学生或全部班级生成个性化/分组练习，并为未来训练结果回流与难度校准预留稳定数据结构。

**Architecture:** 以题库数据库作为知识点桥接、推荐方案和训练任务的持久化中心，批改数据库继续作为考试、学生与得分事实源。`DiagnosisProfileService` 负责从真实批改数据构建诊断画像，`ConceptAlignmentService` 负责将诊断词和题库标签统一到稳定标准知识点，`PracticePlanService` 负责硬筛选、排序、梯度组题和分组，`TrainingTaskService` 负责保存可追溯快照，`TrainingExportService` 负责学生卷、教师卷和 ZIP 的导出及失败重试。两个数据库之间只保存稳定引用与快照，不建立跨库外键。

**Tech Stack:** Python 3, SQLite, Streamlit, pytest, python-docx, existing `DBManager`, existing question-bank services/exporters.

---

## Execution Guardrails

- 当前工作区可能包含教师正在进行的题库、组卷、导出器和本地数据库改动。每个任务开始前运行 `git status --short --branch`，只暂存本任务列出的文件，不覆盖或回退无关改动。
- 不直接修改或提交 `user_data/databases/*.db`。数据库结构通过迁移和 `initialize_database()` 创建，测试使用临时 SQLite 文件。
- 不把低价值错因作为推荐核心信号，也不要求批改 AI 返回每道题错因。只有明确、已确认的错因可作为可选弱信号保存，但第一版排序不使用它。
- 不根据学生原错题难度推断学生能力。候选题难度仅用于组织训练梯度；候选题缺少难度时按中性值处理并显示提示。
- 未确认或低置信知识点映射不能静默参与推荐。题量不足时必须返回结构化缺口说明。
- 当前考试原题排除依赖可靠的题源关联；无法确认全部原题时必须显示警告，不能仅按题号排除。

## Target Data Contracts

### Diagnosis profile

```python
{
    "scope": {"mode": "student|selected|class", "student_ids": ["12"]},
    "exam_scope": {"mode": "current|cross_exam|manual", "session_ids": [14]},
    "students": [
        {
            "student_id": "12",
            "student_name": "张三",
            "class_id": "九年级1班",
            "score_rate": 0.63,
            "weak_points": [
                {
                    "source_term": "二次函数图像与系数",
                    "concept_id": 18,
                    "concept_name": "二次函数图像与性质",
                    "mapping_status": "confirmed",
                    "mastery": 0.42,
                    "evidence_count": 3,
                    "source_question_refs": [
                        {"session_id": 14, "question_id": "17", "score_rate": 0.25}
                    ],
                }
            ],
        }
    ],
    "warnings": [],
}
```

### Practice plan

```python
{
    "scope_snapshot": {},
    "diagnosis_snapshot": {},
    "generation_config": {
        "question_count": 10,
        "stage_ratios": {"direct": 0.60, "prerequisite": 0.25, "transfer": 0.15},
        "weights": {"concept": 0.40, "frequency": 0.35, "gradient": 0.10, "diversity": 0.15},
        "exclude_current_exam_originals": True,
        "include_historical_wrong_questions": False,
    },
    "variants": [
        {
            "variant_key": "student-12",
            "variant_type": "individual",
            "student_ids": ["12"],
            "items": [],
            "shortages": [],
            "warnings": [],
        }
    ],
}
```

## Milestone 1: Stable Concept Bridge

### Task 1: Add the knowledge-alignment and training-task schema

**Files:**
- Create: `migrations/question_bank/006_add_knowledge_alignment_and_training_tasks.sql`
- Modify: `question_bank/database/schema.py`
- Create: `tests/test_knowledge_practice_schema.py`

- [ ] **Step 1: Write the failing schema test**

```python
from question_bank.database.schema import connect, initialize_database


EXPECTED_TABLES = {
    "knowledge_concepts",
    "knowledge_relations",
    "knowledge_source_mappings",
    "grading_question_links",
    "training_tasks",
    "training_variants",
    "variant_students",
    "training_task_items",
    "training_exports",
    "training_attempts",
}


def test_initialize_database_creates_knowledge_practice_tables(tmp_path):
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    assert EXPECTED_TABLES.issubset({row["name"] for row in rows})
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python -m pytest tests/test_knowledge_practice_schema.py -q`

Expected: FAIL because the new tables do not exist.

- [ ] **Step 3: Add the migration and mirror it in `initialize_database()`**

Create these tables and indexes:

- `knowledge_concepts`: stable concept identity, name, aliases JSON, subject/grade/status.
- `knowledge_relations`: `source_concept_id`, `target_concept_id`, relation type (`prerequisite`, `related`, `parent`), weight.
- `knowledge_source_mappings`: source namespace/value to concept, status (`confirmed`, `suggested`, `rejected`), confidence, evidence and reviewer timestamps.
- `grading_question_links`: grading session/question reference to bank question, link method, confidence and status.
- `training_tasks`: task-level scope, diagnosis and generation snapshots, status.
- `training_variants`: individual/group variant metadata and shortage/warning snapshots.
- `variant_students`: variant-to-student assignment.
- `training_task_items`: stable task item code, bank question ID/fingerprint, stage, order and recommendation snapshot.
- `training_exports`: audience, format, path, status, error and retry count.
- `training_attempts`: reserved future attempt/evidence contract; no first-version UI.

Use SQLite checks where practical, unique indexes for stable identities, and regular indexes for all lookup keys. Keep bank-local foreign keys only.

- [ ] **Step 4: Add a contract test for stable uniqueness**

```python
def test_source_mapping_identity_is_unique(tmp_path):
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with connect(db_path) as conn:
        concept_id = conn.execute(
            "INSERT INTO knowledge_concepts (canonical_key, name) VALUES (?, ?)",
            ("math.quadratic_function", "二次函数"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO knowledge_source_mappings
                (source_namespace, source_value, concept_id, status, confidence)
            VALUES (?, ?, ?, 'confirmed', 1.0)
            """,
            ("grading_weak_point", "二次函数图像", concept_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO knowledge_source_mappings
                    (source_namespace, source_value, concept_id, status, confidence)
                VALUES (?, ?, ?, 'confirmed', 1.0)
                """,
                ("grading_weak_point", "二次函数图像", concept_id),
            )
```

- [ ] **Step 5: Run schema tests**

Run: `python -m pytest tests/test_knowledge_practice_schema.py tests/test_question_bank_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add migrations/question_bank/006_add_knowledge_alignment_and_training_tasks.sql question_bank/database/schema.py tests/test_knowledge_practice_schema.py
git commit -m "feat: add knowledge practice data model"
```

### Task 2: Implement concept and source-mapping domain models

**Files:**
- Create: `question_bank/models/knowledge_alignment.py`
- Modify: `question_bank/models/__init__.py`
- Create: `tests/test_knowledge_alignment_models.py`

- [ ] **Step 1: Write failing normalization and validation tests**

```python
from question_bank.models.knowledge_alignment import (
    AlignmentStatus,
    KnowledgeSourceMapping,
    normalize_source_value,
)


def test_normalize_source_value_is_stable():
    assert normalize_source_value(" 二次函数　图像 ") == "二次函数 图像"


def test_confirmed_mapping_requires_full_confidence():
    mapping = KnowledgeSourceMapping(
        source_namespace="grading_weak_point",
        source_value="二次函数图像",
        concept_id=7,
        status=AlignmentStatus.CONFIRMED,
        confidence=0.75,
    )
    assert mapping.confidence == 1.0
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_knowledge_alignment_models.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement typed dataclasses/enums**

Implement:

```python
class AlignmentStatus(str, Enum):
    CONFIRMED = "confirmed"
    SUGGESTED = "suggested"
    REJECTED = "rejected"
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class KnowledgeConcept:
    id: int
    canonical_key: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeSourceMapping:
    source_namespace: str
    source_value: str
    concept_id: int | None
    status: AlignmentStatus
    confidence: float
```

Normalize whitespace/case for lookup but preserve display values. Clamp suggested confidence to `0..0.99`; confirmed mappings become `1.0`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_knowledge_alignment_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add question_bank/models/knowledge_alignment.py question_bank/models/__init__.py tests/test_knowledge_alignment_models.py
git commit -m "feat: add knowledge alignment domain models"
```

### Task 3: Build `ConceptAlignmentService`

**Files:**
- Create: `question_bank/services/concept_alignment_service.py`
- Modify: `question_bank/taxonomy/registry.py`
- Create: `tests/test_concept_alignment_service.py`

- [ ] **Step 1: Write failing service tests**

```python
def test_confirmed_mapping_resolves_directly(alignment_service):
    concept = alignment_service.create_concept("math.quadratic", "二次函数")
    alignment_service.confirm_mapping(
        source_namespace="grading_weak_point",
        source_value="二次函数图像",
        concept_id=concept.id,
    )

    resolved = alignment_service.resolve("grading_weak_point", "二次函数图像")

    assert resolved.status.value == "confirmed"
    assert resolved.concept.id == concept.id


def test_suggested_mapping_never_becomes_eligible_without_confirmation(alignment_service):
    alignment_service.create_concept("math.quadratic", "二次函数", aliases=["抛物线"])

    resolved = alignment_service.resolve("grading_weak_point", "抛物线性质")

    assert resolved.status.value == "suggested"
    assert resolved.eligible_for_recommendation is False
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_concept_alignment_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement CRUD and resolution**

The service must:

- create/update/list concepts and relations;
- create/confirm/reject mappings;
- resolve by exact confirmed mapping first;
- generate deterministic suggestions from concept name, aliases, and existing registry canonicalization;
- return `unmapped` when no safe suggestion exists;
- expose coverage metrics grouped by namespace/status;
- never persist a suggestion as confirmed automatically.

Use the registry only to seed/fallback suggestions; persisted mappings become the source of truth.

- [ ] **Step 4: Add batch confirmation and question-tag mapping tests**

```python
def test_batch_confirm_is_atomic(alignment_service):
    concept = alignment_service.create_concept("math.function", "函数")
    results = alignment_service.confirm_many(
        [
            ("grading_weak_point", "函数关系", concept.id),
            ("question_tag", "函数", concept.id),
        ]
    )
    assert [item.status.value for item in results] == ["confirmed", "confirmed"]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_concept_alignment_service.py tests/test_knowledge_alignment_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/services/concept_alignment_service.py question_bank/taxonomy/registry.py tests/test_concept_alignment_service.py
git commit -m "feat: add persistent concept alignment service"
```

### Task 4: Replace the mapping debugger with a knowledge alignment center

**Files:**
- Modify: `pages/知识图谱适配调试.py`
- Create: `tests/test_knowledge_alignment_ui.py`

- [ ] **Step 1: Write failing UI source-contract tests**

```python
def test_alignment_page_uses_persistent_service():
    source = Path("pages/知识图谱适配调试.py").read_text(encoding="utf-8")
    assert "ConceptAlignmentService" in source
    assert "knowledge_mapping.json" not in source
    assert "load_sample_mastery_rows" not in source


def test_alignment_page_exposes_batch_confirmation():
    source = Path("pages/知识图谱适配调试.py").read_text(encoding="utf-8")
    assert "批量确认" in source
    assert "待确认" in source
    assert "未映射" in source
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_knowledge_alignment_ui.py -q`

Expected: FAIL because the page still uses JSON/sample data.

- [ ] **Step 3: Rework the page**

The page must show:

- summary cards: confirmed, suggested, unmapped, rejected counts;
- source namespace filter (`grading_weak_point`, `question_tag`, `canonical_knowledge_id`);
- concentrated pending-mapping table with suggestion, confidence and evidence count;
- row confirmation, batch confirmation, rejection and reassignment;
- concept and relation editor;
- an inline entry point contract that the training page can use when generation finds unmapped terms.

Do not silently convert suggestions to confirmed mappings.

- [ ] **Step 4: Run UI and service tests**

Run: `python -m pytest tests/test_knowledge_alignment_ui.py tests/test_concept_alignment_service.py -q`

Expected: PASS.

- [ ] **Step 5: Manually verify the page**

Run: `streamlit run app.py`

Expected: the alignment center loads real namespaces, confirms a mapping, and shows the changed coverage count after refresh.

- [ ] **Step 6: Commit**

```powershell
git add pages/知识图谱适配调试.py tests/test_knowledge_alignment_ui.py
git commit -m "feat: add knowledge alignment center"
```

## Milestone 2: Real Diagnosis and Reliable Source Linking

### Task 5: Build reliable grading-question to bank-question links

**Files:**
- Create: `question_bank/services/source_question_link_service.py`
- Modify: `question_bank/services/grading_paper_intake_service.py`
- Create: `tests/test_source_question_link_service.py`

- [ ] **Step 1: Write failing link-policy tests**

```python
def test_confirmed_link_can_exclude_current_exam_original(link_service):
    link_service.confirm_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        link_method="fingerprint",
    )
    assert link_service.confirmed_bank_question_ids(14) == {201}


def test_suggested_link_is_not_used_for_exclusion(link_service):
    link_service.suggest_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        confidence=0.88,
        link_method="text_similarity",
    )
    assert link_service.confirmed_bank_question_ids(14) == set()
    assert link_service.exclusion_warning(14)
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_source_question_link_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement link suggestion and confirmation**

Use this precedence:

1. exact source metadata/fingerprint match;
2. exact normalized question text match;
3. high text-similarity suggestion using existing `similarity_service`;
4. unresolved.

Only `confirmed` links may exclude current-exam originals. Persist link method, confidence and evidence JSON.

- [ ] **Step 4: Integrate link creation with grading-paper intake**

When a grading paper is imported/tagged into the question bank, create confirmed links where stable metadata/fingerprints prove identity and suggested links otherwise. Do not overwrite an existing teacher-confirmed link.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_source_question_link_service.py tests/test_question_bank_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/services/source_question_link_service.py question_bank/services/grading_paper_intake_service.py tests/test_source_question_link_service.py
git commit -m "feat: link grading questions to question bank"
```

### Task 6: Build `DiagnosisProfileService` from real grading data

**Files:**
- Create: `integration/diagnosis_profile_service.py`
- Create: `tests/test_diagnosis_profile_service.py`

- [ ] **Step 1: Write failing scope and score-rate tests**

```python
def test_current_exam_single_student_profile_uses_full_score_weighting(service):
    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )
    weak = profile["students"][0]["weak_points"][0]
    assert weak["mastery"] == pytest.approx(6 / 10)
    assert weak["source_question_refs"][0]["session_id"] == 14


def test_selected_students_and_manual_sessions_are_respected(service):
    profile = service.build_profiles(
        scope={"mode": "selected", "student_ids": ["12", "15"]},
        exam_scope={"mode": "manual", "session_ids": [12, 14]},
    )
    assert {item["student_id"] for item in profile["students"]} == {"12", "15"}
    assert profile["exam_scope"]["session_ids"] == [12, 14]
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_diagnosis_profile_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement real-data profile construction**

Use `DBManager` methods:

- `list_students`
- `list_grading_sessions`
- `get_grading_session`
- `get_session_weak_points`
- `get_active_global_weak_points`
- `get_active_student_score_rates`

Requirements:

- support `student`, `selected`, and `class` scope;
- support `current`, `cross_exam`, and `manual` exam scope;
- aggregate mastery using awarded/full score evidence, never clamp raw awarded points to `0..1`;
- resolve weak-point source terms through `ConceptAlignmentService`;
- return confirmed concept IDs, suggested/unmapped terms and warnings separately;
- retain stable session/question references for future attempt links;
- ignore generic error reasons such as `未作答`, `未选择正确答案`, and `答案不等价` for recommendation purposes.

- [ ] **Step 4: Add unmapped-term tests**

```python
def test_unmapped_terms_are_visible_and_not_eligible(service):
    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )
    assert profile["unmapped_terms"] == ["陌生诊断词"]
    assert profile["students"][0]["weak_points"][0]["eligible_for_recommendation"] is False
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_diagnosis_profile_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add integration/diagnosis_profile_service.py tests/test_diagnosis_profile_service.py
git commit -m "feat: build real grading diagnosis profiles"
```

## Milestone 3: Recommendation, Gradient Planning, and Task Persistence

### Task 7: Redesign recommendation scoring around the agreed policy

**Files:**
- Modify: `question_bank/recommendation/scoring.py`
- Modify: `tests/test_recommendation_frequency_scoring.py`
- Create: `tests/test_practice_candidate_scoring.py`

- [ ] **Step 1: Write failing hard-gate and weight tests**

```python
def test_candidate_without_confirmed_concept_match_is_ineligible():
    result = score_candidate(
        concept_match=0.0,
        mapping_status="suggested",
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
    )
    assert result.eligible is False
    assert result.total_score == 0.0


def test_default_weights_prioritize_concept_and_frequency():
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        frequency_fit=0.8,
        gradient_fit=0.5,
        diversity_fit=0.5,
    )
    assert result.total_score == pytest.approx(0.805)
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_practice_candidate_scoring.py -q`

Expected: FAIL because `score_candidate` does not exist.

- [ ] **Step 3: Implement scoring v2**

Default configurable weights:

```python
DEFAULT_WEIGHTS = {
    "concept": 0.40,
    "frequency": 0.35,
    "gradient": 0.10,
    "diversity": 0.15,
}
```

Rules:

- confirmed concept mapping is a hard eligibility gate;
- concept score can reflect direct/prerequisite/related relation strength;
- frequency combines local/深圳 fit and wider frequency using existing `QuestionFrequencyService`;
- missing difficulty gives neutral gradient score `0.5` plus warning;
- diversity reflects unseen method/model/paper source;
- no core error-type weight;
- reject invalid custom weight totals instead of silently normalizing.

- [ ] **Step 4: Preserve existing frequency behavior tests**

Update old tests only where the agreed policy intentionally changes behavior. Keep explicit assertions for 深圳/local fit data.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_practice_candidate_scoring.py tests/test_recommendation_frequency_scoring.py tests/test_question_frequency_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/recommendation/scoring.py tests/test_practice_candidate_scoring.py tests/test_recommendation_frequency_scoring.py
git commit -m "feat: apply knowledge and frequency recommendation policy"
```

### Task 8: Implement three-stage practice planning and explicit shortages

**Files:**
- Create: `question_bank/recommendation/practice_plan_service.py`
- Modify: `question_bank/recommendation/recommendation_engine.py`
- Modify: `question_bank/recommendation/training_plan.py`
- Create: `tests/test_practice_plan_service.py`

- [ ] **Step 1: Write failing ratio, exclusion and dedupe tests**

```python
def test_default_ten_question_plan_uses_agreed_stage_mix(service):
    plan = service.generate_variant(diagnosis_profile, question_count=10)
    counts = Counter(item["stage"] for item in plan["items"])
    assert counts == {"direct": 6, "prerequisite": 3, "transfer": 1}


def test_current_exam_original_and_near_duplicate_are_excluded(service):
    plan = service.generate_variant(
        diagnosis_profile,
        question_count=10,
        exclude_question_ids={201},
    )
    assert 201 not in {item["question_id"] for item in plan["items"]}
    assert plan["dedupe_summary"]["removed_count"] >= 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_practice_plan_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement candidate retrieval and stage allocation**

Default stage mix:

- `direct`: 60%, direct remediation on confirmed weak concepts;
- `prerequisite`: 25%, prerequisite or method/model reinforcement;
- `transfer`: 15%, related/transfer verification.

Requirements:

- default total `8..12`, default `10`;
- deterministic rounding that always sums to requested count;
- hard concept gate before ranking;
- exclude confirmed current-exam originals by default;
- optional historical wrong-question inclusion;
- dedupe by exact question ID, fingerprint and text similarity;
- cap repeated source paper/method where alternatives exist;
- return `shortages` per stage/concept and never silently fill with unaligned questions.

- [ ] **Step 4: Add missing-difficulty and insufficient-pool tests**

```python
def test_missing_difficulty_is_allowed_with_warning(service):
    plan = service.generate_variant(diagnosis_profile, question_count=8)
    item = next(item for item in plan["items"] if item["question_id"] == 305)
    assert "候选题缺少难度标签" in item["warnings"]


def test_shortage_is_reported_instead_of_unaligned_fill(service):
    plan = service.generate_variant(diagnosis_profile, question_count=10)
    assert len(plan["items"]) == 7
    assert plan["shortages"][0]["missing_count"] == 3
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/recommendation/practice_plan_service.py question_bank/recommendation/recommendation_engine.py question_bank/recommendation/training_plan.py tests/test_practice_plan_service.py
git commit -m "feat: generate staged knowledge practice plans"
```

### Task 9: Support independent papers and automatic grouping

**Files:**
- Modify: `question_bank/recommendation/practice_plan_service.py`
- Create: `tests/test_practice_grouping.py`

- [ ] **Step 1: Write failing grouping tests**

```python
def test_individual_mode_creates_one_variant_per_student(service):
    plan = service.generate(profile_set, variant_mode="individual")
    assert len(plan["variants"]) == 3
    assert all(item["variant_type"] == "individual" for item in plan["variants"])


def test_auto_group_mode_groups_students_with_similar_confirmed_concepts(service):
    plan = service.generate(profile_set, variant_mode="auto_group")
    assert len(plan["variants"]) == 2
    assert sorted(len(item["student_ids"]) for item in plan["variants"]) == [1, 2]
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_practice_grouping.py -q`

Expected: FAIL because grouping is not implemented.

- [ ] **Step 3: Implement deterministic grouping**

Group using confirmed concept weakness vectors and score-rate bands. Record:

- grouping reason;
- covered concepts;
- member student IDs;
- group diagnosis snapshot;
- students that cannot be safely grouped.

Never group on unconfirmed source terms. Keep an explicit teacher override path in the returned plan contract.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_practice_grouping.py tests/test_practice_plan_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add question_bank/recommendation/practice_plan_service.py tests/test_practice_grouping.py
git commit -m "feat: add student and grouped practice variants"
```

### Task 10: Persist immutable training-task snapshots

**Files:**
- Create: `question_bank/services/training_task_service.py`
- Create: `tests/test_training_task_service.py`

- [ ] **Step 1: Write failing persistence tests**

```python
def test_save_task_persists_scope_diagnosis_variants_and_items(service):
    task = service.create_task(practice_plan, created_by="teacher")
    loaded = service.get_task(task.id)

    assert loaded["scope_snapshot"] == practice_plan["scope_snapshot"]
    assert loaded["diagnosis_snapshot"] == practice_plan["diagnosis_snapshot"]
    assert loaded["variants"][0]["items"][0]["question_id"] == 201
    assert loaded["variants"][0]["items"][0]["task_item_code"]


def test_task_item_snapshot_survives_later_question_edit(service, question_service):
    task = service.create_task(practice_plan, created_by="teacher")
    question_service.update_question(201, question_text="后来修改的题干")
    loaded = service.get_task(task.id)
    assert loaded["variants"][0]["items"][0]["question_snapshot"]["question_text"] == "原题干"
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_training_task_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement transactional task creation**

Persist in one transaction:

- task scope, exam scope, diagnosis, config and warnings snapshots;
- variants, grouping reasons and shortages;
- assigned students;
- ordered task items with stable `task_item_code`;
- bank question ID, fingerprint, concept/stage/recommendation snapshots.

Implement `list_tasks`, `get_task`, `cancel_task`, and `mark_export_state`. Do not mutate snapshots after task creation.

- [ ] **Step 4: Add reserved attempt-link contract test**

```python
def test_attempt_can_reference_stable_task_item_code(service):
    task = service.create_task(practice_plan, created_by="teacher")
    item_code = task.variants[0].items[0].task_item_code
    service.record_attempt_stub(
        task_item_code=item_code,
        student_id="12",
        grading_session_id=21,
        grading_question_id="8",
    )
    assert service.list_attempts(task.id)[0]["task_item_code"] == item_code
```

This method only saves future-compatible evidence. It must not calculate or update mastery in the first version.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_training_task_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/services/training_task_service.py tests/test_training_task_service.py
git commit -m "feat: persist traceable training tasks"
```

## Milestone 4: Teacher Workflow, Export, and Acceptance

### Task 11: Replace sample recommendation UI with the real teacher workflow

**Files:**
- Modify: `pages/训练推荐.py`
- Create: `tests/test_training_recommendation_ui.py`

- [ ] **Step 1: Write failing UI source-contract tests**

```python
def test_training_page_uses_real_diagnosis_and_task_services():
    source = Path("pages/训练推荐.py").read_text(encoding="utf-8")
    assert "DiagnosisProfileService" in source
    assert "PracticePlanService" in source
    assert "TrainingTaskService" in source
    assert "load_sample_mastery_rows" not in source


def test_training_page_supports_all_scope_modes():
    source = Path("pages/训练推荐.py").read_text(encoding="utf-8")
    for label in ("单个学生", "筛选多个学生", "全部班级"):
        assert label in source
    for label in ("当前考试", "跨考试", "手动选择考试"):
        assert label in source
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_training_recommendation_ui.py -q`

Expected: FAIL because the page still loads sample mastery data.

- [ ] **Step 3: Implement the scope and exam selectors**

Teacher flow:

1. choose current exam by default, or switch to cross-exam/manual sessions;
2. choose single student, score-filtered multiple students, or whole class;
3. inspect diagnosis coverage, confirmed weak concepts and unmapped terms;
4. open alignment center for unmapped terms;
5. choose individual or automatic-group variants;
6. review count, stage mix, weights and original-question exclusion;
7. generate preview, inspect shortages/warnings, adjust selected questions;
8. save the training task before export.

For multiple students, provide score range filters and explicit checkboxes after filtering. The selected IDs, not merely the filter expression, must be saved in the task snapshot.

- [ ] **Step 4: Add safety-state behavior**

Disable generation when:

- no student is selected;
- no exam/session is selected;
- all weak terms are unmapped;
- question-bank database is unavailable.

Allow generation with partial mappings, but show excluded unmapped terms and expected shortages before generation.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_training_recommendation_ui.py tests/test_diagnosis_profile_service.py tests/test_practice_grouping.py -q`

Expected: PASS.

- [ ] **Step 6: Manually verify main flows**

Run: `streamlit run app.py`

Expected:

- current exam + single student generates a preview;
- score-filtered multiple students preserve manual selection;
- whole class supports individual and grouped variants;
- manual multi-exam selection changes diagnosis evidence;
- unmapped terms are visible and do not silently generate questions.

- [ ] **Step 7: Commit**

```powershell
git add pages/训练推荐.py tests/test_training_recommendation_ui.py
git commit -m "feat: add real teacher practice generation workflow"
```

### Task 12: Coordinate exports and record retryable export state

**Files:**
- Create: `question_bank/services/training_export_service.py`
- Modify: `question_bank/exporters/docx_exporter.py`
- Modify: `question_bank/exporters/markdown_exporter.py`
- Modify: `question_bank/exporters/paper_docx_exporter.py`
- Modify: `tests/test_question_bank_exporter.py`
- Create: `tests/test_training_export_service.py`

- [ ] **Step 1: Inspect and preserve current exporter changes**

Run:

```powershell
git status --short question_bank/exporters tests/test_question_bank_exporter.py
git diff -- question_bank/exporters tests/test_question_bank_exporter.py
```

Expected: understand and retain all pre-existing exporter work before editing.

- [ ] **Step 2: Write failing export-record tests**

```python
def test_export_variant_creates_student_and_teacher_records(service, saved_task):
    bundle = service.export_variant(saved_task.id, saved_task.variants[0].id, formats=["docx"])
    assert {item["audience"] for item in bundle["exports"]} == {"student", "teacher"}
    assert all(item["status"] == "succeeded" for item in bundle["exports"])


def test_failed_export_can_retry_without_regenerating_task(service, saved_task, failing_exporter):
    failed = service.export_variant(saved_task.id, saved_task.variants[0].id, formats=["docx"])
    retried = service.retry_export(failed["exports"][0]["id"])
    assert retried["retry_count"] == 1
    assert retried["task_id"] == saved_task.id
```

- [ ] **Step 3: Run and confirm failure**

Run: `python -m pytest tests/test_training_export_service.py -q`

Expected: FAIL because the orchestration service does not exist.

- [ ] **Step 4: Implement export orchestration**

The service must:

- load immutable task/variant snapshots;
- export student and teacher versions;
- optionally build a ZIP across variants;
- include task code, variant code and stable item codes in teacher-facing metadata;
- record each export attempt, output path, status, error and retry count;
- retry failed exports from saved snapshots without regenerating recommendations;
- leave successful files and records intact if another format/audience fails.

- [ ] **Step 5: Run exporter tests**

Run: `python -m pytest tests/test_training_export_service.py tests/test_question_bank_exporter.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add question_bank/services/training_export_service.py question_bank/exporters/docx_exporter.py question_bank/exporters/markdown_exporter.py question_bank/exporters/paper_docx_exporter.py tests/test_training_export_service.py tests/test_question_bank_exporter.py
git commit -m "feat: export and track training task bundles"
```

### Task 13: Add task history, status recovery, and explicit first-version boundaries

**Files:**
- Modify: `pages/训练推荐.py`
- Create: `tests/test_training_task_history_ui.py`
- Create: `docs/knowledge-practice-operations.md`

- [ ] **Step 1: Write failing history UI contract test**

```python
def test_training_page_exposes_task_history_and_failed_export_retry():
    source = Path("pages/训练推荐.py").read_text(encoding="utf-8")
    assert "历史训练任务" in source
    assert "重试失败导出" in source
    assert "训练结果回流尚未启用" in source
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_training_task_history_ui.py -q`

Expected: FAIL because history/retry UI is absent.

- [ ] **Step 3: Implement history and recovery UI**

Show:

- task code, creation time, scope, exam scope and status;
- variant/student assignments;
- question count, shortage and warning summaries;
- export records and failure details;
- retry action for failed exports;
- clear first-version message that attempt capture/mastery feedback is reserved but not active.

- [ ] **Step 4: Write operations documentation**

Document:

- how concept mappings become eligible;
- why unmapped terms and source-link gaps block or warn;
- how current-exam original exclusion works;
- how to retry exports;
- what is and is not implemented for future training feedback.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_training_task_history_ui.py tests/test_training_recommendation_ui.py tests/test_training_export_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add pages/训练推荐.py tests/test_training_task_history_ui.py docs/knowledge-practice-operations.md
git commit -m "feat: add training task history and recovery"
```

### Task 14: End-to-end acceptance and regression verification

**Files:**
- Create: `tests/test_knowledge_practice_end_to_end.py`
- Modify only if failures reveal an implementation defect in files already covered above.

- [ ] **Step 1: Write the end-to-end happy-path test**

The test fixture must create:

- two grading sessions;
- at least three students with full-score-weighted weak-point evidence;
- confirmed and unmapped diagnosis terms;
- confirmed question-tag mappings and concept relations;
- local/深圳 frequency evidence;
- one confirmed current-exam original link;
- duplicate/near-duplicate candidate questions;
- candidates with and without difficulty labels.

Then assert:

```python
def test_teacher_can_generate_save_and_export_grouped_practice(system):
    diagnosis = system.diagnose_selected_students(["12", "15"], sessions=[12, 14])
    plan = system.generate_grouped_plan(diagnosis, question_count=10)
    task = system.save_task(plan)
    exports = system.export_task(task.id)

    assert diagnosis["unmapped_terms"]
    assert all(item["mapping_status"] == "confirmed" for item in plan["eligible_weak_points"])
    assert 201 not in {item["question_id"] for variant in plan["variants"] for item in variant["items"]}
    assert task["diagnosis_snapshot"] == diagnosis
    assert {item["audience"] for item in exports} == {"student", "teacher"}
```

- [ ] **Step 2: Run focused end-to-end tests**

Run: `python -m pytest tests/test_knowledge_practice_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 3: Run the complete focused feature suite**

Run:

```powershell
python -m pytest tests/test_knowledge_practice_schema.py tests/test_knowledge_alignment_models.py tests/test_concept_alignment_service.py tests/test_knowledge_alignment_ui.py tests/test_source_question_link_service.py tests/test_diagnosis_profile_service.py tests/test_practice_candidate_scoring.py tests/test_practice_plan_service.py tests/test_practice_grouping.py tests/test_training_task_service.py tests/test_training_recommendation_ui.py tests/test_training_export_service.py tests/test_training_task_history_ui.py tests/test_knowledge_practice_end_to_end.py -q
```

Expected: PASS.

- [ ] **Step 4: Run related regression tests**

Run:

```powershell
python -m pytest tests/test_question_bank_service.py tests/test_question_frequency_service.py tests/test_recommendation_frequency_scoring.py tests/test_question_bank_exporter.py tests/test_question_bank_ai_tagging_quality.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`

Expected: PASS. If unrelated pre-existing failures occur, record the exact failing tests and verify that focused feature/regression suites still pass.

- [ ] **Step 6: Perform browser acceptance**

Run: `streamlit run app.py`

Verify:

- normal single-student flow completes in under five minutes;
- current exam is selected by default and manual multi-exam selection works;
- multiple-student score filtering still requires explicit teacher selection;
- whole-class mode supports independent and grouped variants;
- confirmed mapping recommendation accuracy is manually sampled and recorded;
- unmapped/low-confidence/shortage states are explicit;
- current-exam original exclusion warns when source links are incomplete;
- saved task snapshots remain stable after a question-bank edit;
- student paper, teacher paper and ZIP export succeed;
- one intentionally failed export can be retried.

- [ ] **Step 7: Check plan/spec compliance and repository hygiene**

Run:

```powershell
git diff --check
rg -n "load_sample_mastery_rows|knowledge_mapping\.json" pages/训练推荐.py pages/知识图谱适配调试.py
git status --short --branch
```

Expected:

- no whitespace errors;
- no sample mastery or JSON mapping dependency in the production pages;
- no accidental database, output, `.superpowers/`, or unrelated workspace files staged.

- [ ] **Step 8: Commit**

```powershell
git add tests/test_knowledge_practice_end_to_end.py
git commit -m "test: verify knowledge practice workflow end to end"
```

## Final Definition of Done

- Teachers can select one student, manually selected students after score filtering, or the entire class.
- Diagnosis defaults to the current exam and can switch to cross-exam or manually selected exams.
- Only confirmed standard-concept mappings enter recommendation; suggested/unmapped terms remain visible.
- Recommendations use concept hard-gating and configurable `40/35/10/15` scoring, with frequency as a core signal.
- A teacher-reviewed sample of recommendations based on confirmed mappings reaches at least 80% acceptance.
- Generic AI error reasons do not influence recommendation.
- Candidate difficulty organizes the practice gradient without inferring student ability from the original wrong question.
- Generated work supports individual and automatic-group variants with `60/25/15` stage defaults.
- Current-exam originals and near-duplicates are excluded where reliably identifiable; unresolved source links produce warnings.
- Shortages and missing metadata are explicit and never silently replaced with unaligned questions.
- Every saved task contains immutable scope, diagnosis, config, variant, item, source-link and recommendation snapshots.
- Student paper, teacher paper, ZIP export, export status and retry work from the saved task.
- `training_attempts` and stable task-item links exist for future result feedback, but first-version UI does not claim automatic mastery updates.
- Focused, related regression and full test suites pass, or unrelated pre-existing failures are precisely documented.
