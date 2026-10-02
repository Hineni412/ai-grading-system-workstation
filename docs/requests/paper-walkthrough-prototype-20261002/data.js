'use strict';
/* ================= 合成数据 ================= */
const EXAM_ID = 'sess-202610-u5';               // 种子用：同一考试重开选择不变
const EXAM_NAME = '第 5 次单元测（合成）';
const PREV_NAME = '第 4 次单元测（合成）';
const CLASSES = ['9班','10班'];
const CLASS_SIZE = {'9班':22,'10班':22};

const QUESTIONS = [
  {id:'1',max:3,kind:'o',ans:'C'},{id:'2',max:3,kind:'o',ans:'A'},
  {id:'3',max:3,kind:'o',ans:'B'},{id:'4',max:3,kind:'o',ans:'D'},
  {id:'5',max:4,kind:'o',ans:'A'},{id:'6',max:4,kind:'o',ans:'C'},
  {id:'7',max:4,kind:'o',ans:'B'},{id:'8',max:4,kind:'o',ans:'D'},
  {id:'9',max:10,kind:'s'},{id:'10',max:12,kind:'s'},
  {id:'11(1)',max:8,kind:'s'},{id:'11(2)',max:10,kind:'s'},
  {id:'12(1)',max:10,kind:'s'},{id:'12(2)',max:12,kind:'s'},{id:'12(3)',max:10,kind:'s'},
]; // 合计 100
const QMAP = Object.fromEntries(QUESTIONS.map(q=>[q.id,q]));

/* 解答题难度偏移（越低得分率越低）：q10、q12(2) 最低 → 进入典型错法 */
const DIFF = {'9':-0.08,'10':-0.34,'11(1)':-0.05,'11(2)':-0.18,'12(1)':-0.08,'12(2)':-0.36,'12(3)':-0.12};
/* 客观题满分概率 */
const PFULL = {'1':1,'2':1,'3':0.62,'4':0.55,'5':0.5,'6':0.45,'7':0.7,'8':0.55};

/* 错因（已整理）：按班级列学生序号（0 起）。人数 <2 的组会被规则跳过 */
const CAUSES = {
  '10':[
    {name:'漏写负根', cat:'计算与化简', idx:{'9班':[9,13,17],'10班':[8,12,16]}},
    {name:'符号处理错误', cat:'计算与化简', idx:{'9班':[10,15],'10班':[10,18]}},
    {name:'只写结论无过程', cat:'过程与表达', idx:{'9班':[20]}},
  ],
  '12(2)':[
    {name:'辅助线构造不当', cat:'方法与思路', idx:{'9班':[11,14,19],'10班':[9,14,17]}},
    {name:'分类讨论遗漏', cat:'方法与思路', idx:{'9班':[12,18],'10班':[13,19]}},
  ],
  '11(2)':[
    {name:'漏乘系数', cat:'计算与化简', idx:{'9班':[13,16],'10班':[11,15,20]}},
    {name:'公式套用错误', cat:'方法与思路', idx:{'9班':[17,21],'10班':[16,21]}},
  ],
};

/* AI 标记"用了参考答案以外的解法"：[班, 序号, 题号, 该题得分] */
const ALT_FLAGS = [
  ['9班',15,'11(2)',6],   // 后半段、得分过半 → 入选
  ['9班',3,'9',4],        // 得分未过半 → 被规则过滤
  ['10班',14,'11(1)',5],  // 后半段 → 入选
  ['10班',18,'9',6],      // 后半段 → 入选（10班第二个 a 类来源）
  ['10班',3,'12(3)',3],   // 未过半 → 过滤
];

/* 易题失手：[班, 序号, 题号]。序号小=名次高才通过"总分不低于班内中位" */
const SLIPS = [
  ['9班',4,'1'], ['10班',7,'1'],
  ['9班',16,'2'], ['9班',19,'2'], ['10班',15,'2'], ['10班',19,'2'], // 名次靠后 → 被规则过滤
];

/* q12(2) 满分（难题）：低名次满分者进入"意外得分" b 类 */
const HARD_FULL = {'9班':[2,5,16],'10班':[1,4,6,15,20]};

/* 钉分修正：取消偶然满足规则 2 的学生，保证每类在各范围的张数稳定 */
const SCORE_PIN = [['10班',15,'2',3],['10班',8,'7',4],['10班',15,'7',4]];

