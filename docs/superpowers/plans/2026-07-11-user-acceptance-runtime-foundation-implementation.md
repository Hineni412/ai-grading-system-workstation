# User Acceptance Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供不会读取或写入真实 `user_data/` 的用户验收运行模式、固定复核演示工作区、一键启动/重置入口和持续可见的验收标识。

**Architecture:** `PathManager` 在显式 UAT 模式下使用仓库外受控数据根并隔离 API profiles/logs；`tools/user_acceptance_workspace.py` 安全创建固定 `review-demo-v1` 数据；`tools/run_user_acceptance.py` 统一管理 FastAPI/Streamlit 子进程、浏览器和真实数据库指纹。正常 `运行.bat`、配置优先级和生产数据行为保持不变。

**Tech Stack:** Python 3.12、SQLite、Pillow、FastAPI、Streamlit、Windows batch、pytest。

**工作包：** `USER-ACCEPTANCE-RUNTIME`
**规划状态：** `ready_for_execution`
**规划模型：** `S-XH`
**执行模型：** `T-H`
**复审模型：** `S-XH`
**允许夜间执行：** `no`
**计划基线：** `bbc1b7f7edbd70ba696421eab89ab30d37ee5101`
**包类型：** 非正式支撑包；不改变 87 个正式包的数量、状态或夜间资格。
**开工门槛：** `DAY-NIGHT-HANDOFF` 已合并；从当时最新 `origin/main` 建立独立 worktree；不得与正在修改 `path_manager.py`、`backend/api/app.py` 或 `web_app.py` 的任务并行。

## Global Constraints

