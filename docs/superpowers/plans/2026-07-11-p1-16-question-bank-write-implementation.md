# P1-16 Question Bank Write Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为后续 Vue 题库页面提供教师确认标签、题目软删除/恢复、上传暂存和创建导入请求 API，同时保持现有 Streamlit 行为，并把耗时导入和 AI 打标留给 P1-18 Job。

**Architecture:** 新增无 FastAPI 依赖的 `QuestionBankWriteService`，以单个 SQLite `BEGIN IMMEDIATE` 事务完成 revision 校验与轻量写入；只读服务和写服务共享同一题目 revision 算法。导入文件先原子写入受控暂存目录，再创建仅包含相对资源引用和校验摘要的导入请求；本包不解析试卷、不写题库题目、不调用模型。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、pytest、现有 `PathManager` 与统一 `ApiError`。

**执行包：** P1-16
**规划状态：** ready_for_execution
**计划基线：** b74998a2bd3ada1893dcdbe24349b6885c415849
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 仅增加 P1-16 定义的题库轻量写入与导入准备；不实现 P1-18 的长导入、AI 打标、批次进度或重试。
- 不新增或修改 SQLite Schema，不写旧技能表，不调用 `SkillResolutionService`，不把 AI 标签自动视为教师确认。
- 不改变 `QuestionService.save_tag_analysis()`、软删除和恢复的现有 Streamlit 调用行为；新增 API 逻辑通过独立服务复用现有表结构。
- 所有写测试只使用 `tmp_path` 临时题库和临时数据根；不得用 SQLite 打开真实题库或阅卷库。
- 不修改、删除、暂存、提交或 stash 任何 `user_data/`；实现前后真实两库的大小、UTC 修改时间和 SHA-256 必须一致。
- 标签保存是精确集合替换：请求中的允许标签成为该题当前活动标签，写入 `source='manual'`；不投影旧技能身份。
- 相同目标状态的重复请求必须幂等成功；revision 已变化且目标状态不同必须返回 409，绝不静默覆盖。
- 上传只接受非空 `.docx`/`.pdf`，客户端文件名不得决定存储目录；JSON 响应不得返回本机绝对路径。
- 上传和请求清单必须使用临时文件/目录加原子发布；失败时不得留下可见的半成品请求。
- 功能分支不修改 `AGENTS.md`、`ARCHITECTURE.md` 或 `EXECUTION_INDEX.md`；共享状态由 integration 阶段统一更新。

---

## File Structure

- Create: `question_bank/services/question_revision.py`：从题目状态与全部当前标签生成稳定、不透明的 revision。
- Create: `question_bank/services/question_write_service.py`：轻写事务、冲突判定、上传暂存和导入请求发布。
- Modify: `question_bank/services/question_read_service.py`：在列表与详情安全投影中加入 revision。
- Modify: `backend/api/dependencies.py`：新增可覆盖的题库写服务依赖。
- Modify: `backend/api/schemas/question_bank.py`：新增写标签、状态变更、上传和导入请求模型。
- Modify: `backend/api/routers/question_bank.py`：新增轻写与导入准备端点及稳定错误映射。
- Modify: `backend/api/schemas/__init__.py`：公开新增模型。
- Create: `tests/test_api_question_bank_write_routes.py`：P1-16 服务/API/原子性契约。
- Modify: `tests/test_api_question_bank_routes.py`：只读 revision 回归。
- Modify: `tests/test_api_openapi_contract.py`：新增操作与无客户端路径参数守卫。

## Public Interfaces

```python
QuestionBankWriteService(db_path: Path, *, data_root: Path)
QuestionBankWriteService.replace_tags(
    question_id: int,
    *,
    expected_revision: str,
    tags: Sequence[ConfirmedQuestionTag],
) -> QuestionWriteResult
QuestionBankWriteService.set_deleted(
    question_id: int,
    *,
    expected_revision: str,
    deleted: bool,
) -> QuestionWriteResult
QuestionBankWriteService.stage_upload(*, filename: str, content: bytes) -> StagedImportUpload
QuestionBankWriteService.create_import_request(*, upload_id: str) -> QuestionImportRequest

PUT  /api/question-bank/questions/{question_id}/tags
DELETE /api/question-bank/questions/{question_id}
POST /api/question-bank/questions/{question_id}/restore
POST /api/question-bank/import-uploads?filename=<client-name>
POST /api/question-bank/import-requests
```

