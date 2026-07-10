# P1-12 Review Query Service and Atomic Write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 review router 收敛为薄壳，用固定查询次数取得整场题目复核数据，并保证一次多结果确认的评分明细与总分写入全部成功或全部回滚；批注文件失败必须返回可重试的明确补偿结果。

**Architecture:** 新增 `backend.review.service.ReviewApplicationService`，集中保留当前“已复核/需复核/低于 80 置信度”的判定、rubric score map、复核行构造、汇总和确认校验。`DBManager` 提供一次 JOIN 查询与一个跨 result 的 SQLite 事务写入原语；`ManualReviewService` 在事务成功后逐结果重绘批注，并把每个结果的 `succeeded/retry_required` 状态返回给应用服务。FastAPI router 只负责依赖注入、schema 转换和领域错误到 HTTP 错误的映射。

**Tech Stack:** Python 3.12、SQLite、FastAPI/Pydantic v2、pytest、现有 `DBManager`、`ManualReviewService` 与批注渲染器。

## Global Constraints

- 不新增复核状态表、数据库列或 migration；不改变现有“已复核/人工复核不再待复核、需复核标记、置信度低于 80 待复核”口径。
- 不改变现有 review URL、请求字段和既有响应字段；仅为确认响应追加向后兼容的 `annotation_outcomes`。
- GET 复核端点不得再调用 `get_session_results()` 后逐 result 调 `get_result_details()`；除会话存在性查询外，复核数据必须由一次 JOIN 查询取得。
- 确认前必须校验 detail 同时属于当前 session、请求 result 和路径 question，并校验分数为非负有限值且不超过 rubric 中该题满分。
- 所有 detail 元数据更新与所有受影响 result 总分重算必须使用同一 SQLite 连接和事务；任一 UPDATE/重算失败必须回滚整批。
- 批注重绘只在数据库事务提交后执行。单个批注失败不回滚已经原子提交的业务数据，而返回 `retry_required`；重复提交同一确认是补偿重试入口。
- API 不向客户端暴露批注文件绝对路径或原始异常详情；受控媒体 URL 留给 P1-13。
- 测试只使用 `tmp_path`、临时 SQLite、临时 rubric 和 monkeypatch；不得读取或写入真实 `user_data/`。
- 当前工作区包含 P1-09 至 P1-11 与 WP1.2/WP1.3 未提交改动；只做 P1-12 增量，不覆盖或回退既有修改。
- 按用户要求，本计划不 commit、不 push、不创建 PR，也不暂存任何文件。

---

### Task 1: 固定查询次数的复核读模型

**Files:**
- Create: `backend/review/__init__.py`
- Create: `backend/review/service.py`
- Create: `tests/test_review_application_service.py`
- Modify: `db_manager.py`

**Interfaces:**
- Produces: `DBManager.get_session_review_rows(session_id: int) -> list[dict[str, Any]]`。
- Produces: `ReviewApplicationService.list_items(session_id, session) -> list[ReviewItem]`。
- Produces: `ReviewApplicationService.list_questions(session_id, session) -> list[ReviewQuestion]`。
- Preserves: review marker、confidence、candidate score、排序和 max score 现有 API 语义。

- [x] **Step 1: 写大班级固定查询上限与判定口径失败测试**

构造至少 60 个 result、每个两个 detail；直接调用应用服务前给 `DBManager._connect` 计数，并禁止旧的逐 result 查询方法被调用：

```python
def test_large_review_class_uses_one_join_query(...):
    monkeypatch.setattr(db, "get_session_results", fail_old_path)
    monkeypatch.setattr(db, "get_result_details", fail_old_path)
    rows = service.list_items(session_id, session)
    assert len([row for row in rows if row.question_id == "Q1"]) == 60
    assert connect_count == 1  # one joined review query
```

同时覆盖：`已复核/人工复核` 优先排除、显式 `需复核` 标记命中、无标记但置信度 `79.9` 命中、`80` 不命中，以及 raw_json `detail_metadata`/`candidate_scores` 保留。

