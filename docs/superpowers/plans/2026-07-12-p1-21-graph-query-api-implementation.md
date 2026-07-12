# P1-21 Graph Query API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 `question_tags.knowledge_point` 精确标签图谱提供可过滤、可分页、可下钻且不泄露内部路径的只读 FastAPI 契约。

**Architecture:** 新增独立 `/api/graph` 路由，复用 `DiagnosisProfileService.build_tag_profiles()` 作为唯一业务事实来源；不复制诊断算法，也不读取旧 concept/skill 活动语义。`skill_graph_projection.py` 只负责把一次请求内生成的 profile 投影为稳定 rows、聚合 nodes 和分页 evidence；P1-21 返回空 `edges`，为 Phase 4 关系层预留响应形状但不创建或推断任何关系。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 DiagnosisProfileService/QuestionTagProjectionService、pytest。

**执行包：** P1-21
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 4008a1c03e40e478bda66079152c35c25db78614
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-21：tag profiles、question-tag graph rows、聚合节点、空关系边集合、节点证据分页和下钻字段。
- 活动身份只能是 `knowledge_point:<精确裁剪后的 question_tags 标签值>`；不读取或返回 `knowledge_concepts`、`skills`、`question_skill_links`、legacy/shadow/skill 模式。
- 不创建 `tag_relations`，不新增或迁移数据库 Schema，不推断 prerequisite/parent/related 边；P1-21 的 `edges` 必须为空。
- 班级、学生和考试过滤复用现有 `TrainingScopeRequest`、`TrainingExamScopeRequest` 与 `DiagnosisProfileService` 解析规则；不存在或已删除的对象保持明确 warning + 空态，不把输入回显进错误正文。
- 每个 profiles/rows/evidence 请求只调用一次 `build_tag_profiles()`；rows、nodes 与 evidence 都从该请求内 profile 投影，禁止为同一请求重复读取标签投影。
- 证据响应使用显式允许字段，只包含服务器 ID、学生/考试显示字段、题号、题库题 ID、分数、标签上下文和聚合错因；不得返回 front/back image、源文件、绝对路径、富文本或原始 JSON。
- Pydantic 请求全部 `extra="forbid"`，分页 `page >= 1`、`1 <= page_size <= 100`，knowledge key 只接受 `knowledge_point:` 前缀且标签非空。
- 数据库/OSError 统一映射为脱敏 `graph_database_unavailable` 503；请求校验使用现有统一 422 `ErrorResponse`。
- 不修改 Streamlit 行为、训练推荐口径、评分规则、PathManager、运行入口或任何真实 `user_data/`。
- 所有测试只使用 `tmp_path` 临时双库和合成数据；不打开、初始化或写入根目录真实数据库。
- 功能分支不更新 `EXECUTION_INDEX.md`；共享 Index 与最终架构状态由 integration 统一整理。

---

## File Structure

- Create: `backend/api/schemas/graph.py`：严格请求、profile/row/node/edge/evidence 与分页响应模型。
- Create: `backend/api/routers/graph.py`：三个只读 POST 端点、一次 profile 构建、稳定错误映射和显式响应投影。
- Modify: `integration/skill_graph_projection.py`：对 question-tag rows 做确定性排序、节点聚合和证据分页投影；保留旧 skill helper 仅供回退调用，不在新路由使用。
- Modify: `backend/api/routers/__init__.py`：导出 `graph_router`。
- Modify: `backend/api/schemas/__init__.py`：导出 Graph 契约模型。
- Modify: `backend/api/app.py`：注册独立 Graph router。
- Create: `tests/test_api_graph_routes.py`：tag-only、班级/学生/考试过滤、去重、空态、分页、脱敏、未知对象和数据库失败。
- Modify: `tests/test_skill_graph_projection.py`：rows 顺序、节点聚合、空 edges 与 evidence 页切分。
- Modify: `tests/test_api_openapi_contract.py`：Graph 三端点、统一 422/503、无 legacy/broad/relationship 控制和唯一 operation ID。
- Modify: `ARCHITECTURE.md`：实现与验证完成后记录 P1-21 增量边界和 API 路由事实。
- Modify: `docs/superpowers/plans/2026-07-12-p1-21-graph-query-api-implementation.md`：checkbox、RED/GREEN、验证、复审和交接证据。

## Public Interfaces

