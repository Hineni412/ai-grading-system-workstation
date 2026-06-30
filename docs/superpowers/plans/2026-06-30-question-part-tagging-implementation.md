# 小问级题库打标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让题库 AI 按稳定小问输出、校验和保存标签及 0.1 精度难度，并让父题标签成为已确认小问标签的去重并集。

**Architecture:** 新增题库小问、普通标签和稳定技能关联三张表；保留现有 `TagAnalysis` 作为单个小问的标签值对象，新增题目级 `QuestionTagAnalysis` 包装多个小问。AI 结果先保存为候选，教师确认后由题库小问服务在同一事务中解析稳定技能、刷新父题兼容标签和父题最高难度。

**Tech Stack:** Python 3.11、SQLite、Streamlit、pytest；复用现有 `SkillResolutionService`、`skills` 统一技能目录和题库迁移工具，不新增第三方依赖。

## Global Constraints

- 难度范围为 `1.0—10.0`，只接受 `0.1` 步长；`7.1` 与 `7.9` 必须端到端可区分。
- 难度非法、缺失、非有限数或精度超过一位小数时拒绝整题结果，不截断、不夹紧。
- AI 必须返回输入中的全部小问，不能新增、遗漏、合并、重复或重排 `part_ref`。
- 父题标签只取已确认小问标签的稳定去重并集；父题难度取全部有效小问难度最大值。
- 未经教师确认的小问候选不能进入父题正式标签、知识图谱或自动训练推荐。
- 保留现有单问题和历史父题标签读取兼容，不修改评分结果或报告数据。
- `migrations/question_bank/` 是 Schema 权威来源；`question_bank/database/schema.py` 仅同步运行时兼容定义。
- 不新增外部依赖，不覆盖工作区现有数据库或用户数据。

---

## File Structure

- `migrations/question_bank/009_add_question_parts.sql` — 可部署的小问、标签和小问技能关联迁移。
- `question_bank/database/schema.py` — 测试库和旧启动路径的兼容建表定义。
- `question_bank/models/tag_schema.py` — 严格的一位小数难度解析。
- `question_bank/models/question_part.py` — 小问输入、单小问分析和题目级分析值对象。
- `question_bank/services/question_part_parser.py` — 只负责从题干/答案提取稳定小问边界。
- `question_bank/services/question_part_service.py` — 候选保存、教师确认、稳定技能解析和父题汇总事务。
- `question_bank/services/ai_tagging_service.py` — 逐小问提示词、JSON Schema、批量结果校验。
- `question_bank/services/question_service.py` — 兼容入口委托给小问服务并读取父题汇总。
- `pages_shared/question_part_tagging_component.py` — 题库小问候选确认与展开显示组件。
- `pages/题库管理.py` — 接入共享组件，不继续复制小问编辑逻辑。
- `update_tools/backfill_question_parts.py` — 历史题 dry-run/apply 候选生成入口。
- `tests/test_question_part_schema.py`、`tests/test_question_part_parser.py`、`tests/test_question_part_tagging.py`、`tests/test_question_bank_ai_tagging_quality.py`、`tests/test_question_bank_ai_tagging_ui.py` — 数据、领域、AI、事务和界面契约。

### Task 1: 建立小问数据模型和迁移

**Files:**
- Create: `migrations/question_bank/009_add_question_parts.sql`
- Modify: `question_bank/database/schema.py:20-230`
- Create: `tests/test_question_part_schema.py`

**Interfaces:**
- Produces: `question_parts`、`question_part_tags`、`question_part_skill_links`。
- Produces: `difficulty_tenths` 整数存储，`71` 对应 `7.1`。
- Consumes: 现有 `questions(id)` 与 `skills(id)`。

- [ ] **Step 1: 写迁移失败测试**

```python
def test_question_part_schema_enforces_precision_primary_and_status(tmp_path: Path) -> None:
    db = tmp_path / "question_bank.db"
    initialize_database(db)
    with connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"question_parts", "question_part_tags", "question_part_skill_links"} <= tables
        checks = conn.execute("SELECT sql FROM sqlite_master WHERE name='question_parts'").fetchone()[0]
        assert "difficulty_tenths BETWEEN 10 AND 100" in checks
```

