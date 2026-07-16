# P2-09 Session Configuration And Rubric Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-09
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** c4b5732f4ce33f33defe43626b7ac80d1d71e305
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-09-session-config-rubric-quick.md

**Goal:** 在 Vue SPA 中交付可恢复的连续考试配置工作台，让教师从创建考试草稿、上传并本地核对试卷、启动或重试配置生成 Job，一直完成 Rubric/Answer Key 编辑、安全保存和样卷映射结果确认。

**Architecture:** FastAPI 增加服务器拥有的草稿、来源 manifest、受控媒体和配置编辑投影；浏览器只提交 session/source/revision 等语义 ID 与有界教师差异，不回传完整私有文档、题图或内部路径。现有 `config_generation` Job 扩展为从来源生成、整卷单次请求和人工结构 refine，配置与来源仍只在完整成功后原子绑定；Vue 以一个四阶段 Pinia 工作台编排已有 Session/Job Store，并让服务器负责所有评分结构投影、反向写回和权威校验。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、PyMuPDF、python-docx/现有 DOCX importer、现有 JobManager/LLM Gateway、Vue 3.5.39、TypeScript 6.0.3、Pinia 3.0.4、Vue Router 5.1.0、Element Plus 2.14.3、Vitest 4.1.10、Playwright 1.61.1、现有 CSS Tokens。

## Global Constraints

- 业务基线是当前 Streamlit 行为、服务、数据库契约和测试，以及 2026-07-15 用户明确批准的设计；现有 Vue 行为只作为复用候选，不能单独证明业务含义。
- 用户已明确允许在评分依据生成前创建可恢复考试草稿；数据库仍使用现有 `created` 状态，不增加状态枚举、表、列或迁移。
- 不改变 score policy、总分 100 口径、题号/part/step 身份、生成 prompt、模型参数、模型供应商、重试预算、模板几何算法或批改算法。
- 浏览器不得指定服务器路径；公开响应、OpenAPI、Job payload/result、日志和文档不得包含绝对路径、密钥、完整试卷正文、题图 base64 或原始异常。
- 上传只接受 `.docx` 和 `.pdf`，单文件硬上限 200 MiB；有无 `Content-Length` 都必须在流式接收时执行同一限制。DOCX 同时限制单成员 256 MiB、总展开 1 GiB；PDF 最多 500 页，超限在渲染前拒绝。
- 本地拆题不调用模型；DOCX 复用 `extract_docx_text()` 与 `preview_question_blocks_from_docx_bytes()`，PDF 复用 `extract_pdf_text()`、`preview_question_blocks_from_docx_text()` 和 `extract_pdf_question_images()`。
- 默认逐题生成；整卷模式严格一次模型请求，不自动重试。部分结果不绑定考试；失败题重试只重试所选失败题并保留其余成功事实。
- 配置编辑必须由服务器投影和反向写回；浏览器不生成嵌套 Rubric JSON。revision 冲突返回 409，不自动合并或覆盖。
- 未保存 Rubric/Answer Key 文本只驻留 Pinia 内存，不写 `localStorage`、`sessionStorage`、URL 或日志；刷新前使用 `beforeunload`，刷新后只恢复服务器事实、source/job 引用和无敏感教师差异，不伪称未保存文本仍存在。
- 评分依据正式保存成功而样卷映射刷新失败时，返回部分成功：配置保持已保存，页面明确提示需要回旧入口重新确认映射，不得把配置误报为保存失败。
- 旧 Streamlit 配置入口和现有 `/api/sessions/{id}/config`、P1-17 生成/重试接口保持兼容；P2-09 不切换生产 UI，不实现 P2-10 样卷编辑器。
- 页面只支持不低于 1024px 的 Windows 桌面浏览器；验证 1024×768、1280×800、1366×768、1440×900、1920×1080，不建设移动端布局。
- 测试只使用临时数据库、临时数据根、合成 DOCX/PDF、程序生成图片、假 LLM 和 mock API；不得读取、修改、暂存、提交或 stash 真实 `user_data/`，不得调用真实模型或密钥。
- 实施使用 `frontend-design`、`test-driven-development` 和 `executing-plans`；每个行为先运行聚焦测试确认 RED，再做最小 GREEN。完成声明前使用 `verification-before-completion`。
- 本计划不自动派生子代理；只有用户在执行选择中明确选择 Subagent-Driven 后才可使用子代理。独立复审必须对最终候选执行需求审查与代码质量审查，Critical/Important 清零后才进入用户短测。
- 正式实施 worktree 为 `.worktrees/p2-09-session-config`、分支为 `codex/p2-09-session-config`。本计划领取提交必须是 `origin/main..HEAD` 的第一个 first-parent 提交且只包含本文件；已批准设计提交 `ba926973f2ebe5cfcd1703f154c963426531f5b5` 在领取提交后作为第二个纯文档提交带入，不改写或丢弃设计分支历史。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-09
**交接状态：** waiting_user
**功能提交：** 91d4a04576042c393273de16860f264f83dbb1c1
**自动验证：** passed
**独立复审：** passed
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

---

## Business Capability And UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 选择当前考试 | Streamlit 会话选择、sessions API、`grading_sessions`、Session Store 测试 | 只选择真实未删除考试；失效选择清除；后续读写绑定当前考试 | 顶部选择器与 `/sessions` 工作台同步 | 跨页面保持唯一考试上下文 | 无 | Store/API/刷新测试 |
| 创建与重命名考试 | `DBManager.create_grading_session()`、sessions API | 名称非空；创建可追踪、可软删除的普通考试；不伪造配置完成 | 第一阶段立即创建可恢复草稿并在标题处重命名 | 先取得稳定 session ID，来源和 Job 可恢复 | 2026-07-15 用户明确允许生成前保存草稿 | 临时 DB API、刷新 E2E |
| 上传 DOCX/PDF | Streamlit 上传、题库流式上传、来源归档服务 | 类型/大小有界；客户端不指定路径；上传不调用 AI | 第二阶段显示安全文件名、大小、12 位指纹与解析状态 | 浏览器不持有内部路径 | 无 | 流式、原子、路径与类型测试 |
| 本地拆题核对 | `preview_question_blocks_from_docx_*()`、PDF 裁题、现有 UI | 不调用 AI；题号身份不由前端改写；教师可修正题型、排除错拆题 | 连续高密度题目列表，长内容展开，受控媒体 URL | 长卷扫读比卡片堆叠高效 | 无 | 解析 fixtures、组件/E2E |
| 逐题生成与进度 | P1-17 Job、现有生成函数、Job Store | 使用确认题块；完整成功才绑定；任务终态以服务器为准 | 第三阶段提交 source ID/revision/差异并跟踪 Job | 不把完整正文和题图回传浏览器 | 无 | 假 LLM Job/API、刷新/重启测试 |
| 整卷单次请求 | `generate_grading_config_from_text/images()` 与测试 | Word 一次文本请求；PDF 一次全页视觉请求；失败不自动重试 | 高级备用模式，失败后显式手动重提 | 本地拆题不可靠时保留现有回退 | 无 | 假 LLM 调用次数、失败 E2E |
| 失败题重试 | P1-17 Retry | 成功题保留；只重试所选失败题；写请求不自动重放 | 失败题列表与显式“重试所选题” | 恢复路径与成功事实并列 | 无 | 子集重试、重复提交测试 |
| Rubric/Answer Key 编辑 | `build_unified_rubric_rows()`、反向写回、`validate_generated_config()` | 稳定隐藏 ID；100 分；part/step 合计和客观题规则由服务器判定 | 第四阶段大面积高密度编辑表 | 避免前端复制业务规则 | 无 | 特征等价、API、组件/E2E |
| 手工评分单元与 AI 完善 | Streamlit 手工拆分/编辑、`refine_grading_config_from_manual_structure()` | part ID 唯一稳定；分值合计一致；AI 不得改教师结构 | 每题次级评分单元编辑器和显式 AI 完善 | 高级能力按需展开 | 无 | 多 part、身份保持、假 LLM |
| 质量告警与保存准入 | 质量刷新、配置校验、现有 UI | 阻断与普通提醒分开；100 分精确校验；失败不发布半成品 | 顶部问题摘要、行内定位、固定保存栏 | 教师能直接定位问题 | 无 | 422 字段定位、长告警测试 |
| 正式保存与并发 | config PUT、原子配置文件、P1-17 expected path | 服务器重建/校验；失败保留当前版本；旧编辑/旧 Job 不覆盖新版本 | 单一“保存评分依据”；携带不透明 revision | 明确草稿与正式配置边界 | revision 为已有保护的增强 | 原子故障、409 竞态测试 |
| 来源归档与样卷联动 | 来源 SHA 绑定、题库同步状态、`_refresh_template_mapping_from_session()` | 完整成功后绑定；来源变化重置同步；映射失败如实表达部分成功 | 保存结果分层显示配置与映射结果 | 避免误报整体失败 | 无 | 临时数据根、注入映射失败 |
| 未保存保护与恢复 | Vue draft/Job/Session Store | 未保存内容不冒充服务器事实；失败保留内存输入；敏感内容不持久化 | 来源/Job/阶段恢复；脏编辑离开提醒 | 长流程可恢复且不泄露答案 | 纯 UX | Store、刷新、离开提醒 E2E |
| 导航 | 来源重校准决定、已实现 `/grading` | 只展示真实可用入口 | “考试配置”“评分复核”；根路由进入 `/sessions` | 对应建考到复核真实流程 | 纯 UX | 路由/导航测试 |

