# ImageGen 提示词记录

本组概念图使用内置 ImageGen 生成，分类为 `ui-mockup`。每张图都由“共同规格 + 页面规格”组成；02、05、08 另有一次定点修正。

## 共同规格

```text
Use case: ui-mockup
Asset type: shippable desktop web application screen, 16:9 landscape, designed for a 1920×1080 Windows browser.
Product: Chinese “AI 阅卷系统” teacher workstation, module “备课工作台”.
Style/medium: realistic production UI screenshot, polished and implementable, not concept art.
Existing visual system: app background #F4F5F6; white surfaces #FFFFFF; subtle surface #F7F8F8; borders #E2E4E7; primary text #1C2733; secondary text #5C6672; one primary accent #135E6B; selected background #E8F1F1; success #2F7A55; warning #9A6718; danger #B04444. Inter with PingFang SC / Microsoft YaHei. Page title 24px, section title 18–20px, body 14px, utility 12–13px. 8px controls, 12px panels, light borders, almost no shadows, disciplined 4/8/12/16/24 spacing.
Global shell: a 56px collapsed white icon rail on the far left with a small teal “AI” mark; familiar line icons; “备课工作台” active in teal. Quiet white 68px top bar with current lesson context and local status. Main content uses almost full width.
Shared module identity: compact header “备课工作台”, safety label “原课件不会被覆盖”, persistent five-step ruler “选课时 → 核资料 → 定方案 → 审课件 → 上课包”. Active step highlighted; completed steps use subtle check marks.
Visual signature: the preparation ruler plus a narrow lesson spine/evidence page tabs inspired by a teacher’s lesson-plan margin; these encode real structure and provenance.
Constraints: all visible UI copy must be clear Simplified Chinese; use only quoted labels; no gibberish, no extra navigation, no personal data, no real file paths. No gradients, no giant hero, no chat bubbles, no marketing cards, no dashboard theatrics, no glassmorphism, no illustrations, no 3D, no watermark. Dense but calm. Teacher decisions must visually outweigh assistant suggestions. Document/page preview receives the most visual space when present.
```

## 01 学期与课时总览

```text
Active step: “选课时”. Header: “备课工作台” and “八年级上册 · 2026—2027 第一学期”.
Three-area working layout, not a card grid.
Left 300px “学期与课时”: expandable tree with “第十三章 轴对称”, “13.1 轴对称”, “13.2 画轴对称图形” and child lessons. Highlight “第2课时 · 利用轴对称设计图案”; statuses “已上课”, “已备好”, “待准备”; actions “新增课时”, “调整顺序”.
Center, largest, “本周备课”: dense Monday–Friday chronological list; slim progress “计划 48 · 已上 23 · 已备好 2 · 待准备 23”.
Right 320px “本节准备度”: “教材 28—31页 · 已确认”, “参考课件 12—24页 · 待核对”, “教辅 2套 · 已解析”, “答案 1处 · 待验证”, “历史定稿 1版”; primary “开始核对资料”, secondary “查看历史定稿”.
```

## 02 资料库与课时映射

```text
Active step: “核资料”. Context: “八年级上册 / 第十三章 / 13.2 画轴对称图形 / 第2课时”.
Four functional columns.
Column 1 “资料角色”: “教材 1”, “参考课件 3”, “普通教辅 2”, “日常作业教辅 1”, “答案册 2”, “补充资料 1”; action “导入受控副本”.
Column 2 “本学期资料”: compact file rows with “已解析 · 176页”, “待核对”, “有新版本”, “需重新定位”.
Column 3, largest, “页面预览”: crisp axis-symmetry textbook page, toolbar “第 28 / 176 页”, zoom and thumbnail strip; “本地解析，不产生模型费用”.
Column 4 “课时映射建议”: “确认前不会改课时树”; “13.2 画轴对称图形 · 第2课时”, “教材 28—31页”; actions “接受映射”, “修改范围”, “拒绝”.
```

定点修正：把模型自行生成的 `八上13.2同步练习.xlsx` 与 Excel 图标只改为 `八上13.2同步练习.pdf` 与 PDF 图标，其余不变。

## 03 本节资源确认

```text
Active step: “核资料”. Context: “13.2 画轴对称图形 · 第2课时 · 45分钟”.
Left “本节来源”: “教材 28—31页”, “基准课件 12—24页”, “普通教辅 45—47页”, “日常作业 61—62页”, “答案册 88页”, “匿名学情 2条”.
Center, largest, “打开原页核对”: source preview, thumbnail strip, source tabs, toolbar “教材 · 第29页”; actions “上一页”, “下一页”, “确认本页”, “框选题目”.
Right “本节资源包”: ordered provenance checklist, “进入分析前检查”, disclosure “查看将发送的范围”, note “未确认资料不会发送”; primary “冻结本节资源包”, secondary “保存并稍后继续”.
No AI recommendations yet.
```

## 04 全屏题目与答案框选

