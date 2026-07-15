# P2-14 工作台与分析总览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付采用精修方案 B 的只读教师工作台，让当前考试进度、待复核、异常卷、最近任务、最近考试、班级/题目/学生分析和知识标签证据都能从同一上下文被查看并下钻。

**Architecture:** 后端新增独立的只读 analysis 与 workbench 应用服务，统计公式从 Streamlit 抽取为共享实现，Job 列表只返回安全摘要，现有 Graph API 保持原样。Vue 端用严格运行时解码、两个不持久化 Pinia Store 和小型职责组件实现概览先到、分析与标签独立加载、旧请求隔离和最后成功内容保留；共享导航、路由和应用注册只做最小接线并由 integration 最终合并。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、SQLite、pytest、Vue 3、TypeScript 6、Pinia 3、Vue Router 5、Vitest、Playwright、原生语义 HTML/CSS。

**执行包：** P2-14
**用户自测：** quick
**自测清单：** `docs/user-testing/checkpoints/P2-14-v1.5.0-workbench-analysis-quick-check.md`

## Global Constraints

- 设计权威为 `docs/superpowers/specs/2026-07-15-p2-14-workbench-analysis-overview-design.md`，采用精修方案 B。
- 只允许只读聚合；不修改评分、复核、异常处理、Job 取消/重试、会话配置、Rubric、数据库 Schema 或业务数据。
- 不新增同比、环比、风险分、质量分、排名、预测完成时间、AI 建议或其他统计口径。
- 未知或不可计算值返回 `null` 和原因，不能伪装成 0；样本量始终显示，只有单样本使用“当前仅 1 份作答”的客观说明。
- 班级得分率、平均得分、父子题归并、封顶、失分判定和排序必须与现有 Streamlit 实现等价。
- Graph 继续使用 `question_tag` 身份；不新增关系边、掌握度 v2、训练建议或 P2-15 图谱交互。
- 页面唯一视觉签名为连续“批改进度—行动带”；禁止大数字卡片海洋、渐变、强阴影、装饰图标和无动作图表。
- 固定使用现有 Token：`#f6f7f8`、`#ffffff`、`#20242a`、`#d9dde2`、`#2563eb`、`#9a6718`；不新增网络字体。
- 五档桌面视口为 1024×768、1280×720、1366×768、1440×900、1920×1080；不实现移动端产品布局。
- 测试、截图和浏览器演示只使用临时数据库与合成匿名数据，不读取、写入、暂存、提交或 stash 真实 `user_data/`。
- P2-09 与 P2-14 使用不同功能 worktree；`backend/api/app.py`、router exports、`frontend/src/navigation.ts`、router、AppTopbar、App Shell、依赖锁与全局文档由 integration 负责最终接线。
- 不增加生产依赖，不修改 `package.json` 或锁文件。

---

## File Map

**Create:**

- `backend/analytics/__init__.py`：导出会话分析服务与数据类。
- `backend/analytics/service.py`：加载 Rubric 分值、规范题号、构建班级/全体题目行和学生明细；唯一统计公式实现。
- `backend/workbench/__init__.py`：导出工作台只读聚合服务。
- `backend/workbench/service.py`：聚合会话进度、复核、异常、Job 和最近考试。
- `backend/api/schemas/analytics.py`：题目分析与学生明细响应模型。
- `backend/api/schemas/workbench.py`：概览、异常和安全 Job 摘要模型。
- `backend/api/routers/analytics.py`：两个会话分析 GET 接口。
- `backend/api/routers/workbench.py`：概览与异常 GET 接口。
- `tests/test_session_analysis_service.py`：旧公式等价、父子题、缺失满分和学生明细测试。
- `tests/test_api_analytics_routes.py`：分析接口过滤、分页、空态、404 和脱敏测试。
- `tests/test_api_workbench_routes.py`：概览、最近考试、复核、异常、部分数据和只读指纹测试。
- `frontend/src/api/workbench.ts`：概览、异常、Job 安全摘要运行时解码与请求。
- `frontend/src/api/analysis.ts`：题目与学生分析运行时解码与请求。
- `frontend/src/api/graph.ts`：P2-14 所需 Graph rows/evidence 最小严格客户端。
- `frontend/src/stores/workbench.ts`：概览与异常独立状态、旧请求取消、最后成功内容和更新时间。
- `frontend/src/stores/analysis.ts`：班级/题目/标签状态、旧请求隔离和局部重试。
- `frontend/src/components/workbench/WorkbenchProgressRail.vue`：连续进度—行动带。
- `frontend/src/components/workbench/RecentSessions.vue`：最近考试与上下文切换。
- `frontend/src/components/analysis/QuestionAnalysisPanel.vue`：筛选、样本说明、得分率条形列表和题目选择。
- `frontend/src/components/analysis/QuestionAnalysisTable.vue`：可键盘操作的旧口径题目矩阵。
- `frontend/src/components/analysis/StudentAnalysisDetail.vue`：学生得分、扣分和复核证据入口。
- `frontend/src/components/analysis/TagCoverageSummary.vue`：选定班级的 tag-only 覆盖与证据列表。
- `frontend/src/views/WorkbenchView.vue`：页面编排和当前考试上下文。
- `frontend/src/styles/workbench.css`：精修方案 B、五视口和减少动效样式。
- `frontend/src/api/__tests__/workbench.spec.ts`：概览/异常解码和安全错误测试。
- `frontend/src/api/__tests__/analysis.spec.ts`：分析/Graph 解码和查询编码测试。
- `frontend/src/__tests__/workbench-store.spec.ts`：概览部分失败、旧请求和最后成功内容测试。
- `frontend/src/__tests__/analysis-store.spec.ts`：筛选切换、旧请求和独立 Graph 状态测试。
- `frontend/src/__tests__/workbench-view.spec.ts`：可见结构、动作、空态、错误、单样本和键盘测试。
- `frontend/e2e/workbench-overview.spec.ts`：五视口、下钻、部分失败、图表文字替代和非空页面测试。
- `docs/user-testing/checkpoints/P2-14-v1.5.0-workbench-analysis-quick-check.md`：实现和浏览器验证完成后生成的版本化短测卡。

**Modify:**