## Visual Direction

- **Subject / audience / single job:** Windows 本机教师阅卷工具；教师需要在一页内把一份试卷变成可批改的可靠评分依据。
- **Palette:** 只使用现有 Token：工作台灰 `#f6f7f8`、纸面白 `#ffffff`、主墨色 `#20242a`、操作蓝 `#2563eb`、核对琥珀 `#9a6718`、教师确认绿 `#255f49`。危险红只表示阻断或失败。
- **Type:** 保持现有 Inter / PingFang SC / Microsoft YaHei 字体栈；24px 页面标题、20px 阶段标题、14px 正文、13px 密集表格、12px 状态说明，不新增字体或依赖。
- **Layout:** 页面不是营销 Hero，也不是卡片集合。顶部是考试标题和可恢复事实，下面是一条真实有顺序的四阶段“阅卷边栏”，主体使用连续分隔区；到第四阶段时编辑表占据主要宽度和高度。
- **Signature:** 用一条像教师批卷页边注一样的蓝色竖向阶段线串联 1–4 阶段锚点；当前阶段使用 4px 选中边，完成阶段只显示事实性勾选。Rubric 表用粘性题号/评分单元列和细分值小计线，形成“评分账本”而不是通用后台表格。
- **Self-critique:** 不引入暖米色、衬线大标题、渐变、发光、巨型 KPI、零圆角报纸布局或装饰性动效。唯一有辨识度的元素是阶段边注线；其余视觉保持安静，以长题目、错误定位和表格效率为先。

## File Structure

- Create `backend/config_workspace/__init__.py`: 导出草稿、来源、编辑和发布服务的稳定接口。
- Create `backend/config_workspace/atomic.py`: 同目录原子 JSON/字节发布和精确补偿删除。
- Create `backend/config_workspace/locks.py`: 按 session 工作目录复用进程内锁，串行化来源切换、生成绑定和人工保存的最终检查。
- Create `backend/config_workspace/drafts.py`: 创建占位配置与普通 `created` 会话。
- Create `backend/config_workspace/sources.py`: 流式来源、manifest、DOCX/PDF 本地解析、公开题块和受控资产。
- Create `backend/config_workspace/editor.py`: 纯 Python Rubric 行投影、反向写回、评分单元命令、revision 和问题定位。
- Create `backend/config_workspace/publish.py`: 配置/来源绑定、样卷映射刷新与部分成功结果。
- Modify `backend/api/schemas/sessions.py`, `backend/api/routers/sessions.py`: 草稿请求/响应与 `POST /api/sessions/drafts`。
- Modify `backend/api/schemas/config.py`, `backend/api/schemas/__init__.py`, `backend/api/routers/config.py`: 来源、生成、编辑与 refine 契约。
- Modify `backend/api/dependencies.py`: 注入 `ConfigSourceService` 和 `ConfigPublishService` 所需受控根目录。
- Modify `backend/jobs/config_generation.py`, `backend/jobs/default_handlers.py`, `backend/jobs/store.py`: 来源模式、整卷模式、refine、source/config revision 和原子 DB 绑定。
- Modify `db_manager.py`: 人工保存时在一个事务绑定 rubric、answer、来源并复用题库同步重置语义。
- Modify `web_app.py`: 让统一表格、评分单元和样卷刷新包装器委托新服务，保持 Streamlit 行为。
- Create `tests/test_config_workspace_drafts.py`, `tests/test_api_session_drafts.py`: 草稿原子性与 API。
- Create `tests/test_config_source_service.py`, `tests/test_api_config_sources.py`: 上传、解析、manifest、媒体、安全和恢复。
- Modify `tests/test_config_generation_job.py`, `tests/test_api_config_generation_jobs.py`: 来源生成、整卷、revision、归档与原子绑定。
- Create `tests/test_config_editor_service.py`, `tests/test_api_config_editor.py`: 投影、反向写回、命令、revision、保存和部分成功。
- Modify `tests/test_unified_rubric_rows.py`, `tests/test_api_openapi_contract.py`: Streamlit 特征等价与公开契约守卫。
- Modify `frontend/src/api/client.ts`, `frontend/src/api/sessions.ts`; create `frontend/src/api/config-workspace.ts`: raw upload、严格解码和公开 DTO。
- Create `frontend/src/stores/config-workspace.ts`: 四阶段事实、无敏感持久化索引、脏草稿和旧响应隔离。
- Modify `frontend/src/navigation.ts`, `frontend/src/router/index.ts`, `frontend/src/layouts/AppShell.vue`, `frontend/src/components/shell/AppTopbar.vue`: 新业务入口与联合离开保护。
- Create `frontend/src/views/SessionConfigView.vue` and `frontend/src/components/config/*.vue`: 连续工作台、拆题、Job、编辑和保存结果。
- Create `frontend/src/styles/session-config.css`; modify `frontend/src/main.ts`: 只使用现有 Token 的专用布局。
- Create/modify `frontend/src/**/__tests__/*config*.spec.ts`: API、Store、组件和路由单测。
- Create `frontend/e2e/session-config.spec.ts`: mock API 五视口流程、恢复、冲突、失败和控制台守卫。
- Modify `ARCHITECTURE.md`: 只记录最终已实现的 P2-09 事实。
- Create `docs/user-testing/checkpoints/P2-09-session-config-rubric-quick.md`: 页面实际可运行后生成的 5–10 分钟短测。

## Public Interfaces

```text
POST /api/sessions/drafts
PATCH /api/sessions/{session_id}
POST /api/sessions/{session_id}/config/sources
POST /api/sessions/{session_id}/config/sources/submissions/{request_token}/abandon
GET  /api/sessions/{session_id}/config/sources/{source_id}
GET  /api/sessions/{session_id}/config/sources/{source_id}/questions/{question_id}/assets/{asset_kind}
POST /api/sessions/{session_id}/config/generate-from-source
POST /api/sessions/{session_id}/config/generation-jobs/requests/{request_token}/abandon
GET  /api/sessions/{session_id}/config/editor
PUT  /api/sessions/{session_id}/config/editor
POST /api/sessions/{session_id}/config/editor/refine
```

```python
create_session_draft(db: DBManager, upload_config_dir: Path, *, name: str) -> int

ConfigSourceService.stage_and_parse(
    *, session_id: int, filename: str, chunks: AsyncIterator[bytes]
) -> ConfigSourceRecord
ConfigSourceService.load(*, session_id: int, source_id: str) -> ConfigSourceRecord
ConfigSourceService.apply_teacher_decisions(
    record: ConfigSourceRecord,
    decisions: Sequence[QuestionDecision],
) -> PreparedGenerationInput

project_config_editor(payload: dict[str, Any]) -> list[ConfigEditorRow]
apply_config_editor_changes(
    payload: dict[str, Any],
    *, edits: Sequence[ConfigEditorEdit],
    commands: Sequence[ScoringUnitCommand],
) -> dict[str, Any]
config_revision(session: Mapping[str, Any], payload: Mapping[str, Any]) -> str

ConfigPublishService.save_editor(
    *, session_id: int, expected_revision: str,
    edits: Sequence[ConfigEditorEdit],
    commands: Sequence[ScoringUnitCommand],
) -> ConfigSaveResult
```

Stable failures:

```text
400 invalid_session_draft
404 session_not_found
404 config_source_not_found
404 config_asset_not_found
409 config_source_changed
409 config_revision_conflict
409 config_generation_retry_not_available
413 config_source_too_large
415 config_source_type_unsupported
422 config_source_invalid
422 invalid_config_generation_request
422 invalid_config_editor
503 job_type_not_supported
```

---

### Task 1: Recoverable Session Drafts

**Files:**
- Create: `backend/config_workspace/atomic.py`
- Create: `backend/config_workspace/locks.py`
- Create: `backend/config_workspace/drafts.py`
- Create: `backend/config_workspace/__init__.py`
- Modify: `backend/api/schemas/sessions.py:1-25`
- Modify: `backend/api/routers/sessions.py:110-170`
- Create: `tests/test_config_workspace_drafts.py`
- Create: `tests/test_api_session_drafts.py`

**Interfaces:**
- Produces `create_session_draft(db, upload_config_dir, *, name) -> int`.
- Produces `session_config_lock(upload_config_dir: Path, session_id: int) -> ContextManager[None]`; Tasks 2/3/5 reuse this exact lock.
- Produces `POST /api/sessions/drafts` with body `{"name": str}` and existing `SessionSummary` response.
- Placeholder files contain empty `questions` and `draft=true`; they cannot pass `validate_generated_config()`.

- [ ] **Step 1: Write failing atomic draft tests**

