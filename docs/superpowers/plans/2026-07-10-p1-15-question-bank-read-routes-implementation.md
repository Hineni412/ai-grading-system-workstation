# P1-15 Question Bank Read Routes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为后续 Vue 题库页面提供严格只读的试卷列表、题目分页/组合筛选、题目详情、当前标签、富文本/预览元数据和受控图片读取 API，同时保持现有 Streamlit 题库业务口径。

**Architecture:** 新增无 FastAPI 依赖的 `QuestionBankReadService`，使用 SQLite `mode=ro` 连接读取现有 Schema；现有 `QuestionService` 的筛选条件构造器抽成纯函数供两个服务共用，避免 GET 触发初始化、元数据回填或考频缓存写入。FastAPI router 只负责查询参数映射、Pydantic 响应构造和稳定错误映射；所有素材以题目 ID + 资源序号/预览类型访问，存储路径永不进入 JSON 或客户端参数。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、Pillow、pytest、现有 `resolve_controlled_file()` 文件边界。

**Review-driven read-snapshot amendment (2026-07-10):** Task 5 security
review proved that SQLite `mode=ro` still creates or updates source `-wal/-shm`
files, while `immutable=1` hides committed, uncheckpointed WAL rows. Replace the
direct source connection with a bounded, fail-closed optimistic capture of the
main database plus WAL into the system temporary directory. Capture order is
main copy, WAL copy, exact WAL comparison, exact main comparison; any source
change retries the whole capture, and sustained churn returns a sanitized 503.
SQLite opens only the temporary candidate. This is a local single-user
engineering snapshot under normal SQLite WAL protocol, not a claim of an
official atomic backup under arbitrary uncoordinated writers.

## Global Constraints

- 只在现有本机 `127.0.0.1` FastAPI 中增加路由，不改变单用户/loopback 部署边界。
- 不导入文件、不调用 AI、不保存或修改标签、不迁移旧技能体系、不新增/修改数据库 Schema。
- 不修改、删除、恢复、暂存或提交任何 `user_data/` 内容；测试数据库、富文本侧车和图片全部位于 `tmp_path`。
- `user_data` 基线必须保持 205 行状态、SHA-256 `b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`；阅卷库和题库 SHA-256 必须分别保持 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd` 与 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`。
- 不从 `pages/题库管理.py` 导入任何函数；该模块导入时会初始化真实题库、回填题型并创建目录。
- GET 路由不得调用 `QuestionService.initialize_database()`、`QuestionFrequencyService.backfill_all_fingerprints()` 或任何写方法；考频排序只读取现有 `question_frequency_cache`。
- 保持现有筛选语义：维度之间 AND、同一维度多个值 OR；已打标签必须同时存在非空 `knowledge_point/ability/exam_scope/student_level`；软删题目和父试卷已删除题目不可见。
- 分页使用 1-based `page`，默认 `page_size=20`，上限 100；排序必须在 `LIMIT/OFFSET` 前完成；零结果返回 200、`items=[]`、`total_pages=1`。
- JSON 只能使用显式允许字段；不得返回 `source_file`、`content_fingerprint`、`image_paths`、`image_path`、原始预览错误、DOCX XML、`image_relationships` 或任何嵌入式 `[[IMAGE:路径]]`。
- 暂不 commit、push 或创建 PR；每个任务结束只检查差异和暂存区，保持 `git diff --cached --name-only` 为空。

---

## File Structure

- Modify: `question_bank/services/question_service.py`：把现有筛选 SQL 条件构造抽成无副作用的共享函数，旧方法继续委托它。
- Create: `question_bank/services/question_read_service.py`：只读连接、试卷聚合、分页查询、详情安全投影、富文本元数据与素材解析。
- Modify: `backend/api/dependencies.py`：新增绑定题库 DB 与数据根的可覆盖读服务依赖。
- Create: `backend/api/schemas/question_bank.py`：试卷、分页、标签、素材、预览、富文本和详情模型。
- Create: `backend/api/routers/question_bank.py`：JSON 与受控二进制 GET 路由。
- Modify: `backend/api/routers/__init__.py`、`backend/api/schemas/__init__.py`、`backend/api/app.py`：注册新路由/模型。
- Create: `tests/test_api_question_bank_routes.py`：P1-15 聚焦契约、安全与无写入测试。
- Modify: `tests/test_api_openapi_contract.py`：新增 operation、二进制媒体类型与无路径参数守卫。
- Modify after GREEN: `AGENTS.md`、`ARCHITECTURE.md`、`docs/superpowers/packages/EXECUTION_INDEX.md`、`docs/superpowers/packages/phase-1-execution-packages.md`：记录实际实现与验证证据。

