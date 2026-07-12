# P1-19 Training API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 tag-only 诊断、练习推荐和训练任务服务提供稳定的 FastAPI 契约，使教师可查询薄弱点、预览推荐、分页查看任务并确认保存任务。

**Architecture:** 新增严格的 training Pydantic schema 与薄路由，通过 FastAPI dependency 分别注入 `DiagnosisProfileService`、`PracticePlanService` 和 `TrainingTaskService`。诊断只调用 `build_profiles()` 的 tag-only 主路径；推荐只调用现有精确 `knowledge_point` 分支；任务确认复用现有事务写入，并以客户端提供的 UUID 确认键生成稳定任务代码，使重复提交返回同一任务。所有公开 payload 在响应前经过共享脱敏器，绝不公开数据库路径、题库源路径、导出路径或旧 skill/legacy 活动语义。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 DiagnosisProfileService/PracticePlanService/TrainingTaskService、pytest。

**执行包：** P1-19
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 2e047c07aeed967d7a0ece11c7532a15a207f151
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-19 的诊断、推荐草案、训练任务分页/详情和教师确认 API；训练导出 Job 属于 P1-20，图谱下钻属于 P1-21。
- 固定现有 `scope`：`student | selected | class`；固定现有 `exam_scope`：`current | manual | cross_exam`。未知学生/考试沿用现有服务的 warning/空态，不臆造权限或业务规则。
- 诊断必须调用 `DiagnosisProfileService.build_profiles()`，并断言公开结果的 `diagnosis_identity == "question_tag"`；禁止调用 `build_legacy_profiles()`、`build_skill_profiles()` 或恢复旧推荐分支。
- 推荐继续只接受完全相同的 `question_tags.knowledge_point`；`related_fill_policy` 固定为 `exact_only`，`allow_broad_fallback` 固定为 `False`，不实现 Phase 4 新掌握度、关系或回流。
- 不修改 SQLite Schema、评分规则、题号契约、活动标签语义或 Streamlit 页面行为；不调用真实模型。
- 任务确认是本包唯一写操作，只允许写临时题库测试副本。API 不接受客户端 `created_by`，服务端固定记录 `teacher`；重复确认键不得创建重复任务。
- 所有测试只使用 `tmp_path` 中的临时双库和合成题目/学生，不打开或初始化真实 `user_data` 数据库。
- 不修改、删除、暂存、提交或 stash 任何 `user_data/`；结束时根目录真实两库的大小、UTC 修改时间和 SHA-256 必须与领取基线一致。
- 功能分支不更新 `EXECUTION_INDEX.md`；共享 Index/架构状态由 integration 统一整理。

---

## File Structure

- Create: `backend/api/schemas/training.py`：严格的 scope、exam scope、诊断、推荐、任务确认和分页契约。
- Create: `backend/api/routers/training.py`：五个薄端点、稳定错误映射、tag-only 守卫和公开数据脱敏。
- Modify: `backend/api/dependencies.py`：从同一 `PathManager` 快照构造三个现有 Training 服务。
- Modify: `backend/api/app.py`：注册 training router。
- Modify: `backend/api/routers/__init__.py`：导出 training router。
- Modify: `backend/api/schemas/__init__.py`：导出 training schema。
- Modify: `question_bank/services/training_task_service.py`：增加分页读取、按任务代码读取和带稳定任务代码的幂等创建；保持现有调用兼容。
- Create: `tests/test_api_training_routes.py`：临时双库上的诊断、预览、确认、分页、空态、错误和脱敏契约。
- Modify: `tests/test_training_task_service.py`：服务分页和幂等确认的 RED/GREEN 覆盖。
- Modify: `tests/test_api_openapi_contract.py`：新增五个端点、严格请求和稳定错误 schema 守卫。
- Modify: `ARCHITECTURE.md`：仅在实现完成后记录 P1-19 已实现事实。
- Modify: `docs/superpowers/plans/2026-07-12-p1-19-training-api-implementation.md`：checkbox、验证、复审和交接证据。

## Public Interfaces