- 该工作包涉及数据根和启动入口，只能白天实施。
- 正常 `运行.bat`、`config/app_config.yaml` 和非 UAT `PathManager` 解析顺序必须完全不变。
- UAT 数据只允许位于 `%LOCALAPPDATA%\AIGradingSystem\uat\` 的真实子目录。
- UAT 模式必须忽略机器真实 API profiles，默认不调用真实 LLM。
- 所有 reset/delete 在执行前必须解析绝对路径并拒绝 reparse point、UAT 根本身、仓库根和真实 `user_data/`。
- 固定演示数据只能包含合成姓名、合成答卷和预生成评分结果。
- 真实两库的大小、UTC mtime 和 SHA-256 在启动前后必须完全一致。
- P2-20 授权副本和真实模型验收不属于本工作包。
- 本包必须在 P1-29 开工前合并；P1-29 只消费其稳定入口和固定 fixture，不在总门槛中夹带修复。

## PowerShell Session Preamble

Linked worktrees do not contain the ignored portable runtime. Before Task 0, and again after opening any new PowerShell session, run:

```powershell
$commonGitDir = (git rev-parse --path-format=absolute --git-common-dir).Trim()
$primaryRepositoryRoot = Split-Path -Parent $commonGitDir
$Python = Join-Path $primaryRepositoryRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw "portable Python runtime not found" }
```

All Python commands below use `$Python` while keeping the UAT worktree as the working directory.

---

### Task 0: Execution Baseline and Drift Gate

**Files:**
- Verify only; if the code-target diff is non-empty, modify this plan before any implementation.

- [ ] **Step 1: Confirm the handoff protocol and clean latest-main base**

Run in the dedicated UAT worktree:

```powershell
git fetch origin
git merge-base --is-ancestor bbc1b7f7edbd70ba696421eab89ab30d37ee5101 origin/main
if ($LASTEXITCODE -ne 0) { throw "origin/main no longer descends from the reviewed design baseline" }
$head = git rev-parse HEAD
$main = git rev-parse origin/main
if ($head -ne $main) { throw "UAT worktree must start at latest origin/main" }
if (-not (Test-Path -LiteralPath tools\handoff_status.py)) { throw "DAY-NIGHT-HANDOFF is not merged" }
$dirty = git status --porcelain=v1 --untracked-files=all
if ($dirty) { throw "UAT worktree must be clean before implementation" }
```

Expected: `DAY-NIGHT-HANDOFF` is present in `origin/main`, HEAD equals `origin/main`, and the worktree is clean. Otherwise stop before implementation and repair the worktree through the daytime workflow.

- [ ] **Step 2: Check code-target drift since planning**

```powershell
git diff --name-status bbc1b7f7edbd70ba696421eab89ab30d37ee5101..origin/main -- path_manager.py api_profiles.py backend/api/app.py web_app.py db_manager.py question_bank/database/schema.py 运行.bat tests/test_api_app.py tests/test_api_profile_store.py tests/test_portable_path_resolution.py tests/test_review_media_service.py
```

Expected immediately after the handoff protocol package: no output. If any listed code or test file changed, Sol Extra High must reread those diffs, update this plan's interfaces/tests, and commit that plan-only refresh before Terra starts Task 1.

- [ ] **Step 3: Record the real-database baseline without opening SQLite**

```powershell
$normalListeners = Get-NetTCPConnection -State Listen -LocalPort 8000,8501 -ErrorAction SilentlyContinue
if ($normalListeners) { throw "close the normal AI grading application before UAT verification" }
$databasePaths = @(
    'user_data\databases\grading_system.db',
    'user_data\databases\question_bank.db'
)
foreach ($databasePath in $databasePaths) {
    if (Test-Path -LiteralPath $databasePath) {
        $item = Get-Item -LiteralPath $databasePath
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $databasePath
        [pscustomobject]@{
            Path = $databasePath
            Length = $item.Length
            LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
            SHA256 = $hash.Hash
        }
    } else {
        [pscustomobject]@{ Path = $databasePath; State = 'missing' }
    }
}
```

Append the exact output to this plan's evidence section before running Task 1. Do not use SQLite for this baseline.

### Task 1: Explicit UAT Runtime Resolution

**Files:**
- Modify: `path_manager.py`
- Create: `tests/test_uat_runtime_mode.py`
- Modify: `tests/test_api_profile_store.py`

**Interfaces:**
- Produces: `PathManager.is_uat_mode: bool` and `PathManager.runtime_mode: Literal["normal", "uat"]`.
- In UAT mode consumes `AI_GRADING_UAT_MODE=1`, `AI_GRADING_UAT_DATA_DIR`, and `LOCALAPPDATA`.
- Normal mode preserves existing configuration behavior.

- [ ] **Step 1: Write failing path-resolution tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest
import path_manager

from path_manager import PathManager


def _uat_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    local = tmp_path / "LocalAppData"
    data_root = local / "AIGradingSystem" / "uat" / "active"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("AI_GRADING_UAT_MODE", "1")
    monkeypatch.setenv("AI_GRADING_UAT_DATA_DIR", str(data_root))
    return data_root


def test_uat_mode_overrides_configured_user_data(monkeypatch, tmp_path: Path) -> None:
    data_root = _uat_env(monkeypatch, tmp_path)
    machine_profile = tmp_path / "machine" / "api_profiles.json"
    monkeypatch.setenv("AI_GRADING_API_PROFILES_PATH", str(machine_profile))

    manager = PathManager()

    assert manager.is_uat_mode is True
    assert manager.runtime_mode == "uat"
    assert manager.data_root == data_root.resolve()
    assert manager.logs_dir == data_root.resolve() / "logs"
    assert manager.api_profiles_path == data_root.resolve() / "config" / "api_profiles.json"
    assert manager.api_profiles_path != machine_profile.resolve()
    assert manager.legacy_api_profiles_paths == ()


def test_uat_mode_requires_explicit_data_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("AI_GRADING_UAT_MODE", "1")
    monkeypatch.delenv("AI_GRADING_UAT_DATA_DIR", raising=False)

    with pytest.raises(RuntimeError, match="AI_GRADING_UAT_DATA_DIR is required"):
        PathManager()


def test_uat_mode_rejects_path_outside_controlled_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("AI_GRADING_UAT_MODE", "1")
    monkeypatch.setenv("AI_GRADING_UAT_DATA_DIR", str(tmp_path / "outside"))

    with pytest.raises(RuntimeError, match="controlled UAT root"):
        PathManager()


def test_uat_mode_rejects_controlled_root_inside_repository(monkeypatch) -> None:
    project_root = Path(path_manager.__file__).resolve().parent
    monkeypatch.setenv("LOCALAPPDATA", str(project_root))
    monkeypatch.setenv("AI_GRADING_UAT_MODE", "1")
    monkeypatch.setenv(
        "AI_GRADING_UAT_DATA_DIR",
        str(project_root / "AIGradingSystem" / "uat" / "active"),
    )

    with pytest.raises(RuntimeError, match="outside the repository"):
        PathManager()


def test_normal_mode_keeps_configured_user_data(monkeypatch) -> None:
    monkeypatch.delenv("AI_GRADING_UAT_MODE", raising=False)
    monkeypatch.delenv("AI_GRADING_UAT_DATA_DIR", raising=False)

    manager = PathManager()

    assert manager.is_uat_mode is False
    assert manager.runtime_mode == "normal"
    assert manager.data_root == manager.project_root / "user_data"
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
& $Python -m pytest tests\test_uat_runtime_mode.py tests\test_api_profile_store.py -q
```

Expected: new tests fail because UAT properties and override do not exist.

- [ ] **Step 3: Implement strict UAT resolution before normal config lookup**

Add pure helpers near `_PROJECT_ROOT` and use them at the start of `PathManager.__init__`:

