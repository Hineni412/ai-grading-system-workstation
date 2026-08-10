# B-UI-R1 班主任工作台七页重编排实施设计

> 历史实施设计：本文的七页、PIN、保险箱、专用备份和解锁会话描述已被 B-UI-R7 与旧加密第二阶段替代，只保留为当时的设计和验收证据。当前仍只有首页、日历、事务、学生四个工作面；数据使用明文并进入普通备份，不支持的格式只停止班主任读写。见 [B-UI-R7](./B_UI_R7_IMPLEMENTATION_PLAN.md) 与 [第二阶段冻结清单](./LEGACY_ENCRYPTION_RETIREMENT_PHASE2.md)。

> 状态：completed_local_integration；2026-08-02 调试覆盖已通过限定最终复审并合入本地集成预览
>
> 冻结日期：2026-08-01
>
> 工作分支：codex/class-teacher-iteration
>
> 调查基线：2249346d32a64badee92dd5128acfdd178c7e7e1
>
> 风险等级：高
>
> 本文职责：作为后续新窗口实施 B-UI-R1 的权威入口。概念图负责表达视觉方向；当概念图、旧组件或旧计划与本文冲突时，以本文的业务、安全和数据契约为准。

## 0. 新窗口先读什么

按以下顺序阅读，不要只看概念图开始拼页面：

1. 根目录 AGENTS.md；
2. docs/product/class-teacher/ITERATION_SCOPE.md；
3. 本文；
4. docs/product/CLASS_TEACHER_WORKBENCH.md；
5. docs/product/UI_OPTIMIZATION_UNIFIED_ITERATION.md 第 9 节；
6. docs/product/class-teacher/B00_DATA_GOVERNANCE.md；
7. docs/security/class-teacher/B00_SECURITY_AND_THREAT_MODEL.md；
8. docs/ui/STYLE.md；
9. docs/superpowers/packages/EXECUTION_INDEX.md。

视觉参考位于 artifacts/class-teacher-ui-concepts/。其中的图片是目标布局示意，不是已实现事实，也不是数据契约。第 6 页的调研依据位于 artifacts/class-teacher-ui-concepts/research/student-academic-analytics-patterns.md。

## 1. 本轮结论

B-UI-R1 不是把七张图直接替换成七个大 Vue 组件，而是先解决当前实现中的三个根因：

1. 普通 WorkGraph 与旧加密 ActionLedger 同时存在，形成两套任务真值；
2. 学生目录、支持记录、AI 复核、学业证据和安全操作的读取边界过宽，页面需要自己拼装多个浅接口；
3. 当前 /class-teacher 是一张纵向长页，普通与敏感能力虽已部分实现，却没有形成四个稳定工作面和七张可完成任务的页面。

冻结后的产品结构为：

- 左侧仍只有一个“班主任工作台”入口；
- 内部只有四个一级工作面：今日、日历与工作图、事务、学生；
- 四个工作面组织七张页面模板；
- 今日和日历属于普通工作区，无需解锁；
- 事务、学生名单、学生支持、学业证据和高级安全属于敏感区，锁定时不得挂载、请求或缓存；
- 教师只看到一套全局待办：WorkGraph；
- 旧 ActionLedger 只作为敏感流程内部 Implementation，不再提供第二套“今日/本周/全部”页面；
- 敏感流程只向普通 WorkGraph 投影匿名、只读、可恢复的一条待办；
- 页面 4 和页面 6 不提供名单或成绩导入入口；
- AI 每轮必须先预览、再明确确认，本轮最多一次物理请求；AI 结果仍须教师最终确认。

## 2. 冻结任务边界

### 2.1 目标

- 把当前长页重编排为四个内部工作面和七张核心页面；
- 让普通工作、SOP、学生支持、学业证据和关注决定形成一条可追溯闭环；
- 消除两套可见待办，统一教师的“今天要做什么”；
- 把学生目录改为轻量卡片目录，点击学生后才读取单个学生正文；
- 把“保存记录”和“调用 AI”拆成用户能理解的明确步骤；
- 用可比性先行的图表替代第 6 页的大表格；
- 把 PIN、专用备份、整库恢复和完整删除编排成独立安全页；
- 保持现有加密、普通备份排除、逐次模型确认、零自动外发和零自动结案边界。

### 2.2 包含