```python
TrainingTaskService.create_task(
    practice_plan: Mapping[str, Any],
    *,
    created_by: str | None = None,
    task_code: str | None = None,
) -> TrainingTaskRecord

TrainingTaskService.get_task_by_code(task_code: str) -> dict[str, Any]

TrainingTaskService.list_tasks_page(
    *, page: int = 1, page_size: int = 20
) -> tuple[list[dict[str, Any]], int]
```

```text
POST /api/training/diagnosis
POST /api/training/plans/preview
POST /api/training/tasks
GET  /api/training/tasks
GET  /api/training/tasks/{task_id}
```

Stable failures:

```text
404 training_task_not_found
409 training_confirmation_conflict
422 training_scope_invalid
422 training_plan_invalid
503 training_database_unavailable
```

---

### Task 1: Strict Training Schemas And Tag-Only Diagnosis

**Files:**
- Create: `backend/api/schemas/training.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/dependencies.py`
- Create: `backend/api/routers/training.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Create: `tests/test_api_training_routes.py`

**Interfaces:**
- Consumes `TrainingDiagnosisRequest(scope, exam_scope)` with forbidden extra fields and bounded ID lists.
- Produces the existing `question_tag` diagnosis payload without filesystem references or legacy/skill identities.

- [x] **Step 1: Write failing diagnosis and registration tests**

Add a temporary dual-database fixture equivalent to `tests/test_question_tag_diagnosis.py`, dependency-override the three Training services, and assert the diagnosis endpoint returns exact knowledge-point aggregation, normalized scope/exam scope, missing-link/tag coverage, actionable warnings and no path/sensitive keys. Add invalid mode/empty selection cases and prove the endpoint does not call legacy/skill methods.

```python
def test_training_diagnosis_uses_question_tag_identity(training_client):
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnosis_identity"] == "question_tag"
    assert payload["students"][0]["weak_points"][0]["knowledge_point"] == "三角形全等"
```

- [x] **Step 2: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_routes.py -k diagnosis -q
```

Expected: FAIL because training schema/router/dependencies and route registration do not exist.

- [x] **Step 3: Implement minimal strict schemas, dependencies and diagnosis route**

Use `ConfigDict(extra="forbid")`, `Literal` modes, positive/bounded IDs and explicit nested response models for scope, exam scope, student profiles, weak points, coverage and evidence references. Map service `ValueError` to `training_scope_invalid`; map unavailable/corrupt temporary databases to the generic 503 without exception text. Sanitize the response, then reject any result whose `diagnosis_identity` is not `question_tag`.

- [x] **Step 4: Run GREEN and diagnosis regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_routes.py -k diagnosis tests/test_question_tag_diagnosis.py tests/test_diagnosis_profile_service.py -q
```

Expected: all selected tests pass and diagnosis responses contain no legacy/skill activity fields.

---

### Task 2: Exact-Tag Practice Plan Preview

**Files:**
- Modify: `backend/api/schemas/training.py`
- Modify: `backend/api/routers/training.py`
- Modify: `tests/test_api_training_routes.py`

**Interfaces:**
- Consumes scope, exam scope, `variant_mode`, question count 8-12, optional teacher groups, stage ratios summing to 1, and current-exam exclusion flag.
- Produces a public plan envelope with a deterministic `plan_revision` SHA-256 and the sanitized existing plan as `plan`.

- [x] **Step 1: Write failing preview tests**

Cover individual and grouped previews, exact-tag candidate selection, current-exam original exclusion, 8-12 question bound, invalid ratios, no evidence, missing tags, shortages and source-path stripping. Assert requests cannot enable broad fallback or legacy/skill modes.

```python
def test_plan_preview_reports_exact_tag_shortage(training_client):
    response = training_client.post("/api/training/plans/preview", json=preview_request())
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["diagnosis_snapshot"]["diagnosis_identity"] == "question_tag"
    assert all(item["match_kind"] == "exact" for v in plan["variants"] for item in v["items"])
```

- [x] **Step 2: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_routes.py -k preview -q
```

Expected: FAIL because the preview operation does not exist.

- [x] **Step 3: Implement minimal server-side diagnosis plus preview orchestration**

