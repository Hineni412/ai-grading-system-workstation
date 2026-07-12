# 现代化路线执行总览

> **职责：** 本文件是正式执行包动态状态、当前队列和下一动作的唯一权威来源。
> **进度口径：** 只有 `merged` 计入阶段完成；Phase maps 的依赖不是状态。
> **夜间资格：** 只在 `NIGHTLY_ELIGIBILITY_MATRIX.md` 维护；资格不代表当前可以领取。
> **用户验收：** 稳定规则与反馈格式见 `docs/user-testing/README.md`；具体清单只在对应能力已经实现并验证后写入 `docs/user-testing/checkpoints/`。

## 阶段总表

| Phase | 正式范围 | 状态 | 已合并 | 待执行 | 阶段门槛 |
|---|---|---|---:|---:|---|
| Phase 0 | 地基与防护网 | `merged` | 5 | 0 | 工程防护网与冻结基线 |
| Phase 1 | API、任务、LLM、数据访问 | `in_progress` | 23 | 6 | API E2E、完整冒烟、模型请求超时门槛 |
| Phase 2 | Vue SPA 与 Streamlit 切换 | `in_progress` | 3 | 19 | 样板页先验收；真实五流程通过 |
| Phase 3 | 后端拆分、Schema 收敛、瘦身 | `planned` | 0 | 19 | 迁移预演、全量测试、删除可独立回退 |
| Phase 4 | 图谱 2.0 与训练闭环 | `planned` | 0 | 12 | 新旧口径对照、闭环 E2E、评估达标 |
| Phase 5 | 教师命题训练 | `planned` | 0 | 13 | 实验门槛通过后才冻结 Schema |
| Phase 6 | 班主任学生画像 | `deferred` | 0 | 0 | 用户重启并确认合规与数据治理边界 |

## 包状态登记

下列范围没有省略中间编号；同一行中的每个包均采用该状态。标题、边界、依赖和验收见对应 Phase map。

| 包或连续范围 | 状态 | 说明 |
|---|---|---|
| P1-01 至 P1-17 | `merged` | P1-17 已完成可查询、可取消、可重试且原子绑定的配置生成 Job；P1-01 至 P1-08 不属于正式 87 包统计 |
| P1-18 | `merged` | 已实现题库导入与 AI 打标 Job 的进度、取消、部分失败、重试与幂等保护；只使用临时题库和假 AI 验证 |
| P1-19 | `merged` | 已提供 tag-only 诊断、精确标签推荐预览、幂等教师任务确认以及训练任务分页/详情；只在临时双库验证写入 |
| P1-20 | `merged` | 已将现有 Word/Markdown 与整任务 ZIP bundle 接入可查询、可取消、可重试的 Training 导出 Job；文件在 job 专属目录原子发布并通过受控 URL 下载 |
| P1-21 | `merged` | 已提供 tag-only profiles、确定性 graph rows/聚合节点、固定空关系边和分页证据下钻；两库只通过稳定临时候选读取，不打开真实源库 |
| P1-22 | `merged` | 已提供路径无关的版本、目录可写性、数据库临时候选完整性/迁移摘要、外部工具/API 配置状态和有界备份清单；不暴露危险写操作 |
| P1-23 | `merged` | 已提供五类 Ops 严格预检、5 分钟单次确认令牌和独立 Job；备份/导出在线原子发布，恢复/迁移/导入在双服务启动前离线复核、备份、应用并按 Journal 回退；只在临时数据根验证，未操作真实业务数据 |
| P1-24 | `ready` | 下一批 LLM Gateway 核心候选；统一超时、重试分类、节流、请求 ID 和用量记录，不改变模型供应商、prompt 或评分语义 |
| P1-25 至 P1-29 | `planned` | 按依赖逐包放行 |
| P2-01 | `merged` | 已建立精确依赖锁、质量命令、Chromium e2e、loopback API 代理和便携 dist 复制 |
| P2-02 | `merged` | 已固化设计 Token、Element 主题、基础控件与多视口展示页 |
| P2-03 | `merged` | 已建立仅支持 Windows 桌面浏览器（最小 1024px）的 App Shell、导航、路由、全局 session 上下文、404 与安全错误恢复；复杂业务页面尚未迁移 |
| P2-04 | `ready` | 下一批 API Client、统一错误契约与可恢复 Job Store 候选；只建立前端网络层，不迁移业务页面 |
| P2-05 至 P2-22 | `planned` | 样板页门槛前不得扩散迁移 |
| P3-01 至 P3-19 | `planned` | Phase 2 切换门满足后放行 |
| P4-01 至 P4-12 | `planned` | Phase 3 数据边界稳定后放行 |
| P5-01 至 P5-13 | `planned` | 先实验与设计门，再进入正式实现 |

