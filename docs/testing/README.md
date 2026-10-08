# 当前测试与人工验收

本文件只说明当前可用的测试入口、隔离边界和人工验收方法。测试默认使用合成数据和模型替身，不接触真实业务数据，也不产生真实模型费用。

## 按本次影响选择验证

- 文档和设置：检查内容、格式与引用；项目文档检查入口为 `tools/check_documentation.py`，不因此启动业务应用或执行完整业务测试。
- 文案与布局：检查相关页面、显示效果和受影响操作，不默认新写自动测试。
- 功能修复：重现并重走原操作，选择能覆盖实际故障的现有测试；需要回归保护时优先扩展现有测试，新增顺序见 `AGENTS.md` 收尾一节。
- 题库格式与当前依据：复用 `tests/test_question_bank_importer.py`、`tests/test_question_bank_read_cache.py`、`tests/test_api_question_bank_routes.py` 和联合分析、判定版本、个性化推荐、训练卷的现有测试。核对旧格式实际入口、Word 公式与表格、正文和图片文件变化、不同资料目录、逐题失败隔离、同时间多版本选择、教师确认及编辑保护、读取不新增并行模型正文、已发卷快照和新进程缓存恢复。`tests/training/test_combined_question_analysis.py` 核对 Word 解析复用时的完整内容键、嵌套结果独立、损坏输入重试及大输入不入缓存。兼容旧摘要必须同时覆盖正文真正改变后拒绝复用。
- 数据库当前结构与升级协调：复用 `tests/test_schema_baseline.py`、`tests/test_migration_tooling.py`、`tests/test_p3_11_schema_version_gate.py`、`tests/test_make_update_package.py`、`tests/test_ops_jobs.py` 和 `tests/test_ops_offline.py`。检查空库直接初始化、当前库不读历史 SQL、历史前缀仍严格校验、异常记录不盖成功标记、两库失败恢复及代码回退范围；用 `tools/generate_schema_baseline.py --check` 核对生成定义。合成回归不代替实际旧机升级和完整安装包验收。
- 评分、组卷、推荐、保存与性能：对照独立的预期结果；涉及保存时检查重新进入，性能变化比较同等输入的前后测量。
- 后端读取性能：`test_request_read_connections` 覆盖捕获期间两库提交、有限重试与资源关闭；`test_api_training_routes` 覆盖任务元数据不使学情失效、有关输入刷新和解析缓存容量。`test_assembly_assistant` 核对每题资料请求内复用及来源变化不发布起草；`test_analysis_report` 核对修订输入共用、单人读取范围及跨考试并发。`test_report_results` 核对报告状态输入复用、保存结果即时显示及成绩、题面、答案、错因变化后失效；`test_api_workbench_overview` 核对跨请求复核计数复用及批次、赋分与数据库更新后失效。`test_api_frontend_launcher` 使用服务替身核对普通启动实际输出请求耗时，且不带查询参数或正文。推荐原有小组采用用例同时核对固定成员读取和保存都不重新自动分组。
- 代理决定测试位置；用户确认业务预期，不必确认内部接口。已授权实现范围内，隔离本地测试可执行、修复并复跑受影响项。
- 以下套件是可选入口，不是每次修改都要逐级执行的关卡；通过相关检查后，只有新修改、失败或具体未解决问题才扩大范围；日常小改动不默认全量测试或独立复审。
- 学情总览与知识结构：后端 `tests/test_api_graph_selected_scope.py` 覆盖摘要／完整诊断、快照恢复、往届排除、群体区间与关联失败回退；完整题型诊断另核对单人／小组仍读完整同班基准、教师最终分与满分、未选中者改分后的缓存与快照恢复，摘要和技能模式不读新基准；前端 `knowledge-overview-view`、`knowledge-graph-view` 覆盖共享口径、热度边界、范围、出卷预填、筛选与焦点返回。`npx playwright test e2e/knowledge-graph.spec.ts` 使用默认配置与模拟接口验证两栏、关联实线／虚线、抽屉、窄屏和进入按学生训练时不创建草稿。
- 题型标准与新导入题：`tests/test_skill_candidates.py` 覆盖题型候选、主次标签约束、备份与隔离预演、数据变化停止、写入失败回退、来源失效拒绝或显式跳过、同时间版本取最新及已有文件保护；`tests/training/test_combined_question_analysis.py` 覆盖封闭题型候选、主次结果校验与保存、无合适题型待归类且不提知识点新词、五维定义合同及跨册直接链接拒绝、来源变化与恢复，兼容无题型标准。
- 题型推荐与报告：`tests/training/test_personalized_recommendation.py` 覆盖完整班级基准的消费者、单人范围与跨班隔离、未选中者改分后的参照变化、原题参照、难度先上下 1 级不足再 2 级、候选分层、同题型题面排序、小节分散及候选不足补齐、小组对个人补弱需要的保留、后续换题优化与旧技能模式；`tests/test_analysis_report.py`、`tests/test_personal_report_design.py` 覆盖题型归并、同名不同身份、无关联或教学学期、报告称谓、同册混合题型与知识点章、往届技能兼容、目标切换的报告指纹、分数与排名。教师最终分锁仍由既有改分与报告比较测试覆盖。
- 自动题型发布：复用 `tests/test_skill_candidates.py` 的合成整理用例，核对 30 道规范题与 3 份来源卷门槛、出现记录与删卷边界、预估上界实际未达门槛的零请求、无教师操作发布、新增类型与全题覆盖检查、未知请求不重发、处理中新增可用题拒绝发布及激活后写入失败回退。`tests/test_job_manager.py` 核对待发布任务不占满分析队列、排队任务先完成、发布期间阻止新提交、同册两卷共用一次整理授权与费用授权幂等；备份恢复复用原运维测试。旧八上受限合并刷新也扩展 `tests/test_skill_candidates.py`，核对每题一次请求、默认命令行只读、保护资料逐行保持、来源与标准冲突、人工值保留及同一操作不重发。
- 核心流程失败优先修复；不用测试数量代替功能可用。
- 按学生／按章节与批量错题本：`practice-selection` 覆盖名单、四档统计及导出恢复，连同 `training-recommendations-view`、`training-group-recommendations`、`personalized-recommendation-draft`、`student-evidence-view` 和 `training-api` 核对勾选、用途规则、概况及证据入口。后端扩展学生 API、题库读取缓存、个性化推荐、训练 API 与 Word 渲染的现有测试，核对知识排序、来源去重、批量预览、章节过滤、巩固上限、旧请求编号和旧草稿导出。真实数据验收通过应用生成草稿及错题本，不调用模型；仅记录匿名构成、缺口和版式结果。
- 个人报告在线查看与批量导出：扩展 `tests/test_analysis_report.py`、`tests/test_api_report_jobs.py`、`tests/test_report_export_job.py`，核对本人输入失效、旧缓存只读兼容、损坏索引、状态、裁切路径、缓存导出、清单、取消与下载后回收；版式沿用 `tests/test_personal_report_design.py` 和 `tests/test_report_print_layout.py`。前端扩展 `results-center-view`、`file-center-view`、`app-shell`，核对四态入口、当前筛选翻页、复核返回、生成预估确认、多人多场与任务名称。真实数据只读查看，生成和数据写入用合成数据与模型替身；单列首次与连续翻页耗时，不以数据版验证代替真实叙述生成验收。
- 普通组卷导出：`tests/test_assembly_export_job.py` 覆盖 PDF 与兼容 Markdown 的任务发布、记录、两条下载入口、取消及草稿保留，并用本机 LaTeX 实排合成的大图、跨页合并表格和公式；引擎不可用时跳过实排项，不能据此声称验证了 PDF。公式转换复用 `tests/training/test_latex_render.py`，Word 回归复用 `tests/test_question_document_pipeline.py` 与 `tests/training/test_personalized_paper_formula_rendering.py`。前端扩展 `question-assembly-view`，连同 `assembly-store` 核对格式选择与导出请求。人工核对纸面小字、大图、续页和预留作答区；高中样本及断网新工作机需另行验证。
- 班级组卷：`tests/test_assembly_assistant.py` 覆盖多班与指定考试、只读逐题证据、快速起草与规则保存；`tests/test_unified_practice_rules.py` 核对参数化上限与近期规则，`tests/training/test_personalized_recommendation.py` 核对全班固定题序、无证据成员和生成幂等。前端扩展现有 `assembly-assistant`、`question-assembly-view` 与 `personalized-recommendation-draft` 用例；`training-scan-batch-panel` 与 `training-return` 覆盖回收状态、四请求读取上限、导入中断保留、单份与批量费用确认、超时只查询、停止剩余、教师锁与冲突刷新、备注保留及批量更新范围。`training-recommendations-view`、`workbench-view` 核对草稿页头与工作台定位。`npx playwright test --config playwright.p2-18-real.config.ts` 在 8018 端口使用模拟接口，核对回收页焦点、取消不发送、锁定请求、1440/1280/1100/900px 布局、减少动态效果和 40 份答卷读取性能；真实数据人工验收只查看班级、考试、题面、已有篮子与回收入口，添加、替换、降低限制、导出及训练写入在合成环境验证，不调用真实模型。