```python
def _controlled_uat_root() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        raise RuntimeError("LOCALAPPDATA is required in UAT mode")
    return (Path(local_appdata) / "AIGradingSystem" / "uat").resolve()


def _resolve_uat_data_root(project_root: Path) -> Path | None:
    if os.getenv("AI_GRADING_UAT_MODE") != "1":
        return None
    raw = os.getenv("AI_GRADING_UAT_DATA_DIR")
    if not raw:
        raise RuntimeError("AI_GRADING_UAT_DATA_DIR is required in UAT mode")
    controlled_root = _controlled_uat_root()
    try:
        controlled_root.relative_to(project_root.resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("controlled UAT root must stay outside the repository")
    candidate = Path(raw).expanduser().resolve()
    try:
        relative = candidate.relative_to(controlled_root)
    except ValueError as exc:
        raise RuntimeError("UAT data directory must stay under the controlled UAT root") from exc
    if not relative.parts:
        raise RuntimeError("UAT data directory cannot be the controlled UAT root itself")
    real_data_root = (project_root / "user_data").resolve()
    try:
        candidate.relative_to(real_data_root)
    except ValueError:
        pass
    else:
        raise RuntimeError("UAT data directory cannot use real user_data")
    return candidate
```

In `PathManager.__init__`, call `_resolve_uat_data_root()` immediately after loading config. Use this exact branch boundary: when the helper returns a path, set `_is_uat_mode=True`, force data/log/profile paths under that root, and ignore normal `DATA_DIR`, `LOGS_DIR`, `AI_GRADING_DATA_DIR`, and `AI_GRADING_API_PROFILES_PATH` values. When it returns `None`, run the current resolution blocks byte-for-byte and set `_is_uat_mode=False`.

```python
uat_data_root = _resolve_uat_data_root(self._project_root)
self._is_uat_mode = uat_data_root is not None
if uat_data_root is not None:
    self._data_root = uat_data_root
    self._logs_root = uat_data_root / "logs"
    self._api_profiles_path = uat_data_root / "config" / "api_profiles.json"
```

Place the current three normal resolution blocks in the corresponding `else` branch without changing their statements or priority. Import `Literal` and add:

```python
@property
def is_uat_mode(self) -> bool:
    return self._is_uat_mode

@property
def runtime_mode(self) -> Literal["normal", "uat"]:
    return "uat" if self.is_uat_mode else "normal"
```

At the start of `legacy_api_profiles_paths`, return `()` when `is_uat_mode` is true; this prevents `ApiProfileStore` from importing keys from project legacy locations into an intentionally empty UAT profile. Add a regression in `tests/test_api_profile_store.py` that creates a legacy profile containing a sentinel key, constructs the store from UAT `PathManager` paths, and proves loading returns `[]` without reading or copying the sentinel. Include `运行模式` in `summary()` and keep propagating the selected root to `AI_GRADING_DATA_DIR` for legacy readers.

- [ ] **Step 4: Run UAT, profile, and portable-path tests**

```powershell
& $Python -m pytest tests\test_uat_runtime_mode.py tests\test_api_profile_store.py tests\test_portable_path_resolution.py -q
```

Expected: all pass; normal profile override tests remain unchanged outside UAT mode.

- [ ] **Step 5: Commit runtime resolution**

```powershell
git add path_manager.py tests\test_uat_runtime_mode.py tests\test_api_profile_store.py
git commit -m "feat: isolate user acceptance runtime paths"
```

### Task 2: Safe Deterministic Review Demo Workspace

**Files:**
- Create: `tools/user_acceptance_workspace.py`
- Create: `tests/test_user_acceptance_workspace.py`

**Interfaces:**
- Produces: `prepare_workspace(data_root: Path, *, controlled_root: Path, reset: bool) -> UatManifest`.
- Produces: `fingerprint_real_databases(project_root: Path) -> dict[str, FileFingerprint | None]`.
- Creates fixture version `review-demo-v1` with one synthetic session, student, paper, result, two question details, template/answer regions, generated images, both initialized databases, and blank API profiles.

- [ ] **Step 1: Write failing safety and seed tests**

```python
def test_prepare_workspace_seeds_review_demo(tmp_path: Path) -> None:
    controlled = tmp_path / "uat"
    data_root = controlled / "active"

    manifest = prepare_workspace(data_root, controlled_root=controlled, reset=False)

    assert manifest.fixture_version == "review-demo-v1"
    assert manifest.session_id > 0
    assert manifest.result_id > 0
    assert (data_root / "databases" / "grading_system.db").is_file()
    assert (data_root / "databases" / "question_bank.db").is_file()
    assert json.loads((data_root / "config" / "api_profiles.json").read_text("utf-8")) == []


def test_prepare_workspace_reuses_existing_manifest_without_reset(tmp_path: Path) -> None:
    controlled = tmp_path / "uat"
    data_root = controlled / "active"
    first = prepare_workspace(data_root, controlled_root=controlled, reset=False)

    second = prepare_workspace(data_root, controlled_root=controlled, reset=False)

    assert second == first


def test_reset_rejects_outside_and_reparse_targets(tmp_path: Path) -> None:
    controlled = tmp_path / "uat"
    with pytest.raises(UatWorkspaceError, match="controlled UAT root"):
        prepare_workspace(tmp_path / "outside", controlled_root=controlled, reset=True)
```

