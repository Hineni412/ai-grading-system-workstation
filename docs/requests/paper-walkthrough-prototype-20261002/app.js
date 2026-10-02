'use strict';
/* ================= 状态与渲染 ================= */
const $=s=>document.querySelector(s);
const state={scope:null,tab:'overview',pending:true,noPrev:false,
  flow:null /*{cards,idx,end}*/, drawerSid:null, startedAt:0, timerId:null};
const revealed=new Set();      // 已翻开的卡片索引

/* ---------- 总览渲染 ---------- */
function renderOverview(){
  const list=scopeStudents(state.scope);
  const avg=list.reduce((a,s)=>a+total(s),0)/list.length;
  $('#sumline').innerHTML=`参考 <b>${list.length}</b> 人 · 平均 <b>${Math.round(avg)}</b> / 100 · 当前范围：${state.scope||'全部'}`;
  // 复核磁贴
  const tile=$('#tileReview');
  tile.querySelectorAll('.tnum,.tsub,.tact,.warnline').forEach(e=>e.remove());
  const tnum=document.createElement('strong');tnum.className='tnum';
  const tsub=document.createElement('span');tsub.className='tsub';
  const act=document.createElement('div');act.className='tact';
  const goBtn=document.createElement('button');goBtn.className='btn primary small';goBtn.textContent='看卷 10 分钟';
  goBtn.onclick=openFlow;
  if(state.pending){
    tnum.textContent='3 题待复核';
    tsub.textContent='含 AI 低置信 2 · 处理失败 1';
    const l1=document.createElement('a');l1.className='link';l1.textContent='去复核';
    l1.onclick=()=>toast('原型：将打开复核页（教师待办队列）');
    const l2=document.createElement('a');l2.className='link';l2.textContent='抽查复核';
    l2.onclick=()=>toast('原型：将打开复核页（抽查模式）');
    act.append(goBtn,l1,l2);
  }else{
    tnum.textContent='已全部确认';
    tsub.textContent='教师确认 96 · AI 评分 234';
    const l2=document.createElement('a');l2.className='link';l2.textContent='抽查复核';
    l2.onclick=()=>toast('原型：将打开复核页（抽查模式）');
    act.append(goBtn,l2);
  }
  tile.append(tnum,tsub,act);
  // 最弱题目
  const weak=[...QUESTIONS].sort((a,b)=>qRate(a.id,list)-qRate(b.id,list)).slice(0,3);
  $('#weakq').innerHTML=weak.map(q=>`<span>第 ${q.id} 题 <b>${pct(qRate(q.id,list))}</b></span>`).join('');
  // 低分
  const low=list.filter(s=>totalRate(s)<0.4);
  $('#lowNum').textContent=low.length+' 人';
  $('#lowLink').onclick=()=>toast('原型：将打开低分学生名单');
  renderTables();
}
function renderTables(){
  const rows=tbody=>{
    const list=[...scopeStudents(state.scope)].sort((a,b)=>classStats(a.cls).rank[a.id]-classStats(b.cls).rank[b.id]||a.cls.localeCompare(b.cls));
    tbody.innerHTML='';
    for(const s of list){
      const cs=classStats(s.cls),rk=cs.rank[s.id];
      const d=s.prevTotal!=null?total(s)-s.prevTotal:null;
      const tr=document.createElement('tr');
      tr.innerHTML=`<td class="name"><button data-sid="${s.id}">${s.name}</button> <span class="meta">${s.code}</span></td>
        <td>${s.cls}</td><td><b>${fmt(total(s))}</b></td><td>第 ${rk} 名</td>
        <td>${state.noPrev?'<span class="meta">—</span>':d==null?'<span class="meta">上次缺考</span>':`<span class="delta ${d>=0?'up':'down'}">${d>=0?'+':''}${fmt(d)}</span>`}</td>
        <td>${isPending(s)?'<span class="st pend">待复核</span>':'<span class="st final">已确认</span>'}</td>`;
      tr.querySelector('button').onclick=()=>openDrawer(s.id);
      tbody.appendChild(tr);
    }
  };
  rows($('#miniTable tbody'));rows($('#detailTable tbody'));
}
function isPending(s){return state.pending&&PENDING_ITEMS.some(([c,i])=>s.cls===c&&s.idx===i)}