## 自动测试入口

知识资料的生成排版验证复用 `tests/test_knowledge_graph_release.py`，逐一核对所有已登记分类修订的知识发布与配对词表在写入、重新读取后内容和发布哈希一致，并通过既有发布校验。完整级仍覆盖暂存、激活、回退与历史标准兼容；所有写入都使用合成临时文件和数据库。

GitHub 的 [CI 工作流](../../.github/workflows/ci.yml) 在推送或提交 PR 到 `main` 时运行。后端使用现有 `quick --skip-frontend` 测试清单，并检查文档以及已跟踪的私有数据、密钥文件和运行产物；前端执行代码规范、全部单元测试、类型检查、生产构建、合成演示服务测试与默认模拟浏览器流程。仓库路径边界检查使用 `tests/test_repository_data_boundary.py`，覆盖业务目录外的数据库、配置备份、环境文件和生成文件，以及 Git 命令失败。CI 使用合成数据和模型替身，不配置真实模型密钥。它不替代 `full`、独立真实 API 浏览器验收或完整安装包验收。

在项目根目录使用 PowerShell 7 运行：

```powershell
& .\runtime\python\python.exe tools\run_test_suite.py quick
& .\runtime\python\python.exe tools\run_test_suite.py full
& .\runtime\python\python.exe tools\run_test_suite.py serial
& .\runtime\python\python.exe tools\run_test_suite.py release
& .\runtime\python\python.exe tools\run_test_suite.py review
```

