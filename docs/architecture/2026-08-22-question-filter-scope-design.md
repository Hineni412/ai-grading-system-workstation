# 题库筛选三项改进设计：前置知识点隐去、错因筛选、严格教学进度过滤

> 日期：2026-08-22
> 状态：待实现（本文是实现前冻结的设计契约）
> 范围：组卷工作台筛选面板、题库管理试卷内题目筛选、题库读取服务与 facets 接口。
> 不包含：修改任何题目的已有标签；改变打标流程与词表治理流程；训练推荐算法本身。

## 一、背景与目标

教师在组卷工作台按"教材章节 + 标签"挑题组卷。当前题库已有 888 道题（38 份八年级上册期中试卷 + 七年级下册试卷，全部经 AI 打标）。实际使用中暴露三个筛选问题：

1. 选中"八年级上册 第一章 勾股定理"后，知识点筛选项里混入不少七年级的前置知识点，干扰八上训练选题。
2. 题目带有错因标签（error_type），但任何筛选界面都无法按错因筛题。
3. 选中第一章想给"只学到第一章"的学生组训练卷时，跨章题目（同时含第一章和后续章知识点）也会进入备选池，需要人工逐题剔除。

## 二、冻结需求（已与教师确认）

### 需求 1：筛选面板隐去前置知识点

- 只在**筛选面板**做调整，题目与题卡上的标签保持完整显示。
- 知识点维度默认显示**本学期（当前册别）**的所有知识点选项，不再受原 12 个显示上限约束。
- 原"更多知识点"展开器改为"**展开前置知识点（N）**"，点击后才显示之前册别的知识点。
- 无法识别路径格式的知识点标签归入前置区（保守处理）。

### 需求 2：错因筛选

- 在筛选中新增"错因"维度。
- 只显示受控词表 `ERROR_PRONE_CATEGORIES`（`question_bank/models/tag_schema.py`）内的 12 个规范错因值及其计数；AI 自由发挥的长尾错因文本不作为筛选项显示（题目上的标签保留）。

### 需求 3：严格教学进度过滤

- 新增"严格教学进度"开关，默认关闭。
- 开启后按**累计教学进度**过滤：筛选第 N 章时，只保留"知识点全部属于已学范围"的题目。
- 已学范围 = 之前各册（按目录册序）+ 本册第 1..N 章。
- 同时涉及"第 N 章与第 N 章之前章节"的题目**保留**；涉及第 N+1 章及以后章节、或更晚册别知识点的题目**排除**。
- 无知识点标签的题目不排除（没有超纲证据）；无法识别路径格式的知识点标签不排除（保守保留）。

## 三、现状与数据事实（2026-08-22 调查）

### 3.1 需求 1 的数据事实

- 八上第一章（勾股定理）46 题，共 137 条知识点标签：八年级上册 97 条、七年级下册 32 条、七年级上册 8 条。
- 46 题中 19 题带七年级知识点标签。
- 筛选面板的知识点选项来自 `GET /api/question-bank/facets` 的 `knowledge_points`，按当前过滤后的题目集合聚合，因此前置知识点会进入选项列表。
- 知识点标签值是全路径（`册｜章｜小节｜细分点`，全角竖线分隔），前端 `knowledgeLeafLabel()` 只显示末段；册别前缀可直接用于分区判断。

### 3.2 需求 2 的数据事实

- 888 题中 273 题有错因标签；错因值共 172 个不同取值。
- 头部规范值覆盖绝大多数：`运算化简错误` 160、`公式/定理误用` 98、`条件识别不完整` 80、`概念理解不清` 53、`数形转化困难` 37、`分类讨论遗漏` 23、`图形关系识别错误` 16、`辅助线思路缺失` 14、`书写依据不完整` 10。
- 长尾为一次性的 AI 自由文本（如"万花筒镜像计数易重复计数或漏算……"），不应成为筛选项。
- 打标 prompt 已把 `ERROR_PRONE_CATEGORIES` 作为 `error_prone_options` 提供给模型（`question_bank/services/ai_tagging_service.py` 第 728 行附近），因此这 12 个值就是事实上的受控错因词表。
- 词表治理（`question_bank/taxonomy/governance.py` 的 `ALLOWED_DIMENSIONS`）**不包含**错因维度，错因不走治理扩展与别名映射。

### 3.3 需求 3 的数据事实

- 有知识点标签的 273 题中，93 题的知识点跨章。
- 八上第一章 46 题中，11 题的知识点还涉及八上其他章（最典型的一题横跨第一、三、四、五章）。
- 当前过滤实现（`build_question_filter_query` 的 tag_filters）是 `EXISTS (... tag_value IN (...))`：只要含有任一选中值即入选，不排除该题还含有其他章的知识点。

