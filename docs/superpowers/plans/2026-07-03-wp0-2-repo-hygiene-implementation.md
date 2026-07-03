# WP0.2 仓库卫生 Implementation Plan

> 所属：总体方案 Phase 0（docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md）。
> 执行方式：按 Task 顺序勾选执行；每步验证后再进行下一步。

**Goal:** 清理仓库根目录杂物（归档而非删除），补齐 .gitignore 防止再生，使 `git status` 不再有与业务无关的噪音。

**已核验事实（2026-07-03）：**

- 以下 7 项全部为**未跟踪**文件/目录（无需 git rm）：`debug_test.db`、`test_math.png`、`merge_staging_known_hosts.tmp`、`第十五周学情反馈.pdf`、`task.md`（内容已全部完成勾选）、`scratch/`、`analysis_outputs/`。
- `.superpowers/` 含 2 个**已跟踪**文件（sdd task 报告）——**不在本任务范围**，不动。
- `.gitignore` 已含 `*.tmp`、`.pytest_cache/`、`.worktrees/`、`__pycache__/`。

## Global Constraints

- **只移出，不删除**：所有杂物移动到仓库外归档目录 `C:\Users\89418\Desktop\AI阅卷系统_归档_2026-07-03\`，完全可回退。
- 不触碰：`.superpowers/`、`user_data/`、任何已跟踪文件。
- `第十五周学情反馈.pdf` 是用户真实文档，移动后必须在汇报中明确告知去向。

## Tasks

### Task 1: 归档杂物

- [ ] Step 1: 创建归档目录 `C:\Users\89418\Desktop\AI阅卷系统_归档_2026-07-03\`。
- [ ] Step 2: 移动 7 项杂物到归档目录（mv，保留原名）。
- [ ] Step 3: 核对：归档目录含 7 项；仓库根目录不再存在这些路径。

### Task 2: 补 .gitignore

- [ ] Step 1: 在 `.gitignore` "Temporary files" 区块附近追加：

~~~gitignore
# 调试与分析产物（根目录不应再出现）
debug_*.db
scratch/
analysis_outputs/
~~~

- [ ] Step 2: 验证 `git status --short` 中不再出现上述任何路径。

### Task 3: 提交

- [ ] Step 1: `git add .gitignore`，确认暂存区只有 .gitignore（杂物均为未跟踪，移出不产生 git 变更）。
- [ ] Step 2: 提交 `chore: 清理根目录杂物并归档，补齐 .gitignore`。

## 验收

- `git status --short` 无杂物噪音；归档目录 7 项齐全。

## 回退

- 从归档目录把文件移回仓库根目录；revert .gitignore 提交。