| 入口 | 适用场景 | 当前内容 |
|---|---|---|
| `quick` | 需要跨模块快速反馈时 | 从现有用例中选择配置保存、扫描批改、人工改分、推荐去重、模型请求失败及恢复流程，配合少量页面行为和题框编辑器测试 |
| `full` | 完整功能候选 | 当前后端测试、串行隔离组、前端静态检查、单元测试和构建，以及真实浏览器改分保存与成绩表下载验收 |
| `serial` | 单独复现隔离问题 | 只运行依赖 Windows 文件锁、固定端口、子进程或进程级状态的测试文件 |
| `release` | 发布候选或发布工具发生变化 | 先运行 `full`，再补充打包、历史数据库升级和性能工具检查；历史升级仍检查所有支持的起点 |
| `review` | 人工复核、评分保存或成绩展示发生变化 | 复用相关后端和页面测试，再通过真实浏览器验证改分、刷新、新窗口重新进入、旧窗口冲突保护及下载成绩表 |

性能回归复用 `tests/test_class_analysis.py`、`tests/test_question_bank_read_cache.py`、`tests/test_api_training_routes.py`、`tests/test_api_graph_selected_scope.py` 和 `tests/test_api_scan_grading_workspace.py`。检查学生人数增加时来源指纹的计算次数、按册和当前页的实际读取范围、概览与图谱精简内部投影后的完整输出相等、全体学生学期观测只读取一次、完整公开字段及输入不变、完整诊断及含小组响应的新进程恢复、小组参数与题目资源更新失效、命中后不再构造完整诊断或小组模块、小组追加字段与直接生成 JSON 后的响应与原完整模型编码字节相等、非有限数值及数值字符串拒绝、启动首章默认小组与新增最近请求触发空闲准备、最近范围及该范围完整分组优先、无最近范围时题库浏览优先、后台重放不改变前台最近记录、任务间及等待前台期间切换范围后重排，而真实批改任务继续让位、相同模型输入在扣分说明更新及请求临时快照间复用而观测结果／题库来源／参数／计算周／排除证据变化重新计算、本地概览与图谱恢复、题目关联读取不做全表统计且原地标签修改后刷新（`tests/question_bank/test_topic_skill_matching.py`）、正文增删改、计算版本失效、损坏和保存失败回退，以及批改两个未完成计数共用一次查询而下一次摘要重新读取。前端复用 `training-api`、`training-recommendations-view`、`practice-selection` 和 `training-group-recommendations`，核对重复字段及深层路径字段拦截、诊断范围切换、过期请求取消、章节页首次仅一次显示小组请求、小组失败回退基础诊断、整份结果更新、所有知识项的左栏与学生概况相等、完整出卷依据的范围隔离、采用前完整来源复核，以及依据读取失败后的草稿与原令牌恢复。完整、旧摘要和显示响应分别核对进程内命中及新进程恢复。组卷预取复用 `assembly-assistant` 与 `question-assembly-view`，核对仅准备下一目标、前台优先、复用同目标请求、切范围取消及离页停止。正文摘要复用 `tests/training/test_combined_question_analysis.py` 与 `test_part_assessment_mastery.py`，核对嵌套内容变化、线程与请求隔离、容量和失败重试。指定题读取复用 `tests/training/test_personalized_recommendation.py` 与 `tests/test_assembly_assistant.py`，核对完整来源快照及题卡相等、批量边界、遗漏题号和原全范围路径。性能测量分别记录首次进入、连续刷新和合成数据更新后刷新；区分缓存恢复与缓存未命中的重算，并记录数据完成与页面内容可见时间。完整输出相等与耗时比较使用同等输入，真实数据只读。

