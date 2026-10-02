'use strict';
/* ===== 看卷流程：步骤摘要 + 卡片渲染 ===== */
const stepTexts={
  '9':['审题设元','建立函数关系式','整理化简求值','检验并作答'],
  '10':['移项整理为一般式','因式分解','写出两个根','检验并作答'],
  '11(1)':['识别图形关系','列出比例式','代入求值','作答'],
  '11(2)':['构造辅助线','证明三角形全等','推导目标线段','写出结果'],
  '12(1)':['设未知数','列方程','解方程','结合实际取舍'],
  '12(2)':['分析图形条件','构造辅助线','证明三角形全等','写出结论'],
  '12(3)':['利用上问结论','代入计算','配方化简','得出结果'],
};
/* 步骤摘要：与手写行共用 planFor 的掩码（每步整点有无） */
function stepsFor(card){
  const q=QMAP[card.qid],plan=planFor(card.s,card.qid);
  if(plan.kind==='obj'){
    const right=plan.mask[0];
    return [{t:`选择 ${plan.chosen}（正确答案 ${q.ans}）`,mk:right?'y':'n',p:`${right?q.max:0}/${q.max}`}];
  }
  const names=stepTexts[q.id],pts=STEP_PTS[q.id];
  return names.map((t,i)=>({t,mk:plan.mask[i]?'y':'n',p:`${plan.mask[i]?pts[i]:0}/${pts[i]}`}));
}
/* AI 判分理由：由同一掩码生成，不与步骤矛盾 */
function aiReason(card){
  const q=QMAP[card.qid],sc=card.s.scores[card.qid];
  if(q.kind==='o')return sc>=q.max?'选择正确。':'选择错误。';
  const names=stepTexts[q.id],done=planFor(card.s,card.qid).mask;
  const okN=names.filter((_,i)=>done[i]),badN=names.filter((_,i)=>!done[i]);
  const altTag=card.s.alt.has(card.qid)?'解题路径与参考答案不同，':'';
  if(sc>=q.max)return altTag+'过程完整规范，结果正确。';
  if(sc===0)return altTag+'作答与题意不符或关键步骤缺失，未得分。';
  const parts=[];
  if(okN.length)parts.push(okN.join('、')+'正确');
  if(badN.length)parts.push((card.cause?`在「${card.cause}」处失分，`:'')+badN.join('、')+'未完成');
  return altTag+parts.join('；')+'。';
}
function classRateText(card){
  const cs=classStats(card.s.cls),q=QMAP[card.qid];
  return `本班得分率 ${pct(cs.rate(card.qid))} · 满分 ${pct(cs.fullFrac(card.qid))}`;
}

