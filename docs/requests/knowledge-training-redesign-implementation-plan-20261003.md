# 学情总览与知识结构改版实施方案（2026-10-03）

用途：交给实施代理的完整说明。用户已认可原型方向（`docs/requests/knowledge-training-redesign-prototype-20261003/`，本机用浏览器直接打开 `index.html`）。本文写明原型之外的数据来源、规则、改动位置、测试和文档更新；原型与本文冲突时以本文为准（差异见第 6 节）。

状态：已实施，并按用户授权完成本机视觉与交互检查。验证方法和限制见 `knowledge-training-redesign-acceptance-20261003.md`。原型目录按用户要求保留。用户已确认关联数据继续一次加载，两页切换复用结果。

实施前必读：`AGENTS.md`（授权、收尾三行、单次写入 ≤15KB）、`docs/product/KNOWLEDGE_AND_TRAINING.md` 第 150–203 行、`ARCHITECTURE.md` 第 46、56 行、`docs/ui/STYLE.md`。

## 1. 已确认的决定

1. 两页分工：学情总览 = 行动页（先做什么、给谁做）；知识结构 = 全册知识地图（哪里薄弱、知识点与技能怎么关联）。
2. 两页数字必须同一口径，同一范围下完全相等。
3. 删除“学生 × 知识点”矩阵视图；知识结构只保留地图 + 右侧详情抽屉。
4. 删除颜色模式切换。地图只按“明显薄弱人数占比”着色，每格底部加四档人数细条。
5. 知识点与技能分两栏显示（“知识点 · 学什么”｜“技能 · 会做什么”），外形不同；点击后用连线和高亮展示两者关联，关联数据使用题库已有统计，不调用模型。
6. 往届内容（本学期考试涉及、但属于其他册的知识点和技能）单列折叠，不计入本册数字。

## 2. 现状与已查证的问题

- 学情总览：`frontend/src/views/KnowledgeOverviewView.vue` + `components/knowledge-overview/*`，数据来自 `POST /api/training/overview`（`integration/mastery_overview.py::build_mastery_overview`，经 `overview_payload` 缓存，摘要诊断 `build_summary_profiles` 不含来源明细）。
- 知识结构：`frontend/src/views/KnowledgeGraphView.vue` + `components/knowledge-training/KnowledgeStructureBrowser.vue` + `components/knowledge-graph/GraphScopeFilters.vue` + `stores/knowledge-graph.ts`，数据来自 `POST /api/graph/query` 与 `/api/graph/evidence`。
- 数字不一致的两个原因（已用真实数据只读核对，八年级上册、全部学生）：
  1. “明显薄弱”定义不同：总览汇总条 `weak_topic_count`/`weak_skill_count` 统计“至少 1 名学生明显薄弱的项”（13 个知识点、25 项技能）；知识结构页头 `summarizeGraph` 统计“群体档位（人数最多的档位）为明显薄弱的项”，结果为 0。
  2. 范围不同：总览只含本册 369 个节点（8 章、28 节、223 知识点、110 技能）；知识结构含其他册被本学期考试触及的节点，共 382 项（含七年级上、下册章节），且排在前面。
  - 同一节点两接口的 `group_mastery`、`tier`、四档人数完全一致，不一致只来自汇总口径与范围。
- 群体档位不适合做地图底色：真实数据 382 项中群体档位为“明显薄弱”的为 0，大量为“证据不足”，按档位着色几乎全灰或绿。

## 3. 统一口径（两页共用，前端集中在一个模块计算）

所有统计以“当前范围”（教学学期 + 全部学生或单个班级）为准，分母和单位必须在界面写出。

