/* queue.js — 汇总行与左栏学生队列 */

// ---- 汇总行：批次选择 + 可点击计数 + 进度细条 + 补传 + 单一主按钮 ----
const STATS = [
  ['anom',    c => `异常页 ${c.anom} 页`],
  ['missing', c => `缺页 ${c.missing} 份`],
  ['pending', c => `待判定 ${c.pending} 份`],
  ['judging', c => `判定中 ${c.judging} 份`],
  ['failed',  c => `判定失败 ${c.failed} 份`],
  ['review',  c => `待复核 ${c.review} 份`],
  ['update',  c => `待更新 ${c.update} 份`],
  ['done',    c => `已完成 ${c.done} 份`],
];

function renderSummary() {
  const c = counts(W);
  const chips = STATS.filter(([k]) => c[k] > 0)
    .map(([k, f]) => `<button class="stat ${S.statf === k ? 'on' : ''}" type="button" data-act="stat" data-f="${k}">${f(c)}</button>`).join('');
  const donePct = Math.round(c.done / W.subs.length * 100);
  const run = W.run ? `<span class="runtag">正在判定 ${W.run.done}/${W.run.total} 份</span>` : '';
  let primary = '', secondary = '';
  if (c.anom > 0) {
    primary = `<button class="btn primary small" type="button" data-act="firstAnom">处理异常页（${c.anom}）</button>`;
  } else if (c.pending > 0) {
    primary = `${srcTag(SRC.judge)}<button class="btn primary small" type="button" data-act="batchJudge" ${W.run ? 'disabled' : ''}>判定 ${c.pending} 份（${c.pending} 次模型请求）</button>`;
    if (c.update > 0) secondary = `${srcTag(SRC.update)}<button class="btn small" type="button" data-act="batchUpdate">确认并更新 ${c.update} 份掌握度</button>`;
  } else if (c.update > 0) {
    primary = `${srcTag(SRC.update)}<button class="btn primary small" type="button" data-act="batchUpdate">确认并更新 ${c.update} 份掌握度</button>`;
  }
  const upload = S.upOpen
    ? `<div class="upzone" data-act="chooseUp">选择扫描文件<span>PDF / JPG / PNG，可多选</span></div>` : '';
  return `<div class="sumbar">
      <select class="batchsel" data-t="将切换 扫描批次"><option>扫描批次 · 10月4日 14:20 · 20 人</option></select>
      <span class="stats">${chips}${run}
        <span class="donebar"><i style="width:${donePct}%"></i></span><span class="dbt">已完成 ${c.done}/${W.subs.length} 份</span>
        ${srcTag(SRC.counts)}</span>
      <span class="sumact"><button class="btn ghost small" type="button" data-act="upload">补传答卷</button>
      ${secondary}${primary}</span></div>
    ${upload}`;
}

// ---- 左栏：队列 ----
function queueFilter() {
  if (S.statf) return s => (S.statf === 'anom' ? false : s.status === S.statf);
  if (S.qf === 'need') return s => NEED.includes(s.status);
  if (S.qf === 'done') return s => s.status === 'done';
  return () => true;
}
const showAnoms = () => S.statf ? S.statf === 'anom' : S.qf !== 'done';

function renderQueue() {
  const subs = sortSubs(W.subs).filter(queueFilter());
  const anoms = showAnoms() ? W.anoms : [];
  const rows = anoms.map(a => `
    <button class="qrow ${S.sel.type === 'anom' && S.sel.id === a.id ? 'sel' : ''}" type="button" data-act="sel" data-q="anom:${a.id}">
      <span class="q1"><span class="qn">上传文件第 ${a.page} 页</span>${pill('异常页', 'warn')}</span>
      <span class="qm">${esc(a.issue)}</span></button>`).join('')
    + subs.map(s => {
      const st = STATUS[s.status];
      const label = s.status === 'review' ? `待复核 ${pendOf(s).length} 点` : st.t;
      const meta = judged(s) ? ` · 达成 ${gotOf(s)}/${TOTAL_PTS} 点` : '';
      return `<button class="qrow ${S.sel.type === 'stu' && S.sel.id === s.id ? 'sel' : ''} ${s.status === 'judging' ? 'judging' : ''}"
        type="button" data-act="sel" data-q="stu:${s.id}">
        <span class="q1"><span class="qn">${esc(s.name)}</span>${pill(label, st.tone)}</span>
        <span class="qm">${s.cls} · ${s.sid}${meta}</span>
        ${s.status === 'judging' ? '<span class="qbar"><i></i></span>' : ''}</button>`;
    }).join('');
  const anomGrp = anoms.length ? `<div class="subh">异常页</div>` : '';
  const need = W.anoms.length || W.subs.some(s => NEED.includes(s.status));
  return `<div class="ph"><h2>学生</h2><span class="qc">${W.subs.length} 人</span>
      <select class="qf" data-ch="qf">${[['all','全部'],['need','需要处理'],['done','已完成']]
        .map(([v, t]) => `<option value="${v}" ${S.qf === v && !S.statf ? 'selected' : ''}>${t}</option>`).join('')}</select>
      ${srcTag(SRC.queue)}</div>
    <div class="qrows">${anomGrp}${rows}</div>
    ${need ? `<button class="btn small nextneed" type="button" data-act="nextNeed">下一份待处理 ›</button>` : ''}`;
}