```python
def test_only_one_confirmed_primary_skill_per_question_part(tmp_path: Path) -> None:
    db = tmp_path / "question_bank.db"
    initialize_database(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO questions(id, question_number, question_text) VALUES (1, '1', '测试题')")
        skill_ids = [int(row[0]) for row in conn.execute("SELECT id FROM skills ORDER BY id LIMIT 2")]
        part_id = conn.execute(
            "INSERT INTO question_parts(question_id, part_ref, part_order, part_text, difficulty_tenths) VALUES (?, 'part:1', 1, '求值', 71) RETURNING id",
            (1,),
        ).fetchone()[0]
        conn.execute("INSERT INTO question_part_skill_links(question_part_id, skill_id, role, is_primary, review_status) VALUES (?, ?, 'measured', 1, 'confirmed')", (part_id, skill_ids[0]))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO question_part_skill_links(question_part_id, skill_id, role, is_primary, review_status) VALUES (?, ?, 'measured', 1, 'confirmed')", (part_id, skill_ids[1]))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_schema.py -q`

Expected: FAIL，提示三张表不存在。

- [ ] **Step 3: 添加权威 SQL 迁移和运行时镜像**

```sql
CREATE TABLE IF NOT EXISTS question_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    part_ref TEXT NOT NULL,
    part_order INTEGER NOT NULL CHECK (part_order >= 1),
    shared_stem TEXT NOT NULL DEFAULT '',
    part_text TEXT NOT NULL DEFAULT '',
    answer_text TEXT,
    difficulty_tenths INTEGER CHECK (difficulty_tenths BETWEEN 10 AND 100),
    review_status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (review_status IN ('suggested', 'confirmed', 'rejected', 'stale')),
    source TEXT NOT NULL DEFAULT 'ai_tagging',
    confidence REAL CHECK (confidence BETWEEN 0.0 AND 1.0),
    model_name TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(question_id, part_ref),
    UNIQUE(question_id, part_order),
    FOREIGN KEY(question_id) REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS question_part_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_part_id INTEGER NOT NULL,
    tag_type TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'supporting' CHECK (role IN ('primary', 'supporting')),
    review_status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (review_status IN ('suggested', 'confirmed', 'rejected', 'stale')),
    source TEXT NOT NULL DEFAULT 'ai_tagging',
    confidence REAL CHECK (confidence BETWEEN 0.0 AND 1.0),
    model_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(question_part_id, tag_type, tag_value, role),
    FOREIGN KEY(question_part_id) REFERENCES question_parts(id)
);

CREATE TABLE IF NOT EXISTS question_part_skill_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_part_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('measured', 'supporting')),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    review_status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (review_status IN ('suggested', 'confirmed', 'rejected', 'stale')),
    source TEXT NOT NULL DEFAULT 'question_tagging',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (is_primary = 0 OR role = 'measured'),
    UNIQUE(question_part_id, skill_id, role),
    FOREIGN KEY(question_part_id) REFERENCES question_parts(id),
    FOREIGN KEY(skill_id) REFERENCES skills(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_question_part_confirmed_primary
ON question_part_skill_links(question_part_id)
WHERE is_primary = 1 AND review_status = 'confirmed';
```

将同一 DDL 放入 `schema.py` 的 `_create_knowledge_practice_tables()`，保持测试库结构一致。

- [ ] **Step 4: 运行 Schema 测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_schema.py tests/test_skill_catalog_schema.py tests/test_knowledge_practice_schema.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add migrations/question_bank/009_add_question_parts.sql question_bank/database/schema.py tests/test_question_part_schema.py
git commit -m "feat: add question part tagging schema"
```

### Task 2: 改为严格 0.1 难度并定义题目级分析对象

**Files:**
- Modify: `question_bank/models/tag_schema.py:92-140,222-228`
- Create: `question_bank/models/question_part.py`
- Modify: `tests/test_question_bank_ai_tagging_quality.py:12-60`
- Create: `tests/test_question_part_tagging.py`

**Interfaces:**
- Produces: `normalize_difficulty(value: object) -> float`。
- Produces: `QuestionPartInput`、`PartTagAnalysis`、`QuestionTagAnalysis`。
- Produces: `QuestionTagAnalysis.aggregate() -> TagAnalysis`。

- [ ] **Step 1: 写精度和完整性失败测试**

```python
@pytest.mark.parametrize("raw, expected", [(7.1, 7.1), ("7.9", 7.9), (8, 8.0)])
def test_tag_analysis_preserves_tenth_precision(raw: object, expected: float) -> None:
    assert _analysis(difficulty=raw).difficulty == expected

