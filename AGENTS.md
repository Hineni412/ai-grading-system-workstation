# AI 阅卷系统 Codex 接手说明

本文件是 Codex 在本仓库工作的优先入口。历史内容如提到 `CLAUDE.md`，等同于阅读本文件；`CLAUDE.md` 只保留兼容指针。

## Agent skills

### Skill authority

本项目使用 Matt Pocock engineering skills，禁止调用 Superpowers skills。

`docs/superpowers/` 是历史兼容目录名，不代表允许调用同名技能。历史计划中的 `superpowers:*` 指令只保留为过程背景，实际执行改用：

- 存在必须由用户决定、会明显改变最终结果且无法从项目资料判断的需求或设计选择：`grilling`
- 测试优先开发：`tdd`
- 按计划实施：`implement`
- 困难故障诊断：`diagnosing-bugs`
- 稳定候选复审：`code-review`
- 合并冲突：`resolving-merge-conflicts`

能够从项目文件、设计文档、现有代码或测试查明的事实必须自行调查；其他轻微不确定性采用最保守的合理假设继续，并在结果中说明，不得仅为消除形式上的不确定而打断用户。使用 `grilling` 时一次只询问一个关键问题，并同时给出推荐答案。

匹配的 Matt Pocock skill 在当前会话可用时优先使用；如果项目指定的 skill 已安装但当前会话没有提供，或读取失败，则说明原因并按本文件和项目现有流程继续，不虚构调用结果，也不因技能不可用而放弃仍可安全完成的工作。

技能内的通用测试、提交和分支建议如与本仓库规则冲突，以本文件的分层测试、真实数据保护、执行包、worktree 和 integration 规则为准。

代码与需求复审的严重程度统一使用 `Critical`、`Important`、`Suggestion`；用户可见功能验收继续使用 `Blocker`、`Major`、`Minor`。两套等级用途不同，不混用，也不追溯改写历史证据。

### Issue tracker

包内任务、问题、设计决定和待办事项使用 GitHub Issues 管理。正式执行包状态仍只以 `docs/superpowers/packages/EXECUTION_INDEX.md` 为准；关闭 Issue 不代表执行包已经合并。详见 `docs/agents/issue-tracker.md`。

### Triage labels

采用 Matt Pocock 默认标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

采用 single-context：领域词汇使用根目录 `CONTEXT.md`，架构决定在首次需要时使用 docs/adr/。这些文件按实际需要逐步建立，不为初始化制造空文档。详见 `docs/agents/domain.md`。

## 当前产品边界

- 版本以根目录 `VERSION` 为准。
- 当前产品是 Windows 本机单用户应用。生产 UI 为 Vue 3 SPA，由 FastAPI 同源托管并监听 `127.0.0.1:8000`。
- FastAPI、既有 API/JobManager 与 Vue 3 SPA 已进入共同生产基线；旧 Streamlit 日常 UI 不再作为生产入口。
- 迁移期间留下的 Streamlit 行为证据、当前服务实现、数据库契约和测试共同作为业务事实来源；旧 Streamlit 日常入口已退役，但历史证据仍约束用户任务、业务能力、数据语义、业务结果、状态、权限、安全、持久化、危险操作保护、失败恢复、幂等性与审计。
- 当前阶段、包状态和下一动作只在 `docs/superpowers/packages/EXECUTION_INDEX.md` 维护。

## 必读顺序

开始任何较大改动前，按顺序阅读基础资料：

1. `AGENTS.md`
2. `ARCHITECTURE.md` 中与当前改动相关的章节
3. `docs/superpowers/packages/README.md`
4. `docs/superpowers/packages/EXECUTION_INDEX.md`
5. 当前包对应的 `docs/superpowers/packages/phase-*-execution-packages.md`
6. 当前执行包的即时实现计划、相关代码、测试和迁移文件

再按任务场景增读，不把无关资料加入每次上下文：

- 夜间自动化：`docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`、`docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- 并行、worktree 或 integration：`docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`
- 可见功能或用户验收：`docs/user-testing/README.md`
- 跨阶段设计、目标架构或权威冲突：`docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

前端视觉改造还必须阅读 `docs/ui/STYLE.md`；该文件只约束颜色、字体、间距、密度、控件外观、可访问性和通用视觉质量，不定义导航、页面、字段、操作、状态、快捷键或流程。AI 概念图、演示数据、目标架构和历史计划都不能单独作为业务来源。

