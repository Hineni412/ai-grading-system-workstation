# Issue tracker：GitHub

本项目使用 GitHub Issues 管理 Matt Pocock skills 产生的规格、任务、问题和决定。

仓库：`Hineni412/ai-grading-system-workstation`

## 权威边界

- `docs/superpowers/packages/EXECUTION_INDEX.md` 是正式执行包状态和当前队列的唯一权威来源。
- GitHub Issues 用于记录包内任务、根因分组、设计决定、复审问题和纵向切片。
- Issue 被关闭、加标签或完成，不得据此宣称正式执行包已经 `merged`。
- Issue 不得包含真实学生数据、密钥、真实业务文件、内部绝对路径或未脱敏日志。

## 基本操作

在仓库内使用 `gh` 命令操作 Issue；所有写入前先确认当前 GitHub 仓库和 Issue 归属正确。

- 创建：`gh issue create --title "..." --body "..."`
- 读取：`gh issue view <number> --comments`
- 列表：`gh issue list --state open --json number,title,body,labels,assignees`
- 评论：`gh issue comment <number> --body "..."`
- 标签：`gh issue edit <number> --add-label "..."` 或 `--remove-label "..."`
- 关闭：`gh issue close <number> --comment "..."`

当 Matt skill 要求“publish to the issue tracker”时创建 GitHub Issue；要求“fetch the relevant ticket”时读取对应 Issue 正文、标签和评论。

## 并行与依赖

- 每个 Issue 必须写清所属执行包、交付结果和阻塞关系。
- 没有未完成阻塞项的 Issue 才能进入并行候选。
- 同一执行包仍由一个功能 worktree/分支负责，不把一个正式包拆到多个分支同时写代码。
- 每个任务开始前先认领 Issue，避免不同窗口重复实施。
- 通道数量、文件所有权和 integration 规则只以 `AGENTS.md` 与 `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md` 为准，本文件不复制其动态或长期上限。

## Pull requests as a triage surface

**PRs as a request surface: no.**

PR 不作为新需求入口；需求和问题先进入 Issue，再由执行包流程实施。

## Wayfinding

- **Map：** 一个总览 Issue 作为 map，使用 `wayfinder:map` 标签，正文维护 Notes、Decisions-so-far 和 Fog。
- **子任务：** 每个任务使用独立 Issue，并使用 `wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`。优先通过 GitHub sub-issues API 挂到 map：先用 `gh api repos/<owner>/<repo>/issues/<child> --jq .id` 取得子任务 database id，再调用 `gh api --method POST repos/<owner>/<repo>/issues/<map>/sub_issues -F sub_issue_id=<child-db-id>`。若仓库不可用 sub-issues，则在 map 正文任务列表中链接子任务，并在子任务顶部写 `Part of #<map>`。
- **阻塞：** 优先使用 GitHub 原生 dependencies。先用 `gh api repos/<owner>/<repo>/issues/<blocker> --jq .id` 取得阻塞项 database id，再调用 `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`。不可用时在子任务顶部写 `Blocked by: #<n>, #<n>`。
- **可执行前沿：** 列出 map 的未关闭子任务，排除已有负责人或仍存在未关闭阻塞项的任务；按 map 顺序选择第一个。原生 dependencies 可读取 `issue_dependencies_summary.blocked_by`，文本回退则逐一确认 `Blocked by` 中的 Issue 是否已关闭。
- **认领：** `gh issue edit <number> --add-assignee "@me"`，并把认领作为任务的第一次外部写入；认领失败或已有负责人时不得开始实施。
- **完成：** 先用评论记录结果、验证证据和长期决定的链接，再关闭子任务，并更新 map 的 Decisions-so-far。正式执行包状态仍只能由 integration 更新 Index。
