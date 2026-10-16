P.render = function () {
  const s=this.state;
  document.querySelector('#scene').value=s.scene;
  document.querySelector('#volume').textContent=s.scene==='current'?'北师大版 · 八年级上册 · 状态分布复现':'北师大版 · 八年级下册 · 完整流程示例';
  document.querySelector('#scene-description').textContent=s.scene==='current'?'初始分布：12 题缺少可核验的题型匹配结论，1 题需核对题源。题面与编号均为合成示例。':'按处理原因分流。分析或匹配尚未完成的题可补齐；已有明确无匹配结论的题先整理。';
  document.querySelector('#tab-count').textContent=s.items.length;
  const completed=document.querySelector('#completed-count');
  completed.hidden=!s.done;
  completed.textContent='本次演示已完成 '+s.done+' 题';
  document.querySelector('#view').innerHTML=this['view'+s.variant]();
  document.querySelectorAll('[data-variant]').forEach(b=>{b.classList.toggle('active',b.dataset.variant===s.variant);b.setAttribute('aria-pressed',b.dataset.variant===s.variant);});
  const url=new URL(location.href);url.searchParams.set('variant',s.variant);url.searchParams.set('scenario',s.scene);
  history.replaceState(null,'',url);
};
P.reset = function () {
  const s=this.state;s.items=this.makeItems(s.scene);s.group='ai';s.selected=s.items[0]?.id;s.done=0;s.checked.clear();s.stage='match';s.tableFilter='all';
  clearTimeout(this.toastTimer);document.querySelector('#toast').hidden=true;
  document.querySelector('#dialog').close();this.render();
};
P.toast = function (message) {
  const el=document.querySelector('#toast');el.textContent=message;el.hidden=false;clearTimeout(this.toastTimer);this.toastTimer=setTimeout(()=>el.hidden=true,6500);
};
P.open = function (title,subtitle,body,footer='') {
  const dialog=document.querySelector('#dialog');
  document.querySelector('#dialog-content').innerHTML=`<div class="dialog-header"><div><h3>${title}</h3><p>${subtitle}</p></div><button class="close-button" data-action="close" aria-label="关闭对话框">${this.icon('close')}</button></div><div class="dialog-body">${body}</div>${footer?`<div class="dialog-footer">${footer}</div>`:''}`;
  if (!dialog.open) dialog.showModal();
};
P.repair = function (ids) {
  const items=this.state.items.filter(q=>q.group==='ai'&&ids.includes(q.id));
  if (!items.length) return;
  this.pendingIds=items.map(q=>q.id);
  const analyses=items.filter(q=>q.kind==='analysis').length;
  const matches=items.length-analyses;
  const body=`<p>本次仅处理 <b>${items.length} 题</b>，先完成缺少的环节，再按结果分流。</p><ul>${analyses?`<li><span>补基础分析，再匹配题型</span><b>${analyses} 题</b></li>`:''}${matches?`<li><span>补一次已有题型匹配</span><b>${matches} 题</b></li>`:''}<li><span>待题型／标准整理</span><span>不加入</span></li><li><span>需核对原题</span><span>不加入</span></li></ul><div class="preview-outcomes"><b>点击后观看示例结果</b><br>匹配成功 → 完成归类。<br>明确无匹配 → 转入“待题型／标准整理”，不转成人工逐题归类。<br>实际结果应以真实分析为准；此处结果为预设演示。</div>`;
  this.open('确认要补的内容','清单预览 · 不会发起真实 AI 请求',body,`<span class="subtle-note">本次演示费用为 0</span><button class="btn" data-action="close">返回</button><button class="btn primary" data-action="run-repair">演示补齐结果 ${this.icon('arrow')}</button>`);
};
P.runRepair = function () {
  const ids=this.pendingIds||[], items=this.state.items.filter(q=>ids.includes(q.id)&&q.group==='ai');
  const matchItems=items.filter(q=>q.kind!=='analysis');
  const waitingIds=new Set(matchItems.slice(-Math.min(2,matchItems.length)).map(q=>q.id));
  const doneIds=new Set(items.filter(q=>!waitingIds.has(q.id)).map(q=>q.id));
  this.state.items=this.state.items.filter(q=>!doneIds.has(q.id)).map(q=>waitingIds.has(q.id)?{...q,group:'waiting',kind:'no_match'}:q);
  this.state.done+=doneIds.size;this.state.checked.clear();this.state.group=waitingIds.size?'waiting':'ai';this.state.stage=waitingIds.size?'chapter':'analysis';this.state.selected=[...waitingIds][0];this.state.tableFilter=waitingIds.size?'waiting':'all';
  document.querySelector('#dialog').close();this.render();this.toast(`演示完成：${doneIds.size} 题完成归类，${waitingIds.size} 题明确无匹配并转入待题型整理。题源问题仍保留核对。`);
};
P.chapterDialog = function () {
  const isCurrent=this.state.scene==='current';
  const n=this.state.items.filter(q=>q.kind==='no_match').length;
  const body=isCurrent?`<div class="next-block waiting"><strong>当前八上尚未接入自动按章整理</strong><p>这里保留“待整理”原因与题目。后续需要安排章节整理，不能显示为已进入自动任务。</p></div><p>教师可以在有把握时选用已有题型，但“已确认无匹配”本身不构成必须人工逐题归类的理由。</p>`:`<p><b>第一章 · 三角形的证明</b></p><p class="subtle-note">本章已有题型。当前模拟的是：积累未归类题后，再进行本章统一整理。</p>${this.progress()}<ul><li><span>基础判定点资料可用</span><span class="badge neutral">已具备</span></li><li><span>至少来自 3 份试卷</span><span class="badge neutral">3 / 3 份</span></li><li><span>积累 30 道待归类题</span><span class="badge waiting">${n} / 30 题</span></li></ul><p class="subtle-note">按当前示例，还差 ${Math.max(0,30-n)} 道。满足条件后，再单独确认题型整理任务。已发布题型不会在此自动合并或拆分。</p>`;
  this.open('按章统一整理题型',isCurrent?'八上 · 尚未接入自动整理':'八下 · 当前章未达到整理门槛',body,`<button class="btn" data-action="close">知道了</button>`);
};
P.standardDialog = function (id) {
  this.open('先整理知识标准','词表缺口示例 · 拟议处理方式',`<p>示例 ${id} 的题意可以识别，但现有知识点词表没有对应条目。</p><ul><li><span>核对教材、册次与章节归属</span><span>先确认范围</span></li><li><span>检查是否可以采用现有条目</span><span>人工选择</span></li><li><span>确实未覆盖时，整理标准词表</span><span>补充或归并</span></li></ul><div class="next-block waiting"><strong>标准发生变化后，再重新匹配</strong><p>在词表和题目都不变的情况下，重复补打不能解决缺口。</p></div>`,`<button class="btn" data-action="close">知道了</button>`);
};
P.sourceDialog = function (id) {
  this.sourceId=id;
  this.open('核对并补足原题','题源问题示例 · 不要求手工补全标签',`<div class="question-preview">在 △ABC 和 △DEF 中，AB = DE，AC = DF。<br>已知 <span class="missing-condition">图中标注未能识别</span>，求证：△ABC ≌ △DEF。</div><p>没有第三项条件，无法确定全等依据。请先对照原题，补足缺失的信息。</p><label class="field-label" for="condition">补充条件（演示）</label><textarea id="condition" placeholder="例如：∠BAC = ∠EDF"></textarea><p class="detail-footnote">演示保存后，此题进入“可补分析与匹配”，重新分析修正后的题目。</p>`,`<button class="btn" data-action="close">取消</button><button class="btn primary" data-action="save-source" disabled>演示保存并转入分析</button>`);
};
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button||button.disabled)return;
  const d=button.dataset,s=P.state;
  if(d.variant){s.variant=d.variant;P.render();return;}
  if(d.group){s.group=d.group;s.selected=s.items.find(q=>q.group===d.group)?.id;P.render();return;}
  if(d.select){s.selected=d.select;P.render();return;}
  if(d.stage){s.stage=d.stage;s.selected=null;P.render();return;}
  if(d.filter){s.tableFilter=d.filter;P.render();return;}
  switch(d.action){
    case 'reset':P.reset();P.toast('已重置为场景初始状态。');break;
    case 'close':document.querySelector('#dialog').close();break;
    case 'repair-all':P.repair(s.items.filter(q=>q.group==='ai').map(q=>q.id));break;
    case 'repair-one':P.repair([d.id]);break;
    case 'repair-selected':P.repair([...s.checked]);break;
    case 'run-repair':P.runRepair();break;
    case 'chapter':P.chapterDialog();break;
    case 'standard':P.standardDialog(d.id);break;
    case 'source':P.sourceDialog(d.id);break;
    case 'detail':{const q=s.items.find(q=>q.id===d.id);P.open('查看处理原因','合成题目 · 示例 '+q.id,P.detail(q,true));break;}
    case 'save-source':{
      const text=document.querySelector('#condition').value.trim();if(!text)return;
      s.items=s.items.map(q=>q.id===P.sourceId?{...q,group:'ai',kind:'analysis'}:q);s.selected=P.sourceId;s.group='ai';s.stage='analysis';s.tableFilter='ai';document.querySelector('#dialog').close();P.render();P.toast('演示已保存。原题条件补足后，转入基础分析。');break;
    }
  }
});
document.addEventListener('change',event=>{
  const el=event.target,s=P.state;
  if(el.id==='scene'){s.scene=el.value;P.reset();return;}
  if(el.dataset.check){el.checked?s.checked.add(el.dataset.check):s.checked.delete(el.dataset.check);P.render();return;}
  if(el.id==='select-all'){
    const items=s.items.filter(q=>q.group==='ai'&&(!s.tableFilter||['all','ai'].includes(s.tableFilter)));
    items.forEach(q=>el.checked?s.checked.add(q.id):s.checked.delete(q.id));P.render();
  }
});
document.addEventListener('input',event=>{if(event.target.id==='condition')document.querySelector('[data-action="save-source"]').disabled=!event.target.value.trim();});
document.addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight'].includes(event.key)||event.target.closest('input,select,textarea,[contenteditable=true]')||document.querySelector('#dialog').open)return;
  const options=['A','B','C'],i=options.indexOf(P.state.variant);P.state.variant=options[(i+(event.key==='ArrowRight'?1:2))%3];P.render();event.preventDefault();
});
document.querySelector('#dialog').addEventListener('click',event=>{if(event.target===event.currentTarget){const rect=event.currentTarget.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.currentTarget.close();}});
P.reset();
