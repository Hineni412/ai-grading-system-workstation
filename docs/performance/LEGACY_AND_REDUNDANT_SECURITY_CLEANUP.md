# 旧代码与冗余加密清理清单

状态：`implemented_and_reviewed`

实施起点：第二批本地检查点 `a77d9792`。

本清单确定本轮删除边界。用户已明确不再保留历史 `.ctbackup` 专用加密备份兼容；本轮仍不授权读取或盘点真实数据。清理目标是减少维护负担和无效操作，不提出新的安全设计。

> 2026-08-09 后续决定：本文件第 3.1、4、6 节关于“继续保留日常 PIN、解锁、改密和新加密写入”的内容只记录上一批边界，已由旧加密退出第一阶段替代。当前只保留旧库只读识别和一次性离线转换；`.ctbackup` 仍不恢复。见 `docs/product/class-teacher/LEGACY_ENCRYPTION_RETIREMENT_PHASE1.md` 与 ADR-0010。

## 1. 第二批只实施的一项

### 当前明文班主任模式跳过旧总队列

当前产品固定使用明文模式，但行动、规划、事务、学生支持和对话接口仍经过旧保险箱总锁，导致一个慢请求挡住其他无关请求。

- 当前明文开关：`backend/class_teacher/feature.py:14-19`。
- 总锁与等待范围：`backend/class_teacher/vault_service.py:143,354-358`。
- 路由统一挂载：`backend/class_teacher/api/router.py:113-117,437-443`。

第二批只在明文模式直接放行；旧保护模式仍保留原锁，不借此删除恢复代码。

## 2. 本轮低风险旧代码清理候选

这些文件没有生产入口，删除收益主要是减少误修和重复测试，不是明显提速。删除前仍须再次做引用搜索、路由清单、类型检查和生产构建。

### 2.1 已被新班主任工作面替代的旧前端

- `ActionLedgerPanel.vue`
- `CollectionInboxPanel.vue`
- `PlanningInboxPanel.vue`
- `SopWorkspacePanel.vue`
- `SupportWorkspacePanel.vue`
- 只覆盖这些旧面板的 `b04-interactions.spec.ts`、`b07-summary-refresh.spec.ts`、`b10-action-sync.spec.ts`
- 只由旧面板使用的 `api/actions.ts`、`api/collections.ts`、`api/planning.ts`、`api/sop.ts`

当前实际入口只装载 `ConversationDesk`、`HandoffWorkspace`、`CalendarSurface`、`AffairsSurface` 和 `StudentSurface`，见 `frontend/src/workspaces/class-teacher/views/ClassTeacherWorkbenchView.vue:2-13,74-107`。

不得顺带删除仍被新页面使用的 `api/support.ts`、`api/work.ts`、`api/intake.ts` 和 `api/r1.ts`。

### 2.2 未登记到实际服务的旧班主任后端入口

- `backend/class_teacher/api/card_router.py` 及专用 `card_schemas.py`
- `backend/class_teacher/api/home_intake_router.py` 及专用 `home_intake_schemas.py`
- `backend/class_teacher/api/model_router.py` 及专用 `model_schemas.py`

当前主入口只登记 `backend/class_teacher/api/router.py:429-445` 中的现行路由。删除旧入口时不能按相似名称删除仍被当前对话和交接复用的底层业务模块。

### 2.3 其他确定重复或无生产引用的候选

- `report.py` 中被后一个同名定义覆盖的 `_student_label`（约 `1598-1603`）及经全仓引用搜索确认无调用的旧私有报表方法。
- `question_bank/recommendation/training_plan.py` 的旧生成流程和只由它调用的 `recommendation_engine.py` 旧入口；当前训练路由使用 `PracticePlanService.generate`。保留仍被现实现复用的常量和分配方法。
- 只被旧测试引用的知识图谱旧组件 `GraphNodeInspector.vue`、`GraphTextDirectory.vue`、`KnowledgeGraphCanvas.vue`，以及零引用的 `GraphRelationReviewShortcut.vue`。
- 零引用的 `ReviewSelectionSummary.vue`、`TeachingPrepStageRuler.vue`；只被自身旧测试引用的 `LessonPreparationWorkspace.vue`。