## Public Interfaces

```python
QuestionBankReadService(db_path: Path, *, data_root: Path)
QuestionBankReadService.list_papers() -> list[dict[str, Any]]
QuestionBankReadService.list_questions(filters: QuestionReadFilters) -> QuestionReadPage
QuestionBankReadService.get_question(question_id: int) -> dict[str, Any] | None
QuestionBankReadService.resolve_asset(question_id: int, asset_index: int) -> ResolvedFile
QuestionBankReadService.resolve_preview(question_id: int, preview_type: str) -> ResolvedFile

GET /api/question-bank/papers
GET /api/question-bank/questions
GET /api/question-bank/questions/{question_id}
GET /api/question-bank/questions/{question_id}/assets/{asset_index}
GET /api/question-bank/questions/{question_id}/previews/{preview_type}
```

`GET /api/question-bank/questions` query contract:

```text
page: int = 1 (>=1)
page_size: int = 20 (1..100)
question_number: optional exact string
keyword: optional LIKE substring over question_text/answer_text
knowledge_point: optional LIKE substring
difficulty_min/difficulty_max: optional inclusive 1..10 pair
question_types/paper_ids/years/exam_types/grades/exam_scopes: optional repeated raw values
tag_status: all | tagged | untagged
sort: newest | difficulty | frequency_midterm | frequency_final | frequency_zhongkao | frequency_contextual
```

---

### Task 1: Strict Read Model And Paper List

**Files:**
- Modify: `question_bank/services/question_service.py`
- Create: `question_bank/services/question_read_service.py`
- Create: `tests/test_api_question_bank_routes.py`

**Interfaces:**
- Produces: `build_question_filter_query(...) -> tuple[list[str], list[str], list[Any]]` as a pure shared helper.
- Produces: `_read_connection()` backed by `<resolved-db-uri>?mode=ro`.
- Produces: `QuestionBankReadService.list_papers()` with corrected active/tagged counts and no stored paths.

- [x] **Step 1: Write the failing read-only paper tests**

Create a `tmp_path` fixture that initializes a temporary question-bank database, inserts one empty paper, one paper with active/deleted questions and tags, and records `db_path.read_bytes()`. Assert:

```python
def test_read_service_lists_active_papers_without_paths_or_writes(question_bank_fixture):
    service, db_path, _ = question_bank_fixture
    before = db_path.read_bytes()

    papers = service.list_papers()

    assert [item["title"] for item in papers] == ["Newest", "Empty"]
    assert papers[0]["question_count"] == 1
    assert papers[0]["tagged_question_count"] == 1
    assert papers[1]["question_count"] == 0
    assert papers[1]["tagged_question_count"] == 0
    assert "source_file" not in repr(papers)
    assert "content_fingerprint" not in repr(papers)
    assert db_path.read_bytes() == before
```

- [x] **Step 2: Run the test to verify RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py::test_read_service_lists_active_papers_without_paths_or_writes -q
```

Expected: FAIL because `QuestionBankReadService` does not exist.

- [x] **Step 3: Extract the pure filter builder and implement the minimal read service**

Move the body of `QuestionService._build_filter_query()` into a module-level `build_question_filter_query()` with the same parameters and make the existing method delegate to it. In `question_read_service.py`, add:

```python
@contextmanager
def _read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()
```

Implement paper aggregation with `q.id IS NOT NULL AND COALESCE(q.is_deleted, 0)=0`; tagged progress matches the active page definition (at least one non-empty analysis tag in `knowledge_point/method/ability/model/error_type/exam_scope`). Select paper columns explicitly and omit stored source/fingerprint columns.

- [x] **Step 4: Run service and legacy filter regressions to verify GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py::test_read_service_lists_active_papers_without_paths_or_writes tests\test_question_bank_service.py -q
```

