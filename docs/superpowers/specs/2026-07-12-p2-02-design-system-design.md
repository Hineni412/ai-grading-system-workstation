# P2-02 Token、Element 主题与基础控件设计

## 1. 目标

把 `docs/ui/STYLE.md` 中已经确认的颜色、字号、间距、圆角、阴影、动效和状态语义落实为 Vue 前端唯一设计 Token，并用这些 Token 统一 Element Plus 与 P2-02 基础控件。交付一个可直接运行的组件展示页，用于验证长中文、极端数据、键盘焦点、对比度和规定视口，不迁移任何业务页面。

## 2. 范围

本包包含：

- CSS Variables 形式的产品 Token；
- Element Plus 全局主题映射与中文配置；
- 按钮、输入字段、状态徽章、空态、加载态、错误态和反馈组件；
- 展示全部状态与极端内容的组件展示页；
- Token 守卫、组件单测、对比度检查和 Playwright 多视口检查；
- 前端开发说明中关于 Token 与基础控件的使用规则。

本包不包含：

- App Shell、路由导航、全局 session 上下文；
- API Client、Job Store 或任何后端调用；
- 阅卷、考试、题库、训练等业务页面；
- 深色模式、品牌换肤或运行时主题切换；
- 旧 `pages_shared/shared_styles.py` 蓝紫色板迁移；
- 营销 Hero、渐变、大圆角、卡片堆叠或新增 UI 依赖。

## 3. 已确认的视觉决策

- `docs/ui/STYLE.md` 是视觉与交互的唯一权威来源。
- 主强调色固定为 `#2563EB`，不从旧 Streamlit 样式继承颜色。
- 只提供浅色主题；普通层级优先使用背景和边框，阴影只用于脱离文档流的浮层语义。
- 间距只使用 `4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48`。
- 圆角按 `4 / 6 / 8 / 12` 收敛；按钮和输入框使用 6px。
- 状态必须同时包含文字，不能只靠颜色表达。
- 教师状态和 AI 状态使用独立且克制的语义色。

## 4. 方案比较与选择

### 方案 A：Token 驱动的轻量封装（采用）

产品 Token 通过 CSS Variables 维护，并映射到 Element Plus 的 `--el-*` 变量。按钮和输入控件复用 Element Plus；只有产品语义超出 Element Plus 原生表达时才封装组件，例如状态徽章和异步状态面板。

优点是单一来源清楚、不增加构建依赖、后续业务页能直接复用，同时避免为每个 Element Plus 控件再造一层无业务价值的包装。

### 方案 B：全部直接使用 Element Plus

只覆盖全局颜色和圆角，页面直接组合原生组件。实现量最小，但状态语义、错误恢复、长文本行为和测试选择器会散落到后续页面，无法形成稳定设计系统契约。

### 方案 C：全面包装 Element Plus

为按钮、输入、标签、提示等全部创建同名产品组件。入口统一，但包装层会重复 Element Plus API，增加类型、插槽和升级维护成本，不符合本包的最小范围。

## 5. 文件与职责

### 样式层

- `frontend/src/styles/tokens.css`：唯一保存产品级颜色、字体、字号、行高、间距、尺寸、圆角、边框、阴影、动效和层级值。
- `frontend/src/styles/element-theme.css`：只把产品 Token 映射到 Element Plus CSS Variables，并收紧按钮、输入、标签、提示等基础控件的交互状态。
- `frontend/src/styles/base.css`：全局盒模型、页面背景、字体、文字渲染、可见焦点和减少动效偏好的基础规则。

除 `tokens.css` 外，P2-02 新增的 Vue/CSS 文件不得直接声明十六进制、RGB/HSL 色值；常用间距、圆角和阴影必须引用 Token。少量只用于布局计算的百分比、视口单位和组件固有尺寸不视为设计 Token。

### 组件层

- `AppField.vue`：常驻标签、可选说明、错误信息和字段关联；通过默认插槽承载 Element Plus 输入控件，不复制输入控件 API。
- `StatusBadge.vue`：接受受控语义 `neutral | info | success | warning | danger | ai | teacher`，始终显示文字，并为辅助技术提供可读标签。
- `StatePanel.vue`：统一 `empty | loading | error` 三类状态；说明原因和影响范围，错误态可暴露重试动作，加载态使用贴近最终布局的骨架。
- `FeedbackBanner.vue`：统一 `info | success | warning | error` 反馈；支持标题、说明、可选操作和可关闭行为，错误反馈明确是否已保存以及下一动作。
- `ComponentShowcase.vue`：只用于设计系统展示和验收，覆盖按钮层级、输入状态、语义徽章、异步状态和反馈状态。