Add these named cases in the same file:

- `test_reset_rejects_controlled_root_itself`;
- `test_reset_rejects_repository_and_real_user_data_paths`;
- `test_reset_rejects_symlink_or_windows_junction_component`;
- `test_nonempty_workspace_without_valid_manifest_requires_explicit_reset`;
- `test_reset_recreates_the_same_manifest_ids_and_fixture_files`;
- `test_fixture_contains_only_synthetic_names_and_paths_under_data_root`;
- `test_fingerprint_real_databases_is_read_only_and_represents_missing_files_as_none`.

The junction case is required on Windows and may use `cmd /c mklink /J` only inside `tmp_path`; remove it in test cleanup. The test must assert rejection occurs before the sentinel file below the target is deleted.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
& $Python -m pytest tests\test_user_acceptance_workspace.py -q
```

Expected: import failure because the workspace module does not exist.

- [ ] **Step 3: Implement path guard and manifest structures**

Create these public types and path checks. The lexical path is inspected before `resolve()` so a junction cannot be followed and then mistaken for an ordinary child:

```python
@dataclass(frozen=True)
class FileFingerprint:
    length: int
    mtime_utc_ns: int
    sha256: str


@dataclass(frozen=True)
class UatManifest:
    fixture_version: str
    session_id: int
    result_id: int
    detail_ids: tuple[int, ...]


class UatWorkspaceError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _contains_reparse_point(path: Path) -> bool:
    lexical = Path(os.path.abspath(path))
    for candidate in (lexical, *lexical.parents):
        if _is_reparse_point(candidate):
            return True
    if not lexical.exists():
        return False
    for root, directories, filenames in os.walk(lexical, topdown=True, followlinks=False):
        for name in (*directories, *filenames):
            if _is_reparse_point(Path(root) / name):
                return True
    return False


def assert_safe_uat_path(data_root: Path, controlled_root: Path) -> tuple[Path, Path]:
    controlled_lexical = Path(os.path.abspath(controlled_root))
    candidate_lexical = Path(os.path.abspath(data_root))
    if _contains_reparse_point(controlled_lexical) or _contains_reparse_point(candidate_lexical):
        raise UatWorkspaceError("UAT path contains a reparse point")
    controlled = controlled_lexical.resolve()
    candidate = candidate_lexical.resolve()
    try:
        relative = candidate.relative_to(controlled)
    except ValueError as exc:
        raise UatWorkspaceError("path is outside the controlled UAT root") from exc
    if not relative.parts:
        raise UatWorkspaceError("refusing to operate on the controlled UAT root itself")
    project_root = Path(__file__).resolve().parents[1]
    try:
        candidate.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise UatWorkspaceError("UAT workspace must stay outside the repository")
    return candidate, controlled
