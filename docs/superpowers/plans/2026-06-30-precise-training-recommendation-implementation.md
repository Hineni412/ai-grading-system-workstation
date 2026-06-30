# 精确小问训练推荐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 训练推荐按评分小问已确认主要技能精确匹配题库小问，并用掌握率自适应难度硬过滤，保证所有自动推荐永不包含难度 `8.0—10.0` 的题。

**Architecture:** 在现有 skill 模式上收敛，不再让训练页走粗 `question_tag` 分支。难度策略成为独立纯函数；候选加载器只读取已确认题库小问主技能；目标选择器取最多三个最薄弱技能并轮转分配题量；所有旧推荐入口增加 `< 8.0` 防线，避免旁路重新引入高难题。

**Tech Stack:** Python 3.11、SQLite、Streamlit、pytest；复用 `PracticePlanService`、`DiagnosisProfileService.build_skill_profiles()`、`question_part_skill_links`、现有相似题去重、考频和训练任务快照。

## Global Constraints

- 自动候选必须同时精确匹配同一 `knowledge_point` 和同一已确认主要 `skill_id`；方法、模型和错因不能替代该门槛。
- 难度必须有效且 `< 8.0`；`8.0`、`8.1`、`10.0` 在所有模式、阶段、档位和配置下均排除。
- 难度未知、标签未确认、关联失效、原题、近重复题和区间外题目不能进入排序。
- 候选不足时保留实际题数并报告原因，不回退到粗 `knowledge_point`、相邻技能或高难题凑数。
- 默认最多训练三个最薄弱主要技能，按掌握率升序、扣分分值降序、证据量降序排序，并轮转分配题量。
- 掌握率 `<40%`：基础 `1.0—2.9`、直接 `2.0—3.9`、迁移关闭，配比 `50/50/0`。
- 掌握率 `40%—70%`：基础 `2.0—3.9`、直接 `3.0—5.9`、迁移 `5.0—6.9`，配比 `30/60/10`。
- 掌握率 `>70%`：基础 `3.0—4.9`、直接 `4.0—6.9`、迁移 `6.0—7.9`，配比 `20/60/20`；`70.0%` 仍属于中档。
- 单技能题可进入基础或直接训练；多技能综合题只能进入迁移训练且整题最高小问难度 `< 8.0`。
- 本计划依赖前两份 2026-06-30 小问打标与分层图谱计划已完成。

---

## File Structure

- `question_bank/recommendation/difficulty_policy.py` — 一位小数解析、自适应区间和全局 8.0 硬上限。
- `question_bank/recommendation/training_targets.py` — 目标排序、最多三个技能和轮转题量。
- `question_bank/recommendation/precise_candidate_selector.py` — 硬过滤、阶段队列、排序和排除审计。
- `question_bank/recommendation/practice_plan_service.py` — 接入精确技能路径，移除训练页粗标签和相邻技能补位。
- `question_bank/recommendation/recommendation_engine.py`、`question_bank/recommendation/scoring.py` — 旧入口防御性 `<8.0` 过滤和小数难度解析。
- `integration/diagnosis_profile_service.py` — 输出扣分分值、主要技能和候选资格。
- `pages/训练推荐.py` — 按主要子技能和掌握率档位展示目标、计划和缺口。
- `question_bank/services/training_task_service.py` — 快照保留目标技能、小问、难度策略和排除审计。
- `tests/test_training_difficulty_policy.py`、`tests/test_precise_training_candidates.py`、`tests/test_practice_plan_service.py`、`tests/test_practice_grouping.py`、`tests/test_training_recommendation_ui.py`、`tests/test_unified_skill_recommendation.py` — 策略、候选、分配、UI 和兼容测试。

### Task 1: 建立自适应难度硬策略

**Files:**
- Create: `question_bank/recommendation/difficulty_policy.py`
- Create: `tests/test_training_difficulty_policy.py`
- Modify: `question_bank/recommendation/recommendation_engine.py:84-99`
- Modify: `question_bank/recommendation/scoring.py:127-150,210-220`

**Interfaces:**
- Produces: `parse_decimal_difficulty(value) -> float | None`。
- Produces: `adaptive_policy(mastery) -> AdaptiveDifficultyPolicy`。
- Produces: `is_automatic_difficulty_eligible(stage, difficulty, mastery) -> bool`。