可见前端必须先建立“业务能力与 UX 设计溯源表”。P2-03 至 P2-08 已实现的 Vue 行为仅作为历史证据、审计输入和复用候选，不能单独证明业务事实或界面范式；只有回溯到业务事实来源，或有用户留下日期与范围的明确决定支持的部分，才能进入业务基线。

在业务能力可达、数据语义和结果等价、安全边界不降低的前提下，新 Vue SPA 可以主动重组导航、合并或拆分页面、调整布局、控件、呈现顺序和操作步骤。无来源的业务能力、数据语义或业务结果不得实现；服务于已有能力的新布局和控件不要求旧 UI 存在同形元素。

Phase 2 复杂页面扩散必须满足来源重校准门槛和包依赖；门槛结果与当前包状态只在 `docs/superpowers/packages/EXECUTION_INDEX.md` 维护。

## 权威顺序

- `AGENTS.md`：长期协作规则、安全红线和工作流程。
- `ARCHITECTURE.md`：当前已实现的系统事实、边界和风险。
- Master Plan：目标架构、阶段依赖和长期技术决策。
- `EXECUTION_INDEX.md`：正式执行包的唯一动态状态源和当前队列。
- Phase maps：稳定的包定义、依赖、边界、验收与模型要求，不保存动态状态。
- `NIGHTLY_ELIGIBILITY_MATRIX.md`：稳定的夜间资格，不复制包状态。
- 当前包即时计划：开工后的源码级步骤和可验证交接状态。

发生冲突时，先按上述职责判断权威来源；无法判断或涉及业务含义时停止并请求用户确认。

## 面向用户的沟通

- 默认用户没有编程基础。交付说明先讲清楚“改好了什么、会带来什么影响、用户是否需要操作、接下来可以怎么选”，不要让用户先阅读内部实现过程。
- 文件清单、代码结构、命令、测试数字、Git 流程和内部状态默认放在次要位置；只有它们会影响用户判断、风险或后续操作，或者用户明确要求时才展开。
- 无法避免的技术术语在第一次出现时必须用通俗语言解释；适合时用生活化比喻帮助理解，例如把数据库迁移说明为“搬家前先清点和备份，再按清单搬进新柜子”。
- 简化表达不能省略数据安全、失败影响、已知风险、回退方式和必须由用户决定的事项；这些内容要改写成用户能够据此作决定的语言。

## 数据与安全红线

- `user_data/databases/grading_system.db`、`user_data/databases/question_bank.db` 和 `user_data/` 文件树都是真实业务数据。
- 除非用户对本次具体操作明确授权，不得改、删、暂存、提交或用 stash 打包真实 `user_data/`。
- 数据库变更必须有迁移、隔离副本预演、备份和测试。测试和冒烟只能在副本上执行写操作。
- 不得向文档、日志、API 响应或 Git 提交泄露内部绝对路径、密钥或真实业务数据。
- 已接受的历史真实数据例外只记录为约束，不可据此扩大后续写入权限。

## 执行包工作原则

- 先调查再判断，不凭文件名猜字段、接口、状态或业务规则。
- 工作目录默认策略：根目录保持 `main`；有活动里程碑时默认只保留一个滚动复用的功能 worktree 和一个 integration worktree，按 Index 的固定顺序串行推进。每个执行包仍使用独立短分支；只有 Index 明确记录并行收益、文件所有权和第二通道时，才临时启用第二个功能 worktree。
- GitHub Issues 可以拆分包内任务和记录阻塞关系，但不能自行改变执行包状态、依赖或领取顺序。
- 优先复用现有服务与测试，不为目标架构提前重写业务逻辑。
- 结构性变化同步更新 `ARCHITECTURE.md`；普通局部修复不做无意义文档改写。
- 执行包是稳定边界，不是永久有效的代码级步骤。每包开工前根据最新源码生成即时实现计划，完成后更新索引与证据。
- 已领取正式包必须在即时计划维护标记化交接块；允许状态及证据规则以 packages README 为准，字段或 Git 证据不一致时安全停机。
- 用户验收必须使用版本化清单，明确分类、证据和用户确认。具体数据源、启动方式、地址、可见标识与关闭方式，只有在对应执行包实现并验证后才能写入清单；不得把尚未实现的专用验收运行环境写成现有能力。
- 夜间资格和复核要求以 packages README、资格矩阵及当前包定义为准。
- 普通开发按里程碑批次推进，每批固定 3 个相关执行包；数据库迁移、核心状态机、生产切换、正式用户验收、删除等高风险包可以单独成批，不为凑数搭配无关任务。批次组成、顺序和精确基线必须先写入 `EXECUTION_INDEX.md`。
- 测试按反馈范围分层：开发和复审修复阶段运行当前测试、包内聚焦测试与受影响回归；功能分支交接前运行 `tools/smoke_check.py --skip-tests`，默认不运行全量 pytest。integration 逐包运行受影响验证，并在里程碑批次末只运行一次完整门槛。
- 完整测试证据仅在代码 SHA、依赖基线和测试配置相同且其后没有实质代码变化时复用。独立复审、纯文档或交接提交、PR 合并后的主线同步本身不触发全量测试。
- 上次全量后有实质代码变化、合并冲突后手工改代码、公共基础设施或依赖锁变化、数据库 Schema 或核心状态机变化、跨域共享入口变化、测试失败或不稳定、最终候选 SHA 不一致，以及执行包明确要求专项全量门禁时，必须按影响和风险补跑全量测试；删除和破坏性迁移的专项门禁不得省略。

