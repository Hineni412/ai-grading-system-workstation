# Simplified Knowledge Alignment and Training Recommendation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复阅卷薄弱点与知识点映射的身份失联，并把教师流程简化为“选择对象、处理少量例外、生成练习”，同时用细技能匹配阻止粗知识点静默选错题。

**Architecture:** 新增一个共享术语身份模块，确保诊断、映射、焦点过滤和旧数据迁移使用同一 `source_value`。新增纯函数式焦点整理与细技能匹配模块，把可测试的业务逻辑从两个 Streamlit 大页面中抽离；页面只负责呈现和调用。保留现有映射表、训练任务快照和服务接口，通过幂等兼容迁移而非删除旧数据。

**Tech Stack:** Python 3.11、Streamlit、SQLite、pytest、现有 `DBManager` 与 `question_bank` 服务层。

## Global Constraints

- 不修改阅卷、题库导入、组卷和其他无关业务。
- 不删除旧映射、题库数据或历史训练任务快照。
- 名称完全一致、登记别名和无冲突历史确认允许自动通过；纯 AI 语义判断必须由教师确认。
- 仅标准知识点大类一致的题目默认不得自动入选；精确题不足时保留缺题。
- 普通页面只保留题量、训练版本和原题排除三个核心设置。
- “历史错题回流”在真正参与选题前不得出现在教师页面。
- 所有数据库迁移必须幂等，冲突时保留教师历史决定并给出可诊断结果。
- 每个任务只暂存列出的文件，不得带入工作区中的其他修改。

---

## File Map

**Create**

- `integration/knowledge_term_identity.py`：阅卷知识点显示值与稳定映射值的唯一标准化实现。
- `question_bank/services/alignment_review_service.py`：结构化焦点项、焦点合并、当前范围排除和确认后重建诊断。
- `question_bank/recommendation/fine_skill_matching.py`：细技能归一化与匹配评分。
- `tests/test_knowledge_term_identity.py`：术语标准化和编号前缀回归测试。
- `tests/test_alignment_review_service.py`：17 项焦点、命名空间隔离、缺失来源补入和即时确认测试。
- `tests/test_fine_skill_matching.py`：大类相同但技能不同不可入选等测试。

**Modify**

- `integration/diagnosis_profile_service.py`：使用共享术语身份，输出显示值，并采用安全自动确认入口。
- `question_bank/services/concept_alignment_service.py`：旧映射兼容迁移、安全自动确认、映射修订令牌。
- `question_bank/recommendation/scoring.py`：把细技能匹配加入候选准入结果。
- `question_bank/recommendation/practice_plan_service.py`：计算细技能匹配并使用固定可解释排序。
- `question_bank/recommendation/training_plan.py`：默认阶段比例改为 60% 针对、30% 基础、10% 提升。
- `pages/训练推荐.py`：内嵌例外确认、即时刷新、简化核心设置。
- `pages/知识图谱适配调试.py`：改为高级知识点整理页，修复结构化焦点和默认展示。
- `tests/test_diagnosis_profile_service.py`
- `tests/test_concept_alignment_service.py`
- `tests/test_practice_candidate_scoring.py`
- `tests/test_practice_plan_service.py`
- `tests/test_training_recommendation_ui.py`
- `tests/test_knowledge_alignment_ui.py`
- `tests/test_knowledge_practice_end_to_end.py`
- `docs/knowledge-practice-operations.md`

---

### Task 1: Establish one grading knowledge-term identity

**Files:**

- Create: `integration/knowledge_term_identity.py`
- Create: `tests/test_knowledge_term_identity.py`
- Modify: `integration/diagnosis_profile_service.py:52-90,276-283`
- Test: `tests/test_diagnosis_profile_service.py`

**Interfaces:**

- Produces: `GradingKnowledgeTerm(knowledge_id, display_value, source_value)`.
- Produces: `build_grading_knowledge_term(knowledge_id: object, knowledge_label: object) -> GradingKnowledgeTerm`.
- Later tasks must use `source_value` as the persisted mapping identity and `display_value` only for teacher-visible provenance.

- [ ] **Step 1: Write the failing identity tests**

Create `tests/test_knowledge_term_identity.py`:

```python
from integration.knowledge_term_identity import build_grading_knowledge_term


def test_numbered_label_keeps_display_and_strips_mapping_prefix() -> None:
    term = build_grading_knowledge_term("G7_15", "G7_15 · 角平分线性质")

    assert term.knowledge_id == "G7_15"
    assert term.display_value == "G7_15 · 角平分线性质"
    assert term.source_value == "角平分线性质"


def test_plain_label_and_missing_label_have_stable_fallbacks() -> None:
    plain = build_grading_knowledge_term("K1", "二次函数")
    missing = build_grading_knowledge_term("K2", "")

    assert plain.source_value == "二次函数"
    assert plain.display_value == "二次函数"
    assert missing.source_value == "K2"
    assert missing.display_value == "K2"


def test_supported_separators_are_removed_only_after_exact_id_prefix() -> None:
    assert build_grading_knowledge_term("G7_01", "G7_01：尺规作图").source_value == "尺规作图"
    assert build_grading_knowledge_term("G7_01", "G7_01 - 尺规作图").source_value == "尺规作图"
    assert build_grading_knowledge_term("G7_01", "其他 G7_01 尺规作图").source_value == "其他 G7_01 尺规作图"
```

- [ ] **Step 2: Run the new tests and verify the missing module failure**

Run:

```powershell
python -m pytest -q tests/test_knowledge_term_identity.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'integration.knowledge_term_identity'`.

- [ ] **Step 3: Implement the shared identity module**

Create `integration/knowledge_term_identity.py`:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE = re.compile(r"\s+")
_LEADING_SEPARATORS = re.compile(r"^[\s·路:：|\-]+")


@dataclass(frozen=True, slots=True)
class GradingKnowledgeTerm:
    knowledge_id: str
    display_value: str
    source_value: str