- [ ] **Step 1: 写边界与全局上限失败测试**

```python
@pytest.mark.parametrize(
    "mastery, expected_band, expected_ratios",
    [(0.399, "foundation", {"prerequisite": 0.5, "direct": 0.5, "transfer": 0.0}),
     (0.4, "developing", {"prerequisite": 0.3, "direct": 0.6, "transfer": 0.1}),
     (0.7, "developing", {"prerequisite": 0.3, "direct": 0.6, "transfer": 0.1}),
     (0.701, "secure", {"prerequisite": 0.2, "direct": 0.6, "transfer": 0.2})],
)
def test_adaptive_policy_boundaries(mastery, expected_band, expected_ratios) -> None:
    policy = adaptive_policy(mastery)
    assert policy.band == expected_band
    assert policy.stage_ratios == expected_ratios

@pytest.mark.parametrize("difficulty", [8.0, 8.1, 9.9, 10.0, None, "bad"])
@pytest.mark.parametrize("stage", ["prerequisite", "direct", "transfer"])
def test_automatic_training_always_rejects_eight_or_invalid(difficulty, stage) -> None:
    assert not is_automatic_difficulty_eligible(stage, difficulty, mastery=0.95)

def test_seven_point_nine_is_distinct_and_can_enter_high_transfer() -> None:
    assert parse_decimal_difficulty("7.9") == 7.9
    assert is_automatic_difficulty_eligible("transfer", 7.9, mastery=0.71)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_training_difficulty_policy.py -q`

Expected: FAIL，策略模块不存在，现有解析会把 7.9 截断为 7。

- [ ] **Step 3: 实现纯策略对象**

```python
AUTOMATIC_DIFFICULTY_CEILING = 8.0

@dataclass(frozen=True, slots=True)
class AdaptiveDifficultyPolicy:
    band: str
    ranges: Mapping[str, tuple[float, float] | None]
    stage_ratios: Mapping[str, float]

def adaptive_policy(mastery: object) -> AdaptiveDifficultyPolicy:
    value = min(1.0, max(0.0, float(mastery)))
    if value < 0.4:
        return AdaptiveDifficultyPolicy("foundation", {
            "prerequisite": (1.0, 2.9), "direct": (2.0, 3.9), "transfer": None,
        }, {"prerequisite": 0.5, "direct": 0.5, "transfer": 0.0})
    if value <= 0.7:
        return AdaptiveDifficultyPolicy("developing", {
            "prerequisite": (2.0, 3.9), "direct": (3.0, 5.9), "transfer": (5.0, 6.9),
        }, {"prerequisite": 0.3, "direct": 0.6, "transfer": 0.1})
    return AdaptiveDifficultyPolicy("secure", {
        "prerequisite": (3.0, 4.9), "direct": (4.0, 6.9), "transfer": (6.0, 7.9),
    }, {"prerequisite": 0.2, "direct": 0.6, "transfer": 0.2})

def is_automatic_difficulty_eligible(stage: str, difficulty: object, mastery: object) -> bool:
    value = parse_decimal_difficulty(difficulty)
    if value is None or value >= AUTOMATIC_DIFFICULTY_CEILING:
        return False
    allowed = adaptive_policy(mastery).ranges.get(str(stage))
    return bool(allowed and allowed[0] <= value <= allowed[1])
```

`parse_decimal_difficulty()` 复用 `normalize_difficulty()`，捕获 `ValueError` 后返回 `None`；不得调用 `int(float(value))`。

- [ ] **Step 4: 给旧推荐函数加防御性上限**

`practice_gradient_fit()` 对 `None` 或 `>=8.0` 返回 `None`；`recommend_for_weak_point()` 在评分前过滤；`score_candidate()` 收到无难度适配值时 `eligible=False`。这些入口即使未来被误调用，也不能推荐 8 分题。

- [ ] **Step 5: 运行策略和旧评分回归**

Run: `runtime\python\python.exe -m pytest tests/test_training_difficulty_policy.py tests/test_practice_candidate_scoring.py tests/test_question_tag_recommendation.py -q`