@pytest.mark.parametrize("raw", [None, "", 7.11, 0.9, 10.1, float("nan"), float("inf")])
def test_tag_analysis_rejects_invalid_difficulty(raw: object) -> None:
    with pytest.raises(ValueError, match="difficulty"):
        _analysis(difficulty=raw)

def test_question_analysis_rejects_missing_or_unknown_parts() -> None:
    expected = ("part:1", "part:2")
    payload = {"parts": [{"part_ref": "part:1", **_analysis(difficulty=4.2).to_dict()}]}
    with pytest.raises(ValueError, match="missing part_ref"):
        QuestionTagAnalysis.from_dict(payload, expected_part_refs=expected)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_quality.py tests/test_question_part_tagging.py -q`

Expected: FAIL，当前 `difficulty` 被 `int()` 截断且没有题目级 parts 类型。

- [ ] **Step 3: 实现严格难度解析**

```python
from decimal import Decimal, InvalidOperation
import math

def normalize_difficulty(value: object) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError("difficulty must be a number from 1.0 to 10.0")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("difficulty must be a number from 1.0 to 10.0") from None
    if not decimal_value.is_finite() or not Decimal("1.0") <= decimal_value <= Decimal("10.0"):
        raise ValueError("difficulty must be a finite number from 1.0 to 10.0")
    if decimal_value != decimal_value.quantize(Decimal("0.1")):
        raise ValueError("difficulty must use 0.1 increments")
    result = float(decimal_value)
    if not math.isfinite(result):
        raise ValueError("difficulty must be finite")
    return result
```

将 `TagAnalysis.difficulty` 改为 `float`，`from_dict()` 直接调用 `normalize_difficulty()`；删除旧 `_normalize_score()` 的默认值和夹紧行为。

- [ ] **Step 4: 实现题目级对象**

```python
@dataclass(frozen=True, slots=True)
class QuestionPartInput:
    part_ref: str
    part_order: int
    shared_stem: str
    part_text: str
    answer_text: str | None = None

    def as_tagging_context(self) -> TaggingContext:
        text = "\n".join(value for value in (self.shared_stem, self.part_text) if value)
        return TaggingContext(question_text=text, answer_text=self.answer_text)

@dataclass(frozen=True, slots=True)
class PartTagAnalysis:
    part_ref: str
    part_order: int
    analysis: TagAnalysis

@dataclass(frozen=True, slots=True)
class QuestionTagAnalysis:
    parts: tuple[PartTagAnalysis, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_part_refs: Sequence[str]) -> "QuestionTagAnalysis":
        rows = payload.get("parts")
        if not isinstance(rows, list):
            raise ValueError("parts must be an array")
        by_ref: dict[str, PartTagAnalysis] = {}
        for order, row in enumerate(rows, start=1):
            part_ref = str(row.get("part_ref") or "").strip()
            if part_ref in by_ref:
                raise ValueError(f"duplicate part_ref: {part_ref}")
            by_ref[part_ref] = PartTagAnalysis(part_ref, order, TagAnalysis.from_dict(dict(row)))
        expected = tuple(expected_part_refs)
        missing = [ref for ref in expected if ref not in by_ref]
        unknown = [ref for ref in by_ref if ref not in expected]
        if missing or unknown:
            raise ValueError(f"part_ref mismatch; missing part_ref={missing}; unknown part_ref={unknown}")
        return cls(tuple(by_ref[ref] for ref in expected))

    def aggregate(self) -> TagAnalysis:
        return aggregate_part_analyses(self.parts)
```

`aggregate_part_analyses()` 对列表字段按小问顺序去重，难度取最大值，章节/阶段/学生层次使用第一个非空值，`confidence` 取最小值，`reason` 按 `part_ref` 拼接。

- [ ] **Step 5: 运行测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_quality.py tests/test_question_part_tagging.py -q`

