# 中文数学题库与 LaTeX 排版：一手来源核查

调查日期：2026-10-02。用途：为本项目 Word 与 LaTeX 排版效果评估提供外部证据。本文是外部源码调查笔记，没有接入外部题库或复制其应用源码。本项目的实施结果与验证边界见[效果评估](question-bank-latex-layout-evaluation-20261002.md)，当前导出规则见[产品文档](../product/KNOWLEDGE_AND_TRAINING.md)。

## 可以支持的结论与证据边界

LaTeX 值得作为数学试卷 PDF 的改进候选。中文中学试卷已有可核查的模板和题库实现，选择题、填空、解答题、图文组合都不是从零实现。但外部项目不能证明本项目的 Word 富文本题目能无损转换，也不能证明任意大图会自动排得更好。下面只把 README 当作作者功能说明，把源码当作具体实现证据；没有运行这些项目，没有用本项目真实数据测试外部服务，没有依据宣传截图给出效果评分。

网页显示 LaTeX 语法的公式，与把整张试卷交给 TeX 引擎生成 PDF，是两条不同路径。KaTeX 的定位是网页数学公式排版，官方另列受支持及不支持的命令；不能仅凭网页公式预览推断纸张、跨页、答题留白的结果。[KaTeX 官方网站](https://katex.org/)、[支持范围](https://katex.org/docs/supported.html)

## 题库与排版项目核查

| 项目 | 与初高中数学的关系 | 核查到的输出路径 | 对本项目的参考价值 |
| --- | --- | --- | --- |
| [JudgePeach/math-question-bank](https://github.com/JudgePeach/math-question-bank)（MathBank） | 面向中学数学教师的本地题库与组卷工作台 | 网页公式预览；本地 XeLaTeX 编译 PDF；可导出排版源码 | 最接近现有产品形态，可参考题目格式、图布局属性和编译诊断，但不能直接认定效果更好 |
| [JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant) | 高中数学题库、检索与组卷 | `.tex` 题文件与 CSV 索引；试卷/讲义模板；TikZ 本地编译预览 | 可参考题目内容与试卷模板分离；其输入已是 LaTeX，转换难度与本项目不同 |
| [xkwxdyy/exam-zh](https://github.com/xkwxdyy/exam-zh) | 中文中小学/高考试卷模板 | XeLaTeX 整卷；独立题目、选项、图文宏包 | 可参考成熟选项测量、图文位置接口；它是模板，不是完整题库系统 |
| [Onion12138/math](https://github.com/Onion12138/math) | 上海初二、初三数学复习讲义 | XeLaTeX 源码与讲义 PDF | 初中图文题的真实手工编排样本；不是自动题库组卷系统 |
| [mathedu4all/bhcexam](https://github.com/mathedu4all/bhcexam) | 面向中国数学教师、用于 Mathcrowd 题库导卷的中文试卷类 | XeLaTeX 试卷；选择、小问、答案、留白命令 | 可参考按实际宽度选列与学生/教师卷切换；公开源码仍有需核对的分支 |
| [ExBook/ExBookie](https://github.com/ExBook/ExBookie) | 通用考试刷题本；初高中专门定位未确认 | 同题内容输出多种 PDF 版式 | 可参考内容与版式分离；固定留白、整题盒子不适合直接照搬 |

以上定位及作者声称的功能分别来自各仓库 README；实际实现的核查结果如下。这些对象包含题库、模板和手工讲义，不能全部算作已完成全自动排版的题库系统。

### MathBank：公式原生存储，但图文规则仍存在

数据库 `Question` 的题干 `content` 和解析 `answer_markdown` 存的是 LaTeX 加 Markdown；图片路径、TikZ、图位置、图大小和逐图布局另外记录。它不是把既有 Word 文档原封不动交给 TeX。[database.py](https://github.com/JudgePeach/math-question-bank/blob/main/mathbank/database.py)

网页编辑器源码对题目卡片调用 KaTeX 的 `renderMathInElement`；PDF 编译源码则调用系统 `xelatex`，跑两遍解析页码等引用，处理错误与超时。网页预览与 PDF 因而不能视为同一渲染结果。[editor.js](https://github.com/JudgePeach/math-question-bank/blob/main/static/js/editor.js)、[paper_helper.py](https://github.com/JudgePeach/math-question-bank/blob/main/mathbank/paper_helper.py)

本次可读的 `paper_helper.py` 实现包含以下明确取舍：表格内图片限制宽度且高不超过 4 cm；保留原图位置时设置最大 9 × 6 cm；默认右侧图区域 5.2 cm，题干宽度减 5.8 cm；解答题留白按 cm 指定，部分图位置会扣减 3.2 cm。它还会把 A–D 文本选项整理为 `choices`。这些是源码中一组具体规则，不能当作所有版本及所有题型的统一策略；同仓库还有更细的图片布局字段。[paper_helper.py](https://github.com/JudgePeach/math-question-bank/blob/main/mathbank/paper_helper.py)

作者提供 Windows 便携应用包，但 README 明确 PDF 导出和 TikZ 重绘仍依赖另外安装 LaTeX 工具链。仓库 LICENSE 为 AGPL-3.0；本调查仅参考行为，没有复制应用源码。其“100% 拆解成功”等宣传没有本调查的独立验证，不能写成本项目改进后的承诺。[README](https://github.com/JudgePeach/math-question-bank/blob/main/README.md)、[LICENSE](https://github.com/JudgePeach/math-question-bank/blob/main/LICENSE)

### MathCyclus：输入已整理为 TeX，漂亮样卷包含人工控制

作者工作流从按板块/年份组织的 LaTeX 题文件出发，用 CSV 做索引；题干、答案与解析用环境分开。启动需要 Python 与依赖，TikZ 预览需要本机 XeLaTeX。源码把 TikZ 编译成 PDF 后用 PyMuPDF 转 PNG，并有编译失败、缺编译器、超时的返回。[README](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant/blob/main/README.md)、[tikz_ops.py](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant/blob/main/utils/tikz_ops.py)

自定义 `choices` 会测量选项最大宽度，再选 1、2 或 4 列，也允许指定列数。字体、分数行高、题目环境仍有项目自己的处理；这说明 TeX 提供可测量的基础，产品仍需决定排版规则。[MathCyclus_book.cls](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant/blob/main/MathCyclus_book.cls)

试卷类模板实际使用 `exam-zh`。本次读到的 2025 数学样卷含显式 `\newpage`、按 `\linewidth` 分配的 `minipage`，并有用 `\phantom` 文本控制图片位置的写法；图片可指定 6 cm 宽。这是有人工编排的样卷，不能证明题库随机抽题后不需要调整。[试卷类模板.tex](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant/blob/main/Test%20Paper%20Group/%E4%B8%BB%E9%A2%98%E6%A8%A1%E6%9D%BF/%E8%AF%95%E5%8D%B7%E7%B1%BB%E6%A8%A1%E6%9D%BF/%E8%AF%95%E5%8D%B7%E7%B1%BB%E6%A8%A1%E6%9D%BF.tex)

README 声称 MIT，但根目录没有显示 LICENSE，直接访问 `main/LICENSE` 返回 404。本文只能确认作者的声明，不能确认完整授权文件及题目素材的授权范围。[仓库根目录与 README](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant)

### exam-zh：可以复用的排版能力与版本风险

CTAN 登记它为中文试卷类与宏包集合，包含选择、填空、解答及答案控制，需要 XeLaTeX，许可证 LPPL-1.3c。题库存储、题目导入与 Word 转换不属于它。[CTAN 项目页](https://ctan.org/pkg/exam-zh)

`exam-zh-choices.sty` 将每个标签和选项分别放进 `hbox` 实际测宽，取最大宽度。设可用总宽为 T、最大选项宽为 W、列间距为 G；标签不在底部时 W 还计入标签宽及标签间距。先计算 `floor((T+G)/(W+G))`，至少一列；再从默认最大四列按 4→2→1 下降到可容列数以内。最终每项正文宽度为 `(T-(n-1)G)/n`，再扣标签占宽。可借鉴实际测宽及标签预算，不能用字符数猜测公式宽度。[choices 源码](https://github.com/xkwxdyy/exam-zh/blob/main/exam-zh-choices.sty)

`exam-zh-textfigure.sty` 把文字 `varwidth` 和图片分别装进 `hcoffin`，拼接后整体输出。由这一装盒流程推断，题文配图块内部不能自动跨页。自动文字宽为 `linewidth-实际图宽-column-gap`，不足时钳到默认 `.35\linewidth`；源码没有因此自动改成上下布局，故大图与最小文字宽相加仍可能超出版心。建议只把短题图文组合视为候选；长题保留可分页段落、大图作为独立块。这是源码分析，未执行该宏包验证。[textfigure 源码](https://github.com/xkwxdyy/exam-zh/blob/main/exam-zh-textfigure.sty)

维护信息存在不一致：README 仍有 2024-04-26 无限期停止维护警告；CTAN 当前登记 0.3.6、2026-08-01；本次读取的 GitHub 图文模块标记 0.3.8、2026-08-21。不能简单称“停更”，也不能据新版本保证持续维护。若试用，应固定经过实测的版本，核对手册、模板、宏包是否配套。[README](https://github.com/xkwxdyy/exam-zh/blob/main/README.md)、[CTAN](https://ctan.org/pkg/exam-zh)、[图文模块](https://github.com/xkwxdyy/exam-zh/blob/main/exam-zh-textfigure.sty)

### Onion 初中讲义：能体现数学排版，但大图仍由作者决定

README 明确为上海初二、初三复习讲义，包含函数、三角形、四边形、圆、统计等章节，采用 XeLaTeX。授权说明为 LPPL，校徽例外；仓库有源码及 PDF。它证明有人使用 TeX 完成初中数学讲义，不证明自动组卷功能。[README](https://github.com/Onion12138/math/blob/main/README.md)、[仓库文件](https://github.com/Onion12138/math)

实际 `test.tex` 使用普通数学公式、`tabular` 表格、3 cm/4 cm 图片宽度、`figure[H]` 固定图片位置、`minipage` 并排图，以及显式 `\clearpage`。题目与图片可在不同环境中；这些手工决策必须与自动组卷的效果区别开来。源码还包含 Unicode 数学字母等输入，是否所有字形都正确输出需要实际编译和视觉核对。本次 PDF 原始链接因浏览工具不支持其返回的二进制类型而未完成逐页视觉查看。[test.tex](https://github.com/Onion12138/math/blob/main/test.tex)

### BHCexam：真实题库导卷模板仍需核对分支

官方将 BHCexam 定位为中国数学教师试卷类，并说明 Mathcrowd 题库使用它导出 PDF；许可为 LPPL-1.3 或后续版本。GitHub 源码标记 1.9、2025-05-29，CTAN 登记 1.8、2024-10-15，使用时应固定实际测试版本。[项目 README](https://github.com/mathedu4all/bhcexam)、[CTAN 登记](https://ctan.org/pkg/bhcexam)

`fourchoices` 测量带标签选项的自然宽度，最大值超过 `.4\linewidth` 用一列、超过 `.2\linewidth` 用两列，否则四列，输出为 `tabularx`；这些阈值是模板选择，并非任意大公式的保证。`solution` 参数指定答题留白；答案可显示或隐藏。当前 `sixchoices` 分支未测量第六项宽度，四列输出分支 E 项使用 `#2`，而其他分支使用 `#5`。这是直接可见的源码不一致，未运行验证。适合参考测宽与答案控制思想，不能把公开代码等同于可靠效果。[BHCexam.cls](https://github.com/mathedu4all/bhcexam/blob/master/BHCexam.cls)

### ExBook：同题多版式可以借鉴，整题不可拆需避免

仓库的 MIT 许可证可核实，允许按其条件使用或修改软件；题目素材的授权仍应另外核对。它是刷题本文档类，不是现成的题目数据库或 Word 转换器。[README](https://github.com/ExBook/ExBookie/blob/main/README.md)、[LICENSE](https://github.com/ExBook/ExBookie/blob/main/LICENSE)

`ExBook.cls` 的 A4 标准、宽松版将整题装进 `minipage` 并加 2.5 cm、7.5 cm 固定留白；单题版主动换页。解析使用可跨页的 `tcolorbox`，但被外层整题盒子包住时不能据此推断整题可跨页。图片命令 `imgin` 默认 `scale=.38`，没有按版心同时限制宽高。可参考同一题目内容切换版式，不能直接复用其固定缩放或整题装盒策略处理大图、超长题。[ExBook.cls](https://github.com/ExBook/ExBookie/blob/main/ExBook.cls)

## xsim：借鉴结构化题目与答案，不替换题库存储

官方 `xsim.sty` 以题型、内部 id、属性为键，独立保存 `exercise-body` 与 `solution-body`；官方手册又区分内部 id、用户提供的 ID 及显示题号。它还提供集合、难度标签及题目/解答模板，许可为 LPPL-1.3c 或后续版本。可借鉴“内容、身份、模板分开”，目前没有证据要求本项目引入整套 xsim 或另建题库。[xsim.sty 源码与许可](https://github.com/cgnieder/xsim/blob/master/code/xsim.sty)、[官方手册源码](https://github.com/cgnieder/xsim/blob/master/doc/xsim-manual.tex)、[集合示例](https://github.com/cgnieder/xsim/blob/master/doc/examples/xsim.collections.tex)

`printsolutions` 按题型输出，`printallsolutions` 按题目在文档中出现顺序输出，单题可用 `printsolution` 指定题型与 ID。官方手册明确随机抽题命令 `printrandomexercises` 在 XeLaTeX 中不可用。建议仍由应用完成选题，按已选题快照顺序生成题干与对应答案；考试分值、训练达成点沿用现有语义。该建议是对源码的应用判断，未接入产品。[输出命令源码](https://github.com/cgnieder/xsim/blob/master/code/xsim.sty)、[官方手册的随机抽题与答案输出章节](https://github.com/cgnieder/xsim/blob/master/doc/xsim-manual.tex)

## 垂直合并表格：multirow 与 longtable 的可实施边界

`multirow` 作者文档明确支持在 `longtable` 中跨行单元格，但合并区域不能在页底被拆开；示例要求中间行结束用 `\\*`，末行用普通 `\\`。若后面接 `\cline`，作者要求使用宏包 `longtable` 选项修复断页限制失效。该宏包为 LPPL-1.3 或后续版本；`longtable` 为 LPPL-1.3c 或后续版本，可依其许可使用宏包。[multirow 官方文档与源码](https://github.com/pietvo/multirow/blob/master/multirow.dtx)、[multirow.sty](https://github.com/pietvo/multirow/blob/master/multirow.sty)、[longtable 官方登记](https://ctan.org/pkg/longtable)

面向原型的生成规则，尚须本地验证：每个垂直合并占据行区间 `[start,end]` 时，将边界 `start` 至 `end-1` 设为禁止断页；所有合并单元格取边界并集，因此不同列交错合并形成连续不可拆区域。对应行尾用 `\\*`，区域外恢复普通 `\\`。合并覆盖位置留空，内部横线只画未被合并覆盖的列，避免 `\hline` 穿过合并正文；使用 `\cline` 时加载 `\usepackage[longtable]{multirow}`。这些规则是把作者示例推广到结构化表格的推导，不是已经验收的产品实现。[作者 longtable 用法](https://github.com/pietvo/multirow/blob/master/multirow.dtx)

官方 `longtable` 源码说明分页机会位于行与行之间；据此单行不能被自动拆成两页，内嵌小 `tabular` 也不会给外层单元格增加分页机会。长表应处在主文档的单列排版流，不能包在整题 `minipage`、浮动体或另一张表的单元格中期待跨页；官方明确不兼容双栏及改变输出例程的多栏环境。若单行或禁止断页的合并区域高于一页可用高度，保持合并与自动跨页无法同时满足，应检测并明确返回受限原因，不能默默截断、拆坏合并或缩到不可读。关于超页组和嵌套的处理是基于行级分页及装盒机制的工程判断。[longtable 官方源码](https://github.com/latex3/latex2e/blob/develop/required/tools/longtable.dtx)、[官方手册](https://mirrors.ctan.org/macros/latex/required/tools/longtable.pdf)、[环境限制](https://ctan.org/pkg/longtable)

`multirow` 还明确它不知道其他行的实际高度，多行段落、大公式或大图会使纵向定位需要调整。固定列宽可用 `width={=}` 沿用 `p` 等段落列宽，而 `width={*}` 为自然宽、不自动折行；同时横向、纵向合并时，必须将 `multirow` 放在 `multicolumn` 内。原型可优先保留来源顶/中/底对齐；若用顶对齐，也必须检查重叠、边框和正文行高，不能只检查 `multirow` 命令是否生成。[multirow 的宽度、组合与 tall entries 说明](https://github.com/pietvo/multirow/blob/master/multirow.dtx)

## 答题留白：规则长度与实际出页空间要分别验证

ExBook 的固定留白和整题装盒不能直接解决长材料、页底剩余空间不足；本项目分页原型因此保留每题留白总高度，允许整行留白分段跨页。分段大小、哪些内容一起移页，是原型的工程选择，不是外部宏包自动给出的题库标准。LaTeX 内核的空间命令定义可供核查具体长度和禁止换页行为，但仅检查生成源码不足以证明最终页面保留了可用答题区域。[ExBook 源码](https://github.com/ExBook/ExBookie/blob/main/ExBook.cls)、[LaTeX 内核空间命令](https://github.com/latex3/latex2e/blob/develop/base/ltspace.dtx)

zref 作者手册的 savepos 章节说明：页面位置在出页时才确定，位置以 sp 为单位记录，纵坐标由下向上增加；按页的属性也要延迟记录，不能以生成正文时的页计数代替实际落页。实验直接使用引擎的位置命令，在每段留白的起止点记录页码及坐标，再核对实际高度与版心边界，没有引入 zref 到正式产品。该核查证明本机样本中的留白总量保持，不代表跨页后使用体验已完善；续答题号和续材料提示仍需验证。[zref 官方手册 savepos 及属性延迟记录](https://tug.org/docs/latex/zref/zref.pdf)

## 官方能力：哪些问题能改善，哪些仍需规则

| 题目特征 | 官方能力或边界 | 应验证的实际效果 |
| --- | --- | --- |
| 根式、分数、上下标、联立方程、多行演算 | `amsmath` 提供数学结构和对齐环境；多行公式如何分行仍需结构化源码。[amsmath 手册](https://www.latex-project.org/help/documentation/amsldoc.pdf) | 公式转换后意义一致、字形齐全、行高合适、长式没有溢出版心 |
| 中文与公式混排 | CTeX 支持中文标点间距、字体配置和中文章节样式，多引擎可用。[CTeX 官方登记与文档入口](https://ctan.org/pkg/ctex) | 中文字体是否可离线取得，中文与数学字高协调，字体替换是否改变分页 |
| 宽图、竖图、扫描图 | `graphicx` 的 `width`、`height`、`keepaspectratio` 可保证缩放不超过给定边界且不变形。它不负责恢复原图细节。[graphics 官方手册 §4.4](https://mirrors.ctan.org/macros/latex/required/graphics/grfguide.pdf) | 图中文字能否读清，缩小还是占整行/整页，题干与图是否仍紧邻 |
| 跨页长题、长解析 | `amsmath` 默认限制多行公式跨页；可用 `\allowdisplaybreaks` 等放开，但 `split`、`aligned` 等整体盒子仍不可拆。[amsmath 手册 §3.9](https://www.latex-project.org/help/documentation/amsldoc.pdf) | 避免整块推到下页造成大空白，也避免拆在关键推导中间 |
| 题首与题干保持在一起 | `needspace` 可在页底空间不足时先换页；它需要给定需要的高度。[needspace](https://ctan.org/pkg/needspace) | 题号不落单，小题开头不孤立；超一页的题不能整题禁止分页 |
| 长表和横向合并 | `longtable` 保留 `tabular` 的多数能力，包括横向跨列，并允许表在行间分页；不能在一行内部自动分页，部分布局需多次编译才能对齐。[LaTeX 项目维护的 longtable 手册](https://mirrors.ctan.org/macros/latex/required/tools/longtable.pdf) | 保留原列及合并关系，单元格图片、边距与边线计入总宽；特别高的一行及纵向合并需另处理 |
| 小问、分值、续题、页眉页脚 | `exam` 支持多层小问、分值与续题相关页眉页脚；中文试卷还需确认样式。[exam 官方登记](https://ctan.org/pkg/exam) | 普通考试分值与训练达成点语义不变；留白与最终页数可靠 |

将 PNG/JPEG 放入 LaTeX PDF，不会使它自动变成可任意放大的几何矢量图。外部题库的 TikZ 重绘是额外流程；重绘后的点、线、角标和文字是否忠于原题，必须单独核对。MathCyclus 的源码即把图形预览作为独立编译路径。[tikz_ops.py](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant/blob/main/utils/tikz_ops.py)

## Windows 与离线运行

完整 TeX Live 支持 Windows 和便携安装，但完整发行版需数 GB；官方说明安装目录应避免非 ASCII 字符。不能把完整 TeX Live 的体积直接当作本项目必然增加的成本，也不能据别人提供 Windows 应用压缩包认定编译资源已全部打包。[TeX Live 2026 官方指南](https://tug.org/texlive/doc/texlive-en/texlive-en.html)

本项目已存在 Tectonic 路径这一事实由本地调查提供，外部研究不要求新增整套 TeX Live。Tectonic 官方说明它基于 XeTeX 与 TeX Live；命令支持指定本地/远端 `--bundle`，并用 `--only-cached` 只读取已缓存资源。因而下一步应核查现有引擎、宏包与字体是否构成可重现的离线集合；只有可执行文件存在，不能证明新模板会离线编译成功。[Tectonic 官方概览](https://tectonic-typesetting.github.io/book/latest/)、[V1 命令手册](https://tectonic-typesetting.github.io/book/latest/ref/v1cli.html)

字体查找与 TeX 资源缓存也需要分别诊断。Fontconfig 官方说明 `FONTCONFIG_FILE` 指定的是配置文件；XML 中的 `dir` 才是字体目录，`cachedir` 是字体缓存目录。fontspec 官方文档支持按字体文件名加载，并用 `Path` 指定位置；Tectonic 的 Unicode 示例也按文件名选择字体。Windows 已有宋体或 Times 文件，不等于 TeX 默认 Latin Modern 字体及其度量资源已完整缓存。本调查没有独立重现本项目编译错误，以上是排查依据，不是经验证的修复方案。[Fontconfig 用户文档](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)、[fontspec 字体选择官方源码](https://github.com/latex3/fontspec/blob/develop/fontspec-doc-fontsel.tex)、[Tectonic Unicode 官方示例](https://tectonic-typesetting.github.io/book/latest/getting-started/unicode.html)

KaTeX 可以自行托管 JS、CSS 与字体，无需依赖 CDN；官方要求字体目录与 CSS 相邻。这可解决网页公式预览的离线资源问题，但纸张分页仍属于另一套排版流程。[KaTeX 浏览器部署文档](https://katex.org/docs/browser.html)

## 对本项目评估的约束

以下是外部证据对本项目的评估约束，不表示每项均已完成：

1. 优先试用本项目已有题目内容转换与 Tectonic 能力，避免先另建题库、迁移真实题目或换整套运行环境。
2. 分别评价“Word 富文本/公式转 TeX 的内容保真”和“PDF 的视觉排版”；源码预检通过、编译成功、页面好看是三个不同结论。
3. 外部题库源码中的图片宽高、列数和留白只能作为实验起点。用同一道题、同一纸张与字大小比：纯文字、复杂公式、四种选项长度、小图、大宽图、竖长图、多图、表格/合并单元格、小问、长题、长解析、页底剩余空间不足。
4. 记录图片最小可读文字、题图距离、题号落单、公式越界、表格截断、页数与大空白、答题空间、生成时间和失败原因。展示普通案例与失败案例，不能只展示成功样卷。
5. 在转换与分页都完成本地验证之前，可以称“有依据的改进候选”，不能称“替换 Word 后已解决全部题型”。教师继续在 Word 中编辑的需求，应作为选择导出格式的实际成本。

## 未核实的内容

- 未运行外部项目，没有把它们导出的 PDF 与本项目同题逐页对照；公开样卷不代表本项目随机组卷结果。
- 未确认 MathCyclus 独立许可证文件、题目素材版权或其所有模板的完整依赖。
- 未确认这些项目面对跨页超长题、极大竖图、复杂合并表格时的成功率；所读源码不足以推出这些结论。
- GitHub `main` 链接会变化，网页工具部分源码是已缓存页面。正式采用时必须固定源码版本并重新运行本地验证。
- 未调用真实模型，未上传本项目题库、图像或学生数据，未发生模型费用。