Expected: PASS；需要同步旧测试中“难度缺失仍可推荐”与“8—10 可迁移”的过期断言。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/recommendation/difficulty_policy.py question_bank/recommendation/recommendation_engine.py question_bank/recommendation/scoring.py tests/test_training_difficulty_policy.py tests/test_practice_candidate_scoring.py tests/test_question_tag_recommendation.py
git commit -m "feat: enforce adaptive training difficulty ceiling"
```

### Task 2: 读取题库小问确认技能并分类整题

**Files:**
- Create: `question_bank/recommendation/precise_candidate_selector.py`
- Create: `tests/test_precise_training_candidates.py`
- Modify: `question_bank/recommendation/practice_plan_service.py:670-760`

**Interfaces:**
- Produces: `load_precise_candidates(db_path) -> list[PreciseCandidate]`。
- Produces: `PreciseCandidate.confirmed_primary_skill_ids`、`suggested_primary_skill_ids`、`part_refs`、`difficulty`、`is_composite`。
- Produces: `filter_candidate(candidate, target, stage) -> CandidateDecision`。

- [ ] **Step 1: 写硬过滤失败测试**

```python
def test_candidate_requires_confirmed_same_primary_skill_and_valid_difficulty(tmp_path: Path) -> None:
    candidates = load_precise_candidates(seeded_candidates(tmp_path))
    decisions = {item.question_id: filter_candidate(item, target(skill_id=12, mastery=0.5), "direct") for item in candidates}
    assert decisions[101].eligible
    assert decisions[102].reason == "primary_skill_mismatch"
    assert decisions[103].reason == "skill_unconfirmed"
    assert decisions[104].reason == "difficulty_out_of_band"
    assert decisions[105].reason == "difficulty_at_or_above_8"
    assert decisions[106].reason == "difficulty_missing"

def test_same_skill_with_different_knowledge_point_is_not_eligible() -> None:
    candidate = candidate_fixture(
        confirmed_primary_skill_ids=(12,),
        knowledge_points_by_skill={12: ("函数图像",)},
        difficulty=4.2,
    )
    assert filter_candidate(candidate, target(skill_id=12, knowledge_point="一次函数解析式", mastery=0.5), "direct").reason == "knowledge_point_mismatch"

def test_composite_question_is_transfer_only() -> None:
    candidate = candidate_fixture(confirmed_primary_skill_ids=(12, 17), difficulty=6.2)
    assert not filter_candidate(candidate, target(skill_id=12, mastery=0.5), "direct").eligible
    assert filter_candidate(candidate, target(skill_id=12, mastery=0.5), "transfer").eligible
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_precise_training_candidates.py -q`

Expected: FAIL，候选仍只按整题 `question_skill_links` 加载。

- [ ] **Step 3: 实现候选值对象和批量查询**

```python
@dataclass(frozen=True, slots=True)
class PreciseCandidate:
    question_id: int
    confirmed_primary_skill_ids: tuple[int, ...]
    suggested_primary_skill_ids: tuple[int, ...]
    part_refs: tuple[str, ...]
    matched_parts_by_skill: Mapping[int, tuple[str, ...]]
    knowledge_points_by_skill: Mapping[int, tuple[str, ...]]
    difficulty: float | None
    tags: Mapping[str, tuple[str, ...]]
    question: Mapping[str, Any]

    @property
    def is_composite(self) -> bool:
        return len(set(self.confirmed_primary_skill_ids)) > 1
```

加载 SQL 同时读取 confirmed/suggested 状态以形成准确排除审计，但只有 `question_parts.review_status='confirmed'` 且 `question_part_skill_links.review_status='confirmed' AND is_primary=1` 的技能进入 `confirmed_primary_skill_ids`；候选目标仅出现在 suggested 关联时进入 `suggested_primary_skill_ids`。整题难度取所有题库小问 `difficulty_tenths` 最大值；任一确认小问缺失难度则为 `None`。未拆分历史 `question_skill_links` 不自动转成精确候选。

- [ ] **Step 4: 实现顺序固定的硬过滤**

```python
def filter_candidate(candidate: PreciseCandidate, target: TrainingTarget, stage: str) -> CandidateDecision:
    if target.skill_id in candidate.suggested_primary_skill_ids and target.skill_id not in candidate.confirmed_primary_skill_ids:
        return CandidateDecision(False, "skill_unconfirmed")
    if target.skill_id not in candidate.confirmed_primary_skill_ids:
        return CandidateDecision(False, "primary_skill_mismatch")
    if target.knowledge_point not in candidate.knowledge_points_by_skill.get(target.skill_id, ()):
        return CandidateDecision(False, "knowledge_point_mismatch")
    if candidate.difficulty is None:
        return CandidateDecision(False, "difficulty_missing")
    if candidate.difficulty >= 8.0:
        return CandidateDecision(False, "difficulty_at_or_above_8")
    if candidate.is_composite and stage != "transfer":
        return CandidateDecision(False, "composite_transfer_only")
    if not is_automatic_difficulty_eligible(stage, candidate.difficulty, target.mastery):
        return CandidateDecision(False, "difficulty_out_of_band")
    return CandidateDecision(True, "eligible")