Expected: PASS，包含 `7.1 != 7.9` 和非法值拒绝断言。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/models/tag_schema.py question_bank/models/question_part.py tests/test_question_bank_ai_tagging_quality.py tests/test_question_part_tagging.py
git commit -m "feat: preserve tenth precision tag difficulty"
```

### Task 3: 确定性提取题库小问结构

**Files:**
- Create: `question_bank/services/question_part_parser.py`
- Create: `tests/test_question_part_parser.py`

**Interfaces:**
- Produces: `extract_question_parts(question_text: str, answer_text: str | None) -> tuple[QuestionPartInput, ...]`。
- Rule: 仅连续 `(1)/(2)` 或 `（1）（2）` 标记拆分；否则返回一个 `main`，不让 AI 猜小问数。

- [ ] **Step 1: 写失败测试**

```python
def test_extracts_shared_stem_and_numbered_parts() -> None:
    parts = extract_question_parts("如图，已知AB=AC。（1）证明BD=CD；（2）求∠A。", "（1）证明略；（2）40°")
    assert [part.part_ref for part in parts] == ["part:1", "part:2"]
    assert parts[0].shared_stem == "如图，已知AB=AC。"
    assert "证明BD=CD" in parts[0].part_text
    assert parts[1].answer_text == "40°"

def test_coordinate_parentheses_do_not_create_false_parts() -> None:
    parts = extract_question_parts("点A(1,2)，求一次函数解析式。", "y=2x")
    assert parts == (QuestionPartInput("main", 1, "", "点A(1,2)，求一次函数解析式。", "y=2x"),)

def test_non_contiguous_markers_fall_back_to_main() -> None:
    assert extract_question_parts("（1）求值（3）证明", None)[0].part_ref == "main"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_parser.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现保守解析器**

```python
PART_MARKER = re.compile(r"[（(]\s*(?P<number>\d{1,2})\s*[）)]")

def extract_question_parts(question_text: str, answer_text: str | None) -> tuple[QuestionPartInput, ...]:
    text = str(question_text or "").strip()
    matches = list(PART_MARKER.finditer(text))
    numbers = [int(match.group("number")) for match in matches]
    if len(matches) < 2 or numbers != list(range(1, len(numbers) + 1)):
        return (QuestionPartInput("main", 1, "", text, _clean_optional(answer_text)),)
    shared_stem = text[: matches[0].start()].strip()
    answer_parts = _split_numbered_text(str(answer_text or ""), expected=numbers)
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append(QuestionPartInput(
            part_ref=f"part:{numbers[index]}",
            part_order=index + 1,
            shared_stem=shared_stem,
            part_text=text[match.end():end].strip(),
            answer_text=answer_parts.get(numbers[index]),
        ))
    return tuple(result)
```

- [ ] **Step 4: 运行测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_parser.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add question_bank/services/question_part_parser.py tests/test_question_part_parser.py
git commit -m "feat: extract stable question parts"
```

### Task 4: 将 AI 请求和响应改为逐小问

**Files:**
- Modify: `question_bank/models/tag_schema.py:50-89`
- Modify: `question_bank/services/ai_tagging_service.py:231-290,417-562,666-850,988-1045`
- Modify: `tests/test_question_bank_ai_tagging_quality.py`

**Interfaces:**
- Consumes: `TaggingContext.parts: tuple[QuestionPartInput, ...]`。
- Produces: `AITaggingResult.analysis: QuestionTagAnalysis | None`。
- JSON: `{ "parts": [{ "part_ref": "part:1", ... }] }`。

- [ ] **Step 1: 写提示词、Schema 和完整性失败测试**

```python
def test_response_schema_requires_part_ref_and_tenth_difficulty() -> None:
    schema = _tag_analysis_response_format()["schema"]
    item = schema["properties"]["parts"]["items"]
    assert "part_ref" in item["required"]
    assert item["properties"]["difficulty"] == {
        "type": "number", "minimum": 1.0, "maximum": 10.0, "multipleOf": 0.1
    }