```python
class GraphQueryRequest(_GraphModel):
    scope: TrainingScopeRequest
    exam_scope: TrainingExamScopeRequest

class GraphEvidenceRequest(GraphQueryRequest):
    knowledge_key: str = Field(min_length=17, max_length=300)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

class GraphEdge(_GraphModel):
    source_key: str
    target_key: str
    relation_type: Literal["prerequisite", "parent", "related"]
    weight: float = Field(ge=0.0, le=1.0)

POST /api/graph/profiles -> GraphProfilesResponse
POST /api/graph/rows -> GraphRowsResponse
POST /api/graph/evidence -> GraphEvidenceResponse
```

`GraphRowsResponse.edges` 在本包固定为 `[]`。`GraphEvidenceItem` 由 profile 中的 `source_question_refs` 与所属 student/weak-point 显式合成，字段固定为 `student_id/student_code/student_name/class_id/knowledge_key/knowledge_label/session_id/session_name/question_id/bank_question_id/score_awarded/full_score/score_rate/tag_context/actionable_reasons/error_counts`。

---

### Task 1: Freeze strict Graph API schemas and registration contract

**Files:**
- Create: `tests/test_api_graph_routes.py`
- Create: `backend/api/schemas/graph.py`
- Modify: `backend/api/schemas/__init__.py`

**Interfaces:**
- Consumes: `TrainingScopeRequest`, `TrainingExamScopeRequest`, current unified validation error handler.
- Produces: `GraphQueryRequest`, `GraphEvidenceRequest`, `GraphProfilesResponse`, `GraphRowsResponse`, `GraphEvidenceResponse`.

- [x] **Step 1: Write failing schema tests**

```python
def test_graph_request_rejects_legacy_controls(graph_client):
    body = graph_request()
    body["read_mode"] = "skill"
    response = graph_client.post("/api/graph/rows", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

def test_graph_evidence_rejects_non_tag_identity(graph_client):
    body = {**graph_request(), "knowledge_key": "skill:7"}
    assert graph_client.post("/api/graph/evidence", json=body).status_code == 422
```

- [x] **Step 2: Run tests to verify RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_graph_routes.py -q`
Expected: collection/import fails because `backend.api.schemas.graph` and Graph routes do not exist.

- [x] **Step 3: Implement strict schema models**

```python
class _GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class GraphQueryRequest(_GraphModel):
    scope: TrainingScopeRequest
    exam_scope: TrainingExamScopeRequest

class GraphEvidenceRequest(GraphQueryRequest):
    knowledge_key: str = Field(min_length=17, max_length=300)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("knowledge_key")
    @classmethod
    def exact_tag_key(cls, value: str) -> str:
        text = value.strip()
        prefix = "knowledge_point:"
        if not text.startswith(prefix) or not text[len(prefix):].strip():
            raise ValueError("knowledge_key must use question-tag identity")
        return f"{prefix}{text[len(prefix):].strip()}"
```

Define response models with only the fields listed in Public Interfaces, `diagnosis_identity: Literal["question_tag"]`, `edges: list[GraphEdge]`, and bounded pagination integers.

- [x] **Step 4: Run schema tests GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_graph_routes.py -q`
Expected: route tests still fail only because endpoints are unregistered; direct schema validation tests pass.

- [x] **Step 5: Commit Task 1 checkpoint**

```powershell
git add backend/api/schemas/graph.py backend/api/schemas/__init__.py tests/test_api_graph_routes.py
git commit -m "test: define P1-21 graph API contract"
```

### Task 2: Project deterministic tag rows, nodes, empty edges and evidence

**Files:**
- Modify: `integration/skill_graph_projection.py`
- Modify: `tests/test_skill_graph_projection.py`

**Interfaces:**
- Consumes: a `DiagnosisProfileService.build_tag_profiles()` mapping using `diagnosis_identity == "question_tag"`.
- Produces: `build_question_tag_graph_rows(profile)`, `build_question_tag_graph_nodes(rows)`, `build_question_tag_graph_evidence(profile, knowledge_key)`.

- [x] **Step 1: Write failing projection tests**

```python
def test_tag_graph_nodes_aggregate_students_and_items():
    rows = build_question_tag_graph_rows(tag_profile())
    assert build_question_tag_graph_nodes(rows) == [{
        "knowledge_key": "knowledge_point:三角形全等",
        "knowledge_label": "三角形全等",
        "student_count": 2,
        "item_count": 3,
        "deduction_count": 2,
        "average_mastery": 0.7,
        "tag_context": {"method": ["构造辅助线"]},
        "error_counts": {"primary": {"辅助线思路缺失": 1}, "secondary": {}},
    }]

def test_tag_graph_evidence_is_deduplicated_and_path_free():
    items = build_question_tag_graph_evidence(tag_profile(), "knowledge_point:三角形全等")
    assert [(item["student_id"], item["session_id"], item["question_id"]) for item in items] == [(12, 14, "Q1")]
    assert "front_image" not in items[0]
```

