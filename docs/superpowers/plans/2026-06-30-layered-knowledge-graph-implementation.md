# 分层小问知识图谱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将评分小问确认到唯一主要技能，并把全局图谱改为“章节/主题 → 知识点 → 主要子技能 → 评分小问”的可下钻界面且每条评分证据只计算一次。

**Architecture:** 扩展现有 `assessment_item_skills`，保留其统一技能解析语义并增加主要技能、题库小问和教师审核状态；不新建同义映射表。新增主要技能投影服务负责组合评分小问、题库小问、统一技能和支持标签，诊断服务按唯一确认主技能聚合，页面只消费分层视图模型。

**Tech Stack:** Python 3.11、SQLite、Streamlit、pytest；复用 `SkillLinkService`、`SkillResolutionService`、`SourceQuestionLinkService`、`DiagnosisProfileService` 和现有原卷高亮能力。

## Global Constraints

- 每个评分小问最多一个 `confirmed + primary` 技能；只有它贡献子技能掌握率。
- 方法、模型、能力、前置知识和其他 measured 技能只解释，不重复贡献分子或分母。
- AI/规则生成的是 `suggested`，教师确认后才进入细粒度图谱和训练推荐。
- 未确认或失效时保留现有知识点粗粒度图谱，并分别显示“知识点覆盖”和“小问精细度”。
- 全班、单生、总览和详情统一使用 `score_awarded / full_score` 的分值加权口径。
- 全班详情先按评分小问汇总，学生原卷记录分页下钻；不得用第一条学生记录作为全班标题。
- 支持标签分类展示，不能把数百条自由文本塞入 `title` 属性。
- 不修改批改分数、人工复核结论或报告导出条件。
- 本计划依赖 `2026-06-30-question-part-tagging-implementation.md` 已完成。

---

## File Structure

- `migrations/question_bank/010_add_assessment_primary_review.sql` — 扩展现有评分小问技能关联。
- `question_bank/database/schema.py` — 同步运行时兼容字段和索引。
- `question_bank/services/skill_link_service.py` — 查询/保存审核状态和唯一主要技能。
- `question_bank/services/assessment_skill_review_service.py` — 生成评分小问候选、匹配题库小问、确认/拒绝/失效。
- `question_bank/services/question_part_service.py` — 在题库小问主要技能变化后暴露一致性版本信息。
- `integration/primary_skill_projection_service.py` — 只读投影评分小问的唯一主技能及分层上下文。
- `integration/diagnosis_profile_service.py` — 按唯一主技能聚合得分证据与两套覆盖率。
- `integration/skill_graph_projection.py` — 生成分层页面视图模型。
- `pages_shared/assessment_skill_review_component.py` — 教师确认工作台。
- `pages_shared/layered_knowledge_graph_component.py` — 分层总览和小问汇总组件。
- `web_app.py` — 接入组件、技能详情查询参数与分页证据。
- `tests/test_assessment_primary_skill_schema.py`、`tests/test_assessment_skill_review.py`、`tests/test_primary_skill_projection.py`、`tests/test_diagnosis_profile_service.py`、`tests/test_skill_graph_projection.py`、`tests/test_question_tag_graph_ui_contract.py` — 迁移、审核、统计和 UI 契约。

### Task 1: 扩展现有评分小问技能关联

**Files:**
- Create: `migrations/question_bank/010_add_assessment_primary_review.sql`
- Modify: `question_bank/database/schema.py:450-490`
- Create: `tests/test_assessment_primary_skill_schema.py`

**Interfaces:**
- Produces columns: `question_part_id`、`is_primary`、`review_status`、`reviewed_by`、`reviewed_at`。
- Preserves: `status = resolved/conflict` 和现有唯一键。

- [ ] **Step 1: 写迁移失败测试**

```python
def test_assessment_skill_schema_has_primary_review_fields(tmp_path: Path) -> None:
    db = tmp_path / "question_bank.db"
    initialize_database(db)
    with connect(db) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(assessment_item_skills)")}
        assert {"question_part_id", "is_primary", "review_status", "reviewed_by", "reviewed_at"} <= columns

def test_only_one_confirmed_primary_per_assessment_item(tmp_path: Path) -> None:
    db = tmp_path / "question_bank.db"
    initialize_database(db)
    with connect(db) as conn:
        skill_ids = [int(row[0]) for row in conn.execute("SELECT id FROM skills ORDER BY id LIMIT 2")]
        sql = """INSERT INTO assessment_item_skills(
            grading_session_id, source_question_id, skill_id, role, status,
            is_primary, review_status
        ) VALUES (?, ?, ?, 'measured', 'resolved', 1, 'confirmed')"""
        conn.execute(sql, ("1", "Q11(1)", skill_ids[0]))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, ("1", "Q11(1)", skill_ids[1]))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_primary_skill_schema.py -q`

