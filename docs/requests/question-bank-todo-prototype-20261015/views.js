P.progress = function () {
  const n = this.state.items.filter(q => q.kind === 'no_match').length;
  return `<div class="chapter-progress"><div class="progress-label"><span>本章累计待归类</span><span>${n} / 30 题</span></div><div class="progress-track"><span style="width:${Math.min(100,n/30*100)}%"></span></div><p class="detail-footnote">示例来源：3 份试卷。达到整理门槛后，另行确认整理任务。</p></div>`;
};
P.empty = function () {
  const group = this.state.group;
  return `<div class="empty">${this.icon(group === 'waiting' ? 'layers' : 'check')}<strong>${group === 'waiting' ? '当前没有已确认无匹配的题' : '这一类已处理完'}</strong>${group === 'waiting' ? '缺少历史结论，不等于没有合适题型。<br>先完成一次有明确结论的匹配。' : '可以继续查看其他处理类别。'}</div>`;
};
P.detail = function (item, inDialog = false) {
  if (!item) return this.empty();
  const g = this.groups[item.group];
  const basicReady = ['match','legacy','no_match','standard'].includes(item.kind);
  let nextText = '';
  let action = '';
  let note = '';
  if (item.group === 'ai') {
    nextText = '有匹配结论后，再决定完成入库，还是进入题型或标准整理。';
    action = `<button class="btn primary" data-action="repair-one" data-id="${item.id}">${this.icon('spark')}补齐这一题 · 演示</button>`;
    note = '只补缺少的分析或匹配，不重做已完成内容。';
  } else if (item.kind === 'standard') {
    nextText = '先确认题目归属，再决定选用已有知识点、补充词表或调整适用范围。';
    action = `<button class="btn" data-action="standard" data-id="${item.id}">${this.icon('layers')}查看标准缺口</button>`;
    note = '这类题需要整理标准，不应反复调用同一套词表补打。';
  } else if (item.group === 'waiting') {
    nextText = this.state.scene === 'current' ? '当前八上尚未接入自动按章整理。保留为待整理，后续安排；不转成必须人工逐题归类。' : '基础标签已经完成。与同章待归类题一起整理题型，再把题目归入整理结果。';
    action = `<button class="btn" data-action="chapter">${this.icon('layers')}查看本章整理条件</button>`;
    note = '已有明确无匹配结论，因此不进入 AI 补齐清单。';
  } else {
    nextText = '先补足题干或图片中的缺失条件。保存后，再进入基础分析。';
    action = `<button class="btn manual" data-action="source" data-id="${item.id}">${this.icon('file')}核对并补足原题</button>`;
    note = '这里需要教师核对题源，不要求教师手工补全所有标签。';
  }
  return `<article class="inspector${inDialog ? ' dialog-inspector' : ''}">
    <div class="inspector-head"><span class="badge ${g.tone}">${g.owner}</span><span class="inspector-id">示例 ${item.id}</span></div>
    <h3>${this.esc(item.title)}</h3><p class="source-line">${this.esc(item.source)} · 合成题目</p>
    <div class="status-points"><span class="${basicReady ? '' : 'pending'}">${this.icon(basicReady?'check':'clock')}判定点${basicReady?'已完成':'待分析'}</span><span class="${basicReady && item.kind !== 'standard' ? '' : 'pending'}">${this.icon(basicReady && item.kind !== 'standard'?'check':'clock')}知识点${item.kind==='standard'?'待标准整理':basicReady?'已完成':'待分析'}</span><span class="${basicReady?'':'pending'}">${this.icon(basicReady?'check':'clock')}难度等${basicReady?'已完成':'待分析'}</span></div>
    <div class="reason-block"><div class="reason-label">为什么停在这里</div><p>${this.reason(item)}</p></div>
    <div class="next-block ${g.tone}"><div class="reason-label">下一步</div><strong>${this.next(item)}</strong><p>${nextText}</p>${item.kind==='no_match' && this.state.scene==='demo'?this.progress():''}</div>
    <div class="detail-actions">${action}</div><p class="detail-footnote">${note}</p>
  </article>`;
};
P.list = function (items, title) {
  return `<section class="question-list" aria-label="题目列表"><div class="list-header"><strong>${title}</strong><span>${items.length} 题</span></div><div class="list-scroll">${items.length ? items.map(q => `<button class="question-row ${this.currentItem()?.id === q.id?'selected':''}" data-select="${q.id}" aria-pressed="${this.currentItem()?.id===q.id}"><div class="row-meta"><span>示例 ${q.id} · ${q.chapter.split(' · ')[1]}</span>${q.kind==='standard'?'<span class="badge waiting">知识标准</span>':''}</div><h4>${this.esc(q.title)}</h4><div class="row-reason">${this.shortReason(q)}</div></button>`).join('') : this.empty()}</div><div class="list-footer">选择题目，查看具体原因与下一步。</div></section>`;
};
P.viewA = function () {
  const s=this.state, items=s.items.filter(q=>q.group===s.group);
  return `<div class="inbox"><aside class="group-rail" aria-label="按处理动作分类"><div class="rail-label">先看处理方式</div>${Object.entries(this.groups).map(([id,g])=>`<button class="group-button ${id} ${s.group===id?'selected':''}" data-group="${id}" aria-pressed="${s.group===id}"><div class="group-button-head">${this.icon(g.icon)}<strong>${g.title}</strong><b>${this.count(id)}</b></div><p>${g.desc}</p><span class="owner">${g.short}</span></button>`).join('')}<div class="rail-note">只把有机会补齐的题交给 AI。<br>已确认的缺口，先整理。<br>原题有缺失，先核对。</div></aside>${this.list(items, this.groups[s.group].short)}${this.detail(this.currentItem())}</div>
    ${s.group==='ai' && items.length ? `<div class="detail-actions"><button class="btn primary" data-action="repair-all">${this.icon('spark')}查看 ${items.length} 题补齐清单</button><span class="subtle-note">仅包含这一类；待整理与题源问题不会加入。</span></div>`:''}`;
};
P.flowItems = function () {
  return this.state.items.filter(q => this.state.stage==='analysis' ? q.kind==='analysis' : this.state.stage==='match' ? ['legacy','match'].includes(q.kind) : this.state.stage==='chapter' ? q.kind==='no_match' : this.state.stage==='standard' ? q.kind==='standard' : q.group==='manual');
};
P.viewB = function () {
  const s=this.state;
  if (!s.stage) s.stage='match';
  const stages=[{id:'analysis',num:'01',title:'先完成基础分析',desc:'形成判定点、知识点、难度等标签',n:s.items.filter(q=>q.kind==='analysis').length},{id:'match',num:'02',title:'再匹配已有题型',desc:'能对应现有题型，即可完成归类',n:s.items.filter(q=>['legacy','match'].includes(q.kind)).length},{id:'chapter',num:'03',title:'无匹配，按章整理',desc:'汇集同章题目，统一整理题型与归属',n:s.items.filter(q=>q.kind==='no_match').length}];
  const items=this.flowItems();
  if (!items.some(q=>q.id===s.selected)) s.selected=items[0]?.id;
  s.group=items[0]?.group || (s.stage==='chapter'||s.stage==='standard'?'waiting':s.stage==='manual'?'manual':'ai');
  return `<div class="flow-caption">${this.icon('info')}题型在基础标签分析后处理；无匹配时，才进入同章统一整理。</div><div class="flow-track">${stages.map((step,i)=>`${i?`<div class="flow-arrow">${this.icon('arrow')}</div>`:''}<button class="flow-step ${step.id==='chapter'?'waiting':''} ${s.stage===step.id?'selected':''}" data-stage="${step.id}" aria-pressed="${s.stage===step.id}"><span class="step-num">${step.num}</span><h3>${step.title}</h3><p>${step.desc}</p><span class="stage-count"><b>${step.n}</b>题停在这里</span></button>`).join('')}</div><div class="flow-secondary"><button class="exception-route standard" data-stage="standard">${this.icon('layers')}知识词表未覆盖 <strong>${s.items.filter(q=>q.kind==='standard').length} 题</strong><span>→ 先整理知识标准</span></button><button class="exception-route" data-stage="manual">${this.icon('file')}原题有缺失 <strong>${this.count('manual')} 题</strong><span>→ 教师补足题源</span></button></div><div class="flow-detail"><div>${this.list(items, {analysis:'需要补基础分析',match:'需要补已有题型匹配',chapter:'等待同章题型整理',standard:'需要整理知识标准',manual:'需要核对原题'}[s.stage])}</div>${this.detail(items.find(q=>q.id===s.selected))}</div>`;
};
P.viewC = function () {
  const s=this.state, filter=s.tableFilter||'all';
  const items=s.items.filter(q=>filter==='all'||q.group===filter);
  const eligible=items.filter(q=>q.group==='ai');
  const selected=s.items.filter(q=>q.group==='ai'&&s.checked.has(q.id)).length;
  return `<section class="table-panel"><div class="table-toolbar"><div class="filter-chips">${[['all','全部'],['ai','可补齐'],['waiting','待整理'],['manual','核对原题']].map(([key,title])=>`<button class="filter-chip ${filter===key?'active':''}" data-filter="${key}" aria-pressed="${filter===key}">${title}<span>${key==='all'?s.items.length:this.count(key)}</span></button>`).join('')}</div><div class="selection-bar"><span>已选 ${selected} 题</span><button class="btn primary" data-action="repair-selected" ${!selected?'disabled':''}>${this.icon('spark')}补齐所选 · 演示</button></div></div><div class="table-scroll"><table><thead><tr><th><input type="checkbox" id="select-all" aria-label="选择可补齐的题目" ${eligible.length&&eligible.every(q=>s.checked.has(q.id))?'checked':''} ${!eligible.length?'disabled':''}></th><th>题目</th><th>基础分析</th><th>停住的原因</th><th>下一步</th><th>操作</th></tr></thead><tbody>${items.map(q=>`<tr><td>${q.group==='ai'?`<input type="checkbox" data-check="${q.id}" aria-label="选择示例 ${q.id}" ${s.checked.has(q.id)?'checked':''}>`:'<span aria-label="不加入补齐清单">—</span>'}</td><td><button class="item-title" data-action="detail" data-id="${q.id}">${this.esc(q.title)}</button><span class="item-meta">示例 ${q.id} · ${q.chapter}</span></td><td>${q.kind==='standard'?'知识点待整理':['legacy','match','no_match'].includes(q.kind)?'已完成':'未完成'}</td><td><span class="badge ${q.group}">${this.shortReason(q)}</span></td><td>${this.next(q)}</td><td><button class="row-action" data-action="detail" data-id="${q.id}">查看原因</button></td></tr>`).join('')}</tbody></table>${!items.length?this.empty():''}</div><div class="table-footer"><span>只有“可补齐”的题可以加入批量清单。</span><span>待处理 ${s.items.length} 题 · 本次演示完成 ${s.done} 题</span></div></section>`;
};
