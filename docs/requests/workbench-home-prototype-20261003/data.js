/* data.js — 工作台首页原型的合成数据，五种状态（批改进行中/待复核/已完成/新学期/读取失败） */

const SRC = {
  wk: '现有：工作台概况接口',
  train: '需新增：训练回收待办汇总',
  bank: '需新增：本场未挂技能计数',
  review10: '现有：成绩中心（需汇总数量）',
  report: '需新增：个人报告状态汇总',
  handout: '现有：班级组卷只读候选',
  skills: '现有：学情总览接口（需缓存）',
};

// ---- 待处理行（跨状态复用） ----
const R_RUN  = { mod:'任务', title:'AI 批改 · 第五周单元测验', pct:52, stage:'解答题第 18 题', act:'查看', toast:'任务中心', src:SRC.wk };
const R_EXC3 = { mod:'考试', title:'处理 3 条答卷异常', fact:'2 份答卷未匹配学生，1 名学生扫描页缺失', act:'处理异常', toast:'考试批改 · 答卷异常', src:SRC.wk };
const R_EXC1 = { mod:'考试', title:'处理 1 条答卷异常', fact:'1 名学生扫描页缺失', act:'处理异常', toast:'考试批改 · 答卷异常', src:SRC.wk };
const R_REV12= { mod:'考试', title:'复核 12 项评分', fact:'低置信度 9 项，判定点不确定 3 项 · 涉及第 17、18、21 题', act:'开始复核', toast:'考试批改 · 复核', src:SRC.wk };
const R_BANK = { mod:'题库', title:'补齐本场 3 道题的技能关联', fact:'第 8、14、19 题未挂技能，这些题暂不计入学情', act:'去补齐', toast:'题库管理 · 待处理', src:SRC.bank };
const R_PUB  = { mod:'训练', title:'发布 10班 训练证据', fact:'第三周巩固卷 · 42 份已复核，发布后更新掌握度', act:'去发布', toast:'知识与训练 · 生成试卷 · 回收', src:SRC.train };
const R_TRV  = { mod:'训练', title:'复核 9班 训练卷判定 6 份', fact:'第四周补弱个人卷 · 回收 30 份，6 份有不确定判定点', act:'去复核', toast:'知识与训练 · 生成试卷 · 回收', src:SRC.train };
const R_LOOK1= { mod:'考试', title:'第四周学情反馈：看卷 10 分钟', fact:'12 道意外失分题，按原因分组', act:'看卷', toast:'成绩中心 · 看卷 10 分钟', src:SRC.review10 };
const R_LOOK2= { mod:'考试', title:'看卷 10 分钟', fact:'12 道意外失分题，按原因分组', act:'看卷', toast:'成绩中心 · 看卷 10 分钟', src:SRC.review10 };
const R_RPT  = { mod:'考试', title:'个人报告：8 人需重新生成', fact:'80 人已生成；成绩改动后 8 人的 AI 文字仍是上次内容', act:'查看', toast:'成绩中心 · 成绩明细 · 个人报告', src:SRC.report };
const R_HAND = { mod:'题库', title:'按本场失分题组讲义', fact:'得分率低于 70% 的题 9 道，涉及 6 项技能', act:'去组卷', toast:'组卷工作台 · 班级组卷', src:SRC.handout };

// ---- 最近考试行（跨状态复用） ----
const RC_W4  = { name:'第四周学情反馈', date:'9月20日', cls:'9班、10班', graded:'88/88', review:'0 项', warn:0, status:'已完成', tone:'ok',   act:'看成绩', toast:'成绩中心' };
const RC_W3  = { name:'第三周小测',     date:'9月13日', cls:'9班',       graded:'44/44', review:'0 项', warn:0, status:'已完成', tone:'ok',   act:'看成绩', toast:'成绩中心' };
const RC_W2  = { name:'第二周小测',     date:'9月6日',  cls:'9班、10班', graded:'87/87', review:'0 项', warn:0, status:'已完成', tone:'ok',   act:'看成绩', toast:'成绩中心' };
const RC_W1  = { name:'开学摸底',       date:'9月1日',  cls:'9班、10班', graded:'88/88', review:'0 项', warn:0, status:'已完成', tone:'ok',   act:'看成绩', toast:'成绩中心' };
const RC_SUM = { name:'暑期衔接测',     date:'8月28日', cls:'9班、10班', graded:'88/88', review:'0 项', warn:0, status:'已完成', tone:'ok',   act:'看成绩', toast:'成绩中心' };