/* ---------- 看卷流程 ---------- */
function openFlow(){
  const cards=buildDeck(state.scope,state.noPrev);
  revealed.clear();
  state.flow={cards,idx:0,end:false};
  state.startedAt=Date.now();
  $('#flow').classList.add('on');
  $('#flScope').textContent='· '+(state.scope||'全部');
  $('#flWarn').hidden=!state.pending;
  clearInterval(state.timerId);
  state.timerId=setInterval(()=>{const s=Math.floor((Date.now()-state.startedAt)/1000);
    $('#flTimer').textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')},500);
  renderFlow();
  $('#flow').querySelector('button').focus();
}
function closeFlow(){$('#flow').classList.remove('on');clearInterval(state.timerId);state.flow=null}
function elapsed(){const s=Math.floor((Date.now()-state.startedAt)/1000);return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}

function renderFlow(){
  const f=state.flow;if(!f)return;
  const body=$('#flBody');
  // 顶栏进度与类别 chips
  $('#flProg').textContent=f.end?`${f.cards.length} / ${f.cards.length}`:`${f.idx+1} / ${f.cards.length}`;
  const cats=$('#flCats');cats.innerHTML='';
  const counts={};f.cards.forEach((c,i)=>{(counts[c.cat]=counts[c.cat]||[]).push(i)});
  for(const cat in counts){
    const b=document.createElement('button');
    b.textContent=`${CAT_LABEL[cat].split(' ')[0]} ${counts[cat].length}`;
    if(!f.end&&f.cards[f.idx].cat===+cat)b.classList.add('cur');
    b.onclick=()=>{f.idx=counts[cat][0];f.end=false;renderFlow()};
    cats.appendChild(b);
  }
  if(f.end){renderEnd(body);return}
  const card=f.cards[f.idx];
  const rev=revealed.has(f.idx);
  const q=card.qid?QMAP[card.qid]:null;
  const sub=card.cat===1
    ?(rev&&card.cause?`第 ${card.qid} 题 · 错法「${card.cause}」`:`第 ${card.qid} 题 · 典型错法代表`)
    :card.qid?`第 ${card.qid} 题 · ${CAT_LABEL[card.cat]}`:`整卷 · ${CAT_LABEL[card.cat]}`;
  body.innerHTML=`
    <div class="card">
      <div class="chead">
        <span class="bd ai">卡 ${f.idx+1}</span>
        <span class="who">${card.s.name} <span>${card.s.cls} · 学号 ${card.s.code}</span></span>
        <span class="qlab">${sub}</span>
      </div>
      <div class="imgbox ${card.whole?'paper':''}" id="imgbox"></div>
      <div class="rzone"></div>
    </div>`;
  const box=$('#imgbox');
  if(card.whole){
    const [p1,p2]=paperSvg(card,false);
    box.innerHTML=`<div><div class="pg">${p1}</div><div class="pgcap">正面</div></div><div><div class="pg">${p2}</div><div class="pgcap">反面</div></div><span class="zoomhint">点击放大</span>`;
  }else{
    card._rev=rev;
    box.innerHTML=cropSvg(card,false)+'<span class="zoomhint">点击放大</span>';
  }
  box.onclick=()=>{
    $('#zoomBox').innerHTML=card.whole
      ?`<div style="display:flex;gap:12px">${paperSvg(card,true).join('')}</div>`
      :cropSvg(card,true);
    $('#zoom').classList.add('on');
  };
  const zone=body.querySelector('.rzone');
  if(!rev){
    zone.innerHTML=`<p class="guess">先看作答，${card.whole?'想一想这份卷子整体写得怎么样':'想一想他这题得几分'} <span class="gtip"><kbd>空格</kbd> 翻开分数</span></p>`;
    $('#flReveal').textContent='翻开分数';
  }else{
    $('#flReveal').textContent='收起分数';
    zone.innerHTML=card.whole?wholeRevealHtml(card):itemRevealHtml(card);
    const l=zone.querySelector('.gorev');if(l)l.onclick=e=>{e.stopPropagation();toast('正式版会打开该生该题的复核深查，改分仍走教师确认')};
  }
  $('#flPrev').disabled=f.idx===0;
  $('#flNext').disabled=false;$('#flReveal').disabled=false;
  $('#flNext').textContent=f.idx===f.cards.length-1?'看完 →':'下一张 →';
}
function itemRevealHtml(card){
  const q=QMAP[card.qid],sc=card.s.scores[card.qid];
  const cls=sc>=q.max?'ok':sc===0?'zero':'part';
  return `<div class="rev">
    <div class="score"><b class="${cls}">${fmt(sc)}</b><span class="max">/ ${q.max} 分</span>
      <span class="clsrate">${classRateText(card)}</span></div>
    <div class="steps"><ul>${stepsFor(card).map(st=>`<li><span class="mk ${st.mk}">${st.mk==='y'?'✓':'✗'}</span><span style="flex:1">${st.t}</span><span style="color:var(--ink3)">${st.p}</span></li>`).join('')}</ul></div>
    <p class="aireason">AI 判分理由：${aiReason(card)}</p>
    <p class="whyline"><span class="wl-tag">入选理由</span>${card.reason}<a class="link gorev" style="margin-left:12px">去复核 / 改分</a></p>
  </div>`;
}
function wholeRevealHtml(card){
  const s=card.s,cs=classStats(s.cls);
  return `<div class="rev">
    <div class="score"><b>${fmt(total(s))}</b><span class="max">/ 100 分</span>
      <span class="clsrate">本班第 ${cs.rank[s.id]} 名 · 班均 ${Math.round(cs.list.reduce((a,x)=>a+total(x),0)/cs.n)}</span></div>
    <div class="qstrip2">${QUESTIONS.map(q=>{const sc=s.scores[q.id];
      return `<span class="qc ${sc>=q.max?'full':sc===0?'zero':''}">${q.id} <b>${fmt(sc)}</b></span>`}).join('')}</div>
    <p class="whyline"><span class="wl-tag">入选理由</span>${card.reason}</p>
  </div>`;
}
function renderEnd(body){
  const f=state.flow;
  let rows='';
  for(const cat of Object.keys(counts_by(f))){
    const idxs=counts_by(f)[cat];
    rows+=`<div class="endcat"><div class="ect">${CAT_LABEL[cat]} · ${idxs.length} 张</div>`+
      idxs.map(i=>{const c=f.cards[i],q=c.qid?QMAP[c.qid]:null;
        const score=c.whole?`${fmt(total(c.s))}/100`:`${fmt(c.s.scores[c.qid])}/${q.max}`;
        return `<button class="endrow" data-i="${i}"><span class="en">${i+1}</span>
          <span>${c.s.name}</span><span style="color:var(--ink3)">${c.s.cls}</span>
          <span class="eq">${c.qid?'第 '+c.qid+' 题':'整卷'}</span>
          <span class="esc">${score}</span></button>`}).join('')+'</div>';
  }
  body.innerHTML=`<div class="endwrap">
    <h2>看完了 ${f.cards.length} 张，用时 ${elapsed()}</h2>
    <p class="esub">看卷不改分、不留痕；要改分请走复核页。点任意一行可回到那张卡。</p>
    ${rows}
    <button class="btn primary" id="backOv" style="margin-top:6px">返回考情总览</button>
  </div>`;
  body.querySelectorAll('.endrow').forEach(b=>b.onclick=()=>{f.idx=+b.dataset.i;f.end=false;revealed.add(f.idx);renderFlow()});
  $('#backOv').onclick=closeFlow;
  $('#flPrev').disabled=true;$('#flNext').disabled=true;$('#flReveal').disabled=true;
}
function counts_by(f){const m={};f.cards.forEach((c,i)=>{(m[c.cat]=m[c.cat]||[]).push(i)});return m}
function flowNext(){const f=state.flow;if(f.idx>=f.cards.length-1){f.end=true}else f.idx++;renderFlow()}
function flowPrev(){const f=state.flow;if(f.idx>0)f.idx--;renderFlow()}
function flowToggleReveal(){const f=state.flow;if(f.end)return;
  revealed.has(f.idx)?revealed.delete(f.idx):revealed.add(f.idx);renderFlow()}
