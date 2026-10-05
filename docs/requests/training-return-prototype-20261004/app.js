/* app.js — 状态、渲染、事件分发、快捷键、原型控制 */

let W = null;
const S = {
  demo: 'review', sel: { type: 'stu', id: 's02' },
  qf: 'all', statf: null, pg: 1, onlyPend: false, dtab: 'mastery',
  adjust: false, checks: [], files: [], upOpen: false, mok: null, timers: [],
  freshSel: false, // 刚换了选中行：渲染后滚动到首个待复核点并聚焦（不动页面滚动）
};

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// ---- 状态 A：回收答卷（无批次） ----
function renderStateA() {
  const n = S.checks.filter(Boolean).length;
  const grid = S.adjust ? `<div class="adjgrid">${STUDENTS.map(s =>
    `<label class="adj"><input type="checkbox" data-ch="adj" data-id="${s.id}" ${S.checks[s.ix] ? 'checked' : ''}>
      ${esc(s.name)} · ${s.ver} · ${s.pages} 页</label>`).join('')}</div>` : '';
  const zone = S.files.length
    ? `<div class="dropzone has"><b>${S.files.map(esc).join('、')}</b><span>已选 ${S.files.length} 个文件</span></div>`
    : `<div class="dropzone" data-act="files">选择扫描文件<span>PDF / JPG / PNG，可多选</span></div>`;
  return `<section class="panel recpanel"><div class="ph"><h2>回收答卷</h2></div>
    <div class="recline">本次回收 <b>${n}</b> 份训练卷（每人最新版本）
      <button class="link" type="button" data-act="adjust">调整 ▾</button></div>
    ${grid}${zone}
    <button class="btn primary" type="button" data-act="importBatch" ${S.files.length ? '' : 'disabled'}>导入并归组（${S.files.length || 3} 个文件）</button></section>`;
}

function renderAll() {
  const a = S.demo === 'nobatch';
  $('#stateA').hidden = !a; $('#stateB').hidden = a;
  if (a) { $('#stateA').innerHTML = renderStateA(); return; }
  $('#sumRow').innerHTML = renderSummary();
  $('#colQ').innerHTML = `<section class="panel queue">${renderQueue()}</section>`;
  $('#colS').innerHTML = `<div class="sheetcol">${renderSheet()}</div>`;
  $('#colL').innerHTML = `<section class="panel ledger">${renderLedger()}</section>`;
  if (S.freshSel) {
    S.freshSel = false;
    const sub = selSub();
    if (sub && ['review', 'update'].includes(sub.status)) {
      const lb = $('#colL .lbody'), fp = $('#colL .pt.pend');
      if (lb && fp) {
        lb.scrollTop += fp.getBoundingClientRect().top - lb.getBoundingClientRect().top - 8;
        fp.focus({ preventScroll: true });
      }
    }
  }
}

// 只滚动台账的 .lbody，让元素进入可视区（不改页面滚动）
function scrollPendIntoView(el) {
  const lb = $('#colL .lbody'); if (!lb || !el) return;
  const r = el.getBoundingClientRect(), b = lb.getBoundingClientRect();
  if (r.top < b.top || r.bottom > b.bottom) lb.scrollTop += r.top - b.top - 8;
}

function reset(kind) {
  S.timers.forEach(clearTimeout); S.timers = [];
  S.demo = kind; W = buildWorld(kind);
  S.qf = 'all'; S.statf = null; S.pg = 1; S.onlyPend = false; S.dtab = 'mastery';
  S.upOpen = false; S.mok = null; closeModal();
  if (kind === 'nobatch') { S.checks = STUDENTS.map(() => true); S.files = []; }
  const first = W.batch
    ? (W.anoms[0] ? { type: 'anom', id: W.anoms[0].id }
      : { type: 'stu', id: (sortSubs(W.subs).find(s => NEED.includes(s.status)) || W.subs[0]).id })
    : { type: 'stu', id: 's01' };
  S.sel = first; S.freshSel = true;
  $$('#protoCtl input[name=ctlstate]').forEach(r => { r.checked = r.value === kind; });
  renderAll();
}