入口与并发：

- 后端并行进程数按本机处理器数自动选择，最多 6；`--workers` 可指定后端进程数；前端单元测试最多使用 4 个进程，排除 `test-results/` 中的历史验收副本。
- `--durations` 控制每组显示的慢用例数量，默认 10，设为 0 关闭明细；成功与失败摘要都保留慢用例耗时。
- 测试文件分组以 `tools/test_suite_manifest.py` 为准。

入口关系：

- 这些入口复用同一批测试，不需要依次全部运行：`quick` 和 `review` 是按操作选择的子集；`serial` 只规定需要独立运行的环境，也包含少量发布工具测试；`release` 已包含 `full`。
- 前端其他浏览器专项和演示服务测试通过下表的业务命令运行，不计入 `full` 的页面单元测试；统计全项目数量时应单独计入它们。前端代码规范检查跳过前端output目录中的测试缓存与生成产物，该目录仍保留在本机。
- 兼容入口 `tools/smoke_check.py` 的后端测试委托给 `run_test_suite.py full --skip-frontend`，使用同一分组、隔离环境和默认进程数；它自身仍负责文档、静态编译及数据库副本初始化检查；`--pytest-workers` 和 `--pytest-durations` 会传给统一入口，`--parallel-tests` 仅保留为旧命令兼容参数；需要前端验收时直接使用 `full`。
- `review` 复用 `tests/api_e2e/harness.py` 的合成考试与模型替身，通过真实页面、API 和临时数据库保存分数，不拦截保存响应；浏览器按当前步骤给分、确认后继续复核、返回成绩明细及下载成绩表的操作验收，同时检查非法输入、旧窗口冲突和重新进入后的分数。浏览器环境使用独立动态端口与数据目录，直接运行当前前端源码，不重建共享 `frontend/dist`。
- 需已安装前端依赖和 Playwright Chromium；截图、失败追踪和浏览器日志保存在本次新建的 `output/review_browser_*` 目录。
- `full` 在前端检查通过后执行同一浏览器步骤；`review` 是只验证相关业务的较小入口，不能跳过前端；`full --skip-frontend` 与默认 `npm run e2e` 不包含这项真实保存验收。

默认 `npm run e2e` 只收录八个模拟接口文件：`app-shell`、`session-config`、`knowledge-graph`、`template-region-editor`、`review-evidence`、`review-queue`、`workbench-overview`、`training-recommendations-real-api`。最后一个文件也使用模拟接口。各文件复用 `e2e/mock-fixtures.ts`：未提供合成响应的 API 请求会失败，测试结束会检查遗漏；教材目录复用 `e2e/mock-curriculum.ts`。测试禁用服务工作线程；启动服务前用 Vite 在新建的 `output/TEST-e2e-mock-*` 目录构建并预览，关闭 `/api` 转发且不复用已有服务。真实 API 与演示服务器的流程使用各自的 `playwright.p2-*.config.ts`；改分保存专项使用 `review` 入口。

工作台几何检查使用“减少动态效果”，核对静止布局的间距、重叠和吸顶位置。

题框真实 API 专项用替身返回空的自动建议，保留人工框选、保存、确认及重新进入的检查；它不验证 OCR 质量。扫描专项准备已确认样卷后验证预检、暂停、续跑、后来匹配补批、替换批次与取消。批量复核保留超分和非整数拒绝、整批失败保留草稿及只发送一次的检查。

在 `frontend` 目录运行专项入口：