```

原题和相似题过滤仍在 `PracticePlanService._dedupe_candidates()` 执行，但审计结果必须合并到同一 `reason_counts`。

- [ ] **Step 5: 运行候选测试**

Run: `runtime\python\python.exe -m pytest tests/test_precise_training_candidates.py tests/test_question_skill_dual_write.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/recommendation/precise_candidate_selector.py question_bank/recommendation/practice_plan_service.py tests/test_precise_training_candidates.py
git commit -m "feat: filter training candidates by confirmed part skill"
```

### Task 3: 选择三个薄弱目标并轮转分配题量

**Files:**
- Create: `question_bank/recommendation/training_targets.py`
- Modify: `tests/test_practice_grouping.py`
- Create: `tests/test_training_target_allocation.py`

**Interfaces:**
- Produces: `TrainingTarget(skill_id, skill_name, mastery, deduction_score, evidence_count)`。
- Produces: `select_training_targets(profile, limit=3)`。
- Produces: `allocate_target_counts(targets, question_count)`。

- [ ] **Step 1: 写排序和轮转失败测试**

```python
def test_selects_at_most_three_targets_by_mastery_loss_and_evidence() -> None:
    targets = select_training_targets(profile_with_four_skills(), limit=3)
    assert [target.skill_id for target in targets] == [11, 12, 13]
    assert all(target.skill_id != 14 for target in targets)

def test_target_counts_rotate_without_rich_skill_monopoly() -> None:
    targets = [target_fixture(11), target_fixture(12), target_fixture(13)]
    assert allocate_target_counts(targets, 8) == {11: 3, 12: 3, 13: 2}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_training_target_allocation.py tests/test_practice_grouping.py -q`

Expected: FAIL，目标模块不存在。

- [ ] **Step 3: 实现目标选择**

```python
@dataclass(frozen=True, slots=True)
class TrainingTarget:
    skill_id: int
    skill_name: str
    knowledge_point: str
    mastery: float
    deduction_score: float
    evidence_count: int
    support_tags: Mapping[str, tuple[str, ...]]
    error_types: tuple[str, ...]

def select_training_targets(profile: Mapping[str, Any], *, limit: int = 3) -> tuple[TrainingTarget, ...]:
    if isinstance(profile.get("students"), list):
        weak_points = [
            item for student in profile["students"] if isinstance(student, Mapping)
            for item in student.get("weak_points", []) if isinstance(item, Mapping)
        ]
    else:
        weak_points = [item for item in profile.get("weak_points", []) if isinstance(item, Mapping)]
    targets = [
        TrainingTarget(
            skill_id=int(item["skill_id"]),
            skill_name=str(item.get("skill_name") or ""),
            knowledge_point=str(item.get("knowledge_point") or item.get("topic_name") or ""),
            mastery=float(item.get("mastery") or 0.0),
            deduction_score=float(item.get("deduction_score") or 0.0),
            evidence_count=int(item.get("evidence_count") or 0),
            support_tags={str(k): tuple(v) for k, v in dict(item.get("support_tags") or {}).items()},
            error_types=tuple(str(v) for v in item.get("error_types", [])),
        )
        for item in weak_points if item.get("eligible_for_recommendation") and item.get("skill_id")
    ]
    targets.sort(key=lambda item: (item.mastery, -item.deduction_score, -item.evidence_count, item.skill_id))
    return tuple(targets[:limit])

def allocate_target_counts(targets: Sequence[TrainingTarget], question_count: int) -> dict[int, int]:
    counts = {target.skill_id: 0 for target in targets}
    for index in range(question_count):
        counts[targets[index % len(targets)].skill_id] += 1
    return counts