这些项目必须在实际清理分支重新运行引用搜索；动态入口、历史兼容承诺或当前测试证据存在时不得删除。

## 3. 兼容流程清理边界

### 3.1 本轮删除历史 `.ctbackup` 专用加密备份

- 前端删除 `.ctbackup` 创建、预览和恢复客户端、请求格式及只覆盖这条流程的测试。
- 后端删除 `.ctbackup` 创建、下载、预览和恢复路由、请求格式、服务方法及只覆盖这条流程的测试。
- 当前页面本来不展示专用备份入口，因此不新增替代页面或迁移向导。

上一批当时的边界是：旧 PIN、解锁、改密和旧加密数据库识别不因删除 `.ctbackup` 自动删除。特别是发现旧加密数据库后停止明文混写的判断必须保留；剩余 PIN/密码代码只有在删除备份后再次证明完全无现行或旧库边界用途时，才进入后续清理。本轮不读取或盘点真实备份。该保留决定现已由第 8 节替代。

### 3.2 学生删除夹带旧专用备份处理

`backend/class_teacher/support_record_service.py:1231-1495` 仍扫描 `.ctbackup`、要求第二段销毁确认并维护中断恢复文件。当前新学生页面没有这个删除入口，默认明文模式又已进入普通备份。

本轮移除专用备份数量、文件清单、仅为 `.ctbackup` 设置的第二确认语句和暂存恢复处理；学生删除本身的影响预览、正式确认和业务删除结果必须保留。

### 3.3 固定浏览器请求标记

后端 `backend/class_teacher/api/router.py:35,64-89` 与多个前端请求文件重复携带固定 `x-class-teacher-client` 文本。它不能证明请求来源，只有维护负担，性能收益很小。

删除会改变现有请求格式，因此放入接口兼容清理，不混入性能批次。

### 3.4 普通备份/导出的旧短时确认流程

普通备份和导出目前也经过“预检、短时令牌、输入确认短语、再提交”。后续可单独评估只对会覆盖数据的恢复、导入和迁移保留确认；普通备份/导出直接提交。该调整会改变操作步骤，必须另行冻结页面流程，不在第二批实施。

## 4. 必须保留，不能按名字删除

- 当前 `EncryptedDatabase` / `VaultService` 虽沿用旧名称，但仍承载明文工作库的真实读写和迁移准备，见 `backend/class_teacher/vault_service.py:147-165,1026-1048` 与 `feature.py:25-38`。
- 旧加密库识别和停止混写必须保留，直到旧库兼容策略正式结束；否则可能把旧加密内容当明文写入。
- 普通备份、恢复、导入完整性和版本检查必须保留，见 `backend/ops/database_validation.py:60-92`、`backend/ops/offline.py:148-175`、`data_transfer_service.py:201-208`。
- 只要旧专用备份恢复仍受支持，其版本检查就必须保留。
- 外部模型服务的 HTTPS 是服务商正常连接方式，不是项目额外套上的重复加密；本机模型仍可使用现有 HTTP 地址。本轮不新增也不删除传输设计。
- `/files`、`/model-profiles` 等低成本旧地址跳转仍有导航测试，不作为死代码删除。

## 5. 本轮清理验收

- 每个删除候选先用路由、manifest、全仓引用和生产构建四项证据确认没有入口。
- 旧测试若记录仍有效的业务规则，先把规则迁移到当前页面或后端测试，再删除旧测试。
- 删除后实际路由和前端客户端不再提供 `.ctbackup` 创建、下载、预览或恢复；普通备份、恢复和导入流程保持不变。
- 学生删除仍保留业务影响预览和一次正式确认，不再扫描、暂存或恢复 `.ctbackup` 文件。
- 清理测试只能使用合成临时数据库和合成备份；真实 `user_data`、旧库和真实备份的任何读取或迁移仍需逐次授权。
- 清理前后实际 OpenAPI 路由、当前 R7 页面、普通备份恢复、版本冲突和明文工作库读写保持正确。
- 不把删除旧安全步骤替换成新的加密、传输或密码设计。

