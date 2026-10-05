/* actions.js — 交互动作：导入/选择/判定/锁定/异常页/更新（全部本地模拟） */

const FAKE_FILES = ['scan-01.pdf', 'scan-02.jpg', 'scan-03.png'];

// ---- 选择与定位 ----
function selSet(type, id) {
  S.sel = { type, id };
  S.pg = 1; S.onlyPend = false; S.dtab = 'mastery'; S.freshSel = true;
  renderAll();
}
function rowsOrder() { // 队列可视顺序：异常页 + 排序后学生
  return W.anoms.map(a => 'anom:' + a.id).concat(sortSubs(W.subs).filter(queueFilter()).map(s => 'stu:' + s.id));
}
function navRow(step) {
  const rows = rowsOrder(); if (!rows.length) return;
  const cur = rows.indexOf((S.sel.type === 'anom' ? 'anom:' : 'stu:') + S.sel.id);
  const next = rows[(cur + step + rows.length) % rows.length].split(':');
  selSet(next[0] === 'anom' ? 'anom' : 'stu', next[1]);
}
function navNeed() {
  const rows = rowsOrder().filter(r =>
    r.startsWith('anom:') || NEED.includes(W.subs.find(s => s.id === r.slice(4)).status));
  if (!rows.length) { toast('原型：没有待处理的答卷'); return; }
  const cur = rows.indexOf((S.sel.type === 'anom' ? 'anom:' : 'stu:') + S.sel.id);
  const next = rows[(cur + 1 + rows.length) % rows.length].split(':');
  selSet(next[0] === 'anom' ? 'anom' : 'stu', next[1]);
}
const selSub = () => (W.batch && S.sel.type === 'stu' ? W.subs.find(s => s.id === S.sel.id) : null);

// ---- 判定模拟 ----
function applyOutcome(sub, kind) {
  sub.spec = kind === 'review' ? SPEC_2.slice() : [];
  sub._pt = null;
  if (kind !== 'failed') sub.status = kind; else sub.status = 'failed';
}
function judgeTimer(sub, kind, ms, done) {
  sub.status = 'judging'; renderAll();
  S.timers.push(setTimeout(() => { applyOutcome(sub, kind); if (done) done(); renderAll(); }, ms));
}
function judgeOneNow(sub) {
  judgeTimer(sub, 'review', 1500,
    () => toast(`判定完成：${sub.name} · 达成 ${gotOf(sub)}/${TOTAL_PTS} 点，待复核 ${pendOf(sub).length} 点`));
}
function batchJudgeRun() {
  const list = sortSubs(W.subs).filter(s => s.status === 'pending');
  const failAt = Math.min(2, list.length - 1); // 恰好一件失败（演示）
  W.run = { done: 0, total: list.length };
  let i = 0;
  const step = () => {
    if (i >= list.length) { W.run = null; renderAll(); return; }
    const sub = list[i], kind = i === failAt ? 'failed' : i % 2 === 0 ? 'review' : 'update';
    judgeTimer(sub, kind, 700, () => { W.run = { done: ++i, total: list.length }; step(); });
  };
  step();
}

// ---- 确认弹窗 ----
function openModal(html) { $('#modalWrap').innerHTML = `<div class="mmask" data-act="mCancel"></div><div class="mbox">${html}</div>`; }
const closeModal = () => { $('#modalWrap').innerHTML = ''; };
function costModal(body, okLabel, onOk) {
  S.mok = onOk;
  openModal(`${body}<div class="mbtns"><button class="btn" type="button" data-act="mCancel">取消</button>
    <button class="btn primary" type="button" data-act="mOk">${esc(okLabel)}</button></div>`);
}

// ---- 点级判定 ----
function lockPoint(sub, q, p, v) {
  const pt = ptab(sub)[q + ':' + p];
  const from = aiCallOf(pt);
  pt.st = v; pt.locked = true; pt.unc = null;
  toast(`已锁定：${from} → 教师 ${SEG_TXT[v]}`);
  if (sub.status === 'done') { sub.status = 'update'; sub.edited = true; }
  else if (sub.status === 'review' && !pendOf(sub).length) sub.status = 'update';
  renderAll();
  const rows = $$('.pt');
  const idx = rows.findIndex(r => r.dataset.pt === q + ':' + p);
  const nxt = rows.slice(idx + 1).find(r => r.classList.contains('pend')) || $$('.pt.pend')[0];
  if (nxt) { scrollPendIntoView(nxt); nxt.focus({ preventScroll: true }); }
}

// ---- 异常页 ----
function dropAnom(a) {
  W.anoms = W.anoms.filter(x => x.id !== a.id);
  if (S.sel.type === 'anom' && S.sel.id === a.id) {
    const first = W.anoms[0] || sortSubs(W.subs).find(s => NEED.includes(s.status)) || W.subs[0];
    S.sel = first && first.id.startsWith('a') ? { type:'anom', id:first.id } : { type:'stu', id:first.id };
    S.pg = 1; S.onlyPend = false; S.freshSel = true;
  }
}