```

- [ ] **Step 4: 按每个目标掌握率分配阶段数**

对每个目标的题量调用现有 `allocate_stage_counts(count, adaptive_policy(target.mastery).stage_ratios)`；低掌握率 `transfer` 必须分配 0。

- [ ] **Step 5: 运行目标和分组测试**

Run: `runtime\python\python.exe -m pytest tests/test_training_target_allocation.py tests/test_practice_grouping.py -q`

Expected: PASS；自动分组键改为“最多三个主要技能 ID 向量 + 各技能掌握率档位”，不再使用宽泛知识点。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/recommendation/training_targets.py tests/test_training_target_allocation.py tests/test_practice_grouping.py
git commit -m "feat: allocate practice across weak primary skills"
```

### Task 4: 重写 skill 计划选择和排除审计

**Files:**
- Modify: `question_bank/recommendation/practice_plan_service.py:39-290,555-827,1027-1332`
- Modify: `tests/test_practice_plan_service.py`
- Modify: `tests/test_unified_skill_recommendation.py`

**Interfaces:**
- Produces plan item: `target_skill_id`、`matched_part_refs`、`mastery_band`、`difficulty_range`、`selection_reason`。
- Produces shortage: `target_skill_id`、`stage`、`missing_count`、`reason_counts`。
- Keeps: 原题排除、相似题排除、同卷/同方法多样性限制。

- [ ] **Step 1: 写端到端精确推荐失败测试**

```python
def test_plan_is_exact_adaptive_and_never_contains_eight(tmp_path: Path) -> None:
    service, profile = precise_skill_system(tmp_path)
    plan = service.generate(profile, question_count=8, exclude_current_exam_originals=False)
    items = plan["variants"][0]["items"]
    assert items
    assert all(item["match_kind"] == "exact_primary_skill" for item in items)
    assert all(float(item["difficulty"]) < 8.0 for item in items)
    assert {item["target_skill_id"] for item in items} <= set(profile["confirmed_skill_ids"])
    assert all(item["matched_part_refs"] for item in items)

def test_shortage_reports_reasons_without_neighbor_or_coarse_fill(tmp_path: Path) -> None:
    service, profile = sparse_precise_skill_system(tmp_path)
    variant = service.generate(profile, question_count=8, exclude_current_exam_originals=False)["variants"][0]
    assert len(variant["items"]) < 8
    assert variant["shortages"]
    reasons = merge_reason_counts(variant["shortages"])
    assert reasons["difficulty_at_or_above_8"] == 2
    assert reasons["primary_skill_mismatch"] >= 1
    assert all(item.get("match_kind") != "neighbor" for item in variant["items"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_practice_plan_service.py tests/test_unified_skill_recommendation.py -q`

Expected: FAIL，当前难度只是排序分且 skill 路径可补相邻技能。

- [ ] **Step 3: 使用目标 × 阶段队列选择**

```python
for target in targets:
    requested_by_stage = allocate_stage_counts(target_counts[target.skill_id], adaptive_policy(target.mastery).stage_ratios)
    for stage, requested in requested_by_stage.items():
        eligible, audit = selector.eligible_for(target, stage, excluded_ids=selected_ids)
        ranked = sorted(eligible, key=lambda item: (
            abs(item.difficulty - policy_range_center(target, stage)),
            -support_tag_overlap(item, target),
            -error_type_overlap(item, target),
            -frequency_score(item),
            diversity_penalty(item, selected),
            item.question_id,
        ))
        queues[(target.skill_id, stage)] = deque(ranked[:requested])
```

最终按目标顺序轮转取队列；某目标队列为空时记录缺口，不把名额直接全部给候选最多的技能。全局 `selected_question_ids` 保证整题不重复。

- [ ] **Step 4: 删除训练页可达的邻居/粗标签补位**

`PracticePlanService.generate()` 对 `diagnosis_identity='skill'` 固定精确主技能路径；`related_fill_policy` 在兼容 API 中仅保留解析，但新路径若不是 `exact_only` 直接归一为 `exact_only` 并写 warning。`question_tag` 分支保留给旧快照重放，但同样应用 `<8.0` 硬过滤，训练页不再调用它。

- [ ] **Step 5: 运行计划测试**