```python
def test_create_session_draft_creates_recoverable_created_session(tmp_path):
    db = initialized_db(tmp_path / "grading.db")
    session_id = create_session_draft(db, tmp_path / "uploaded", name="七年级期末")
    row = db.get_grading_session(session_id)
    assert row["status"] == "created"
    assert json.loads(Path(row["rubric_path"]).read_text("utf-8"))["draft"] is True
    assert json.loads(Path(row["answer_key_path"]).read_text("utf-8"))["questions"] == []

def test_create_session_draft_compensates_files_when_database_insert_fails(tmp_path):
    db = FailingCreateSessionDb()
    with pytest.raises(RuntimeError, match="insert failed"):
        create_session_draft(db, tmp_path / "uploaded", name="测试考试")
    assert list((tmp_path / "uploaded").glob("session-draft-*")) == []
```

- [ ] **Step 2: Run draft tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_workspace_drafts.py tests/test_api_session_drafts.py -q
```

Expected: collection/import FAIL because `backend.config_workspace.drafts` and `/api/sessions/drafts` do not exist.

- [ ] **Step 3: Implement atomic placeholder creation and thin API**

```python
EMPTY_RUBRIC = {"draft": True, "total_score": 0, "questions": []}
EMPTY_ANSWER_KEY = {"draft": True, "questions": []}

def create_session_draft(db: DBManager, upload_config_dir: Path, *, name: str) -> int:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("session name must be nonblank")
    token = uuid.uuid4().hex
    rubric_path = Path(upload_config_dir) / f"session-draft-{token}-rubric.json"
    answer_path = Path(upload_config_dir) / f"session-draft-{token}-answer-key.json"
    created: list[Path] = []
    try:
        write_json_atomic(rubric_path, {**EMPTY_RUBRIC, "exam_title": clean_name})
        created.append(rubric_path)
        write_json_atomic(answer_path, EMPTY_ANSWER_KEY)
        created.append(answer_path)
        return db.create_grading_session(clean_name, str(rubric_path), str(answer_path))
    except Exception:
        remove_exact_files(created)
        raise

@contextmanager
def session_config_lock(upload_config_dir: Path, session_id: int):
    lock_key = (Path(upload_config_dir).resolve(strict=False), int(session_id))
    with _lock_for(lock_key):
        yield
```

Map `ValueError` to `400 invalid_session_draft`; do not accept path fields and return `SessionSummary`, not `SessionDetail`, so the new browser flow never receives placeholder paths.

- [ ] **Step 4: Run draft and existing session/config regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_workspace_drafts.py tests/test_api_session_drafts.py tests/test_api_write_routes.py tests/test_api_config_routes.py -q
```

Expected: all selected tests PASS; existing `/api/sessions` remains compatible.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/config_workspace backend/api/schemas/sessions.py backend/api/routers/sessions.py tests/test_config_workspace_drafts.py tests/test_api_session_drafts.py
git commit -m "feat: add recoverable session drafts"
```

---

### Task 2: Controlled Source Upload, Local Parsing And Assets

**Files:**
- Create: `backend/config_workspace/sources.py`
- Modify: `backend/config_workspace/__init__.py`
- Modify: `backend/api/dependencies.py:35-75,160-205`
- Modify: `backend/api/schemas/config.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/routers/config.py`
- Create: `tests/test_config_source_service.py`
- Create: `tests/test_api_config_sources.py`

**Interfaces:**
- Produces 32-character lowercase hex `source_id` and 64-character lowercase hex `source_revision`.
- Manifest is stored at `upload_config_dir/config_sources/session-{id}/{source_id}/manifest.json` by `os.replace()` and owns an exact file list.
- Active source pointer is stored atomically at `upload_config_dir/config_sources/session-{id}/active.json`; source upload replaces it only after the new manifest is complete, under `session_config_lock()`.
- Public source response contains only safe filename, suffix, byte count, 12-character SHA prefix, IDs, parse state and question projections.

- [ ] **Step 1: Write failing upload and parser tests**

```python
@pytest.mark.parametrize("filename", ["../数学卷.docx", "C:\\private\\数学卷.pdf"])
def test_source_filename_is_reduced_to_basename(tmp_path, filename):
    record = asyncio.run(service(tmp_path).stage_and_parse(
        session_id=7, filename=filename, chunks=chunks(valid_source_bytes(filename))
    ))
    assert record.safe_filename in {"数学卷.docx", "数学卷.pdf"}
    assert "private" not in record.safe_filename

def test_stream_overflow_removes_only_new_source_files(tmp_path):
    source_service = service(tmp_path, max_upload_bytes=4)
    with pytest.raises(ConfigSourceTooLargeError):
        asyncio.run(source_service.stage_and_parse(
            session_id=7, filename="paper.pdf", chunks=chunks(b"123", b"45")
        ))
    assert list((tmp_path / "config_sources").rglob("*.tmp")) == []

def test_public_projection_has_no_paths_text_or_base64(parsed_pdf_record):
    body = parsed_pdf_record.public_snapshot()
    assert_no_path_key_or_value(body)
    assert "document_text" not in json.dumps(body)
    assert "base64" not in json.dumps(body).casefold()
```

Add fixtures for DOCX rich text, PDF text/crops, long question, no detected questions, malformed ZIP, 501-page PDF, restart reload from manifest, source/session mismatch, question/answer asset, and exact old-source retention while a Job references it.

- [ ] **Step 2: Run source tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_source_service.py tests/test_api_config_sources.py -q
```

Expected: collection/import FAIL because `ConfigSourceService` and source routes do not exist.

- [ ] **Step 3: Implement the bounded source record and public projection**

```python
@dataclass(frozen=True, slots=True)
class ConfigQuestionPreview:
    question_id: str
    question_type: str
    question_preview: str
    answer_preview: str
    answer_present: bool
    needs_review: bool
    local_answer_trusted: bool
    has_question_asset: bool
    has_answer_asset: bool

@dataclass(frozen=True, slots=True)
class ConfigSourceRecord:
    session_id: int
    source_id: str
    source_revision: str
    safe_filename: str
    suffix: Literal[".docx", ".pdf"]
    size_bytes: int
    sha256: str
    questions: tuple[ConfigQuestionPreview, ...]
    manifest_path: Path
    private_source_path: Path
    private_blocks: tuple[dict[str, Any], ...]
    private_document_text: str
    private_question_images: dict[str, dict[str, str | None]]
    private_whole_page_images: tuple[bytes, ...]

@dataclass(frozen=True, slots=True)
class QuestionDecision:
    question_id: str
    question_type: Literal["choice", "fill_blank", "calculation", "proof", "comprehensive"]
    excluded: bool

@dataclass(frozen=True, slots=True)
class PreparedGenerationInput:
    confirmed_blocks: tuple[dict[str, Any], ...]
    document_text: str
    question_images: dict[str, dict[str, str | None]]
    whole_page_images: tuple[bytes, ...]
```

Stream to a source-owned dot-temp file while hashing and counting, reject unsupported suffix/magic before parsing, then publish the source file with `os.replace()`. Validate DOCX ZIP member/expanded sizes before `extract_docx_text`; validate PDF page count before calling existing text/image extractors. Store full blocks, document text, PDF whole-page images and question assets only in the private manifest/source directory. Strip HTML tags and `[[IMAGE:...]]` markers from public previews and cap each preview at 500 characters. `load(..., require_active=True)` compares `active.json` and rejects a replaced source; `load(..., require_active=False)` is reserved for an already-recorded Job retry. Old source directories remain until no Job payload references their source ID; cleanup reads the manifest-owned file list and never removes a directory recursively.

- [ ] **Step 4: Add source routes and controlled assets**

```python
@router.post("/sessions/{session_id}/config/sources", response_model=ConfigSourceResponse, status_code=201)
async def upload_config_source(session_id: int, request: Request, ...):
    _require_session(db, session_id)
    filename = decode_upload_filename(request.headers.get("x-upload-filename"))
    return (await source_service.stage_and_parse(
        session_id=session_id, filename=filename, chunks=request.stream()
    )).public_snapshot()

@router.get("/sessions/{session_id}/config/sources/{source_id}/questions/{question_id}/assets/{asset_kind}")
def get_config_source_asset(...):
    if asset_kind not in {"question", "answer"}:
        raise ApiError(404, "config_asset_not_found", "Config asset not found")
    content, media_type = source_service.read_asset(...)
    return Response(content, media_type=media_type, headers={"Cache-Control": "private, no-store"})
```

Return 413/415/422 stable errors without echoing filename/path/parser exception. Source GET reloads the atomic manifest after process restart.

- [ ] **Step 5: Run parser, API and path-security regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_source_service.py tests/test_api_config_sources.py tests/test_source_paper_archive_service.py tests/test_api_question_bank_write_routes.py -q
```

Expected: all selected tests PASS; no test writes outside `tmp_path`.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/config_workspace backend/api/dependencies.py backend/api/schemas/config.py backend/api/schemas/__init__.py backend/api/routers/config.py tests/test_config_source_service.py tests/test_api_config_sources.py
git commit -m "feat: add controlled config source parsing"
```

---

### Task 3: Generate From Source, Whole-Document Mode And Atomic Binding