| 要检查的操作 | 命令 | 前端来源 |
|---|---|---|
| 批量复核演示流程 | `npm run e2e:review-batch` | 普通入口先构建，再启动合成演示服务；并行任务可将 `P2_08_FRONTEND_DIST` 设为独立构建目录后运行 `:prepared` |
| 样卷题框编辑 | `npm run e2e:template-regions` | 使用当前源码的开发服务，不需要预构建 |
| 扫描与批改 | `npm run e2e:scan-grading` | 普通入口先构建，后端使用隔离测试数据；并行任务可将 `SCAN_BROWSER_FRONTEND_DIST` 设为独立构建目录后运行 `:prepared` |
| 设置页学生名单 | `npm run e2e:students` | 普通入口先构建，后端使用隔离测试数据；覆盖导入预览、编辑与输入确认删除；并行任务可将 `STUDENT_BROWSER_FRONTEND_DIST` 设为独立构建目录后运行 `:prepared`，避免改写共享成品 |
| 技能找题、原地标注、整卷、待处理与试卷篮 | `npm run e2e:question-bank` | 普通入口先构建，每次新建 TEST-question-bank 合成目录，独立端口；在原测试文件拆成技能编辑与共享试卷篮、大整卷恢复、待办跳转三条流程；保留 2,005 道题、1280/1440、Esc 与焦点返回 |
| 训练推荐 | `npm run e2e:training-recommendations` | 普通入口先构建；浏览器使用当前源码与模拟接口，覆盖三栏／窄屏、选人、讲义预设、抽屉、批量错题本及下载 |

依赖前端成品的四组入口保留同名 `:prepared` 命令；训练推荐的 `:prepared` 直接使用当前源码与模拟接口；已有当前且完整的构建产物时可直接运行，不重建共享 `frontend/dist`。题框编辑入口直接使用源码，没有单独的 prepared 命令。专项配置从项目 `runtime/python/python.exe` 启动后端，历史配置文件名保留，入口按业务操作命名。

这些专项沿用固定端口和各自的 `frontend/test-results/` 合成数据目录；运行前确认没有其他任务共用服务、构建产物或该测试目录。

`npm run demo:review-batch` 单独启动批量复核演示服务，`npm run demo:test` 检查该服务的合成数据行为。正式改分保存与下载验收仍使用上面的 `review` 或 `full` 入口。

## 掌握度前向检验

维护人员在项目根目录运行：

```powershell
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper --initial
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper --grid
```

默认只读 `user_data`；`--data-root` 可指定已有数据位置，不创建副本或迁移。工具用较早考试及测试考试之前的已发布训练证据预测后一次考试，比较当前模型、仅整体能力与直接做除法，并列出方案记录的旧公式基线。对数损失、Brier 使用全部有效观测，AUC 仅使用完全对与完全错的观测；分档应验率只统计训练、测试中都直接观察到的学生与目标，证据不足不定义应验率。网格以最近考试的对数损失选参数，差异不超过 0.002 时优先保留更大的知识点层差异。输出只含汇总指标、校准、分档覆盖、档位变化与无身份个案计数，不保存逐生结果、不调用模型。参数选择用于提交检验依据，不自动改变产品常量。

## 第一、二章训练卷适配实验

```powershell
& .\runtime\python\python.exe tools\experiment_training_fit.py --output output/test_training_fit_current/aggregate-report.json
& .\runtime\python\python.exe tools\experiment_training_fit.py --evidence-loss --output output/test_training_fit_current/evidence-loss-report.json
& .\runtime\python\python.exe tools\experiment_training_fit.py --match-audit --output output/test_training_fit_current/match-audit-report.json
```

该入口是独立的真实数据只读实验，不属于自动测试。默认核对本学期第一、二章及两章合卷；个人卷在该入口保留只补弱对照设置，未测目标上限为 0，使用原限制内换题，并核对选题与内存草稿构造一致。新建个人卷的补弱优先规则需用下述带补充上限的端点比较验证。小组卷保持原用途；补弱试配只保留能对应实际直接失分的题目，仍需对全体成员适用，不要求每题对全员补弱，不设个人覆盖差距合格线。数据库保持原位置，以只读连接读取，不保存草稿、不准备判定版本、不持久化学情、不复制数据库、不调用模型。报告仅包含匿名计数、按水平汇总与知识标准名称，不包含学生身份或题目正文。知识点与技能按教材归属计数，跨章关联单列；关联覆盖不等于独立教师确认的训练任务覆盖。已有训练资料的只读预览不能代表正式新建时可能补齐的资料，报告不作为学习效果或教师验收结果。