Run: `runtime\python\python.exe -m pytest tests/test_practice_plan_service.py tests/test_unified_skill_recommendation.py tests/test_question_tag_recommendation.py tests/test_recommendation_frequency_scoring.py -q`

Expected: PASS；没有任何自动计划项难度大于等于 8.0。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/recommendation/practice_plan_service.py tests/test_practice_plan_service.py tests/test_unified_skill_recommendation.py tests/test_question_tag_recommendation.py
git commit -m "feat: generate exact adaptive primary skill practice"
```

### Task 5: 更新训练页为主要子技能界面

**Files:**
- Modify: `pages/训练推荐.py:110-220,679-830`
- Modify: `tests/test_training_recommendation_ui.py`

**Interfaces:**
- Consumes: `DiagnosisProfileService.build_skill_profiles()`。
- Displays: 目标主技能、掌握率档位、自适应区间、候选排除原因。

- [ ] **Step 1: 写页面契约失败测试**

```python
def test_training_page_uses_primary_skills_and_adaptive_difficulty() -> None:
    source = Path("pages/训练推荐.py").read_text(encoding="utf-8")
    assert ".build_skill_profiles(" in source
    assert ".build_tag_profiles(" not in source
    assert "主要子技能" in source
    assert "自适应难度" in source
    assert "8.0—10.0 不进入自动训练" in source
    assert "精确匹配已确认主要子技能" in source

def test_fixed_30_60_10_controls_are_removed() -> None:
    source = Path("pages/训练推荐.py").read_text(encoding="utf-8")
    assert 'number_input("基础巩固 %"' not in source
    assert 'number_input("针对训练 %"' not in source
    assert 'number_input("提升应用 %"' not in source
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_training_recommendation_ui.py -q`

Expected: FAIL，当前页面只显示 knowledge_point 且固定 30/60/10。

- [ ] **Step 3: 切换诊断和目标摘要**

```python
diagnosis = DiagnosisProfileService(pm.db_path, pm.qb_db_path).build_skill_profiles(
    scope=selected_scope,
    exam_scope=selected_exam_scope,
)
for target in select_training_targets(diagnosis, limit=3):
    st.markdown(f"**{target.skill_name}** · 掌握率 {target.mastery:.1%} · {band_label(target.mastery)}")
    st.caption(format_policy(adaptive_policy(target.mastery)))
```

页面第三步改名“查看主要子技能”；不再把 `knowledge_point` 标签数量当训练目标数量。

- [ ] **Step 4: 显示推荐理由和缺口审计**

```python
st.caption(
    f"精确匹配已确认主要子技能「{item['target_skill_name']}」 · "
    f"小问 {', '.join(item['matched_part_refs'])} · 难度 {float(item['difficulty']):.1f}"
)
for shortage in variant.get("shortages", []):
    labels = {
        "skill_unconfirmed": "标签未确认", "primary_skill_mismatch": "主要子技能不同",
        "knowledge_point_mismatch": "知识点不同",
        "difficulty_out_of_band": "难度不在当前区间",
        "difficulty_at_or_above_8": "难度 8.0 以上", "difficulty_missing": "难度缺失",
        "original_question": "当前考试原题", "near_duplicate": "近重复题",
    }
    reasons = "、".join(f"{labels.get(key, key)} {value} 道" for key, value in shortage["reason_counts"].items())
    st.warning(f"{shortage['skill_name']} / {shortage['stage']} 还缺 {shortage['missing_count']} 道；{reasons}")
