# 项目计划与已完成工作质量审计

> **审计日期：** 2026-07-10
> **范围：** Master Plan、Phase/WP 文档、当前分支增量 API/JobManager、迁移与测试证据
> **数据边界：** P1-14 一次未完整 override lifespan 的诊断在真实 grading DB 创建了空 `jobs` 表与两个索引；用户明确要求保留。该接受例外之后不再写入真实 `user_data`，完整 smoke 前后两库文件指纹一致，且 `user_data/` 不进入候选提交。

## 总结

| 维度 | 评分 | 结论 |
|---|---:|---|
| 总体架构方向 | 8/10 | Streamlit -> FastAPI -> Vue 的渐进顺序合理，保留旧入口回退也合理 |
| 已完成代码工程质量 | 8/10 | 有依赖注入、契约测试、临时数据库、迁移预演和服务复用 |
| 当前可集成状态 | 6/10 | P1-02 至 P1-15 已形成可审阅的本地 Git 检查点；尚未合并到 `main` |
| 原计划可执行性 | 6/10 | Phase 0/1 较具体；Phase 2-6 多为方向段落，不能直接交给执行模型 |
| 自动化测试质量 | 8/10 | 覆盖主契约和多数异常，但欠缺大数据量、事务、真实取消和下载闭环 |
| 文档一致性 | 5/10 | 日期、测试数量、行号、状态和“完成”口径存在漂移 |

## 已完成部分的优点

1. API 以现有服务和 DBManager 为事实来源，没有为了新架构提前重写评分业务。
2. FastAPI dependency override 让测试使用临时库和临时目录，避免污染真实数据。
3. 每批 API 基本都有失败测试、契约测试、回归和快速冒烟证据。
4. template/regions 提交继续复用数据库+文件快照补偿，而不是绕过安全协议。
5. grading job 复用 `grading_runs/grading_run_items`，没有另造批改明细账本。

## 必须修正的问题

| 级别 | 问题 | 影响 | 对应执行包 |
|---|---|---|---|
| 已解决 | 当前大量 API/Job 文件尚未形成 Git 检查点 | 用户已授权把 P1-02 至 P1-15 整理为当前分支本地提交；仍未 push、创建 PR 或合并到 `main` | P1-09 至 P1-15 `verified`，当前分支 Git 检查点 |
| 已解决 | P1-11 已让 running cancel 只记录请求，并由 handler 在安全边界确认；现有 report/scan/grading 已接入 | 后续新增 job 类型必须复用同一确认协议，不能只改状态 | P1-11 verified |
| 已修复 | `jobs` 表曾同时由 migration 和 JobStore 字面 DDL 定义 | P1-10 已改为仅从 `003_add_jobs.sql` 读取完整 DDL，并保留旧表补列兼容 | P1-10（`verified`） |
| 已修复 | review router 曾聚合业务、逐 result 查询且多结果写入无总事务 | P1-12 已改为应用服务单 JOIN 读模型和跨 result SQLite 总事务；事务后批注采用脱敏可重试补偿 | P1-12（`verified`） |
| 已修复 | job manager 曾为全局单例且缺少 app shutdown 生命周期 | P1-10 已改为 app state/lifespan 所有；override 外部实例不由应用关闭 | P1-10（`verified`） |
| 已修复 | report job 曾返回绝对文件路径且缺图片/下载资源边界 | P1-13 已提供语义化媒体/下载入口、受控根/类型/所属关系校验；P1-14 进一步统一公开数据净化和二进制 OpenAPI 契约 | P1-13/P1-14（`verified`） |
| 中 | 完整测试在失效 `Z:` 路径上稳定失败，且两个锁测试只支持 POSIX fork | 已修复并恢复跨平台全绿基线 | P1-09（`verified`） |
| 中 | WP1.3 被写成“完成”，实际只完成最小框架和三类 handler | 后续模型可能跳过 config/tagging/training job | P1-17/P1-18/P1-20 |
| 中 | Master Plan 称 `training_attempts` 无写入方，但已有 stub | Phase 4 可能重复建设 | P4-09 已校正 |
| 中 | 原 Phase 3 建议删除旧迁移历史 | 会破坏历史升级可追溯性 | P3-17 改为前向退役迁移 |

未发现需要立即停止使用现有 Streamlit 主流程的 P0 数据破坏问题；上述高风险主要影响当前增量 API 和未来切换质量。

## 2026-07-10 验证证据