按钮直接使用 Element Plus 的主要、普通、文字、危险、加载和禁用形态。展示页保证每个操作区最多一个主要按钮，并用具体结果文案而非抽象“确定”。

## 6. Element Plus 集成

`main.ts` 只导入 Element Plus 基础样式和展示页实际使用的 Button、Input、Icon 组件样式，再按顺序导入 `tokens.css`、`element-theme.css` 和 `base.css`。组件使用 TypeScript 直接导入，根组件通过 `ElConfigProvider` 提供中文 locale，并保持默认 `el` namespace；不全量注册组件库，不引入 Sass 与自定义 namespace 的额外编译约束。

主题映射至少覆盖：

- primary、success、warning、danger、info 色；
- 页面、普通表面、填充、遮罩、文字和边框层级；
- 组件圆角、控件高度、字体和过渡；
- hover、active、focus-visible、disabled、invalid 状态；
- Message、Alert、Tag、Empty、Skeleton 的基础视觉。

不修改 Element Plus 源码，也不通过行内样式覆盖组件主题。

## 7. 展示页结构与响应式

展示页是纵向文档式工作区，而不是应用外壳。顶部只说明“设计系统展示”和适用范围；下方按“基础 Token、按钮、输入、状态徽章、空/加载/错误、反馈”分区。普通分区使用标题、分隔线和留白组织，不套装饰性卡片。

长文本样例至少包含：

- 超长考试名称和学生姓名；
- 带小数和边界值的分数；
- 多句错误影响说明；
- 超长知识点/错因标签；
- 禁用、只读、必填和校验失败输入。

在 1440×900、1280×800、1024×768、768×1024、390×844 五种视口下不得出现页面级横向溢出。窄屏允许控件纵向排列，但主要操作和状态说明不能被截断。

## 8. 可访问性与交互

- 所有交互元素支持键盘访问并显示清楚的 `:focus-visible` 轮廓。
- 输入错误通过 `aria-invalid`、`aria-describedby` 和紧邻错误文字关联。
- 加载状态使用 `aria-busy`；重要反馈使用与严重程度匹配的 live region。
- 状态徽章和反馈均包含文字，不使用单独色点表达含义。
- 主要文字、次要文字、强调按钮和状态前景/背景组合通过 WCAG 2.1 AA 对比度自动检查；普通文字至少 4.5:1，大文字至少 3:1。
- 遵守 `prefers-reduced-motion`，减少动效时关闭非必要过渡。

## 9. 测试与验收

### 静态守卫

- 验证全部必需 Token 存在且主强调色固定；
- 扫描 P2-02 新增样式和组件，拒绝 Token 文件之外的散落颜色值；
- 验证间距、圆角与阴影只来自允许的 Token；
- 验证未引入新的直接依赖或浮动版本。

### 组件单测

- `AppField` 正确关联标签、帮助和错误信息；
- `StatusBadge` 对每种受控语义输出文字和稳定标识，非法语义在 TypeScript 阶段被拒绝；
- `StatePanel` 的空、加载、错误与重试分支行为正确；
- `FeedbackBanner` 的严重程度、操作和关闭事件正确；
- 展示页覆盖加载、禁用、错误、长中文与极端数据。

### 浏览器验收

- 五种规定视口无页面级横向溢出；
- Tab 导航能到达主要交互，焦点样式可见；
- 输入错误文字与字段关联；
- 展示页没有渐变、营销 Hero、大圆角卡片堆叠和多个同级主要按钮；
- 浏览器控制台无应用错误。

### 工程门槛

运行 lint、typecheck、unit、build、Chromium e2e、相关 Python 守卫测试和 `tools/smoke_check.py --skip-tests`。所有测试只使用展示数据和临时路径；真实两库只比较大小、UTC 修改时间和 SHA-256，不能写入。

## 10. 用户自测与回退

本包是明显可见的前端批次，用户自测类型为 `quick`。自动验证与独立复审通过后，提供版本化短测清单，让用户只检查整体基调、可读性、状态辨识、焦点和窄屏展示；不要求验证业务流程。

P2-02 尚未成为生产 UI，回退时可整体回退该包的前端样式、组件和展示页提交。Streamlit 入口、FastAPI、数据库和真实 `user_data/` 均不受影响。