- [x] **Step 2: 运行读模型测试确认 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_review_application_service.py tests\test_api_review_routes.py -q
```

Expected: FAIL；`backend.review.service` 和批量查询尚不存在，现有 router 仍执行 N+1。

- [x] **Step 3: 实现一次 JOIN 查询**

`get_session_review_rows()` 只执行一次 SELECT，连接 `session_results`、`students`、`exam_papers`、`session_details`，返回 result/student/detail/raw_json 所需列；按 result ID 缓存 raw_json 解析，避免同一学生每题重复解析。

```sql
SELECT sr.id AS result_id, s.student_code, s.name AS student_name,
       s.class_name, ep.ocr_name, sr.raw_json,
       sd.id AS detail_id, sd.question_id, sd.score_awarded,
       sd.deduction_reason, sd.error_category, sd.error_summary,
       sd.confidence_score
FROM session_results sr
JOIN students s ON s.id = sr.student_id
JOIN exam_papers ep ON ep.id = sr.paper_id
JOIN session_details sd ON sd.result_id = sr.id
WHERE sr.session_id = ?
ORDER BY sr.id, sd.id
```

- [x] **Step 4: 提取应用服务读逻辑**

服务内使用 dataclass 读模型，不依赖 FastAPI/Pydantic；迁移并保持 `_load_score_map`、`_detail_metadata_for_qid`、`_is_substantive_review_reason`、自然题号排序和空值清理的行为。应用服务只读取一次 `get_session_review_rows()`。

- [x] **Step 5: 验证读模型 GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_review_application_service.py -q
```

---

### Task 2: 多结果确认的单事务写入

**Files:**
- Modify: `tests/test_review_application_service.py`
- Create: `tests/test_manual_review_atomic.py`
- Modify: `db_manager.py`
- Modify: `backend/review/service.py`

**Interfaces:**
- Produces: `DBManager.apply_session_review_adjustments(session_id, adjustments) -> dict[str, int]`。
- Produces: `ReviewConfirmationInput` 与 `ReviewApplicationService.prepare_adjustments(session_id, session, question_id, items) -> list[dict[str, Any]]`。
- Produces: `ReviewDetailNotFoundError` 与 `ReviewValidationError` 领域错误。
- Validates: session/result/question/detail 四元所属关系、detail 去重、有限非负分数和 rubric 满分上限。

- [x] **Step 1: 写所属关系、分数范围和重复 detail 失败测试**

覆盖 detail 属于其他 session、result ID 与 detail 不匹配、路径 question 与 detail 不匹配、`NaN/Inf`、负数、超过满分和同一 detail 重复提交；断言数据库保持原值。

- [x] **Step 2: 写第二个 result UPDATE 失败时整批回滚测试**

对第二个 result 的 detail 建临时 SQLite trigger：

```sql
CREATE TRIGGER fail_second_review_update
BEFORE UPDATE ON session_details
WHEN OLD.id = <second_detail_id>
BEGIN
    SELECT RAISE(ABORT, 'forced second review write failure');
END;
```

直接调用 DB 原子写原语提交两个 result 的调整，断言抛错、第一和第二 detail 的 score/reason/category/summary 均未变化，两个 `session_results.student_score` 也未变化。批注未启动与补偿协议由 Task 3 在接入 `ManualReviewService` 后验证。

- [x] **Step 3: 运行原子写测试确认 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_manual_review_atomic.py tests\test_review_application_service.py -q
```

Expected: FAIL；当前只存在逐 detail 独立提交和逐 result 重算。

- [x] **Step 4: 实现事务原语与应用层预校验**

应用服务的 `prepare_adjustments()` 从已加载 review rows 建 detail lookup，完成全部请求校验并返回带 session/result/question/detail 所属信息的规范化调整；Task 3 再把它交给 `ManualReviewService`。DB 在同一连接内重新查询并校验 session/result/question 所属关系，依次更新四个可变字段，重算全部受影响 result 总分，最后只 commit 一次；任何异常显式 rollback 后原样抛出。

```python
try:
    # validate ownership, UPDATE every detail, recalculate affected totals
    conn.commit()
except Exception:
    conn.rollback()
    raise
```

- [x] **Step 5: 验证原子写 GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_manual_review_atomic.py tests\test_review_application_service.py -q
```

---

### Task 3: 事务后批注补偿与薄 router