```

排除原因使用教师可读文案：标签未确认、主要子技能不同、难度不在当前区间、难度 8.0 以上、原题/近重复。候选不足时提供“前往确认小问标签”和“人工选题”，不显示允许相邻技能自动补入的按钮。

- [ ] **Step 5: 运行页面测试**

Run: `runtime\python\python.exe -m pytest tests/test_training_recommendation_ui.py tests/test_training_task_history_ui.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add pages/训练推荐.py tests/test_training_recommendation_ui.py tests/test_training_task_history_ui.py
git commit -m "feat: show adaptive primary skill training plans"
```

### Task 6: 固化训练任务快照和解释字段

**Files:**
- Modify: `question_bank/services/training_task_service.py`
- Modify: `tests/test_training_task_service.py`
- Modify: `tests/test_unified_skill_recommendation.py`

**Interfaces:**
- Persists: `target_skill_id/name`、`matched_part_refs`、`mastery_band`、`difficulty_range`、`difficulty`、`reason_counts`。

- [ ] **Step 1: 写快照失败测试**

```python
def test_training_snapshot_keeps_primary_skill_and_difficulty_policy(tmp_path: Path) -> None:
    task = TrainingTaskService(db_path).create_task(precise_plan(), created_by="teacher")
    loaded = TrainingTaskService(db_path).get_task(task.id)
    item = loaded["variants"][0]["items"][0]["recommendation_snapshot"]
    assert item["target_skill_id"] == 12
    assert item["matched_part_refs"] == ["part:2"]
    assert item["mastery_band"] == "developing"
    assert item["difficulty_range"] == [3.0, 5.9]
    assert float(item["difficulty"]) < 8.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_training_task_service.py tests/test_unified_skill_recommendation.py -q`

Expected: FAIL，快照缺少小问与难度策略字段。

- [ ] **Step 3: 扩展白名单和读取兼容**

`TrainingTaskService` 不重新计算快照，只原样保存上述字段；读取旧任务时缺失字段返回空值并标记 `legacy_snapshot=True`，不能用当前规则伪造历史推荐理由。

```python
PRECISE_RECOMMENDATION_FIELDS = (
    "target_skill_id", "target_skill_name", "matched_part_refs", "mastery_band",
    "difficulty_range", "difficulty", "selection_reason", "reason_counts",
)
```

- [ ] **Step 4: 运行快照与导出回归**

Run: `runtime\python\python.exe -m pytest tests/test_training_task_service.py tests/test_training_export_service.py tests/test_unified_skill_recommendation.py -q`

Expected: PASS；旧任务可读，新任务解释完整。

- [ ] **Step 5: 提交**

```powershell
git add question_bank/services/training_task_service.py tests/test_training_task_service.py tests/test_unified_skill_recommendation.py
git commit -m "feat: persist precise training recommendation trace"
```

### Task 7: 全套验证、浏览器验收和架构更新

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: 运行训练推荐全套测试**

Run: `runtime\python\python.exe -m pytest tests/test_training_difficulty_policy.py tests/test_precise_training_candidates.py tests/test_training_target_allocation.py tests/test_practice_candidate_scoring.py tests/test_practice_plan_service.py tests/test_practice_grouping.py tests/test_question_tag_recommendation.py tests/test_unified_skill_recommendation.py tests/test_training_recommendation_ui.py tests/test_training_task_service.py tests/test_training_export_service.py -q`

Expected: PASS。

- [ ] **Step 2: 扫描所有自动推荐入口的 8.0 防线**

Run: `rg -n "recommend_for_weak_point|PracticePlanService|practice_gradient_fit|parse_difficulty|difficulty" question_bank/recommendation pages/训练推荐.py`

Expected: 每个可达自动推荐入口最终调用 `is_automatic_difficulty_eligible()` 或先执行 `difficulty < 8.0` 硬过滤；没有 `8—10` 自动迁移区间。

- [ ] **Step 3: 用边界数据做服务级验收**

运行专用测试数据同时包含难度 `7.1`、`7.9`、`8.0`、`8.1`、`10.0`、空值和非法值；分别对掌握率 `39.9%`、`40.0%`、`70.0%`、`70.1%` 生成个人与自动分组计划。

Expected: `8.0+` 和非法值始终为零入选；边界档位与规格一致；候选不足如实返回。

- [ ] **Step 4: 浏览器验证训练页**

在 1366×768、1440×900、1920×1080 检查：主要子技能目标、自适应区间、推荐题小问与一位小数难度、候选不足原因、保存与历史任务。检查键盘焦点、长技能名换行和控制台。

Expected: 页面不再声称“只按 knowledge_point 精确匹配”；任何自动计划中不出现 8.0—10.0。

- [ ] **Step 5: 更新 ARCHITECTURE.md**

记录训练数据流：`assessment_item_skills confirmed primary → DiagnosisProfileService → TrainingTarget → question_part_skill_links confirmed primary → hard filters → rank → snapshot`，并注明旧粗标签路径只用于历史快照读取。

- [ ] **Step 6: 提交**

```powershell
git add ARCHITECTURE.md
git commit -m "docs: document precise adaptive training flow"
```
