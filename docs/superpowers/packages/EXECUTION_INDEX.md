# 现代化路线执行总览

> **更新时间：** 2026-07-10
> **当前阶段：** Phase 1 进行中
> **当前分支：** `codex/wp1-2-api-routes`
> **进度口径：** 只有 `merged` 计入阶段完成

## 当前证据

| 检查 | 2026-07-10 结果 | 结论 |
|---|---|---|
| P1-14 聚焦回归 | 87 passed | OpenAPI、Job 生命周期/公开数据、review、media/files 稳定化契约通过 |
| API 回归 | 85 passed | sessions/config/template/job/review/media/files 现有与新增安全契约兼容 |
| 完整冒烟 | 静态编译 338 文件；两库副本幂等且 `integrity_check=ok` | 当前工作树完整运行门通过 |
| 全量 pytest | 889 passed / 0 skipped / 0 failed | P1-14 后保持跨平台全绿 |
| P1-15 组合回归 | 156 passed | Question Bank、既有 API/Job/review/media/files 契约兼容 |
| P1-15 Question Bank/OpenAPI | 64 passed | 只读筛选/详情/媒体、WAL 临时快照、503 与 OpenAPI 契约通过 |
| P1-15 快速冒烟 | 静态编译 342 文件；两库副本幂等且 `integrity_check=ok` | 真实源库未打开，副本检查通过 |
| P1 检查点隔离完整冒烟 | 967 passed；静态编译 342 文件；两库副本幂等且 `integrity_check=ok` | 提交前在隔离副本完成，真实 `user_data/` 基线不变 |
| 当前 OpenAPI | 30 paths / 38 operations / 0 duplicate IDs | 新增 5 个 Question Bank GET，运行时与文档一致 |
| Git 检查点 | P1-02 至 P1-15 已在 `codex/wp1-2-api-routes` 形成可审阅的本地提交 | 仍为 `verified`，尚未合并到 `main`，不能表述为 Phase 1 已完成 |

## 阶段总表

| Phase | 正式范围 | 状态 | 已合并/已验证 | 待执行包 | 阶段门槛 | 默认执行模型 |
|---|---|---|---:|---:|---|---|
| Phase 0 | 地基与防护网 | `merged` | 5 / 5 | 0 | PR #1 与冻结标签 | 已完成 |
| Phase 1 | API、任务、LLM、数据访问 | `in_progress` | 1 merged + 14 verified | 14 | API E2E、全量冒烟、无缺失超时 | Terra；高风险用 Sol |
| Phase 2 | Vue SPA 与 Streamlit 切换 | `planned` | 0 | 22 | 样板页先验收；真实五流程通过 | Terra；视觉门槛 Sol 复核 |
| Phase 3 | 后端拆分、Schema 收敛、瘦身 | `planned` | 0 | 19 | 迁移预演、全量测试、删除可独立回退 | Terra High / Sol |
| Phase 4 | 图谱 2.0 与训练闭环 | `planned` | 0 | 12 | 新旧口径对照、闭环 E2E、评估达标 | Terra High / Sol |
| Phase 5 | 教师命题训练 | `planned` | 0 | 13 | 实验门槛通过后才冻结 Schema | Sol 规划；Terra 实现 |
| Phase 6 | 班主任学生画像 | `deferred` | 0 | 0 active | 用户重启 + 合规/数据治理确认 | 暂不分配 |

## 依赖顺序

| 前置门槛 | 可开始内容 |
|---|---|
| P1-13 完成（P1-09 至 P1-13 已验证），复核媒体与 E2E 切片可用 | Phase 2 工程基础与单题复核样板页 |
| Phase 1 总门槛通过 | Phase 2 复杂页面扩散 |
| Phase 2 真实五流程与切换完成 | Phase 3 深度拆分与删除 |
| Phase 3 Schema/仓储稳定 | Phase 4 正式数据模型实施 |
| Phase 4 语义和推荐接口稳定；P5 实验门槛通过 | Phase 5 正式实现 |

## 当前执行队列

| 顺序 | 包 | 状态 | 目标 | 模型（规划/执行/复核） |
|---:|---|---|---|---|
| 1 | P1-16 Question Bank 轻写与导入准备 | `ready` | 教师确认标签与安全导入请求，耗时导入仍交给 Job | `S-XH / T-H / S-H` |

最近完成：P1-15 `verified`。即时实现计划为 `docs/superpowers/plans/2026-07-10-p1-15-question-bank-read-routes-implementation.md`；新增试卷、分页筛选、详情、当前标签/富文本/预览元数据和受控图片 GET。源题库通过有界 M1-W1-W2-M2 临时快照读取，SQLite 只打开系统临时候选；持续变化返回脱敏 503，JSON/媒体不公开磁盘路径。组合回归 156 passed，Question Bank/OpenAPI 64 passed，快速冒烟编译 342 文件且两库副本幂等，整包复审 0 Critical / 0 Important / 0 Minor。该包已纳入当前分支的本地 Git 检查点，`user_data/` 基线保持不变。

## 文档入口

| 文档 | 内容 |
|---|---|
| `README.md` | 执行包读取顺序、状态口径、模型代码、升级条件与通用回退 |
| `PLAN_AUDIT_2026-07-10.md` | 原计划合理性、已完成质量、问题与验证证据 |
| `../specs/2026-07-10-roadmap-execution-packages-design.md` | 两级计划体系、Phase 5/6 决策与生成原则 |
| `phase-1-execution-packages.md` | 当前 API/Job 状态与 14 个剩余包 |
| `phase-2-execution-packages.md` | Vue 基础、样板页、页面迁移和切换共 22 包 |
| `phase-3-execution-packages.md` | 仓储、SessionManager、Schema、删除和性能共 19 包 |
| `phase-4-execution-packages.md` | 关系、图谱、掌握度和训练闭环共 12 包 |
| `phase-5-execution-packages.md` | 四个实验、设计门槛和正式命题工作台共 13 包 |
| `phase-6-deferred.md` | 延后原因、重启条件和不可取消红线 |

## 下一动作

执行 `P1-16`。先重新调查 Question Bank 现有写行为并生成即时实现计划；只使用临时题库和上传目录实现教师确认标签、安全元数据轻写与导入请求准备，耗时导入仍交给 Job，不恢复旧技能自动双写。P1-02 至 P1-15 已在当前分支形成 Git 检查点，但尚未 push、创建 PR 或合并到 `main`；后续 Git 集成仍需用户明确确认。