**Files:**
- Modify: `backend/review/service.py`
- Modify: `manual_review_service.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/review.py`
- Modify: `backend/api/schemas/review.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `tests/test_api_review_routes.py`
- Modify: `tests/test_manual_review_atomic.py`

**Interfaces:**
- Produces: `ManualReviewService.apply_review_adjustments(session_id, adjustments, highlight_qids) -> dict[str, Any]`。
- Produces: `ReviewApplicationService.confirm(session_id, session, question_id, items) -> ReviewConfirmationResult`，组合 Task 2 预校验与 `ManualReviewService`。
- Produces: `get_review_application_service()` FastAPI dependency。
- Extends: `ReviewConfirmResponse.annotation_outcomes`，每项仅包含 `result_id`、`status` 与可选的稳定 `message`。
- HTTP mapping: 未找到/所属关系错误继续使用 `review_detail_not_found` 404；无效分数使用稳定 422 领域错误。

- [x] **Step 1: 写批注失败补偿失败测试**

让第一个 result 的 `render_result_annotation()` 抛错、第二个成功；断言数据库两组调整都已提交，响应分别为 `retry_required` 与 `succeeded`，失败消息不含本机路径或原始异常文本。再次提交同一请求并让渲染成功，断言补偿状态变为 `succeeded`。

- [x] **Step 2: 写 API 兼容与薄壳失败测试**

更新既有确认测试以 override `get_review_application_service`；保留 URL、请求字段、`updated_details/updated_results` 断言，并增加 `annotation_outcomes`。增加超过满分、错误 detail 所属和批注重试结果的 HTTP 契约测试；对 60 个 result 的 API GET 断言 `_require_session` 加单次 JOIN 总共不超过两个 DB 连接。

- [x] **Step 3: 实现批注补偿协议**

`ManualReviewService.apply_review_adjustments()` 先调用单事务 DB 原语；事务成功后按 result ID 排序渲染。成功返回 `succeeded`；异常或 `None` 返回稳定的 `retry_required`，允许客户端原样重放确认请求重试批注。不得返回 annotated path。

- [x] **Step 4: 收敛依赖与 router**

`get_review_application_service()` 组合同一个 `DBManager` 与 `ManualReviewService`。router 三个 handler 只做：require session → 调用应用服务 → schema 转换；确认 handler 仅保留领域错误到 `ApiError` 的映射，不再构建 score map、遍历 result 或分组提交。

- [x] **Step 5: 运行 review 聚焦回归**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_review_routes.py tests\test_review_application_service.py tests\test_manual_review_atomic.py tests\test_report_score_adjustment.py -q
```

Expected: PASS；既有 review/report 手工调分契约兼容。

---

### Task 4: 文档、范围审计与分层验证

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
- Modify: this plan

- [x] **Step 1: 更新架构和 P1-12 执行证据**

记录新 review 应用服务、单 JOIN 读模型、SQLite 原子写边界和事务后批注补偿；将 P1-12 标为 `verified`，把当前队列推进到 P1-13，并修正 Phase 1 剩余包数量。

- [x] **Step 2: 做占位符、路由重逻辑和数据范围审计**

Run:

```powershell
rg -n "TODO|FIXME|NotImplemented|placeholder|pass$" backend/review backend/api/routers/review.py tests/test_review_application_service.py tests/test_manual_review_atomic.py
rg -n "get_session_results|get_result_details|apply_manual_adjustments" backend/api/routers/review.py
git diff --check
git status --short -- user_data
```

Expected: 新增范围无占位实现；router 不再包含旧 N+1/逐 result 写逻辑；`git diff --check` 通过；`user_data` 状态与开工基线一致。

- [x] **Step 3: API 回归**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_*routes.py tests\test_api_jobs.py -q
```

- [x] **Step 4: 快速冒烟**

Run:

```powershell
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

- [x] **Step 5: 全量回归**

Run:

```powershell
runtime\python\python.exe -m pytest -q
```

- [x] **Step 6: 复核最终 diff 与证据**

确认只存在 P1-12 预期增量和此前已知未提交改动；记录测试通过数、查询上限、回滚与补偿证据。按用户要求停止在未暂存、未提交、未推送状态。

## Completion Evidence

- P1-12 聚焦回归：35 passed。
- API 显式文件列表回归：35 passed。
- 快速冒烟：静态编译 324 个第一方 Python 文件；两库临时副本初始化幂等且 `integrity_check=ok`。
- 全量回归：823 passed / 0 skipped / 0 failed。
- 范围审计：router 旧 N+1/逐 result 写入符号零命中；`git diff --check` 通过；`user_data` Git 状态保持 205 行，SHA-256 指纹保持 `4f84005eb0296f61524f291f85268f3569c40dcb6f279154ff93ac5b200e3272`。
- 状态：P1-12 `verified`，下一包 P1-13；未暂存、未提交、未推送。