### 3.4 相关代码现状

- 组卷工作台筛选面板：`frontend/src/components/question-bank/AssemblyQuestionBrowser.vue`。`primaryTagRows` 定义标签维度行（知识点/能力/解题方法/数学思想/模型/特殊题型），知识点行 `visibleLimit` 为 12，超出部分收进"更多知识点"展开器。
- 题库管理试卷内题目筛选：`frontend/src/components/question-bank/QuestionBankFilters.vue`（纯文本输入表单，不加载 facets）。
- 题库读取服务：`question_bank/services/question_read_service.py`。`QuestionReadFilters` 是过滤数据契约；`_question_filter_parts()` 组装 SQL；`_list_facets()` 聚合各维度；`_tag_facet()` 的允许 tag_type 集合当前不含 `error_type`。
- 路由：`backend/api/routers/question_bank.py` 的 `GET /api/question-bank/questions` 与 `GET /api/question-bank/facets`。
- 响应 schema：`backend/api/schemas/question_bank.py` 的 `QuestionFacetsResponse`。
- 前端 API 客户端：`frontend/src/api/question-bank.ts` 的 `QuestionBankFilters`、`QuestionBankFacets`、`questionListPath()`、`questionFacetPath()` 与 facets 解码器。
- 教材目录：`question_bank/taxonomy/curriculum_catalog.py`，`load_curriculum_catalog()` 返回 5 册（`order` 1-5），每册章节有 `order`、`id`、`label`、`exam_scope_values`。知识点路径前缀格式为 `{册label}｜{章label}｜…`。

## 四、详细设计

### 4.1 需求 1：筛选面板知识点分区（仅前端）

只改 `frontend/src/components/question-bank/AssemblyQuestionBrowser.vue`。

- 仅对 `key === 'knowledgePoints'` 的维度行做分区，其他维度行不变。
- 分区依据：当前册别 `currentVolume.value?.label`（如 `八年级上册`）。知识点值以 `${册label}｜` 开头的归入"本学期"，其余（含无法识别路径格式的值）归入"前置知识点"。
- 有册别上下文时：
  - 本学期分区：完整显示全部 chips（不受 `visibleLimit` 12 限制），多选、计数、已选态行为不变；
  - 前置分区：默认收起，显示为展开器"展开前置知识点（N）"，展开后行为与原"更多知识点"一致；
  - 前置分区为空时不显示展开器。
- 无册别上下文（目录未加载完成等）：保持现状（前 12 个 + "更多知识点"展开器）。
- 两个分区内的 chips 点击都走现有 `toggleTagFilter('knowledgePoints', value)`，筛选语义不变。
- 已选条件（activeFilters）展示不变：即使前置知识点被收起，已选中的前置知识点仍出现在"已选条件"里，可单独清除。

### 4.2 需求 2：错因筛选（前后端）

**后端**：

1. `question_bank/services/question_read_service.py`：
   - `QuestionReadFilters` 增加字段 `error_types: tuple[str, ...] = ()`。
   - `_question_filter_parts()` 的 tag_filters 构造中增加 `("error_type", filters.error_types)`（直接精确匹配，不走 `expand()`，因为错因不在治理维度内）。
   - `_tag_facet()` 的允许集合增加 `"error_type"`。
   - `_list_facets()`：增加 `error_types` 的 facet_source（`facet_source(error_types=())`，即错因 facet 自身不被已选错因过滤，保持与其他维度一致的自排除语义），返回字典增加 `"error_types"` 键。
   - 错因 facet 结果按受控词表过滤：只保留 `value in ERROR_PRONE_CATEGORIES` 的项（从 `question_bank.models.tag_schema` 导入），计数不变。建议做法：`_tag_facet` 增加可选参数 `allowed_values: frozenset[str] | None`，在 `_public_facet_items` 之后过滤。
2. `backend/api/routers/question_bank.py`：`list_questions` 与 `list_question_facets` 增加查询参数 `error_types: Annotated[list[str] | None, Query()] = None`，传入 `QuestionReadFilters(error_types=tuple(error_types or ()), ...)`。
3. `backend/api/schemas/question_bank.py`：`QuestionFacetsResponse` 增加 `error_types: list[QuestionFacetItem]`。

**前端**：

1. `frontend/src/api/question-bank.ts`：
   - `QuestionBankFilters` 增加 `errorTypes?: string[]`；
   - `QuestionBankFacets` 增加 `error_types: QuestionBankFacet[]`；
   - `questionListPath()` 增加 `appendTexts(parameters, 'error_types', filters.errorTypes)`；
   - facets 解码器增加 `error_types` 的解码（与其他维度同规则）。