## Git 与 Worktree 强制流程

并行分工、共享文件所有权和启动规则以 `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md` 为准：

1. 根目录始终保持本地 `main`。有活动里程碑时，默认拓扑是一个滚动功能 worktree 加一个 integration worktree；没有活动里程碑时可以只保留根目录。
2. 滚动功能 worktree 同一时间只实施一个执行包；每个包仍创建独立短分支。复用的是物理目录，不是在旧功能分支继续叠包。
3. 里程碑第 1 包从 `origin/main` 创建；后续包从该里程碑已完成逐包验证的 integration 精确 SHA 创建新分支。
4. 只有上一包已进入 integration 并完成逐包验证、功能目录干净、`user_data/` 无本地项、没有未知或未交接提交且没有外部 reparse point 时，才可在同一功能 worktree 切换到下一包分支。
5. 功能分支完成 TDD、包内回归、快速冒烟和指定复核后，提交给 integration 分支，不直接写入 `main`。
6. integration 一次合入一个包并解决共享入口/文档冲突；逐包只跑受影响验证，里程碑完成后运行一次组合回归与完整冒烟，并核对真实两库指纹不变。
7. 里程碑门槛通过后才推送 integration，并通过一个 PR 合并 GitHub `main`；默认禁止直接 push `main`。
8. PR 合并后同步 `origin/main`、本地 `main` 和活动 worktree；下一里程碑必须基于新主线。第 3 包确实受阻时，允许前 2 包作为明确记录的部分里程碑收口。
9. 只有 `git branch --merged origin/main` 能证明已合并且不再承担任务的分支才可普通删除；独有提交必须保留，除非用户明确授权丢弃。
10. 删除或重新分配 linked worktree 前分别检查源码状态、`user_data/` 和 Windows reparse point。存在本地数据、未合并提交或指向外部的链接时必须停止；禁止手工递归删除 worktree。
11. 任何阶段都不得暂存、提交或 stash 真实 `user_data/`。

用户明确要求启动执行包，即持续授权该包走完标准功能分支、integration、push、PR、合并和基线同步流程。该授权不包含直接 push `main`、force push、真实数据写入、不可逆删除，或丢弃 `origin/main` 未包含的历史提交。

## 夜间自动化

如启用每天 4:00 的“阅卷系统夜间4点推进”，以 `docs/superpowers/packages/NIGHTLY_AUTOMATION.md` 为唯一权威提示词。任务选择还必须读取资格矩阵、并行手册、Index 和候选包即时计划，并用 `tools/handoff_status.py` 核验证据。

夜间只允许实施同时满足以下条件的包：资格为 `eligible_after_plan`、有白天预先分配且由当前包独占的干净滚动功能 worktree、Index 状态和即时计划满足自动化门槛、规划已经过要求的复核。夜间不得自行切换旧分支或重新分配 worktree；实现只能形成本地功能提交并留下 `waiting_review`，不得自行宣称独立复审或用户验收通过，不得 push、创建 PR、合并、清理 worktree/分支或操作真实数据。

## 常用命令

优先使用项目自带运行时：

```powershell
runtime\python\python.exe -m pytest -q
runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_schema_baseline.py -q
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_run_bat_api_entry.py -q
runtime\python\python.exe tools\smoke_check.py
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe -m backend.api.launcher --host 127.0.0.1 --port 8000
```

日常启动可双击 `运行.bat`。

## 当前工作入口

任何任务开始时，先读取 `docs/superpowers/packages/EXECUTION_INDEX.md` 获取当前阶段、执行队列和下一动作；不要从本文件推断动态进度。