// ---- 学情行 ----
const SKILLS = [
  { name:'利用 SAS 判定三角形全等', chap:'第十二章 全等三角形', w:17, t:86 },
  { name:'分式方程检验增根',       chap:'第十五章 分式',       w:14, t:86 },
  { name:'完全平方公式展开',       chap:'第十四章 整式的乘法与因式分解', w:11, t:86 },
];

const STEP_T = ['考试配置','考试批改 · 预检','考试批改','考试批改 · 复核','成绩中心'];
const step = (label, st, status, i) => ({ label, st, status, toast: STEP_T[i] });

const STATES = {
  grading: {
    side: { name:'第五周单元测验', line:'八年级上册 · 10月2日 · 批改中' },
    exam: { name:'第五周单元测验', meta:'10月2日 · 9班、10班 · 88 人' },
    steps: [
      step('考试配置','done','已完成',0), step('答卷与预检','done','86 份已匹配',1),
      step('批改','prog','46/88',2), step('复核','todo','等待批改',3), step('成绩与讲评','todo','—',4) ],
    todo: { run:[R_RUN], need:[R_EXC3, R_TRV], cont:[R_LOOK1] },
    recent: [
      { name:'第五周单元测验', date:'10月2日', cls:'9班、10班', graded:'46/88', review:'0 项', warn:0, status:'批改中', tone:'info', act:'继续批改', toast:'考试批改', cur:1 },
      RC_W4, RC_W3, RC_W2, RC_W1 ],
    skills: SKILLS, smeta:'八年级上册 · 3 场考试', sev:'证据不足 12 项 · 未挂技能 5 道题',
  },
  review: {
    side: { name:'第五周单元测验', line:'八年级上册 · 10月2日 · 待复核' },
    exam: { name:'第五周单元测验', meta:'10月2日 · 9班、10班 · 88 人' },
    steps: [
      step('考试配置','done','已完成',0), step('答卷与预检','done','88 份已匹配',1),
      step('批改','done','88/88',2), step('复核','prog','12 项待复核',3), step('成绩与讲评','todo','复核后可查看',4) ],
    todo: { run:[], need:[R_REV12, R_EXC1, R_TRV], cont:[R_LOOK1] },
    recent: [
      { name:'第五周单元测验', date:'10月2日', cls:'9班、10班', graded:'88/88', review:'12 项', warn:1, status:'待复核', tone:'warn', act:'去复核', toast:'考试批改 · 复核', cur:1 },
      RC_W4, RC_W3, RC_W2, RC_W1 ],
    skills: SKILLS, smeta:'八年级上册 · 3 场考试', sev:'证据不足 12 项 · 未挂技能 5 道题',
  },
  done: {
    side: { name:'第四周学情反馈', line:'八年级上册 · 9月20日 · 已完成' },
    exam: { name:'第四周学情反馈', meta:'9月20日 · 9班、10班 · 88 人' },
    steps: [
      step('考试配置','done','已完成',0), step('答卷与预检','done','88 份已匹配',1),
      step('批改','done','88/88',2), step('复核','done','0 项',3), step('成绩与讲评','done','可查看',4) ],
    todo: { run:[], need:[R_BANK, R_PUB], cont:[R_LOOK2, R_RPT, R_HAND] },
    recent: [ Object.assign({ cur:1 }, RC_W4), RC_W3, RC_W2, RC_W1, RC_SUM ],
    skills: SKILLS, smeta:'八年级上册 · 3 场考试', sev:'证据不足 12 项 · 未挂技能 5 道题',
  },
  newterm: {
    side: { name:'尚未选择考试', line:'八年级上册' },
    exam: null, steps: [],
    todo: { run:[], need:[], cont:[] },
    recent: [],
    skills: [], smeta:'', sev:'',
  },
  error: {
    side: { name:'暂时无法读取', line:'八年级上册' },
    exam: 'fail', steps: [],
    todo: { run:[], need:[R_BANK, R_PUB], cont:[] },
    recent: 'fail',
    skills: SKILLS, smeta:'八年级上册 · 3 场考试', sev:'证据不足 12 项 · 未挂技能 5 道题',
  },
};