def test_prompt_requires_per_part_tags_and_parent_union_is_server_derived() -> None:
    prompt = _system_prompt()
    assert "每个小问分别输出" in prompt
    assert "7.1 与 7.9" in prompt
    assert "不要输出父题汇总标签" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_quality.py -q`

Expected: FAIL，当前 Schema 是整题平铺字段。

- [ ] **Step 3: 改造上下文、Prompt 和 JSON Schema**

```python
def _part_properties() -> dict[str, Any]:
    arrays = {name: {"type": "array", "items": {"type": "string"}} for name in LIST_FIELDS}
    return {
        "part_ref": {"type": "string", "minLength": 1},
        **arrays,
        "difficulty": {"type": "number", "minimum": 1.0, "maximum": 10.0, "multipleOf": 0.1},
        "textbook_chapter": {"type": "string"},
        "teaching_stage": {"type": "string"},
        "suitable_student_level": {"type": "string"},
        "canonical_knowledge_id": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    }

def _tag_analysis_response_format() -> dict[str, Any]:
    part_properties = _part_properties()
    return {
        "type": "json_schema",
        "name": "question_bank_part_tag_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"parts": {"type": "array", "minItems": 1, "items": {
                "type": "object", "properties": part_properties,
                "required": list(part_properties), "additionalProperties": False,
            }}},
            "required": ["parts"], "additionalProperties": False,
        },
    }
```

在单题和批量解析后统一调用：

```python
expected_refs = tuple(part.part_ref for part in context.parts)
analysis = QuestionTagAnalysis.from_dict(payload, expected_part_refs=expected_refs)
```

批量 Schema 的每个 `question_id` 项内嵌同样的 `parts`，不能保留旧平铺字段。

- [ ] **Step 4: 保持质量检查逐小问执行**

```python
def _evaluate_question_analysis_quality(result: QuestionTagAnalysis, context: TaggingContext) -> tuple[str, list[str], float]:
    notes: list[str] = []
    statuses = []
    confidences = []
    context_by_ref = {part.part_ref: part for part in context.parts}
    for part in result.parts:
        status, part_notes, confidence = _evaluate_analysis_quality(part.analysis, context_by_ref[part.part_ref].as_tagging_context())
        statuses.append(status)
        confidences.append(confidence)
        notes.extend(f"{part.part_ref}：{note}" for note in part_notes)
    overall = "complete" if all(status == "complete" for status in statuses) else "invalid"
    return overall, notes, min(confidences, default=0.0)
```

- [ ] **Step 5: 运行 AI 服务测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_quality.py tests/test_prompt_injection_guard.py -q`

Expected: PASS；批量和单题均拒绝缺失/未知小问。

- [ ] **Step 6: 提交**

```powershell
git add question_bank/models/tag_schema.py question_bank/services/ai_tagging_service.py tests/test_question_bank_ai_tagging_quality.py
git commit -m "feat: tag every question part with AI"
```

### Task 5: 保存候选、确认技能并派生父题并集

**Files:**
- Create: `question_bank/services/question_part_service.py`
- Modify: `question_bank/services/question_service.py:854-934,1080-1175`
- Modify: `tests/test_question_part_tagging.py`
- Modify: `tests/test_question_skill_dual_write.py`

**Interfaces:**
- Produces: `QuestionPartService.save_candidate(question_id, analysis, model_name) -> None`。
- Produces: `QuestionPartService.confirm(question_id, parts, reviewer) -> None`。
- Produces: `QuestionPartService.get_view(question_id) -> QuestionPartView`。
- Consumes: `SkillResolutionService.resolve()`，冲突继续写现有 `skill_resolution_conflicts`。

- [ ] **Step 1: 写事务和父题并集失败测试**

```python
def test_confirm_saves_parts_primary_skills_and_parent_union_atomically(tmp_path: Path) -> None:
    service, question_id, analysis = build_two_part_test_system(tmp_path)
    service.save_candidate(question_id, analysis, model_name="tag-model")
    service.confirm(question_id, analysis, reviewer="teacher")
    view = service.get_view(question_id)
    assert [part.difficulty for part in view.parts] == [7.1, 7.9]
    assert view.parent_difficulty == 7.9
    assert view.parent_tags["knowledge_point"] == ["一次函数", "函数图像"]
    assert view.parent_tags["sub_skill"] == ["求一次函数解析式", "读取函数图像"]

def test_resolution_conflict_rolls_back_confirmation_and_parent_tags(tmp_path: Path) -> None:
    service, question_id, analysis = build_conflicting_test_system(tmp_path)
    service.save_candidate(question_id, analysis, model_name="tag-model")
    with pytest.raises(QuestionPartConfirmationError):
        service.confirm(question_id, analysis, reviewer="teacher")
    assert service.get_view(question_id).confirmed_part_count == 0
```