- [x] **Step 2: Run tests to verify RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_skill_graph_projection.py -q`
Expected: imports fail for the two missing helper functions.

- [x] **Step 3: Implement minimal deterministic projections**

```python
def build_question_tag_graph_nodes(rows):
    grouped = {}
    for row in rows:
        key = row["knowledge_key"]
        bucket = grouped.setdefault(key, {"rows": [], "students": set()})
        bucket["rows"].append(row)
        bucket["students"].add(row["student_id"])
    return [_node_from_rows(key, grouped[key]) for key in sorted(grouped)]

def build_question_tag_graph_evidence(profile, knowledge_key):
    items, seen = [], set()
    for student in profile.get("students", []):
        for weak in student.get("weak_points", []):
            if weak.get("knowledge_key") != knowledge_key:
                continue
            for ref in weak.get("source_question_refs", []):
                identity = (student.get("student_id"), ref.get("session_id"), ref.get("question_id"), ref.get("bank_question_id"))
                if identity in seen:
                    continue
                seen.add(identity)
                items.append(_public_tag_evidence(student, weak, ref))
    return sorted(items, key=lambda item: (item["session_id"], item["student_id"], item["question_id"], item["bank_question_id"]))
```

Aggregate mastery weighted by each row's `item_count` (falling back to one), merge tag lists in first-seen order, sum primary/secondary error counters, and sort rows by `(knowledge_key, student_id)`.

- [x] **Step 4: Run projection tests GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_skill_graph_projection.py -q`
Expected: all projection tests pass, including existing legacy helper compatibility.

- [x] **Step 5: Commit Task 2 checkpoint**

```powershell
git add integration/skill_graph_projection.py tests/test_skill_graph_projection.py
git commit -m "feat: project tag graph nodes and evidence"
```

### Task 3: Add profiles, rows and paginated evidence endpoints