| 名称 | 定义 | 单位 / 分母 |
|---|---|---|
| 有证据学生（某项） | 该项有观测的学生，即 `node.evidence_student_count`；四档人数之和等于它 | 人 |
| 明显薄弱人数占比（某项） | `distribution.weak / evidence_student_count`；分母为 0 时为“无证据” | 分母：该项有证据学生 |
| 有学生明显薄弱的项 | 本册内 `distribution.weak > 0` 的知识点或技能（即现有 `summary.weak_topic_count` / `weak_skill_count`） | 项；知识点、技能分开计 |
| 至少 1 项明显薄弱的学生 | 范围内学生中 `topics.weak + skills.weak > 0` 的人数 | 人 / 有证据学生数（`summary.evidence_student_count`） |
| 群体掌握度 | 沿用 `group_mastery`（有观测学生等权平均），附群体区间（各生区间边界平均） | 百分比，不是另一次拟合 |
| 本册 / 往届内容 | 本册 = 当前教学学期教材目录内的节点；往届 = 其他册且本范围内至少 1 名学生有证据的知识点或技能 | 往届不计入任何本册数字 |

热度色阶（地图底色，替代原型 7 级）：0（白）、(0,10%]、(10%,20%]、(20%,30%]、(30%,40%]、>40% 共 6 级；无证据用斜纹灰。色阶阈值写在一个常量里并在图例显示“底色越深＝明显薄弱的学生占比越高（分母：有证据学生）；底部细条＝四档人数分布”。这是显示色阶，不是新的掌握档位；档位仍只由后端判定。

“群体档位”（人数最多的档位）不再用于这两页的着色或汇总；班级报告等其他位置的规则不变。

## 4. 后端改动

### 4.1 复用选择

- 复用 `POST /api/training/overview` 作为两页唯一数据源，不新建接口。理由：两页数字同源即可天然一致；该接口已有本地快照、预热和失效规则。
- 不用 `/api/graph/query`：缺少逐生档位分布与知识点—技能关联，范围也不同。该接口保留，`stores/analysis.ts` 及报告仍在使用。
- 不用 `/api/training/diagnose`：含逐生来源明细，体积和计算量大，只为取关联不划算。

### 4.2 `integration/mastery_overview.py` 与相关模式

在 `build_mastery_overview(diagnosis, *, volume_id, associations=None)` 中：

1. 节点增加 `definition: str`（可为空）。来源：在 `integration/diagnosis_profile_service.py` 构造 `knowledge_catalog`（约第 769 行）时从 `resolver.nodes` 的 `definition` 补一个字段；`TrainingDiagnosisResponse.knowledge_catalog` 是 `dict[str, Any]`，无需改模式。
2. 节点增加 `group_interval_low`、`group_interval_high`（可为 null），取自 `group_weak_points` 对应条目的 `interval_low`/`interval_high`。
3. 节点增加 `in_volume: bool`。现有节点为 `True`。新增往届节点：`knowledge_catalog` 中 `node_kind` 为 topic/skill、不在本册节点集合、且范围内至少 1 名学生 `_has_evidence` 的键，`in_volume=False`；`chapter_key`/`section_key` 沿 catalog 的 `parent_knowledge_key` 链取到的章、节键，取不到填空字符串；`display_name` 用 catalog 全路径（前端按“册｜章”分组）。
4. `summary` 的全部计数和 `students[].topics/skills` 只统计 `in_volume=True` 的节点（保持现有数值不变）。
5. 响应增加 `associations: list[{topic_key, skill_key, question_count, same_part_question_count, basis}]`，只保留两端都在返回节点中的条目。`associations` 参数为 None 时使用 `diagnosis.get("knowledge_associations")`。
6. `overview_payload` 的计算函数在摘要诊断之外取关联：`CurrentKnowledgeResolver.from_active_database(service.question_bank_db_path)` + `knowledge_skill_associations(load_question_facets(path, resolver))`（`question_bank/recommendation/target_matching.py`，已有进程内缓存）。取不到时返回空列表并在 `warnings` 加“知识点与技能关联暂不可用”，不阻断总览。
7. 缓存键升版：`"mastery-overview-v1"` → `v2`，`'overview-payload-v1'` → `v2`（本地快照 `profiles.cache` 中旧版本不再命中）。
8. `backend/api/schemas/training.py`：`TrainingOverviewNode` 增加上述三个字段（均有默认值），`TrainingOverviewResponse` 增加 `associations`（新模型 `TrainingOverviewAssociation`，`basis: Literal['same_part','question_cooccurrence']`）。前端 `api/training.ts` 的类型与校验函数同步。

