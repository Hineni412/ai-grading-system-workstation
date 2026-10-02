'use strict';
/* ================= 抽卡规则（本机规则，不调用模型） =================
   返回 cards: [{cat, sid, qid|null, cause?, reason, note?}]
   约束：类别顺序 1→5；学生最多 2 次；(学生,题) 最多 1 次；总数 ≤15 */
const CAT_LABEL={1:'典型错法代表',2:'意外失分',3:'意外得分 / 特别解法',4:'名次变化大',5:'随机抽看'};
const CAP_TOTAL=15, CAP_PER_STU=2;

function buildDeck(scope,noPrev){
  const list=scopeStudents(scope);
  const useCount=new Map(), usedPair=new Set(), cards=[];
  const quotaOk=s=>(useCount.get(s.id)||0)<CAP_PER_STU;
  const pairOk=(s,qid)=>!usedPair.has(s.id+'|'+qid);
  function push(card){
    if(cards.length>=CAP_TOTAL)return false;
    const s=card.s;if(!quotaOk(s)||(card.qid&&!pairOk(s,card.qid)))return false;
    useCount.set(s.id,(useCount.get(s.id)||0)+1);
    if(card.qid)usedPair.add(s.id+'|'+card.qid);
    cards.push(card);return true;
  }
  pickTypicalErrors();pickUnexpectedLoss();pickUnexpectedGain();
  if(!noPrev)pickRankChange();
  pickRandomSample();
  return cards;

  /* 1. 典型错法代表 ≤4 */
  function pickTypicalErrors(){
    let n=0;
    const withCauses=Object.keys(CAUSES);
    if(withCauses.length){
      const qs=QUESTIONS.filter(q=>CAUSES[q.id]).sort((a,b)=>qRate(a.id,list)-qRate(b.id,list)).slice(0,3);
      for(const q of qs){
        const groups=CAUSES[q.id]
          .map(g=>({g,members:list.filter(s=>g.idx[s.cls]?.includes(s.idx))}))
          .filter(x=>x.members.length>=2)
          .sort((a,b)=>b.members.length-a.members.length).slice(0,2);
        for(const {g,members} of groups){
          if(n>=4)return;
          const sorted=[...members].sort((a,b)=>a.scores[q.id]-b.scores[q.id]||a.idx-b.idx);
          // 中位优先，被占额度时顺延到最接近中位的下一位
          const order=[sorted[medIdx(sorted.length)],...sorted].filter(Boolean);
          const rep=order.find(s=>quotaOk(s)&&pairOk(s,q.id));
          if(rep&&push({cat:1,s:rep,qid:q.id,cause:g.name,
            reason:`第 ${q.id} 题 · 错法「${g.name}」（${g.cat}）· 共 ${members.length} 人这样错（本范围）`}))n++;
        }
      }
      return;
    }
    // 没有题整理过错因：最弱 2 题各取一个 0 分与一个中间得分代表
    const qs=[...QUESTIONS].sort((a,b)=>qRate(a.id,list)-qRate(b.id,list)).slice(0,2);
    for(const q of qs){
      if(n>=4)return;
      const zeros=list.filter(s=>s.scores[q.id]===0&&quotaOk(s)&&pairOk(s,q.id));
      const parts=list.filter(s=>s.scores[q.id]>0&&s.scores[q.id]<q.max&&quotaOk(s)&&pairOk(s,q.id))
        .sort((a,b)=>a.scores[q.id]-b.scores[q.id]||a.idx-b.idx);
      for(const s of [zeros[0],parts[medIdx(parts.length)]].filter(Boolean))
        if(push({cat:1,s,qid:q.id,reason:`第 ${q.id} 题 · 错因尚未整理，按得分挑选代表`}))n++;
    }
  }

  /* 2. 意外失分 ≤3 */
  function pickUnexpectedLoss(){
    const cand=[];
    for(const s of list){const cs=classStats(s.cls);
      for(const q of QUESTIONS){
        if(s.scores[q.id]>=q.max)continue;
        const ff=cs.fullFrac(q.id);if(ff<0.75)continue;
        if(totalRate(s)<cs.medRate)continue;
        cand.push({s,q,ff,key:ff*(q.max-s.scores[q.id])/q.max});
      }}
    cand.sort((a,b)=>b.key-a.key||a.s.idx-b.s.idx);
    let n=0;
    for(const c of cand){if(n>=3)break;
      const rk=cs_rank(c.s);
      if(push({cat:2,s:c.s,qid:c.q.id,
        reason:`本班 ${pct(c.ff)} 满分，他丢了 ${fmt(c.q.max-c.s.scores[c.q.id])}/${c.q.max} 分；本次总分班内第 ${rk} 名`}))n++;}
  }
  function cs_rank(s){return classStats(s.cls).rank[s.id]}

  /* 3. 意外得分 / 特别解法 ≤3（每个子来源 ≤2） */
  function pickUnexpectedGain(){
    let n=0;
    const a=list.filter(s=>[...s.alt].some(qid=>s.scores[qid]>=QMAP[qid].max/2))
      .flatMap(s=>[...s.alt].filter(qid=>s.scores[qid]>=QMAP[qid].max/2).map(qid=>({s,qid})))
      .sort((x,y)=>{
        const bx=totalRate(x.s)<classStats(x.s.cls).medRate?0:1;
        const by=totalRate(y.s)<classStats(y.s.cls).medRate?0:1;
        return bx-by||totalRate(x.s)-totalRate(y.s)||x.s.idx-y.s.idx});
    let na=0;
    for(const c of a){if(na>=2||n>=3)break;
      if(push({cat:3,s:c.s,qid:c.qid,reason:`AI 标记：用了参考答案以外的解法（得分 ${fmt(c.s.scores[c.qid])}/${QMAP[c.qid].max}）`})){na++;n++}}
    const b=[];
    for(const s of list){const cs=classStats(s.cls);
      for(const q of QUESTIONS){
        if(s.scores[q.id]!==q.max)continue;
        const ff=cs.fullFrac(q.id);if(ff>0.30)continue;
        if(cs.rank[s.id]<=cs.n/2)continue;
        b.push({s,q,ff});}}
    let nb=0;
    for(const c of b){if(nb>=2||n>=3)break;
      if(push({cat:3,s:c.s,qid:c.q.id,
        reason:`本班只有 ${pct(c.ff)} 满分，他拿了满分；他本次班内第 ${cs_rank(c.s)} 名`})){nb++;n++}}
  }

  /* 4. 名次变化大 ≤4：进步/退步各取前 2，|变化|≥10 */
  function pickRankChange(){
    const imp=[],dec=[];
    for(const s of list){if(!s.prevRank)continue;
      const cs=classStats(s.cls),now=cs.rank[s.id],d=s.prevRank-now;
      if(Math.abs(d)<10)continue;
      (d>0?imp:dec).push({s,d,now});
    }
    imp.sort((a,b)=>b.d-a.d);dec.sort((a,b)=>a.d-b.d);
    const cardFor=c=>{
      // 卡题取"该生得分 − 本班均分"绝对分差最大的一题：进步取最大正差，退步取最大负差
      const cs=classStats(c.s.cls);let best=null,bestV=c.d>0?-1e9:1e9;
      for(const q of QUESTIONS){
        const v=c.s.scores[q.id]-cs.avg(q.id);
        if(c.d>0?v>bestV:v<bestV){bestV=v;best=q}
      }
      const sc=c.s.scores[best.id];
      return {cat:4,s:c.s,qid:best.id,
        reason:`名次 ${c.prevRankAbs} → ${c.now}（${c.d>0?'进步':'退步'} ${Math.abs(c.d)} 名）；这题他${sc===best.max?'满分':fmt(sc)+'/'+best.max+'分'}，比本班均分${bestV>=0?'+':''}${Math.round(bestV)} 分，本班 ${pct(cs.rate(best.id))}`};
    };
    for(const c of [...imp.slice(0,2).map(c=>({...c,prevRankAbs:c.s.prevRank})),
                    ...dec.slice(0,2).map(c=>({...c,prevRankAbs:c.s.prevRank}))])
      push(cardFor(c));
  }

  /* 5. 随机抽看 3：高/中/低段各 1，未出现过的学生，种子固定 */
  function pickRandomSample(){
    const segs=[['高分段',0,1/3],['中段',1/3,2/3],['低分段',2/3,1.01]];
    const unused=list.filter(s=>!useCount.has(s.id));
    for(const [label,a,b] of segs){
      const pool=unused.filter(s=>{const cs=classStats(s.cls),r=cs.rank[s.id]/cs.n;return r>a&&r<=b});
      const from=pool.length?pool:unused;
      if(!from.length)continue;
      const r=mulberry32(hashStr(EXAM_ID+'|'+scope+'|'+label));
      const s=from[Math.floor(r()*from.length)];
      unused.splice(unused.indexOf(s),1);
      push({cat:5,s,qid:null,reason:`随机抽看 · ${label}（本班第 ${cs_rank(s)} 名）`,whole:true});
    }
  }
}