Expected: all selected tests PASS; existing Streamlit service filtering is unchanged.

---

### Task 2: Paged Question JSON Routes

**Files:**
- Create: `backend/api/schemas/question_bank.py`
- Create: `backend/api/routers/question_bank.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `question_bank/services/question_read_service.py`
- Test: `tests/test_api_question_bank_routes.py`

**Interfaces:**
- Produces: `get_question_bank_read_service() -> QuestionBankReadService` using one bundled DB/data-root context.
- Produces: `QuestionReadFilters`, `QuestionReadPage`, `QuestionPaperListResponse`, `QuestionListResponse`, `QuestionListItem`, `QuestionTagResponse`.

- [x] **Step 1: Write route registration, empty-state, pagination and combination-filter RED tests**

Add tests that override `get_question_bank_read_service` with a temp service and assert:

```python
assert client.get("/api/question-bank/papers").json() == {"items": [], "total": 0}
assert client.get("/api/question-bank/questions").json() == {
    "items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 1
}
```

Seed 25 deterministic questions and assert page 2 contains five items in newest order. Seed a target question satisfying paper/year/exam type/type/difficulty/core-tag/exam-scope/keyword filters and a near miss for each dimension; call with repeated query parameters and assert only the target is returned. Assert raw `source_file`, fingerprints and image paths are absent from serialized JSON.

- [x] **Step 2: Run the list-route tests to verify RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k "registration or empty or pagination or combined_filters" -q
```

Expected: FAIL because router, schemas and dependency are absent.

- [x] **Step 3: Implement bounded list schemas, service query and thin router**

Use explicit models with `ConfigDict(extra="forbid")`. The service must:

1. Call `build_question_filter_query()` with raw repeated values and exact `exam_scope` tag filters.
2. Count `DISTINCT q.id`.
3. Join `question_frequency_cache` only for frequency sorts; never backfill.
4. Order before `LIMIT/OFFSET`.
5. Batch-load tags for the selected page.
6. Strip every `[[IMAGE:...]]` marker from public question/answer text and emit only semantic `/api/question-bank/questions/{id}/assets/{index}` links.

Map `tag_status` as `all -> None`, `tagged -> 已打标签`, `untagged -> 未打标签`. Map sort values to the existing SQL formulas; `newest` is `created_at DESC, id DESC`.

- [x] **Step 4: Run the list-route tests and invalid-query tests to verify GREEN**