```

Import `os` and `stat`. Do not call `shutil.rmtree` anywhere except after `assert_safe_uat_path` returns successfully for that exact target.

- [ ] **Step 4: Implement deterministic fixture seeding**

Use `FIXTURE_VERSION = "review-demo-v1"` and `MANIFEST_NAME = "uat_manifest.json"`. `prepare_workspace` follows this exact transaction boundary:

1. Validate `data_root` and `controlled_root` before creating or deleting anything.
2. If `reset=False` and `active/uat_manifest.json` exists, parse it, require `fixture_version == FIXTURE_VERSION`, require both databases and all four image files, and verify the recorded session/result/detail IDs still exist. Return it only when every check passes.
3. If `reset=False` and `active` is non-empty without a fully valid manifest, raise `UatWorkspaceError("UAT workspace is incomplete; run an explicit reset")`; never repair it in place.
4. If `reset=True` and `active` exists, validate that exact path again and remove only that directory.
5. Create the already-validated controlled root with `mkdir(parents=True, exist_ok=True)`, validate it again, then create a sibling staging directory with `tempfile.mkdtemp(prefix=".active-build-", dir=controlled)`. Validate the staging path, seed everything there, and write the manifest last. Rename the staging directory to the requested `data_root` with `os.replace` only after all validation succeeds. On failure, validate and remove only the staging directory, then re-raise.
6. Return IDs by reading the manifest from the published `active` directory, never by assuming SQLite starts at 1.

Seed the staging directory with these exact records and files:

| Entity | Persisted fixture values |
|---|---|
| Rubric | `config/uploaded/rubric.json`; Q1 max 10, Q2 max 5 |
| Answer key | `config/uploaded/answer.json`; deterministic text answers for Q1/Q2 |
| Session | name `用户验收演示考试`; paths to the two files above |
| Template | generated 1200x1700 front/back JPEG files under `templates / f"session_{session_id}"` |
| Regions | confirmed manual front-page regions `region-q1` and `region-q2`, non-overlapping and mapped to Q1/Q2 |
| Student | code `DEMO001`, name `演示学生001`, class `演示班` |
| Paper | generated 1200x1700 front/back JPEG files under `exams / f"session_{session_id}"`; `matched` and `graded` |
| Result | total 15, student score 10, `needs_human_review=1` |
| Q1 detail | score 8, reason/category `需复核`, confidence 55; `raw_json.detail_metadata.Q1` has one candidate score and no filesystem path or secret-like key |
| Q2 detail | score 2, reason/category `计算错误`, confidence 92 |
| Question bank | initialized current schema with no imported paper/question rows |
| API profiles | `config/api_profiles.json` containing exactly `[]`, plus `api_profiles.json.migration-v1` containing `migration-v1-complete` |

Initialize the databases with `DBManager(staging / "databases/grading_system.db").initialize()` and `question_bank.database.schema.initialize_database(staging / "databases/question_bank.db")`. Use `DBManager.create_grading_session`, `upsert_session_template`, and `add_answer_region` for their owned records; use one parameterized SQLite transaction for `students`, `exam_papers`, `session_results`, and both `session_details`, following the current contracts in `tests/test_api_review_routes.py` and `tests/test_review_media_service.py`. Generate images with Pillow geometry and text constants only; do not read existing papers, templates, profiles, databases, or network resources.

Write the profile migration marker defensively even though UAT `legacy_api_profiles_paths` is empty. Write the manifest as sorted UTF-8 JSON to `uat_manifest.json.tmp`, flush and close it, then publish it with `os.replace`. The manifest contains only `fixture_version`, `session_id`, `result_id`, and the two `detail_ids`.

- [ ] **Step 5: Implement read-only real database fingerprinting**

Read bytes in 1 MiB chunks with `hashlib.sha256`, `Path.stat()` and no SQLite connection. Capture size and `st_mtime_ns` before and after hashing; if either changes, retry once, then raise `UatWorkspaceError("real database changed while fingerprinting")` rather than returning an unstable fingerprint. Missing databases are represented explicitly rather than created:

```python
def fingerprint_real_databases(project_root: Path) -> dict[str, FileFingerprint | None]:
    database_dir = project_root.resolve() / "user_data" / "databases"
    return {
        name: _fingerprint(database_dir / name)
        for name in ("grading_system.db", "question_bank.db")
    }
```

- [ ] **Step 6: Run fixture and existing schema tests**

```powershell
& $Python -m pytest tests\test_user_acceptance_workspace.py tests\test_schema_baseline.py tests\test_review_media_service.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit deterministic workspace**

```powershell
git add tools\user_acceptance_workspace.py tests\test_user_acceptance_workspace.py
git commit -m "feat: prepare deterministic acceptance workspace"
```

### Task 3: Managed UAT Launcher and Reset Entry

**Files:**
- Create: `tools/run_user_acceptance.py`
- Create: `运行-用户验收.bat`
- Create: `重置-用户验收数据.bat`
- Create: `tests/test_user_acceptance_launcher.py`

**Interfaces:**
- Consumes: Task 2 workspace and fingerprint APIs.
- Produces: `build_child_environment(data_root: Path, *, base: Mapping[str, str] | None = None) -> dict[str, str]`.
- Produces: `build_commands(project_root: Path) -> tuple[list[str], list[str]]` for UI/API ports 8502/8001.
- Produces: `run_acceptance(*, reset: bool = False, prepare_only: bool = False, open_browser: bool = True, project_root: Path | None = None) -> int`.

- [ ] **Step 1: Write failing launcher tests**

Test that environment values point only to the controlled UAT workspace, commands use the current Python executable, API/Streamlit ports are isolated, real fingerprints are captured before workspace preparation and compared after cleanup, child processes are terminated, and batch files call only the managed Python runner.

```python
def test_build_child_environment_isolates_data_and_profiles(tmp_path: Path) -> None:
    data_root = tmp_path / "uat" / "active"

    env = build_child_environment(
        data_root,
        base={"LOCALAPPDATA": str(tmp_path), "LLM_API_KEY": "must-not-leak"},
    )

    assert env["AI_GRADING_UAT_MODE"] == "1"
    assert env["AI_GRADING_UAT_DATA_DIR"] == str(data_root.resolve())
    assert env["AI_GRADING_API_PROFILES_PATH"] == str(
        data_root.resolve() / "config" / "api_profiles.json"
    )
    assert env["AI_GRADING_DATA_DIR"] == str(data_root.resolve())
    assert env["PORT"] == "8502"
    assert env["API_PORT"] == "8001"
    assert "LLM_API_KEY" not in env
```