`tests/test_question_part_tagging.py` 同时定义两个完整测试构造器：`build_two_part_test_system()` 初始化临时题库、插入一道题，并用内置技能名“一次函数解析式/读取一次函数图像”构造难度 `7.1/7.9` 的两个小问；`build_conflicting_test_system()` 复用同一题目分析，但向 `QuestionPartService` 注入始终返回 `ResolutionOutcome.CONFLICT` 的假解析器。两个构造器都返回 `(service, question_id, analysis)`，且只操作 `tmp_path`。

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_tagging.py tests/test_question_skill_dual_write.py -q`

Expected: FAIL，服务不存在。

- [ ] **Step 3: 实现候选保存和确认事务**

```python
class QuestionPartService:
    def save_candidate(self, question_id: int, analysis: QuestionTagAnalysis, *, model_name: str | None) -> None:
        with connect(self.db_path) as conn:
            _replace_suggested_parts(conn, question_id, analysis, model_name=model_name)

    def confirm(self, question_id: int, analysis: QuestionTagAnalysis, *, reviewer: str) -> None:
        with connect(self.db_path) as conn:
            part_ids = _upsert_confirmed_parts(conn, question_id, analysis, reviewer=reviewer)
            _replace_confirmed_part_tags(conn, part_ids, analysis)
            _replace_confirmed_part_skills(conn, part_ids, analysis, resolver=self.resolver, reviewer=reviewer)
            _sync_parent_question_tags(conn, question_id)
            _sync_parent_difficulty(conn, question_id)
```

`_replace_confirmed_part_skills()` 对每小问只把 `measured_skills[0]` 设为 `is_primary=1`；其余 `measured_skills` 和 `supporting_skills` 设为解释性关联。主要技能无法解析时抛出 `QuestionPartConfirmationError`，事务整体回滚。

- [ ] **Step 4: 实现稳定父题汇总**

```python
MANAGED_PARENT_TAG_TYPES = tuple(TAG_ANALYSIS_MAP.values()) + ("canonical_knowledge_id",)