Add and run 422 tests for page 0, page_size 0/101, unsupported tag status/sort, and an incomplete or inverted difficulty range:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k "papers or questions or invalid" -q
```

Expected: all selected tests PASS and errors use `validation_error` or an explicit unified 422 `invalid_difficulty_range` body.

---

### Task 3: Safe Detail, Current Tags, Rich Content And Preview Metadata

**Files:**
- Modify: `question_bank/services/question_read_service.py`
- Modify: `backend/api/schemas/question_bank.py`
- Modify: `backend/api/routers/question_bank.py`
- Test: `tests/test_api_question_bank_routes.py`

**Interfaces:**
- Produces: `QuestionDetailResponse`, `QuestionAssetLink`, `QuestionPreviewMetadata`, `QuestionPreviewBox`, `QuestionRichContentMetadata`, `QuestionRichTextBlock`.
- Produces: 404 `question_not_found` for missing/soft-deleted question IDs.

- [x] **Step 1: Write long-text, rich-content, preview and path-redaction RED tests**

Seed one active question with:

- question and answer strings longer than 10,000 characters;
- absolute and relative image markers;
- ordered current tags;
- a version-3 rich sidecar under the injected temp root containing `text`, raw XML and path-bearing `image_relationships`;
- ready/failed preview rows whose stored `source_file`, `image_path` and failure `message` contain absolute paths.

Assert exact untruncated public text after marker removal, ordered allowed tag fields, rich block text and counts, typed bbox/status, semantic URLs, and recursively assert that no temp absolute path, `source_file`, `image_path`, `image_relationships`, XML or `[[IMAGE:` marker occurs in JSON. Add exact 404/request-id assertions for a missing and a soft-deleted question.

- [x] **Step 2: Run detail tests to verify RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k "detail or long_text or missing_question or path_redaction" -q
```

Expected: FAIL because detail projection is not implemented.

- [x] **Step 3: Implement explicit safe projections**

The detail projection must:

```text
- select only active questions whose parent paper is not deleted;
- return paper metadata through an explicit allowlist;
- parse tags ordered by tag id and return only typed tag fields;
- load rich content only from <injected-data-root>/question_bank/rich_content/question_<id>.json;
- reject a mismatched sidecar question_id or non-v3/invalid payload;
- expose only block text with image markers stripped plus asset indexes/URLs;
- omit raw Word XML and relationship mappings;
- parse preview bbox into optional x0/y0/x1/y1 floats;
- expose preview type/status/page/update time and a semantic URL only when an image row exists;
- omit raw preview failure message because it can contain a resolved disk path.
```

- [x] **Step 4: Run all JSON route tests to verify GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k "not media" -q
```

Expected: all selected tests PASS.

---

### Task 4: Controlled Question Assets And Preview Images

**Files:**
- Modify: `question_bank/services/question_read_service.py`
- Modify: `backend/api/routers/question_bank.py`
- Test: `tests/test_api_question_bank_routes.py`
- Modify: `tests/test_api_openapi_contract.py`

**Interfaces:**
- Produces: ID-bound `resolve_asset()` and `resolve_preview()` returning `ResolvedFile`.
- Produces: binary routes with image MIME allowlist and `Cache-Control: no-store`.

- [x] **Step 1: Write controlled-media RED tests**

Create valid PNG/JPEG files under the temp `question_bank/extracted_images` and `question_bank/previews` roots plus outside-root, missing and `.txt` candidates. Assert:

```python
response = client.get(f"/api/question-bank/questions/{question_id}/assets/0")
assert response.status_code == 200
assert response.headers["content-type"] == "image/png"
assert response.headers["cache-control"] == "no-store"
```

Also assert wrong question/asset index returns 404, missing returns 410, outside root returns 403, wrong suffix returns 415, and none of the error bodies contains a filesystem path. Repeat success/expired cases for `previews/question`; invalid preview types must fail validation.

- [x] **Step 2: Run media tests to verify RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py -k "asset or preview_media" -q
```

Expected: FAIL because media resolvers/routes do not exist.

- [x] **Step 3: Implement controlled resolution and stable error mapping**

Use `resolve_question_bank_asset_path()` only for legacy rebasing/fallback, then pass the result through `resolve_controlled_file()` against `question_bank/extracted_images` or `question_bank/previews` and the image suffix allowlist. Never accept path/filename query parameters. Map internal not-found/expired/forbidden/type errors to stable `ApiError` codes with `no-store` headers.

- [x] **Step 4: Extend OpenAPI binary contract and verify GREEN**

Add both binary operations to `EXPECTED_OPERATIONS`, assert they accept no `path/file/file_path/filename` parameter, and declare only `image/jpeg`, `image/png`, `image/webp`, `image/bmp`, each with `{type: string, format: binary}`.

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py tests\test_api_openapi_contract.py -q
```

Expected: all selected tests PASS; operation IDs are unique and every 422 references `ErrorResponse`.

---

### Task 5: Regression, Independent Review, Documentation And Data Guard

**Files:**
- Modify only if actual verified behavior requires it: `AGENTS.md`, `ARCHITECTURE.md`, `docs/superpowers/packages/EXECUTION_INDEX.md`, `docs/superpowers/packages/phase-1-execution-packages.md`
- Test: all relevant API/question-bank tests and `tools/smoke_check.py`

**Interfaces:**
- Produces: verified P1-15 evidence and P1-16 as next ready package.
- Preserves: empty Git staging area and exact `user_data` baseline.

- [x] **Step 1: Run focused and API regressions**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_question_bank_routes.py tests\test_question_bank_service.py tests\test_question_bank_asset_path_service.py tests\test_api_openapi_contract.py tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py tests\test_api_jobs.py tests\test_api_job_lifecycle.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_review_routes.py tests\test_api_media_routes.py tests\test_api_file_downloads.py -q
```

Expected: all selected tests PASS.

Actual: 156 passed in 25.99s after all review-driven fixes.

- [x] **Step 2: Run quick smoke**

Run:

```powershell
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

Expected: first-party compile passes; both database copies initialize idempotently and `integrity_check=ok`.

Actual: 342 first-party Python files compiled; both database copies were
idempotent with `integrity_check=ok`.

- [x] **Step 3: Request independent code review and resolve findings with TDD**

Reviewer checks exact package scope, strict no-write behavior, filter parity, path/marker redaction, controlled-root ownership, OpenAPI/runtime alignment, test isolation and no P1-16 write/import creep. Any Critical/Important finding gets a failing regression test before the fix and is re-reviewed.

Actual: root/sibling link escapes, exact-text/current-preview drift,
reason/tag/rich-content leakage, and WAL sidecar writes were reproduced with
failing regressions before fixes. Final independent package and security reviews
both reported 0 Critical / 0 Important / 0 Minor.

- [x] **Step 4: Update factual documentation and execution state**

After verification only:

```text
- Mark P1-15 verified with actual focused/API/smoke counts.
- Update Phase 1 verified/todo counts and make P1-16 ready.
- Record only the new read service/routes, no-write boundary and semantic media URLs.
- Do not claim P1-15 merged or Phase 1 complete.
```

Actual: `AGENTS.md`, `ARCHITECTURE.md`, `EXECUTION_INDEX.md` and the Phase 1
package map now record P1-15 as local `verified`, Phase 1 as 1 merged + 14
verified / 14 pending, and P1-16 as the next `ready` package.

- [x] **Step 5: Re-run final verification after documentation/fixes**

Run at minimum the focused test file, OpenAPI test and quick smoke again if code changed during review. Then run:

```powershell
git diff --check
git diff --cached --name-only
git status --short -- backend question_bank tests docs AGENTS.md ARCHITECTURE.md
```

Expected: no whitespace errors; staging is empty; only P1-15 source/test/plan/status documents are newly changed beyond the pre-existing worktree.

Actual: the fresh 17-file combination gate passed 156 tests; the fresh
Question Bank/OpenAPI gate passed 64 tests; quick smoke compiled 342 first-party
Python files and verified idempotent copied-database initialization with
`integrity_check=ok`. OpenAPI contained 30 paths / 38 operations / 0 duplicate
operation IDs, including five Question Bank paths. `git diff --check` exited 0
with only the repository's LF-to-CRLF working-copy warnings,
`git diff --cached --name-only` returned no paths, and the scoped status was
inspected without staging, committing or pushing.

- [x] **Step 6: Recompute the immutable `user_data` guard**

Use sorted `git status --short -- user_data` joined by LF without a trailing newline. Expected: 205 lines and hash `b81a376...f6e343cce8`. Recompute both database hashes and require the exact Global Constraints values. On any mismatch, stop and investigate; never auto-restore user data.

Actual: the final guard remained exactly 205 lines with SHA-256
`b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`.
`grading_system.db` remained
`93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`
and `question_bank.db` remained
`e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`.
Neither database had a `-wal` or `-shm` sidecar after verification.

---

## Plan Self-Review

- **Spec coverage:** Tasks 1-4 cover empty paper/question state, corrected paper counts, bounded pagination, combination filters, detail/404, long text, ordered current tags, rich-content/preview metadata, semantic media, path redaction, strict no-write behavior and OpenAPI stability. Task 5 covers the package verification/index evidence requirement.
- **Scope:** No imports, AI calls, tag writes, deleted-content recovery, Schema changes, legacy skill migration, filter-option endpoint or P1-16 behavior.
- **Type consistency:** The same `QuestionBankReadService` is produced by dependency injection and consumed by all JSON/binary routes; all media references use the same deterministic asset index sequence as `resolve_asset()`.
- **TDD:** Every production change has an explicit RED command before implementation and a GREEN command after it.
- **Data safety:** Test databases/assets use `tmp_path`; production-like read candidates use self-cleaning system temporary directories and SQLite never opens the source question-bank path. Runtime dependencies are never invoked by tests; the final status/hash guard must match the accepted P1-14 baseline.
- **No placeholders:** All routes, query fields, service signatures, error behavior, commands and expected results are specified; implementation may adjust exact internal helper names only when current-source evidence requires it and the plan is updated first.