| 检查 | 结果 |
|---|---|
| API/Job 核心测试 | 53 passed |
| migration/schema/smoke tests | 14 passed |
| `smoke_check.py --skip-tests` | 编译 320 个第一方 Python 文件；两库副本幂等 OK |
| 审计初始全量 pytest | 772 passed / 2 skipped / 1 failed |
| P1-09 后全量 pytest | 776 passed / 0 skipped / 0 failed |
| P1-09 根因与处理 | `Path.is_file()` 的不可访问网络盘 `OSError` 未处理；两个 skip 为 fork-only 测试设计。现已安全回退，并用跨平台 spawn 测试覆盖真实锁行为 |
| P1-10 聚焦回归 | 34 passed；覆盖 DDL 权威、旧表兼容、连接释放、关机竞态、lifespan 与四组任务 API |
| P1-10 迁移与冒烟 | 阅卷库临时副本执行 003 成功、Schema 等价、业务行数无变化；编译 320 文件且两库副本幂等 |
| P1-10 后全量 pytest | 785 passed / 0 skipped / 0 failed |
| P1-11 后全量 pytest | 797 passed / 0 skipped / 0 failed；三类真实 Job 协作式取消与副作用边界通过 |
| P1-12 后全量 pytest | 823 passed / 0 skipped / 0 failed；复核单 JOIN、跨 result 原子确认与批注补偿通过 |
| P1-13 后全量 pytest | 871 passed / 0 skipped / 0 failed；受控媒体/下载、语义化 URL 与公开路径脱敏通过 |
| P1-14 聚焦/API | 87 passed / 85 passed；OpenAPI、Job 生命周期/公开数据、review、media/files 契约通过 |
| P1-14 完整冒烟 | 889 passed / 0 skipped / 0 failed；编译 338 个第一方文件；两库临时副本初始化幂等且 `integrity_check=ok` |
| P1-14 独立复审 | 首轮 7 个代码 Important 与 1 个测试 Minor 均经 TDD 修复；聚焦复审为 0 Critical / 0 Important / 0 Minor |
| P1-14 数据守卫 | 接受后的 grading DB 为 2,863,104 bytes / `93fee56e...fb841cd`，question-bank DB 为 3,461,120 bytes / `e1e5123a...7a88b8`；完整 smoke 后均不变，`user_data` 状态仍为 205 行 / `b81a376...f6e343cce8` |
| P1-15 组合 / Question Bank OpenAPI | 156 passed / 64 passed；试卷、分页筛选、详情、富文本/预览元数据、受控 asset/preview、WAL 临时候选与 503 契约通过 |
| P1-15 快速冒烟与复审 | 静态编译 342 个第一方 Python 文件；两库副本幂等且 `integrity_check=ok`；整包与安全复审 0 Critical / 0 Important / 0 Minor |
| P1 检查点隔离完整冒烟 | 967 passed；编译 342 个第一方 Python 文件；两库副本初始化幂等且 `integrity_check=ok`；源 `user_data` 状态和两库 SHA-256 与接受基线一致 |

## 计划结构调整

| 原状 | 调整后 |
|---|---|
| Master Plan 同时承担方向、进度和执行细节 | Master 只保留策略；Index 负责进度；Phase maps 负责执行包 |
| “已实现”容易等同“已完成” | 使用 implemented_uncommitted / verified / merged 分级 |
| Phase 2-5 大包直接交给模型 | 拆为 87 个独立验收包，开工时再生成源码级 TDD 计划 |
| Phase 2 等全部 Phase 1 完成才验证 UI | review/media 切片稳定后先做样板页，其他页面仍等 Phase 1 总门槛 |
| Phase 5 直接定 Schema | 先做工作流、编辑器、相似度、AI rubric 四项实验和用户门槛 |
| Phase 6 正式排期 | 按用户决定延后，当前无活动执行包 |

## 模型性价比建议

下表是 87 个正式包“执行槽”的实际分配，不是官方价格承诺；规划槽全部使用 Sol Extra High，复核槽另按风险分配。Codex 套餐和模型可用性以模型选择器为准。

| 模型 | 效果/成本定位 | 执行包分配 | 适用包 |
|---|---|---:|---|
| GPT-5.6 Terra Medium | 最省成本，适合边界很窄的实现 | 6 / 87（6.9%） | 小型 API、明确修复与低风险适配 |
| GPT-5.6 Terra High | 本项目主要执行模型，兼顾跨模块能力与成本 | 63 / 87（72.4%） | 复杂测试、页面、Job、服务和 repository 迁移 |
| GPT-5.6 Sol High/Extra High | 效果优先，保留给高风险实现 | 18 / 87（20.7%） | Schema、并发、语义门、LLM 核心、删除和发布事务 |
| GPT-5.6 Luna Medium | 仅用于包外机械辅助，不承担正式包端到端执行 | 0 / 87 | 状态抄录、格式整理和确定性清单 |

推荐工作方式：Sol Extra High 为全部包生成源码级计划；Terra 执行 79.3% 的正式包；Sol 执行或复核高风险包。官方模型说明：<https://learn.chatgpt.com/docs/models>。

## 结论

原路线可以继续，不需要推倒重写；P1-09 至 P1-15 已验证，review/media/公开数据/OpenAPI 稳定化门和 Question Bank 严格只读门均已通过。当前分支 Git 检查点已按用户授权形成；下一执行包是 P1-16 Question Bank 轻写与导入准备。详细进度和下一动作以 `EXECUTION_INDEX.md` 为准。