- `db_manager.py`：新增单次只读异常查询，不改变表和写路径。
- `web_app.py`：保留旧函数名，改为调用共享 analysis 服务，保证 Streamlit 行为不分叉。
- `backend/jobs/store.py`：增加稳定过滤和分页的只读 Job 查询。
- `backend/jobs/manager.py`：暴露只读 `list` 方法。
- `backend/api/schemas/jobs.py`：增加不含 payload/result/error 的 Job 列表模型。
- `backend/api/routers/jobs.py`：增加 `GET /api/jobs`，现有详情/取消/提交契约不变。
- `backend/api/routers/__init__.py`：最小导出 analytics/workbench router；integration 复核 P2-09 并存。
- `backend/api/app.py`：最小注册 analytics/workbench router；integration 复核 P2-09 并存。
- `backend/api/dependencies.py`：构造 analysis/workbench 服务，复用同一请求级数据库和 Job Manager。
- `backend/api/schemas/__init__.py`：仅在现有约定需要时导出新增 schema。
- `tests/test_api_jobs.py`：Job 列表过滤、分页、倒序和脱敏回归。
- `tests/test_api_openapi_contract.py`：冻结四个新增 GET 和 Job list 契约。
- `frontend/src/navigation.ts`：增加真实 Workbench 路由定义；integration 处理 P2-09 共享入口。
- `frontend/src/router/index.ts`：注册 `/workbench` 并把 `/` 指向工作台；保留 `/grading`。
- `frontend/src/components/shell/AppTopbar.vue`：只显示已实现的“工作台/评分复核”导航；integration 合并 P2-09 导航。
- `frontend/src/components/ApplicationErrorBoundary.vue`：安全返回工作台。
- `frontend/src/views/NotFoundView.vue`：安全返回工作台。
- `frontend/src/main.ts`：导入 `workbench.css`。
- `frontend/src/__tests__/navigation-router.spec.ts`、`App.spec.ts` 和 `components/shell/__tests__/app-shell.spec.ts`：更新真实路由、默认入口和恢复目标。
- `ARCHITECTURE.md`：记录实现后的只读工作台和共享统计服务事实。
- `docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md`：领取、证据和交接状态。

**Read and preserve:**

- `backend/api/routers/review.py`、`backend/review/service.py`：复用复核判断，不改确认写语义。
- `backend/api/routers/graph.py`、`backend/api/schemas/graph.py`：直接消费，不改 Graph 契约。
- `frontend/src/views/ReviewQueueView.vue` 与复核 Stores：只通过路由上下文进入，不复制评分或确认逻辑。
- `migrations/`：零修改。

---

### Task 0: 从已合并设计基线领取 P2-14

**Files:**

- Modify: `docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md`

**Interfaces:**

- Consumes: 已进入最新 `origin/main` 的 P2-14 设计规格、实施计划和 Index `ready` 状态。
- Produces: 专属功能 worktree、不可变 stash 基线和机器可验证的 `in_progress` 首提交。

- [ ] **Step 1: 先把设计与计划通过标准 integration/PR 合入主线**

从设计分支接收设计规格、Index 调整和本计划，运行纯文档治理与快速冒烟，通过 PR 合并；禁止直接 push `main`。重新获取主线后必须满足：

```powershell
git fetch --prune origin
git merge-base --is-ancestor codex/p2-14-workbench-design origin/main
git show origin/main:docs/superpowers/packages/EXECUTION_INDEX.md | Select-String 'P2-14.*ready'
```

Expected: 第一条祖先检查退出码为 0，Index 命中唯一的 P2-14 `ready` 行。若设计提交经 squash 合并导致祖先检查不成立，改用下面的内容等价检查；Index 继续以上述 `ready` 命中为准，任一文件缺失或有差异都停止：

```powershell
git diff --exit-code origin/main..codex/p2-14-workbench-design -- docs/superpowers/specs/2026-07-15-p2-14-workbench-analysis-overview-design.md docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md
```

- [ ] **Step 2: 用 `superpowers:using-git-worktrees` 创建正式功能工作区**

从最新 `origin/main` 创建 `.worktrees/p2-14-workbench` 和 `codex/p2-14-workbench`。不得在设计 worktree 或根目录实现。创建后运行：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git status --short -- user_data
git stash list --format=%H
```

Expected: HEAD 与 `origin/main` 相同，源码和 worktree 内 `user_data` 无输出；记录全部现有 stash SHA，当前已知共享基线若仍未变化为 `85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2`，若主线合并前合法变化则记录现场完整列表而不是沿用旧快照。

- [ ] **Step 3: 加入唯一交接块并创建领取提交**

按 `docs/superpowers/packages/README.md` 的唯一规范，在本计划末尾加入一对昼夜交接标记和九个字段，字段值固定如下；Stash 基线使用 Step 2 的现场完整值，空列表时写 `none`：

| 字段 | 值 |
|---|---|
| 执行包 | `P2-14` |
| 交接状态 | `in_progress` |
| 功能提交 | `none` |
| 自动验证 | `pending` |
| 独立复审 | `pending` |
| 用户验收 | `pending` |
| 真实数据指纹 | `not_touched` |
| Stash 基线 | Step 2 的现场完整值或 `none` |
| 夜间动作 | `report_only` |

暂存并验证：

```powershell
git add -- docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: claim P2-14 workbench package"
& '..\..\runtime\python\python.exe' tools\handoff_status.py --plan docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md --repo .
```

Expected: 首个 first-parent 提交只修改本计划，验证器输出 `ok=true`；源码修改前完成。

---

### Task 1: 抽取唯一的会话分析公式服务

**Files:**

- Create: `backend/analytics/__init__.py`
- Create: `backend/analytics/service.py`
- Create: `tests/test_session_analysis_service.py`
- Modify: `db_manager.py:1787-1816`
- Modify: `web_app.py:5401-5605`

**Interfaces:**

- Consumes: `DBManager.get_grading_session()`, `get_session_results()`, `get_result_details()` 和当前 Rubric JSON。
- Produces: `SessionAnalysisService.list_questions(session_id, class_name=None)`、`list_students(session_id, question_id, class_name=None)`、`QuestionAnalysisRow`、`StudentAnalysisRow`；Streamlit 旧函数调用同一服务。

- [ ] **Step 1: 写公式等价失败测试**

在临时 DB 写入两个班级、未分班学生、`Q1`、`Q2(1)`、`Q2(2)`、满分封顶、满分和失分明细；保存调用旧 `_build_session_question_analysis` 的基准结果。测试核心断言：

```python
def test_service_matches_streamlit_question_analysis_for_supported_rows(seed_analysis):
    db, session_id = seed_analysis
    legacy = _build_session_question_analysis(db, session_id)
    actual = SessionAnalysisService(db).list_questions(
        session_id,
        class_name="七年级一班",
    )
    legacy_class = [
        row for row in legacy["rows"] if row["班级"] == "七年级一班"
    ]
    assert [(row.class_name, row.question_id) for row in actual] == [
        (row["班级"], row["题号"]) for row in legacy_class
    ]
    assert actual[0].question_id == "Q1"
    assert actual[0].score_rate == 90.0
    assert actual[0].average_score == 9.0
    assert actual[0].deduction_count == 1
    assert actual[0].attempt_count == 2


def test_service_uses_null_for_uncomputable_rate(seed_missing_max_score):
    db, session_id = seed_missing_max_score
    row = SessionAnalysisService(db).list_questions(session_id)[0]
    assert row.score_rate is None
    assert row.metric_status == "missing_max_score"
    assert row.attempt_count == 1
```

- [ ] **Step 2: 运行测试确认 RED**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_session_analysis_service.py -q
```

Expected: collection 失败，指出 `backend.analytics` 不存在。

- [ ] **Step 3: 实现数据类、规范题号和问题行**

在 `backend/analytics/service.py` 定义精确公共类型：