**Files:**
- Create: `backend/api/routers/graph.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `tests/test_api_graph_routes.py`

**Interfaces:**
- Consumes: `get_diagnosis_profile_service`, Graph request schemas, projection helpers.
- Produces: three registered `/api/graph/*` operations with unified 422/503 errors.

- [x] **Step 1: Write failing endpoint behavior tests**

```python
def test_graph_rows_use_tag_only_profile_once(graph_client, graph_service):
    response = graph_client.post("/api/graph/rows", json=graph_request())
    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnosis_identity"] == "question_tag"
    assert payload["edges"] == []
    assert payload["rows"][0]["knowledge_key"] == "knowledge_point:三角形全等"
    assert graph_service.build_calls == 1

def test_graph_evidence_paginates_without_paths(graph_client):
    response = graph_client.post("/api/graph/evidence", json={
        **graph_request(), "knowledge_key": "knowledge_point:三角形全等", "page": 1, "page_size": 1,
    })
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    assert "C:/private" not in response.text
```

Also cover class filter, manual/cross-exam filter, missing student/session warnings, missing tag empty state, page past end, database failure 503, and rejection of legacy/broad/relations fields.

- [x] **Step 2: Run endpoint tests to verify RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_graph_routes.py -q`
Expected: 404 for the three missing Graph routes.

- [x] **Step 3: Implement thin router with one profile build per request**

```python
router = APIRouter(prefix="/api/graph", tags=["graph"])

def _profile(body, service):
    try:
        profile = service.build_tag_profiles(
            scope=body.scope.model_dump(exclude_none=True),
            exam_scope=body.exam_scope.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise ApiError(422, "graph_scope_invalid", "Graph scope is invalid") from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "graph_database_unavailable", "Graph data is temporarily unavailable") from exc
    if profile.get("diagnosis_identity") != "question_tag":
        raise ApiError(422, "graph_scope_invalid", "Graph scope is invalid")
    return profile
```

Profiles validates the profile directly; rows calls the row/node helpers and returns `edges=[]`; evidence calls the evidence helper, slices `start=(page-1)*page_size`, and returns `total_pages=max(1, ceil(total/page_size))`. Register `graph_router` once in `create_app()`.

- [x] **Step 4: Run endpoint tests GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_graph_routes.py tests\test_skill_graph_projection.py -q`
Expected: all Graph API/projection tests pass.

- [x] **Step 5: Run adjacent tag/training regression**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_diagnosis_profile_service.py tests\test_question_tag_diagnosis.py tests\test_question_tag_projection_service.py tests\test_api_training_routes.py -q`
Expected: existing tag-only Training and projection behavior remains green.

- [x] **Step 6: Commit Task 3 checkpoint**

```powershell
git add backend/api/routers/graph.py backend/api/routers/__init__.py backend/api/app.py tests/test_api_graph_routes.py
git commit -m "feat: add read-only graph query API"
```

### Task 4: Freeze OpenAPI, architecture and package handoff evidence

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-21-graph-query-api-implementation.md`

**Interfaces:**
- Consumes: registered Graph operations and all prior task evidence.
- Produces: stable OpenAPI contract, current architecture fact, valid P1-21 handoff.

- [x] **Step 1: Write failing OpenAPI assertions**

```python
GRAPH_OPERATIONS = {
    ("post", "/api/graph/profiles"),
    ("post", "/api/graph/rows"),
    ("post", "/api/graph/evidence"),
}

def test_graph_openapi_declares_strict_tag_only_operations():
    schema = create_app().openapi()
    for method, path in GRAPH_OPERATIONS:
        assert {422, 503}.issubset({int(code) for code in schema["paths"][path][method]["responses"]})
    graph_text = json.dumps({path: schema["paths"][path] for _, path in GRAPH_OPERATIONS})
    assert "read_mode" not in graph_text
    assert "allow_broad_fallback" not in graph_text
    assert "tag_relations" not in graph_text
```

- [x] **Step 2: Run OpenAPI test RED, then add exact route list and response assertions**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py -q`
Expected before assertion update: missing Graph operations in the expected current Phase 1 route set; after update: pass with unique operation IDs and unified 422 schemas.

- [x] **Step 3: Update architecture fact after code verification**

Add one P1-21 increment bullet and update FastAPI route listings to say Graph exposes tag-only profiles, deterministic rows/nodes, empty Phase-4 relationship edges, and paginated path-free evidence; no Schema or relationship semantics changed.

- [x] **Step 4: Run final focused and affected regression**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_graph_routes.py tests\test_skill_graph_projection.py tests\test_diagnosis_profile_service.py tests\test_question_tag_diagnosis.py tests\test_question_tag_projection_service.py tests\test_api_training_routes.py tests\test_api_openapi_contract.py tests\test_api_app.py -q`
Expected: all selected tests pass with 0 failures.

- [ ] **Step 5: Run package completion gates**

```powershell
git diff --check
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p1-21-graph-query-api-implementation.md --repo .
git status --short -- user_data
```

Expected: diff clean; quick smoke passes; handoff validator emits one JSON line with `ok=true`; worktree `user_data/` status empty.

- [x] **Step 6: Re-read root real database fingerprints without opening SQLite**

Compare size, UTC mtime and SHA-256 to the recorded baseline:

- grading DB: `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`
- question-bank DB: `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`

Any difference is a blocking failure; do not integrate.

- [ ] **Step 7: Record waiting_review, commit feature, complete independent review, then create plan-only final handoff commit**

The functional commit must contain `交接状态: waiting_review`, `功能提交: branch_head`, automatic validation passed, independent review pending, user acceptance not required, unchanged fingerprint, and unchanged stash baseline. After fresh review has zero Critical/Important findings, create a plan-only final handoff commit whose block records its direct parent full SHA as `verified_pending_integration`.

## Verification and Recovery

- Rollback is the isolated P1-21 commit chain; no data migration or persistent file format changes exist.
- If a focused test, quick smoke, handoff validation, independent review or real-data fingerprint check fails, preserve the feature worktree and do not integrate.
- Full pytest is not the default feature-branch gate; integration runs the wave-end full smoke once, unless a repository risk trigger requires additional full testing.

## Implementation Evidence

- Baseline: existing diagnosis/tag projection/graph projection/Training API set `36 passed` before source changes.
- RED/GREEN: Graph schema tests first failed collection on the missing schema module; projection tests first failed imports for the missing node/evidence helpers; Graph route tests then failed `6` cases with 404. The schema cycle passed `5`, projection cycle passed `5`, and combined Graph route/projection cycle passed `16` after the minimal implementations.
- Focused regression: final Graph/diagnosis/tag projection/Training/OpenAPI/app set `66 passed`; adjacent Graph/tag/Training set passed `49` before the final OpenAPI/app expansion.
- Quick smoke: document governance, static compile of `370` first-party Python files, and two temporary database copies' idempotent initialization with `integrity_check=ok` passed. Full pytest remains the integration wave-end gate under repository policy.
- Independent review: pending.
- Real data: root grading DB remained `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`; root question-bank DB remained `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`. Worktree `user_data/` status is empty and no SQLite connection was opened against either real database.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-21
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