The route first rebuilds diagnosis from scope/exam scope, then calls `PracticePlanService.generate()` with only package-approved arguments: `related_fill_policy="exact_only"`, `allow_broad_fallback=False`, caller-selected variant mode/count/ratios/exclusion and bounded teacher groups. Canonically JSON-encode the sanitized plan and return its SHA-256 as `plan_revision`; no plan is persisted.

- [x] **Step 4: Run GREEN and recommendation regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_routes.py -k preview -q
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py tests/test_practice_grouping.py -q
```

Expected: all selected tests pass; shortages are explicit and no near/legacy candidate fills them.

---

### Task 3: Idempotent Teacher Confirmation And Paginated Task Reads

**Files:**
- Modify: `question_bank/services/training_task_service.py`
- Modify: `backend/api/schemas/training.py`
- Modify: `backend/api/routers/training.py`
- Modify: `tests/test_training_task_service.py`
- Modify: `tests/test_api_training_routes.py`

**Interfaces:**
- Consumes `confirmation_id: UUID`, `expected_plan_revision: 64 lowercase hex`, and the same preview request fields.
- Regenerates and compares the current plan; mismatch returns 409. A stable task code derived from the confirmation UUID makes identical retries return the existing task.
- `GET /tasks?page=1&page_size=20` sorts by `created_at DESC, id DESC` before slicing and returns `total_pages` with a minimum of 1.

- [x] **Step 1: Write failing service and API tests**

Cover efficient pagination/count, task detail 404, same confirmation retry returning one task, same confirmation with changed plan returning 409, different confirmation creating a new task, fixed `created_by="teacher"`, transactional failure, empty plan rejection and response stripping of `paper_source_file`/`output_path`/absolute paths.

```python
def test_repeated_confirmation_returns_same_task(training_client):
    preview = training_client.post("/api/training/plans/preview", json=preview_request()).json()
    body = {
        **preview_request(),
        "confirmation_id": "12345678-1234-5678-1234-567812345678",
        "expected_plan_revision": preview["plan_revision"],
    }
    first = training_client.post("/api/training/tasks", json=body)
    second = training_client.post("/api/training/tasks", json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
```

- [x] **Step 2: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_training_task_service.py tests/test_api_training_routes.py -k "pagination or confirmation or task" -q
```

Expected: FAIL because service pagination/idempotent code lookup and task API operations do not exist.

- [x] **Step 3: Implement minimal service and route behavior**

Keep `list_tasks()` unchanged for Streamlit compatibility. Add read-only `list_tasks_page()` and `get_task_by_code()`. Extend `create_task()` with an optional validated `task_code`; on a unique-code race, load the existing task and compare canonical scope/exam/diagnosis/generation snapshots before returning it, otherwise raise a conflict. The route regenerates the plan exactly as preview does, compares `plan_revision`, uses `TRN-CFM-<UUID hex uppercase>` as task code, fixes `created_by="teacher"`, and sanitizes list/detail/create responses.

- [x] **Step 4: Run GREEN and existing task regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_training_task_service.py tests/test_api_training_routes.py tests/test_training_task_history_ui.py -q
```

Expected: all selected tests pass; repeat confirmation creates exactly one task and existing Streamlit reads remain compatible.

---

### Task 4: OpenAPI, Architecture, Verification And Handoff

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-19-training-api-implementation.md`

**Interfaces:**
- Produces five unique OpenAPI operations with strict request models and stable 404/409/422/503 `ErrorResponse` declarations.
- Produces a reviewed functional SHA and a final handoff-only commit validated as `verified_pending_integration`.

- [x] **Step 1: Write OpenAPI assertions**

Add the five operations to `EXPECTED_OPERATIONS`; assert all JSON request schemas forbid extra properties, no request exposes `created_by`, path/destination/key/token/secret fields, and all declared failures use `ErrorResponse`.

- [x] **Step 2: Run contract checks and focused GREEN**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_openapi_contract.py -q
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_routes.py tests/test_training_task_service.py tests/test_question_tag_diagnosis.py tests/test_diagnosis_profile_service.py tests/test_practice_plan_service.py tests/test_practice_candidate_scoring.py tests/test_practice_grouping.py tests/test_training_task_history_ui.py tests/test_api_openapi_contract.py -q
```

Expected: route behavior first fails while operations are missing; after Tasks 1-3 register those operations, OpenAPI and focused checks pass together.

- [x] **Step 3: Record only implemented architecture facts and run affected API regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_app.py tests/test_api_read_routes.py tests/test_api_write_routes.py tests/test_api_question_bank_routes.py tests/test_api_question_bank_write_routes.py tests/test_api_training_routes.py tests/test_api_openapi_contract.py -q
git diff --check
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
```

Expected: selected API regression passes; diff check and quick smoke exit 0. Because this package changes shared API registration and writes training task state on temporary databases, integration must run the wave-end complete smoke; add full pytest earlier only if failures, shared infrastructure drift or candidate SHA changes trigger the repository rules.

- [x] **Step 4: Recheck scope, stash, worktree data and real-data fingerprints**

Confirm every package commit and `git diff --name-only origin/main...HEAD` exclude `user_data/`; the immutable stash baseline is unchanged; root real database size, UTC mtime and SHA-256 match the pre-work values; no worktree database or generated artifact is staged.

- [x] **Step 5: Create functional commit and perform independent review**

Update this plan evidence and handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `真实数据指纹: unchanged`; commit only P1-19 code/tests/architecture/plan. Perform an independent review of `origin/main..HEAD`; fix every Critical/Important finding with a new failing test and rerun its affected verification until Critical/Important are zero.

- [x] **Step 6: Create final handoff-only commit**

After independent review passes, update only this plan: record the full reviewed functional SHA, change to `verified_pending_integration`, set automated verification/independent review to `passed`, keep user acceptance `not_required`, and set the real-data fingerprint `unchanged`; commit only this plan and run `tools/handoff_status.py` from the project root.

---

## Rollback

- Revert the P1-19 functional commit chain; no database migration or Schema rollback is required.
- Existing Streamlit training page and the three current services remain available because the package only adds API adapters and compatible service methods.
- Training tasks written during tests exist only in temporary database copies. No real training task is created during implementation or verification.
- If focused regression, quick smoke, handoff validation, independent review or real-data fingerprint checks fail, keep the feature worktree intact and do not integrate.

## Implementation Evidence

- Baseline: existing diagnosis/recommendation/training task/API suite `52 passed` before source changes.
- RED/GREEN: diagnosis先因依赖缺失出现 3 个预期错误；推荐预览先以 5 个 404 失败；任务服务/确认/分页先以 11 个缺接口失败；随后分别转绿。复审前又以 2 个失败用例关闭空班级范围扩散和确认预查数据库错误泄漏，以 1 个失败用例关闭训练导出原始错误公开；首轮独立复审后以连续创建两个默认任务的失败用例复现 `None-V...` 明细码唯一约束冲突，改用 resolved task code 后转绿。
- Focused regression: 修复后的最终 P1-19 聚焦与受影响领域/API 合并回归 `177 passed`。
- Affected API regression: 独立运行 API App、读写、Question Bank、Training 与 OpenAPI 组合 `128 passed`；最终已包含在上述 176 项候选验证中。
- Quick smoke: 文档治理、363 个第一方 Python 文件静态编译、两库临时副本初始化幂等和 `integrity_check=ok` 通过。
- Independent review: 首轮复审提交 `2551e195c66516a31b666ec62bca077559220639` 为 0 Critical / 1 Important / 0 Minor，发现默认任务明细码错误使用可空原始参数；修复后的候选 `c047fb4d94eaaa7b2046e4fa56e0dad0e320cf78` 经全新复审为 0 Critical / 0 Important / 0 Minor，复审者从目标 worktree 独立运行 `42 passed` 与 `git diff --check`，结论 `Ready to merge: Yes`。
- Real data: 根目录两库大小、UTC mtime 与 SHA-256 均和领取基线一致；`grading_system.db` SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`，`question_bank.db` SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`；worktree 无 `user_data/` 变化。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-19
**交接状态：** verified_pending_integration
**功能提交：** c047fb4d94eaaa7b2046e4fa56e0dad0e320cf78
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
