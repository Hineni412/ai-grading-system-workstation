# WP0.5 冒烟脚本 Implementation Plan

> 所属：总体方案 Phase 0。执行方式：按 Task 顺序勾选执行。

**Goal:** 一条命令完成"静态编译 + 全量测试 + 两库初始化幂等检查"，作为后续每个工作包的统一回归入口（总体方案要求每个 WP 必跑）。

**已核验事实（2026-07-03）：**

- `tools/` 已有 storage_audit.py 等维护脚本（放置约定成立）；根目录已有 `运行核心测试.bat` 先例（.bat 包装约定成立）。
- 全量测试当前 705 通过 / 2 跳过，约 110 秒。

**当前状态（2026-07-08，Codex 接续）：**

- `tools/smoke_check.py`、`冒烟检查.bat`、`tests/test_smoke_check.py` 已在当前工作区新增。
- 快速冒烟已通过：`runtime\python\python.exe tools\smoke_check.py --skip-tests`。
- 完整冒烟已通过：`runtime\python\python.exe tools\smoke_check.py`，结果为静态编译 OK、全量测试 719 passed / 2 skipped、两库副本初始化幂等 OK。
- 提交步骤暂不执行，需先决定 WP0.4/WP0.5 与 `user_data` 变更的提交边界。

## Global Constraints

- 冒烟脚本**只读**真实库（复制到临时目录再操作副本），绝不写真实 user_data。
- 任何一步失败 → 退出码非 0 且汇总打印失败步骤；成功打印各步耗时。

## File Structure

Create:

- `tools/smoke_check.py`
- `冒烟检查.bat`（根目录，便携 python 包装）

## Tasks

### Task 1: 实现 tools/smoke_check.py

- [x] Step 1: 三个检查步骤，顺序执行、聚合结果：
  1. **静态编译**：`compileall` 编译第一方 .py（排除 `runtime/`、`.worktrees/`、`user_data/`、`__pycache__`、`.git`）；
  2. **全量测试**：`python -m pytest -q`（透传退出码与末行摘要）；
  3. **两库初始化幂等**：复制两真实库到临时目录（真实库不存在则跳过并注明）→ 对副本各运行两次运行时初始化（grading: `DBManager.initialize()`+`GradingRunStore.initialize()`；QB: `initialize_database()`）→ 两次之间 Schema 无变化 → `PRAGMA integrity_check` = ok。
- [x] Step 2: `--skip-tests` 可选参数（联调时快速跑编译+库检查）；默认全跑。

### Task 2: .bat 包装与验证

- [x] Step 1: `冒烟检查.bat`：用 `runtime\python\python.exe tools\smoke_check.py %*` 并 `pause`（沿用 运行核心测试.bat 风格）。
- [x] Step 2: 完整运行一次 `smoke_check.py`，三步全绿。
- [x] Step 3: 验证失败传播：临时用 `--skip-tests` + 人为坏路径场景仅做代码走查（不引入假失败提交）。

### Task 3: 提交

- [ ] Step 1: 提交 `feat: 冒烟脚本统一回归入口`。

## 验收

- `冒烟检查.bat` 或 `python tools/smoke_check.py` 一条命令全绿；作为此后每个 WP 的验收入口写入工作流。

## 回退

- 纯新增文件，revert 即可。