// ---- 动作分发 ----
const COST_ONE = '将发送本份训练答卷、题目和判定点，调用模型 1 次并产生费用；实际费用以模型服务商计费为准；失败后不会自动重试。';
function doAct(act, el) {
  const sub = selSub();
  switch (act) {
    case 'adjust': S.adjust = !S.adjust; return renderAll();
    case 'files': S.files = FAKE_FILES.slice(); return renderAll();
    case 'importBatch': return S.files.length ? reset('imported') : undefined;
    case 'stat': S.statf = S.statf === el.dataset.f ? null : el.dataset.f; return renderAll();
    case 'sel': { const [t, id] = el.dataset.q.split(':'); return selSet(t === 'anom' ? 'anom' : 'stu', id); }
    case 'firstAnom': return W.anoms[0] && selSet('anom', W.anoms[0].id);
    case 'nextNeed': return navNeed();
    case 'pg': S.pg = +el.dataset.pg; return renderAll();
    case 'upload': S.upOpen = !S.upOpen; return renderAll();
    case 'chooseUp': {
      S.upOpen = false;
      const m = W.subs.find(s => s.status === 'missing');
      if (m) { m.recv++; m.status = 'pending'; toast(`补传页已匹配到 ${m.name} 的第 ${m.missPage} 页`); m.missPage = null; }
      else { W.anoms.push({ id: 'a' + Date.now(), page: 9, issue: '页面身份无法读取', tag: '补传文件' }); toast('已补传 2 个文件：1 页已归组，1 页待匹配'); }
      return renderAll();
    }
    case 'judgeOne': case 'retryOne':
      return costModal(`<div class="mtitle">判定 ${esc(sub.name)} · ${sub.ver}</div><p>${COST_ONE}</p>`,
        '确认判定', () => judgeOneNow(sub));
    case 'batchJudge': {
      const list = sortSubs(W.subs).filter(s => s.status === 'pending');
      return costModal(`<div class="mtitle">判定 ${list.length} 份</div>
          <div class="mnames">${list.map(s => esc(s.name)).join('、')}</div>
          <p>将逐份发送这 ${list.length} 份答卷、题目和判定点，共 ${list.length} 次模型请求，产生费用；实际费用以模型服务商计费为准。失败的不会自动重试。</p>`,
        `确认判定 ${list.length} 份`, batchJudgeRun);
    }
    case 'batchUpdate': {
      const ups = W.subs.filter(s => s.status === 'update');
      const rev = W.subs.filter(s => s.status === 'review').length;
      ups.forEach(s => { s.status = 'done'; });
      toast(`已更新 ${ups.length} 份掌握度${rev ? ` · 另有 ${rev} 份待复核，需逐份确认` : ''}`);
      return renderAll();
    }
    case 'seg': return lockPoint(sub, +el.dataset.q, +el.dataset.p, el.dataset.v);
    case 'punc': {
      const pt = ptab(sub)[el.dataset.q + ':' + el.dataset.p];
      pt.st = null; pt.locked = false; pt.unc = 't-unc';
      if (sub.status === 'done') { sub.status = 'update'; sub.edited = true; }
      else if (sub.status === 'update') sub.status = 'review';
      toast('已标为不确定，回到待复核');
      return renderAll();
    }
    case 'confirmUpdate': sub.status = 'done'; sub.edited = false; S.dtab = 'mastery'; toast(`已更新 1 份掌握度`); return renderAll();
    case 'cancelSub': sub.status = 'canceled'; toast(`已取消 ${sub.name} 的提交`); return renderAll();
    case 'dtab': S.dtab = el.dataset.v; return renderAll();
    case 'withdraw': sub.status = 'update'; toast('已撤回本次证据：掌握度回到训练前，需重新确认并更新（演示）'); return renderAll();
    case 'anMatch': case 'anReplace': case 'anIgnore': {
      const a = W.anoms.find(x => x.id === S.sel.id); if (!a) return;
      const tgt = W.subs.find(s => s.id === $('.ansel').value);
      const pg = +$('.anpg').value || 1;
      if (act === 'anMatch') {
        if (tgt.status === 'missing' && pg === tgt.missPage) {
          tgt.recv++; tgt.missPage = null; tgt.status = 'pending';
          toast(`已匹配到 ${tgt.name} 的第 ${pg} 页，该份可判定`);
        } else toast(`已匹配到 ${tgt.name} 的第 ${pg} 页（演示）`);
      } else if (act === 'anReplace') toast(`已替换 ${tgt.name} 的第 ${pg} 页（演示）`);
      else toast('已忽略此页');
      dropAnom(a); return renderAll();
    }
    case 'mOk': { const f = S.mok; closeModal(); S.mok = null; return f && f(); }
    case 'mCancel': return closeModal();
  }
}