2. `frontend/src/components/question-bank/AssemblyQuestionBrowser.vue`：
   - `primaryTagRows` 增加一行 `{ key: 'errorTypes', label: '错因', items: facets.value.error_types, visibleLimit: 10 }`；
   - `filters` reactive 增加 `errorTypes: [] as string[]`；`TagArrayFilterKey` 增加 `'errorTypes'`；`activeFilterLabels` 增加 `errorTypes: '错因'`；`queryFilters()` 增加 `errorTypes: [...filters.errorTypes]`；`resetFilters()` 同步清空。
3. `frontend/src/components/question-bank/QuestionBankFilters.vue`（题库管理-试卷内）：
   - 增加"错因"下拉：选项来自 `questionBankApi.listFacets({ paperIds: props.paperId ? [props.paperId] : [], tagStatus: 'all' })` 的 `error_types`（组件挂载时加载一次，失败时静默隐藏该下拉）；
   - `draft` 增加 `errorType: ''`，`buildFilters()` 增加 `errorTypes: draft.errorType ? [draft.errorType] : []`，`reset()` 同步清空。

### 4.3 需求 3：严格教学进度过滤（前后端）

**后端**：

1. `question_bank/taxonomy/curriculum_catalog.py` 新增辅助函数：

   ```python
   def teaching_progress_allowed_prefixes(chapter_id: object) -> tuple[str, ...] | None:
       """返回该章作为教学进度上限时，已学范围允许的知识点路径前缀。

       已学范围 = 目录中册序更小的所有册（整册）+ 本册章序不超过该章的所有章。
       章节 ID 无法解析时返回 None（调用方失败关闭，不加过滤）。"""
   ```

   实现：用 `load_curriculum_catalog()` 找到该章所属册与章序；前缀集合 = `{更早册label}｜`（每册一个）+ `{本册label}｜{章label}｜`（本册 order ≤ N 的每章一个）。知识点路径以"册｜"开头即属于该册整册；以"册｜章｜"开头属于该章。
2. `question_bank/services/question_read_service.py`：
   - `QuestionReadFilters` 增加字段 `teaching_progress_chapter: str = ""`。
   - `_question_filter_parts()`：当该字段非空时，调用上面的辅助函数得到允许前缀；若返回 `None`，追加 `where.append("1 = 0")`（无法解析进度上限时失败关闭，不静默放行）；否则追加：

     ```sql
     NOT EXISTS (
         SELECT 1 FROM question_tags tp
         WHERE tp.question_id = q.id
           AND tp.tag_type = 'knowledge_point'
           AND COALESCE(tp.tag_value, '') <> ''
           AND NOT (tp.tag_value LIKE ? OR tp.tag_value LIKE ? OR ...)
     )
     ```

     参数为各允许前缀加 `%` 后缀。无知识点标签的题目该子查询自然为真（保留）。
3. `backend/api/routers/question_bank.py`：`list_questions` 与 `list_question_facets` 增加查询参数 `teaching_progress_chapter: str | None = None`，传入 filters。非法值（目录中不存在）按上一条失败关闭（返回空列表），不抛 4xx。

**前端**（`frontend/src/components/question-bank/AssemblyQuestionBrowser.vue`）：

1. 增加开关状态 `strictProgress = ref(false)` 与 UI：筛选面板头部区域放一个开关"严格教学进度：排除涉及未学章节的题"。
2. 可用性：仅在 `selectedChapterId` 或 `selectedVolumeId` 非空时可用；否则禁用并附提示"先在左侧选择教材章节"。
3. 开启时 `queryFilters()` 增加 `teachingProgressChapter`：
   - 选中章（或选中小节）时：传该章的稳定 ID；
   - 只选中册时：传该册最后一章（按目录 order）的稳定 ID，等价于允许整册 + 之前各册。
4. `frontend/src/api/question-bank.ts`：`QuestionBankFilters` 增加 `teachingProgressChapter?: string`；`questionListPath()`/`questionFacetPath()` 增加 `parameters.set('teaching_progress_chapter', ...)`（非空时）。
5. 开关变化、章节变化后沿用现有 `loadQuestions(true)` 刷新链路（含 facets 联动）。
6. 开关状态不持久化，刷新页面后恢复关闭（避免教师忘记开关处于开启状态）。

### 4.4 API 契约变更汇总