**Files:**
- Modify: `backend/config_workspace/sources.py`
- Create: `backend/config_workspace/publish.py`
- Modify: `backend/jobs/config_generation.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/jobs/store.py:298-365`
- Modify: `backend/api/schemas/config.py`
- Modify: `backend/api/routers/config.py`
- Modify: `tests/test_config_generation_job.py`
- Modify: `tests/test_api_config_generation_jobs.py`
- Modify: `tests/test_grading_config_generation_policy.py`

**Interfaces:**
- Request: `{source_id, source_revision, generation_mode, decisions}` where mode is `per_question` or `whole_document`.
- Job payload: `{session_id, mode, generation_mode, input_id, source_id, source_revision}`; public projection removes `input_id`.
- Complete binding updates rubric, answer key, source path/SHA, source-sync reset fields and Job success in one SQLite transaction.

- [ ] **Step 1: Write failing source-generation and whole-mode tests**

```python
def test_generate_from_source_stages_private_input_and_public_job_is_safe(client, source):
    response = client.post(f"/api/sessions/{source.session_id}/config/generate-from-source", json={
        "source_id": source.source_id,
        "source_revision": source.source_revision,
        "generation_mode": "per_question",
        "decisions": [{"question_id": "Q2", "question_type": "proof", "excluded": False}],
    })
    assert response.status_code == 202
    assert response.json()["payload"] == {
        "session_id": source.session_id,
        "mode": "generate",
        "generation_mode": "per_question",
        "source_id": source.source_id,
        "source_revision": source.source_revision,
    }
    assert_private_source_content_absent(response.text)

def test_whole_pdf_generation_calls_visual_model_exactly_once(fake_llm, context):
    result = run_config_generation_job(..., llm_client_factory=lambda: fake_llm)
    assert fake_llm.json_from_images_once_calls == 1
    assert fake_llm.json_from_text_calls == 0
    assert result["outcome"] == "complete"
```

Also cover stale source revision, replaced active source, per-question partial retry, whole-mode failure with no auto-retry, cancellation, restart, archive reuse, DB bind failure, old config revision, and exact cleanup of only newly created unreferenced files.

- [ ] **Step 2: Run generation tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py tests/test_api_config_generation_jobs.py -k "source or whole or revision or archive" -q
```

Expected: FAIL because source requests and whole-document Job branching do not exist.

- [ ] **Step 3: Prepare server-owned generation input**

```python
def prepare_generation_input(record, decisions, generation_mode):
    verified = apply_question_decisions(record.private_blocks, decisions)
    if generation_mode == "per_question" and not verified.confirmed_blocks:
        raise ValueError("at least one confirmed question is required")
    return {
        "session_id": record.session_id,
        "source_id": record.source_id,
        "source_revision": record.source_revision,
        "source_suffix": record.suffix,
        "source_file": str(record.private_source_path),
        "confirmed_blocks": verified.confirmed_blocks,
        "document_text": record.private_document_text,
        "question_images": record.private_question_images,
        "whole_page_images": record.private_whole_page_images,
    }
```

Teacher decisions may only change `question_type` among the five existing types or exclude a known question ID. Reject unknown/duplicate IDs and any extra fields. Stage through existing atomic config input storage; public Job projection includes IDs/revisions only.

- [ ] **Step 4: Extend the Job runner without changing prompts**

```python
if generation_mode == "per_question":
    payload = generate_grading_config_from_confirmed_blocks(
        confirmed_blocks, document_text, llm_client=client,
        model_name=_config_model(client), report=report, q_images=question_images or None,
    )
elif source_suffix == ".docx":
    payload = generate_grading_config_from_text(
        document_text, llm_client=client, model_name=_config_model(client), report=report,
    )
else:
    payload = generate_grading_config_from_images(
        whole_page_images, "", llm_client=client,
        model_name=_config_model(client), report=report,
    )
```

Do not catch and retry whole-mode model failures. Keep selected-question retry limited to per-question partial Jobs. Under the per-session lock, recheck source/config revision, archive the exact private source with `archive_source_paper()`, atomically publish JSON, and call the extended JobStore transaction.

- [ ] **Step 5: Extend the single final SQLite transaction**

```python
def finish_config_generation_and_bind(..., source_paper_path, source_paper_sha256, result) -> bool:
    # existing BEGIN IMMEDIATE and running/cancel checks
    session_update = conn.execute("""
        UPDATE grading_sessions
        SET rubric_path = ?, answer_key_path = ?,
            source_paper_path = ?, source_paper_sha256 = ?,
            question_bank_sync_state = CASE WHEN COALESCE(source_paper_sha256, '') <> ?
                THEN 'not_started' ELSE question_bank_sync_state END,
            question_bank_sync_details_json = CASE WHEN COALESCE(source_paper_sha256, '') <> ?
                THEN '{}' ELSE question_bank_sync_details_json END,
            question_bank_sync_error = CASE WHEN COALESCE(source_paper_sha256, '') <> ?
                THEN NULL ELSE question_bank_sync_error END,
            question_bank_sync_updated_at = CASE WHEN COALESCE(source_paper_sha256, '') <> ?
                THEN NULL ELSE question_bank_sync_updated_at END,
            updated_at = datetime('now','localtime')
        WHERE id = ? AND rubric_path = ? AND answer_key_path = ?
    """, (...))
```

Use pre-update source SHA values in the change comparison, require exactly one session row, then update the Job to succeeded and commit. A failed transaction leaves the old session binding and the runner removes only files/archive copies it created and that were not reused.

- [ ] **Step 6: Run Job/API/policy regressions**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py tests/test_api_config_generation_jobs.py tests/test_grading_config_generation_policy.py tests/test_api_job_lifecycle.py tests/test_job_manager.py tests/test_job_store.py -q
```

Expected: all selected tests PASS; existing P1-17 endpoints remain green.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/config_workspace backend/jobs/config_generation.py backend/jobs/default_handlers.py backend/jobs/store.py backend/api/schemas/config.py backend/api/routers/config.py tests/test_config_generation_job.py tests/test_api_config_generation_jobs.py tests/test_grading_config_generation_policy.py
git commit -m "feat: generate configs from controlled sources"
```

---

### Task 4: Server-Authoritative Rubric Editor Service

**Files:**
- Create: `backend/config_workspace/editor.py`
- Modify: `backend/config_workspace/__init__.py`
- Modify: `web_app.py:5774-6728`
- Create: `tests/test_config_editor_service.py`
- Modify: `tests/test_unified_rubric_rows.py`

**Interfaces:**
- Produces immutable `ConfigEditorRow` with stable `row_id = sha256(question_id\0part_id\0step_id)[:24]`.
- Consumes edits addressed by `row_id`; no visible label is used as identity.
- Produces `split`, `replace_parts` commands and preserves teacher part IDs through refine.

- [ ] **Step 1: Write failing projection and reverse-write feature tests**

```python
def test_projection_preserves_hidden_question_part_step_identity(payload):
    rows = project_config_editor(payload)
    assert [(r.question_id, r.part_id, r.step_id) for r in rows] == [
        ("Q12", "P1", "S1"), ("Q12", "P1", "S2")
    ]
    assert len({r.row_id for r in rows}) == 2

def test_apply_edits_changes_only_addressed_score_and_answer(payload):
    row = project_config_editor(payload)[0]
    updated = apply_config_editor_changes(payload, edits=[ConfigEditorEdit(
        row_id=row.row_id, score=4, standard_answer="完整证明"
    )], commands=[])
    assert updated["rubric"]["questions"][0]["parts"][0]["steps"][0]["step_score"] == 4
    assert updated["answer_key"]["questions"][0]["parts"][0]["answer"] == "完整证明"
```

Cover whole-question rows, multiple parts, multiple steps, readable match rules, accepted answers, answer-only caps, final-answer flags, missing IDs, duplicate IDs, score totals, objective all-or-nothing semantics, split 2–30, replace-parts unique IDs and deep-copy immutability.

- [ ] **Step 2: Run editor tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_editor_service.py tests/test_unified_rubric_rows.py -q
```

Expected: import FAIL for the new editor service.

- [ ] **Step 3: Implement stable English DTOs and pure-Python transformations**

```python
@dataclass(frozen=True, slots=True)
class ConfigEditorRow:
    row_id: str
    question_id: str
    part_id: str
    step_id: str
    part_label: str
    question_type: str
    core_goal: str
    score: float
    standard_answer: str
    accepted_answers: tuple[str, ...]
    match_rule: str
    knowledge: str
    answer_only_max_score: float | None
    require_final_answer: bool | None

@dataclass(frozen=True, slots=True)
class ConfigEditorEdit:
    row_id: str
    score: float | None = None
    standard_answer: str | None = None
    accepted_answers: tuple[str, ...] | None = None
    answer_only_max_score: float | None = None
    require_final_answer: bool | None = None

@dataclass(frozen=True, slots=True)
class ManualPartInput:
    part_id: str
    score: float
    core_goal: str

@dataclass(frozen=True, slots=True)
class SplitScoringUnitCommand:
    kind: Literal["split"]
    question_id: str
    count: int
    style: Literal["subquestion", "blank"]

@dataclass(frozen=True, slots=True)
class ReplaceScoringUnitsCommand:
    kind: Literal["replace_parts"]
    question_id: str
    parts: tuple[ManualPartInput, ...]
```