def _clean(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", normalized).strip()


def build_grading_knowledge_term(
    knowledge_id: object,
    knowledge_label: object,
) -> GradingKnowledgeTerm:
    normalized_id = _clean(knowledge_id)
    display_value = _clean(knowledge_label) or normalized_id or "UNKNOWN"
    source_value = display_value
    if normalized_id and source_value.startswith(normalized_id):
        remainder = source_value[len(normalized_id) :]
        source_value = _LEADING_SEPARATORS.sub("", remainder).strip() or normalized_id
    return GradingKnowledgeTerm(
        knowledge_id=normalized_id,
        display_value=display_value,
        source_value=source_value or normalized_id or "UNKNOWN",
    )


__all__ = ["GradingKnowledgeTerm", "build_grading_knowledge_term"]
```

- [ ] **Step 4: Use the shared identity in diagnosis output**

In `integration/diagnosis_profile_service.py`, import `build_grading_knowledge_term`. Replace the current `_source_term()` use in `build_profiles()` with:

```python
term = build_grading_knowledge_term(
    row.get("knowledge_id"),
    row.get("knowledge_label"),
)
source_term = term.source_value
```

Add this field to each weak-point payload:

```python
"source_display": term.display_value,
```

Replace `_source_term()` with a compatibility wrapper so external imports do not break:

```python
def _source_term(row: Mapping[str, Any]) -> str:
    return build_grading_knowledge_term(
        row.get("knowledge_id"),
        row.get("knowledge_label"),
    ).source_value
```

Add to `tests/test_diagnosis_profile_service.py`:

```python
def test_numbered_rubric_label_exposes_plain_mapping_term(service: DiagnosisProfileService) -> None:
    profile = service.build_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(item for item in profile["students"][0]["weak_points"] if item["source_term"] == "二次函数")
    assert weak["source_display"].endswith("二次函数")
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest -q tests/test_knowledge_term_identity.py tests/test_diagnosis_profile_service.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit the identity boundary**

```powershell
git add -- integration/knowledge_term_identity.py integration/diagnosis_profile_service.py tests/test_knowledge_term_identity.py tests/test_diagnosis_profile_service.py
git commit -m "fix: unify grading knowledge term identity"
```

---

### Task 2: Preserve legacy confirmations and auto-confirm only safe matches

**Files:**

- Modify: `question_bank/services/concept_alignment_service.py:24-38,579-714`
- Modify: `integration/diagnosis_profile_service.py:29-43`
- Test: `tests/test_concept_alignment_service.py`
- Test: `tests/test_diagnosis_profile_service.py`

**Interfaces:**

- Consumes: `build_grading_knowledge_term()` from Task 1.
- Produces: `LegacyAlignmentMigrationReport(copied, reused, conflicts)`.
- Produces: `ConceptAlignmentService.migrate_legacy_grading_mappings() -> LegacyAlignmentMigrationReport`.
- Produces: `ConceptAlignmentService.resolve_for_training(namespace, source_value) -> AlignmentResolution`.
- Produces: `ConceptAlignmentService.revision_token() -> str`.

- [ ] **Step 1: Add failing migration and safe-auto-confirm tests**

Append to `tests/test_concept_alignment_service.py`:

```python
def test_legacy_numbered_confirmation_is_copied_to_plain_term(
    alignment_service: ConceptAlignmentService,
) -> None:
    concept = alignment_service.create_concept("math.angle_bisector", "角平分线")
    alignment_service.confirm_mapping(
        "grading_weak_point",
        "G7_15 · 角平分线性质",
        concept.id,
        reviewed_by="teacher",
    )

    first = alignment_service.migrate_legacy_grading_mappings()
    second = alignment_service.migrate_legacy_grading_mappings()
    resolved = alignment_service.resolve("grading_weak_point", "角平分线性质")

    assert first.copied == 1
    assert second.copied == 0
    assert resolved.status.value == "confirmed"
    assert resolved.concept is not None and resolved.concept.id == concept.id


def test_legacy_conflict_is_reported_without_overwriting_teacher_choice(
    alignment_service: ConceptAlignmentService,
) -> None:
    old_concept = alignment_service.create_concept("math.old", "旧分类")
    current_concept = alignment_service.create_concept("math.current", "当前分类")
    alignment_service.confirm_mapping("grading_weak_point", "G7_15 · 角平分线性质", old_concept.id)
    alignment_service.confirm_mapping("grading_weak_point", "角平分线性质", current_concept.id)

    report = alignment_service.migrate_legacy_grading_mappings()
    resolved = alignment_service.resolve("grading_weak_point", "角平分线性质")

    assert report.conflicts == ("角平分线性质",)
    assert resolved.concept is not None and resolved.concept.id == current_concept.id


def test_training_resolution_auto_confirms_exact_alias_but_not_fuzzy_semantics(
    alignment_service: ConceptAlignmentService,
) -> None:
    concept = alignment_service.create_concept("math.quadratic", "二次函数", aliases=["抛物线"])
    exact = alignment_service.resolve_for_training("grading_weak_point", "抛物线")
    fuzzy = alignment_service.resolve_for_training("grading_weak_point", "二次函数图像应用")

    assert exact.status.value == "confirmed"
    assert exact.concept is not None and exact.concept.id == concept.id
    assert fuzzy.status.value == "suggested"
    assert fuzzy.eligible_for_recommendation is False
```

- [ ] **Step 2: Run the tests and verify missing interfaces**

Run:

```powershell
python -m pytest -q tests/test_concept_alignment_service.py -k "legacy or training_resolution"
```

Expected: failures report missing `migrate_legacy_grading_mappings` and `resolve_for_training`.

- [ ] **Step 3: Add the migration result model and safe exact matcher**

In `question_bank/services/concept_alignment_service.py`, add:

```python
@dataclass(frozen=True)
class LegacyAlignmentMigrationReport:
    copied: int = 0
    reused: int = 0
    conflicts: tuple[str, ...] = ()
```

Add a private helper that accepts exact names, registered aliases and canonical registry aliases but rejects substring/fuzzy matches:

```python
def _safe_automatic_concept(
    conn: sqlite3.Connection,
    source_value: str,
) -> KnowledgeConcept | None:
    normalized = normalize_source_value(source_value)
    if not normalized:
        return None
    concepts = [
        _concept_from_row(row)
        for row in conn.execute(
            "SELECT * FROM knowledge_concepts WHERE status = 'active' ORDER BY id"
        ).fetchall()
    ]
    registry_match = canonicalize_knowledge(source_value)
    for concept in concepts:
        exact_values = (concept.canonical_key, concept.name, *concept.aliases)
        if normalized in {normalize_source_value(value) for value in exact_values}:
            return concept
        if registry_match is not None and normalize_source_value(registry_match.canonical_id) == concept.canonical_key:
            return concept
    return None
```

- [ ] **Step 4: Implement idempotent legacy mapping migration**

Add `migrate_legacy_grading_mappings()` to `ConceptAlignmentService`. It must:

```python
def migrate_legacy_grading_mappings(self) -> LegacyAlignmentMigrationReport:
    self.initialize_database()
    copied = 0
    reused = 0
    conflicts: list[str] = []
    with connect(self.db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge_source_mappings
            WHERE source_namespace = 'grading_weak_point'
              AND source_value LIKE '%·%'
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            legacy_value = str(row["source_value"])
            legacy_id, _, legacy_label = legacy_value.partition("·")
            source_value = build_grading_knowledge_term(legacy_id, legacy_value).source_value
            target = conn.execute(
                """
                SELECT * FROM knowledge_source_mappings
                WHERE source_namespace = 'grading_weak_point'
                  AND normalized_value = ?
                ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'rejected' THEN 1 ELSE 2 END, id
                LIMIT 1
                """,
                (normalize_source_value(source_value),),
            ).fetchone()
            if target is not None:
                same_decision = (
                    str(target["status"]) == str(row["status"])
                    and target["concept_id"] == row["concept_id"]
                )
                if same_decision:
                    reused += 1
                elif str(target["status"]) == "confirmed" and str(row["status"]) == "confirmed":
                    conflicts.append(source_value)
                continue
            _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace="grading_weak_point",
                    source_value=source_value,
                    concept_id=row["concept_id"],
                    status=AlignmentStatus(str(row["status"])),
                    confidence=float(row["confidence"] or 0.0),
                    sub_skill_tags=tuple(_json_list(row["sub_skill_tags"])),
                ),
                reviewed_by=row["reviewed_by"],
                evidence={"legacy_source_value": legacy_value},
            )
            copied += 1
    return LegacyAlignmentMigrationReport(
        copied=copied,
        reused=reused,
        conflicts=tuple(sorted(set(conflicts))),
    )
```

Import `build_grading_knowledge_term` at the top. Keep every original row unchanged.

- [ ] **Step 5: Implement explicit training resolution and revision token**

Add:

```python
def resolve_for_training(self, source_namespace: str, source_value: str) -> AlignmentResolution:
    resolved = self.resolve(source_namespace, source_value)
    if resolved.status in {AlignmentStatus.CONFIRMED, AlignmentStatus.REJECTED}:
        return resolved
    with connect(self.db_path) as conn:
        concept = _safe_automatic_concept(conn, source_value)
    if concept is None:
        return resolved
    self.confirm_mapping(
        source_namespace,
        source_value,
        concept.id,
        reviewed_by="system:exact-or-alias",
        evidence={"automatic_rule": "exact-or-registered-alias"},
    )
    return self.resolve(source_namespace, source_value)


def revision_token(self) -> str:
    self.initialize_database()
    with connect(self.db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS item_count,
                   COALESCE(MAX(updated_at), '') AS latest_update
            FROM knowledge_source_mappings
            """
        ).fetchone()
    return f"{int(row['item_count'])}:{row['latest_update']}"
```

At the start of `DiagnosisProfileService.build_profiles()`, run the migration once, append a warning for conflicts, and replace `self.alignment.resolve(...)` with `self.alignment.resolve_for_training(...)`.

- [ ] **Step 6: Run alignment and diagnosis tests**

Run:

```powershell
python -m pytest -q tests/test_concept_alignment_service.py tests/test_diagnosis_profile_service.py
```

Expected: all tests pass; the existing `resolve()` suggestion test remains unchanged because only `resolve_for_training()` auto-confirms.

- [ ] **Step 7: Commit compatibility and safe automatic matching**

```powershell
git add -- question_bank/services/concept_alignment_service.py integration/diagnosis_profile_service.py tests/test_concept_alignment_service.py tests/test_diagnosis_profile_service.py
git commit -m "fix: preserve legacy knowledge confirmations"
```

---

### Task 3: Replace string-only focus mode with structured exception items

**Files:**

- Create: `question_bank/services/alignment_review_service.py`
- Create: `tests/test_alignment_review_service.py`
- Modify: `pages/知识图谱适配调试.py:20-21,57-104,317-347`
- Modify: `pages/训练推荐.py:36,154-170`
- Test: `tests/test_knowledge_alignment_ui.py`
- Test: `tests/test_training_recommendation_ui.py`

**Interfaces:**

- Consumes: `build_grading_knowledge_term()` and diagnosis weak-point dictionaries.
- Produces: `AlignmentFocusItem` with namespace, source value, display value, evidence count, student IDs and session IDs.
- Produces: `focus_items_from_diagnosis(diagnosis)`, `merge_focus_sources(source_terms, focus_items)`, and `filter_focus_sources(source_terms, focus_items)`.

- [ ] **Step 1: Write the failing structured-focus tests**

Create `tests/test_alignment_review_service.py`:

```python
from question_bank.services.alignment_review_service import (
    AlignmentFocusItem,
    filter_focus_sources,
    focus_items_from_diagnosis,
    merge_focus_sources,
)


def _diagnosis() -> dict:
    return {
        "exam_scope": {"session_ids": [1]},
        "students": [
            {
                "student_id": "70",
                "weak_points": [
                    {
                        "source_term": "角平分线性质",
                        "source_display": "G7_15 · 角平分线性质",
                        "mapping_status": "suggested",
                        "evidence_count": 3,
                    }
                ],
            }
        ],
    }


def test_focus_item_preserves_namespace_and_provenance() -> None:
    items = focus_items_from_diagnosis(_diagnosis())

    assert items == [
        AlignmentFocusItem(
            source_namespace="grading_weak_point",
            source_value="角平分线性质",
            display_value="G7_15 · 角平分线性质",
            evidence_count=3,
            student_ids=("70",),
            session_ids=(1,),
        )
    ]


def test_missing_focus_source_is_injected_and_same_name_question_tag_is_not_used() -> None:
    focus = focus_items_from_diagnosis(_diagnosis())
    sources = [
        {
            "source_namespace": "question_tag",
            "source_value": "角平分线性质",
            "display_value": "角平分线性质",
            "evidence_count": 4,
        }
    ]

    merged = merge_focus_sources(sources, focus)
    filtered = filter_focus_sources(merged, focus)

    assert len(filtered) == 1
    assert filtered[0]["source_namespace"] == "grading_weak_point"
    assert filtered[0]["source_value"] == "角平分线性质"
    assert filtered[0]["evidence_count"] == 3
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

```powershell
python -m pytest -q tests/test_alignment_review_service.py
```

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement structured focus helpers**

Create `question_bank/services/alignment_review_service.py` with:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from question_bank.models.knowledge_alignment import normalize_source_value


@dataclass(frozen=True, slots=True)
class AlignmentFocusItem:
    source_namespace: str
    source_value: str
    display_value: str
    evidence_count: int
    student_ids: tuple[str, ...]
    session_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def focus_items_from_diagnosis(diagnosis: Mapping[str, Any]) -> list[AlignmentFocusItem]:
    session_ids = tuple(sorted({int(value) for value in diagnosis.get("exam_scope", {}).get("session_ids", [])}))
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for student in diagnosis.get("students", []):
        student_id = str(student.get("student_id") or "")
        for weak in student.get("weak_points", []):
            if str(weak.get("mapping_status") or "") in {"confirmed", "rejected"}:
                continue
            source_value = str(weak.get("source_term") or "").strip()
            if not source_value:
                continue
            key = ("grading_weak_point", normalize_source_value(source_value))
            row = grouped.setdefault(
                key,
                {
                    "source_value": source_value,
                    "display_value": str(weak.get("source_display") or source_value),
                    "evidence_count": 0,
                    "student_ids": set(),
                },
            )
            row["evidence_count"] += int(weak.get("evidence_count") or 0)
            if student_id:
                row["student_ids"].add(student_id)
    return [
        AlignmentFocusItem(
            source_namespace=namespace,
            source_value=row["source_value"],
            display_value=row["display_value"],
            evidence_count=row["evidence_count"],
            student_ids=tuple(sorted(row["student_ids"])),
            session_ids=session_ids,
        )
        for (namespace, _), row in sorted(grouped.items())
    ]


def _focus_key(namespace: object, value: object) -> tuple[str, str]:
    return normalize_source_value(namespace), normalize_source_value(value)


def merge_focus_sources(
    source_terms: Iterable[Mapping[str, Any]],
    focus_items: Iterable[AlignmentFocusItem],
) -> list[dict[str, Any]]:
    result = [dict(item) for item in source_terms]
    existing = {_focus_key(item.get("source_namespace"), item.get("source_value")) for item in result}
    for item in focus_items:
        key = _focus_key(item.source_namespace, item.source_value)
        if key not in existing:
            result.append(
                {
                    "source_namespace": item.source_namespace,
                    "source_value": item.source_value,
                    "display_value": item.display_value,
                    "evidence_count": item.evidence_count,
                }
            )
            existing.add(key)
    return result


def filter_focus_sources(
    source_terms: Iterable[Mapping[str, Any]],
    focus_items: Iterable[AlignmentFocusItem],
) -> list[dict[str, Any]]:
    allowed = {_focus_key(item.source_namespace, item.source_value) for item in focus_items}
    return [
        dict(item)
        for item in source_terms
        if _focus_key(item.get("source_namespace"), item.get("source_value")) in allowed
    ]


__all__ = [
    "AlignmentFocusItem",
    "filter_focus_sources",
    "focus_items_from_diagnosis",
    "merge_focus_sources",
]
```

- [ ] **Step 4: Make the advanced page load normalized grading terms**

In `pages/知识图谱适配调试.py`, change `_load_real_source_terms()` so each grading row uses `build_grading_knowledge_term()` and adds both values:

```python
term = build_grading_knowledge_term(
    item.get("knowledge_id"),
    item.get("knowledge_label"),
)
_add_source_term(
    terms,
    source_namespace="grading_weak_point",
    source_value=term.source_value,
    display_value=term.display_value,
    evidence_count=int(item.get("item_count") or 1),
)
```

Extend `_add_source_term()` with `display_value: object | None = None` and persist it in each row. Use `source_value` for resolving and `display_value` for the read-only “原始诊断” column.

- [ ] **Step 5: Pass structured focus data from training and consume it safely**

In `pages/训练推荐.py`, replace the string-only assignment with:

```python
focus_items = focus_items_from_diagnosis(diagnosis)
st.session_state[ALIGNMENT_FOCUS_SESSION_KEY] = [item.to_dict() for item in focus_items]
```

In `pages/知识图谱适配调试.py`, coerce stored dictionaries to `AlignmentFocusItem`, call `merge_focus_sources()` and `filter_focus_sources()`, and remove the fallback that silently shows all source terms. If the focus list is non-empty, the resulting list must also be non-empty because missing items are injected.

Update `tests/test_training_recommendation_ui.py` and `tests/test_knowledge_alignment_ui.py` to assert the use of `focus_items_from_diagnosis`, `merge_focus_sources`, `filter_focus_sources`, and absence of the legacy bare-string filter expression.

- [ ] **Step 6: Run focus and page contract tests**

```powershell
python -m pytest -q tests/test_alignment_review_service.py tests/test_knowledge_alignment_ui.py tests/test_training_recommendation_ui.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit structured focus mode**

```powershell
git add -- question_bank/services/alignment_review_service.py pages/训练推荐.py pages/知识图谱适配调试.py tests/test_alignment_review_service.py tests/test_knowledge_alignment_ui.py tests/test_training_recommendation_ui.py
git commit -m "fix: make alignment focus items actionable"
```

---

### Task 4: Confirm exceptions inline and refresh diagnosis immediately

**Files:**

- Modify: `question_bank/services/alignment_review_service.py`
- Modify: `pages/训练推荐.py:79-170,733-787`
- Modify: `tests/test_alignment_review_service.py`
- Modify: `tests/test_training_recommendation_ui.py`

**Interfaces:**

- Consumes: structured focus items from Task 3.
- Produces: `AlignmentReviewService.confirm_and_rebuild(...) -> dict[str, Any]`.
- Produces: `apply_scope_exclusions(diagnosis, excluded_terms) -> dict[str, Any]`.
- The page stores current-scope exclusions under a selection-signature key; it does not persist them as global rejected mappings.

- [ ] **Step 1: Add failing tests for immediate confirmation and current-scope skipping**

Extend the imports in `tests/test_alignment_review_service.py`:

```python
import json
import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager
from question_bank.services.alignment_review_service import (
    AlignmentReviewService,
    apply_scope_exclusions,
)
```

Add this complete temporary grading/question-bank fixture:

```python
@pytest.fixture
def review_system(tmp_path: Path):
    grading_db = tmp_path / "grading_system.db"
    question_bank_db = tmp_path / "question_bank.db"
    DBManager(grading_db).initialize()
    rubric_path = tmp_path / "review_rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 5,
                        "knowledge_id": "K_UNKNOWN",
                        "knowledge_name": "陌生诊断词",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(grading_db) as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (12, 'S12', '张三', '九年级1班')"
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '当前考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'completed')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score,
                student_score, needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 5, 0, 0, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_id, knowledge_ids
            ) VALUES (14001, 'Q1', 0, '需要巩固', 'K_UNKNOWN', '["K_UNKNOWN"]')
            """
        )
    review = AlignmentReviewService(grading_db, question_bank_db)
    concept = review.alignment.create_concept("math.unknown", "待确认知识点")
    scope = {"mode": "student", "student_ids": ["12"]}
    exam_scope = {"mode": "current", "session_ids": [14]}
    return review, concept.id, scope, exam_scope
```

Add the two behavior tests:

```python
def test_confirm_and_rebuild_returns_confirmed_current_diagnosis(review_system) -> None:
    review, concept_id, scope, exam_scope = review_system

    refreshed = review.confirm_and_rebuild(
        scope=scope,
        exam_scope=exam_scope,
        source_value="陌生诊断词",
        concept_id=concept_id,
    )

    weak = refreshed["students"][0]["weak_points"][0]
    assert weak["mapping_status"] == "confirmed"
    assert weak["eligible_for_recommendation"] is True


def test_scope_exclusion_does_not_create_global_rejection() -> None:
    diagnosis = _diagnosis()
    updated = apply_scope_exclusions(diagnosis, {"角平分线性质"})

    weak = updated["students"][0]["weak_points"][0]
    assert weak["eligible_for_recommendation"] is False
    assert weak["review_state"] == "本次不推荐"
```

- [ ] **Step 2: Run the tests and verify missing review service behavior**

```powershell
python -m pytest -q tests/test_alignment_review_service.py -k "confirm_and_rebuild or scope_exclusion"
```

Expected: tests fail because the new class and function do not exist.

- [ ] **Step 3: Implement the review orchestration**

Add to `question_bank/services/alignment_review_service.py`:

```python
from copy import deepcopy
from pathlib import Path

from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.services.concept_alignment_service import ConceptAlignmentService


class AlignmentReviewService:
    def __init__(self, grading_db_path: str | Path, question_bank_db_path: str | Path) -> None:
        self.diagnosis = DiagnosisProfileService(grading_db_path, question_bank_db_path)
        self.alignment = ConceptAlignmentService(question_bank_db_path)

    def confirm_and_rebuild(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
        source_value: str,
        concept_id: int,
    ) -> dict[str, Any]:
        self.alignment.confirm_mapping(
            "grading_weak_point",
            source_value,
            concept_id,
            sub_skill_tags=[source_value],
            reviewed_by="teacher",
        )
        return self.diagnosis.build_profiles(scope=scope, exam_scope=exam_scope)


def apply_scope_exclusions(
    diagnosis: Mapping[str, Any],
    excluded_terms: set[str],
) -> dict[str, Any]:
    result = deepcopy(dict(diagnosis))
    for student in result.get("students", []):
        for weak in student.get("weak_points", []):
            if str(weak.get("source_term") or "") in excluded_terms:
                weak["eligible_for_recommendation"] = False
                weak["review_state"] = "本次不推荐"
    result["confirmed_concept_ids"] = sorted(
        {
            int(weak["concept_id"])
            for student in result.get("students", [])
            for weak in student.get("weak_points", [])
            if weak.get("eligible_for_recommendation") and weak.get("concept_id") is not None
        }
    )
    return result
```

- [ ] **Step 4: Replace the jump-first workflow with inline exception cards**

In `pages/训练推荐.py`, prepare concept choices and current-scope exclusions without displaying internal IDs:

```python
alignment_service = ConceptAlignmentService(pm.qb_db_path)
review_service = AlignmentReviewService(pm.db_path, pm.qb_db_path)
concepts = alignment_service.list_concepts()
concept_by_id = {concept.id: concept for concept in concepts}
concept_ids = list(concept_by_id)
scope_exclusion_key = f"training_scope_exclusions:{selection_signature}"
exclusions = set(st.session_state.get(scope_exclusion_key) or [])
focus_items = focus_items_from_diagnosis(diagnosis)
confirmed_count = len(diagnosis.get("confirmed_concept_ids") or [])
```

Render each focus item with teacher language only:

```python
st.info(f"已自动整理 {confirmed_count} 个知识点，还有 {len(focus_items)} 个需要您确认。")
for item in focus_items:
    resolution = alignment_service.resolve(item.source_namespace, item.source_value)
    with st.container(border=True):
        st.markdown(f"**薄弱点：{item.source_value}**")
        suggested_id = resolution.concept.id if resolution.concept else None
        selected_id = st.selectbox(
            "系统建议",
            concept_ids,
            index=concept_ids.index(suggested_id) if suggested_id in concept_ids else 0,
            format_func=lambda concept_id: concept_by_id[concept_id].name,
            key=f"alignment_choice_{normalize_source_value(item.source_value)}",
        )
        accept_col, skip_col = st.columns(2)
        if accept_col.button("使用这个匹配", key=f"alignment_accept_{normalize_source_value(item.source_value)}"):
            refreshed = review_service.confirm_and_rebuild(
                scope=scope,
                exam_scope=exam_scope,
                source_value=item.source_value,
                concept_id=int(selected_id),
            )
            st.session_state[DIAGNOSIS_KEY] = refreshed
            st.session_state.pop(PLAN_KEY, None)
            st.session_state.pop(SAVED_TASK_KEY, None)
            st.rerun()
        if skip_col.button("本次不推荐", key=f"alignment_skip_{normalize_source_value(item.source_value)}"):
            exclusions.add(item.source_value)
            st.session_state[scope_exclusion_key] = sorted(exclusions)
            st.session_state[DIAGNOSIS_KEY] = apply_scope_exclusions(diagnosis, exclusions)
            st.session_state.pop(PLAN_KEY, None)
            st.session_state.pop(SAVED_TASK_KEY, None)
            st.rerun()
```

Keep one secondary text link, “打开知识点整理（高级）”, for maintenance. Remove the advanced-page jump as the required path.

- [ ] **Step 5: Include the mapping revision in the selection signature**

Change `_scope_signature` to accept a revision token:

```python
def _scope_signature(
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    alignment_revision: str,
) -> str:
    return json.dumps(
        {
            "scope": dict(scope),
            "exam_scope": dict(exam_scope),
            "alignment_revision": alignment_revision,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
```

Build it using `ConceptAlignmentService(pm.qb_db_path).revision_token()`. After a confirmation, store the refreshed signature before rerun so the rebuilt diagnosis remains current.

- [ ] **Step 6: Update page contract tests and run them**

Update `tests/test_training_recommendation_ui.py` to assert:

```python
assert "AlignmentReviewService" in PAGE_SOURCE
assert "使用这个匹配" in PAGE_SOURCE
assert "本次不推荐" in PAGE_SOURCE
assert "alignment_revision" in PAGE_SOURCE
assert "前往知识点对齐中心处理" not in PAGE_SOURCE
```

Run:

```powershell
python -m pytest -q tests/test_alignment_review_service.py tests/test_training_recommendation_ui.py tests/test_diagnosis_profile_service.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit inline review and immediate refresh**

```powershell
git add -- question_bank/services/alignment_review_service.py pages/训练推荐.py tests/test_alignment_review_service.py tests/test_training_recommendation_ui.py
git commit -m "feat: review knowledge exceptions in training flow"
```

---

### Task 5: Require fine-skill evidence before automatic recommendation

**Files:**

- Create: `question_bank/recommendation/fine_skill_matching.py`
- Create: `tests/test_fine_skill_matching.py`
- Modify: `question_bank/recommendation/scoring.py:28-81`
- Modify: `question_bank/recommendation/practice_plan_service.py:298-348,503-598,643-663`
- Modify: `tests/test_practice_candidate_scoring.py`
- Modify: `tests/test_practice_plan_service.py`

**Interfaces:**

- Produces: `fine_skill_match_score(target_skills, candidate_tags) -> float` in `[0.0, 1.0]`.
- Extends: `score_candidate(..., fine_skill_match, allow_broad_fallback=False)`.
- Extends: `PracticePlanService.generate()` and `generate_variant()` with `allow_broad_fallback: bool = False`.
- Confirmed explicit knowledge relations remain trusted stage evidence; ordinary same-concept candidates require fine-skill evidence.

- [ ] **Step 1: Write failing fine-skill unit tests**

Create `tests/test_fine_skill_matching.py`:

```python
from question_bank.recommendation.fine_skill_matching import fine_skill_match_score


def test_exact_and_contained_skill_matches_are_strong() -> None:
    assert fine_skill_match_score(["角平分线性质"], ["角平分线性质"]) == 1.0
    assert fine_skill_match_score(["角平分线性质"], ["利用角平分线性质求面积"]) >= 0.8


def test_same_broad_area_without_skill_overlap_does_not_match() -> None:
    assert fine_skill_match_score(["角平分线性质"], ["线段中点计算", "线段与角"]) == 0.0


def test_punctuation_and_whitespace_do_not_break_matching() -> None:
    assert fine_skill_match_score(["一次函数 图像应用"], ["一次函数图像应用"]) == 1.0
```

- [ ] **Step 2: Write the failing candidate eligibility test**

Append to `tests/test_practice_candidate_scoring.py`:

```python
def test_confirmed_broad_concept_without_fine_skill_is_ineligible_by_default() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=0.0,
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
    )

    assert result.eligible is False
    assert "具体训练技能不匹配" in result.warnings


def test_advanced_broad_fallback_is_explicit_and_warned() -> None:
    result = score_candidate(
        concept_match=1.0,
        mapping_status="confirmed",
        fine_skill_match=0.0,
        frequency_fit=1.0,
        gradient_fit=1.0,
        diversity_fit=1.0,
        allow_broad_fallback=True,
    )

    assert result.eligible is True
    assert "仅按标准知识点大类补足" in result.warnings
```

Update every pre-existing `score_candidate()` call in `tests/test_practice_candidate_scoring.py` with `fine_skill_match=1.0` when that test is exercising frequency, gradient, diversity or weight behavior rather than fine-skill rejection. This keeps each test focused on one dimension.

- [ ] **Step 3: Run tests and verify missing fine-skill behavior**

```powershell
python -m pytest -q tests/test_fine_skill_matching.py tests/test_practice_candidate_scoring.py -k "fine_skill or broad"
```

Expected: missing module/signature failures.

- [ ] **Step 4: Implement normalized fine-skill scoring**

Create `question_bank/recommendation/fine_skill_matching.py`:

```python
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable


_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize(value: object) -> str:
    return _NON_WORD.sub("", str(value or "")).casefold()


def fine_skill_match_score(
    target_skills: Iterable[object],
    candidate_tags: Iterable[object],
) -> float:
    targets = [_normalize(value) for value in target_skills if _normalize(value)]
    candidates = [_normalize(value) for value in candidate_tags if _normalize(value)]
    best = 0.0
    for target in targets:
        for candidate in candidates:
            if target == candidate:
                best = max(best, 1.0)
            elif min(len(target), len(candidate)) >= 2 and (target in candidate or candidate in target):
                best = max(best, 0.8)
            else:
                ratio = SequenceMatcher(None, target, candidate).ratio()
                if ratio >= 0.72:
                    best = max(best, round(ratio, 4))
    return best


__all__ = ["fine_skill_match_score"]
```

- [ ] **Step 5: Make fine skill a candidate gate**

In `question_bank/recommendation/scoring.py`, extend the keyword-only signature with `fine_skill_match: object` and `allow_broad_fallback: bool = False`, add `fine_skill_match` to components, and replace eligibility calculation with:

```python
fine_match = _rate(fine_skill_match, default=0.0)
components["fine_skill"] = fine_match
confirmed_concept = _text(mapping_status).casefold() == "confirmed" and components["concept"] > 0
eligible = confirmed_concept and (fine_match > 0 or allow_broad_fallback)
if not confirmed_concept:
    warnings.append("知识点映射未确认或候选题与目标概念不匹配")
elif fine_match <= 0 and not allow_broad_fallback:
    warnings.append("具体训练技能不匹配")
elif fine_match <= 0:
    warnings.append("仅按标准知识点大类补足")
```

Do not add `fine_skill` to teacher-configurable weights. It is a gate and primary sorting key, not another knob.

- [ ] **Step 6: Compute role-level fine-skill evidence and sort deterministically**

Extend `PracticePlanService.generate()` and `generate_variant()` with:

```python
allow_broad_fallback: bool = False,
```

Pass the value from `generate()` to every `generate_variant()` call and record it under `generation_config["allow_broad_fallback"]` in the returned plan and variant snapshots.

In `_assign_candidate_roles()`:

```python
target_skills = list(weak.get("sub_skill_tags") or []) or [str(weak.get("source_term") or "")]
candidate_skill_tags = [
    value
    for tag_type in ("knowledge_point", "prerequisite", "method", "model")
    for value in candidate.get("tags", {}).get(tag_type, [])
]
fine_match = fine_skill_match_score(target_skills, candidate_skill_tags)
```

Store `fine_skill_match` and `target_skills` in each direct/difficulty role. For a role created by an explicitly persisted `knowledge_relations` row, set `fine_skill_match=1.0` and `match_basis="confirmed_relation"`; this preserves trusted teacher-created relations without allowing ordinary broad matches.

Pass the role score and `allow_broad_fallback` to `score_candidate()`. Sort stage candidates with this tuple before the existing tie-breaker:

```python
ranking_key = (
    -float(role["fine_skill_match"]),
    -float(result.components["gradient"]),
    -float(result.components["frequency"]),
    -float(result.components["diversity"]),
    question_id,
)
```

Include `fine_skill_match`, `target_skills`, and `match_basis` in `_item_payload()` so previews and task snapshots explain the selection.

- [ ] **Step 7: Add service-level broad-mismatch and shortage tests**

In `tests/test_practice_plan_service.py`, add an explicit broad-only candidate test:

```python
def test_broad_only_candidate_requires_explicit_advanced_fallback(
    practice_system: tuple[PracticePlanService, dict],
) -> None:
    service, diagnosis_profile = practice_system
    target_concept_id = int(diagnosis_profile["weak_points"][0]["concept_id"])
    alignment = ConceptAlignmentService(service.db_path)
    alignment.confirm_mapping(
        "question_tag",
        "函数图像平移",
        target_concept_id,
        reviewed_by="teacher",
    )
    with connect(service.db_path) as conn:
        conn.execute("UPDATE questions SET is_deleted = 1 WHERE id BETWEEN 210 AND 215")
        _insert_question(
            conn,
            question_id=450,
            title="只有大类相同",
            text="完成函数图像的平移操作。",
            knowledge="函数图像平移",
            method="图像平移",
            difficulty="5",
        )

    strict = service.generate_variant(
        diagnosis_profile,
        question_count=8,
        exclude_question_ids={201},
    )
    fallback = service.generate_variant(
        diagnosis_profile,
        question_count=8,
        exclude_question_ids={201},
        allow_broad_fallback=True,
    )

    assert 450 not in {item["question_id"] for item in strict["items"]}
    fallback_item = next(item for item in fallback["items"] if item["question_id"] == 450)
    assert "仅按标准知识点大类补足" in fallback_item["warnings"]
```

Update fixture weak points with explicit `source_term` and candidate tags so current direct, prerequisite and transfer expectations remain intentional.

Update fixture weak points with explicit `source_term` and candidate tags so current direct, prerequisite and transfer expectations remain intentional.

- [ ] **Step 8: Run recommendation tests**

```powershell
python -m pytest -q tests/test_fine_skill_matching.py tests/test_practice_candidate_scoring.py tests/test_practice_plan_service.py tests/test_practice_grouping.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit fine-skill recommendation safety**

```powershell
git add -- question_bank/recommendation/fine_skill_matching.py question_bank/recommendation/scoring.py question_bank/recommendation/practice_plan_service.py tests/test_fine_skill_matching.py tests/test_practice_candidate_scoring.py tests/test_practice_plan_service.py
git commit -m "feat: require fine skill evidence for recommendations"
```

---

### Task 6: Simplify the ordinary training-generation interface

**Files:**

- Modify: `question_bank/recommendation/training_plan.py:12-16`
- Modify: `pages/训练推荐.py:611-926`
- Modify: `tests/test_training_recommendation_ui.py`
- Modify: `tests/test_training_task_history_ui.py`

**Interfaces:**

- Consumes: inline review from Task 4 and `allow_broad_fallback` from Task 5.
- Ordinary UI exposes exactly: question count, variant mode and original-question exclusion.
- Advanced UI exposes stage ratios and broad fallback; recommendation weights remain internal defaults.

- [ ] **Step 1: Update the UI contract test first**

Replace obsolete assertions in `tests/test_training_recommendation_ui.py` and add:

```python
def test_training_page_exposes_only_teacher_facing_core_controls() -> None:
    for label in ("每个版本题量", "训练版本", "排除当前所选考试的原题"):
        assert label in PAGE_SOURCE
    assert "高级设置" in PAGE_SOURCE
    assert "推荐排序权重" not in PAGE_SOURCE
    assert "include_historical_wrong_questions" not in PAGE_SOURCE
    assert "历史错题回流" not in PAGE_SOURCE


def test_training_page_uses_teacher_facing_title_and_stage_names() -> None:
    assert 'st.title("生成错题巩固练习")' in PAGE_SOURCE
    for label in ("基础巩固", "针对训练", "提升应用"):
        assert label in PAGE_SOURCE
```

- [ ] **Step 2: Run the test and verify it fails against the current controls**

```powershell
python -m pytest -q tests/test_training_recommendation_ui.py
```

Expected: failures mention the old title, weight controls and historical-wrong-question checkbox.

- [ ] **Step 3: Change default stage ratios without breaking the ten-question 6/3/1 mix**

In `question_bank/recommendation/training_plan.py`:

```python
DEFAULT_STAGE_RATIOS = {
    "direct": 0.60,
    "prerequisite": 0.30,
    "transfer": 0.10,
}
```

Retain internal stage keys for database compatibility. Use teacher labels only in the page.

- [ ] **Step 4: Collapse the generation controls**

In `pages/训练推荐.py`:

- Change the title to `生成错题巩固练习`.
- Keep `训练版本`, `每个版本题量`, and `排除当前所选考试的原题和可识别近重复题` visible.
- Remove the four recommendation-weight inputs.
- Remove the historical-wrong-question checkbox and always pass `False` to the backward-compatible service parameter.
- Place stage ratio controls and `允许仅大类匹配的题目补足（不推荐）` inside a collapsed `st.expander("高级设置", expanded=False)`.
- Default advanced values to 60, 30 and 10; default broad fallback to false.
- Pass no custom weights to `PracticePlanService.generate()`.
- Pass `allow_broad_fallback` from the advanced checkbox.

Use this teacher-facing mapping wherever a stage is displayed:

```python
STAGE_LABELS = {
    "prerequisite": "基础巩固",
    "direct": "针对训练",
    "transfer": "提升应用",
}
```

Hide internal recommendation scores from the summary table. Keep component details only inside an expander named `推荐依据（高级）`.

- [ ] **Step 5: Update task-history rendering contracts**

Update `tests/test_training_task_history_ui.py` only where old stage names or always-visible scores are asserted. Preserve saved-task, export and retry behavior exactly.

- [ ] **Step 6: Run page and task-history tests**

```powershell
python -m pytest -q tests/test_training_recommendation_ui.py tests/test_training_task_history_ui.py tests/test_training_task_service.py tests/test_training_export_service.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit the simplified ordinary interface**

```powershell
git add -- question_bank/recommendation/training_plan.py pages/训练推荐.py tests/test_training_recommendation_ui.py tests/test_training_task_history_ui.py
git commit -m "feat: simplify training generation for teachers"
```

---

### Task 7: Turn the old alignment page into an advanced maintenance page

**Files:**

- Modify: `pages/知识图谱适配调试.py:180-660`
- Modify: `tests/test_knowledge_alignment_ui.py`

**Interfaces:**

- Consumes: structured focus and normalized terms from Tasks 1 and 3.
- Keeps existing persistent service operations available for maintenance.
- Does not expose technical controls in the default expanded view.

- [ ] **Step 1: Replace old page contract assertions**

Update `tests/test_knowledge_alignment_ui.py`:

```python
def test_alignment_page_is_advanced_maintenance_not_required_teacher_flow() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert 'st.title("知识点整理（高级）")' in source
    assert "系统自动整理" in source
    assert "需要教师确认" in source
    assert "AI 处理详情（维护）" in source
    assert "置信度阈值" not in source
    assert "自动勾选" not in source


def test_relation_editor_is_not_shown_when_no_relations_exist() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "if relations:" in source
    assert "知识点关系管理（维护）" in source
```

- [ ] **Step 2: Run the UI test and verify current technical UI fails it**

```powershell
python -m pytest -q tests/test_knowledge_alignment_ui.py
```

Expected: failures mention the old title, confidence threshold and automatic checkbox controls.

- [ ] **Step 3: Simplify default advanced-page content**

In `pages/知识图谱适配调试.py`:

- Rename the page and browser title to `知识点整理（高级）`.
- Replace English/internal statuses with `系统自动整理`, `需要教师确认`, `本次不使用`, and `教师已确认`.
- Remove the confidence threshold slider and automatic checkbox action.
- Keep AI batch processing inside `st.expander("AI 处理详情（维护）", expanded=False)`.
- Keep the table read-only for overview. Under it, add one `selectbox("选择要修改的记录", ...)`, one concept selector formatted by concept name, and the three explicit actions “保存修改”, “恢复系统建议”, and “本次停用”. Do not retain row checkboxes or batch confirmation.
- Move concept IDs, canonical keys, source namespaces and confidence values into a collapsed diagnostics expander.
- Render relation management only when `relations` is non-empty:

```python
relations = service.list_relations()
if relations:
    with st.expander("知识点关系管理（维护）", expanded=False):
        _render_relation_editor(service)
```

- Keep concept creation and alias editing because the inline training flow depends on having a correct alternative concept.

- [ ] **Step 4: Run advanced-page and service tests**

```powershell
python -m pytest -q tests/test_knowledge_alignment_ui.py tests/test_concept_alignment_service.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit advanced maintenance simplification**

```powershell
git add -- pages/知识图谱适配调试.py tests/test_knowledge_alignment_ui.py
git commit -m "refactor: simplify advanced knowledge maintenance"
```

---

### Task 8: Prove the real regression is fixed and update operations documentation

**Files:**

- Modify: `tests/test_knowledge_practice_end_to_end.py`
- Modify: `docs/knowledge-practice-operations.md`
- Verify: all related tests and the full suite.

**Interfaces:**

- Exercises the complete chain: numbered grading label → legacy confirmation reuse → diagnosis → fine-skill recommendation → task save → export.
- Documents only behavior available in the completed implementation.

- [ ] **Step 1: Add an end-to-end numbered-label regression test**

Add to `tests/test_knowledge_practice_end_to_end.py`:

```python
def test_numbered_weak_point_confirmation_survives_simplified_training_flow(tmp_path: Path) -> None:
    grading_db = tmp_path / "grading_system.db"
    question_bank_db = tmp_path / "question_bank.db"
    _build_numbered_grading_fixture(
        grading_db,
        tmp_path,
        knowledge_id="G7_15",
        knowledge_name="角平分线性质",
    )
    question_id, concept_id = _build_angle_bisector_question_bank(question_bank_db)
    alignment = ConceptAlignmentService(question_bank_db)
    alignment.confirm_mapping(
        "grading_weak_point",
        "G7_15 · 角平分线性质",
        concept_id,
        reviewed_by="teacher",
    )

    diagnosis = DiagnosisProfileService(grading_db, question_bank_db).build_profiles(
        scope={"mode": "student", "student_ids": ["70"]},
        exam_scope={"mode": "current", "session_ids": [1]},
    )
    plan = PracticePlanService(question_bank_db).generate(
        diagnosis,
        question_count=8,
    )

    weak = diagnosis["students"][0]["weak_points"][0]
    assert weak["source_term"] == "角平分线性质"
    assert weak["mapping_status"] == "confirmed"
    assert question_id in {
        item["question_id"]
        for variant in plan["variants"]
        for item in variant["items"]
    }
```

Add the two fixture helpers to the same test file:

```python
def _build_numbered_grading_fixture(
    db_path: Path,
    root: Path,
    *,
    knowledge_id: str,
    knowledge_name: str,
) -> None:
    DBManager(db_path).initialize()
    rubric_path = root / "numbered_rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 10,
                        "knowledge_id": knowledge_id,
                        "knowledge_name": knowledge_name,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (70, 'S70', '测试学生', '10')"
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (1, '编号知识点考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        _insert_result(
            conn,
            session_id=1,
            student_id=70,
            paper_id=7001,
            result_id=70001,
            student_score=0,
            details=[("Q1", 0, knowledge_id)],
        )


def _build_angle_bisector_question_bank(db_path: Path) -> tuple[int, int]:
    initialize_database(db_path)
    alignment = ConceptAlignmentService(db_path)
    concept = alignment.create_concept("math.angle_bisector", "角平分线")
    alignment.confirm_mapping(
        "question_tag",
        "角平分线性质",
        concept.id,
        reviewed_by="teacher",
    )
    with connect(db_path) as conn:
        for index, question_id in enumerate(range(710, 718), start=1):
            _insert_question(
                conn,
                question_id,
                f"角平分线训练{index}",
                f"利用角平分线性质完成第 {index} 个计算。",
                "角平分线性质",
                f"角平分线方法{index}",
                "5",
            )
    return 710, concept.id
```

- [ ] **Step 2: Run the new end-to-end test**

```powershell
python -m pytest -q tests/test_knowledge_practice_end_to_end.py::test_numbered_weak_point_confirmation_survives_simplified_training_flow
```

Expected: pass.

- [ ] **Step 3: Rewrite the teacher operations guide**

Update `docs/knowledge-practice-operations.md` to document:

1. Select exam and students.
2. Click “分析学习薄弱点”.
3. Review only cards labelled “需要确认”.
4. Choose “使用这个匹配”, another knowledge point, or “本次不推荐”.
5. Generate preview with the three ordinary controls.
6. Understand that missing precise questions produce a shortage instead of broad-topic filler.
7. Use “知识点整理（高级）” only to correct historical decisions or manage concepts.

Remove instructions for confidence thresholds, batch checkboxes, required page jumping and historical wrong-question return.

- [ ] **Step 4: Run the complete related regression set**

```powershell
python -m pytest -q tests/test_knowledge_term_identity.py tests/test_alignment_review_service.py tests/test_fine_skill_matching.py tests/test_concept_alignment_service.py tests/test_diagnosis_profile_service.py tests/test_knowledge_alignment_ui.py tests/test_training_recommendation_ui.py tests/test_practice_candidate_scoring.py tests/test_practice_plan_service.py tests/test_practice_grouping.py tests/test_training_task_service.py tests/test_training_task_history_ui.py tests/test_training_export_service.py tests/test_knowledge_practice_end_to_end.py
```

Expected: zero failures.

- [ ] **Step 5: Run the full suite**

```powershell
python -m pytest -q
```

Expected: zero failures. If unrelated pre-existing failures occur, record the exact failing test names and verify all tests from Step 4 remain green before stopping.

- [ ] **Step 6: Manually verify the teacher flow**

Start the application using the existing launcher, then verify with the real case shown during diagnosis:

1. Select exam `0609` and student `刘宇婉`.
2. Analyze weak points.
3. Confirm that the 17 source terms no longer lead to an empty action area.
4. Confirm that the 16 existing teacher mappings are recognized after compatibility migration.
5. Confirm that any remaining AI-only suggestion appears as a simple card.
6. Confirm that accepting a card refreshes the diagnosis immediately.
7. Confirm that ordinary generation shows only question count, variant mode and original-question exclusion.
8. Confirm that a broad-only candidate is absent unless the advanced fallback is explicitly enabled.

- [ ] **Step 7: Commit end-to-end evidence and documentation**

```powershell
git add -- tests/test_knowledge_practice_end_to_end.py docs/knowledge-practice-operations.md
git commit -m "test: cover simplified knowledge training flow"
```

---

## Completion Checklist

- [ ] The 17-term focus regression is represented by an automated test.
- [ ] Legacy `G7_15 · 知识点` confirmations resolve from the plain semantic term.
- [ ] Same-name question tags cannot replace grading weak points in focus mode.
- [ ] Exact names, registered aliases and conflict-free history auto-confirm; fuzzy AI suggestions do not.
- [ ] Teachers can confirm exceptions without leaving the training page.
- [ ] Confirmation invalidates stale diagnosis and preview state immediately.
- [ ] Broad-only candidates are excluded by default and shortages are explicit.
- [ ] Ordinary controls are limited to question count, variant mode and original-question exclusion.
- [ ] The no-op historical wrong-question control is absent.
- [ ] Advanced maintenance remains available without being a required workflow.
- [ ] Related regression set and full suite both complete with zero failures, or unrelated pre-existing failures are documented precisely.
