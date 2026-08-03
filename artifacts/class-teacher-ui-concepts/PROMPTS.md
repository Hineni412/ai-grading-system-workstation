# 生图提示词归档

## 生成方式

- 工具：内置 ImageGen。
- 模式：第 1 张从零生成；首版第 2–7 张使用已有页面作为风格参考；第 4–6 张 V2 使用对应首版图片作定向编辑目标，只调整用户指出的页面内容。
- 画幅：1600 × 1000，桌面端高保真 Web 应用概念图。
- 说明：为便于复现，下面把实际提示词中重复的外壳要求提炼成“共享提示词”，再列出每页最终页面段。复现时使用“共享提示词 + 对应页面段”。

## 共享提示词

```text
Create a separate high-fidelity desktop web-app UI mockup, 1600×1000 landscape,
for the Chinese “AI 阅卷系统” class-teacher workbench. Use a quiet professional
white/light-gray app shell, narrow left icon rail, top product tabs “今日 / 日历与工作图 /
事务 / 学生”, restrained teal #135E6B accent, text #1C2733, compact and highly
legible Simplified Chinese typography, thin neutral gray borders, 8–12 px radii,
34–38 px controls, 13–14 px dense body text, and almost no shadows. Use a subtle
2–4 px teal teacher-ledger spine as the single signature motif.

No gradients, glass, oversized marketing hero, decorative illustrations, colorful
card ocean, avatar wall, chat bubbles, real personal data, risk ranking, personality
labels, diagnosis, automatic punishment, automatic bullying determination, auto-send,
or auto-close. AI outputs must be shown as drafts or suggestions; teacher confirmation
must remain the final action.
```

## 01 今日工作台

```text
Active tab “今日”. Header “今天，从一件事开始”. At the top create one compact quick
entry row with a unique one-line task input, date field, and primary “整理工作” button.
Below show inline counts for 今日 / 逾期 / 待复核 / 等待中. Use a broad left task list
and a right current-work inspector. Show a selected goal, steps, dependencies, latest
status, and a purple AI-proposed branch awaiting teacher confirmation. Include a clearly
locked sensitive-area gateway with six PIN boxes and “解锁学生区”. Explain that one
teacher confirmation permits at most one physical model request and a second teacher
confirmation writes the formal work graph. Use only synthetic school-work examples.
```

## 02 日历与工作图

```text
Use screen 01 only as a style reference. Active tab “日历与工作图”. Header “本周工作进程”.
Top controls: 今天 / 本周 / 时间线 / 全部, previous week / this week / next week, and a
seven-day strip. Main split 72/28: a calm work-relation graph on the left and “行动详情”
inspector on the right. Use goal “完成秋游回执上报” with child nodes “发布家长通知”,
“统计未交回执”, “等待年级组确认格式”, “向年级上报”, “班会材料准备”; add one purple
AI branch awaiting confirmation. Inspector shows status, target date, collection counts,
latest update, “设为等待” and “完成”. No drag-to-reschedule.
```

## 03 事务处理台

```text
Use screen 01 only as a style reference. Active tab “事务”. Header “事务处理台” and
neutral chip “个人工作清单 · 非学校正式 SOP”. Three columns: left template and affair
lists; center selected “学生冲突情况核对”; right upcoming steps and school configuration
gaps. In the center show a pale-red outline banner “人工处置优先”, current step 2/5
“分别核对双方陈述”, required-safety label “安全必做 · 不可跳过”, fact textarea,
unsent communication draft, teacher decision choices, completed step, and history.
Right side shows contacts/process gaps, AI requests 0, save, teacher-confirmed close,
and reopen. Never present this as an official school procedure or an automatic decision.
```

## 04 受保护的学生卡目录 V2

```text
Edit screen 04 while preserving the global shell and visual tokens. Redesign the page as
an encrypted student-card directory whose only job is finding a student and opening the
support record. Remove every add, import, upload, batch-paste, delete and inline-edit entry.
Use a compact search/filter toolbar, then a two-column by four-row grid of long white
rounded cards with a 4 px teal ledger spine. Each synthetic card “学生 A–H” shows class,
local identifier, confirmed/support/plan counts, anonymous-sync state and action
“查看支持档案 ›”. No portraits, grades, risk labels or comparisons. Keep a fixed right
security panel with unlocked status, five-minute lock, ordinary-backup exclusion,
“真实模型：关闭”, “立即锁定敏感区”, and a page-boundary explanation.
```

## 05 学生支持记录与 AI 追问 V2

```text
Edit screen 05 while preserving the global shell. Depict the exact mid-flow state in which
AI has asked follow-up questions and is waiting for teacher supplementation. Add the clear
five-step path “保存教师记录 / 核对匿名内容 / 确认发送给 AI / AI 追问或生成草稿 /
教师核对并写入”, with step 4 active. In the record composer separate “仅保存到本机”
from “保存并准备 AI 讨论” and state that the latter first opens an anonymous preview,
not an immediate request. Every saved timeline entry has “与 AI 讨论这条记录”. The right
purple-gray AI panel shows two numbered questions, a large “用自己的话补充” field,
“生成补充后的匿名预览”, “结束本次 AI 讨论”, request count 1/1 and the fact that the
next round is not sent. No chat bubbles or final AI proposal on this screen.
```

## 06 单个学生学业证据分析 V2

```text
Edit screen 06 while preserving the standard class-teacher shell. Remove the import flow
and long evidence table. Make charts the information body for synthetic “学生 A”: compact
filters and summaries; a longitudinal class-rank chart labelled with both rank and reference
count such as “7/48”; a visible break for a different exam type; shape-coded absence and
makeup events; horizontal per-subject dumbbells for 数学 18/48→9/48, 物理 14/48→8/48,
语文 13/48→12/48, 英语 10/48→17/48; and a bottom band of rounded exam-evidence cards.
The right column uses precise horizontal dot bars for stable strength/recent improvement/
stable/support-needed with evidence counts, never a radar chart. Add a teacher-decision
attention card and a compact selected-evidence inspector with source-row drill-down.
Only comparable confirmed exams connect; different cohorts, missing states and pending
evidence break the line. No class leaderboard, risk score or causal diagnosis.
```

## 07 高级数据安全

```text
Use screen 04 only as a style reference. Active shield subtab “高级数据安全”. Header
explains this is not a daily page. Blue factual banner: sensitive data is in an independent
encrypted vault and excluded from ordinary backups and logs. Left column: protection
status (six-digit PIN, Windows-current-user protection, offline recovery-key state,
five-minute idle lock) and encrypted class-teacher-only backup list. Right column:
four-step restore preview with full-replacement warning, no silent merge, current vault
unchanged before confirmation and on failure, and re-lock after success. Full-width bottom
red-outline danger zone: select synthetic 学生 A, preview counts of student card/support
records/evidence/follow-up, two empty exact-phrase confirmation inputs, and a disabled
permanent-delete button. Never display the recovery key or provide plain-text export.
```