// ---- 事件 ----
document.addEventListener('click', e => {
  const pt = e.target.closest('.ptt');
  if (pt) { pt.classList.toggle('exp'); return; }
  const b = e.target.closest('[data-act]');
  if (b) { doAct(b.dataset.act, b); return; }
  const t = e.target.closest('[data-t]');
  if (t) { if (t.tagName === 'A') e.preventDefault(); toast('原型：' + t.dataset.t); }
});
document.addEventListener('change', e => {
  const el = e.target.closest('[data-ch]'); if (!el) return;
  const ch = el.dataset.ch;
  if (ch === 'qf') { S.qf = el.value; S.statf = null; }
  if (ch === 'onlyPend') S.onlyPend = el.checked;
  if (ch === 'adj') { S.checks[+el.dataset.id.slice(1) - 1] = el.checked; }
  if (ch === 'anSub') { S.anSub = el.value; const t = W.subs.find(s => s.id === el.value); if (t) $('.anpg').value = t.missPage || 1; return; }
  if (ch === 'anPage') return;
  renderAll();
});
document.addEventListener('input', e => {
  const el = e.target.closest('.notein'); if (!el) return;
  const sub = selSub(); if (!sub || !judged(sub)) return;
  const [q, p] = el.dataset.note.split(':');
  ptab(sub)[q + ':' + p].note = el.value;
});
document.addEventListener('keydown', e => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  const k = e.key.toLowerCase();
  if (k === 'j') { navRow(1); e.preventDefault(); }
  if (k === 'k') { navRow(-1); e.preventDefault(); }
  if (e.key === 'Tab') {
    const pends = $$('.pt.pend'); if (!pends.length) return;
    const cur = pends.indexOf(e.target.closest('.pt'));
    const nx = cur === -1 ? (e.shiftKey ? pends[pends.length - 1] : pends[0])
      : pends[(cur + (e.shiftKey ? -1 : 1) + pends.length) % pends.length];
    nx.focus(); e.preventDefault();
  }
  if (['1', '2', '3'].includes(e.key)) {
    const ptEl = e.target.closest('.pt'); const sub = selSub();
    if (!ptEl || !sub || !judged(sub)) return;
    const [q, p] = ptEl.dataset.pt.split(':');
    lockPoint(sub, +q, +p, { '1': 'hit', '2': 'miss', '3': 'unread' }[e.key]);
    e.preventDefault();
  }
});

// ---- 原型控制 ----
$$('#protoCtl input[name=ctlstate]').forEach(r => r.addEventListener('change', e => reset(e.target.value)));
$('#ctlSrc').addEventListener('change', e => document.body.classList.toggle('showsrc', e.target.checked));
$('#pcToggle').addEventListener('click', () => $('#protoCtl').classList.toggle('open'));

reset('review');