- frontend/src/workspaces/class-teacher/** 内的壳层、页面、状态模块、传输客户端和测试；
- backend/class_teacher/** 内直接服务本轮页面的深 Module、公开 Schema、路由和兼容读取；
- migrations/class_teacher_work/** 与 migrations/student_affairs/** 的增量迁移定义；
- tests/class_teacher/** 的接口、迁移、安全、并发和恢复测试；
- 一个外部入口、四个内部工作面和七张页面的导航与交互；
- 普通工作详情、状态历史、收集汇总和待确认 AI 分支；
- SOP 事务列表、当前步骤、持久草稿、沟通草稿、结案和重开；
- 轻量学生目录和按学生延迟读取；
- 绑定支持记录修订的 AI 复核会话；
- 考试场次、多指标、可比性和图表 read model；
- 服务端权威倒计时、正常修改 PIN、专用备份、恢复预览和学生完整删除；
- 全部合成数据验收和最终集中双复审。

### 2.3 明确不包含

- 不在页面 4、5、6 增加名单导入、成绩导入、批量粘贴或文件上传；
- 不在本轮接通“系统其他名单入口”到班主任加密名单，也不静默复制阅卷学生身份；
- 不保留或展示成绩文件原始行、原始文件、完整路径、未匹配学生内容或临时上传文件；
- 不自动计算总分；总分只能来自明确来源或教师确认的计算快照；
- 不平均、相加不同学科排名，不生成综合风险分或全班风险榜；
- 不把旧 student_card_entries 猜测迁成事实、观察或正式支持记录；
- 不做 AI 自动调用、自动重试、自动回答追问、自动入档、自动建行动、自动外发或自动结案；
- 不做选择性恢复、合并恢复、云备份或普通明文导出；
- 不新增教师可见的审计日志页；现有审计继续只写无正文元数据；
- 不读取、写入、复制、迁移或删除真实 user_data；
- 不调用真实模型、不使用真实密钥、不产生费用；
- 不启动会自动迁移真实数据库的正式应用；
- 不 push、不创建 PR、不合并或同步 main。

### 2.4 验收条件

完整候选必须同时满足：

1. /class-teacher 仍是唯一外部入口，四个内部工作面可前进、后退和刷新；
2. 锁定时事务、学生、备份、恢复和删除模块的请求数为 0；
3. 今日和日历读取同一 WorkGraph，旧 ActionLedger 没有第二个全局列表入口；
4. 普通节点可更新；匿名敏感投影只允许“打开处理”，不得在普通区直接完成或改期；
5. 一个敏感事务轮次、一次关注跟进或一次最终学生支持结果，各自最多投影一条匿名待办；
6. 页面 4 只加载目录元数据，不解密或返回全班学生正文；
7. 页面 5 的“仅保存到本机”物理模型请求数为 0；追问后的补充必须形成新预览并再次确认；
8. 页面 6 的所有图表结论由后端同一规则版本产生，缺考不等于 0 分，不可比点不连线；
9. 页面 7 的恢复明确为完整替换；恢复成功立即锁定，失败保持当前库；
10. 受保护正文不进入普通库、URL、浏览器持久存储、普通日志、普通备份或错误响应；
11. B 线受影响回归、完整 B 测试、前端测试、类型检查、lint、生产构建和合成浏览器流程通过；
12. 同一冻结候选完成一次需求符合性复审和一次代码质量复审，当前范围内没有 Critical 或 Important 阻塞。

### 2.5 风险判断

文档修改本身为低风险；后续实现为高风险。它会改变受保护页面挂载、两库投影、支持记录与模型结果关联、学业证据结构、PIN 保护和整库恢复。失败可能造成敏感内容暴露、状态漂移、重复模型费用、错误学业结论或恢复覆盖，因此必须按本文的故障场景和最终集中复审执行。

## 3. 当前实现基线

### 3.1 当前可见入口

- frontend/src/workspaces/class-teacher/manifest.ts 只注册 /class-teacher；
- ClassTeacherWorkbenchView.vue 当前只正式挂载 UnifiedWorkBoard 和 SensitiveStudentWorkspace；
- 页面是纵向长页，没有“今日 / 日历与工作图 / 事务 / 学生”四个内部工作面；
- SopWorkspacePanel、SupportWorkspacePanel、ActionLedgerPanel 已有代码和测试，但没有生产入口。

### 3.2 当前两套行动

普通区：

- 数据库：class_teacher_work.db；
- 当前迁移：000—002；
- 正式页面：UnifiedWorkBoard；
- 真值：WorkGraph 的 work_nodes / work_edges。

敏感区：

- 数据库：student_affairs.db；
- 当前迁移：000—019；
- SOP、关注和支持计划仍使用加密 ActionLedger；
- 旧 ActionLedgerPanel 有“今天/本周/时间线/全部”，但未挂载。

直接把 ActionLedgerPanel 重新挂回页面会重新出现第二套待办，因此禁止作为本轮方案。

### 3.3 当前学生目录问题

StudentCardService.list_cards 会遍历全部学生，并读取每名学生的卡片正文、当前摘要和支持计划。目录页如果继续调用该接口，即使只画姓名卡，也已经把全班正文读取到了前端。

本轮必须建立 StudentDirectory Module：目录只返回解锁后的身份索引、计数、最后确认时间和匿名投影状态；点击一名学生后再读取该学生的记录、AI 结果、计划或证据。

### 3.4 当前 AI 复核问题

现有敏感模型预览只绑定 source_text 和可选 subject_id。它已经具备：

- 实际发送内容预览；
- 本地匿名化和禁止字段阻断；
- 每份预览最多一次物理请求；
- AI 追问；
- 教师自然语言补充；
- result_unknown 后查询同一 operation；
- 教师确认后写 student_card_entries。

缺口是它没有绑定“哪一条支持记录的哪一个修订”，因此页面无法可靠表达“与 AI 讨论这条已保存记录”。

### 3.5 当前学业和安全问题

- 后端已有单条证据、成绩状态、排名语境、基础可比性、至少三次证据才称趋势、关注卡和教师决定；
- 前端没有 compare/trend 方法，也没有解码 rank_context；
- 当前数据没有可靠的“同一场考试”父对象，无法稳定组织总分和多学科；
- 原始文件按设计不保留，概念图中的“查看原始行”不能实现为真实原始行；
- 备份、验证、恢复预览和恢复后端已存在，但没有正式页面；
- 当前恢复预览没有当前库/备份库分项数量，也没有“预览后当前库又变化”的失效检查；
- 当前 v2 PIN 没有正常修改 PIN 接口；
- 当前只有固定 5 分钟规则，没有服务端剩余秒数字段；
- 审计只有内部写入，没有读取页面。

## 4. 信息架构与安全导航

### 4.1 一个外部入口、四个内部工作面

manifest 和全局路由保持不变。内部状态使用白名单查询参数：

~~~text
/class-teacher?surface=today
/class-teacher?surface=calendar&range=week&week=2026-08-03
/class-teacher?surface=affairs
/class-teacher?surface=students&panel=directory
/class-teacher?surface=students&panel=support
/class-teacher?surface=students&panel=academic
/class-teacher?surface=students&panel=security
~~~

允许进入 URL 的值只有：

- surface：today、calendar、affairs、students；
- panel：directory、support、academic、security；
- range：today、week、timeline、all；
- week：普通 ISO 日期。

以下内容只存在当前内存，不能进入 URL、localStorage 或 sessionStorage：

- 学生、事务、证据、备份的内部 ID；
- 学生搜索词、班级筛选、排序和页码；
- PIN、旧密码、恢复密钥和备份密码；
- 恢复预览 token、删除确认短语；
- 学生记录草稿、AI 匿名正文、追问和模型草稿；
- 敏感模型 operation ID；
- 当前学生、当前事务和当前证据选择。

非法参数使用 router.replace 规范到 today。教师切换工作面使用 router.push，浏览器前进和后退有效。

### 4.2 敏感域

事务和学生属于同一个敏感域：

- 从事务切换到学生，或在学生四个子页面之间切换，可以维持本次解锁；
- 从敏感域切换到今日或日历时，先同步卸载敏感子树，再尽力调用后端 lock；
- 刷新事务或学生页面后，前端 token 不恢复，只显示解锁门；
- 刷新 support 或 academic 后没有内存中的当前学生，解锁后回到 directory，并提示重新选择学生；
- security 不要求预先选择学生，解锁后可直接进入；
- 普通区点击匿名投影时，只把 projection_id 暂存在内存；解锁后由后端解析真实目标。

### 4.3 锁定顺序

VaultSession Module 的 lock Implementation 必须按顺序执行：

1. 立即清空前端 token；
2. 增加 sessionEpoch，同步卸载全部敏感 DOM；
3. 清除当前学生、事务、证据、筛选、正文草稿、AI 预览、模型结果、恢复 token、密码和确认短语；
4. 取消能取消的请求；
5. 所有异步结果提交前检查 sessionEpoch，旧响应不得回填新会话；
6. 尽力通知后端锁定；即使网络失败也不能恢复敏感 DOM；
7. 焦点回到解锁标题或 PIN 输入框。

锁定时不是“加遮罩”，而是敏感 Module 不构造、不请求、不缓存。

## 5. 深 Module、Interface 与 Seam

本节使用统一术语：

- Module 是隐藏内部复杂性的业务单元；
- Interface 是调用方唯一需要学习的少量行为；
- Implementation 是加密、规则、事务和恢复等内部细节；
- Seam 是两个 Module 之间需要稳定契约的连接处；
- Adapter 只在确实存在两个实现时使用；
- Depth 表示小 Interface 隐藏了多少复杂性；
- Leverage 表示一处实现能服务多少页面和流程；
- Locality 表示同一规则集中在同一位置，而不是散落到页面。

### 5.1 UnifiedWork Module

Interface：

~~~text
read(WorkQuery) -> WorkBoardSnapshot
act(WorkCommand) -> WorkCommandResult
~~~

WorkCommand 使用带类型的命令联合：

- update_status；
- reschedule；
- record_progress；
- update_collection_summary；
- open_restricted_projection。

Implementation 隐藏：

- 普通任务与匿名敏感投影合并；
- today/week/timeline/all 查询；
- 工作关系、排序、过期和等待判断；
- 普通文本敏感内容阻断；
- operation ID 幂等；
- expected_revision 冲突；
- 普通节点可写、匿名投影只读。

Depth 来自“一次读取、一次命令”隐藏了普通库、敏感投影、日期和版本处理。Leverage 来自今日和日历共同使用同一 Interface。Locality 要求页面不得重新计算任务真值。

### 5.2 EncryptedActionLedger Module

它继续存在，但只作为 SOP、关注和学生支持内部 Implementation。

内部 Interface：

~~~text
read_group(SourceRef) -> SensitiveActionGroupSnapshot
apply(ActionGroupCommand, operation_id, expected_revision) -> SensitiveActionGroupSnapshot
~~~

禁止向新页面提供 list_all、today、week 或 dashboard。旧 ActionLedgerPanel 不再成为生产入口。敏感页面只显示当前事务或当前学生自己的步骤和行动。

### 5.3 SensitiveWorkProjection Module

Interface：

~~~text
drain(token, limit) -> ProjectionDrainReport
resolve(token, projection_id) -> SensitiveTarget | Gone
~~~

Implementation 隐藏：

- 一个敏感聚合只产生一个匿名节点；
- projection_id 到真实来源的加密映射；
- source_revision、指纹、幂等重放和 tombstone；
- 固定安全标题；
- 加密库 outbox 到普通 WorkGraph 的部分失败恢复；
- 删除或来源不存在后的清理。

普通库只允许接收：

- 随机 projection_id；
- 安全类型枚举；
- status；
- due_date；
- source_revision；
- envelope_fingerprint。

普通库禁止接收姓名、班级、成绩、事务正文、学生代号、风险词、真实 source_id 或调用方自定义标题。

新增唯一合理的 Adapter Seam：

~~~text
WorkProjectionSink.apply(ProjectionEnvelope) -> ProjectionReceipt
~~~

- 生产 Adapter：WorkGraphProjectionAdapter；
- 测试 Adapter：RecordingFailingProjectionAdapter，用于模拟第一次失败、第二次成功和 ack 丢失。

这里确实存在生产和失败注入两个实现，因此 Adapter 合理。数据库 Repository、时钟和 UUID 不再人为制造假 Seam，测试使用真实临时库。

### 5.4 AffairWorkflow Module

Interface：

~~~text
list(AffairQuery) -> AffairSummaryPage
read(affair_id) -> AffairView
advance(AffairCommand, operation_id, expected_revision) -> AffairChange
~~~

AffairCommand 包括保存步骤草稿、完成/免除步骤、记录教师决定、保存未发送沟通草稿、结案和重开。

Implementation 隐藏：

- 模板版本冻结；
- 并行步骤和依赖；
- safety_required 不可免除；
- 学校配置缺口；
- ActionLedger 同步；
- 一个事务轮次一个匿名投影；
- 持久草稿和过期清理；
- 结案、重开和新发生轮次。

### 5.5 StudentDirectory Module

Interface：

~~~text
search(DirectoryQuery) -> StudentDirectoryPage
open(subject_id) -> StudentWorkspaceHeader
~~~

Implementation 只解密学生身份索引，并通过关系表计算计数；不解密支持记录、学生卡、AI 草稿、计划或学业正文。班级规模首版按本机单班处理，服务端可在内存中过滤解密后的身份索引，但只向前端返回当前页。

### 5.6 SupportRecordAIReview Module

Interface：

~~~text
prepare(record_id, expected_revision, teacher_supplement) -> ExactPreview
confirm(review_id, preview_id, fingerprint, operation_id) -> ReviewState
read(review_id | operation_id) -> ReviewState
apply(review_id, model_operation_id, expected_revision, teacher_result, operation_id)
    -> StudentCardEntry
reject(review_id, operation_id) -> ReviewState
~~~

Implementation 隐藏：

- 读取绑定的支持记录修订；
- 每轮最小匿名预览；
- 每份预览最多一次物理请求；
- 追问、教师补充和下一轮预览；
- result_unknown 同操作查询；
- AI 结构校验和禁止内容阻断；
- 教师编辑后的最终 student_card_entry；
- source_record_id、source_revision、review_id 和 model_operation_id 关联；
- 最终一个匿名跟进投影。

本轮保留 student_card_entries 作为“教师确认的结构化学生卡”，但把它绑定到明确的支持记录修订。它不自动覆盖原始 SupportRecord，也不自动改变 record_kind。教师原记录继续保留；AI 结果只是有来源的派生卡片。

模型继续复用现有 ApprovedModelGateway Seam：

- 生产为关闭或经另行授权的 Gateway Adapter；
- 合成测试使用 FakeApprovedModelGateway Adapter。

不再叠加第二层模型 wrapper。

### 5.7 StudentAcademicAnalysis Module

Interface：

~~~text
confirm_session_snapshot(batch, operation_id) -> SessionReceipt
get_student_analysis(subject_id, filters, as_of) -> AcademicAnalysis
get_evidence_detail(evidence_version_id) -> ConfirmedEvidenceSnapshot
~~~

页面 6 只使用后两项；第一项由系统其他受控证据入口调用。

Implementation 隐藏：

- 考试场次、总分和多学科指标；
- 0 分、缺考、免考、缺失、未完成和补测；
- 分数与排名分别判断可比性；
- 相对位置、连续趋势和证据不足；
- 图表 read model；
- 规则版本和 source_version；
- 证据修订后关注卡失效。

所有可比性和摘要规则都留在后端 Module，前端只渲染，不成为第二套算法。

### 5.8 Vault Modules

拆成三个小 Interface：

~~~text
VaultLease.status / unlock / lock / touch
VaultRecovery.list / create / preview / confirm
SubjectErasure.preview / confirm
~~~

VaultLease 隐藏会话、剩余时间和同步卸载；VaultRecovery 隐藏备份验证、候选迁移、当前库变化校验和原子替换；SubjectErasure 隐藏影响核对、双确认、派生数据清理和专用备份保守处理。

## 6. 唯一待办与匿名投影规则

### 6.1 真值归属

| 内容 | 正式真值 | 普通区表现 |
|---|---|---|
| 普通班务 | WorkGraph | 可查看、完成、等待、改期 |
| SOP 事务步骤 | 加密 ActionLedger + AffairWorkflow | 每个事务轮次最多一条只读匿名投影 |
| 学业关注跟进 | AttentionWorkflow + 加密 ActionLedger | 教师决定跟进/观察后最多一条只读匿名投影 |
| AI 学生支持结果 | student_card_entry + 敏感行动组 | 教师最终确认后最多一条只读匿名投影 |
| AI 预览、追问、未确认草稿 | 临时敏感工作区 | 不投影 |
| 仅保存的支持记录 | SupportRecord | 不投影 |
| 无需处理的关注决定 | AttentionWorkflow | 不建行动、不投影 |

### 6.2 投影聚合

- SOP group key：affair_id + occurrence_id；
- Attention group key：attention_card_id；
- Student support group key：student_card_entry_id；
- stable projection_id 首次随机生成，并只在加密库保存真实映射；
- due_date：SOP 取当前行动组最早明确复查日；关注取教师 review_at；学生支持取教师最终确认的 review_date；
- 无日期显示“未排期”，不能猜；
- 状态聚合集中在 SensitiveWorkProjection Implementation：
  - 有 in_progress 时为 in_progress；
  - 否则有 pending 时为 pending；
  - 全部等待时为 waiting；
  - 全部终态或事务结案时为 completed/cancelled。

普通标题由安全枚举固定生成：

- sensitive_affair：敏感事务待处理；
- attention_followup：学生事项待复查；
- student_support：学生支持待跟进。

### 6.3 版本、幂等与删除

- 所有命令携带 operation_id；超时重试必须复用；
- 同 operation_id + 同指纹返回原结果；同 operation_id + 不同内容返回冲突；
- 敏感聚合使用独立 group_revision，不复用普通节点 revision；
- ProjectionSink 以 projection_id + source_revision 幂等；
- 小 revision 不覆盖大 revision；
- 相同 revision 指纹不同必须冲突；
- restricted_projection 不接受普通 WorkGraph 更新；
- reopen 创建新 occurrence 和新投影，旧完成投影不复活；
- 删除、关闭和无需处理使用显式 tombstone；
- 完整学生删除必须先成功写 tombstone/outbox，再删除映射；不能留下永远打不开的普通节点。

### 6.4 部分失败

- 敏感来源、行动组和加密 outbox 在同一 student_affairs 事务提交；
- 敏感来源成功、普通 WorkGraph 写失败：敏感操作仍成功，显示“已保存，普通工作区同步待恢复”；
- 普通投影已写、outbox ack 失败：下次幂等重放，不产生第二节点；
- 敏感事务失败：无 outbox、无普通投影；
- drain 触发点：解锁成功后、敏感写入后、读取该敏感来源时；
- 普通工作板在锁定状态不得反向打开加密库 drain；
- resolve 找不到来源时返回 Gone，并补发 tombstone；
- 投影重试与模型请求完全分离，绝不追加模型调用或费用。

## 7. 数据迁移设计

全部迁移只做加法并保留旧读。真实数据库执行仍需另行授权。

### 7.1 普通库

当前尾部为 migrations/class_teacher_work/002_protection_and_model_approval.sql。

#### 003_work_views_and_projection_guards.sql

建议包含：

- work_node_events：状态、日期、进展和收集汇总历史；
- work_collection_snapshots：expected_count、received_count、needs_review_count、revision；
- work_nodes 增加 projection_type、projection_fingerprint；
- WorkGraph 投影 upsert/tombstone 的幂等收据；
- restricted_projection 的数据库与 Module 双重只读保护。

普通进展正文写入前继续执行普通敏感内容策略。收集汇总只保存数量，不保存具体学生名单。

#### 004_pin_change_receipts.sql

建议包含：

- protection_change_operations；
- change_kind 固定 pin_change；
- request_fingerprint 使用内部高熵 secret 做 HMAC，禁止保存可离线枚举的 PIN hash；
- staged、activated、completed 状态；
- sensitive_protection_pending 增加可选 operation_id。

### 7.2 敏感库

当前尾部为 migrations/student_affairs/019_student_card_entries.sql。

#### 020_sensitive_work_projection_groups.sql

建议包含：

- sensitive_work_groups；
- sensitive_work_projection_outbox；
- group_revision、projection_id、source_kind、source_id、occurrence_id、state、due_date；
- upsert/tombstone envelope；
- sop_step_drafts；
- 草稿 payload 继续存 encrypted_objects；
- 草稿默认 7 天无修改后过期；
- 兼容读取并逐步替代 student_card_projection_outbox。

不得猜测迁移旧 ActionLedger 的所有行动。只在对应 SOP、关注或学生卡下次被读取/修改时创建稳定 group。

#### 021_support_ai_review_bindings.sql

建议包含：

- support_ai_review_sessions；
- support_ai_review_turns；
- state：drafting、preview_ready、awaiting_teacher、proposal_ready、applied、rejected、expired、invalidated、result_unknown；
- record_id、base_revision_number、subject_id；
- preview_id、model_operation_id 作为跨库不透明引用，不建立跨库外键；
- student_card_entries 增加 source_record_id、source_record_revision、review_id、state；
- 同一 record revision 最多一个 active 教师确认结构卡；
- 旧 student_card_entries 保持 legacy 兼容，不猜测绑定来源。

#### 022_assessment_sessions_v2.sql

新增：

- assessment_sessions；
- assessment_session_members；
- attention_card_evidence_links；
- 每个 session 的加密 payload 保存标题、学年、学期、年级、考试类型、比较系列、来源引用、教师确认时间和 raw_file_retained=false；
- member 把现有 assessment 绑定为 subject_score 或 total_score；
- measure_key 使用 VMK HMAC 指纹作为索引；
- rank_context 加密 payload 增加 rank_origin、cohort_key、ranking_rule_version；
- evidence payload 固化 session_id、measure_role、measure_key、场次元数据、来源版本和教师确认时间。

旧 assessment 不自动按标题、日期或 import_id 合并。没有 member 的旧证据投影为一对一 legacy 虚拟场次，并明确 metadata_complete=false。

### 7.3 原始文件与证据快照

正式学业证据只保存：

- Adapter 类型；
- 教师命名的来源标签；
- 不可逆来源指纹；
- 教师确认的字段映射摘要；
- 场次元数据；
- 匹配后的 subject_id；
- 规范化 measure、value、result_state 和 rank_context；
- 确认时间与版本。

明确不保存：

- 原始文件字节；
- 完整文件路径；
- 工作表原始行；
- 未匹配学生内容；
- 临时文件；
- 剪贴板原文。

因此页面用语统一为“查看教师确认的证据快照”，不能写“查看原始行”。

## 8. 公开 API 目标契约

现有 API 在兼容期继续可读；新页面只调用下列深 Interface 对应的公开合同。

### 8.1 普通工作

~~~text
GET  /api/class-teacher/work?view=&anchor=&cursor=
GET  /api/class-teacher/work/nodes/{node_id}
POST /api/class-teacher/work/nodes/{node_id}/commands
POST /api/class-teacher/work/nodes/{node_id}/progress/previews
~~~

WorkBoardSnapshot 返回：

- summary：today、overdue、waiting、review_due；
- nodes、edges；
- selected range；
- cursor；
- source_version。

WorkNodeDetail 返回：

- 当前节点；
- 目标和上下游关系；
- progress_events；
- 可选 collection_summary；
- allowed_commands；
- restricted_projection 时只给 open_restricted_projection。

### 8.2 敏感投影解析

~~~text
GET /api/class-teacher/protected-work/{projection_id}
~~~

必须解锁。只返回前端安全路由目标，不返回普通区可持久化的真实 ID。前端立即以内存选择打开 affair、support 或 academic。

### 8.3 SOP

~~~text
GET  /api/class-teacher/sop/affairs?state=&template=&cursor=
GET  /api/class-teacher/sop/affairs/{affair_id}
PUT  /api/class-teacher/sop/affairs/{affair_id}/steps/{step_id}/draft
POST /api/class-teacher/sop/affairs/{affair_id}/commands
~~~

list 返回轻量摘要；detail 才返回当前、完成和后续步骤。command 使用判别类型承载 complete_step、teacher_decision、close、reopen。

### 8.4 学生目录

~~~text
GET /api/class-teacher/support/directory?q=&class_label=&state=&sort=&cursor=&page_size=
GET /api/class-teacher/support/subjects/{subject_id}/workspace-header
~~~

DirectoryItem 只允许：

- subject_id；
- source_student_id；
- display_name；
- class_label；
- confirmed_entry_count；
- support_record_count；
- support_plan_count；
- attention_pending_count；
- projection_state；
- last_confirmed_at。

所有响应 no-store。page_size 默认 20、最大 50。

### 8.5 支持记录与 AI 复核

~~~text
POST /api/class-teacher/support/records/{record_id}/ai-reviews/previews
POST /api/class-teacher/support/ai-reviews/{review_id}/previews/{preview_id}/confirm
GET  /api/class-teacher/support/ai-reviews/{review_id}
GET  /api/class-teacher/support/ai-reviews/operations/{operation_id}
POST /api/class-teacher/support/ai-reviews/{review_id}/apply
POST /api/class-teacher/support/ai-reviews/{review_id}/reject
~~~

首轮 preview 绑定 record_id + expected_revision。追问后的 teacher_supplement 创建新 turn 和新 exact preview。apply 必须重新核对：

- operation 已成功；
- response_kind 为 proposal；
- proposal 属于该 review；
- record revision 仍是 base revision；
- teacher_result 已由教师编辑确认；
- 同一结果尚未写入其他 student_card_entry。

不满足时返回 409 support_ai_review_stale 或对应安全错误，不自动合并。

### 8.6 学业分析

~~~text
GET /api/class-teacher/support/subjects/{subject_id}/academic-analysis
GET /api/class-teacher/evidence/{evidence_version_id}/snapshot
POST /api/class-teacher/attention-cards/{attention_card_id}/decide
~~~

AcademicAnalysis 必须返回：

- contract_version；
- source_version；
- ruleset_version；
- sessions；
- series；
- rank_change_pairs；
- relative_subject_signals；
- recent_changes；
- insufficient_reasons；
- attention_cards；
- detail_ref。

每段比较分别返回 score 和 rank 两个 dimension，不用一个字符串同时控制全部图形：

- overall_status；
- dimensions.score/status/reason_codes；
- dimensions.rank/status/reason_codes；
- allowed_outputs；
- basis；
- ruleset_version。

### 8.7 Vault

兼容扩展：

~~~text
GET  /api/class-teacher/vault/status
POST /api/class-teacher/vault/touch
POST /api/class-teacher/vault/pin/change
GET  /api/class-teacher/vault/backups
POST /api/class-teacher/vault/backups
POST /api/class-teacher/vault/restore/preview
POST /api/class-teacher/vault/restore/confirm
GET  /api/class-teacher/support/subjects/{subject_id}/deletion-preview
DELETE /api/class-teacher/support/subjects/{subject_id}
~~~

status 和 touch 新增：

- session_expires_in_seconds；
- status_observed_at；
- lock_reason。

前端只把秒数作为显示，服务端 401 永远是最终权威。

restore preview 新增：

- source_relation，不暴露裸 instance_id；
- backup_schema_version、current_schema_version、migration_required；
- backup_scope_counts、current_scope_counts；
- mode 固定 complete_replace；
- will_replace_current=true；
- will_lock_after_confirm=true；
- confirmation_phrase；
- preview_token 和 expires_in_seconds。

confirm 必须绑定备份 digest、预览时当前库 digest、当前 session 和 instance。预览后当前库有任何写入时返回 409，要求重新预览。前端必须让教师亲手输入确认语，不能由 vault.ts 硬编码。

## 9. 七张页面实施合同

## 9.1 页面 1：今日工作台

目标布局：

- 顶部：一句话快速录入和明确日期；
- 下方 58/42；
- 左侧：今天、逾期、待复查、等待中高密度列表；
- 右侧：当前普通工作的目标、步骤、依赖、最新情况和待确认 AI 分支；
- 底部：紧凑敏感入口，锁定时不挂载敏感 Module。

交互：

1. 输入一句话后生成普通模型 exact preview；
2. 教师确认后最多一次物理请求；
3. AI 结果显示为紫灰虚线草案；
4. 教师第二次确认后才写 WorkGraph；
5. 点列表行只切换右侧检查器；
6. 状态和进展使用 expected_revision；
7. result_unknown 只查询同一 operation。

状态必须区分：

- loading；
- empty；
- ready；
- refreshing；
- read_failed；
- write_failed；
- saved_refresh_failed；
- conflict；
- model_unavailable；
- model_unknown。

禁止：

- 模型失败时生成本地伪 AI 步骤；
- 把未确认分支写入正式图；
- 把敏感投影正文补进右侧检查器。

## 9.2 页面 2：日历与工作图

目标布局：

- 顶部：today/week/timeline/all 和周切换；
- 下方 72/28；
- 左侧：唯一工作关系图；
- 右侧：选中节点详情、日期、进展、收集汇总和命令；
- 约 1000px 以下检查器移到图下方。

交互：

- 点击日期只过滤，不修改下一条新任务日期；
- 点击节点只更新检查器；
- 禁止拖拽改期；
- 日期、状态和最新情况使用明确字段；
- AI 待确认分支以虚线显示，确认前不能执行；
- restricted_projection 只有“解锁并处理”。

图只回答“工作怎样包含、依赖和接续”。统计不要堆成第二个仪表盘。

页面必须区分：

- 本周没有工作；
- 整个工作图为空；
- 筛选后为空；
- 旧快照可见但读取失败；
- revision 冲突；
- 匿名来源已变化或已删除。

## 9.3 页面 3：事务处理台

事务属于敏感域。锁定时不得请求模板、事务或步骤。

目标布局：

- 左 240px：基线模板、进行中、等待、待结案、已结案事务；
- 中间：当前可执行步骤、事实草稿、未发送沟通草稿和教师决定；
- 右 300px：后续步骤、并行分支、学校配置缺口和安全提示。

交互：

- 空状态显示六套基线模板；
- 每次突出当前可执行步骤，但允许并列多个当前步骤；
- 不显示虚假的“第 2/5 步”固定线性进度；
- 暂存必须得到服务端确认，刷新和重新解锁后可恢复；
- safety_required 没有免除按钮；
- 沟通草稿可编辑、版本化，但永远标“尚未发送”；
- AI 建议不能驱动高影响分支；
- 必做步骤未完成时结案按钮禁用并解释原因；
- 只有 closed 事务显示重开；
- 重开必须填写理由并创建新 occurrence。

概念图纠正：

- 锁定时不能显示学生姓名或事务正文；
- 教师决定只在对应 decision step 激活后出现；
- 结案和重开不能同时出现；
- 学校联系人缺失时显示配置缺口，不编造正式流程。

## 9.4 页面 4：受保护的学生名单

目标布局：

- 72/28；
- 左侧顶部只有搜索、班级、档案状态、排序；
- 主区两列长方形圆角学生卡；
- 右侧 sticky 安全栏；
- 紧凑窗口改为上下布局。

每张卡只显示：

- 姓名、学号/内部编号、班级；
- 确认结构卡数量；
- 支持记录数量；
- 支持计划数量；
- 待关注数量；
- 最后确认时间；
- 匿名同步状态。

卡中禁止：

- 支持记录正文；
- AI 摘要正文；
- 成绩；
- 头像；
- 风险颜色；
- 行内编辑、删除、导入或新增。

点击整卡或键盘 Enter/Space 进入该学生支持页。学生选择只保存在内存。

目录 API 只读取身份索引和计数，不能继续使用 student-cards 全量接口。

空状态必须如实说明“当前没有可查看的受保护学生”。本轮不新增名单入口、不静默接入普通学生库，也不在高级安全页偷偷增加第八个名单配置流程。

## 9.5 页面 5：学生支持记录与 AI 复核

目标布局：

- 顶部五步：保存记录、核对匿名内容、确认本轮发送、AI 追问/草稿、教师确认写入；
- 主体 18/52/30；
- 左：当前学生、返回名单、紧凑切换；
- 中：新增记录和可修订时间线；
- 右：匿名预览、追问、教师补充和最终草稿；
- 紧凑窗口改为纵向，不使用聊天气泡。

用户可见动作必须明确分开：

1. 仅保存到本机；
2. 保存并准备 AI 讨论；
3. 与 AI 讨论这条记录；
4. 确认本轮发送；
5. 用自己的话补充；
6. 核对并写入加密学生卡。

状态机：

~~~text
editing_record
→ saving_local
→ saved_local
→ preparing_anonymous_preview
→ preview_ready
→ dispatching_once
→ needs_information | proposal_ready | unavailable | result_unknown
→ teacher_review
→ persisting_card
→ saved
~~~

不变量：

- 仅保存到本机的物理模型请求数为 0；
- 保存并准备只生成预览，不立即发送；
- 每条记录单独讨论，不默认发送整名学生档案；
- 每轮预览最多一次请求；
- 追问后的补充生成下一轮新预览；
- AI 原始草稿不可变，教师最终值单独保存；
- AI 不得改变 record_kind，不得把转述升级为事实；
- record revision 变化后旧 review 失效；
- 最终 student_card_entry 绑定 source_record_id、source_revision 和 review_id；
- 最终确认只生成一个敏感行动组和一个匿名投影；
- 投影失败不回滚已经保存的学生卡；
- 锁定清空正文、预览、追问、草稿和 operation ID。

## 9.6 页面 6：学业证据与关注行动

页面只查看、核对和决定，不导入名单或成绩。

目标布局：

- 顶部：时间、考试系列、学科、只看可比证据和四项紧凑摘要；
- 主体 72/28；
- 左侧依次放四个“一图一问”区域；
- 右侧放证据解释、关注决定和选中证据快照。

四个问题：

1. 位次趋势：总体班级相对位置如何变化？
2. 学科哑铃：哪些学科在相邻可比考试中位次前移或后移？
3. 证据时间带：哪些考试可比，哪些必须断线？
4. 相对学科线索：哪些判断有足够重复证据？

图表共同规则：

- 使用现有 ECharts，不引入新图表依赖；
- 每图有文字摘要、单位、参评人数、图例和键盘可访问数据点；
- 颜色之外同时使用点形、实线/断线和文字；
- 不使用雷达图；
- 不使用双 Y 轴混合分数和名次；
- 不可比点保留但断线；
- 0 分是有效数字点；
- 缺考、免考、缺失、未完成、待核对和补测使用不同点形与文字；
- 点击点、学科行或考试卡都更新同一个右侧检查器；
- 下钻显示“教师确认的证据快照”，不是原始行。

排名相对位置：

~~~text
relative_position = 1 - (rank - 1) / (participant_count - 1)
~~~

只有 participant_count > 1 且 1 <= rank <= participant_count 时计算。participant_count < 10 时只展示原始名次，不生成长项/支持线索。

学业规则版本 academic_ruleset_v1：

- 只使用 active、teacher_confirmed、normal、场次元数据完整的证据；
- total_score 不参与学科相对长项判断；
- reference_only 只展示，不触发摘要；
- “相对长项线索 / 相对需要支持线索”要求同一场次、同一 cohort、scope 和 ranking rule；
- 每场至少 3 科，该学科至少 3 个合格场次；
- 该科相对本场各科中位位置的中位差 >= 0.10，且至少 2/3 场不低于中位数，才显示相对长项线索；
- 中位差 <= -0.10，且至少 2/3 场不高于中位数，才显示相对需要支持线索；
- 其他情况显示“证据不足”或“表现不一致”；
- 最近两点只有对应 dimension directly_comparable 才返回实际 delta；
- 至少 3 个连续、同系列、直接可比点，且相邻两段同方向达到阈值，才显示连续变化；
- 所有线索都是动态证据摘要，不持久化为学生标签；
- AI 不参与数值计算或长项判断。

关注决定：

- 跟进、观察、暂不行动三项都要求教师理由；
- 跟进和观察必须有复查日期；
- 暂不行动不建行动；
- 跟进和观察由 AttentionWorkflow 创建一组加密行动和一条匿名投影；
- 提交时重新校验 source_version；
- 证据修订后旧关注卡 invalidated，不自动搬运结论；
- 不显示风险分。

## 9.7 页面 7：高级数据安全

目标布局：

- 顶部 56/44；
- 左：保护状态和高密度专用备份表；
- 右：四步恢复流程；
- 底部：全宽危险区。

保护状态：

- 显示 PIN + Windows 当前用户保护；
- 显示服务端同步的剩余秒数；
- 显示 5 分钟无操作锁定；
- 显示普通备份不包含学生资料；
- 恢复密钥只显示“是否已确认离线保存”，绝不再次显示真实密钥；
- v2 模式提供“修改 6 位 PIN”；
- 修改成功立即锁定；
- 新旧 PIN 相同拒绝。

备份：

- 扩展名固定 .ctbackup；
- 列表显示文件名、时间、大小和验证状态；
- 创建必须输入独立备份密码；
- 密码提交后立即清空内存；
- 不提供明文导出；
- 备份列表读取失败不影响保护状态区。

恢复状态机：

~~~text
select_backup
→ entering_secret
→ verifying
→ impact_review
→ final_confirmation
→ restoring
→ restored_and_locked | preview_expired | preview_stale | restore_failed
~~~

规则：

- 恢复永远是 complete_replace；
- 不静默合并；
- 预览展示当前库和备份库的分项计数；
- 预览后当前库有写入则预览失效；
- 教师必须亲手输入服务端返回的确认短语；
- 恢复失败明确显示“当前库保持不变”；
- 恢复成功立即锁定并卸载敏感子树；
- 旧 schema 备份先在隔离 candidate 迁移和校验；
- 未知未来 schema 失败关闭；
- candidate 不得留下明文。

删除状态机：

~~~text
select_subject
→ loading_impact
→ impact_ready
→ entering_two_phrases
→ deleting
→ deleted | preview_stale | delete_failed | deleted_refresh_failed
~~~

规则：

- 影响分项包括学生卡、支持记录、学业证据、AI review、关注、敏感行动和匿名投影；
- 专用备份显示“可能包含该学生的现有备份”，除非已逐个验证，不能声称精确识别；
- 两个确认短语逐字匹配；
- 删除前绑定影响摘要版本，数据变化后要求重做预览；
- 删除完成后清空当前学生和所有缓存；
- 删除已完成但刷新失败时，仍显示“删除已完成，列表暂未读回”；
- tombstone 必须在删除真实映射前成功进入 outbox。

概念图纠正：

- 不承诺随时查看恢复密钥；
- 不把恢复画成选择性合并；
- 不使用 .bctbak；
- 不把现有审计描述成“谁查看了哪个学生”；
- 不把全部旧备份误称为已精确包含该学生。

## 10. 前端文件实施建议

保留一个外部 view，内部按工作面拆分。建议目标结构：

~~~text
frontend/src/workspaces/class-teacher/
├── views/ClassTeacherWorkbenchView.vue
├── shell/
│   ├── ClassTeacherSurfaceTabs.vue
│   ├── ClassTeacherSurfaceHost.vue
│   └── useClassTeacherRouteState.ts
├── security/
│   ├── VaultGate.vue
│   ├── VaultStatusStrip.vue
│   ├── createVaultSessionModule.ts
│   └── sensitiveSessionContext.ts
├── ordinary/
│   ├── createOrdinaryWorkModule.ts
│   ├── TodaySurface.vue
│   ├── CalendarSurface.vue
│   ├── WorkGraphView.vue
│   └── WorkNodeInspector.vue
├── affairs/
│   ├── createAffairModule.ts
│   ├── AffairsSurface.vue
│   ├── AffairRail.vue
│   ├── CurrentAffairStep.vue
│   └── AffairContextRail.vue
├── students/
│   ├── StudentSurface.vue
│   ├── StudentSubnav.vue
│   ├── directory/
│   ├── support/
│   ├── academic/
│   └── security/
└── __tests__/
~~~

每个前端 Module 只跨一条后端 Interface，页面不跨多个 transport client 重新拼业务状态。

旧组件处理：

| 旧组件 | 可迁移逻辑 | 处理 |
|---|---|---|
| UnifiedWorkBoard.vue | 工作图关系、普通 AI 操作恢复、revision 更新 | 拆入页面 1/2；新页面稳定后停止挂载 |
| ActionLedgerPanel.vue | 等待/完成校验、检查器交互参考 | 不挂载；ActionLedger 退为内部 Implementation |
| SopWorkspacePanel.vue | SOP 命令和模板显示参考 | 迁移到 AffairWorkflow Interface 后替换 |
| SensitiveStudentWorkspace.vue | 匿名预览、追问、模型操作恢复 | 拆入目录与支持页；禁止继续显示全班正文 |
| SupportWorkspacePanel.vue | 记录修订、计划、证据、关注、删除的写后读失败处理 | 按页面 5/6/7 拆分 |
| PlanningInboxPanel.vue | 普通敏感拦截参考 | 不重新放回首页 |
| CollectionInboxPanel.vue | 数量与等待规则参考 | 只迁移数据规则，不挂整块会议入口 |

新测试面稳定后，删除旧组件中重复的 UI 测试；保留真正验证业务规则、幂等、并发和安全的测试。不要在新 Interface 外再叠一层旧测试。

## 11. 实施包与依赖

这些包用于组织依赖，不是逐包正式测试或复审关口。按 ITERATION_SCOPE 连续推进，完整候选后集中验收。

### B-UI-R1-00：契约与合成基线

- 固化公开 Schema、错误码、领域词汇和匿名安全标题；
- 建立 42 名合成学生、并行 SOP、AI 追问、7 场考试、缺考/补测、备份/恢复 fixture；
- 为新 Module 写 Interface 级失败测试；
- 不修改页面。

完成标志：所有新行为先能由合同测试描述，旧 API 兼容预期明确。

### B-UI-R1-01：壳层与 VaultSession

- 四工作面查询参数；
- sessionEpoch；
- 敏感挂载门；
- 切出敏感域立即卸载和锁定；
- 服务端剩余秒数；
- 非法 URL 规范化。

完成标志：锁定时所有敏感 transport 调用为 0，后退和旧响应不能恢复正文。

### B-UI-R1-02：统一工作与匿名投影

- ordinary 003；
- UnifiedWork Interface；
- restricted_projection 只读；
- SensitiveWorkProjection Module 和 WorkProjectionSink Adapter；
- 当前 student card 双投影迁为一个 group；
- 页面 1 和 2；
- 普通节点详情、历史和收集汇总。

完成标志：教师只有一套全局待办；投影失败可恢复且不重复。

### B-UI-R1-03：事务处理台

- sensitive 020；
- AffairWorkflow 列表摘要、详情、命令和草稿；
- SOP 一个 occurrence 一个投影；
- 页面 3；
- 必做、安全、结案和重开。

完成标志：并行步骤仍只有一条匿名全局待办，旧 ActionLedgerPanel 不可达。

### B-UI-R1-04：学生目录

- 轻量目录和 workspace header；
- 页面 4 两列长卡；
- 搜索、筛选、排序、分页；
- 只在点击学生后读取单人正文。

完成标志：目录路径不会调用 student-cards 全量正文接口，页面无新增/导入。

### B-UI-R1-05：支持记录与 AI 复核

- sensitive 021；
- SupportRecordAIReview；
- 记录修订绑定、多轮逐次预览和最终 student card；
- 页面 5；
- 最终单投影。

完成标志：仅保存零模型请求；每轮最多一次；stale revision 不入档。

### B-UI-R1-06：学业证据分析

- sensitive 022；
- dual-read legacy session；
- StudentAcademicAnalysis；
- 页面 6 的三个图和一个证据线索区；
- 证据快照下钻；
- AttentionWorkflow 的理由、日期、source_version 和单投影。

完成标志：前端不计算可比性；不可比不连线；无原始行或风险分。

### B-UI-R1-07：高级数据安全

- ordinary 004；
- 修改 PIN；
- VaultLease 精确剩余秒数；
- restore preview v2；
- 删除预览版本绑定；
- 页面 7。

完成标志：恢复完整替换、成功锁定、失败保留当前库；删除不遗留匿名节点。

### B-UI-R1-08：旧入口退场与完整候选

- 停止生产挂载旧大组件；
- 删除重复 UI 和过期兼容调用；
- 更新 ARCHITECTURE.md 当前实现事实；
- 更新执行索引和实施记录；
- 冻结完整候选；
- 进行一次集中测试、双复审和合成人工验收。

## 12. 测试设计

### 12.1 后端 Interface 测试

建议新增：

- tests/class_teacher/test_unified_work_views.py；
- tests/class_teacher/test_sensitive_work_projection.py；
- tests/class_teacher/test_affair_workspace.py；
- tests/class_teacher/test_student_directory.py；
- tests/class_teacher/test_support_ai_review.py；
- tests/class_teacher/test_student_academic_analysis.py；
- tests/class_teacher/test_vault_admin_v2.py。

必须覆盖：

- ordinary + restricted 同屏；
- restricted 更新被拒并返回 open_required；
- projection 第一次失败、第二次成功；
- ack 丢失重放；
- 小 revision 不覆盖大 revision；
- 相同 revision 不同指纹冲突；
- 3 个并行 SOP 步骤仍只有 1 个投影；
- follow_up/observe 各 1 个投影，no_action 0 个；
- StudentCard 最终确认由 2 个投影降为 1 个；
- 目录不解密记录正文；
- record revision 变化使 AI review stale；
- 每个 preview 物理请求不超过 1；
- legacy 学业证据不自动归组；
- 0 分、缺考、补测和 rank > participant_count；
- analysis source_version 一致；
- PIN change 中断；
- restore preview stale；
- 恢复失败保持原库；
- 删除顺序先 tombstone 后移除映射。

### 12.2 前端 Vitest

建议新增：

- shell-navigation.spec.ts；
- sensitive-unmount.spec.ts；
- ordinary-work-module.spec.ts；
- today-surface.spec.ts；
- calendar-surface.spec.ts；
- affairs-surface.spec.ts；
- student-directory.spec.ts；
- student-support-flow.spec.ts；
- academic-analysis.spec.ts；
- vault-security-page.spec.ts。

必须覆盖：

- 一个外部路由、四个内部工作面；
- 非法参数规范化；
- 学生 ID、搜索词和敏感 token 不进入 URL；
- 锁定时敏感调用数 0；
- 旧 sessionEpoch 响应不回填；
- 日期点击只过滤；
- AI 待确认分支不入正式图；
- SOP 安全步骤不可免除；
- 目录卡无正文和导入入口；
- 仅保存物理调用为 0；
- 追问后必须新预览；
- 缺考不等于 0；
- 不可比点断线；
- 三种关注决定均要求理由；
- 恢复确认语由教师输入；
- 恢复成功立即卸载；
- 删除已完成但刷新失败仍显示已完成。

### 12.3 合成浏览器验收

只使用工作树隔离目录、临时数据库和假模型：

- 1440×900 与约 1024px；
- 四工作面键盘可达；
- 42 名学生分页；
- 长中文事务标题；
- 并行 SOP；
- AI 一次追问和一次最终草稿；
- 7 场考试、不同参评人数、缺考、补测和不可比断线；
- 创建 .ctbackup、预览、模拟 stale、恢复失败和恢复成功锁定；
- 模拟 5 分钟超时，确认姓名、正文、图表和恢复预览从 DOM 消失；
- 网络断开分别检查“没有保存”“已保存但读回失败”“结果未知”三种文案；
- prefers-reduced-motion；
- 图表数据点可键盘选中并更新文字检查器。

## 13. 故障场景总表

| 场景 | 冻结结果 |
|---|---|
| 重复操作 | 同 operation 和指纹返回同一结果；不重复节点、记录、模型调用、恢复或删除 |
| 同时操作 | expected_revision/source_version 冲突；不静默覆盖 |
| 中途退出 | 正式事务原子回滚；已成功的敏感事实不因投影失败回滚 |
| 重新启动 | Vault 锁定；模型 claimed 变 result_unknown；恢复预览失效；不自动重发 |
| 失败重试 | 模型不自动重试；本地 outbox 可幂等重试 |
| 取消 | 未确认草稿不入 WorkGraph、学生卡或关注行动 |
| 部分完成 | 明确区分“已保存、同步待恢复”和“没有保存” |
| 数据缺失 | 显示未知/证据不足；不猜日期、总分、排名或学校流程 |
| 数据冲突 | 保留来源和旧版本；教师重新核对 |
| 自动锁定 | 先卸载敏感 DOM，再尽力通知后端；旧响应无效 |
| 投影失败 | 敏感来源保留，outbox pending，不制造第二节点 |
| 来源删除 | 先 tombstone，再删除映射；普通区不残留打不开节点 |
| 学业修订 | 旧 attention invalidated，旧图形 source_version 不可提交 |
| AI 结果不明 | 查询原 operation，不追加物理请求 |
| PIN 修改中断 | 至少旧 PIN 仍可用，或新 PIN 可完成已暂存激活；不能两者都失效 |
| 恢复预览后写入 | preview stale，必须重新预览 |
| 恢复失败 | 当前库保持不变，不留明文 candidate |
| 删除失败 | 返回未完成项，不宣称完整删除 |

## 14. 最终验收与复审节奏

B-UI-R1 为高风险连续迭代：

1. R1-00—R1-07 只做受影响范围检查，不逐包启动正式全量测试和复审；
2. R1-08 冻结同一个完整候选；
3. 集中运行 B 全部后端测试、前端完整测试、类型检查、lint、构建、安全扫描和合成浏览器流程；
4. 并行进行一次需求符合性复审和一次代码质量复审；
5. 等全部意见返回后去重；
6. 只有本次直接引入或当前任务遗漏的 Critical / Important 阻塞；
7. 如有阻塞，统一修复一次，只复测受影响范围；
8. 由原评审者进行一次限定最终复审；
9. 若仍有当前范围 Critical / Important，停止并请用户决定，不启动第三轮；
10. 用户完成合成人工验收后，push、PR、合并和真实试点仍需分别授权。

## 15. 开始实现前的硬性检查

新窗口开始编码前必须确认：

- 当前目录是 D:\AI阅卷系统_工作机版_v1.5.0\.worktrees\class-teacher-iteration；
- 当前分支是 codex/class-teacher-iteration；
- git status 中 artifacts/ 是已有概念产物，不删除、不覆盖；
- 先查看本次文档差异，不能用旧计划覆盖本文；
- 不运行正式应用，不触碰根目录真实 user_data；
- 所有迁移先只对空合成库和停在 019/002 的合成库验证；
- 模型使用 Fake 或关闭状态；
- 不因为页面空状态而临时增加名单或成绩导入；
- 不把旧 ActionLedgerPanel 重新挂回生产页面；
- 不从概念图复制错误的恢复密钥、原始行、双投影或审计承诺。

建议新窗口第一条实施任务：

> 执行 B-UI-R1-00 与 B-UI-R1-01：先建立公开合同、合成 fixture、四工作面路由状态和 VaultSession 敏感卸载门。不得先画七页静态页面；不得挂载旧 ActionLedgerPanel；不得读取真实 user_data、运行真实迁移或调用真实模型。

## 16. 本文完成记录

- 集中调查：完成；覆盖产品设计、B01—B11 旧计划、UI-I1、B00 治理/安全、当前前后端、迁移和测试；
- 原始问题：按根因归并为“长页信息架构、两套行动真值、目录过度读取、AI 未绑定记录修订、学业场次缺失、安全页未编排”6 组；
- 设计复核：并行完成普通/敏感行动收口、学业/Vault 契约和七页交互状态三路只读复核；
- 测试与复审轮次：业务测试 0 轮、正式需求复审 0 轮、正式代码质量复审 0 轮；完成 1 轮三路只读设计校核，该校核不替代实现后的正式复审；
- 复核意见计数：收到 3 份设计校核意见，去重后并入上述 6 个根因；当前没有代码候选，因此不作 Critical / Important 代码问题裁定；
- 阶段耗时：本次文档设计未单独计时；没有执行长时运行检查、应用启动或模型等待；
- 文档测试：只需 Markdown 链接、差异和 git diff --check；未运行业务测试；
- 当前剩余工作：B-UI-R1-00—R1-08 全部实现；
- 当前任务是否通过验收：文档任务完成后为通过，业务实现尚未开始；
- 当前版本是否允许发布：不允许；真实数据、模型、迁移、启动和发布均未授权。

## 17. 实施候选记录（2026-08-01）

- R1-00—R1-08 已在 `codex/class-teacher-iteration` 连续实现并冻结为一个完整候选；没有逐包重复启动正式测试或开放式复审。
- 四工作面与七页已经替代生产入口中的旧长页挂载；视觉采用现有设计 token、低干扰高密度布局和贯穿普通工作、SOP 与证据的连续轨迹线。
- 普通区形成唯一 WorkGraph 深接口；敏感事务、关注和学生卡统一为一来源 occurrence 一投影，普通区只保存匿名安全标题。
- 敏感区形成内存 session epoch、离开即卸载/锁定、轻量目录、记录 revision 绑定 AI 复核、后端学业规则、PIN 修改、完整替换恢复和预览绑定删除。
- 页面补齐一句话 exact preview、最多一次模型发送、AI 草案二次确认、日历关系图、并行 SOP/教师决定/结案重开、目录筛选分页、本机记录保存修订、三图一线索、证据快照和双短语删除。
- 开发期只使用临时数据库、合成内容和假模型；没有读取真实 `user_data`，没有调用真实模型、产生费用、执行真实迁移、恢复或删除。
- 原始完整候选集中检查为后端 154 项、前端 99 个文件 883 项通过，类型检查、lint 和生产构建通过；首轮双复审后完成一次统一修复及限定最终复审。
- 限定最终复审仍有 5 项 `Important`，依流程停止；用户于 2026-08-02 授权独立限定修复，只处理回放、并发决定、关注原子事务、服务端学业筛选和恢复/删除未知结果。
- 限定修复候选验证：受影响后端 97 项、B 线完整后端 164 项通过；前端 99 个文件 887 项顺序测试通过；类型检查、lint、生产构建、Python 编译和差异格式检查通过。
- 限定双复审需求轴 0 条，质量轴 1 条 `Important`：学年/90天筛选锚点错误；唯一统一修复已完成，跨自然年与当前日期90天边界在学业分析 7 项测试中通过。
- 原质量评审者限定最终复审确认唯一登记问题已解决，`Critical=0`、`Important=0`、`Suggestion=0`，没有修复直接回归。
- 当前剩余工作：提交专用分支并本地合并到最新集成预览分支。
- 当前任务是否通过验收：自动测试和限定双复审通过，等待本地合并完成。
- 当前版本是否允许发布：尚不允许；即使本地合入集成预览，真实数据、真实模型、push、PR 和 `main` 仍未授权。

## 18. 明文调试与普通班务 AI 可见性候选（2026-08-02）

- 目标：解决普通班务真实模型已经请求但页面只留下空“AI 草案”的问题，并按用户最新授权直接取消班主任工作台调试期隐私保护。
- 冻结包含：普通班务生成按钮直接调用 AI；显示 `invalid_result` 等所有结果状态和真实收据请求数；启用共享有限重试与模型诊断；取消普通班务一预览一请求限制；学生与事务页直接挂载；生产 feature 使用明文对象仓库；班主任目录进入普通备份和恢复。
- 唯一保留的隐私行为：真实学生内容送到外部模型前，本机匿名化、完整预览并由教师点击一次确认。确认前物理请求必须为 0。
- 不包含：真实模型调用、8035 服务重启、真实学生数据写入、push、PR 或同步 `main`。用户虽授权迁移现有敏感库，但班主任分支和集成预览分支均未发现 `student_affairs.db`，所以本候选没有迁移目标。
- 故障边界：重复生成使用新操作编号但同一编号仍幂等；进行中调用不并发复用；共享网关只做有限重试；模型失败不写入工作图；明文写入仍用 SQLite 事务和 revision 冲突；普通备份对 SQLite 创建一致快照并排除 WAL/SHM；学生内容缺少匿名预览或确认时继续拒绝外发。
- 红色回归：普通班务按钮仍停在发送预览、`invalid_result` 无说明、生产状态仍锁定、普通备份排除班主任目录四类测试均先失败；实现后转绿。
- 当前自动验证：首轮完整候选为后端 226 项、班主任前端 29 项、lint 和生产构建通过。首轮双复审收到 5 条原始意见，去重为 `Critical=1`、`Important=3`：班主任数据库普通恢复、有限重试真实请求数、失败收据文案、旧加密库误写风险。已按一个统一批次修复；受影响复测为后端 56 项、Ops 70 项、班主任前端 14 项通过，lint 和生产构建通过。另发现 `test_workspace_foundation.py` 内一条修改前已有的迁移测试会跳过 019 却保留依赖 019 的 021，属于范围外旧测试；本次相关的 2 条共享网关测试单独通过。
- 限定最终复审：原需求评审者与原质量评审者均确认 `0 blockers`，4 个登记问题全部解决，没有修复直接引入的 `Critical` 或 `Important`。
- 本地集成：业务提交 `9ea9b415` 已合入 `codex/teacher-platform-integration`，首个集成合并提交为 `b350be61`；集成工作树原有 `user_data` 改动未被覆盖。合并后后端 240 项、班主任前端 31 项、lint、类型检查和生产构建通过。
- 当前剩余工作：等待用户另行授权重启 8035，再由用户人工查看；不调用真实模型、不 push、不创建 PR、不同步 `main`。
- 当前任务是否通过验收：自动测试、正式复审和本地集成通过；尚未重启到新代码，人工页面验收待进行。
- 当前版本是否允许发布：不允许；这里只授权本地集成预览，且当前服务尚未重启到新代码。