Expected: FAIL，字段不存在。

- [ ] **Step 3: 添加增量字段与部分唯一索引**

```sql
ALTER TABLE assessment_item_skills ADD COLUMN question_part_id INTEGER;
ALTER TABLE assessment_item_skills ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1));
ALTER TABLE assessment_item_skills ADD COLUMN review_status TEXT NOT NULL DEFAULT 'suggested'
    CHECK (review_status IN ('suggested', 'confirmed', 'rejected', 'stale'));
ALTER TABLE assessment_item_skills ADD COLUMN reviewed_by TEXT;
ALTER TABLE assessment_item_skills ADD COLUMN reviewed_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_confirmed_primary
ON assessment_item_skills(grading_session_id, source_question_id)
WHERE status = 'resolved' AND review_status = 'confirmed' AND is_primary = 1;

CREATE INDEX IF NOT EXISTS idx_assessment_review_queue
ON assessment_item_skills(grading_session_id, review_status, source_question_id);
```

`schema.py` 通过 `_ensure_columns()` 添加同名列并创建索引；不重建已有表，不回写已有行。既有行默认 `suggested`，避免未经本功能确认就进入新图谱。

- [ ] **Step 4: 运行 Schema 回归**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_primary_skill_schema.py tests/test_skill_catalog_schema.py tests/test_assessment_skill_intake.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add migrations/question_bank/010_add_assessment_primary_review.sql question_bank/database/schema.py tests/test_assessment_primary_skill_schema.py
git commit -m "feat: add primary review state to assessment skills"
```

### Task 2: 生成并确认评分小问主要技能候选

**Files:**
- Modify: `question_bank/services/skill_link_service.py:13-245`
- Create: `question_bank/services/assessment_skill_review_service.py`
- Create: `tests/test_assessment_skill_review.py`
- Modify: `tests/test_assessment_skill_intake.py`

**Interfaces:**
- Produces: `AssessmentSkillReviewService.generate_candidates(session_id, rubric) -> ReviewSummary`。
- Produces: `confirm_primary(session_id, item_ref, link_id, reviewer) -> None`。
- Produces: `reject_item(session_id, item_ref, reviewer) -> None`。
- Produces: `review_rows(session_ids) -> list[AssessmentSkillReviewRow]`。

- [ ] **Step 1: 写候选与确认失败测试**

```python
def test_generate_candidates_uses_part_ref_and_does_not_auto_confirm(tmp_path: Path) -> None:
    service, rubric = seeded_review_service(tmp_path)
    summary = service.generate_candidates("1", rubric)
    rows = service.review_rows(["1"])
    assert summary.items == 2
    assert {row.item_ref for row in rows} == {"Q11(1)", "Q11(2)"}
    assert {row.review_status for row in rows} == {"suggested"}
    assert all(not row.is_primary for row in rows)
    assert rows[0].question_part_id is not None