`--evidence-loss` 盘点考试关联、可用逐点记录、有效单判定点结果、教师总分及多判定点题的归因缺口；固定分数、题库、参数、学生与近期实际参加记录，比较现状、可用逐点信息退化为粗粒度小问得分、去掉教师多判定点总分、仅保留有效逐点记录、去掉最早考试及完整有效结果六种条件。退化条件不把多点总分保留为精确补弱来源；完整有效结果同时纳入有效逐点结果和可归属的单判定点题项结果，包含正确作答，不要求补造步骤。逐条件绕过持久化与共享学情缓存，比较个人选题和现有规则形成的小组成员；小组成员变化不代表共用候选已经通过资格检查。条件中的删证据只发生于内存，不是建议删除考试记录；只有逐点输入仍会丢弃有效客观题，不能当成教学金标准。结果是敏感性，不是“补齐后必然改善多少”的因果估计。

`--match-audit` 在内存中放宽难度与近期原题两项条件，再按原规则还原候选和严格补弱选题，分别追查无核心候选与已有候选未入卷。范围、有效资料和作答要求条件保持原状；题量与同技能限制继续按完整题执行，多个入卷限制允许重叠计数。匿名报告保留拒绝原因、标准名称及缺失目标的当前直接关联库存；库存为空不等于题库没有相同技能的题，须另查知识主题与技能关联。原用途规则的水平样本仅作兼容诊断，正式个人结果以严格补弱汇总为准。用于原位置核对的题目定位只即时显示，不写入报告，题面、答卷及学生身份不导出。匹配追查不能替代对学生水平和完整任务的独立教学判断。

实验的合成边界验证复用 `tests/training/test_personalized_recommendation.py`，覆盖共同适用、只补弱用途与范围、缺题和空卷、补弱优先及未测目标上限、已有有效正确或失分记录不算未测、过难与超范围补充拒绝、未测题换题及保存、下一轮参数恢复、原限制内换题及新增/失去目标、兼容配置、空卷讲义跳过与草稿不变、只读写入拒绝、对照条件保留分数、匿名配对统计、难度与近期原题限制及重叠入卷限制。粗粒度总分失分只安排受新练习上限约束的独立诊断短题，不计补弱覆盖。适合难度覆盖掌握度模型的基准与范围计算、最基础题与出卷上限两种收口、模型字段缺失或无证据的回退、预计做对可能性说明，并保留经验规则的失分依据统计、教师上限与缺分不算失分；选题覆盖新目标比例及先探测后提高难度。作答方式覆盖客观来源配过程作答的完整覆盖、过程来源配客观作答的环节练习，以及未知来源作答方式不标完整覆盖。选题验证同一学生同一技能需要去重、最新掌握度改变优先级而不删除历史失分、两题联合替换突破单题技能冲突并继续遵守解答题和相似题限制，以及章节分组按共用卷保留每名成员至少四分之三个人薄弱练习且不少于 6 道题的质量判定，连同共同技能需要比例、难度窗口相交与目标难度差的相容前置检查。班级核心候选及相近任务卷内限制复用 `tests/test_unified_practice_rules.py`，区分作答方式、技能集合、题图与规范化题面，多小问题不因部分重合删除整题；候选仍保留到难度评价阶段。前端请求与讲义导出复用 `frontend/src/__tests__/personalized-recommendation-draft.spec.ts`。实验设置、解释与未完成部分见 [当前实现与交接说明](../requests/training-recommendation-backend-handoff-20261004.md)。

推荐算法改动的前后配对比较使用 `tools/compare_training_endpoints.py --output <目录> --baseline-remediation-only --baseline-max-unmeasured-questions 4 --max-unmeasured-questions 4`：目录下 `baseline/`、`latest/` 分别放两版 `personalized.py`、`diagnosis_profile_service.py` 与 `question_tag_projection_service.py`，工具在同一只读输入上重跑第一章、第二章与两章合卷的个人卷和小组卷，核对两端个人选题与各自正式内存草稿一致，并按最新规则复核难度、失分关系与近期原题。仅优化 `personalized.py` 的性能时使用同一工具的 `--performance-only`：诊断与投影两份源文件必须相同，两端共用只读事务、诊断和固定时钟；在内存中按规范化 JSON 完整比较分组成员、目标、卡片、警告和来源版本，以及所有个人和小组草稿的题目、题序、用途、理由、缺口与警告。测量候选缓存清空后的分组、同进程再次分组、91 人个人草稿、5 人逐人草稿和最大组的所选成员复核；`performance.json` 只保存耗时、汇总人数及组数和代码摘要，每个范围通过后保存汇总，仅全部完成及来源不变时标记 `completed: true`。