Add `test_fingerprints_wrap_prepare_only_and_reset`, `test_occupied_uat_port_fails_before_child_start`, `test_api_must_report_uat_before_browser_opens`, `test_all_started_children_are_stopped_on_startup_failure`, and `test_fingerprint_change_forces_exit_two`. Use fakes for HTTP, `Popen`, browser, port checks and fingerprint functions; these tests must not bind real ports or launch child processes.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
& $Python -m pytest tests\test_user_acceptance_launcher.py -q
```

Expected: import and file-existence failures.

- [ ] **Step 3: Implement child environment and command construction**

Use `sys.executable` for both commands:

```python
def build_commands(project_root: Path) -> tuple[list[str], list[str]]:
    python = sys.executable
    api = [python, "-m", "uvicorn", "backend.api.app:app", "--host", "127.0.0.1", "--port", "8001"]
    ui = [
        python,
        "-m",
        "streamlit",
        "run",
        "web_app.py",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8502",
        "--server.headless",
        "true",
    ]
    return api, ui
```

Implement the environment builder exactly at the UAT boundary; later assignments intentionally overwrite inherited production paths:

```python
def build_child_environment(
    data_root: Path,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    resolved = data_root.resolve()
    env.update(
        {
            "AI_GRADING_UAT_MODE": "1",
            "AI_GRADING_UAT_DATA_DIR": str(resolved),
            "AI_GRADING_DATA_DIR": str(resolved),
            "AI_GRADING_API_PROFILES_PATH": str(resolved / "config" / "api_profiles.json"),
            "API_PORT": "8001",
            "PORT": "8502",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        }
    )
    for key in (
        "LLM_API_KEY",
        "LLM_CONFIG_API_KEY",
        "LLM_OBJECTIVE_API_KEY",
        "OPENAI_API_KEY",
        "QUESTION_BANK_TAGGING_API_KEY",
        "QUESTION_BANK_TAGGING_REVIEW_API_KEY",
    ):
        env.pop(key, None)
    return env
```

Before launching, parse the selected profile file and require it to equal `[]`. Any other value is a hard failure; do not fall back to a machine profile or environment credential. Recheck that the profile file is still `[]` during final cleanup; a change fails the run and requires an explicit reset before reuse.

- [ ] **Step 4: Implement managed process lifecycle**

Add `_stop_process(process: subprocess.Popen[bytes]) -> None`: if `poll()` is `None`, call `terminate()`, wait at most 5 seconds, then `kill()` and wait again. Add an HTTP poller using `urllib.request.urlopen` with a 0.5-second request timeout and a 30-second monotonic deadline; it must abort immediately if the associated child exits.

`run_acceptance` follows this exact order:

1. Resolve `project_root` to the repository containing the runner. Require `LOCALAPPDATA`, derive `%LOCALAPPDATA%\AIGradingSystem\uat` and its `active` child, and reject startup if the resolved controlled root is the repository or lies below it.
2. Call `fingerprint_real_databases(project_root)` before `prepare_workspace`, directory creation, or child startup.
3. Enter one `try/finally` that covers preparation, optional reset, startup and the interactive wait. Prepare/reuse the fixture, verify the profile JSON is exactly `[]`, and use a short-lived loopback `socket.bind` probe to require ports 8001 and 8502 are free before child creation.
4. For `prepare_only=True`, skip all process and browser operations but still continue through `finally` and the after-fingerprint comparison.
5. Otherwise start FastAPI first with `subprocess.Popen(api_command, cwd=project_root, env=env, shell=False)`. Poll `http://127.0.0.1:8001/healthz`, parse JSON, and require both HTTP 200 and `runtime_mode == "uat"` before starting Streamlit.
6. Start Streamlit with the same `cwd`, environment and `shell=False`; require HTTP 200 from `http://127.0.0.1:8502/_stcore/health`. Only then may `webbrowser.open("http://127.0.0.1:8502")` run when `open_browser=True`.
7. After startup, poll both child processes every 0.25 seconds instead of blocking only on Streamlit. If FastAPI exits while Streamlit is alive, stop the run as failure; if Streamlit exits, use its exit code. A normal zero exit or `KeyboardInterrupt` returns 0; startup timeout, early child exit, malformed health JSON or nonzero exit returns 1 and prints a concise error without paths to profiles or secrets.
8. In `finally`, stop Streamlit and API in reverse order with `_stop_process`, then recompute real database fingerprints.
9. If before/after fingerprints differ, print `真实数据库指纹发生变化，验收运行已停止。` and return 2 regardless of any earlier result. Otherwise return the result from Step 7.

Implement the result as a local integer initialized before the `try`; do not `return` from inside the `try`, because the fingerprint comparison in `finally` must be able to override it. Provide CLI flags `--reset`, `--prepare-only`, and `--no-browser`. The reset batch uses `--reset --prepare-only`; the run batch uses no flags so restarting preserves the active demo run.

- [ ] **Step 5: Add minimal batch wrappers**

Both batch files must reuse the portable-runtime fallback pattern from `运行.bat`. `运行-用户验收.bat` runs:

```bat
"%PYTHON_EXE%" tools\run_user_acceptance.py
```

`重置-用户验收数据.bat` displays that only the isolated demo workspace will be replaced, asks `choice /C YN`, and on Y runs:

```bat
"%PYTHON_EXE%" tools\run_user_acceptance.py --reset --prepare-only
```

- [ ] **Step 6: Run launcher tests**

```powershell
& $Python -m pytest tests\test_user_acceptance_launcher.py tests\test_run_bat_api_entry.py -q
```

Expected: all pass; the normal launcher test remains unchanged.

- [ ] **Step 7: Commit launcher slice**

```powershell
git add tools\run_user_acceptance.py 运行-用户验收.bat 重置-用户验收数据.bat tests\test_user_acceptance_launcher.py
git commit -m "feat: add managed user acceptance launcher"
```

### Task 4: Persistent UAT Mode Signals in API and Streamlit

**Files:**
- Modify: `backend/api/app.py`
- Modify: `web_app.py`
- Modify: `tests/test_api_app.py`
- Create: `tests/test_uat_mode_banner.py`

**Interfaces:**
- Consumes: `PathManager.runtime_mode` and `is_uat_mode` from Task 1.
- Produces: health JSON field `runtime_mode: Literal["normal", "uat"]` for future Vue use.
- Produces: `_render_uat_banner() -> None`, called at the top of every Streamlit render.

- [ ] **Step 1: Write failing API and banner tests**

Update the normal health expectation to include `runtime_mode: "normal"`; monkeypatch `backend.api.app.get_path_manager` with a fake UAT manager and expect `runtime_mode: "uat"` from both health aliases. Add focused tests that monkeypatch `web_app.get_path_manager` and `web_app.st.warning`, call `_render_uat_banner()` directly, and assert one warning containing `用户验收模式` in UAT mode and no warning in normal mode.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
& $Python -m pytest tests\test_api_app.py tests\test_uat_mode_banner.py -q
```

Expected: health field and warning assertions fail.

- [ ] **Step 3: Add runtime mode to health response**

```python
from typing import Literal


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ai-grading-api"
    version: str
    runtime_mode: Literal["normal", "uat"]


def healthz() -> HealthResponse:
    paths = get_path_manager()
    return HealthResponse(version=paths.version, runtime_mode=paths.runtime_mode)
```

Keep both `/healthz` and `/api/healthz` aliases identical.

- [ ] **Step 4: Render the UAT warning immediately after page config**

Add this helper beside `render_header()`:

```python
def _render_uat_banner() -> None:
    if get_path_manager().is_uat_mode:
        st.warning("用户验收模式：当前使用隔离演示数据，不会写入真实工作区。")
```

Call `_render_uat_banner()` in `render_header()` immediately after `_inject_css()` and before the normal top bar markup. Do not show absolute paths or machine information in the banner.

- [ ] **Step 5: Run API, OpenAPI, and banner regressions**

```powershell
& $Python -m pytest tests\test_api_app.py tests\test_api_openapi_contract.py tests\test_uat_mode_banner.py -q
```

Expected: all pass and OpenAPI exposes the new required health field.

- [ ] **Step 6: Commit visible mode signals**

```powershell
git add backend\api\app.py web_app.py tests\test_api_app.py tests\test_uat_mode_banner.py
git commit -m "feat: expose user acceptance runtime mode"
```

### Task 5: User Guide Integration and Browser Verification

**Files:**
- Modify: `docs/user-testing/README.md`
- Modify: `docs/user-testing/USER_TEST_TEMPLATE.md`
- Create: `docs/user-testing/checkpoints/uat-runtime-foundation-quick.md`
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `README_工作机使用说明.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`

- [ ] **Step 1: Document exact launch and reset behavior**

Explain that users first close the normal application, then double-click `运行-用户验收.bat`, expect port 8502 and the persistent banner, use `重置-用户验收数据.bat` only when Codex asks, and never continue if the banner is missing. Document fixture `review-demo-v1`, synthetic names, stripped credential environment, disabled legacy profile migration and no-real-LLM behavior.

- [ ] **Step 2: Attach the runtime foundation to P1-29 preparation**

Update P1-29 to require the merged managed UAT launcher and fixed fixture as inputs to its just-in-time black-box checklist. This support package must not mark P1-29 `in_progress` or `verified`, and must not add P1-29 click steps before that gate begins.

- [ ] **Step 3: Update architecture facts**

Record normal/UAT path resolution, controlled UAT root, profile isolation, ports and the health `runtime_mode` field. Document rollback as removing the two UAT launchers and UAT-only code branch, then deleting only the validated repository-external UAT directory; normal `运行.bat` and real data remain untouched. Do not describe future Vue behavior as implemented.

- [ ] **Step 4: Start the UAT runtime and perform browser checks**

Start the command in a long-running terminal session so it remains attached while browser checks run:

```powershell
& $Python tools\run_user_acceptance.py --reset
```

At 1440x900 verify the banner is visible without scrolling, the session selector shows `用户验收演示考试`, the review tab displays synthetic Q1 evidence and the generated paper image, the health endpoint reports `runtime_mode=uat`, and the console has no application error. At 390x844 verify the new banner text wraps without overlap or clipping and does not introduce a new horizontal overflow; record unrelated legacy Streamlit mobile-layout limitations without expanding this package into a redesign. Finally interrupt Streamlit and verify the API also terminates and the runner prints a real-data fingerprint match.

Use the in-app browser control skill for both viewports, preserve only screenshots containing synthetic data, then interrupt the runner and wait until both child processes have exited. Do not treat browser automation as user approval; this is implementation verification only.

- [ ] **Step 5: Generate the first quick-check card from verified screens**

Create `docs/user-testing/checkpoints/uat-runtime-foundation-quick.md` from the shared template only after Step 4. It must contain the actual full functional commit SHA, URL `http://127.0.0.1:8502`, a 5-10 minute estimate, safety preflight, and only actions proven possible during Step 4. Explicitly exclude entering API keys, starting configuration generation, grading, tagging, import or any other AI-backed action. Its result remains `pending` until the user performs the check.

- [ ] **Step 6: Commit docs and architecture**

```powershell
git add docs\user-testing AGENTS.md ARCHITECTURE.md README_工作机使用说明.md docs\superpowers\packages\phase-1-execution-packages.md
git commit -m "docs: explain isolated user acceptance mode"
```

### Task 6: Full Verification and Daytime Integration

- [ ] **Step 1: Run focused suite**

```powershell
& $Python -m pytest tests\test_uat_runtime_mode.py tests\test_user_acceptance_workspace.py tests\test_user_acceptance_launcher.py tests\test_uat_mode_banner.py tests\test_api_app.py tests\test_api_profile_store.py tests\test_run_bat_api_entry.py -q
```

Expected: all pass.

- [ ] **Step 2: Run affected regressions and quick smoke**

```powershell
& $Python -m pytest tests\test_api_openapi_contract.py tests\test_review_media_service.py tests\test_schema_baseline.py tests\test_portable_path_resolution.py -q
& $Python tools\smoke_check.py
git diff --check
```

Expected: all tests pass with zero unexpected skip/failure; compile, copied-database initialization and integrity checks pass.

- [ ] **Step 3: Verify destructive-path and real-data guards**

Use the Task 0 evidence already recorded for both root real databases. Run reset tests against temporary roots, confirm attempts targeting the UAT root itself, repository, real `user_data/`, external path, symlink and Windows junction all fail before deletion, then compare both databases against those recorded values.

- [ ] **Step 4: Request independent Sol Extra High review**

Review path precedence, reparse/delete safety, child cleanup, profile isolation, fixture semantics, API contract and UI banner. Fix all Critical/Important findings and rerun Steps 1-3. Final gate: 0 Critical / 0 Important.

- [ ] **Step 5: Run the first user quick check only after technical gates pass**

Codex starts the isolated runtime and provides the URL plus a generated quick checklist. The user confirms banner, synthetic session, basic navigation and visual clarity. Record `passed` or return failures to this work package; do not call this P1-29 formal acceptance.

- [ ] **Step 6: Use the daytime integration workflow**

This is a non-formal support package, so it must not publish a formal package ID, alter the 87-package counts, or become a nightly candidate. After the user quick check passes, append exact test, browser, review and real-database evidence to this plan, create a final docs-only evidence commit, push the feature branch, create a ready PR, merge through GitHub, fast-forward local `main`, and clean its worktree. P1-29 may list the resulting merge commit as a prerequisite only after it is present in latest `origin/main`.

The evidence commit must contain only this plan and the completed quick-check card:

```powershell
git add docs\superpowers\plans\2026-07-11-user-acceptance-runtime-foundation-implementation.md docs\user-testing\checkpoints\uat-runtime-foundation-quick.md
git commit -m "docs: record user acceptance runtime evidence"
git diff-tree --no-commit-id --name-only -r HEAD
```

Expected: the final command prints exactly those two paths.