- `GET /api/question-bank/questions`：新增可选查询参数 `error_types`（多值）、`teaching_progress_chapter`（单值）。响应结构不变。
- `GET /api/question-bank/facets`：新增同样的两个可选参数；响应新增字段 `error_types: [{value, count}]`（只含受控错因值）。
- 旧客户端不传新参数时行为完全不变（向后兼容）。

## 五、受影响文件清单

| 文件 | 改动 |
| --- | --- |
| `question_bank/services/question_read_service.py` | filters 契约 + 错因过滤/聚合 + 严格进度 NOT EXISTS |
| `question_bank/taxonomy/curriculum_catalog.py` | 新增 `teaching_progress_allowed_prefixes()` |
| `backend/api/routers/question_bank.py` | 两个端点各加两个查询参数 |
| `backend/api/schemas/question_bank.py` | `QuestionFacetsResponse.error_types` |
| `frontend/src/api/question-bank.ts` | filters/facets 类型、路径构造、解码器 |
| `frontend/src/components/question-bank/AssemblyQuestionBrowser.vue` | 知识点分区、错因维度行、严格进度开关 |
| `frontend/src/components/question-bank/QuestionBankFilters.vue` | 错因下拉 |
| `tests/test_api_question_bank_routes.py` | 新参数与错因 facet 用例 |
| `tests/test_question_bank_read_cache.py`（如受影响） | 缓存键包含新 filter 字段的回归 |
| `frontend/src/__tests__/question-assembly-view.spec.ts` | 分区显示、前置展开器、严格开关 |
| 前端 API 解码相关测试 | `error_types` 解码 |

注意：`QuestionReadFilters` 是 facets 缓存键的一部分（`list_facets` 的 cache key 含 filters），新增字段后确认缓存键测试仍然通过。

## 六、测试计划

### 后端（新增/扩展用例）

1. 错因过滤：`error_types=运算化简错误` 只返回带该错因的题；多个值取并集。
2. 错因 facet：只返回 `ERROR_PRONE_CATEGORIES` 内的值；自由文本错因不出现在 facet 中但题目标签保留。
3. 严格进度：
   - 构造跨章题 A（第一章+第四章知识点）与纯本章题 B（仅第一章）、跨"第一章+第二章"题 C；
   - `teaching_progress_chapter=第一章ID` 时：B、C 在结果中，A 不在；
   - `teaching_progress_chapter=第二章ID` 时：A 仍不在（含第四章），C 在；
   - 无知识点标签的题始终保留；
   - 非法章节 ID 返回空列表；
   - `/facets` 带同参数时计数与列表一致。

### 前端

1. 选中八上第一章后，知识点行默认只显示八上路径的 chips；"展开前置知识点（N）"显示且数量正确；展开后七年级 chips 可正常多选筛选。
2. 无册别上下文时保持原"更多知识点"行为。
3. 错因维度出现在维度切换中，选择后请求携带 `error_types`。
4. 严格开关：未选章节时禁用；选中章开启后请求携带正确的 `teaching_progress_chapter`；只选册时传该册最后一章 ID。
5. facets 解码器接受含 `error_types` 的响应，缺失时按既有严格解码规则处理（与后端契约同步变更）。

## 七、验收标准（按仓库 AGENTS.md 顺序）

1. 应用启动正常；
2. 组卷工作台页面打开正常；
3. 核心流程：选"八年级上册 第一章" → 知识点区默认只见八上知识点，前置收起 → 展开前置可看到七年级知识点 → 错因维度可筛选 → 开启严格进度后，横跨第一/三/四/五章的题（如恢复数据中的 840 题）不再出现，纯第一章与"第一章+前置"题保留；
4. 保存试卷篮后重新进入，筛选与篮内题目仍正确；
5. 页面状态与视觉布局正常（维度切换、展开器、开关的可用/禁用态）；
6. 受影响测试全部通过；
7. 需求复审与代码质量复审各一次。

## 八、风险与边界

- 需求 1 只改筛选面板的显示，不改任何标签数据；题卡上的标签保持完整。
- 需求 2 的错因 facet 只显示受控值，可能让教师看不到长尾自由文本错因的存在；题目详情中的错因标签不受影响。若后续要把长尾纳入治理，应走词表治理流程，不在本次范围。
- 需求 3 的"已学范围"按目录册序/章序机械判定，不考虑学校实际教学顺序差异；教师可随时关闭开关回到现状行为。
- 严格进度过滤不排除"无知识点标签"的题；这类题在按知识点精选时本就不会被选中，只在仅按章节浏览时出现，教师人工判断成本可接受。
- 三个需求都不改变分数、掌握度、训练证据与审计规则；只影响题库读取过滤与筛选面板展示。