Stable failures:

```text
404 question_not_found
404 question_import_upload_not_found
409 question_write_conflict
415 question_import_type_not_supported
422 validation_error
```

---

### Task 1: Shared Question Revision

**Files:**
- Create: `question_bank/services/question_revision.py`
- Modify: `question_bank/services/question_read_service.py`
- Test: `tests/test_api_question_bank_routes.py`

**Interfaces:**
- Produces: `question_revision(conn: sqlite3.Connection, question_id: int) -> str | None`.
- Produces: list/detail payload field `revision: str` for active questions.

- [ ] **Step 1: Write failing revision tests**

Add temporary-database tests that assert list and detail return the same 64-character revision, unchanged reads return the same value, and direct tag or soft-delete changes alter it:

```python
def test_question_read_payload_exposes_state_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    seed_question_with_tags(db_path, question_id=1)
    service = QuestionBankReadService(db_path)

    first = service.get_question(1)
    second = service.list_questions(QuestionReadFilters()).items[0]

    assert first is not None
    assert first["revision"] == second["revision"]
    assert len(first["revision"]) == 64
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k revision -q
```

Expected: FAIL because read payloads have no `revision`.

- [ ] **Step 3: Implement canonical revision hashing**

Create a helper that explicitly selects `id/is_deleted/updated_at` plus all tag rows ordered by `tag_type, tag_value, source, confidence, model_name, id`, serializes with `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, and returns SHA-256. The digest is opaque and must not expose tag text or paths.

Call it while the P1-15 temporary snapshot connection is open and add the result to list/detail rows before public sanitization. Add required `revision: str` fields to `QuestionListItem` and inherited detail schemas.

- [ ] **Step 4: Run revision and existing read tests to verify GREEN**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -q
```

Expected: all tests PASS.

---

### Task 2: Atomic Teacher-Confirmed Tag Replacement

**Files:**
- Create: `question_bank/services/question_write_service.py`
- Create: `tests/test_api_question_bank_write_routes.py`

**Interfaces:**
- Produces: `ConfirmedQuestionTag(tag_type: str, tag_value: str, confidence: float | None)`.
- Produces: `QuestionWriteConflict`, `QuestionWriteNotFound`, `QuestionWriteResult`.
- Produces: `replace_tags(...)` with exact, manual-only, idempotent behavior.

- [ ] **Step 1: Write failing service tests**

Cover exact replacement, manual source, duplicate normalization, idempotent retry with a stale revision but identical target tags, stale-different conflict, invalid/missing/deleted question IDs, no `question_skill_links` writes, and transaction rollback on an injected insert failure:

```python
def test_replace_tags_conflicts_instead_of_overwriting_newer_state(write_seed) -> None:
    service, question_id, revision = write_seed
    service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
    )

    with pytest.raises(QuestionWriteConflict):
        service.replace_tags(
            question_id,
            expected_revision=revision,
            tags=[ConfirmedQuestionTag("knowledge_point", "二次函数", 1.0)],
        )
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k replace_tags -q
```

Expected: collection/import FAIL because the write service does not exist.

- [ ] **Step 3: Implement minimal transactional replacement**

Inside `BEGIN IMMEDIATE`:

1. Load question including deleted state and current revision.
2. Normalize tags by trimmed `(tag_type, tag_value)` while preserving request order.
3. If the current exact tag set already equals the target, return success without writing even when `expected_revision` is stale.
4. Otherwise require exact revision equality or raise `QuestionWriteConflict(current_revision=...)`.
5. Reject missing or deleted questions.
6. Delete current allowed `question_tags`, insert target rows with `source='manual'` and `model_name=NULL`, update `questions.updated_at`, and commit once.
7. Invalidate only derived frequency cache after successful commit; cache invalidation failure must not turn an already committed write into an API failure.

Do not call `save_tag_analysis()` or any skill resolver.

