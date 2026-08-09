# 旧代码与冗余加密清理清单

状态：`frozen_inventory_only`

本清单只确定删除边界，不授权读取真实数据，也不在第二批性能实现中批量删除。清理目标是减少维护负担和无效操作，不提出新的安全设计。

## 1. 第二批只实施的一项

### 当前明文班主任模式跳过旧总队列

当前产品固定使用明文模式，但行动、规划、事务、学生支持和对话接口仍经过旧保险箱总锁，导致一个慢请求挡住其他无关请求。

- 当前明文开关：`backend/class_teacher/feature.py:14-19`。
- 总锁与等待范围：`backend/class_teacher/vault_service.py:143,354-358`。
- 路由统一挂载：`backend/class_teacher/api/router.py:113-117,437-443`。

第二批只在明文模式直接放行；旧保护模式仍保留原锁，不借此删除恢复代码。

## 2. 后续低风险旧代码清理候选

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

## 3. 明显冗余，但必须单独做兼容清理

### 3.1 旧 PIN、密码、解锁、自动锁定和专用加密备份

- 前端旧客户端集中在 `frontend/src/workspaces/class-teacher/api/vault.ts:207-375`；当前页面不查询保险箱状态。
- 后端旧 PIN、解锁、恢复和密码入口仍登记在 `backend/class_teacher/api/router.py:119-333`。
- 专用 `.ctbackup` 备份与恢复入口仍登记在 `backend/class_teacher/api/router.py:335-427`。
- `backend/class_teacher/vault_service.py:569-676` 的旧初始化仍可能创建加密元数据；不能零散删除一半后留下无法读写状态。

后续兼容清理必须一次处理入口、请求格式、PIN 组件、旧用户绑定、专用备份、恢复密钥和相应测试。是否继续打开历史 `.ctbackup` 必须先获得单独授权并确认兼容需求；本清单不读取或盘点真实备份。

### 3.2 学生删除夹带旧专用备份处理

`backend/class_teacher/support_record_service.py:1231-1495` 仍扫描 `.ctbackup`、要求第二段销毁确认并维护中断恢复文件。当前新学生页面没有这个删除入口，默认明文模式又已进入普通备份。

后续可以移除专用备份数量、文件清单、第二确认语句和 `.ctbackup` 暂存恢复处理；学生删除本身的影响预览、正式确认和业务删除结果必须保留。

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

## 5. 后续清理验收

- 每个删除候选先用路由、manifest、全仓引用和生产构建四项证据确认没有入口。
- 旧测试若记录仍有效的业务规则，先把规则迁移到当前页面或后端测试，再删除旧测试。
- 兼容清理必须使用合成临时数据库和合成备份；真实 `user_data`、旧库和真实备份的任何读取或迁移均需逐次授权。
- 清理前后实际 OpenAPI 路由、当前 R7 页面、普通备份恢复、版本冲突和明文工作库读写保持正确。
- 不把删除旧安全步骤替换成新的加密、传输或密码设计。