Move transformation logic, not Streamlit rendering, into the service. Apply edits to a JSON deep copy, recompute part/question scores from steps, refresh quality warnings, and call `validate_generated_config()` at the final publish boundary. Return stable issues `{code, severity, row_id, field, message}` without parser/validation stack details.

- [ ] **Step 4: Keep Streamlit as a compatibility adapter**

```python
def build_unified_rubric_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [editor_row_to_streamlit_dict(row) for row in project_config_editor(payload)]

def _apply_unified_table_to_payload(payload, edited_df):
    edits = streamlit_dataframe_to_editor_edits(payload, edited_df.to_dict(orient="records"))
    return apply_config_editor_changes(payload, edits=edits, commands=[])
```

Make `_split_payload_question_parts()` and `_apply_manual_part_rows()` delegate to the same service commands. Existing Chinese labels and visible Streamlit behavior stay unchanged.

- [ ] **Step 5: Run editor, generation and Streamlit regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_editor_service.py tests/test_unified_rubric_rows.py tests/test_grading_config_generation_policy.py tests/test_pdf_crop_preview_ui.py -q
```

Expected: all selected tests PASS and legacy feature fixtures produce equivalent values/IDs.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/config_workspace/editor.py backend/config_workspace/__init__.py web_app.py tests/test_config_editor_service.py tests/test_unified_rubric_rows.py
git commit -m "refactor: centralize rubric editor rules"
```

---

### Task 5: Editor API, Revision, Refine And Partial Save Results

**Files:**
- Modify: `backend/config_workspace/publish.py`
- Modify: `db_manager.py:807-829`
- Modify: `backend/jobs/config_generation.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/schemas/config.py`
- Modify: `backend/api/routers/config.py`
- Modify: `web_app.py:7505-7536`
- Create: `tests/test_api_config_editor.py`
- Modify: `tests/test_config_generation_job.py`
- Modify: `tests/test_api_openapi_contract.py`

**Interfaces:**
- `GET editor` returns `{configured, revision, rows, total_score, issues, source}` without paths.
- `PUT editor` accepts `{revision, edits, commands}` and returns a fresh editor response plus `save_result`.
- `POST editor/refine` accepts `{revision, commands}` and returns a standard 202 Job.

- [ ] **Step 1: Write failing API, revision and partial-success tests**

```python
def test_new_draft_editor_is_truthfully_unconfigured(client, draft_session):
    body = client.get(f"/api/sessions/{draft_session}/config/editor").json()
    assert body == {
        "session_id": draft_session, "configured": False, "revision": body["revision"],
        "rows": [], "total_score": 0, "issues": [], "source": None,
    }

def test_stale_revision_preserves_current_config(client, configured_session):
    first = client.get(f"/api/sessions/{configured_session}/config/editor").json()
    save_once(client, configured_session, first["revision"])
    stale = client.put(f"/api/sessions/{configured_session}/config/editor", json={
        "revision": first["revision"], "edits": [], "commands": []
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_revision_conflict"
```

Cover 422 row/field issues, no-op save, double-file publish failure, DB failure, source preservation, mapping absent/refreshed/reconfirm-required, refine part-ID preservation, old refine Job conflict, no paths in all responses, and OpenAPI additionalProperties guards.

