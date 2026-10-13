/* 工作台首页改版原型 · 合成样例数据（无真实考试名与学生名） */
window.WB_PROTO = (function () {
'use strict';

/* 考试按新→旧排列；avgRate 为「平均得分率」样例（正式数据尚未提供） */
var exams = [
  { id: 'A', name: '第六周 一次函数小测', date: '10月11日', papers: 45, avgRate: 68 },
  { id: 'B', name: '第五周 实数单元练习', date: '10月4日', papers: 44, avgRate: 74 },
  { id: 'C', name: '第四周 勾股定理复习卷', date: '9月27日', papers: 45, avgRate: 71 },
  { id: 'D', name: '第三周 平方根小测', date: '9月20日', papers: 43, avgRate: 79 }
];

/* 全部完成场次的最近考试行；status: completed / grading / created / failed */
var DONE_ROWS = [
  { id: 'A', status: 'completed', graded: 45, matched: 45, review: 0 },
  { id: 'B', status: 'completed', graded: 44, matched: 44, review: 0 },
  { id: 'C', status: 'completed', graded: 45, matched: 45, review: 0 },
  { id: 'D', status: 'completed', graded: 43, matched: 43, review: 0 }
];
function rowsWith(patch) {
  return DONE_ROWS.map(function (r) {
    var o = patch[r.id];
    return o ? { id: r.id, status: o.status || r.status, graded: o.graded != null ? o.graded : r.graded,
      matched: o.matched != null ? o.matched : r.matched, review: o.review || 0 } : r;
  });
}

/* 本学期学情卡：三种版式、各场景一致（正常有数据状态） */
var insight = {
  term: '八年级上册', scope: '全部学生',
  evidence: { got: 18, total: 24, unit: '题型' },
  attention: 7,
  top: [
    { name: '勾股定理的实际应用', loc: '第一章 第3节', need: 14, total: 45 },
    { name: '二次根式化简', loc: '第二章 第7节', need: 12, total: 44 },
    { name: '一次函数图象性质', loc: '第四章 第3节', need: 11, total: 45 }
  ],
  note: '需关注包括明显薄弱与还不稳；人数以该项有证据学生为分母。',
  source: '依据本学期考试与已发布训练'
};

var scenarios = {
  done: {
    label: '最近一场已全部完成', current: 'A',
    progress: { total: 45, matched: 45, graded: 45, failed: 0 },
    anomalies: { unmatched: 0, scanIssues: 0, failed: 0 },
    review: { items: 0, questions: 0 },
    reports: { current: 45, stale: 0 },
    training: [],
    jobs: [],
    rows: rowsWith({})
  },
  todo: {
    label: '有必办事项', current: 'A',
    progress: { total: 45, matched: 45, graded: 45, failed: 0 },
    anomalies: { unmatched: 0, scanIssues: 0, failed: 0 },
    review: { items: 7, questions: 3 },
    reports: { current: 42, stale: 3 },
    training: [{ draft: '训练卷 10月10日 14:20', scanPages: 4, reviewCount: 0, publishCount: 0 }],
    jobs: [],
    rows: rowsWith({ A: { review: 7 } })
  },
  grading: {
    label: '正在批改', current: 'A',
    progress: { total: 45, matched: 43, graded: 28, failed: 0 },
    anomalies: { unmatched: 2, scanIssues: 0, failed: 0 },
    review: { items: 0, questions: 0 },
    reports: { current: 43, stale: 0 },
    training: [],
    jobs: [{ type: 'grading_run', name: '批改 第六周 一次函数小测', detail: '已批改 28 份答卷，失败 0 份。', progress: 0.62 }],
    rows: rowsWith({ A: { status: 'grading', graded: 28, matched: 43 } })
  },
  old: {
    label: '切到以前的考试', current: 'C',
    progress: { total: 45, matched: 45, graded: 45, failed: 0 },
    anomalies: { unmatched: 0, scanIssues: 0, failed: 0 },
    review: { items: 0, questions: 0 },
    reports: { current: 45, stale: 0 },
    training: [],
    jobs: [],
    rows: rowsWith({})
  }
};

return {
  greeting: '下午好',
  today: '10月13日星期二',
  term: '八年级上册',
  exams: exams,
  insight: insight,
  scenarios: scenarios
};
})();