合成回归扩展现有个性化推荐与统一练习规则测试，核对重复评价减少、分组请求内不变学生输入只序列化一次、普通评价的可变输入重新校验、近期原题并集过滤后的完整输出、候选池扩大时重算、生成小组草稿不重建自动分组、内部共享输入不被改写、同目标同难度计划在内部复用、公开评价的目标映射独立；试组位集合保留组员近期原题并集排除、无关题号和空候选结果，排序仅在前六项条件打平时比较同型程度；适合难度检查先排除题目再展开作答任务，匹配与难度警告数量不变。章节初始匹配覆盖改变题数、每项技能题数及解答题数量后完整结果与新计算相同，缓存恢复与同值新快照复用，学生输入、掌握度、候选、来源资料、近期原题、排除集合、难度、题库真实合成提交和知识内容变化时失效，交错输入淘汰，以及同键并发只计算一次且各请求候选身份和逐目标记录独立恢复。缓存容量与并发错误传播在现有 `tests/test_question_bank_read_cache.py` 中核对，包括总字节淘汰、超大替换不保留旧值、超大结果供已等待者使用、失败后重试与返回值独立。固定偏好和匹配覆盖内容、作答方式、错因状态与教材位置变化，选卷计数覆盖配额改变和重复已选题，掌握度资料覆盖诊断说明、当前模型输出与真实合成提交的失效。页面计时可用当前前端的独立构建与正式只读诊断路由，不启动生产应用生命周期、写业务库或保存真实响应；只导出汇总耗时与逐字节相等判断。首次无快照、完整响应命中和真实更新后计时须分开报告。

效果报告使用 `tools/build_training_endpoint_report.py <目录>` 生成匿名 HTML，`tools/serve_training_endpoint_report.py <目录> --port <端口>` 在本机只读显示题面，只含匿名编号、题号和派生判断。当前效果依据为 `output/test_training_skill_model_v4_20261003/`，解释与性能交接见[当前实现与交接说明](../requests/training-recommendation-backend-handoff-20261004.md)。

## 工作台首页

在 `frontend` 目录运行 `npx vitest run src/__tests__/workbench-view.spec.ts src/__tests__/knowledge-overview-view.spec.ts src/__tests__/results-center-view.spec.ts src/components/results-center/__tests__/results-overview.spec.ts src/components/results-center/__tests__/report-pipeline-button.spec.ts`。覆盖待处理规则、步骤边界、考试切换保护与旧请求取消、学情读取不保存范围，以及首页看卷入口自动打开与不可用状态。第二期规则覆盖训练页数与提交份数、无当前考试的训练入口、未挂技能、个人报告与训练来源重试。成绩中心的入口行为放在已有页面测试中，“AI 整理”按钮的预取、缓存打开与核对期间禁用由 `report-pipeline-button.spec.ts` 单独核对；纯规则测试继续复用原文件。

`npx playwright test e2e/workbench-overview.spec.ts` 使用当前前端源码与合成接口，覆盖 1024、1280、1366、1440、1920px 布局、右栏吸顶、失败提示、考试切换和第二期汇总；首页允许的 POST 仅为只读的学情总览。后端运行 `runtime/python/python.exe -m pytest tests/test_api_workbench_overview.py tests/training/test_training_assessment.py tests/training/test_training_feedback_api.py tests/test_question_bank_read_cache.py tests/test_api_report_jobs.py`，核对学期筛选、原统计不变、训练当前修订与取消状态、个人报告三态与只读边界，以及本场未挂技能与技能索引一致。真实数据验收只通过正式应用读取页面，不执行评分、模型或数据清理操作。

## 隔离规则

设置和原卷清理的回归用例复用学生、媒体、模板上传、扫描续跑及 API 写入测试；`tests/test_session_originals.py` 覆盖文件清理模块的保留范围、共享路径、回执损坏、中断续清和重复请求。内部系统检查复用 `tests/test_api_ops_routes.py`；设置页三个标签、旧地址与上次标签恢复、AI 调用记录按需读取和未保存保护复用 `settings-hub-view.spec.ts`，数据与空间操作及无关检查请求隔离复用 `settings-ops-view.spec.ts`。诊断摘要失效复用 `tests/test_llm_gateway.py`，题号输入缓存和配置延迟加载复用题号兼容与 `config-workspace-store.spec.ts`。人工验收在 1440、1280、宽屏和窄屏检查三个设置标签、学生名册、抽屉和弹窗；性能对比使用同一考试与数据，分别测量首次打开、刷新和主要接口，排除人为等待，并核对统计、成绩输出一致。核对 PDF 释放后现有页面仍能批改、清除原卷后仍可人工改分。真实数据验收需取得本次授权，仅只读查看；清理、备份恢复和模型验证使用合成环境。

