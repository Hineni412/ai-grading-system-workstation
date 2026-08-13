# Issue tracker：GitHub

仓库：`Hineni412/ai-grading-system-workstation`

## P3.5

P3.5 以用户和 Codex 在当前任务中的直接讨论为需求入口，不要求先创建 GitHub Issue。当前阶段、分支、已确认决定和待讨论事项只在 `docs/superpowers/packages/EXECUTION_INDEX.md` 维护。

只有以下情况才创建 Issue：

- 用户明确要求放到 GitHub；
- 问题不属于当前批次，但值得以后单独处理；
- 需要长期跟踪、且不能只靠当前 Index 表达的外部阻塞。

Issue 不改变 P3.5 是否完成，也不能代替用户的实际验收结论。

## 未来正式 Phase

Phase 4 或之后恢复正式执行包流程时，Issue 可以记录包内任务、阻塞关系和范围外问题；正式状态仍只由 Index 决定。

## 标签

| 标签 | 含义 |
|---|---|
| `needs-triage` | 等待确认优先级、归属或处理方式 |
| `needs-info` | 缺少用户决定或必要资料 |
| `ready-for-agent` | 信息与依赖完整，可以实施 |
| `ready-for-human` | 必须由用户或人工操作 |
| `wontfix` | 已决定不实施，并记录原因 |

## 安全

Issue、评论和 PR 不得包含真实学生数据、密钥、真实业务文件、内部绝对路径或未脱敏日志。任何外部写入前先核对仓库和目标编号。
