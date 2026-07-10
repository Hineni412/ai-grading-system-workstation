# P1-13 Controlled Media and File Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Vue 提供答题区裁剪、原卷/批注页和已完成报告的受控读取 URL，同时保证客户端既不能提交文件系统路径，也不能从 Job API 收到内部绝对路径。

**Architecture:** 新增无 FastAPI 依赖的受控文件守卫，以“服务端反查的存储值 + 明确根目录 + 明确扩展名”解析真实文件；新增 `ReviewMediaService` 用 `session_id/result_id/detail_id/page/variant` 校验资源所属关系并在内存中生成裁剪图。文件下载只接受 `job_id`，由后端读取 JobStore 的内部 `file_path` 后校验其仍位于 reports 根；公开 JobResponse 会删除所有 `*_path` 字段，并为成功的 `report_export` 返回受控下载 URL。

**Tech Stack:** Python 3.12、FastAPI/Starlette `FileResponse`/`Response`、Pydantic v2、SQLite、Pillow、pytest、现有 `DBManager`/`JobManager`/`PathManager`。

## Global Constraints

- 不新增通用文件浏览、目录列举、`?path=`、绝对路径、相对路径或文件名下载参数；客户端只能提交既有数据库/Job 数字 ID 和固定枚举。
- 允许根固定为 `exams_dir`、`annotated_dir`、`templates_dir` 和 `reports_dir`；`resolve()` 后必须仍位于对应根内，路径穿越、盘符绝对路径、符号链接/联接越界均拒绝。
- 原卷/批注图片只允许 `.jpg/.jpeg/.png/.webp/.bmp`；当前 `report_export` 下载只允许 `.xlsx`。未来 job 类型必须显式扩展“job 类型 -> 根目录/扩展名”映射。
- 不改变题号、题框匹配、坐标缩放、裁剪留白和红框行为；裁剪结果在内存中编码为 JPEG，不写磁盘。
- 不修改原卷、批注、报告或数据库业务数据；`OriginalPaperExporter` 只做路径链审计，本包不新增同步导出端点或新 job 类型。
- JobStore 可继续保存后端内部绝对路径；所有 Job API 响应必须递归移除 `path`/`*_path`/`*_paths` 字段，`report_export` 改为公开 `download_url`。
- 不改变现有 report/scan/grading handler 的执行、取消和原子发布协议。
- 错误响应不得包含原始存储路径、Windows 盘符、用户名或底层异常文本。
- 图片和下载响应设置 `Cache-Control: no-store`；文件下载使用实际受控文件名和固定 MIME 映射。
- 所有测试只使用 `tmp_path`、临时 SQLite 和生成图片/报告；不得读取或写入真实 `user_data/`。
- 当前工作区包含 P1-09 至 P1-12 与 WP1.2/WP1.3 未提交改动；只做 P1-13 增量，不覆盖或回退既有修改。
- 按用户要求，本计划不 stage、不 commit、不 push、不创建 PR。

---

### Task 1: 受控文件守卫与复核媒体应用服务

**Files:**
- Create: `backend/file_access.py`
- Create: `backend/media/__init__.py`
- Create: `backend/media/service.py`
- Modify: `db_manager.py`
- Create: `tests/test_controlled_file_access.py`
- Create: `tests/test_review_media_service.py`

**Interfaces:**
- Produces: `ResolvedFile(path: Path, media_type: str)`。
- Produces: `resolve_controlled_file(path_value, *, root, data_root, allowed_suffixes) -> ResolvedFile`。
- Produces: `ControlledFileForbidden`、`ControlledFileExpired`、`ControlledFileTypeError`；异常文本不带路径。
- Produces: `DBManager.get_review_media_context(session_id, result_id, detail_id) -> dict[str, Any] | None`。
- Produces: `ReviewMediaService.resolve_result_page(session_id, result_id, page, variant) -> ResolvedFile`。
- Produces: `ReviewMediaService.render_detail_crop(session_id, result_id, detail_id) -> bytes`。
- Produces: `ReviewMediaNotFound` 与 `ReviewMediaUnreadable`。