- [ ] **Step 4: Run service tests and legacy service regressions**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k replace_tags tests\test_question_bank_service.py -q
```

Expected: all selected tests PASS and Streamlit service tests remain green.

---

### Task 3: Optimistic Soft Delete And Restore

**Files:**
- Modify: `question_bank/services/question_write_service.py`
- Modify: `tests/test_api_question_bank_write_routes.py`

**Interfaces:**
- Produces: `set_deleted(question_id, expected_revision, deleted) -> QuestionWriteResult`.

- [ ] **Step 1: Write failing state-transition tests**

Cover delete, restore, idempotent repeated delete/restore, stale-different conflict, missing ID, tag preservation, and rollback when the update fails.

```python
def test_soft_delete_and_restore_preserve_question_tags(write_seed) -> None:
    service, question_id, revision = write_seed
    deleted = service.set_deleted(
        question_id, expected_revision=revision, deleted=True
    )
    restored = service.set_deleted(
        question_id, expected_revision=deleted.revision, deleted=False
    )

    assert deleted.deleted is True
    assert restored.deleted is False
    assert load_tag_values(service.db_path, question_id) == ["一次函数"]
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k "delete or restore" -q
```

Expected: FAIL because `set_deleted` is absent.

- [ ] **Step 3: Implement minimal state transition**

Use the same `BEGIN IMMEDIATE` and revision helper. If current state already equals the requested state, return it without writing. Otherwise require the expected revision, update only `is_deleted/deleted_at/updated_at`, and preserve tags, rich content and source assets.

- [ ] **Step 4: Run state and legacy delete/restore tests**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k "delete or restore" tests\test_question_bank_service.py -q
```

Expected: all selected tests PASS.

---

### Task 4: Controlled Upload Staging And Import Request Publication

**Files:**
- Modify: `question_bank/services/question_write_service.py`
- Modify: `tests/test_api_question_bank_write_routes.py`

**Interfaces:**
- Produces: `StagedImportUpload(upload_id, filename, suffix, size, sha256)`.
- Produces: `QuestionImportRequest(request_id, upload_id, filename, size, sha256, status)`.
- Produces: `stage_upload()` and `create_import_request()` without importing a paper.

- [ ] **Step 1: Write failing staging tests**

Cover DOCX/PDF success, empty content, unsupported suffix, traversal filename, hash/size manifest, missing or tampered upload, request JSON without absolute paths, no question/paper rows, and injected write/rename failure cleanup.

```python
def test_create_import_request_only_publishes_pending_manifest(tmp_path: Path) -> None:
    service = QuestionBankWriteService(
        tmp_path / "question_bank.db", data_root=tmp_path / "data"
    )
    upload = service.stage_upload(filename="../数学试卷.pdf", content=b"%PDF-test")
    request = service.create_import_request(upload_id=upload.upload_id)

    assert request.status == "pending"
    assert count_rows(service.db_path, "papers") == 0
    assert count_rows(service.db_path, "questions") == 0
    assert str(tmp_path) not in request.to_json()
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k "upload or import_request" -q
```

Expected: FAIL because staging interfaces are absent.

- [ ] **Step 3: Implement atomic controlled resources**

Store resources only below:

```text
<data_root>/question_bank/import_staging/uploads/<upload_id>/
<data_root>/question_bank/import_staging/requests/<request_id>.json
```

Use server-generated UUID hex IDs, a safe stored filename (`source.pdf` or `source.docx`), SHA-256 and byte length. Write upload content and metadata into a sibling temporary directory, flush/close, then atomically rename the directory. Create requests by re-reading and validating the upload manifest and content hash, writing a temporary JSON file, then `os.replace()` to the final request path. On exceptions, remove only the server-generated temporary resource; never delete a published upload or request.

- [ ] **Step 4: Run staging tests and prove no import occurred**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k "upload or import_request" -q
```

Expected: all selected tests PASS; temporary DB contains no imported paper/question rows.

---

### Task 5: FastAPI Light-Write Routes And Stable Errors

**Files:**
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/schemas/question_bank.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/routers/question_bank.py`
- Modify: `tests/test_api_question_bank_write_routes.py`

**Interfaces:**
- Produces the five P1-16 HTTP operations listed under Public Interfaces.
- Consumes only `QuestionBankWriteService`; routers contain no SQL or direct filesystem paths.

- [ ] **Step 1: Write failing route contract tests**