- [ ] **Step 2: Run editor API tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_config_editor.py tests/test_api_openapi_contract.py -q
```

Expected: 404 for the three editor routes and missing schemas.

- [ ] **Step 3: Implement revision and authoritative editor response**

```python
def config_revision(session, payload):
    canonical = json.dumps({
        "session_id": int(session["id"]),
        "rubric": payload["rubric"],
        "answer_key": payload["answer_key"],
        "source_sha256": str(session.get("source_paper_sha256") or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

The route loads current files through existing guarded resolution, recognizes only the exact draft marker as `configured=false`, projects rows, and emits source safe name/type/12-character SHA prefix only. PUT compares revision under the session lock before applying changes and again before DB binding.

- [ ] **Step 4: Implement atomic manual save and mapping result**

```python
@dataclass(frozen=True, slots=True)
class ConfigSaveResult:
    config_saved: bool
    mapping_status: Literal["not_present", "refreshed", "reconfirm_required"]
    mapping_message: str

def save_editor(...):
    with session_config_lock(session_id):
        current = load_current_payload(...)
        require_revision(expected_revision, current)
        candidate = apply_config_editor_changes(current.payload, edits=edits, commands=commands)
        validate_generated_config(candidate)
        rubric_path, answer_path = save_generated_config(upload_config_dir, candidate, token)
        db.publish_grading_session_config(..., expected=current.paths)
    return refresh_mapping_after_config_save(...)
```

`publish_grading_session_config()` performs one SQLite transaction, preserves the existing source binding, and requires expected old config paths. Extract mapping refresh orchestration into `publish.py`; `web_app._refresh_template_mapping_from_session()` delegates to it. Catch only the post-save mapping exception and return `reconfirm_required`; do not roll back the already committed config or expose the exception.

- [ ] **Step 5: Add refine Job mode**

```python
if mode == "refine":
    candidate = apply_config_editor_changes(
        existing_payload, edits=[], commands=load_refine_commands(inputs)
    )
    expected_part_ids = editor_part_ids(candidate)
    payload = refine_grading_config_from_manual_structure(
        candidate, llm_client=client, model_name=_config_model(client)
    )
    if editor_part_ids(payload) != expected_part_ids:
        raise ValueError("refined config changed teacher scoring-unit identities")
```

Refine stages server-side current config plus commands, records expected config revision, uses the existing Job type, and follows the same full-success publication/revision guard. It never accepts nested Rubric JSON from the browser.

- [ ] **Step 6: Run API, Job, Streamlit and OpenAPI regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_config_editor.py tests/test_config_generation_job.py tests/test_api_config_routes.py tests/test_api_config_generation_jobs.py tests/test_unified_rubric_rows.py tests/test_api_openapi_contract.py -q
```

Expected: all selected tests PASS; existing config endpoints remain compatible.

- [ ] **Step 7: Commit Task 5**

```powershell
git add backend/config_workspace/publish.py db_manager.py backend/jobs/config_generation.py backend/jobs/default_handlers.py backend/api/dependencies.py backend/api/schemas/config.py backend/api/routers/config.py web_app.py tests/test_api_config_editor.py tests/test_config_generation_job.py tests/test_api_openapi_contract.py
git commit -m "feat: add revisioned rubric editor API"
```

---

### Task 6: Typed Frontend Foundation, Navigation And Workspace State

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/sessions.ts`
- Create: `frontend/src/api/config-workspace.ts`
- Create: `frontend/src/stores/config-workspace.ts`
- Modify: `frontend/src/stores/session.ts`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layouts/AppShell.vue`
- Modify: `frontend/src/components/shell/AppTopbar.vue`
- Create: `frontend/src/views/SessionConfigView.vue`
- Create: `frontend/src/components/config/ConfigStageRail.vue`
- Create: `frontend/src/components/config/SessionDraftPanel.vue`
- Create: `frontend/src/__tests__/config-workspace-api.spec.ts`
- Create: `frontend/src/__tests__/config-workspace-store.spec.ts`
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`
- Modify: `frontend/src/__tests__/session-store.spec.ts`
- Modify: `frontend/src/components/shell/__tests__/app-shell.spec.ts`

**Interfaces:**
- `createSessionDraft(name)`, `renameSession(id, name)`, `uploadConfigSource(...)`, `fetchConfigSource(...)`, `submitConfigGeneration(...)`, `fetchConfigEditor(...)`, `saveConfigEditor(...)`, `refineConfigEditor(...)` all strictly decode responses.
- Store persists only `{sessionId, phase, sourceId, sourceRevision, jobId, decisions}`; editor rows/answers stay memory-only.

- [ ] **Step 1: Write failing raw upload, route and Store tests**

```ts
it('sends a File once as octet-stream without JSON encoding', async () => {
  const file = new File(['%PDF-test'], '数学卷.pdf', { type: 'application/pdf' })
  await uploadConfigSource(7, file)
  expect(fetchMock).toHaveBeenCalledWith('/api/sessions/7/config/sources', expect.objectContaining({
    method: 'POST', body: file,
    headers: expect.objectContaining({
      'content-type': 'application/octet-stream',
      'x-upload-filename': encodeURIComponent('数学卷.pdf'),
    }),
  }))
})

it('persists no rubric rows or answer text', () => {
  store.setEditor(editorWithAnswer('标准答案正文'))
  store.persistSafeIndex()
  expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).not.toContain('标准答案正文')
})
```

Assert navigation order is `sessions`, `grading`; `/` redirects to `/sessions`; draft creation selects and reloads the new session; stale IDs clear; old session/source responses cannot overwrite a later selection; AppShell warns when review or config drafts are dirty.

- [ ] **Step 2: Run foundation tests and verify RED**

```powershell
Set-Location frontend
npm test -- --run src/__tests__/config-workspace-api.spec.ts src/__tests__/config-workspace-store.spec.ts src/__tests__/navigation-router.spec.ts src/__tests__/session-store.spec.ts src/components/shell/__tests__/app-shell.spec.ts
```

Expected: FAIL because the config API, Store and `/sessions` route do not exist.

- [ ] **Step 3: Extend ApiClient for raw bodies without changing retry rules**

```ts
export interface ApiRequestOptions<T> {
  method?: ApiMethod
  body?: unknown
  rawBody?: BodyInit
  headers?: Readonly<Record<string, string>>
  decode: ResponseDecoder<T>
  signal?: AbortSignal
  timeoutMs?: number
}

if (options.body !== undefined && options.rawBody !== undefined) {
  throw contractError('ambiguous_request_body', requestId, null)
}
const body = options.rawBody ?? (
  options.body === undefined ? undefined : JSON.stringify(options.body)
)
```

Merge only caller headers with safe base headers; caller cannot remove `accept` or `x-request-id`. Keep GET at three attempts and every write at one attempt.

- [ ] **Step 4: Implement strict adapters and safe Store persistence**

```ts
export interface PersistedConfigWorkspace {
  sessionId: number
  phase: 'draft' | 'source' | 'generation' | 'editor'
  sourceId: string | null
  sourceRevision: string | null
  jobId: number | null
  decisions: QuestionDecision[]
}
```

Every decoder validates exact scalar/list shapes and rejects path-like keys. `setEditor()` keeps rows and edit buffers in refs only. `hasDirtyEditor` becomes true after a local edit/command and false only after a successful authoritative reload/save.

- [ ] **Step 5: Add truthful navigation and phase shell**

```ts
export const sessionRouteDefinition = {
  id: 'sessions', label: '考试配置', path: '/sessions',
  title: '考试配置', description: '创建考试并准备评分依据', breadcrumb: '考试配置',
} as const
export const navigationItems = [sessionRouteDefinition, reviewRouteDefinition] as const
```

Render the two links in `AppTopbar`, preserve the current-exam selector, and mount `SessionConfigView` at `/sessions`. The initial page shows a real empty/loading/error state and `SessionDraftPanel`; `ConfigStageRail` derives completion from session/source/job/editor facts and never writes a database status.

- [ ] **Step 6: Run foundation tests, typecheck and lint**

```powershell
Set-Location frontend
npm test -- --run src/__tests__/config-workspace-api.spec.ts src/__tests__/config-workspace-store.spec.ts src/__tests__/navigation-router.spec.ts src/__tests__/session-store.spec.ts src/components/shell/__tests__/app-shell.spec.ts
npm run typecheck
npm run lint
```

Expected: all selected tests PASS; typecheck/lint exit 0.

- [ ] **Step 7: Commit Task 6**

```powershell
git add frontend/src/api frontend/src/stores frontend/src/navigation.ts frontend/src/router/index.ts frontend/src/layouts/AppShell.vue frontend/src/components/shell frontend/src/components/config frontend/src/views/SessionConfigView.vue frontend/src/__tests__
git commit -m "feat: add session configuration workspace shell"
```

---

### Task 7: Source Upload And Question Review UI

**Files:**
- Create: `frontend/src/components/config/ConfigSourceUpload.vue`
- Create: `frontend/src/components/config/QuestionBlockReview.vue`
- Create: `frontend/src/components/config/__tests__/config-source-upload.spec.ts`
- Create: `frontend/src/components/config/__tests__/question-block-review.spec.ts`
- Modify: `frontend/src/views/SessionConfigView.vue`
- Modify: `frontend/src/stores/config-workspace.ts`
- Create: `frontend/src/styles/session-config.css`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Upload emits `uploaded(source)`; question review emits only `QuestionDecision[]`.
- Source replacement clears generation/editor context only after the new source is accepted; failed replacement retains the previous source.

- [ ] **Step 1: Write failing component tests**

```ts
it('keeps the accepted source visible when replacement fails', async () => {
  const wrapper = mountSourceUpload({ source: acceptedSource, uploader: rejectUpload })
  await chooseFile(wrapper, new File(['bad'], 'bad.pdf'))
  expect(wrapper.text()).toContain(acceptedSource.safe_filename)
  expect(wrapper.get('[role="alert"]').text()).toContain('新文件未接收成功')
})

it('emits only type and exclusion decisions for known questions', async () => {
  const wrapper = mountQuestionReview(sourceWithLongQuestions)
  await wrapper.get('[aria-label="Q2 题型"]').setValue('proof')
  await wrapper.get('[aria-label="排除 Q3"]').setValue(true)
  expect(wrapper.emitted('update:decisions')?.at(-1)?.[0]).toEqual([
    { question_id: 'Q2', question_type: 'proof', excluded: false },
    { question_id: 'Q3', question_type: 'comprehensive', excluded: true },
  ])
})
```

Cover wrong type, 200 MiB client precheck, indeterminate upload, parse error, zero questions, answer-present flag, controlled asset URL, long content expand/collapse, keyboard labels and source revision reset.

- [ ] **Step 2: Run component tests and verify RED**

```powershell
Set-Location frontend
npm test -- --run src/components/config/__tests__/config-source-upload.spec.ts src/components/config/__tests__/question-block-review.spec.ts
```

Expected: FAIL because both components do not exist.

- [ ] **Step 3: Implement upload and dense review components**

Use one visible file input labeled “选择 DOCX 或 PDF”, one primary action “上传并拆题”, and an indeterminate status because native Fetch has no portable upload-progress callback. Show safe filename, formatted bytes, 12-character fingerprint and source revision; never render a path.

```vue
<li v-for="question in source.questions" :key="question.question_id" class="question-review__row">
  <strong class="question-review__id">{{ question.question_id }}</strong>
  <select :aria-label="`${question.question_id} 题型`" ... />
  <span v-if="question.needs_review" class="status status--warning">需要核对</span>
  <button type="button" @click="toggle(question.question_id)">展开题目</button>
  <label><input type="checkbox" ... />排除此题</label>
</li>
```

Use a continuous list with rules, not one card per question. Asset `<img>` uses only the API URL constructed from semantic IDs and includes alt text “Qx 题目图/答案图”.

- [ ] **Step 4: Implement the grading-ledger visual direction**

```css
.config-workspace {
  max-width: var(--content-max-width);
  margin-inline: auto;
  padding: var(--space-6);
}
.config-stage-rail {
  border-inline-start: var(--border-selected-width) solid var(--color-accent);
}
.question-review__row {
  display: grid;
  grid-template-columns: 72px 132px minmax(0, 1fr) auto;
  border-block-end: var(--border-width) solid var(--color-border-subtle);
}
```

At 1024px reduce gaps and stack row actions beneath content without page-level horizontal overflow. Respect `prefers-reduced-motion`; no decorative animation, gradients or new colors.

- [ ] **Step 5: Run components, build and CSS overflow guards**

```powershell
Set-Location frontend
npm test -- --run src/components/config/__tests__/config-source-upload.spec.ts src/components/config/__tests__/question-block-review.spec.ts
npm run build
```

Expected: tests and build PASS.

- [ ] **Step 6: Commit Task 7**

```powershell
git add frontend/src/components/config frontend/src/views/SessionConfigView.vue frontend/src/stores/config-workspace.ts frontend/src/styles/session-config.css frontend/src/main.ts
git commit -m "feat: add source upload and question review"
```

---

### Task 8: Generation Progress, Retry, Cancel And Refresh Recovery

**Files:**
- Create: `frontend/src/components/config/ConfigGenerationPanel.vue`
- Create: `frontend/src/components/config/__tests__/config-generation-panel.spec.ts`
- Modify: `frontend/src/stores/config-workspace.ts`
- Modify: `frontend/src/views/SessionConfigView.vue`
- Modify: `frontend/src/api/config-workspace.ts`
- Modify: `frontend/src/__tests__/config-workspace-store.spec.ts`

**Interfaces:**
- Consumes existing `useJobStore().track/refresh/cancel` and safe config Job summaries.
- Produces selected failed IDs and explicit retry request; whole-document failures produce a fresh explicit generation request, never the partial retry endpoint.

- [ ] **Step 1: Write failing Job UI and recovery tests**

```ts
it('retries only checked failed questions and preserves success count', async () => {
  const wrapper = mountGeneration({ job: partialJob(['Q2', 'Q5']) })
  await wrapper.get('[aria-label="选择失败题 Q5"]').setValue(true)
  await wrapper.get('button[name="重试所选题"]').trigger('click')
  expect(retry).toHaveBeenCalledWith(sessionId, job.id, ['Q5'])
  expect(wrapper.text()).toContain('已成功 3 题')
})

it('does not relabel cancel-requested as cancelled before the server terminal state', () => {
  const wrapper = mountGeneration({ job: runningJob({ cancel_requested: true }) })
  expect(wrapper.text()).toContain('正在等待当前模型请求返回')
  expect(wrapper.text()).not.toContain('已取消')
})
```

Cover per-question/whole mode copy, submit disabled state, write-once behavior, partial/succeeded/failed/cancelled, sync error with retained job, stored job recovery, process-restart failed detail, old session response isolation, and editor reload after complete generation.

- [ ] **Step 2: Run generation UI tests and verify RED**

```powershell
Set-Location frontend
npm test -- --run src/components/config/__tests__/config-generation-panel.spec.ts src/__tests__/config-workspace-store.spec.ts
```

Expected: FAIL because generation panel actions do not exist.

- [ ] **Step 3: Implement explicit generation and retry actions**

```ts
async function startGeneration(mode: GenerationMode): Promise<void> {
  if (!store.canGenerate || submitting.value) return
  submitting.value = true
  try {
    const job = await submitConfigGeneration(sessionId, store.sourceRequest(mode))
    jobStore.track(job)
    store.attachJob(job.id)
  } finally {
    submitting.value = false
  }
}
```

Do not catch a write failure and submit again. On partial Job show totals and checkboxes for only `failed_question_ids`. On complete Job fetch the editor and move the derived current phase to `editor`. On source/session change, invalidate pending view updates by generation counter but leave the tracked Job available in JobStore.

- [ ] **Step 4: Run Job Store and component regression**

```powershell
Set-Location frontend
npm test -- --run src/components/config/__tests__/config-generation-panel.spec.ts src/__tests__/config-workspace-store.spec.ts src/__tests__/job-store.spec.ts src/api/__tests__/jobs.spec.ts
npm run typecheck
```

Expected: all selected tests PASS and typecheck exits 0.

- [ ] **Step 5: Commit Task 8**

```powershell
git add frontend/src/components/config/ConfigGenerationPanel.vue frontend/src/components/config/__tests__/config-generation-panel.spec.ts frontend/src/stores/config-workspace.ts frontend/src/views/SessionConfigView.vue frontend/src/api/config-workspace.ts frontend/src/__tests__/config-workspace-store.spec.ts
git commit -m "feat: add recoverable config generation workflow"
```

---

### Task 9: Rubric Ledger, Scoring Units And Safe Save UX

**Files:**
- Create: `frontend/src/components/config/RubricEditorTable.vue`
- Create: `frontend/src/components/config/ScoringUnitEditor.vue`
- Create: `frontend/src/components/config/ConfigSaveResult.vue`
- Create: `frontend/src/components/config/__tests__/rubric-editor-table.spec.ts`
- Create: `frontend/src/components/config/__tests__/scoring-unit-editor.spec.ts`
- Create: `frontend/src/components/config/__tests__/config-save-result.spec.ts`
- Modify: `frontend/src/views/SessionConfigView.vue`
- Modify: `frontend/src/stores/config-workspace.ts`
- Modify: `frontend/src/styles/session-config.css`

**Interfaces:**
- Table emits `ConfigEditorEdit` by stable `row_id`; scoring unit editor emits typed `split` or `replace_parts` commands.
- Save sends one PUT with the loaded revision and current edits/commands; success replaces all editor state with the authoritative response.

- [ ] **Step 1: Write failing ledger, command and conflict tests**

```ts
it('addresses an edited score by hidden row id', async () => {
  const wrapper = mountRubricEditor(editorWithTwoSteps)
  await wrapper.get('[aria-label="Q12 P1 S1 分值"]').setValue('4')
  expect(wrapper.emitted('edit')?.at(-1)?.[0]).toEqual({ row_id: 'row-q12-p1-s1', score: 4 })
})

it('retains local edits after a 409 and offers an explicit reload', async () => {
  save.mockRejectedValue(apiError(409, 'config_revision_conflict'))
  const wrapper = mountConfigViewWithDirtyEditor()
  await wrapper.get('button[name="保存评分依据"]').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('服务器已有较新版本')
  expect(wrapper.get('[aria-label="Q12 P1 S1 分值"]').element.value).toBe('4')
})
```

Cover score/answer/accepted forms, issue row focus, 100-point blocking, normal warnings, long text, sticky columns, split count/style, replace part IDs/scores, refine Job, dirty beforeunload, failed save retained edits, successful authoritative reset, mapping statuses and disabled double submit.

- [ ] **Step 2: Run editor component tests and verify RED**

```powershell
Set-Location frontend
npm test -- --run src/components/config/__tests__/rubric-editor-table.spec.ts src/components/config/__tests__/scoring-unit-editor.spec.ts src/components/config/__tests__/config-save-result.spec.ts
```

Expected: FAIL because the editor components do not exist.

- [ ] **Step 3: Implement the dense server-driven ledger**

```vue
<div class="rubric-ledger__viewport" tabindex="0" aria-label="评分依据编辑表">
  <table>
    <thead><tr><th>题号</th><th>评分单元</th><th>评分点</th><th>分值</th><th>标准答案</th><th>等价答案</th></tr></thead>
    <tbody>
      <tr v-for="row in rows" :key="row.row_id" :data-row-id="row.row_id">
        <th scope="row">{{ row.question_id }}</th>
        <td>{{ row.part_label }}</td>
        <td>{{ row.core_goal }}</td>
        <td><input type="number" :aria-label="`${identity(row)} 分值`" ... /></td>
        <td><textarea :aria-label="`${identity(row)} 标准答案`" ... /></td>
        <td><textarea :aria-label="`${identity(row)} 等价答案`" ... /></td>
      </tr>
    </tbody>
  </table>
</div>
```

Question/part labels and server-computed match rules are read-only. Table may scroll horizontally inside its explicit viewport; `document.documentElement` must not overflow. Keep the save bar sticky inside the workspace and show exactly one primary button.

- [ ] **Step 4: Implement commands, save and conflict recovery**

```ts
async function saveEditor(): Promise<void> {
  const request = store.buildSaveRequest()
  saving.value = true
  try {
    const response = await saveConfigEditor(sessionId, request)
    store.replaceWithAuthoritativeEditor(response)
  } catch (error) {
    if (isApiError(error, 'config_revision_conflict')) store.markConflict()
    else store.markSaveFailure()
  } finally {
    saving.value = false
  }
}
```

Conflict “重新加载最新版本” first explains that local edits will be discarded and requires a second explicit click; do not auto-merge. Mapping `reconfirm_required` copy is “评分依据已保存；样卷映射需要回旧入口重新确认”, while `refreshed` says “评分依据已保存，样卷映射已刷新”.

- [ ] **Step 5: Run all config frontend tests, lint, typecheck and build**

```powershell
Set-Location frontend
npm test -- --run src/__tests__/config-workspace-api.spec.ts src/__tests__/config-workspace-store.spec.ts src/components/config
npm run lint
npm run typecheck
npm run build
```

Expected: all tests PASS; lint/typecheck/build exit 0.

- [ ] **Step 6: Commit Task 9**

```powershell
git add frontend/src/components/config frontend/src/views/SessionConfigView.vue frontend/src/stores/config-workspace.ts frontend/src/styles/session-config.css
git commit -m "feat: add rubric ledger and safe save workflow"
```

---

### Task 10: Browser Flow, Visual QA, Documentation And Handoff

**Files:**
- Create: `frontend/e2e/session-config.spec.ts`
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`
- Modify: `frontend/src/components/shell/__tests__/app-shell.spec.ts`
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Create: `docs/user-testing/checkpoints/P2-09-session-config-rubric-quick.md`
- Modify: `docs/superpowers/plans/2026-07-15-p2-09-session-config-rubric-implementation.md`

**Interfaces:**
- Produces repeatable mock-browser evidence for the complete flow and five viewports.
- Produces a `waiting_review` feature head only after automatic gates pass; user quick test remains pending until independent review anchors an exact SHA.

- [x] **Step 1: Write failing Playwright workflow and layout checks**

```ts
test('draft to saved rubric survives partial generation and refresh', async ({ page }) => {
  await installConfigWorkspaceMockApi(page, { firstGeneration: 'partial' })
  await page.goto('/sessions')
  await page.getByLabel('考试名称').fill('七年级数学期末')
  await page.getByRole('button', { name: '创建考试草稿' }).click()
  await uploadSyntheticDocx(page)
  await page.getByLabel('Q2 题型').selectOption('proof')
  await page.getByRole('button', { name: '开始逐题生成' }).click()
  await expect(page.getByText('Q3 生成失败')).toBeVisible()
  await page.reload()
  await page.getByLabel('选择失败题 Q3').check()
  await page.getByRole('button', { name: '重试所选题' }).click()
  await page.getByLabel('Q1 Q1-P1 S1 分值').fill('20')
  await page.getByRole('button', { name: '保存评分依据' }).click()
  await expect(page.getByText('评分依据已保存')).toBeVisible()
})
```

Add whole-mode manual retry, upload/parse failure, save 422, revision 409 with retained draft, mapping partial success, cancel race, stale response isolation, long questions/multiple parts, keyboard focus, no console/page errors, and `scrollWidth <= clientWidth` at all five viewports.

- [x] **Step 2: Run Playwright tests and verify RED**

```powershell
Set-Location frontend
npx playwright test e2e/session-config.spec.ts --project=chromium
```

Expected: FAIL until the mock routes, stable selectors and all workspace states are complete.

- [x] **Step 3: Complete browser fixtures and perform visual critique**

Use only synthetic Chinese exam text and generated placeholder media. Capture screenshots for the upload review, partial failure and rubric ledger at 1024×768 and 1440×900. Inspect them for page overflow, clipped controls, sticky-column overlap, focus visibility, overly dominant decoration and error proximity. Remove any redundant panel border or badge that competes with the blue stage margin; do not add decorative elements to fill whitespace.

- [x] **Step 4: Run focused backend and complete frontend gates**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_workspace_drafts.py tests/test_api_session_drafts.py tests/test_config_source_service.py tests/test_api_config_sources.py tests/test_config_generation_job.py tests/test_api_config_generation_jobs.py tests/test_config_editor_service.py tests/test_api_config_editor.py tests/test_api_config_routes.py tests/test_unified_rubric_rows.py tests/test_grading_config_generation_policy.py tests/test_api_openapi_contract.py -q
Set-Location frontend
npm run lint
npm run typecheck
npm test
npm run build
npx playwright test e2e/session-config.spec.ts --project=chromium
```

Expected: all tests PASS and all quality commands exit 0.

- [x] **Step 5: Run affected regression, diff and quick smoke**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_app.py tests/test_api_read_routes.py tests/test_api_write_routes.py tests/test_api_jobs.py tests/test_api_job_lifecycle.py tests/test_answer_region_commit_service.py tests/test_source_paper_archive_service.py -q
git diff --check
..\..\runtime\python\python.exe tools/smoke_check.py --skip-tests
```

Expected: all selected regression tests PASS; diff check and quick smoke exit 0. Full pytest is not run on the feature branch unless a risk trigger from `AGENTS.md` occurs.

- [x] **Step 6: Update implemented architecture facts and generate the real quick checklist**

After the page runs and browser selectors are verified, record only implemented facts in `ARCHITECTURE.md`. Create the 5–10 minute checklist with actual start command, loopback URL, visible build/session marker, synthetic dataset, exact six user actions, expected outcomes, stop command and feedback severity fields. Do not claim user acceptance or include production paths/data.

- [x] **Step 7: Recheck scope, Git history, stash and real-data fingerprints**

```powershell
git status --short
git log --first-parent --name-only origin/main..HEAD
git stash list --format='%H'
..\..\runtime\python\python.exe tools/handoff_status.py --plan docs/superpowers/plans/2026-07-15-p2-09-session-config-rubric-implementation.md --repo .
```

Confirm the first first-parent commit changes only this plan; no package commit or new stash contains `user_data/`; real grading/question-bank database length, UTC timestamp and SHA-256 match the recorded领取 baseline.

- [x] **Step 8: Create functional checkpoint and `waiting_review` handoff**

Update Implementation Evidence with exact RED/GREEN and gate counts. Change the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: pending`, `真实数据指纹: unchanged`, `夜间动作: report_only`. Commit P2-09 code, tests, implemented architecture facts, checklist and plan; do not push or integrate before independent review.

---

## Independent Review And User Quick Test

1. Review the final feature SHA against this plan and the approved design spec; perform separate requirement and code-quality passes.
2. Fix every Critical/Important finding with focused RED/GREEN and affected regression; rerun only gates whose evidence was invalidated.
3. When review is 0 Critical / 0 Important, create a plan-only anchor commit whose handoff records the reviewed parent SHA, `自动验证: passed`, `独立复审: passed`, `用户验收: pending`, state `waiting_user`.
4. Run `tools/handoff_status.py` and launch the synthetic loopback environment for the versioned quick checklist. The user tests only that exact reviewed SHA.
5. If the user reports Blocker/Major, return to implementation and review; do not edit the evidence checklist to hide the result.
6. After the user explicitly passes, create one evidence commit that modifies only the declared P2-09 checklist and records the same reviewed SHA plus `passed`; then create one final plan-only `verified_pending_integration` handoff commit.
7. Follow the standard integration branch, affected regression, complete smoke, push, PR, merge and baseline synchronization flow authorized by the user’s P2-09 start instruction. Never push `main` directly.

## Plan Self-Review

- **Spec coverage:** Goals/scope and the approved pre-generation draft decision map to Task 1; upload/local parsing/source recovery map to Task 2; per-question/whole generation, retry, cancellation, archive and atomic binding map to Task 3; unified rows/manual units map to Task 4; revision/save/refine/mapping partial success map to Task 5; navigation/state/privacy map to Task 6; source review maps to Task 7; Job recovery maps to Task 8; dense editor/conflict UX maps to Task 9; five viewports, failures, quick test, review and integration map to Task 10. No approved design section is left without an implementation and verification task.
- **Scope check:** Backend and Vue changes are not independent products: neither can deliver the approved draft-to-saved-rubric flow alone. They remain one package, while each task ends in an independently reviewable test checkpoint.
- **Placeholder scan:** The plan contains no deferred implementation instruction, unspecified error handling, generic “write tests” step or missing command. Protocol values such as `pending` describe the current handoff state rather than unfinished plan content.
- **Type consistency:** `QuestionDecision`, `PreparedGenerationInput`, `ConfigSourceRecord`, `ConfigEditorRow`, `ConfigEditorEdit`, `ManualPartInput`, scoring-unit commands and `ConfigSaveResult` are defined before downstream use. Public route names, generation modes, revision names and frontend DTO fields remain identical across tasks.
- **Safety check:** Every write path is server-derived, write requests are single-attempt, exact-file compensation replaces recursive deletion, partial results never bind, user-data tests are forbidden, and rollback never silently deletes already-created business records.

## Rollback

- Revert the P2-09 functional commit chain; no database Schema rollback is required.
- Existing Streamlit configuration remains the production fallback and existing sessions/config/Job routes remain compatible.
- A draft session uses ordinary soft-delete semantics. Rollback code does not delete already created business sessions or source/config files; business-data cleanup requires separate explicit authorization.
- Partial/failed generation never changes the current session binding. Unreferenced server-created temporaries are removed only by their exact creation record; no recursive directory deletion is used.
- A completed config save is not automatically undone when mapping refresh fails or code is rolled back. Restoring saved business data must use existing backup/recovery flow with separate authorization.
- If integration regression, complete smoke, OpenAPI guards, quick user test, handoff validation or real-data fingerprint checks fail, stop before push/merge and keep the old Streamlit entry available.

## Implementation Evidence

- Planning baseline: `origin/main` at `c4b5732f4ce33f33defe43626b7ac80d1d71e305`; formal worktree clean; real `user_data/` untouched; stash baseline recorded in the handoff block.
- Design: 2026-07-15 user approved recoverable pre-generation drafts, continuous workspace option A, all three design sections, and written specification commit `ba926973f2ebe5cfcd1703f154c963426531f5b5`.
- Baseline tests: before any source change, config API/Job baseline `19 passed`; complete frontend Vitest baseline `25 files / 196 tests passed`.
- RED/GREEN: Task 10 Chromium RED ran 1 test and failed at the absent “考试名称” control because the initial sessions mock was intentionally incomplete; after the fixture was completed and its API matcher was constrained to real `/api/` requests, the full browser suite passed 14/14. The final unknown-write race regression first produced `5 failed / 67 passed` frontend tests and `1 failed / 3 passed` backend tests; after atomic server abandon markers and guarded client unlock were completed, focused GREEN passed `72 frontend / 4 backend`, followed by `146` affected backend tests. The final suite covers draft/upload/review, partial/reload/retry, whole-mode manual retry, upload failure, 422 focus, 409 retention and confirmed reload, mapping partial success, cancellation race, stale isolation, long/multipart content, keyboard focus, console/page-error guards and all five desktop viewports.
- Browser evidence: six mock-only screenshots were generated under ignored `frontend/test-results/` for upload review, partial recovery and the Rubric ledger at 1024×768 and 1440×900. Visual inspection found no document overflow, clipped controls, sticky-column overlap or competing decoration; keyboard focus remained visible. The versioned quick checklist uses the same verified mock fixture in a headed Chromium session and was launch-tested at `127.0.0.1:5173` without real data or model calls.
- Automatic gates: frozen candidate `91d4a04576042c393273de16860f264f83dbb1c1` passed focused backend `271`, frontend lint `0 errors / 0 warnings`, typecheck exit `0`, Vitest `34 files / 344 tests`, build `1675 modules`, P2-09 Chromium E2E `15`, and affected backend `70`; `git diff --check` exited `0`; `tools/smoke_check.py --skip-tests` passed document governance, static compilation of `451` first-party Python files and isolated-copy dual-database idempotence/integrity. Full pytest was not run because no `AGENTS.md` risk trigger occurred.
- Real data: feature-worktree `user_data/` status is empty. Root grading database remained `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`; root question-bank database remained `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`. Only file metadata and hashes were read.
- Independent review: frozen candidate `91d4a04576042c393273de16860f264f83dbb1c1` received `0 Critical / 0 Important / 0 Minor` from the requirements reviewer and `0 Critical / 0 Important / 1 Minor` from the standards reviewer. The non-blocking Minor is an empty per-token upload directory retained after an abandoned unknown request; it contains no manifest or private content and does not permit a late request to pass the tombstone.
- User quick test: pending; the versioned checklist and headed synthetic loopback mode were generated and launch-verified, but no user acceptance result is claimed before independent review anchors an exact SHA.
- Real data: unchanged; final file-level size, UTC mtime and SHA-256 comparison exactly matched the recorded baseline for both root databases.