def _sync_parent_question_tags(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute(
        f"DELETE FROM question_tags WHERE question_id = ? AND tag_type IN ({','.join('?' for _ in MANAGED_PARENT_TAG_TYPES)}) AND source IN ('ai', 'part_union', 'taxonomy')",
        (question_id, *MANAGED_PARENT_TAG_TYPES),
    )
    rows = conn.execute(
        """
        SELECT t.tag_type, t.tag_value, MIN(p.part_order) AS first_part,
               MIN(t.id) AS first_tag, MIN(t.confidence) AS confidence
        FROM question_parts p JOIN question_part_tags t ON t.question_part_id = p.id
        WHERE p.question_id = ? AND p.review_status = 'confirmed'
          AND t.review_status = 'confirmed'
        GROUP BY t.tag_type, t.tag_value
        ORDER BY first_part, first_tag
        """,
        (question_id,),
    ).fetchall()
    conn.executemany(
        "INSERT INTO question_tags(question_id, tag_type, tag_value, confidence, source) VALUES (?, ?, ?, ?, 'part_union')",
        [(question_id, row["tag_type"], row["tag_value"], row["confidence"]) for row in rows],
    )
```

父题难度仅在所有确认小问均有 `difficulty_tenths` 时写为 `f"{max_tenths / 10:.1f}"`；否则写 `NULL` 并在视图中返回 `difficulty_pending=True`。

- [ ] **Step 5: 将旧保存入口改为兼容委托**

`QuestionService.save_tag_analysis()` 接受 `QuestionTagAnalysis | TagAnalysis`。收到旧 `TagAnalysis` 时仅包装成 `main` 小问，先保存候选；只有 UI 明确传入 `confirm=True, reviewer=...` 时确认。保留手工父题标签编辑路径，但标注为历史兼容，不写小问正式技能。

- [ ] **Step 6: 运行服务回归**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_tagging.py tests/test_question_skill_dual_write.py tests/test_question_bank_service.py -q`

Expected: PASS；父题并集顺序稳定且冲突原子回滚。

- [ ] **Step 7: 提交**

```powershell
git add question_bank/services/question_part_service.py question_bank/services/question_service.py tests/test_question_part_tagging.py tests/test_question_skill_dual_write.py
git commit -m "feat: confirm part tags and derive parent union"
```

### Task 6: 题库界面按小问确认和展示

**Files:**
- Create: `pages_shared/question_part_tagging_component.py`
- Modify: `pages/题库管理.py:656-850,1196-1525,2188-2405,2520-2655`
- Modify: `pages_shared/shared_components.py:9-28`
- Modify: `tests/test_question_bank_ai_tagging_ui.py`
- Create: `tests/test_question_part_tagging_ui.py`

**Interfaces:**
- Produces: `render_question_part_candidate(service, question_id, analysis, key_prefix)`。
- Produces: `render_question_part_summary(view, key_prefix)`。

- [ ] **Step 1: 写 UI 契约失败测试**

```python
def test_question_bank_uses_shared_part_component_and_decimal_inputs() -> None:
    page = Path("pages/题库管理.py").read_text(encoding="utf-8")
    component = Path("pages_shared/question_part_tagging_component.py").read_text(encoding="utf-8")
    assert "render_question_part_candidate" in page
    assert 'step=0.1' in component
    assert 'format="%.1f"' in component
    assert "来自" in component and "个小问" in component
    assert "确认全部小问标签" in component
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_ui.py tests/test_question_part_tagging_ui.py -q`

Expected: FAIL，共享组件不存在且当前难度步长为 1。

- [ ] **Step 3: 实现共享小问组件**

```python
def render_question_part_candidate(service, question_id: int, analysis: QuestionTagAnalysis, *, key_prefix: str) -> None:
    edited_parts: list[PartTagAnalysis] = []
    for part in analysis.parts:
        with st.expander(f"小问 {part.part_ref} · 难度 {part.analysis.difficulty:.1f}", expanded=True):
            difficulty = st.number_input(
                "难度", min_value=1.0, max_value=10.0, value=float(part.analysis.difficulty),
                step=0.1, format="%.1f", key=f"{key_prefix}_{part.part_ref}_difficulty",
            )
            primary_skill = st.text_input(
                "主要训练子技能", value=part.analysis.measured_skills[0],
                key=f"{key_prefix}_{part.part_ref}_primary_skill",
            )
            edited_parts.append(_edited_part(part, difficulty=difficulty, primary_skill=primary_skill))
    if st.button("确认全部小问标签", type="primary", key=f"{key_prefix}_confirm"):
        service.confirm(question_id, QuestionTagAnalysis(tuple(edited_parts)), reviewer="teacher")
        st.success("小问标签已确认，父题标签已按去重并集刷新。")
```

组件同时显示 `父题标签（来自 N 个小问）`，按标签类型折叠；候选状态使用文字，不只靠颜色。

- [ ] **Step 4: 替换四处重复标签编辑区域**

`pages/题库管理.py` 的批量待确认、单题 AI 结果、卡片管理、历史编辑均调用共享组件；删除整数 `number_input` 和整题唯一标签表单。自动保存改为 `save_candidate()`，界面摘要计数改为“候选 / 已确认 / 失败”。

- [ ] **Step 5: 运行 UI 契约测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_bank_ai_tagging_ui.py tests/test_question_part_tagging_ui.py tests/test_question_preview_display_ui.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add pages_shared/question_part_tagging_component.py pages_shared/shared_components.py pages/题库管理.py tests/test_question_bank_ai_tagging_ui.py tests/test_question_part_tagging_ui.py
git commit -m "feat: review question tags by part"
```

### Task 7: 为历史题生成可审查的小问候选

**Files:**
- Create: `update_tools/backfill_question_parts.py`
- Create: `tests/test_question_part_backfill.py`
- Modify: `pages/系统自检.py:180-235`

**Interfaces:**
- Produces: CLI `python -m update_tools.backfill_question_parts --db ... --dry-run|--apply`。
- Produces: 只写 `suggested`，不自动确认、不刷新父题正式标签。

- [ ] **Step 1: 写 dry-run/apply 失败测试**

```python
def test_backfill_dry_run_does_not_write_and_apply_only_saves_suggestions(tmp_path: Path) -> None:
    db = seeded_legacy_questions(tmp_path)
    dry = backfill_question_parts(db, mode="dry-run", analyzer=FakePartAnalyzer())
    assert dry.candidates == 2
    assert count_rows(db, "question_parts") == 0
    applied = backfill_question_parts(db, mode="apply", analyzer=FakePartAnalyzer())
    assert applied.saved == 2
    assert statuses(db, "question_parts") == {"suggested"}
    assert parent_tags_unchanged(db)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_backfill.py -q`

Expected: FAIL，回填模块不存在。

- [ ] **Step 3: 实现有界回填**

```python
@dataclass(frozen=True, slots=True)
class BackfillReport:
    scanned: int
    candidates: int
    saved: int
    failed: tuple[str, ...]

class PartAnalyzer(Protocol):
    def analyze(self, question: Mapping[str, Any]) -> QuestionTagAnalysis: ...

def backfill_question_parts(db_path: Path, *, mode: str, analyzer: PartAnalyzer) -> BackfillReport:
    if mode not in {"dry-run", "apply"}:
        raise ValueError("mode must be dry-run or apply")
    questions = QuestionService(db_path).query_questions(tag_status="已打标签")
    pending = [question for question in questions if not _has_question_parts(db_path, int(question["id"]))]
    if mode == "dry-run":
        return BackfillReport(len(questions), len(pending), 0, ())
    return _analyze_and_save_suggestions(db_path, pending, analyzer)
```

每批复用现有限流和失败计数；失败题保留错误，不做无限重试。系统自检页只提供 dry-run 报告和显式 apply 按钮。

- [ ] **Step 4: 运行回填测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_backfill.py tests/test_storage_maintenance.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add update_tools/backfill_question_parts.py pages/系统自检.py tests/test_question_part_backfill.py
git commit -m "feat: backfill question part tag candidates"
```

### Task 8: 完整验证、真实迁移预演和架构文档

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `README_工作机使用说明.md`

**Interfaces:**
- Documents: 三张新表、AI 候选确认流、父题派生规则、回退方式。

- [ ] **Step 1: 运行本计划全部测试**

Run: `runtime\python\python.exe -m pytest tests/test_question_part_schema.py tests/test_question_part_parser.py tests/test_question_part_tagging.py tests/test_question_part_backfill.py tests/test_question_bank_ai_tagging_quality.py tests/test_question_bank_ai_tagging_ui.py tests/test_question_skill_dual_write.py tests/test_question_bank_service.py -q`

Expected: PASS。

- [ ] **Step 2: 在数据库副本上预演迁移**

```powershell
Copy-Item -LiteralPath user_data/databases/question_bank.db -Destination scratch/question_bank_part_migration.db
@'
import sqlite3
from pathlib import Path
db = Path("scratch/question_bank_part_migration.db")
sql = Path("migrations/question_bank/009_add_question_parts.sql").read_text(encoding="utf-8")
with sqlite3.connect(db) as conn:
    conn.executescript(sql)
    conn.executescript(sql)
print("migration applied twice to scratch copy")
'@ | runtime\python\python.exe -
```

Expected: 输出 `migration applied twice to scratch copy`；原数据库不变，第二次执行不产生重复表或索引错误。

- [ ] **Step 3: 更新架构和使用说明**

在 `ARCHITECTURE.md` 数据视图增加 `questions → question_parts → question_part_tags/question_part_skill_links → skills`，并注明父题 `question_tags` 是兼容汇总；在 README 增加历史回填 `dry-run → apply → 教师确认` 顺序。

- [ ] **Step 4: 浏览器验证题库页面**

在真实 Streamlit 页面检查：单问题、多小问题、7.1/7.9、候选待确认、确认后父题并集、长标签换行、1366×768/1440×900/1920×1080、键盘展开按钮和控制台错误。

Expected: 三种分辨率无横向溢出；父题显示“来自 N 个小问”；刷新后仍显示 7.1/7.9。

- [ ] **Step 5: 提交文档**

```powershell
git add ARCHITECTURE.md README_工作机使用说明.md
git commit -m "docs: document question part tagging flow"
```