性能要求：用同等输入（真实数据只读，八年级上册全部学生）记录改动前后 `/api/training/overview` 冷启动首个请求和热缓存请求的耗时与响应字节数。冷启动增加超过 1 秒或响应增大超过 30% 时，先报告数字再决定是否改为按需加载。

不改变：掌握度计算、档位判定、考试成绩、训练证据、教师最终分锁；其他接口只有 `/api/training/diagnose` 的 `knowledge_catalog` 条目多出 `definition` 字段，其余响应不变。

## 5. 前端改动

### 5.1 共享部分

- 新建 `stores/mastery-overview.ts`（Pinia）：持有最近一次 overview 响应与加载状态（`idle | loading | ready | error | stale-error`，沿用现有总览的语义），键为 `(volumeId, scopeSelection)`；同键且就绪时两页切换不重新请求，`AbortController` 取消过期请求。复用候选：`stores/knowledge-graph.ts` 绑定 graph 接口与节点证据分页，不适用；`stores/training.ts` 持有完整诊断，不适用。
- 新建 `components/knowledge-overview/OverviewScopeBar.vue`：从 `KnowledgeOverviewView.vue` 现有“学生范围”按钮组抽出，两页同位置使用。内容：册名、“本学期 N 场考试 + 已发布训练”（场次数取响应 `exam_scope.sessions.length`）、“全部学生”与各班单选；右侧“口径说明”弹层，文字为第 3 节表格的通俗版。范围读写沿用 `features/evidence-scope/session.ts` 的现有规则（只恢复单班；其他保存值回到全部学生）。
- 新建 `components/knowledge-overview/metrics.ts`：第 3 节全部口径与热度色阶函数，两页只从这里取数；`model.ts` 中已有的 `shortNodeName`、`formatPercent`、`masteryDetail`、`compareFocusNodes`、`defaultStudentSort` 继续复用。
- 新建 `components/knowledge-overview/StudentTierChips.vue`：按四档分组的学生名牌（“姓名 18%”，悬停显示 80% 区间与“作答 n 处、全对 m 处”，即 `masteryDetail`），点击进入 `student-evidence`（`params.studentId`，`query: { knowledge, klabel, from: 'overview' }`）。总览卡片展开和地图抽屉共用。
- `OverviewTierBar.vue` 保留并增加可选的段内人数文字。
- `KnowledgeTrainingTabs.vue` 不变。

### 5.2 学情总览（`KnowledgeOverviewView.vue` 重写布局）

自上而下：

1. 范围栏（5.1）。
2. 四个指标：有证据学生 `evidence/student_count` 人；本学期平均得分率（`exam_score_rate`，注“有成绩 N 人”）；至少 1 项明显薄弱的学生 N 人（注分母）；有学生明显薄弱的技能 X 项 · 知识点 Y 项。
3. 左栏“本周建议优先处理”：本册技能按 `compareFocusNodes` 排序取前 5（只取 `distribution.weak > 0` 的；不足 5 项按实际数显示，0 项时显示“当前范围没有明显薄弱的技能”）。每张卡：排名、技能名、“属于 节 · 章”、“相关知识点：A、B、C”（取 `associations` 中该技能的知识点，同小问依据优先、题量降序，最多 3 个）、带人数的四档条、“有证据 N 人”、前 8 名明显薄弱学生名牌 + “+N”，按钮“给这 N 人出训练卷”（主）与“看错题”（次）、“展开分层名单”（展开后用 `StudentTierChips`）。卡片下方“查看全部 X 项 →”跳到知识结构并带“只看有学生明显薄弱”筛选（`query.filter=weak`）。
4. 右栏“需要个别关注的学生”：`defaultStudentSort` 后取前 8 名中 `topics.weak + skills.weak > 0` 的学生；每行姓名、班级、学号、得分率、“明显薄弱 N 项”及该生四档项数条；点击进入该生 `student-evidence`（`query.from=overview`）。
5. 右栏下方“本学期考查进度”：只列本册内至少 1 项有证据的章，每行“章名 · 群体掌握度 · 有证据 x/y 项 · 有学生明显薄弱的 n 项”，下面一条由该章各项小色块（同地图热度色阶）组成的条带；全部无证据的章合并成一行“尚未考查：……（k 章，共 m 项）”。各章“有学生明显薄弱”的项数之和等于指标卡中的对应总数。
6. 底部折叠“全部 N 名学生”：复用 `OverviewStudentTable.vue`（检索、排序不变）。