- 所有自动测试使用合成数据库、合成图片和测试替身；测试不得调用真实模型；子进程会移除模型密钥等敏感环境变量。
- 测试入口把成绩数据、工作区数据、模型配置、分类状态、运维状态和本机应用数据目录改到本次运行的临时沙箱；不得读取、写入或迁移真实 `user_data/`，也不得依赖用户电脑中已有的业务文件。
- 统一入口和直接运行 `python -m pytest` 都默认在项目 `.test-runs/` 下建立短路径临时目录，同时重定向 `TEMP`、`TMP`、`TMPDIR`；并行进程和浏览器使用各自的沙箱；直接运行时显式指定的 `--basetemp` 仍由调用者负责选择和清理。
- `tmp_path` 创建的独立副本在用例通过后立即回收，失败用例的副本最多保留到本次运行结束；整个运行结束后清理基础库、临时备份及日志文件并恢复原有环境，验收截图与命令日志保留在 `output/`；进程被强制终止或文件仍被占用时可能留下该次临时目录。
- 测试收尾关闭数据库观察连接和本轮日志文件，避免 Windows 文件占用阻止回收；统一入口在每个 pytest 子进程退出后清理该组临时目录；历史升级检查每验证完一个版本就释放该版本的合成数据库和备份，保留各版本检查结果及工作目录中原有内容。
- 后端并行采用按文件分配，同一测试文件始终留在同一进程。
- 只有确实依赖文件锁、固定端口、子进程或全局状态的文件才进入 `SERIAL_TEST_PATHS`；若前一文件留下的进程状态仍会影响结果，再进入 `PROCESS_ISOLATED_TEST_PATHS`。
- 新测试默认进入 `full`；`QUICK_TEST_PATHS` 和 `QUICK_FRONTEND_TEST_PATHS` 分别选择后端和前端的少量关键流程，直接复用现有用例，未被选中的用例继续在 `full` 中运行；`RELEASE_AUDIT_TEST_PATHS` 只收发布候选需要的治理检查。
- 不重复维护源码字符串与实际行为的同一要求：页面导航、任务保存等优先由实际行为测试覆盖，固定颜色、像素和源码写法不作为日常回归条件；确有长期价值的模块边界和启动配置检查保留。
- 文档链接检查通过 `tools/check_documentation.py` 或兼容入口 `tools/smoke_check.py` 单独执行；功能全量不重复扫描历史方案中的文件链接；文档检查器自身的规则仍由合成文档测试验证。
- 优先保留能验证用户结果的完整流程，以及评分、保存冲突、取消恢复、重复请求和失败回滚等关键边界；相同行为已被上层流程覆盖时，删除重复的底层测试及仅供它们使用的数据准备代码；公共规则只保留必要的输入分类，不在每个调用接口重复展开组合。
- 旧阶段完成断言、简单转发及实现写法检查不作为持续回归要求；独立故障不为数量目标而塞进一条循环，也不通过跳过或排除文件来降低完整级的数量；性能工具复用实际请求验证，避免复制整份路由配置作为另一套预期值。
- 测试数据优先复用当前数据库初始化入口；需要多道独立候选题时使用不同的合成题干，只有验证重复题处理时才故意使用相同内容；历史版本升级测试仍逐一验证每个迁移起点。
- 反复准备相同题库的业务测试可显式使用 `question_bank_database` fixture（测试准备函数）：每个进程按知识标准版本建立临时基础库，每个用例获得独立副本，并再次执行生产初始化检查；副本不覆盖已有目标，测试写入不会进入基础库；空库初始化、升级与恢复测试继续直接使用原入口，不自动替换。
- `DATABASE_BASELINE_TEST_PATHS` 显式列出的业务测试复用当前数据库结构：每个进程真实初始化一次基础库，再为各用例复制独立数据库，继续执行生产入口的版本检查与后续初始化；只处理临时目录内尚不存在的数据库；已有数据库、自定义迁移、历史升级、数据库结构检查及真实 API 启动验收沿用原流程。
- 修改测试分组后，先运行 `tests/test_test_suite_runner.py` 检查路径唯一性和车道边界。

页面验收选择受影响流程，检查操作完成、保存后重新进入、布局与失败提示；复杂改造再专门复审。不要用测试数量代替功能结果。正式服务重启和真实数据操作按 `AGENTS.md` 授权。