- [x] **Step 1: 写受控文件路径 RED 测试**

`tests/test_controlled_file_access.py` 使用独立 `data_root/reports`，覆盖：

```python
def test_controlled_file_accepts_existing_allowlisted_file_inside_root(tmp_path):
    reports = tmp_path / "data" / "reports"
    report = reports / "成绩.xlsx"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"xlsx")

    resolved = resolve_controlled_file(
        report,
        root=reports,
        data_root=tmp_path / "data",
        allowed_suffixes={".xlsx"},
    )

    assert resolved.path == report.resolve()
    assert resolved.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
```

另写四个独立测试：`../outside.xlsx` 穿越、现存绝对路径越界、根内 `.exe`、根内已删除 `.xlsx`，分别断言 `ControlledFileForbidden`、`ControlledFileForbidden`、`ControlledFileTypeError`、`ControlledFileExpired`。增加符号链接能力存在时的越界测试。

- [x] **Step 2: 写复核媒体所属/裁剪 RED 测试**

用 `tmp_path/data/databases/grading.db` 初始化临时库，在 `data/exams/session_<id>` 创建 400x300 JPEG，在 `data/templates/session_<id>` 创建同尺寸模板，插入一条 `Q1` 正面题框、paper/result/detail。覆盖：

```python
def test_review_media_crop_uses_owned_detail_and_existing_region(seed_media):
    service, ids = seed_media

    payload = service.render_detail_crop(
        ids.session_id,
        ids.result_id,
        ids.detail_id,
    )

    assert payload.startswith(b"\xff\xd8")
    with Image.open(BytesIO(payload)) as crop:
        assert crop.format == "JPEG"
        assert crop.width < 400
        assert crop.height < 300
```

再覆盖错误 session、错误 result/detail 组合、无匹配题框、原卷文件缺失、原卷绝对路径越界、`../` 穿越、错误图片扩展名；错误所属/无题框抛 `ReviewMediaNotFound`，文件安全错误保持其专用异常。

- [x] **Step 3: 运行服务测试确认 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_controlled_file_access.py tests\test_review_media_service.py -q
```

Expected: FAIL；受控文件模块、媒体服务和 DB 查询尚不存在。

- [x] **Step 4: 实现根目录与扩展名守卫**

`backend/file_access.py` 固定 MIME 映射，不使用任意系统 MIME 猜测：

```python
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

@dataclass(frozen=True, slots=True)
class ResolvedFile:
    path: Path
    media_type: str
```

实现顺序必须是：

1. 用 `resolve_stored_file_path(path_value, data_root=data_root, search_roots=[root])` 兼容旧机失效路径重映射。
2. `candidate.resolve(strict=False)` 和 `root.resolve(strict=False)`。
3. 用 `relative_to(root)` 校验包含关系；任何 `ValueError/OSError` 转 `ControlledFileForbidden("Stored file is outside the allowed root.")`。
4. 校验小写后缀在本调用白名单且存在固定 MIME；否则 `ControlledFileTypeError`。
5. 调用 `is_file()`，`False` 或 `OSError` 转 `ControlledFileExpired("Stored file is no longer available.")`。

异常不得把 `candidate` 拼进消息。

- [x] **Step 5: 增加一次 JOIN 的媒体所属查询**

`DBManager.get_review_media_context()` 使用一次查询同时验证 session/result/detail：

```sql
SELECT
    sr.session_id,
    sr.id AS result_id,
    sd.id AS detail_id,
    sd.question_id,
    ep.front_image,
    ep.back_image
FROM session_details sd
JOIN session_results sr ON sr.id = sd.result_id
JOIN exam_papers ep ON ep.id = sr.paper_id
WHERE sr.session_id = ?
  AND sr.id = ?
  AND sd.id = ?