/* ---------- 学生抽屉 ---------- */
function openDrawer(sid){
  const s=STUDENTS.find(x=>x.id===sid),cs=classStats(s.cls);
  // 对比行：较上次 / 名次 / 意外失分（按规则 2 条件，不受名额限制）
  let cmpHtml;
  if(state.noPrev){cmpHtml='<span>暂无可对比考试</span>'}
  else if(s.prevRank==null){cmpHtml='<span>上次缺考，暂无对比</span>'}
  else{
    const d=total(s)-s.prevTotal,rk=cs.rank[s.id];
    cmpHtml=`<span>较上次 <span class="${d>=0?'up':'down'}">${d>=0?'+':''}${fmt(d)} 分</span> · 班内名次 ${s.prevRank} → ${rk}</span>`;
  }
  // 意外失分：该生在班内满足规则 2 的最严重一题
  let worst=null;
  for(const q of QUESTIONS){
    if(s.scores[q.id]>=q.max)continue;
    const ff=cs.fullFrac(q.id);
    if(ff<0.75||totalRate(s)<cs.medRate)continue;
    const key=ff*(q.max-s.scores[q.id])/q.max;
    if(!worst||key>worst.key)worst={q,ff,key};
  }
  if(worst)cmpHtml+=`<span>这次意外失分：第 ${worst.q.id} 题（本班 ${pct(worst.ff)} 满分，他 ${fmt(s.scores[worst.q.id])}/${worst.q.max}）</span>`;
  const items=QUESTIONS.map(q=>{
    const pend=state.pending&&PENDING_ITEMS.some(([c,i,qd])=>s.cls===c&&s.idx===i&&qd===q.id);
    const rsn=s.alt.has(q.id)?'AI 标记：参考答案以外的解法':
      s.scores[q.id]===q.max?'满分':'点击查看答卷与评分依据';
    return `<div class="di"><span class="dq">第 ${q.id} 题</span>
      <span class="ds"><b>${fmt(s.scores[q.id])}</b> / ${q.max}</span>
      <span class="st ${pend?'pend':'final'}">${pend?'待复核':'已确认'}</span>
      <span class="dr">${rsn}</span></div>`;
  }).join('');
  $('#drawer').innerHTML=`
    <div class="dhead"><div>
      <div class="eb">学生成绩详情</div><h2>${s.name}</h2><p>学号 ${s.code} · ${s.cls}</p>
    </div><button class="btn small dclose" id="dclose">关闭</button></div>
    <div class="dtotal"><span>总分</span><strong>${fmt(total(s))}<small> / 100</small></strong>
      <em>本班第 ${cs.rank[s.id]} 名 / ${cs.n} 人</em></div>
    <div class="dcmp">${cmpHtml}</div>
    <div class="ditems">${items}</div>`;
  $('#drawerWrap').classList.add('on');
  $('#dclose').onclick=()=>$('#drawerWrap').classList.remove('on');
  $('#dclose').focus();
}

/* ---------- toast / 事件 ---------- */
let toastT=null;
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('on');
  clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('on'),2600)}

document.addEventListener('keydown',e=>{
  if($('#zoom').classList.contains('on')){if(e.key==='Escape'||e.key===' '){e.preventDefault();$('#zoom').classList.remove('on')}return}
  if($('#drawerWrap').classList.contains('on')){if(e.key==='Escape')$('#drawerWrap').classList.remove('on');return}
  if(!state.flow)return;
  if(e.key===' '){e.preventDefault();flowToggleReveal()}
  else if(e.key==='ArrowRight'){e.preventDefault();flowNext()}
  else if(e.key==='ArrowLeft'){e.preventDefault();flowPrev()}
  else if(e.key==='Escape'){e.preventDefault();closeFlow()}
});
$('#flReveal').onclick=flowToggleReveal;
$('#flNext').onclick=()=>{if(state.flow&&!state.flow.end)flowNext()};
$('#flPrev').onclick=flowPrev;
$('#flExit').onclick=closeFlow;
$('#zoom').onclick=()=>$('#zoom').classList.remove('on');
$('#drawerWrap').addEventListener('mousedown',e=>{if(e.target.id==='drawerWrap')$('#drawerWrap').classList.remove('on')});

document.querySelectorAll('.scopebar').forEach(bar=>bar.addEventListener('click',e=>{
  const b=e.target.closest('.scp');if(!b)return;
  state.scope=b.dataset.s||null;
  document.querySelectorAll('.scp').forEach(x=>x.classList.toggle('on',x.dataset.s===(state.scope||'')));
  renderOverview();
}));
$('#tabs').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  state.tab=b.dataset.v;
  document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.id==='v-'+state.tab));
});
$('#ctlPending').onchange=e=>{state.pending=e.target.checked;renderOverview()};
$('#ctlNoPrev').onchange=e=>{state.noPrev=e.target.checked;renderOverview()};

/* ---------- 验证辅助（供原型自查，控制台可调用） ---------- */
window.__deck=(scope)=>buildDeck(scope||null,state.noPrev).map(c=>({cat:c.cat,stu:c.s.id,q:c.qid,reason:c.reason}));
window.__state=state;

renderOverview();