删除：`OverviewChapters.vue`、`OverviewFocusColumns.vue` 及只为它们服务的样式。

### 5.3 知识结构（`KnowledgeGraphView.vue` 重写）

- 数据：只用 `stores/mastery-overview.ts`，不再调用 graph 接口；地址栏只保留 `filter` 和可选 `focus`（节点键），不再序列化范围。旧链接带的 `session`/`class` 等参数忽略。
- 页头：标题“知识结构”，一句说明，右侧汇总“本册 x 项 · 有证据 y 项 · 有学生明显薄弱 z 项（与学情总览同一口径）”。
- 工具条：筛选 `全部 | 只看技能 | 只看知识点 | 只看有学生明显薄弱`，图例（第 3 节文字 + 色阶 + 无证据样例）。
- 地图：每个本册有证据的章一个区块（章名、群体掌握度、有证据 x/y 项）；区块内每节一行：左为节名，右为两栏：“知识点 · 学什么”｜“技能 · 会做什么”，中间细分隔线；某栏为空显示“—”。尚未考查的章合并为一个可展开的“尚未考查的 k 章”。最下方折叠“往届内容（本学期考试涉及 · n 项）”，按“册｜章”分组，同样两栏。
- 格子：约 132×52px。知识点：小圆角、左侧 3px 强调色边、底色浅化；技能：大圆角、底色全饱和。内容：名称（最多两行，完整名在 `title` 与 `aria-label`）、右上角明显薄弱人数（0 不显示，不得遮住名称：名称区右侧预留宽度）、底部 4px 四档人数细条。无证据格子为斜纹灰、无细条。
- 悬停：相关格子保持，其余降为 35% 不透明。点击：选中并打开右侧抽屉，同时用一层 SVG 画出到相关格子的连线（实线 = `same_part`，虚线 = `question_cooccurrence`），窗口缩放、滚动、展开折叠区时重算；相关格子被筛选隐藏或在未展开的折叠区时不画线，只在抽屉列出。连线路径避开相邻格子：同一行时走格子上方的弧线，不从中间格子穿过。Esc 或点空白处取消选择。连线计算可参考 `TrainingKnowledgeStructure.vue` 现有 `connectionLines` 的做法。
- 抽屉：名称、类型（知识点 / 技能）、“属于 节 · 章”、`definition`、群体掌握度与灰色群体区间、带人数的四档条、有证据人数、`StudentTierChips`、“相关技能 / 相关知识点”列表（名称、热度色块、明显薄弱人数、“同一小问 12 题”或“仅同题出现 3 题（虚线）”，点击切换选中），灰字说明“关联来自题库里同时考查两者的题目，不代表两者掌握度相同。”；按钮“给明显薄弱的 N 人出训练卷”与“看错题”（规则见 5.4）；无明显薄弱学生时出卷按钮不显示。
- 删除：`KnowledgeStructureBrowser.vue`、`components/knowledge-graph/GraphScopeFilters.vue`、`stores/knowledge-graph.ts`、`features/knowledge-graph/route.ts` 与 `model.ts`（删除前全局检索确认无其他引用）、`styles/knowledge-graph.css` 中不再使用的部分。