```text
Active step: “核资料”. Context: “13.2 画轴对称图形 · 第2课时 / 普通教辅 · 第45页”.
Top tool row: “题目模式”, “答案模式”, “跨页继续框选”, “完成本页”; “题目模式” active.
Left “已确认页面”: thumbnails 45, 46, 47.
Center, largest: high-resolution math workbook page; two teal rectangles “题目区域 1”, “题目区域 2”; one dashed rectangle being dragged.
Right “题目与答案区域”: two ordered question regions, one answer region from page 88 with “待教师核对”; form “题号 7”, “难度 中等”, “课堂用途 引导练习”, “预计 6 分钟”, “教学重点 识别对称轴”.
Bottom: “公式、图形和复杂排版始终引用原图”; “放弃编辑”, “保存候选题”, “确认答案匹配”.
```

## 05 证据选择与资源包冻结

```text
Active step: “定方案”. Context: “13.2 画轴对称图形 · 第2课时 · 资源包准备”.
Left “已确认来源”: textbook, baseline PPT, workbooks and answer book; “候选题 3道 · 已验证答案 3道”; “来源有更新时只会生成新版本”.
Center “可选教学证据”: “题库证据”, “历史考试证据”; “未选择时不会读取”; privacy strip “只保存班级汇总，不保存姓名、学号、答卷图片”.
Right “本次备课设置”: class, overall context, knowledge scope; baseline-PPT range intent; compact “我的课件改编偏好”; separate “保存为个人默认” and “仅用于本次”. Summary “将冻结 5类资料 · 3道候选题 · 2条匿名证据”; “冻结为资源包 v3”, “下载安全清单”.
```

定点修正：把“参考课件范围意图”改为只包含 `基准课件 12—18页 / 保留` 和 `基准课件 19—24页 / 候选删除` 两行，不再错误列出教材、教辅和答案册。

## 06 课堂草稿与容量审核

```text
Active step: “定方案”. Context: “13.2 画轴对称图形 · 第2课时 · 资源包 v3 已冻结”.
Notice: “本地模板 · 不调用模型 · 不产生费用”.
Left “草稿版本”: “v1 本地模板”, “v2 教师修改”, “v3 当前编辑”; sections “知识目标”, “重难点”, “预计困难”.
Center “课堂流程与容量”: “43 / 45 分钟”; “课堂流程 30 · 课堂题 9 · 机动 4”; editable rows “导入 4”, “探索 8”, “例题 8”, “练习 6”, “总结 4”; source citations; “课堂题取舍” decisions “进入课堂”, “备用”, “移到课后”.
Right “依据与不确定项”: “教材依据”, “参考课件依据”, “教辅共同题型”, “匿名学情”, “教师决定”; “当前无可用学情依据”; “仍有 2 分钟余量”.
Bottom: “调整环节或题目后会立即重算”; “保存为新草稿版本”; “确认此版本并生成改编计划”.
```

## 07 逐页 PPT 改编审核

```text
Active step: “审课件”. Context: “13.2 画轴对称图形 · 第2课时 / 改编计划 v4”.
Summary: “原 28 页 → 建议 24 页”, “已决定 15 / 15”, “自动执行 13 · 拒绝 1 · 人工处理 1”; “批量批准 5 项低风险删除”.
Left “幻灯片”: thumbnails and “保留”, “删除”, “改写”, “新增”, “人工处理”; slide 18 selected; “计划 v4”.
Center “第18页改编对照”: “原页” and “建议执行后”, removing excess practice while preserving a teaching example and page style.
Right “逐项决定”: “整页删除 · 第18页 · 低风险 / 批准”; “新增文本框 · 第21页 · 中风险 / 拒绝”; “复杂组合对象 · 第22页 · 仅人工处理 / 已加入人工清单”; actions as appropriate “批准”, “拒绝”, “继续复核”, “编辑备注”.
Bottom: “当前只审核计划，尚未打开 WPS；源课件变化时计划自动失效”; “导出人工修改清单”, “保存新计划版本”, “完成审核并进入生成”.
```

## 08 WPS 生成、课件版本与完整上课包

```text
Active step: “上课包”. Context: “13.2 画轴对称图形 · 第2课时 / 定稿 v3”.
Execution rail complete: “创建隔离副本 → WPS 执行 → 全页验证 → 发布新版本”; “源课件未改变”.
Left “课件版本”: “v3 定稿 · 当前”, “v2 已生成”, “v1 草稿”; “上课包版本”: “包 v2 · 当前”, “包 v1”; “恢复为当前版本”.
Center “定稿课件预览”: large axis-symmetry slide; “第 1 / 24 页”. “完整上课包” contains exactly `lesson-slides.pptx`, `class-exercise.pdf`, `teacher-answer.pdf`, `lesson-flow.pdf`, `sources.json`, `preflight.json`, `manifest.json`; “离线检查通过”.
Right “WPS 验证报告”: “重开成功”, “24页全部渲染”, “放映检查通过”, “主题与母版保持”, “受保护对象未意外变化”; “机器用时 4分12秒 / 5分钟”, “模型调用 0”, “WPS执行 1”, “技术重试 0”; “下载完整上课包”, “下载课件”, “派生八年级（3）班版本”, “课后一分钟复盘”; note “中断时只恢复发布或清理暂存，不自动重试模型”.
```

定点修正：把封面下方 `八年级 数学 · 人教版` 中的 `人教版` 只改为 `华师大版`，其余不变。