```

找不到返回 `None`，不做模糊回退。

- [x] **Step 6: 实现媒体服务和现有裁剪行为**

`ReviewMediaService` 构造参数固定为：

```python
def __init__(
    self,
    db: DBManager,
    *,
    data_root: Path,
    exams_dir: Path,
    templates_dir: Path,
    annotated_dir: Path,
) -> None:
    ...
```

`resolve_result_page()`：

- `page` 只接受 `front/back`，`variant` 只接受 `original/annotated`。
- original 读取 `get_result_context(result_id)` 并二次校验 `session_id`，只在 `exams_dir` 解析。
- annotated 读取 `get_annotated_result(result_id)` 并校验 `session_id`，只在 `annotated_dir` 解析。

`render_detail_crop()`：

- 使用 Task 1 的一次 JOIN 上下文。
- 从 `list_answer_regions(session_id)` 选择与现有 Streamlit 相同的 `qid/f"Q{qid}"` 和数字父题回退。
- 模板源尺寸只允许从 `templates_dir` 读取；模板缺失/不安全时回退原坐标，不读取越界模板。
- 用 `scaled_region_bbox()`、`pad = max(18, min(width, height) // 70)`、现有红框公式裁剪。
- `BytesIO` 中以 RGB/JPEG quality 90 保存并关闭 Pillow 图像；解码失败转 `ReviewMediaUnreadable`，不回显底层异常。

- [x] **Step 7: 验证服务 GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_controlled_file_access.py tests\test_review_media_service.py -q
```

---

### Task 2: 语义化媒体端点与复核资源链接

**Files:**
- Create: `backend/api/routers/media.py`
- Create: `backend/api/schemas/media.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/review.py`
- Modify: `backend/api/schemas/review.py`
- Create: `tests/test_api_media_routes.py`
- Modify: `tests/test_api_review_routes.py`

**Interfaces:**
- Produces: `GET /api/sessions/{session_id}/results/{result_id}/pages/{page}?variant=original|annotated`。
- Produces: `GET /api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop`。
- Produces: `ReviewMediaLinksResponse`，字段为 `crop_url`、`original_front_url`、`original_back_url`、`annotated_front_url`、`annotated_back_url`。
- Extends: `ReviewItemResponse.media`；不增加数据库查询或暴露任何路径。
- Produces dependencies: `get_data_root()`、`get_exams_dir()`、`get_media_service()`。

- [x] **Step 1: 写正常媒体响应 RED 测试**

`tests/test_api_media_routes.py` 复用 Task 1 临时数据并 override `get_grading_db/get_data_root/get_exams_dir/get_templates_dir/get_annotated_dir`。断言：

```python
page = client.get(
    f"/api/sessions/{session_id}/results/{result_id}/pages/front"
)
assert page.status_code == 200
assert page.headers["content-type"] == "image/jpeg"
assert page.headers["cache-control"] == "no-store"
assert page.content.startswith(b"\xff\xd8")

crop = client.get(
    f"/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop"
)
assert crop.status_code == 200
assert crop.headers["content-type"] == "image/jpeg"
```

插入 `annotated_results` 后验证 `variant=annotated`；无批注文件返回过期契约。

- [x] **Step 2: 写安全与错误契约 RED 测试**

覆盖：

- 其他 session 请求同一 result/detail -> 404 `media_not_found`。
- 不属于 result 的 detail -> 404 `media_not_found`。
- 根内已删除文件 -> 410 `media_expired`。
- DB 中现存绝对越界路径和 `../` 路径 -> 403 `media_path_forbidden`。
- `.txt/.svg/.html` -> 415 `media_type_not_supported`。
- 非法 page/variant -> FastAPI 422 validation error。
- 所有错误 body 和字符串都不包含临时目录绝对路径。

- [x] **Step 3: 写复核列表媒体链接 RED 测试**

在 `test_review_question_summary_and_items` 增加：

```python
assert row["media"] == {
    "crop_url": (
        f"/api/sessions/{session_id}/results/{result_id}"
        f"/details/{detail_id}/crop"
    ),
    "original_front_url": (
        f"/api/sessions/{session_id}/results/{result_id}/pages/front"
    ),
    "original_back_url": (
        f"/api/sessions/{session_id}/results/{result_id}/pages/back"
    ),
    "annotated_front_url": (
        f"/api/sessions/{session_id}/results/{result_id}"
        "/pages/front?variant=annotated"
    ),
    "annotated_back_url": (
        f"/api/sessions/{session_id}/results/{result_id}"
        "/pages/back?variant=annotated"
    ),
}
assert "front_image" not in row
assert "back_image" not in row
```

保留 60 result API GET “最多 2 次连接”断言，证明 URL 纯由已有 ID 生成。

- [x] **Step 4: 运行 API 测试确认 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_media_routes.py tests\test_api_review_routes.py -q
```

Expected: FAIL；router/dependency/schema/links 尚不存在。

- [x] **Step 5: 实现媒体依赖、router 与统一错误映射**

`get_media_service()` 组合同一 `DBManager` 与 PathManager 受控根。router 参数使用 `Literal["front", "back"]` 和 `Literal["original", "annotated"]`，从而在进入服务前拒绝任意字符串。

错误映射：

| 领域异常 | HTTP | code | message |
|---|---:|---|---|
| `ReviewMediaNotFound` | 404 | `media_not_found` | `Media resource not found` |
| `ControlledFileExpired` | 410 | `media_expired` | `Media resource is no longer available` |
| `ControlledFileForbidden` | 403 | `media_path_forbidden` | `Media resource is outside the allowed storage boundary` |
| `ControlledFileTypeError` | 415 | `media_type_not_supported` | `Media type is not supported` |
| `ReviewMediaUnreadable` | 422 | `media_unreadable` | `Media resource could not be decoded` |

所有 `details` 只含数字 ID、page/variant，不含存储值。

原卷/批注页用 `FileResponse(path, media_type=...)`，不提供 `filename`，保持浏览器 inline 显示；裁剪用 `Response(content=..., media_type="image/jpeg")`；二者都设置 `Cache-Control: no-store`。

- [x] **Step 6: 实现 review media schema 链接**

`ReviewItemResponse` 新增必填 `media: ReviewMediaLinksResponse`。review router 在完成 domain -> Pydantic 转换后仅根据 `session_id/result_id/detail_id` 构造五个相对 URL，不读取文件、不查批注表。

- [x] **Step 7: 验证媒体 API GREEN 与查询上限**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_media_routes.py tests\test_api_review_routes.py tests\test_review_application_service.py -q
```

---

### Task 3: Job 下载端点与公开结果脱敏

**Files:**
- Create: `backend/files/__init__.py`
- Create: `backend/files/service.py`
- Create: `backend/api/routers/files.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/jobs.py`
- Modify: `tests/test_api_report_jobs.py`
- Modify: `tests/test_api_scan_jobs.py`
- Create: `tests/test_api_file_downloads.py`

**Interfaces:**
- Produces: `JobFileService.resolve(job: JobRecord) -> ResolvedFile`。
- Produces: `GET /api/jobs/{job_id}/download`。
- Produces: `public_job_result(job: JobRecord) -> dict[str, Any]`。
- Public report result exact keys: `session_id`、`filename`、`download_url`。
- Public scan result keeps `session_id/summary` and removes `scan_analysis_path`。

- [x] **Step 1: 写公开 Job 结果不泄露路径 RED 测试**

修改 report API 测试：

```python
loaded = client.get(f"/api/jobs/{created['id']}").json()
assert loaded["result"] == {
    "session_id": session_id,
    "filename": "report.xlsx",
    "download_url": f"/api/jobs/{created['id']}/download",
}
assert "file_path" not in str(loaded["result"])
assert str(tmp_path) not in str(loaded["result"])
```

修改 scan API 测试，断言 `scan_analysis_path` 和 `tmp_path` 不在公开结果，`summary` 保持兼容。

新增一个递归结果测试：假 job result 在嵌套 dict/list 中放 `artifact_path`/`paths`，`public_job_result()` 必须移除路径键而保留相邻业务字段。

- [x] **Step 2: 写下载成功与失败 RED 测试**

`tests/test_api_file_downloads.py` 使用临时 JobManager 和 `reports_dir`：

```python
response = client.get(f"/api/jobs/{job.id}/download")
assert response.status_code == 200
assert response.content == b"xlsx"
assert response.headers["content-type"] == (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
assert response.headers["content-disposition"].startswith("attachment;")
assert "report.xlsx" in response.headers["content-disposition"]
assert response.headers["cache-control"] == "no-store"
```

分别覆盖：未知 job 404、running/failed/cancelled 409、不支持 job 类型 404、成功 job 缺 `file_path` 410、文件已删除 410、绝对路径越界/`../`/符号链接越界 403、`.exe/.html/.svg` 415。错误响应不得出现内部路径。

- [x] **Step 3: 运行 Job 文件测试确认 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_file_downloads.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_jobs.py -q
```

Expected: FAIL；下载 router/service 与公开结果脱敏尚不存在。

- [x] **Step 4: 实现 JobFileService 的显式类型映射**

当前只定义：

```python
JOB_FILE_RULES = {
    "report_export": {
        "result_field": "file_path",
        "allowed_suffixes": frozenset({".xlsx"}),
        "root_name": "reports",
    },
}
```

`resolve()` 必须要求 `status == "succeeded"`、job 类型受支持、内部结果含路径，再调用 Task 1 守卫。状态/类型/过期/安全/类型错误使用专用领域异常，不返回 JobStore 原始错误。

- [x] **Step 5: 实现公开 Job 结果脱敏**

`public_job_result()` 先递归删除键名标准化后等于 `path/paths` 或以 `_path/_paths` 结尾的字段。对成功 `report_export`：

```python
result["download_url"] = f"/api/jobs/{job.id}/download"
```

`filename` 只保留 `Path(filename).name`；若内部 filename 缺失则仅在下载时从受控真实路径取名，公开结果不解析文件。`_job_response()` 对 submit/get/cancel 三个 API 统一使用该函数；JobStore/manager 直接读取仍保留内部路径，既有 handler 单测不改。

- [x] **Step 6: 实现下载 router 与错误映射**

`get_reports_dir()` 由 `PathManager` 注入；`files.py` 只接收 `job_id`，调用既有 `_require_job()` 与 `JobFileService`，返回 `FileResponse(filename=resolved.path.name, media_type=resolved.media_type)` 和 `Cache-Control: no-store`。

稳定错误：

- 非终态或非 succeeded：409 `job_file_unavailable`。
- 不支持类型：404 `job_file_not_found`。
- 结果/文件过期：410 `job_file_expired`。
- 越界：403 `job_file_forbidden`。
- 扩展名：415 `job_file_type_not_supported`。

- [x] **Step 7: 验证 Job 下载 GREEN**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_file_downloads.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_jobs.py tests\test_report_export_job.py tests\test_scan_analysis_job.py -q
```

- [x] **Step 8: 审计 OriginalPaperExporter 输出边界**

只读核对 `OriginalPaperExporter.export_session_originals()` 的 `pdf_path` 和中间页面均位于传入 `output_dir`，且正式 API 尚无该 job 类型。不修改 exporter；在计划完成证据中记录：未来若新增 `original_paper_export` job，必须显式加入 `JOB_FILE_RULES`，允许 `.pdf`，不得复用 `report_export` 的 `.xlsx` 规则。

---

### Task 4: 文档、范围审计与分层验证

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
- Modify: this plan

- [x] **Step 1: 运行 P1-13 聚焦回归**

```powershell
runtime\python\python.exe -m pytest tests\test_controlled_file_access.py tests\test_review_media_service.py tests\test_api_media_routes.py tests\test_api_file_downloads.py tests\test_api_review_routes.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_jobs.py tests\test_report_export_job.py tests\test_scan_analysis_job.py -q
```

- [x] **Step 2: 运行显式 API 回归**

```powershell
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py tests\test_api_jobs.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_review_routes.py tests\test_api_media_routes.py tests\test_api_file_downloads.py -q
```

- [x] **Step 3: 做安全与占位符审计**

```powershell
rg -n "TODO|FIXME|NotImplemented|placeholder|pass$" backend/file_access.py backend/media backend/files backend/api/routers/media.py backend/api/routers/files.py tests/test_controlled_file_access.py tests/test_review_media_service.py tests/test_api_media_routes.py tests/test_api_file_downloads.py
rg -n "FileResponse|file_path|scan_analysis_path|download_url" backend/api backend/files tests/test_api_report_jobs.py tests/test_api_scan_jobs.py tests/test_api_file_downloads.py
git diff --check
```

Expected: 新范围无占位实现；二进制 route 不存在客户端 `path` 参数；`file_path/scan_analysis_path` 仅作为后端内部输入或测试种子，不在公开响应断言中；`git diff --check` 通过。

- [x] **Step 4: 运行快速冒烟**

```powershell
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

只允许工具创建临时副本；不得操作真实 `user_data/`。

- [x] **Step 5: 运行全量回归**

```powershell
runtime\python\python.exe -m pytest -q
```

- [x] **Step 6: 更新架构和执行证据**

记录语义化资源 URL、根/扩展名白名单、裁剪内存生成、JobStore 内部路径与公开结果脱敏边界、HTTP 错误契约和实际测试数字。把 P1-13 标成 `verified`，把 P1-14 推进为 `ready`；不得写入未实际运行的证据。

- [x] **Step 7: 最终范围与 user_data 指纹检查，不提交**

```powershell
git diff --check
git status --short -- backend db_manager.py tests docs/superpowers AGENTS.md ARCHITECTURE.md
git status --short -- user_data
```

对开工时记录的 `user_data` status 行数和 SHA-256 指纹重新计算并比较，必须保持 `205` 行与 `b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`；若变化，停止并调查，不得自动还原用户数据。最后保持未暂存、未提交、未推送。

## Plan Self-Review

- Spec coverage: 正常裁剪/原卷/批注、下载 URL、绝对路径、穿越、错误 session、过期、扩展名、Content-Type/filename 均有明确 RED/GREEN 任务。
- Scope: 不新增导出类型、不修改原卷、不改数据库 Schema、不改变 Streamlit 行为；`OriginalPaperExporter` 仅审计。
- Type consistency: `ResolvedFile`、`ReviewMediaService`、`JobFileService`、`ReviewMediaLinksResponse` 和公开 URL 在生产/测试任务中名称一致。
- Placeholder scan: 无 TBD/TODO/“类似前项”或未定义接口。

## Completion Evidence

- P1-13 聚焦回归：`73 passed in 12.64s`。
- 显式 API 回归：`67 passed in 10.75s`。
- 快速冒烟：编译 336 个第一方 Python 文件；阅卷库与题库临时副本初始化幂等，`integrity_check=ok`。
- 全量回归：`871 passed in 111.27s`。
- 安全审计：media/files 路由无客户端路径参数；内部 `file_path`/`scan_analysis_path` 不进入公开响应；`git diff --check` 通过。
- 独立复审首轮指出旧路径 basename 碰撞、job error 路径泄露、报告公开结果白名单和错误响应缓存头四项问题；修复后复审无 critical/important 遗留。
- `user_data` 最终状态仍为 205 行，排序状态 SHA-256 仍为 `b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`；未修改、暂存或提交用户数据。
- Git 收尾：当前分支 `codex/wp1-2-api-routes`，普通 checkout，暂存区 0 文件；按用户要求保持未提交、未推送、未创建 PR。
- 最终状态：P1-13 `verified`，P1-14 `ready`。