### 5.4 跳转与预设

- “给这 N 人出训练卷”（仅技能）：
  1. `saveEvidenceScope(semesterEvidenceQuery({ mode: 'selected', student_ids: 明显薄弱学生 }, volumeId))`；
  2. 在 `features/training/paper-selection-session.ts` 新增 `presetFocusedTraining({ targetKeys, rangeKeys })`：读取已有会话，没有时用 `TrainingRecommendationsView` 当前默认值（题数 10、难度上限 8、排除近期原题 true、`paperMode: 'individual'`）；设置 `targetKeys=[技能键]`、`rangeKeys=[所在节键]`、`scopeMode='focused'`、`paperMode='individual'`，清空 `adoptedGroup`、`groupEditor`；保存；
  3. `router.push({ name: 'training', query: { mode: 'student' } })`。到达后显示的是按学生训练的现有流程，不自动生成草稿、不冻结、不写训练数据。
  4. 返回学情总览后范围回到“全部学生”（现有恢复规则只恢复单班）；在本文档和产品文档写明。
- “看错题”：`{ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: 键, klabel: 名称, from: 'overview' } }`，沿用现有群体错题模式，范围取已保存的证据范围。
- 学生名牌、学生行：进入该生 `student-evidence`，带 `from: 'overview'`，返回按钮回到学情总览（现有行为）。

### 5.5 状态、窄屏与无障碍

- 未选教学学期、首次加载、读取失败、显示旧结果时读取失败：沿用现有总览的四种提示文字与“重新加载”按钮，两页一致。
- 宽度 < 960px：总览左右栏改为上下；地图两栏改为上下（先知识点后技能），抽屉改为全宽底部弹层；窄屏不画连线，只做高亮和抽屉列表。
- 格子、名牌、学生行都是可聚焦按钮；抽屉打开后焦点进入抽屉，关闭后回到原格子；色块旁始终有文字或数字，不只靠颜色。
- 颜色使用 `styles/tokens.css` 现有危险、警告、成功、边框色派生；如新增热度色阶变量，在 `docs/ui/STYLE.md` 对应位置写明。

## 6. 与原型的差异（实施以本文为准）

| 位置 | 原型 | 实施 | 原因 |
|---|---|---|---|
| 总览“本学期考查进度”的条 | 按各项“群体档位”统计项数 | 按各项热度色块组成的条带 | 群体档位已从两页移除，避免重新引入不一致口径 |
| 热度色阶 | 7 级，阈值 8%/16%/24%/32%/42% | 6 级，阈值 10%/20%/30%/40% | 便于图例说明 |
| 数据 | 合成数据、合成关联 | 真实 overview 响应与题库关联统计 | — |
| 往届内容计数 | 合成 10 项 | 按 4.2 第 3 条从真实证据得出 | — |
| 连线 | 同行相邻格子之间可能穿过中间格子 | 同行走上方弧线 | 原型缺陷 |
| 薄弱人数角标 | 可能压住长名称 | 名称区预留宽度 | 原型缺陷 |
| 原型控制 | 角标切换演示状态 | 不实现 | 仅演示用 |

## 7. 测试

按 `AGENTS.md` 顺序优先扩展现有测试：

- 后端：扩展 `tests/test_api_graph_selected_scope.py` 中已覆盖 overview 的测试（约第 146–212 行）：
  - 往届节点 `in_volume=False`，且 `summary` 与只用本册节点重算的结果相等；学生的 `topics/skills` 计数不含往届节点；
  - `associations` 两端都在返回节点中，`basis` 与 `same_part_question_count > 0` 一致；
  - 有定义的节点 `definition` 非空，群体区间与 `group_weak_points` 一致；
  - 摘要路径（`overview_payload`）与完整诊断路径（`build_mastery_overview(full, ...)`）结果仍相等，本地快照恢复后仍相等；
  - 关联读取失败时返回空列表与对应 warning，其余字段不变（新增测试函数，放在同一文件）。