```python
@dataclass(frozen=True, slots=True)
class QuestionAnalysisRow:
    class_name: str
    question_id: str
    max_score: float | None
    score_rate: float | None
    average_score: float | None
    deduction_count: int
    attempt_count: int
    metric_status: Literal["ready", "missing_max_score", "no_attempts"]


@dataclass(frozen=True, slots=True)
class StudentAnalysisRow:
    result_id: int
    detail_id: int
    student_id: int
    student_code: str | None
    student_name: str
    class_name: str
    question_id: str
    score_awarded: float
    max_score: float | None
    deduction_amount: float | None
    deduction_reason: str | None
    needs_review: bool


class SessionAnalysisService:
    def __init__(
        self,
        db: DBManager,
        review_service: ReviewApplicationService | None = None,
    ) -> None:
        self.db = db
        self.review_service = review_service or ReviewApplicationService(db)

    def list_questions(
        self,
        session_id: int,
        class_name: str | None = None,
    ) -> list[QuestionAnalysisRow]:
        """Return legacy-equivalent class rows or merged all-class rows."""

    def list_students(
        self,
        session_id: int,
        question_id: str,
        class_name: str | None = None,
    ) -> list[StudentAnalysisRow]:
        """Return direct details plus legacy inferred child full-score rows."""
```

实现时直接移入 `_load_session_score_type_maps`、`_normalize_question_analysis_details`、`_canonical_question_id_for_score`、`_question_parent_id`、`_question_sort_key` 和 `_merge_question_analysis_rows` 的计算语义。给 `DBManager.get_session_results()` 的既有 SELECT 增加 `sr.student_id`，不改变排序或其他返回字段。传入班级时返回该班级旧口径行；`class_name=None` 时返回旧 UI “全部班级”的合并行并把 `class_name` 固定为“全部班级”。`score_rate` 在 `full_sum <= 0` 时为 `None`；`average_score` 在 `attempt_count == 0` 时为 `None`；其他数字仍按旧规则保留两位小数。服务不得返回 `_wrong_items`、路径或 raw JSON。

- [ ] **Step 4: 实现学生明细与父题满分推断测试**

增加断言：直接子题按规范题号返回；只有父题满分且父题得满分时才推断子题满分；失分阈值继续为 `0.01`；`needs_review` 使用 `ReviewApplicationService.list_items()` 的既有判断结果映射到 detail ID，不复制判断公式。

```python
def test_student_rows_infer_child_full_score_only_from_full_parent(seed_parent_parts):
    db, session_id = seed_parent_parts
    rows = SessionAnalysisService(db).list_students(session_id, "Q2(1)")
    inferred = next(row for row in rows if row.student_code == "S-FULL")
    assert inferred.score_awarded == 4
    assert inferred.max_score == 4
    assert inferred.deduction_amount == 0
    assert all(row.student_code != "S-PARTIAL" for row in rows)
```

- [ ] **Step 5: 让 Streamlit 委托共享服务并运行 GREEN**

保留 `_build_session_question_analysis` 与 `_merge_question_analysis_rows` 的外部兼容返回结构，但内部从 `QuestionAnalysisRow` 转换；删除重复计算正文。运行：

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_session_analysis_service.py tests\test_report_score_adjustment.py tests\test_question_id_contract.py -q
```

Expected: 全部通过，旧报表与题号回归无变化。

- [ ] **Step 6: 提交共享公式服务**

```powershell
git add -- backend/analytics db_manager.py web_app.py tests/test_session_analysis_service.py
git diff --cached --check
git commit -m "refactor: share session analysis formulas"
```

---

### Task 2: 增加安全、可过滤的 Job 列表

**Files:**

- Modify: `backend/jobs/store.py`
- Modify: `backend/jobs/manager.py`
- Modify: `backend/api/schemas/jobs.py`
- Modify: `backend/api/routers/jobs.py`
- Modify: `tests/test_api_jobs.py`

**Interfaces:**

- Consumes: 现有 jobs 表和 `JobRecord`。
- Produces: `JobStore.list_jobs(...) -> tuple[list[JobRecord], int]`、`JobManager.list(...)`、`GET /api/jobs`、`JobSummaryListResponse`。

- [ ] **Step 1: 写列表过滤与脱敏失败测试**

```python
def test_jobs_list_filters_session_and_returns_safe_summaries(client_with_manager):
    client, manager = client_with_manager
    first = manager.store.create_job("grading_run", {"session_id": 7, "path": "C:/private/a"})
    second = manager.store.create_job("report_export", {"session_id": 8, "token": "secret"})
    manager.store.update_progress(first.id, progress=0.4, stage="grading", detail="processing")

    response = client.get("/api/jobs", params={"session_id": 7, "page": 1, "page_size": 20})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == first.id
    assert "payload" not in response.text
    assert "result" not in response.text
    assert "error" not in response.text
    assert "C:/private" not in response.text
    assert [item["id"] for item in response.json()["items"]] == [first.id]
```

另测重复 `job_type`/`status` 查询参数、非法状态 422、`id DESC` 稳定分页和 `page_size` 1—100。

- [ ] **Step 2: 运行目标测试确认 RED**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_jobs.py -q
```

Expected: 新测试得到 405 或 422，因为 `/api/jobs` GET 尚未注册。

- [ ] **Step 3: 实现 Store 与 Manager 只读查询**

增加：

```python
def list_jobs(
    self,
    *,
    session_id: int | None = None,
    job_types: tuple[str, ...] = (),
    statuses: tuple[str, ...] = (),
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[JobRecord], int]:
    """Filter valid JSON payload session_id and return id-desc stable pagination."""
```

SQL 只拼接受控占位符和值；`job_types`/`statuses` 去空白并去重，状态必须属于 `JOB_STATUSES`。`session_id` 用 `CAST(json_extract(payload_json, '$.session_id') AS INTEGER) = ?`；所有 payload 均由 Store 生成有效 JSON，不新增迁移。

- [ ] **Step 4: 实现安全响应 schema 与路由**

```python
class JobSummaryResponse(BaseModel):
    id: int
    job_type: str
    status: str
    progress: float
    stage: str
    detail: str
    created_at: str
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None


class JobSummaryListResponse(BaseModel):
    items: list[JobSummaryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
```

路由签名固定为 `session_id: int | None = Query(None, gt=0)`、`job_type: list[str] = Query(default=[])`、`status: list[str] = Query(default=[])`、`page: int = Query(1, ge=1)`、`page_size: int = Query(20, ge=1, le=100)`。`detail` 通过 `sanitize_public_mapping({"detail": job.detail}).get("detail", "")`，不调用 `_job_response`。

