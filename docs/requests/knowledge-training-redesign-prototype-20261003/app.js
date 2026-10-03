/* app.js — 壳、范围条、页签、抽屉、提示（共享） */
const S = { view: 'overview', cls: 'all', filter: 'all', demo: 'ok' };

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = v => v == null ? '—' : Math.round(v * 100) + '%';

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// ---- 抽屉 ----
function openDrawer(html) {
  $('#drawer').innerHTML = html;
  $('#drawerWrap').classList.add('on');
  $('#drawer .dbody').scrollTop = 0;
}
function closeDrawer() { $('#drawerWrap').classList.remove('on'); }
$('#drawerMask').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); hidePop(); if (window.clearSel) clearSel(); }
});

// ---- 浮动 tooltip ----
const tip = $('#tip');
function showTip(e, html) { tip.innerHTML = html; tip.style.display = 'block'; moveTip(e); }
function moveTip(e) {
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX + 12, innerWidth - r.width - 10) + 'px';
  tip.style.top = Math.min(e.clientY + 12, innerHeight - r.height - 10) + 'px';
}
function hideTip() { tip.style.display = 'none'; }
function bindTip(el, htmlFn) {
  el.addEventListener('mouseenter', e => showTip(e, htmlFn()));
  el.addEventListener('mousemove', moveTip);
  el.addEventListener('mouseleave', hideTip);
}

// ---- 小弹层（口径说明） ----
let popEl = null;
function hidePop() { if (popEl) { popEl.remove(); popEl = null; } }
function showPop(anchor, html) {
  hidePop();
  popEl = document.createElement('div');
  popEl.className = 'pop';
  popEl.innerHTML = html;
  document.body.appendChild(popEl);
  const r = anchor.getBoundingClientRect();
  popEl.style.top = (r.bottom + 8 + scrollY) + 'px';
  popEl.style.left = Math.min(r.left, innerWidth - 350) + 'px';
}
document.addEventListener('click', e => { if (popEl && !popEl.contains(e.target)) hidePop(); });

// ---- 共享零件 ----
function tierBar(c, mini) {
  const tot = c.weak + c.unsteady + c.stable + c.insufficient || 1;
  const seg = k => `<i class="${k[0]}" style="width:${(c[k] / tot * 100).toFixed(1)}%"></i>`;
  return `<div class="tbar${mini ? ' minibar' : ''}">${seg('weak')}${seg('unsteady')}${seg('stable')}${seg('insufficient')}</div>`;
}
function tierCounts(c) {
  return `<div class="tcount">
    <span class="cw">明显薄弱 <b>${c.weak}</b></span>
    <span class="cu">还不稳 <b>${c.unsteady}</b></span>
    <span class="cs">较稳定 <b>${c.stable}</b></span>
    <span class="ci">证据不足 <b>${c.insufficient}</b></span>
  </div>`;
}
function stuChip(st, o, extra) {
  const pc = Math.round(o.m * 100);
  return `<span class="stuchip ${o.tier}" data-tip>${esc(st.name)} <b>${pc}%</b></span>`;
}
function chipTip(st, o) {
  return `<b>${esc(st.name)}</b><br>掌握度 ${Math.round(o.m * 100)}%（${Math.round(o.lo * 100)}%–${Math.round(o.hi * 100)}%）<br>作答 ${o.obs} 处 · 全对 ${o.full} 处`;
}
function chipsFor(list, max, hiddenLabel) {
  let h = list.slice(0, max).map(x => `<span class="stuchip ${x.o.tier}" data-obs>${esc(x.st.name)} <b>${Math.round(x.o.m * 100)}%</b></span>`).join('');
  if (list.length > max) h += `<span class="stuchip more">+${list.length - max}${hiddenLabel || ''}</span>`;
  return h;
}
function wireChips(root, pairs) {
  // pairs: map element index -> {st,o}；简单做法：按顺序对应 data-obs chip
  const chips = $$('.stuchip[data-obs]', root);
  chips.forEach((el, i) => { const p = pairs[i]; if (p) bindTip(el, () => chipTip(p.st, p.o)); });
}

// ---- 范围条 / 页签 / 原型控制 ----
function currentStudents() { return KT.scopeStudents(S.cls); }
function rerender() {
  if (S.demo !== 'ok') { showState(S.demo); return; }
  $$('#v-overview,#v-map,#v-state').forEach(x => x.classList.remove('on'));
  $('#v-' + S.view).classList.add('on');
  if (S.view === 'overview') renderOverview();
  else renderMap();
}
function switchView(v) {
  S.view = v;
  $$('#tabs button[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  if (S.demo !== 'ok') { showState(S.demo); return; }
  $$('#v-overview,#v-map').forEach(x => x.classList.remove('on'));
  $('#v-' + v).classList.add('on');
  if (v === 'overview') renderOverview(); else renderMap();
}
function showState(kind) {
  $$('#v-overview,#v-map').forEach(x => x.classList.remove('on'));
  const el = $('#v-state');
  el.classList.add('on');
  el.innerHTML = kind === 'empty'
    ? `<div class="statepane"><h3>未选择教学学期</h3><p>请先在顶部"当前考试"中选择教学学期，本页将按该学期汇总掌握度。</p></div>`
    : `<div class="statepane"><h3>学情数据暂时无法读取</h3><p>原型演示的读取失败状态。<br><button class="btn small" onclick="toast('原型：重新加载会再次请求学情数据')">重新加载</button></p></div>`;
}

$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.hasAttribute('data-dead')) { toast('原型未包含'); return; }
  switchView(b.dataset.v);
});
$('#scopebar').addEventListener('click', e => {
  const b = e.target.closest('.scp');
  if (!b) return;
  S.cls = b.dataset.s;
  $$('#scopebar .scp').forEach(x => x.classList.toggle('on', x === b));
  $('#ctlCls').value = S.cls;
  rerender();
});
$('#scopeDef').addEventListener('click', e => {
  e.stopPropagation();
  showPop(e.currentTarget, `<h4>口径说明（两页共用）</h4><dl>
    <dt>明显薄弱</dt><dd>做对可能性低于 60% 的把握达到 80%</dd>
    <dt>有学生明显薄弱的项</dt><dd>至少 1 人明显薄弱</dd>
    <dt>群体档位</dt><dd>有观测学生中人数最多的档位</dd>
    <dt>证据不足</dt><dd>不按 0 计，也不参与档位多数</dd>
    <dt>证据范围</dt><dd>本学期 3 场考试 + 已发布训练；往届内容单列，不计入本册数字</dd>
  </dl>`);
});
$('#ctlCls').addEventListener('change', e => {
  S.cls = e.target.value;
  $$('#scopebar .scp').forEach(x => x.classList.toggle('on', x.dataset.s === S.cls));
  rerender();
});
$$('#protoCtl input[name=ctlstate]').forEach(r => r.addEventListener('change', e => {
  S.demo = e.target.value;
  rerender();
}));

// 地图工具条
$('#filterChips').addEventListener('click', e => {
  const b = e.target.closest('.fchip'); if (!b) return;
  S.filter = b.dataset.f;
  $$('#filterChips .fchip').forEach(x => x.classList.toggle('on', x === b));
  if (S.demo === 'ok' && S.view === 'map') renderMap();
});

$('#pcToggle').addEventListener('click', () => $('#protoCtl').classList.toggle('open'));

// 侧栏死链
$$('.navgrp a, .sidelow a').forEach(a => a.addEventListener('click', e => { e.preventDefault(); toast('原型未包含'); }));
