/* 原型数据：任务模板与假页面内容，全部为虚构 */
(function () {
  'use strict';

  // 任务模板。dur 为模拟时长（秒，1× 速度下）；cat 用于抽屉筛选。
  window.TC_TEMPLATES = {
    'paper-analysis': {
      name: '试卷分析与本场赋分', kind: 'ai', cat: 'exam', dur: 40,
      scope: '考试：八年级上学期期中数学', origin: 'exam-config', goTo: 'exam-config',
      stages: ['正在准备试卷题目', '正在分析试卷与生成评分依据', '正在完成本场赋分与校验'],
      outcome: { label: '全部完成', tone: 'success', note: '本场评分依据与赋分已生成。' },
    },
    'grading': {
      name: '答卷批改', kind: 'ai', cat: 'exam', dur: 60,
      scope: '考试：八年级上学期期中数学', origin: 'scan', goTo: 'review',
      stages: ['正在准备答卷图像', '正在批改答卷', '正在汇总批改结果'],
      batches: 8,
      outcome: { label: '等待教师核对', tone: 'warning',
        note: '本次处理已结束，仍有结果需要教师核对；请返回原操作页面查看。' },
    },
    'tagging': {
      name: '题目标签与判定点分析', kind: 'ai', cat: 'bank', dur: 45,
      scope: '试卷：2025 秋期中卷、单元测试三', origin: 'question-bank', goTo: 'question-bank',
      stages: ['正在读取题目资料', '正在分析题目标签与判定点', '正在保存分析结果'],
      outcome: { label: '部分完成', tone: 'warning', note: '仍有 3 道题未完成。' },
    },
    'ai-summary': {
      name: 'AI 整理（错因、班级与个人报告）', kind: 'ai', cat: 'exam', dur: 50,
      scope: '考试：八年级上学期期中数学', origin: 'results', goTo: 'results',
      stages: ['正在汇总成绩与作答证据', '正在生成错因与班级报告', '正在生成学生个人报告'],
      outcome: { label: '全部完成', tone: 'success', note: '错因分析、班级报告与个人报告已生成。' },
      failOutcome: { label: '未完成', tone: 'error',
        note: '本次处理已结束，但业务结果未完成；请返回原操作页面检查。' },
    },
    'export-book': {
      name: '导出错题本', kind: 'short', cat: 'export', dur: 3,
      scope: '所选学生的本学期错题', origin: 'learning', goTo: 'learning',
      stages: ['正在整理错题与生成文件'],
      outcome: { label: '全部完成', tone: 'success', note: '错题本文件已生成，可在面板中下载。' },
      download: '错题本-初二3班.xlsx',
    },
  };

  window.TC_NAV = [
    { group: '教学', items: [['workbench', '工作台', '⌂']] },
    { group: '考试与阅卷', items: [
      ['exam-config', '考试配置', '▦'], ['scan', '扫描批改', '✎'],
      ['review', '复核', '◎'], ['results', '成绩中心', '▤']] },
    { group: '题库与组卷', items: [['question-bank', '题库', '▣'], ['assembly', '组卷', '⧉']] },
    { group: '教学分析', items: [['learning', '学情', '◈']] },
  ];
  window.TC_SETTINGS_NAV = [['settings', '设置', '⚙']];

  // 每页 2–3 张占位卡；go-card 为「去查看」高亮目标
  window.TC_PAGES = {
    'workbench': { title: '工作台', sub: '今天的阅卷与教学概览', cards: [
      { id: 'recent', title: '最近考试', body: ['<span class="num">八年级上学期期中数学</span>', '初二 3 班 · 48 份答卷 · 批改进行中'] },
      { id: 'todo', title: '待办', body: ['5 份答卷待复核', '2 道题的评分依据待确认'] },
      { id: 'pulse', title: '本周动态', body: ['完成 1 场考试批改', '题库新增 36 道题'] },
    ] },
    'exam-config': { title: '考试配置', sub: '八年级上学期期中数学', cards: [
      { id: 'go-card', title: '评分依据与本场赋分', body: ['<span class="tag">12 道题</span><span class="tag">满分 100</span>', '评分依据已生成，本场赋分待确认'] },
      { id: 'paper', title: '试卷概况', body: ['2025 秋期中卷 · 12 题', '选择 6 · 填空 3 · 解答 3'] },
      { id: 'students', title: '参考学生', body: ['<span class="num">48</span>', '初二 3 班全体'] },
    ] },
    'scan': { title: '扫描批改', sub: '八年级上学期期中数学', cards: [
      { id: 'scan-progress', title: '扫描进度', body: ['<span class="num">46 / 48</span>', '已识别 46 份答卷，2 份待处理'] },
      { id: 'scan-queue', title: '批改队列', body: ['答卷批改进行中', '当前批次自动排队处理'] },
      { id: 'device', title: '扫描设备', body: ['本机摄像头 · 已连接', '上次校准：今天 08:40'] },
    ] },
    'review': { title: '复核', sub: '八年级上学期期中数学', cards: [
      { id: 'go-card', title: '待核对答卷', body: ['<span class="num">5</span>', '低置信度评分与识别冲突待教师核对'] },
      { id: 'review-low', title: '低置信度题目', body: ['第 9 题 · 3 份', '第 12 题 · 2 份'] },
      { id: 'review-random', title: '抽查队列', body: ['随机抽查 4 份', '用于校准批改一致性'] },
    ] },
    'results': { title: '成绩中心', sub: '八年级上学期期中数学', cards: [
      { id: 'go-card', title: '成绩概览', body: ['平均 <span class="num">78.4</span>', '及格率 91.7% · 优秀率 33.3%'] },
      { id: 'class-compare', title: '班级对比', body: ['初二 3 班 vs 年级平均', '+2.1 分'] },
      { id: 'analysis', title: '错因与报告', body: ['AI 整理结果将汇总到这里', '含班级报告与学生个人报告'] },
    ] },
    'question-bank': { title: '题库', sub: '题目资料与判定点', cards: [
      { id: 'go-card', title: '题库总量', body: ['<span class="num">1,284</span>', '覆盖八年级数学全部章节'] },
      { id: 'need-fix', title: '待补资料', body: ['3 道题缺判定点', '5 道题缺标签'] },
      { id: 'import', title: '最近导入', body: ['2025 秋期中卷 · 12 题', '单元测试三 · 10 题'] },
    ] },
    'assembly': { title: '组卷', sub: '选题成卷与卷面预览', cards: [
      { id: 'draft', title: '组卷草稿', body: ['单元测试三（草稿）', '已选 8 / 10 题'] },
      { id: 'recommend', title: '推荐题目', body: ['按班级薄弱点推荐', '一元二次方程 · 3 题'] },
      { id: 'preview', title: '卷面预览', body: ['印刷版式 · A4 双栏', '含答题区与评分栏'] },
    ] },
    'learning': { title: '学情', sub: '掌握度与个性化训练', cards: [
      { id: 'go-card', title: '掌握度概览', body: ['班级平均达成 <span class="num">72%</span>', '判定点口径'] },
      { id: 'weak', title: '薄弱知识点', body: ['二次函数图像性质', '一元二次方程应用'] },
      { id: 'export', title: '错题本导出', body: ['按学生或小组导出', '导出的文件在任务面板下载'] },
    ] },
    'settings': { title: '设置', sub: '应用与模型配置', cards: [
      { id: 'model', title: '模型配置', body: ['批改模型 · 已绑定', '内容生成模型 · 已绑定'] },
      { id: 'data', title: '数据与空间', body: ['已用空间 1.8 GB', '自动备份：开启'] },
      { id: 'roster', title: '学生名单', body: ['初二 3 班 · 48 人', '名单已是最新'] },
    ] },
  };

  window.TC_PAGE_NAMES = {
    'workbench': '工作台', 'exam-config': '考试配置', 'scan': '扫描批改', 'review': '复核',
    'results': '成绩中心', 'question-bank': '题库', 'assembly': '组卷', 'learning': '学情',
    'settings': '设置',
  };
})();
