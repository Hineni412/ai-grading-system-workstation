/* states.js — 五种原型控制状态的世界模型（批次、异常页、各份状态） */

// 状态词与色调：缺页/待复核 warning · 待判定/判定中 info · 判定失败 danger · 待更新 accent · 已完成 ok · 已取消 gray
const STATUS = {
  missing:  { t:'缺 1 页',  tone:'warn',   rank:0 },
  failed:   { t:'判定失败', tone:'danger', rank:1 },
  review:   { t:'待复核',   tone:'warn',   rank:2 },
  pending:  { t:'待判定',   tone:'info',   rank:3 },
  update:   { t:'待更新',   tone:'ac',     rank:4 },
  judging:  { t:'判定中',   tone:'info',   rank:5 },
  done:     { t:'已完成',   tone:'ok',     rank:6 },
  canceled: { t:'已取消',   tone:'gray',   rank:7 },
};
const NEED = ['missing','failed','review','pending','update']; // 需要先处理（排序在前，"需要处理"筛选项）

// 判定点 spec：[题号, 点号, 原因]；原因 unc = AI 拿不准，unread = AI 无法辨认
const SPEC_2 = [[7,1,'unc'],[9,0,'unread']];   // 新判定完成后默认的 2 个待复核点

function mkSub(ix, status, spec, extra) {
  const s = Object.assign({}, STUDENTS[ix], {
    status, spec: spec || [], recv: STUDENTS[ix].pages, missPage: null, edited: false, _pt: null,
  }, extra || {});
  if (status === 'missing') { s.recv = s.pages - 1; s.missPage = s.pages; }
  return s;
}

const ANOMS = () => [
  { id:'a1', page:3, issue:'页面身份无法读取', tag:'上传文件' },
  { id:'a2', page:7, issue:'检测到重复页面',   tag:'上传文件' },
];

// subs 按题号顺序构造，再按 STATUS.rank 排序；队列渲染时按需要处理在前
function buildWorld(kind) {
  if (kind === 'nobatch') return { batch:false };
  const plan = { anoms: [], run: null, st: {} };
  if (kind === 'imported') {
    plan.anoms = ANOMS();
    plan.st = { 5:['missing'] }; // 其余全部待判定
  }
  if (kind === 'review') {
    plan.anoms = ANOMS();
    plan.st = {
      5:['missing'], 0:['pending'], 2:['pending'], 7:['pending'], 11:['pending'], 14:['pending'],
      3:['judging'],
      1:['review',[[7,1,'unc'],[9,0,'unread']]],          // 待复核 2 点
      8:['review',[[7,4,'unc']]],                          // 待复核 1 点
      13:['review',[[8,2,'unc'],[9,3,'unread'],[7,0,'unc']]], // 待复核 3 点
      4:['update'], 9:['update'], 15:['update'], 18:['update'],
    };
  }
  if (kind === 'judging') {
    plan.run = { done:2, total:5 }; // 批量判定进行中：5 份里 2 份已回、1 份在路上
    plan.st = {
      5:['missing'], 0:['pending'], 2:['pending'],
      7:['judging'], 11:['failed'],                     // 运行中已有一件失败（演示）
      14:['review', SPEC_2],
      1:['review',[[7,1,'unc'],[9,0,'unread']]], 8:['review',[[7,4,'unc']]],
      13:['review',[[8,2,'unc'],[9,3,'unread'],[7,0,'unc']]],
      3:['update'], 4:['update'], 9:['update'], 15:['update'], 18:['update'],
    };
  }
  const DEF = { imported:'pending', review:'done', judging:'done', done:'done' };
  const subs = STUDENTS.map((_, ix) => {
    const p = plan.st[ix] || [DEF[kind]];
    return mkSub(ix, p[0], p[1]);
  });
  return { batch:true, anoms:plan.anoms, run:plan.run, subs };
}