- 前端单元（vitest）：
  - 扩展 `src/__tests__/knowledge-overview-view.spec.ts`：指标卡数值与分母；前 5 技能排序与“相关知识点”；“给这 N 人出训练卷”写入的证据范围、`presetFocusedTraining` 结果和跳转地址；“看错题”地址；考查进度各章之和等于指标；保留现有三个用例的语义。
  - 重写 `src/__tests__/knowledge-graph-view.spec.ts` 为地图测试：使用 overview 模拟响应；页头数字等于同一响应下总览的数字；热度等级边界（0、10%、10.1%、40%、40.1%、无证据）；两栏分组；筛选；点击后高亮、抽屉内容与关联依据文字；Esc 关闭并还原焦点；往届折叠区不计入页头。
  - 为 `metrics.ts` 和 `presetFocusedTraining` 的边界补断言（放在上面两个文件内即可，不另建文件）。
  - 删除随组件一起失效的 `knowledge-structure-browser.spec.ts`、`knowledge-graph-store.spec.ts`、`knowledge-graph-route.spec.ts`。
  - `workbench-view.spec.ts` 中到 `/knowledge-graph` 的跳转断言保持通过。
- 浏览器（Playwright，默认 `playwright.config.ts`，接口用 `page.route` 模拟）：`e2e/knowledge-graph.spec.ts` 目前测试的是已不存在的画布图（`.knowledge-graph-canvas`），用新地图用例替换：渲染、连线出现且实线/虚线正确、抽屉、窄屏布局、总览到按学生训练的跳转。
- 验证命令：`npm run build`（类型检查 + 构建），相关 vitest 文件，`runtime/python/python.exe -m pytest tests/test_api_graph_selected_scope.py`，上面的 Playwright 文件。重建共享 `frontend/dist` 前先确认没有其他任务在用（`AGENTS.md`）。

## 8. 文档更新（同一提交）

- `docs/product/KNOWLEDGE_AND_TRAINING.md`：
  - 第 158–159 行：保留群体颜色规则，注明“学情总览和知识结构不使用群体档位着色，见下文”；
  - 第 173–175 行：学情总览与知识结构只提供全部学生或单个班级范围；得分率和指定学生筛选保留在按章节、按学生训练；
  - 第 191–197 行“学情总览”：按 5.2 改写，删除“章节概览”“最需关注”的旧描述；
  - 新增“知识结构”小节：本册地图、两栏、热度色阶口径、往届内容、关联连线与依据、抽屉；
  - 写明“给这 N 人出训练卷”进入按学生训练专项、只预填不生成，以及返回后范围回到全部学生。
- `CONTEXT.md`：如“明显薄弱人数占比”“往届内容”成为页面固定用语，补词条并写清分母。
- `ARCHITECTURE.md` 第 46 行附近：说明知识结构页改为复用学情总览接口；overview 响应包含往届节点与知识点—技能关联，关联来自题库只读统计。
- `docs/testing/README.md`：如测试入口或分组变化（删除的 spec、替换的 e2e），改对应句子。
- `docs/ui/STYLE.md`：新增色阶变量时写明。
- 本文件与原型：实施完成后在本文件开头改写“状态”一句；原型目录是否保留由用户决定。

## 9. 验收

- 同一范围下两页的“有证据”“有学生明显薄弱”数字一致；切换班级后两页同时变化。
- 真实数据只读打开两页（不调用模型、不写数据）截图：总览首屏、地图首屏、选中一个有跨节关联的知识点、窄屏各一张。
- 第 4.2 节的性能前后数字。
- 从总览进入按学生训练后，学生、技能和小节已预填，且没有生成草稿。

## 10. 不在本次范围

- 个人报告（另有方案）。
- 掌握度模型、档位门槛、推荐与组卷规则。
- 班级报告和个人报告中的知识掌握图（仍按群体档位着色）。
- `/api/graph/*` 接口本身（保留给其他调用方）。