- [ ] **Step 5: 运行 Job 聚焦回归并提交**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_jobs.py tests\test_api_job_lifecycle.py -q
git add -- backend/jobs/store.py backend/jobs/manager.py backend/api/schemas/jobs.py backend/api/routers/jobs.py tests/test_api_jobs.py
git diff --cached --check
git commit -m "feat: add safe job summary list"
```

Expected: 聚焦测试全部通过；现有详情、提交和取消行为不变。

---

### Task 3: 建立工作台概览与异常只读聚合

**Files:**

- Create: `backend/workbench/__init__.py`
- Create: `backend/workbench/service.py`
- Create: `backend/api/schemas/workbench.py`
- Create: `backend/api/routers/workbench.py`
- Create: `tests/test_api_workbench_routes.py`
- Modify: `db_manager.py`
- Modify: `backend/api/dependencies.py`

**Interfaces:**

- Consumes: session list/progress、ReviewApplicationService、JobManager.list 和新异常查询。
- Produces: `GET /api/workbench/overview`、`GET /api/sessions/{session_id}/anomalies`、`WorkbenchOverviewResponse`、`SessionAnomalyListResponse`。

- [ ] **Step 1: 写概览、异常和只读指纹失败测试**

用临时 DB 写入三个考试、待复核题、未匹配卷、scan_issue、失败卷和两条 Job。核心断言：

```python
def test_overview_returns_existing_counts_and_recent_sessions(workbench_client):
    client, session_id = workbench_client
    payload = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id, "recent_limit": 2},
    ).json()
    assert payload["current_session"]["id"] == session_id
    assert payload["progress"]["unmatched_papers"] == 1
    assert payload["review"]["question_count"] == 1
    assert payload["review"]["item_count"] == 1
    assert len(payload["recent_sessions"]) == 2
    assert "rubric_path" not in str(payload)
    assert "answer_key_path" not in str(payload)


def test_workbench_gets_do_not_change_database_fingerprint(workbench_client):
    client, session_id, db_path = workbench_client
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    client.get("/api/workbench/overview", params={"session_id": session_id})
    client.get(f"/api/sessions/{session_id}/anomalies")
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert after == before
```

另测 `recent_limit` 1/20/21、无 session、404、无数据、分页和路径型 `source_reason` 被省略。

- [ ] **Step 2: 运行测试确认 RED**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_workbench_routes.py -q
```

Expected: collection 或路由失败，因为 workbench 模块尚不存在。

- [ ] **Step 3: 增加一次性异常查询**

在 `DBManager` 增加：

```python
def list_session_anomalies(self, session_id: int) -> list[dict[str, Any]]:
    """Return unmatched papers, scan-issue attendance and failed papers without paths."""
```

返回字段固定为 `anomaly_id`、`anomaly_type`、`display_name`、`student_code`、`class_name`、`status`、`detail`、`created_at`。类型只能是 `unmatched_paper`、`scan_issue`、`grading_failed`；用稳定顺序 `anomaly_type, anomaly_id`。`front_image`、`back_image` 和数据库路径永不查询；`detail` 在服务层通过 `sanitize_public_mapping` 过滤。

- [ ] **Step 4: 实现 workbench 服务与响应模型**

```python
class WorkbenchService:
    def __init__(
        self,
        db: DBManager,
        review_service: ReviewApplicationService,
        job_manager: JobManager,
    ) -> None:
        self.db = db
        self.review_service = review_service
        self.job_manager = job_manager

    def overview(self, session_id: int | None, recent_limit: int) -> dict[str, Any]:
        """Aggregate existing read models without internal HTTP calls."""

    def list_anomalies(self, session_id: int) -> list[dict[str, Any]]:
        """Return sanitized stable anomaly rows."""
```

Pydantic 响应结构固定为：

```python
class WorkbenchReviewSummary(BaseModel):
    question_count: int
    item_count: int


class WorkbenchAnomalySummary(BaseModel):
    unmatched_papers: int
    scan_issue_students: int
    failed_papers: int


class RecentSessionSummary(BaseModel):
    session: SessionSummary
    progress: SessionProgress


class WorkbenchOverviewResponse(BaseModel):
    current_session: SessionSummary | None = None
    progress: SessionProgress | None = None
    review: WorkbenchReviewSummary | None = None
    anomalies: WorkbenchAnomalySummary | None = None
    recent_jobs: list[JobSummaryResponse]
    recent_sessions: list[RecentSessionSummary]
    updated_at: str


class SessionAnomalyResponse(BaseModel):
    anomaly_id: str
    anomaly_type: Literal["unmatched_paper", "scan_issue", "grading_failed"]
    display_name: str
    student_code: str | None = None
    class_name: str | None = None
    status: str
    detail: str | None = None
    created_at: str | None = None


class SessionAnomalyListResponse(BaseModel):
    items: list[SessionAnomalyResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
```

概览若未传 `session_id`，`current_session`、`progress`、`review`、`anomalies` 和 `recent_jobs` 均为 `None` 或空列表，只返回最近考试；不得擅自选择考试。复核 `question_count` 是 `needs_review_count > 0` 的现有问题数量，`item_count` 是这些问题的 `needs_review_count` 总和。异常摘要仅复制 progress 的 `unmatched_papers`、`scan_issue_students`、`failed_papers`。

- [ ] **Step 5: 实现两个 GET 路由并运行 GREEN**

概览参数为 `session_id` 可选正整数和 `recent_limit` 1—20 默认 5；异常参数为 `page` 默认 1、`page_size` 默认 20 上限 100、可选 `anomaly_type` 枚举。运行：

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_workbench_routes.py tests\test_api_read_routes.py tests\test_api_review_routes.py tests\test_api_jobs.py -q
```

Expected: 全部通过；API 响应中无路径、payload、result、error 或 raw JSON。

- [ ] **Step 6: 提交工作台聚合**

```powershell
git add -- backend/workbench backend/api/schemas/workbench.py backend/api/routers/workbench.py backend/api/dependencies.py db_manager.py tests/test_api_workbench_routes.py
git diff --cached --check
git commit -m "feat: add readonly workbench aggregation"
```

---

### Task 4: 暴露分析 API 并冻结 OpenAPI

**Files:**

- Create: `backend/api/schemas/analytics.py`
- Create: `backend/api/routers/analytics.py`
- Create: `tests/test_api_analytics_routes.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `tests/test_api_openapi_contract.py`

**Interfaces:**

- Consumes: Task 1 `SessionAnalysisService` 和 Task 3 workbench router。
- Produces: 四个正式 GET 路由已在应用注册：overview、anomalies、question analysis、student analysis；OpenAPI 稳定。

- [ ] **Step 1: 写分析路由失败测试**

```python
def test_question_analysis_filters_class_and_pages(analysis_client):
    client, session_id = analysis_client
    response = client.get(
        f"/api/sessions/{session_id}/analysis/questions",
        params={"class_name": "七年级一班", "page": 1, "page_size": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["class_name"] == "七年级一班"
    assert payload["items"][0]["attempt_count"] > 0
    assert payload["total_pages"] >= 1


def test_student_analysis_encodes_evidence_url(analysis_client):
    client, session_id = analysis_client
    row = client.get(
        f"/api/sessions/{session_id}/analysis/questions/Q1/students"
    ).json()["items"][0]
    assert row["evidence_url"] == (
        f"/api/sessions/{session_id}/results/{row['result_id']}"
        f"/details/{row['detail_id']}/crop"
    )
```

另测 URL 编码题号、无明细、missing max 返回 null、单样本、page 越界返回空 items、session 404 和无路径泄露。

- [ ] **Step 2: 运行分析测试确认 RED**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_analytics_routes.py -q
```

Expected: 404，因为 analytics router 未注册。

- [ ] **Step 3: 实现 schema 和路由**

响应字段固定为：

```python
class AnalysisScope(BaseModel):
    session_id: int
    class_name: str | None = None
    question_id: str | None = None