状态变更只修改本表、阶段汇总和当前队列；不得同步复制到 Phase maps 或夜间资格矩阵。

## 依赖顺序

| 前置门槛 | 可开始内容 |
|---|---|
| P1-13 的复核媒体与 E2E 切片可用 | Phase 2 工程基础与单题复核样板页 |
| Phase 1 总门槛通过 | Phase 2 复杂页面扩散 |
| Phase 2 真实五流程与切换完成 | Phase 3 深度拆分与删除 |
| Phase 3 Schema/仓储稳定 | Phase 4 正式数据模型实施 |
| Phase 4 语义和推荐接口稳定，且 Phase 5 实验门通过 | Phase 5 正式实现 |

## 当前执行队列

| 顺序 | 包 | 状态 | 目标 |
|---:|---|---|---|
| 1 | P1-24 LLM Gateway 核心 | `ready` | 建立唯一模型请求策略层，统一超时、重试分类、节流、请求 ID 和用量记录，不迁移调用方或改变模型行为 |
| 2 | P2-04 API Client、错误契约与 Job Store | `ready` | 建立类型化 API client、统一错误解析和可恢复 job store，不提前迁移业务页面 |

领取任一候选前，必须根据最新源码生成即时实现计划，并现场核验该包的 worktree、分支、共同基线、真实 `user_data/` 状态和交接块。

## 文档入口

| 文档 | 职责 |
|---|---|
| `README.md` | 执行包规则、状态口径、安全停机条件与通用回退 |
| `PARALLEL_WORKTREE_EXECUTION.md` | 持久并行、集成、同步与安全清理规则 |
| `NIGHTLY_ELIGIBILITY_MATRIX.md` | 87 个正式包的稳定夜间资格、理由和额外门槛 |
| `NIGHTLY_AUTOMATION.md` | 每天 4:00 自动化的选择、停机、验证与本地提交规则 |
| `../../user-testing/README.md` | 短测/正式验收节奏、数据安全、反馈格式与结果等级 |
| `phase-1-execution-packages.md` | Phase 1 包定义与依赖 |
| `phase-2-execution-packages.md` | Phase 2 包定义与依赖 |
| `phase-3-execution-packages.md` | Phase 3 包定义与依赖 |
| `phase-4-execution-packages.md` | Phase 4 包定义与依赖 |
| `phase-5-execution-packages.md` | Phase 5 包定义与依赖 |
| `phase-6-deferred.md` | Phase 6 延后原因、重启条件和安全红线 |

## 下一动作

P1-24 与 P2-04 可以在各自专属 worktree 中并行，但都必须先基于最新共同基线创建或同步 worktree，并完成即时计划和现场门槛。P1-24 只建立统一模型请求策略层，不迁移四处调用方、不更换用户模型供应商，也不改变 prompt 或评分语义；任何真实模型健康检查仍需另行授权。P2-04 只建立类型化 API client、统一错误契约和可恢复 Job Store，样板页验收前仍不得扩散迁移其他业务页面。功能提交逐包交给 integration 验证，通过 PR 进入 GitHub `main` 后再同步共同基线。