/* 上一场班内名次（目标名次，其余学生≈本次名次）；prevMissing = 上次缺考 */
const PREV_MOVES = {
  '9班':{3:21, 8:22, 6:19},            // 进步 14/17 名（6 号仅 7 名，不达标）
  '10班':{5:18, 0:20, 13:21, 17:3},    // 进步 9/17/13 名；退步 16 名（唯一退步达标者）
};
const PREV_MISSING = {'9班':[18],'10班':[2]};

/* 待复核条目（配合"还有 3 题待复核"开关） */
const PENDING_ITEMS = [['9班',7,'10'],['10班',5,'12(2)'],['9班',20,'9']];

/* ---------- 随机数（稳定种子） ---------- */
function hashStr(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const pct=r=>Math.round(r*100)+'%';
const medIdx=n=>Math.floor((n-1)/2);
function median(arr){if(!arr.length)return 0;const s=[...arr].sort((a,b)=>a-b);return s[medIdx(s.length)]}

/* ---------- 学生与成绩 ---------- */
const SURNAMES='陈林黄张李王吴刘杨许郑谢郭曾周苏叶徐'.split('');
const GIVEN=['雨桐','子墨','浩然','欣怡','一诺','思远','佳琪','宇航','若曦','铭泽','欣妍','俊杰','诗涵','子轩','可欣','嘉懿','晨曦','语嫣','天佑','梓萱','文博','雅静','志远','慧敏','子涵','思源','晓彤','凯文','欣悦','一鸣','思彤','明轩'];

const STUDENTS=[];
(function buildStudents(){
  let seq=0;
  for(const cls of CLASSES){
    const n=CLASS_SIZE[cls];
    for(let i=0;i<n;i++){
      const name=SURNAMES[(i*3+seq)%SURNAMES.length]+GIVEN[(i*7+seq*2)%GIVEN.length];
      const st={id:'s'+cls.slice(0,2)+(i+1), idx:i, cls, name,
        code:(cls==='9班'?'09':'10')+String(i+1).padStart(2,'0'),
        ability:0.93-i*0.023, scores:{}, alt:new Set(), prevRank:null, prevTotal:null};
      STUDENTS.push(st);seq++;
    }
  }
})();

/* 解答题步骤分值（整点有无：每步全对或 0，题分 = 达成步骤之和）。
   各题分值集合需尽量覆盖 0..max，个别缺口（q10、q12(2) 的 1/11）由 snapScore 就近吸附 */
const STEP_PTS={'9':[2,3,4,1],'10':[3,4,3,2],'11(1)':[2,3,2,1],'11(2)':[2,3,4,1],
  '12(1)':[2,3,4,1],'12(2)':[3,4,3,2],'12(3)':[2,3,4,1]};
function achievableSet(qid){const s=new Set([0]);for(const p of STEP_PTS[qid])for(const v of[...s])s.add(v+p);return s}
const ACHIEVE={};
function snapScore(qid,v){
  const set=ACHIEVE[qid]||(ACHIEVE[qid]=achievableSet(qid));
  if(set.has(v))return v;
  let best=0,bd=1e9;for(const a of set){const d=Math.abs(a-v);if(d<bd){bd=d;best=a}}
  return best;
}

/* 错法 → 出错步骤：at=从第几步开始错（此前各步得分，之后失分）；
   mask=显式达成集合（用于"只写结论无过程"这类跳步错法）。
   错法学生的得分由掩码推出，手写行、步骤摘要、AI 理由共用同一掩码 */
const CAUSE_FAIL={
  '10':{'漏写负根':{at:2},'符号处理错误':{at:1},'只写结论无过程':{mask:[0,0,1,0]}},
  '11(2)':{'漏乘系数':{at:2},'公式套用错误':{at:1}},
  '12(2)':{'辅助线构造不当':{at:1},'分类讨论遗漏':{at:3}},
};
function causeMask(qid,cause){
  const f=(CAUSE_FAIL[qid]||{})[cause];if(!f)return null;
  return f.mask?f.mask.map(Boolean):STEP_PTS[qid].map((_,i)=>i<f.at);
}
function maskScore(qid,mask){return STEP_PTS[qid].reduce((a,p,i)=>a+(mask[i]?p:0),0)}
/* 无错法的普通部分分：子集和挑达成步骤，偏好靠前的步骤 */
function achieveSteps(pts,score){
  const n=pts.length;let best=-1,bestKey=-1;
  for(let m=0;m<(1<<n);m++){
    let s=0;for(let i=0;i<n;i++)if(m>>i&1)s+=pts[i];
    if(s!==score)continue;
    let key=0;for(let i=0;i<n;i++)key=key*2+((m>>i)&1);
    if(key>bestKey){bestKey=key;best=m}
  }
  return best<0?pts.map(()=>false):pts.map((_,i)=>!!(best>>i&1));
}

function baseScore(q, st){
  const r=mulberry32(hashStr(`${EXAM_ID}|${st.cls}|${q.id}|${st.idx}`));
  if(q.kind==='o') return r()<PFULL[q.id]?q.max:0;
  const t=clamp(st.ability+DIFF[q.id]+(r()*0.12-0.06),0,1);
  return Math.round(t*q.max);
}
(function fillScores(){
  for(const st of STUDENTS) for(const q of QUESTIONS) st.scores[q.id]=baseScore(q,st);
  for(const [cls,i,qid] of SLIPS) stu(cls,i).scores[qid]=0;
  for(const [cls,i,qid,sc] of SCORE_PIN) stu(cls,i).scores[qid]=sc;
  for(const [cls,i,qid,sc] of ALT_FLAGS){const s=stu(cls,i);s.scores[qid]=sc;s.alt.add(qid)}
  for(const cls of CLASSES) for(const i of HARD_FULL[cls]) stu(cls,i).scores['12(2)']=QMAP['12(2)'].max;
  // 错因成员：得分=该错法掩码的分值之和（与笔迹行、步骤摘要同源）
  for(const qid in CAUSES) for(const g of CAUSES[qid])
    for(const cls in g.idx) for(const k of g.idx[cls])
      stu(cls,k).scores[qid]=maskScore(qid,causeMask(qid,g.name));
  // 整数化收口：解答题得分吸附到步骤分值可表达的值
  for(const st of STUDENTS) for(const q of QUESTIONS)
    if(q.kind==='s') st.scores[q.id]=snapScore(q.id,Math.round(st.scores[q.id]));
  // 上一场名次：先按"≈本次"占位，再套用指定目标名次，剩余顺次填充
  for(const cls of CLASSES){
    const n=CLASS_SIZE[cls], moves=PREV_MOVES[cls]||{};
    const used=new Set(Object.values(moves));
    const free=[];for(let v=1;v<=n;v++)if(!used.has(v))free.push(v);
    let f=0;
    for(const st of STUDENTS.filter(s=>s.cls===cls)){
      st.prevRank = st.idx in moves ? moves[st.idx] : free[f++];
      if(PREV_MISSING[cls].includes(st.idx)) st.prevRank=null;
    }
  }
  // 上一场总分：按上次名次套用"本场总分排序值"，分布自然
  for(const cls of CLASSES){
    const totals=STUDENTS.filter(s=>s.cls===cls).map(total).sort((a,b)=>b-a);
    for(const st of STUDENTS.filter(s=>s.cls===cls))
      if(st.prevRank) st.prevTotal=totals[st.prevRank-1];
  }
})();
function stu(cls,idx){return STUDENTS.find(s=>s.cls===cls&&s.idx===idx)}
function total(st){return QUESTIONS.reduce((a,q)=>a+st.scores[q.id],0)}
function totalRate(st){return total(st)/100}
const fmt=v=>Number.isInteger(v)?v:v.toFixed(1);

/* ---------- 统计（按范围/本班） ---------- */
function scopeStudents(scope){return STUDENTS.filter(s=>!scope||s.cls===scope)}
function qRate(qid,list){return list.reduce((a,s)=>a+s.scores[qid]/QMAP[qid].max,0)/list.length}
function qFullFrac(qid,list){return list.filter(s=>s.scores[qid]===QMAP[qid].max).length/list.length}
const classCache={};
function classStats(cls){
  if(classCache[cls])return classCache[cls];
  const list=STUDENTS.filter(s=>s.cls===cls);
  const ranked=[...list].sort((a,b)=>total(b)-total(a)||a.idx-b.idx);
  const rank={};ranked.forEach((s,i)=>rank[s.id]=i+1);
  const medRate=median(list.map(totalRate));
  const stats={list,rank,medRate,n:list.length,
    fullFrac:qid=>qFullFrac(qid,list), rate:qid=>qRate(qid,list),
    avg:qid=>list.reduce((a,s)=>a+s.scores[qid],0)/list.length};
  classCache[cls]=stats;return stats;
}