class QuestionAnalysisItem(BaseModel):
    class_name: str
    question_id: str
    max_score: float | None = None
    score_rate: float | None = None
    average_score: float | None = None
    deduction_count: int
    attempt_count: int
    metric_status: Literal["ready", "missing_max_score", "no_attempts"]


class QuestionAnalysisListResponse(BaseModel):
    scope: AnalysisScope
    classes: list[str]
    items: list[QuestionAnalysisItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class StudentAnalysisItem(BaseModel):
    result_id: int
    detail_id: int
    student_id: int
    student_code: str | None = None
    student_name: str
    class_name: str
    question_id: str
    score_awarded: float
    max_score: float | None = None
    deduction_amount: float | None = None
    deduction_reason: str | None = None
    needs_review: bool
    evidence_url: str
```

两个列表响应都包含 `scope/items/total/page/page_size/total_pages`；问题列表额外包含当前考试真实出现的排序后 `classes`，供前端班级筛选使用。问题路由支持 `class_name` 与 `question_id` 可选筛选；学生路由支持 `class_name`。所有文本 trim，空字符串 422。

- [ ] **Step 4: 最小注册共享入口并冻结 OpenAPI**

在 router exports 和 `create_app()` 增加 analytics/workbench；不得重排其他 router。OpenAPI 测试断言：

```python
expected_gets = {
    "/api/jobs": "JobSummaryListResponse",
    "/api/workbench/overview": "WorkbenchOverviewResponse",
    "/api/sessions/{session_id}/anomalies": "SessionAnomalyListResponse",
    "/api/sessions/{session_id}/analysis/questions": "QuestionAnalysisListResponse",
    "/api/sessions/{session_id}/analysis/questions/{question_id}/students": "StudentAnalysisListResponse",
}
```

对每条路径验证 GET 200 schema `$ref`；保留现有 OpenAPI 快照。

- [ ] **Step 5: 运行后端包级回归并提交**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_session_analysis_service.py tests\test_api_analytics_routes.py tests\test_api_workbench_routes.py tests\test_api_jobs.py tests\test_api_review_routes.py tests\test_api_graph_routes.py tests\test_api_openapi_contract.py tests\test_api_performance_metrics.py -q
git add -- backend/api/schemas/analytics.py backend/api/routers/analytics.py backend/api/dependencies.py backend/api/routers/__init__.py backend/api/app.py tests/test_api_analytics_routes.py tests/test_api_openapi_contract.py
git diff --cached --check
git commit -m "feat: expose workbench analysis APIs"
```

Expected: 全部通过；共享入口 diff 仅包含新增 import/include，不覆盖 P2-09 内容。

---

### Task 5: 建立前端严格客户端与独立加载状态

**Files:**

- Create: `frontend/src/api/workbench.ts`
- Create: `frontend/src/api/analysis.ts`
- Create: `frontend/src/api/graph.ts`
- Create: `frontend/src/stores/workbench.ts`
- Create: `frontend/src/stores/analysis.ts`
- Create: `frontend/src/api/__tests__/workbench.spec.ts`
- Create: `frontend/src/api/__tests__/analysis.spec.ts`
- Create: `frontend/src/__tests__/workbench-store.spec.ts`
- Create: `frontend/src/__tests__/analysis-store.spec.ts`

**Interfaces:**

- Consumes: Task 2—4 JSON 契约和现有 `apiClient`。
- Produces: `fetchWorkbenchOverview`、`fetchSessionAnomalies`、`fetchQuestionAnalysis`、`fetchStudentAnalysis`、`fetchGraphRows`、`fetchGraphEvidence`、`useWorkbenchStore`、`useAnalysisStore`。

- [ ] **Step 1: 安装锁定依赖并写解码 RED 测试**

执行时先调用 workspace dependency locator，让其 Node bin 在当前命令环境可用，再在 `frontend/` 运行：

```powershell
npm ci --ignore-scripts
npm run test -- src/api/__tests__/workbench.spec.ts src/api/__tests__/analysis.spec.ts
```

新测试必须拒绝：未知 Job 状态、NaN/Infinity、负计数、非 `/api/` evidence URL、`total` 与 items 不一致、Graph `diagnosis_identity` 非 `question_tag`、响应夹带路径作为必需字段。Expected: import 失败，因为三个 API 模块不存在。

- [ ] **Step 2: 实现完整 TypeScript 类型和运行时解码**

公共类型必须与 Pydantic 一一对应。分析核心类型：

```typescript
export type MetricStatus = 'ready' | 'missing_max_score' | 'no_attempts'

export interface QuestionAnalysisItem {
  class_name: string
  question_id: string
  max_score: number | null
  score_rate: number | null
  average_score: number | null
  deduction_count: number
  attempt_count: number
  metric_status: MetricStatus
}

export interface StudentAnalysisItem {
  result_id: number
  detail_id: number
  student_id: number
  student_code: string | null
  student_name: string
  class_name: string
  question_id: string
  score_awarded: number
  max_score: number | null
  deduction_amount: number | null
  deduction_reason: string | null
  needs_review: boolean
  evidence_url: string
}

export interface AnalysisScope {
  session_id: number
  class_name: string | null
  question_id: string | null
}

export interface QuestionAnalysisResponse {
  scope: AnalysisScope
  classes: string[]
  items: QuestionAnalysisItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface StudentAnalysisResponse {
  scope: AnalysisScope
  items: StudentAnalysisItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface WorkbenchOverview {
  current_session: SessionSummary | null
  progress: SessionProgress | null
  review: { question_count: number; item_count: number } | null
  anomalies: {
    unmatched_papers: number
    scan_issue_students: number
    failed_papers: number
  } | null
  recent_jobs: JobSummary[]
  recent_sessions: RecentSessionSummary[]
  updated_at: string
}

export interface SessionProgress {
  total_papers: number
  matched_papers: number
  unmatched_papers: number
  graded_papers: number
  failed_papers: number
  grading_papers: number
  needs_human_review: number
  absent_students: number
  scan_issue_students: number
  progress_percent: number
}

export interface JobSummary {
  id: number
  job_type: string
  status: JobStatus
  progress: number
  stage: string
  detail: string
  created_at: string
  started_at: string | null
  updated_at: string
  finished_at: string | null
}

export interface RecentSessionSummary {
  session: SessionSummary
  progress: SessionProgress
}

export interface SessionAnomaly {
  anomaly_id: string
  anomaly_type: 'unmatched_paper' | 'scan_issue' | 'grading_failed'
  display_name: string
  student_code: string | null
  class_name: string | null
  status: string
  detail: string | null
  created_at: string | null
}

export interface SessionAnomalyResponse {
  items: SessionAnomaly[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface GraphNode {
  knowledge_key: string
  knowledge_label: string
  student_count: number
  item_count: number
  deduction_count: number
  average_mastery: number
}

export interface GraphRowsResponse {
  nodes: GraphNode[]
  coverage: { covered_items: number; total_items: number; missing_items: Record<string, string> }
  warnings: string[]
  diagnosis_identity: 'question_tag'
}
```

所有请求使用 `URLSearchParams` 构造已知查询值，`question_id` 使用 `encodeURIComponent`，Graph 只接受当前考试和已选择班级：

```typescript
const body = {
  scope: { mode: 'class' as const, class_id: className },
  exam_scope: { mode: 'current' as const, session_ids: [sessionId] },
}
```

- [ ] **Step 3: 写 Store 旧请求与最后成功内容 RED 测试**

```typescript
it('keeps the last successful overview when refresh fails', async () => {
  const store = useWorkbenchStore()
  await store.loadOverview(7, async () => overview7)
  await store.loadOverview(7, async () => { throw new Error('private failure') })
  expect(store.overview).toEqual(overview7)
  expect(store.overviewState).toBe('stale-error')
  expect(store.overviewError).toBe('工作台数据暂时无法更新')
})

it('ignores the old session response after switching exams', async () => {
  const store = useAnalysisStore()
  const old = deferred<QuestionAnalysisResponse>()
  void store.loadQuestions(7, null, () => old.promise)
  await store.loadQuestions(8, null, async () => session8Questions)
  old.resolve(session7Questions)
  await old.promise
  expect(store.sessionId).toBe(8)
  expect(store.questions).toEqual(session8Questions.items)
})
```

另测 overview、anomalies、questions、students、graph 各自独立状态和重试；Store 不调用 localStorage。

- [ ] **Step 4: 实现两个 Store**

状态枚举固定为 `idle | loading | ready | empty | stale-error | error`。每个资源拥有独立 `AbortController` 和递增 generation；切换 session 立即 abort 并清除旧考试可见内容，刷新同一 session 失败保留最后成功值与 ISO `updatedAt`。对外方法：

```typescript
export type OverviewLoader = (
  sessionId: number | null,
  signal: AbortSignal,
) => Promise<WorkbenchOverview>
export type AnomalyLoader = (
  sessionId: number,
  signal: AbortSignal,
) => Promise<SessionAnomalyResponse>
export type QuestionLoader = (
  sessionId: number,
  className: string | null,
  signal: AbortSignal,
) => Promise<QuestionAnalysisResponse>
export type StudentLoader = (
  sessionId: number,
  questionId: string,
  className: string | null,
  signal: AbortSignal,
) => Promise<StudentAnalysisResponse>
export type GraphLoader = (
  sessionId: number,
  className: string,
  signal: AbortSignal,
) => Promise<GraphRowsResponse>

loadOverview(sessionId: number | null, loader?: OverviewLoader): Promise<void>
loadAnomalies(sessionId: number, loader?: AnomalyLoader): Promise<void>
loadQuestions(sessionId: number, className: string | null, loader?: QuestionLoader): Promise<void>
loadStudents(sessionId: number, questionId: string, className: string | null, loader?: StudentLoader): Promise<void>
loadGraph(sessionId: number, className: string, loader?: GraphLoader): Promise<void>
resetForSession(sessionId: number | null): void
```

`loadGraph` 在 className 空白时不请求，状态为 `idle`；页面显示“选择班级后查看标签覆盖”。

- [ ] **Step 5: 运行前端数据层 GREEN 并提交**

```powershell
npm run test -- src/api/__tests__/workbench.spec.ts src/api/__tests__/analysis.spec.ts src/__tests__/workbench-store.spec.ts src/__tests__/analysis-store.spec.ts
npm run typecheck
git add -- src/api/workbench.ts src/api/analysis.ts src/api/graph.ts src/stores/workbench.ts src/stores/analysis.ts src/api/__tests__/workbench.spec.ts src/api/__tests__/analysis.spec.ts src/__tests__/workbench-store.spec.ts src/__tests__/analysis-store.spec.ts
git diff --cached --check
git commit -m "feat: add workbench frontend data layer"
```

Expected: 目标 Vitest 和 typecheck 通过，无生产依赖变化。

---

### Task 6: 实现精修方案 B 工作台组件

**Files:**

- Create: `frontend/src/components/workbench/WorkbenchProgressRail.vue`
- Create: `frontend/src/components/workbench/RecentSessions.vue`
- Create: `frontend/src/components/analysis/QuestionAnalysisPanel.vue`
- Create: `frontend/src/components/analysis/QuestionAnalysisTable.vue`
- Create: `frontend/src/components/analysis/StudentAnalysisDetail.vue`
- Create: `frontend/src/components/analysis/TagCoverageSummary.vue`
- Create: `frontend/src/views/WorkbenchView.vue`
- Create: `frontend/src/styles/workbench.css`
- Create: `frontend/src/__tests__/workbench-view.spec.ts`
- Modify: `frontend/src/main.ts`

**Interfaces:**

- Consumes: Task 5 Stores、现有 session Store、`/grading` 路由和受控 evidence URL。
- Produces: 可读、可键盘操作、五视口稳定的完整 WorkbenchView；不包含写动作。

- [ ] **Step 1: 写可见结构与动作 RED 测试**

挂载 `WorkbenchView`，注入合成 overview/questions/students/graph。必须断言：

```typescript
expect(host.querySelector('h1')?.textContent).toBe('工作台')
expect(host.querySelector('[data-testid="progress-action-rail"]')).not.toBeNull()
expect(host.textContent).toContain('本题基于 12 份已批改作答')
expect(host.textContent).toContain('班级题目分析')
expect(host.textContent).toContain('最近考试')
expect(host.textContent).toContain('知识标签覆盖')
expect(host.querySelectorAll('[data-metric-card]').length).toBe(0)
```

点击“去复核”后断言当前 session Store 仍为 7 且路由为 `/grading`；从具体题目点击“进入评分复核”时路由为 `/grading?question=Q1`。点击题目后加载学生明细；点击最近考试调用 `sessionStore.selectSession`；Job 区域不存在取消/重试/提交按钮。

- [ ] **Step 2: 写空态、部分失败和单样本 RED 测试**

覆盖无当前考试、未批改、确实无复核、确实无异常、overview stale-error、analysis error 但 overview 可用、Graph 失败但题目表可用、`attempt_count === 1`。文案固定：

```text
请选择考试后查看工作台
当前考试还没有已批改题目
当前没有待复核项目
当前没有异常记录
数据可能不是最新 · 上次更新 <time>
分析数据暂时无法读取；工作台其他内容仍可使用
当前仅 1 份已批改作答，不代表整体情况
```

- [ ] **Step 3: 实现进度—行动带与最近考试**

`WorkbenchProgressRail` 使用语义 `<ol>`，四个节点为批改进度、待复核、异常记录、最近任务。每个节点只接收已有值、状态说明和一个事件；未知值显示“暂不可用”，不显示 0。1024px 由 CSS 改为垂直基线。`RecentSessions` 用按钮切换 session，显示考试名、已有状态和更新时间，不显示排名或趋势。

组件事件固定为：

```typescript
defineEmits<{
  openGrading: []
  openReview: []
  openAnomalies: []
  openJobs: []
}>()
```

- [ ] **Step 4: 实现题目分析、学生明细和标签证据**

题目得分率用可聚焦 `<button>` 包裹的原生 CSS 横条，宽度只在 `score_rate !== null` 时使用 `clamp(0%, rate%, 100%)`；按钮可见文字同时包含题号、百分比或“无法计算”、样本数。`QuestionAnalysisTable` 使用真实 `<table>`，选中行 `aria-current="true"`。

学生明细只显示姓名/学号/班级、得分/满分、扣分与原因、“查看答卷证据”和“进入评分复核”；证据链接必须以 `/api/` 开头并在当前标签打开。Tag 区只在选定班级后请求 Graph rows，显示 coverage 的 covered/total、warnings、node label/item_count/deduction_count；选择 node 后请求 evidence 并显示安全明细，不绘制关系图。

题目条和证据动作使用下列语义结构，不用 div 模拟按钮：

```vue
<button
  type="button"
  class="question-rate"
  :aria-pressed="item.question_id === selectedQuestionId"
  @click="$emit('select', item.question_id)"
>
  <span class="question-rate__label">{{ item.question_id }}</span>
  <span class="question-rate__track" aria-hidden="true">
    <span
      v-if="item.score_rate !== null"
      class="question-rate__fill"
      :style="{ inlineSize: `${Math.min(100, Math.max(0, item.score_rate))}%` }"
    />
  </span>
  <span>{{ item.score_rate === null ? '无法计算' : `${item.score_rate}%` }}</span>
  <span>{{ item.attempt_count }} 份</span>
</button>
```

- [ ] **Step 5: 编排 WorkbenchView 和独立重试**

监听 `sessionStore.selectedSessionId`；切换时调用两个 Store 的 `resetForSession`，并行启动 overview/questions，Graph 等待班级选择。overview 动作滚动/聚焦对应区块或进入已实现路由；异常与 Job 在当前页展开只读列表。每个失败区块有自己的“重新加载”按钮，刷新不得触发 POST。

```typescript
watch(
  () => sessionStore.selectedSessionId,
  async (sessionId) => {
    workbenchStore.resetForSession(sessionId)
    analysisStore.resetForSession(sessionId)
    await workbenchStore.loadOverview(sessionId)
    if (sessionId !== null) {
      await analysisStore.loadQuestions(sessionId, null)
    }
  },
  { immediate: true },
)
```

- [ ] **Step 6: 实现精修方案 B 样式**

`workbench.css` 只使用现有 CSS Variables。主内容最大宽度使用现有 `--content-max-width`；进度—行动带为唯一强调结构；内容区用细分隔而非每块独立阴影卡。数字使用 `font-variant-numeric: tabular-nums`。唯一动画为 160ms opacity，`prefers-reduced-motion` 归零。禁止新增任意十六进制颜色。

```css
.workbench-progress-rail {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0;
  padding: var(--space-4) 0;
  border-block: var(--border-width) solid var(--color-border-default);
  list-style: none;
}

.workbench-progress-rail__value {
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1100px) {
  .workbench-progress-rail {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .workbench-view__content {
    animation: none;
  }
}
```

- [ ] **Step 7: 运行组件测试、lint、typecheck 并提交**

```powershell
npm run test -- src/__tests__/workbench-view.spec.ts src/__tests__/workbench-store.spec.ts src/__tests__/analysis-store.spec.ts
npm run lint
npm run typecheck
git add -- src/components/workbench src/components/analysis src/views/WorkbenchView.vue src/styles/workbench.css src/main.ts src/__tests__/workbench-view.spec.ts
git diff --cached --check
git commit -m "feat: build actionable workbench overview"
```

Expected: 组件测试、lint 和 typecheck 全部通过；锁文件无差异。

---

### Task 7: 接入真实路由、导航与恢复目标

**Files:**

- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/shell/AppTopbar.vue`
- Modify: `frontend/src/components/ApplicationErrorBoundary.vue`
- Modify: `frontend/src/views/NotFoundView.vue`
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`
- Modify: `frontend/src/__tests__/App.spec.ts`
- Modify: `frontend/src/components/shell/__tests__/app-shell.spec.ts`

**Interfaces:**

- Consumes: Task 6 `WorkbenchView` 与现有 `ReviewQueueView`。
- Produces: `/` → `/workbench`，真实导航仅含“工作台/评分复核”，错误和 404 安全返回工作台。

- [ ] **Step 1: 更新路由测试并确认 RED**

```typescript
expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
  ['workbench', '工作台', '/workbench'],
  ['grading', '评分复核', '/grading'],
])
```

实际异步 push `/` 后断言 `/workbench`；`/workbench` 组件名为 `WorkbenchView`；删除“workbench 必须 404”的旧断言，继续保证 `/students`、`/analytics`、`/question-bank` 未实现时为 404。

- [ ] **Step 2: 运行目标测试确认 RED**

```powershell
npm run test -- src/__tests__/navigation-router.spec.ts src/__tests__/App.spec.ts src/components/shell/__tests__/app-shell.spec.ts
```

Expected: 当前默认 `/grading` 和旧恢复文案导致失败。

- [ ] **Step 3: 实现最小共享接线**

路由定义：

```typescript
export type WorkspaceRouteId = 'workbench' | 'grading'

export const workbenchRouteDefinition = {
  id: 'workbench',
  label: '工作台',
  path: '/workbench',
  title: '工作台',
  description: '查看当前考试进度、待处理事项与学情分析',
  breadcrumb: '工作台',
} as const
```

AppTopbar 在 identity 与 session 之间渲染两个真实 RouterLink；当前链接使用 `aria-current="page"`。ApplicationErrorBoundary 和 NotFound 的按钮统一 `router.push('/workbench')`，文案为“返回工作台”。

- [ ] **Step 4: 运行共享前端回归并提交**

```powershell
npm run test -- src/__tests__/navigation-router.spec.ts src/__tests__/App.spec.ts src/components/shell/__tests__/app-shell.spec.ts src/__tests__/review-queue-view.spec.ts
npm run lint
npm run typecheck
npm run build
git add -- src/navigation.ts src/router/index.ts src/components/shell/AppTopbar.vue src/components/ApplicationErrorBoundary.vue src/views/NotFoundView.vue src/__tests__/navigation-router.spec.ts src/__tests__/App.spec.ts src/components/shell/__tests__/app-shell.spec.ts
git diff --cached --check
git commit -m "feat: route the truthful workbench entry"
```

Expected: 前端回归与构建通过。交接说明把这批文件列为 integration 冲突热点，P2-09 内容必须同时保留。

---

### Task 8: 完成浏览器验收证据与包级验证

**Files:**

- Create: `frontend/e2e/workbench-overview.spec.ts`
- Create after implementation: `docs/user-testing/checkpoints/P2-14-v1.5.0-workbench-analysis-quick-check.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md`

**Interfaces:**

- Consumes: Tasks 1—7 完整候选。
- Produces: 五视口、部分失败、下钻、可访问性、只读守卫、独立复审和 quick 用户验收所需证据。

- [ ] **Step 1: 写 Playwright 合成路由与五视口测试**

`workbench-overview.spec.ts` 拦截 sessions、overview、jobs、anomalies、analysis questions/students、graph rows/evidence；全部数据为匿名固定 JSON。每个视口断言：页面非空、无横向页面溢出、进度—行动带可见、题目按钮/表格/文字摘要一致、Tab 可到达动作、颜色不是唯一状态、1024px 行动带为纵向。

```typescript
const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]
```

另测 overview 200 + analysis 503、overview 503 + retained stale fixture、快速从 session 7 切到 8、选择班级后才发 Graph、题目/学生/证据下钻，以及所有拦截到的请求方法均为 GET 或既有 Graph POST，绝不出现 Job/评分写 POST。

- [ ] **Step 2: 运行浏览器测试并检查截图**

```powershell
npm run build
npx playwright test e2e/workbench-overview.spec.ts --project=chromium
```

Expected: 全部通过，无 pageerror/console error。保留测试输出目录外的受控截图证据时只使用合成数据；用应用截图查看器人工确认 1024、1366、1920 三档没有卡片海洋、渐变、遮挡和空白像素。

- [ ] **Step 3: 运行完整包内验证**

在仓库根调用便携 Python，在 `frontend/` 调用 Node 工具：

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_session_analysis_service.py tests\test_api_analytics_routes.py tests\test_api_workbench_routes.py tests\test_api_jobs.py tests\test_api_read_routes.py tests\test_api_review_routes.py tests\test_api_graph_routes.py tests\test_api_openapi_contract.py tests\test_api_performance_metrics.py tests\test_frontend_api_client.py tests\test_frontend_app_shell.py tests\test_documentation_governance.py -q
Push-Location frontend
npm run lint
npm run typecheck
npm run test
npm run build
npx playwright test e2e/workbench-overview.spec.ts --project=chromium
Pop-Location
& '..\..\runtime\python\python.exe' tools\smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: 聚焦/受影响 pytest、前端 lint/typecheck/unit/build/browser 和快速冒烟全部通过；`user_data` 无输出。功能分支不默认跑全量 pytest；integration 波次结束按风险规则运行完整门槛。

- [ ] **Step 4: 更新架构事实和生成真实短测卡**

`ARCHITECTURE.md` 只记录已实现事实：共享只读分析服务、五个 GET、Job 安全摘要列表、Vue 工作台、Graph 复用、无 Schema/写语义。短测卡在上述浏览器验证完成后才写，包含候选 SHA、合成数据启动方式、URL `/workbench`、关闭方式和 5—10 分钟检查：进度—行动带、考试切换、题目/学生下钻、部分失败恢复、1024px 可读性；不要求用户接触命令或真实数据。

- [ ] **Step 5: 记录 `waiting_review` 并创建功能提交**

把交接块改为 `waiting_review / branch_head / passed / pending / pending / unchanged / report_only`，保持 Stash 基线不变。暂存明确的 P2-14 文件，确认无 `user_data`、构建产物、截图缓存、node_modules、真实导出或临时数据库后提交：

```powershell
git add -- ARCHITECTURE.md docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md docs/user-testing/checkpoints/P2-14-v1.5.0-workbench-analysis-quick-check.md frontend/e2e/workbench-overview.spec.ts
git diff --cached --check
git commit -m "docs: prepare P2-14 review evidence"
& '..\..\runtime\python\python.exe' tools\handoff_status.py --plan docs/superpowers/plans/2026-07-15-p2-14-workbench-analysis-overview-implementation.md --repo .
```

Expected: 验证器在干净 worktree 输出 `ok=true`；状态停在 `waiting_review`，不得自报复审或用户验收通过。

- [ ] **Step 6: 独立复审、用户短测与最终计划提交**

独立复审范围为 `origin/main..HEAD`，重点检查旧公式等价、null/0 区分、Job/异常脱敏、请求世代、Graph scope、共享导航冲突和写请求缺失。Critical/Important 必须为 0；修复必须新增 RED 测试并重跑影响面。

复审通过后创建只改本计划的 `waiting_user` 锚点，`功能提交` 记录该锚点直接父提交的完整已复审功能 SHA；用户只测试这个功能 SHA。用户确认通过后，创建只改唯一 P2-14 短测卡的证据提交，再创建只改本计划的最终交接提交；最终块继续记录同一个已复审功能 SHA，字段组合为 `verified_pending_integration / passed / passed / passed / unchanged / independent_candidate_allowed`。运行 handoff validator 并要求 `ok=true`。

---

## Integration Order and Conflict Rules

1. integration 从当时最新 `origin/main` 创建；先核对 P2-09 是否已合并或仍是并行候选。
2. 若 P2-09 已完成，先合入 P2-09，再合入 P2-14；若 P2-14 先完成，只能先合入无冲突的服务/组件提交，动态状态仍由 integration 最终统一。
3. `backend/api/app.py`、router exports、`frontend/src/navigation.ts`、router、AppTopbar、ApplicationErrorBoundary、NotFound 和全局测试不得整文件选择一侧；同时保留所有已实现真实入口。
4. 每合入一个包立即运行该包聚焦回归；波次全部合入后运行一次完整 `tools/smoke_check.py`、前端 lint/typecheck/unit/build/browser 和真实两库指纹守卫。
5. integration 统一更新 Index：只有主线包含完整功能和验证证据时，P2-14 才能标为 `merged`；P2-15 只能在其全部依赖进入最新 `origin/main` 后变为 `ready`。
6. 推送 integration 并通过 PR 合并 GitHub `main`；禁止直接 push main、force push、真实数据写入或丢弃未合并历史。

## Rollback and Stop Conditions

- 任一实现要求数据库迁移、新统计口径、写操作、真实模型调用或真实数据测试时立即停止并重新评审包边界。
- 公式等价测试、脱敏测试、旧响应隔离、前端构建、浏览器测试、快速冒烟、handoff validator 或真实两库指纹任一失败时保留 worktree，不进入 integration。
- 回退删除新增 Vue 路由/组件/样式、只读 router/service/schema 和对应测试；保留现有 sessions/review/jobs/graph、Streamlit 页面和数据库。无 Schema 或业务写入，因此不需要数据恢复。
- P2-09 共享文件发生无法机械合并的业务语义冲突时停止，由 integration 按最新已批准规格逐项解决，不在功能分支猜测覆盖。

## Plan Self-Review

- 规格覆盖：工作台概览、现有分析公式、Job 安全列表、异常明细、Graph 复用、动作下钻、独立加载、旧请求、最后成功内容、null/0、小样本、可访问性、五视口、只读数据安全、并行所有权、回退和 quick 用户验收均有对应任务。
- 类型一致：后端 `QuestionAnalysisItem` / `StudentAnalysisItem` 与前端同名字段一致；Store 方法、组件数据和 E2E mock 使用同一 snake_case API 契约。
- 依赖一致：Task 1 先提供公式服务，Task 2 提供 Job list，Task 3 聚合概览，Task 4 注册接口，Task 5 建立客户端状态，Task 6 组件，Task 7 共享接线，Task 8 验证交接。
- 范围一致：零迁移、零写动作、零新 KPI、零新依赖；P2-15/P2-11/P2-09 能力没有提前实现。
