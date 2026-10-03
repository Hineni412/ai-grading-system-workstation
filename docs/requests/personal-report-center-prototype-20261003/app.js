/* app.js — 壳、页签、弹层、提示、原型控制（共享） */
const S = {
  cls: 'all', q: '', pend: false, nogen: false,
  rstate: 'real', drawerSid: null,
  readerOpen: false, rsid: null, rexid: 'e4', dataOnly: false,
};

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// ---- 对话框 ----
function showDlg(html, wide) {
  $('#dlg').classList.toggle('wide', !!wide);
  $('#dlg').innerHTML = html;
  $('#dlgWrap').classList.add('on');
}
function hideDlg() { $('#dlgWrap').classList.remove('on'); $('#dlg').innerHTML = ''; $('#dlg').classList.remove('wide'); }
$('#dlgWrap .mask').addEventListener('click', hideDlg);

// ---- 小弹层（导出菜单等） ----
let popEl = null;
function hidePop() { if (popEl) { popEl.remove(); popEl = null; } }
function showPop(anchor, html) {
  hidePop();
  popEl = document.createElement('div');
  popEl.className = 'pop';
  popEl.innerHTML = html;
  document.body.appendChild(popEl);
  const r = anchor.getBoundingClientRect();
  popEl.style.top = (r.bottom + 6) + 'px';
  popEl.style.right = Math.max(10, innerWidth - r.right) + 'px';
  return popEl;
}
document.addEventListener('click', e => { if (popEl && !popEl.contains(e.target) && e.target.id !== 'exportBtn') hidePop(); });

// ---- 图片放大 ----
function openZoom(svgHtml, cap) {
  $('#zoomBox').innerHTML = svgHtml + `<div class="zcap">${esc(cap)}</div>`;
  $('#zoom').classList.add('on');
}
$('#zoom').addEventListener('click', () => $('#zoom').classList.remove('on'));

// ---- Esc / 方向键 ----
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#zoom').classList.contains('on')) { $('#zoom').classList.remove('on'); return; }
    if ($('#dlgWrap').classList.contains('on')) { hideDlg(); return; }
    if (popEl) { hidePop(); return; }
    if (S.readerOpen) { closeReader(); return; }
    closeStudentDrawer();
    return;
  }
  if (S.readerOpen && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && e.target.tagName !== 'INPUT') {
    navStudent(e.key === 'ArrowLeft' ? -1 : 1);
  }
});

// ---- 状态文案 / 强制状态 ----
const STXT = { done: '已生成', stale: '需重新生成', none: '未生成', absent: '缺考' };
const SICO = { done: '✓', stale: '↻', none: '○', absent: '缺考' };
function effStatus(st, exId) {
  if (S.rstate === 'real' || exId !== S.rexid) return st['st_' + exId];
  if (S.rstate === 'released') return st['st_' + exId] === 'absent' ? 'absent' : 'done';
  return S.rstate;
}
function imagesReleased(exId) { return S.rstate === 'released' && exId === S.rexid; }

$('#pcToggle').addEventListener('click', () => $('#protoCtl').classList.toggle('open'));
$('#ctlRstate').addEventListener('change', e => {
  S.rstate = e.target.value;
  if (S.readerOpen) renderReader();
  if (S.drawerSid) openStudentDrawer(S.drawerSid);
});

// ---- 页签 / 导出菜单 ----
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.id === 'exportBtn') { popEl ? hidePop() : openExportPop(b); return; }
  if (b.hasAttribute('data-dead')) { toast('原型未包含'); return; }
});

$$('.navgrp a, .sidelow a').forEach(a => a.addEventListener('click', e => { e.preventDefault(); toast('原型未包含'); }));