Use dependency override with a `tmp_path` service. Cover success response shapes, 404, 409 with current revision, 415, shared 422, request IDs, no internal exception/path leakage, raw binary upload body, and repeated request idempotency.

```python
response = client.put(
    f"/api/question-bank/questions/{question_id}/tags",
    json={
        "expected_revision": revision,
        "tags": [
            {
                "tag_type": "knowledge_point",
                "tag_value": "一次函数",
                "confidence": 1.0,
            }
        ],
    },
)
assert response.status_code == 200
assert response.json()["tags"][0]["tag_value"] == "一次函数"
```

- [ ] **Step 2: Run route tests and verify RED**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -k route -q
```

Expected: 404/405 because write routes are not registered.

- [ ] **Step 3: Add explicit schemas, dependency and thin routes**

Use `ConfigDict(extra='forbid')`, bounded strings, `Literal` tag types already accepted by read schemas, and no client-supplied storage paths. Map service exceptions to stable `ApiError` responses. `POST /import-uploads` accepts the raw request body plus required `filename` query parameter; it must not accept an absolute destination. Return 201 for newly staged uploads and import requests.

- [ ] **Step 4: Run all P1-16 API tests**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py -q
```

Expected: all tests PASS.

---

### Task 6: OpenAPI, Regression And Package Verification

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `docs/superpowers/plans/2026-07-11-p1-16-question-bank-write-implementation.md`

**Interfaces:**
- Produces: OpenAPI operations for all five routes without filesystem destination parameters.
- Produces: a `waiting_review` functional commit with machine-verifiable handoff evidence.

- [ ] **Step 1: Write failing OpenAPI assertions**

Assert all paths and methods exist; write request bodies reject extra fields; upload exposes only `filename` plus binary body; error responses use `ErrorResponse`; no schema property contains `path`, `destination`, `api_key`, `token`, `password` or `secret`.

- [ ] **Step 2: Run OpenAPI test and verify RED**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py -q
```

Expected: FAIL until the new operations are fully declared.

- [ ] **Step 3: Complete minimal OpenAPI declarations and run focused regression**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_question_bank_write_routes.py tests\test_api_question_bank_routes.py tests\test_question_bank_service.py tests\test_question_bank_importer.py tests\test_api_openapi_contract.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 4: Run affected API regression, diff and smoke checks**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_question_bank_routes.py tests\test_api_question_bank_write_routes.py -q
git diff --check
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\smoke_check.py --skip-tests
```

Expected: all tests PASS; diff check and smoke exit 0.

- [ ] **Step 5: Recheck scope and real-data fingerprints**

Confirm `git status --short -- user_data` is empty, no new stash contains `user_data`, and both real database size/UTC/SHA-256 triples exactly match the recorded baseline.

- [ ] **Step 6: Create the functional commit and update handoff**

Update this plan’s evidence and handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `真实数据指纹: unchanged`; commit only package code, tests and this plan. Do not push or merge before independent review.

---

## Rollback

- Code/API rollback: revert the single P1-16 functional commit chain; no Schema rollback is required.
- Tag/delete rollback: P1-16 tests use only temporary databases. The API does not touch real data during implementation or verification.
- Upload/request rollback: unreferenced staged resources are server-generated, isolated and removable as a whole by a later explicit maintenance operation; this package performs no automatic destructive cleanup.
- Integration rollback: do not merge the package if focused regression, smoke, handoff validation or real-data fingerprint guard fails.

## Implementation Evidence

- RED/GREEN: revision 缺失、写服务缺失、删除/恢复缺失、暂存接口缺失、API 依赖缺失、OpenAPI 二进制声明缺失、空白标签 500 和未知标签残留均先由聚焦测试复现，再以最小实现转绿。
- P1-16 聚焦回归：`94 passed`。
- 受影响 API 回归：`93 passed`。
- 全量测试：`1059 passed in 244.81s`。
- 快速冒烟：文档治理、349 个第一方 Python 文件静态编译、两库副本初始化幂等与 integrity check 全部通过。
- `git diff --check`：通过。
- 真实数据库：`grading_system.db` 与 `question_bank.db` 的大小、UTC 修改时间和 SHA-256 与开工基线完全一致。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-16
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