def test_confirm_primary_is_atomic_and_unique(tmp_path: Path) -> None:
    service, rubric = seeded_review_service(tmp_path)
    service.generate_candidates("1", rubric)
    candidates = [row for row in service.review_rows(["1"]) if row.item_ref == "Q11(1)"]
    service.confirm_primary("1", "Q11(1)", candidates[0].link_id, reviewer="teacher")
    confirmed = service.confirmed_primary_links(["1"])
    assert [(row["source_question_id"], row["skill_id"]) for row in confirmed] == [("Q11(1)", candidates[0].skill_id)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review.py tests/test_assessment_skill_intake.py -q`

Expected: FAIL，审核服务和字段读取不存在。

- [ ] **Step 3: 扩展 SkillLinkService 的显式审核接口**

```python
def confirmed_primary_assessment_links(self, session_ids: Sequence[str]) -> list[dict[str, Any]]:
    return self._assessment_links(
        session_ids,
        where="status = 'resolved' AND review_status = 'confirmed' AND is_primary = 1",
    )

def set_primary_review(
    self, grading_session_id: str, source_question_id: str, link_id: int, *, reviewer: str
) -> None:
    with connect(self.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM assessment_item_skills WHERE id=? AND grading_session_id=? AND source_question_id=? AND status='resolved'",
            (link_id, grading_session_id, source_question_id),
        ).fetchone()
        if row is None:
            raise KeyError("assessment skill candidate not found")
        conn.execute(
            "UPDATE assessment_item_skills SET is_primary=0, review_status='confirmed', reviewed_by=?, reviewed_at=datetime('now','localtime') WHERE grading_session_id=? AND source_question_id=? AND status='resolved'",
            (reviewer, grading_session_id, source_question_id),
        )
        conn.execute(
            "UPDATE assessment_item_skills SET is_primary=1 WHERE id=?",
            (link_id,),
        )
```

旧 `assessment_links_for_sessions()` 继续返回所有 resolved 行，防止破坏管理页；图谱与推荐必须改用新方法。

- [ ] **Step 4: 实现候选生成和题库小问匹配**

```python
def _part_number(value: str) -> int | None:
    match = re.search(r"[（(._-](\d+)[）)]?$", str(value or "").strip())
    return int(match.group(1)) if match else None

def match_question_part(item_ref: str, parts: Sequence[Mapping[str, Any]]) -> int | None:
    if len(parts) == 1 and str(parts[0]["part_ref"]) == "main":
        return int(parts[0]["id"])
    number = _part_number(item_ref)
    matches = [int(part["id"]) for part in parts if _part_number(str(part["part_ref"])) == number]
    return matches[0] if len(matches) == 1 else None
```

`generate_candidates()` 使用 `iter_effective_rubric_items()` 和现有 `SkillResolutionService`；解析成功的 measured/supporting 关联写 `suggested`，第一条 measured 只作为界面默认选择，不置 `is_primary=1`。无法唯一匹配题库小问或技能冲突时写现有冲突收件箱，并在 `ReviewSummary.unresolved` 中报告。

- [ ] **Step 5: 运行服务测试**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review.py tests/test_assessment_skill_intake.py -q`

Expected: PASS；SkillLinkService 的新查询与确认断言保留在这两份现有/新增测试中。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/services/skill_link_service.py question_bank/services/assessment_skill_review_service.py tests/test_assessment_skill_review.py tests/test_assessment_skill_intake.py
git commit -m "feat: review primary skill for assessment parts"
```

### Task 3: 让已确认关联在来源变化后显式失效

**Files:**
- Modify: `question_bank/services/assessment_skill_review_service.py`
- Modify: `question_bank/services/question_part_service.py`
- Modify: `tests/test_assessment_skill_review.py`

**Interfaces:**
- Produces: `AssessmentSkillReviewService.reconcile_session(session_id, rubric) -> StaleSummary`。
- Rule: 来源题关联、小问引用、题库小问或主要技能变化后把 `review_status` 改为 `stale`，不自动跟随新值。

- [ ] **Step 1: 写失效条件测试**

```python
@pytest.mark.parametrize("change", ["source_link", "rubric_item_removed", "question_part_removed", "primary_skill_changed"])
def test_confirmed_assessment_primary_becomes_stale_after_source_change(tmp_path: Path, change: str) -> None:
    service, rubric = confirmed_review_system(tmp_path)
    apply_change(service.db_path, rubric, change)
    summary = service.reconcile_session("1", rubric)
    assert summary.stale_count == 1
    assert service.confirmed_primary_links(["1"]) == []
    assert service.review_rows(["1"])[0].review_status == "stale"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review.py -q`

Expected: FAIL，当前关联不会检查来源版本。

- [ ] **Step 3: 保存确认时的一致性证据并实现 reconcile**

```python
@dataclass(frozen=True, slots=True)
class StaleSummary:
    checked_count: int
    stale_count: int
    reasons: Mapping[str, int]

def reconcile_session(self, session_id: str, rubric: Mapping[str, Any]) -> StaleSummary:
    effective_refs = {item_ref for item_ref, _question, _item in iter_effective_rubric_items(rubric)}
    rows = self.review_rows([session_id])
    stale: dict[int, str] = {}
    for row in rows:
        reason = self._stale_reason(row, effective_refs)
        if reason:
            stale[row.link_id] = reason
    with connect(self.db_path) as conn:
        conn.executemany(
            "UPDATE assessment_item_skills SET review_status='stale', evidence_json=json_set(evidence_json, '$.stale_reason', ?), updated_at=datetime('now','localtime') WHERE id=?",
            [(reason, link_id) for link_id, reason in stale.items()],
        )
    return StaleSummary(len(rows), len(stale), dict(Counter(stale.values())))
```

确认时的 `evidence_json` 保存 `bank_question_id`、`question_part_id`、`part_ref`、`skill_id` 和题库小问 `updated_at`。`_stale_reason()` 逐项精确比对，不用名称模糊匹配。

- [ ] **Step 4: 在候选页和图谱查询前调用 reconcile**

候选工作台打开和 `PrimarySkillProjectionService.project_sessions()` 读取每个会话前调用一次；失效行保留审计并显示原因，不参与图谱和推荐。

- [ ] **Step 5: 运行失效测试并提交**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review.py tests/test_question_part_tagging.py -q`

Expected: PASS。

```powershell
git add question_bank/services/assessment_skill_review_service.py question_bank/services/question_part_service.py tests/test_assessment_skill_review.py tests/test_question_part_tagging.py
git commit -m "feat: invalidate stale primary skill reviews"
```

### Task 4: 构建唯一主要技能投影

**Files:**
- Create: `integration/primary_skill_projection_service.py`
- Create: `tests/test_primary_skill_projection.py`
- Modify: `integration/grading_paper_skill_workflow_service.py:250-360`

**Interfaces:**
- Produces: `PrimarySkillProjectionService.project_sessions(session_ids) -> PrimarySkillProjection`。
- Produces per item: stable skill, topic, chapter, knowledge point, support tags, bank question/part IDs, status。

- [ ] **Step 1: 写投影失败测试**

```python
def test_projection_returns_one_confirmed_primary_context_per_item(tmp_path: Path) -> None:
    db = seeded_confirmed_assessment_part(tmp_path)
    projection = PrimarySkillProjectionService(db).project_sessions(["1"])
    item = projection.by_item()[("1", "Q11(2)")]
    assert item.skill_name == "读取一次函数图像"
    assert item.topic_name == "一次函数"
    assert item.knowledge_point == "函数图像"
    assert item.chapter == "八年级上册 第四章 一次函数"
    assert item.support_tags["method"] == ("数形结合",)

def test_projection_excludes_suggested_and_stale_primary_links(tmp_path: Path) -> None:
    projection = PrimarySkillProjectionService(seeded_unconfirmed_links(tmp_path)).project_sessions(["1"])
    assert projection.confirmed_items == 0
    assert projection.pending_items == {("1", "Q11(1)"), ("1", "Q11(2)")}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_primary_skill_projection.py -q`

Expected: FAIL，投影服务不存在。

- [ ] **Step 3: 实现一次查询的只读投影**

```python
@dataclass(frozen=True, slots=True)
class PrimarySkillContext:
    grading_session_id: str
    item_ref: str
    skill_id: int
    skill_name: str
    topic_name: str
    bank_question_id: int | None
    question_part_id: int | None
    chapter: str
    knowledge_point: str
    support_tags: Mapping[str, tuple[str, ...]]

@dataclass(frozen=True, slots=True)
class PrimarySkillProjection:
    items: tuple[PrimarySkillContext, ...]
    total_items: int
    knowledge_covered_items: int
    pending_items: frozenset[tuple[str, str]]
```

SQL 从 `assessment_item_skills` 过滤唯一确认主技能，连接 `skills/skill_topics/question_parts/questions`；小问标签单独批量查询后按 `question_part_id` 合并。`knowledge_point` 取已确认小问 `knowledge_point` 的第一项，`chapter` 取 `exam_scope` 第一项；缺失时回退 topic 名但明确 `context_fallback=True`。

- [ ] **Step 4: 将题库流程完整度增加小问精细度**

`GradingPaperWorkflowStatus` 新增 `fine_confirmed_items`、`fine_total_items`，但不把未确认小问误计入现有题库关联完成度。状态卡显示两行覆盖率。

- [ ] **Step 5: 运行投影和流程测试**

Run: `runtime\python\python.exe -m pytest tests/test_primary_skill_projection.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add integration/primary_skill_projection_service.py integration/grading_paper_skill_workflow_service.py tests/test_primary_skill_projection.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py
git commit -m "feat: project confirmed primary assessment skills"
```

### Task 5: 按唯一主要技能聚合诊断

**Files:**
- Modify: `integration/diagnosis_profile_service.py:54-62,250-340,520-670`
- Modify: `tests/test_diagnosis_profile_service.py:200-290`

**Interfaces:**
- Produces: `build_skill_profiles()` 只读确认主技能。
- Produces coverage: `knowledge_covered_items`、`fine_confirmed_items`、`fine_total_items`、`pending_items`。
- Produces weak point: `score_sum`、`full_score_sum`、`deduction_score`、`assessment_items`、分层上下文。

- [ ] **Step 1: 写不重复计分和覆盖率失败测试**

```python
def test_skill_profile_counts_each_assessment_record_once(tmp_path: Path) -> None:
    service = seeded_diagnosis_with_primary_and_supporting_skills(tmp_path)
    profile = service.build_skill_profiles(scope=scope(), exam_scope=exam_scope())
    weak = profile["students"][0]["weak_points"]
    assert len(weak) == 1
    assert weak[0]["score_sum"] == 2.0
    assert weak[0]["full_score_sum"] == 4.0
    assert weak[0]["mastery"] == 0.5
    assert weak[0]["deduction_score"] == 2.0
    assert profile["coverage"] == {
        "knowledge_covered_items": 2,
        "fine_confirmed_items": 1,
        "fine_total_items": 2,
        "pending_items": ["Q11(2)"],
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_diagnosis_profile_service.py -q`

Expected: FAIL，当前会读取所有 measured 技能且没有两套覆盖率。

- [ ] **Step 3: 只读取主技能投影并补齐分层上下文**

```python
projection = PrimarySkillProjectionService(self.question_bank_db_path).project_sessions(
    [str(value) for value in session_ids]
)
primary_by_item = projection.by_item()
for row in evidence_rows:
    context = primary_by_item.get((str(row["session_id"]), str(row["question_id"])))
    if context is None:
        continue
    # 仅在这里累加一次 score/full_score；support_tags 不进入循环。
```

聚合键使用 `skill_id`；`assessment_items` 再按 `session_id + question_id` 汇总全班人数、失分人数、得分分值、满分分值和扣分分值。`eligible_for_recommendation=True` 仅赋给已确认主技能。

- [ ] **Step 4: 修正 skill_evidence 只读取主技能**

```python
source_keys = {
    (int(link["grading_session_id"]), str(link["source_question_id"]))
    for link in self.skill_links.confirmed_primary_assessment_links([str(v) for v in selected_sessions])
    if int(link["skill_id"]) == normalized_skill_id
}
```

- [ ] **Step 5: 运行诊断测试**

Run: `runtime\python\python.exe -m pytest tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py -q`

Expected: PASS；支持技能不再复制分值。

- [ ] **Step 6: 提交**

```powershell
git add integration/diagnosis_profile_service.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py
git commit -m "feat: aggregate diagnosis by one primary skill"
```

### Task 6: 建立分层图谱视图模型

**Files:**
- Modify: `integration/skill_graph_projection.py:1-147`
- Modify: `tests/test_skill_graph_projection.py`

**Interfaces:**
- Produces: `build_layered_skill_graph(profile, aggregate) -> LayeredSkillGraph`。
- Hierarchy: chapter → knowledge point → primary skill → assessment item。

- [ ] **Step 1: 写层级和排序失败测试**

```python
def test_layered_graph_groups_and_orders_riskiest_nodes() -> None:
    graph = build_layered_skill_graph(profile_fixture(), aggregate=True)
    chapter = graph.chapters[0]
    assert chapter.name == "八年级上册 第四章 一次函数"
    assert chapter.knowledge_points[0].name == "函数图像"
    skill = chapter.knowledge_points[0].skills[0]
    assert skill.skill_key == "skill:12"
    assert skill.weighted_score_rate == 41.0
    assert [item.item_ref for item in skill.assessment_items] == ["Q11(2)", "Q11(3)"]

def test_support_tags_do_not_create_sibling_mastery_nodes() -> None:
    graph = build_layered_skill_graph(profile_fixture(), aggregate=False)
    assert graph.skill_keys() == {"skill:12", "skill:17"}
    assert "数形结合" not in graph.skill_keys()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_skill_graph_projection.py -q`

Expected: FAIL，当前只输出扁平行。

- [ ] **Step 3: 实现不可变分层模型**

```python
@dataclass(frozen=True, slots=True)
class AssessmentItemNode:
    session_id: int
    item_ref: str
    weighted_score_rate: float
    deducted_students: int
    evidence_count: int

@dataclass(frozen=True, slots=True)
class SkillNode:
    skill_id: int
    skill_key: str
    name: str
    weighted_score_rate: float
    deduction_count: int
    evidence_count: int
    support_tags: Mapping[str, tuple[str, ...]]
    assessment_items: tuple[AssessmentItemNode, ...]
```

知识点和章节节点用同样的 `score_sum/full_score_sum` 直接从唯一证据聚合，不通过对子节点百分比求平均。排序依次为得分率升序、扣分分值降序、名称。

- [ ] **Step 4: 运行投影测试**

Run: `runtime\python\python.exe -m pytest tests/test_skill_graph_projection.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add integration/skill_graph_projection.py tests/test_skill_graph_projection.py
git commit -m "feat: build layered primary skill graph model"
```

### Task 7: 增加评分小问标签确认工作台

**Files:**
- Create: `pages_shared/assessment_skill_review_component.py`
- Modify: `pages_shared/grading_paper_skill_workflow_component.py:35-135`
- Modify: `web_app.py:4060-4168`
- Create: `tests/test_assessment_skill_review_ui.py`

**Interfaces:**
- Produces: `render_assessment_skill_review(service, session_ids, key_prefix)`。

- [ ] **Step 1: 写 UI 契约失败测试**

```python
def test_graph_page_exposes_part_skill_review_workbench() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")
    component = Path("pages_shared/assessment_skill_review_component.py").read_text(encoding="utf-8")
    assert "render_assessment_skill_review" in source
    assert "建议主要子技能" in component
    assert "接受" in component and "修改" in component and "拒绝" in component
    assert "全部接受" in component and "unresolved" in component
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review_ui.py -q`

Expected: FAIL，组件不存在。

- [ ] **Step 3: 实现父题分组确认组件**

```python
def render_assessment_skill_review(service, session_ids: Sequence[int], *, key_prefix: str) -> None:
    rows = service.review_rows([str(value) for value in session_ids])
    for parent_ref, item_rows in groupby_parent(rows):
        with st.expander(f"{parent_ref} · {confirmed_count(item_rows)}/{len(item_rows)} 已确认"):
            for item in item_rows:
                st.markdown(f"**{item.item_ref}**　{item.core_goal}")
                choice = st.selectbox("建议主要子技能", item.candidate_options, key=f"{key_prefix}_{item.link_id}")
                cols = st.columns(3)
                if cols[0].button("接受", key=f"{key_prefix}_accept_{item.link_id}"):
                    service.confirm_primary(item.session_id, item.item_ref, choice, reviewer="teacher")
                if cols[2].button("拒绝", key=f"{key_prefix}_reject_{item.item_ref}"):
                    service.reject_item(item.session_id, item.item_ref, reviewer="teacher")
```

“全部接受”只在每个小问都有唯一题库小问、至少一个 resolved measured 候选且无 `unresolved` 时启用。

- [ ] **Step 4: 运行 UI 契约**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_skill_review_ui.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add pages_shared/assessment_skill_review_component.py pages_shared/grading_paper_skill_workflow_component.py web_app.py tests/test_assessment_skill_review_ui.py tests/test_grading_paper_skill_workflow_ui.py
git commit -m "feat: review assessment skills by part"
```

### Task 8: 替换全局图谱为分层界面和小问汇总详情

**Files:**
- Create: `pages_shared/layered_knowledge_graph_component.py`
- Modify: `web_app.py:4060-4770`
- Modify: `tests/test_question_tag_graph_ui_contract.py`
- Create: `tests/test_layered_knowledge_graph_ui.py`

**Interfaces:**
- Produces: `render_layered_knowledge_graph(graph, session_ids, selected_student_id)`。
- Query key: `kg_skill_id=<positive int>`，不再把粗 `knowledge_point:` 作为细粒度详情身份。

- [ ] **Step 1: 写新图谱和详情契约失败测试**

```python
def test_active_graph_uses_confirmed_skill_profiles_and_layered_component() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")
    assert ".build_skill_profiles(" in source
    assert "build_layered_skill_graph" in source
    assert "render_layered_knowledge_graph" in source
    assert ".skill_evidence(" in source
    assert ".build_tag_profiles(" not in graph_function(source)

def test_detail_is_item_summary_before_student_records() -> None:
    component = Path("pages_shared/layered_knowledge_graph_component.py").read_text(encoding="utf-8")
    assert "评分小问汇总" in component
    assert "失分人数" in component
    assert "查看学生证据" in component
    assert "失分题次" in component
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_tag_graph_ui_contract.py tests/test_layered_knowledge_graph_ui.py -q`

Expected: FAIL，页面仍读取父题 knowledge_point。

- [ ] **Step 3: 实现可折叠分层组件**

```python
def render_layered_knowledge_graph(graph: LayeredSkillGraph, *, session_ids: Sequence[int]) -> None:
    for chapter_index, chapter in enumerate(graph.chapters):
        with st.expander(chapter.title, expanded=chapter_index < 3):
            for knowledge in chapter.knowledge_points:
                st.markdown(f"#### {knowledge.name}　{knowledge.weighted_score_rate:.1f}%")
                for skill in knowledge.skills:
                    st.link_button(
                        f"{skill.name} · {skill.weighted_score_rate:.1f}% · 失分题次 {skill.deduction_count}/{skill.evidence_count}",
                        build_skill_detail_url(skill.skill_id, session_ids),
                        use_container_width=True,
                    )
```

不再截断为 14 个节点；每层先显示风险最高项和“显示更多”。状态用“已确认/待确认/粗粒度”文字与图标，不只用颜色。

- [ ] **Step 4: 重写详情为聚合优先**

```python
items = diagnosis_service.skill_evidence(skill_id=skill_id, student_ids=selected_students, session_ids=session_ids)
summary = aggregate_skill_evidence_by_assessment_item(items)
st.dataframe(summary, column_config={
    "item_ref": "评分小问", "score_rate": "得分率",
    "deducted_students": "失分人数", "deduction_score": "扣分分值",
})
selected_item = st.selectbox("查看学生证据", [row["item_ref"] for row in summary])
render_paginated_student_evidence(items, selected_item=selected_item, page_size=20)
```

全班标题固定为“筛选学生合计 · 技能名”，单生标题才显示姓名；错因按规范化类别前五项聚合，自由文本只在证据下钻显示。

- [ ] **Step 5: 运行图谱测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_tag_graph_ui_contract.py tests/test_layered_knowledge_graph_ui.py tests/test_grading_tag_context_and_errors.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add pages_shared/layered_knowledge_graph_component.py web_app.py tests/test_question_tag_graph_ui_contract.py tests/test_layered_knowledge_graph_ui.py tests/test_grading_tag_context_and_errors.py
git commit -m "feat: render layered question-level knowledge graph"
```

### Task 9: 完整回归、浏览器验收和架构更新

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: 运行图谱全套测试**

Run: `runtime\python\python.exe -m pytest tests/test_assessment_primary_skill_schema.py tests/test_assessment_skill_review.py tests/test_primary_skill_projection.py tests/test_diagnosis_profile_service.py tests/test_skill_graph_projection.py tests/test_question_tag_projection_service.py tests/test_question_tag_diagnosis.py tests/test_question_tag_counts.py tests/test_question_tag_graph_ui_contract.py tests/test_layered_knowledge_graph_ui.py -q`

Expected: PASS。

- [ ] **Step 2: 运行批改与报告回归**

Run: `runtime\python\python.exe -m pytest tests/test_grading_completeness.py tests/test_grading_tag_context_and_errors.py tests/test_report_completeness.py tests/test_report_score_adjustment.py -q`

Expected: PASS，评分与报告输出未改变。

- [ ] **Step 3: 浏览器验证真实页面**

验证当前 Q11：三个评分小问可分别确认主要技能；总览按章节/知识点/技能分层；每条小问只在一个技能节点计分；全班详情先显示小问汇总；学生证据分页；待确认时粗粒度图仍可用。检查 1366×768、1440×900、1920×1080、键盘焦点和浏览器控制台。

Expected: 无横向溢出或控制台错误；全班详情不显示任意第一位学生姓名；文案使用“失分题次”。

- [ ] **Step 4: 更新 ARCHITECTURE.md**

记录 `assessment_item_skills` 审核字段、主要技能投影、分层视图模型、粗粒度降级和跨库无事务边界。

- [ ] **Step 5: 提交**

```powershell
git add ARCHITECTURE.md
git commit -m "docs: document layered primary skill graph"
```