## 6. 实施结果

- 前端删除 21 个无生产入口文件，包含 5 个旧班主任面板、3 份旧面板测试、4 个只服务旧面板的请求文件、4 个旧知识图谱组件及其测试，以及 3 个零引用或自循环引用的备课/复核文件；当前班主任、知识图谱和备课入口保持不变。
- 后端删除 3 组从未登记到现行服务的班主任旧路由及其专用数据格式文件；现行 `support`、`intake` 和 `work` 路由继续登记。
- 删除旧训练生成孤岛、7 个无调用报表方法、被后续同名实现覆盖的方法，以及只被这些旧代码使用的内部辅助；现行训练方案仍使用的比例、题量分配、训练阶段、难度梯度和文字相似度保留。
- 删除 `.ctbackup` 的创建、列表、校验、恢复预览和恢复确认入口，以及对应前端客户端、服务方法、数据格式和专用测试。
- 学生删除仍保留影响预览、预览版本检查、一次正式确认、独立副本执行、失败回退和进程中断恢复；只删除专用备份清单、第二次销毁确认及 `.ctbackup` 暂存处理。
- 上一批当时没有修改普通备份、普通恢复、完整性和版本检查，并继续保留旧加密数据库识别、停止明文混写、PIN、旧库解锁和改密；日常加密操作现已由第 8 节退役。
- 已存在的历史 `.ctbackup` 不会被打开，也不会被自动删除。发现旧学生删除事务内含可能唯一的专用备份副本时，程序停止并保留文件，等待人工处理。

## 7. 集中验证结果

- 前端完整测试：104 个文件、1020 项全部通过；完整代码规范检查、类型检查、生产构建和资源体积限制通过。
- 后端合并核心回归：60 项全部通过，覆盖班主任删除、专用备份路由消失、旧库识别、明文模式、现行训练和应用入口。
- `.ctbackup` 与学生删除专项检查共 49 项通过；后端不可达代码清理的训练、班主任、报表和编译检查共 98 项通过。两组与合并回归存在重叠，不合并计算总数。
- 报表扩大检查 25 项通过，另有 7 项因测试临时文件不在当前受控目录而失败；同类代表用例已在第二批检查点复现。本轮只删除全仓无调用的方法，没有改变报表文件路径规则。
- Python 编译检查、前后端残留引用搜索和 `git diff --check` 通过。
- 未读取、移动、迁移或删除真实 `user_data`，未调用真实模型，未启动或重启正式服务，未推送或合并。
- 需求符合性复审和代码质量复审均未发现 Critical 或 Important 问题。旧类名仍不够贴合当前明文模式，但它们还承担旧库识别职责，本轮不为改名扩大变更。

## 8. 2026-08-09 旧加密第一阶段退出

- 正常班主任运行固定为明文，不再提供 PIN 初始化、密码或 PIN 解锁、恢复后改密、锁定、续时、改 PIN、改密码和恢复密钥确认。
- 正常生产代码不再创建新加密库或写入新密文；现行记录仓库只读写当前 `plaintext-json-v1` 格式。
- 前端删除旧保险箱客户端、未使用状态、空 session 参数和 `x-class-teacher-session`；保留现有 `x-class-teacher-client` 浏览器标记。
- 旧加密库仍在正常运行中被只读识别并阻断班主任读写，避免补表、事务恢复和明密混写；这一识别不是旧保险箱恢复运行。
- 旧密码、旧恢复密钥或旧 PIN 只允许交给一次性离线工具。工具只读源库并发布到尚不存在的工作区外新路径，不注册页面、FastAPI、Job 或工作台模块，也不自动替换正式数据库。
- 普通 ZIP 备份、普通恢复、班主任四工作面和业务数据语义保持不变；`.ctbackup` 创建、打开、校验和恢复继续不受支持。
- 第二阶段只有在另行确认所有所需旧库都已转换后，才可删除离线转换器、旧解密实现和旧格式说明。
