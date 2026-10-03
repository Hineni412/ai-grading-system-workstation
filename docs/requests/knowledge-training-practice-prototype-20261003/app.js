/* app.js — 壳、范围条、页签、抽屉、提示（共享） */
const S = {
  view: 'student',        // student | chapter
  cls: 'all',             // all | 9班 | 10班
  minScore: null,         // 最低考试得分率 %（null = 不限）
  demo: 'ok',             // ok | empty
  // 按学生训练
  sel: new Set(KT.students.map(s => s.id)), // 已选学生（默认全选）
  stuQuery: '', stuOnlyWeak: false,
  rangeMode: 'zh',        // zh 综合 | zx 专项
  learnTo: '第二章 实数', // 综合训练：已学到
  zxSel: new Set(KT.allSkillIds()),          // 专项：勾选的技能（默认全勾）
  openSecs: new Set(),    // 中列展开的节
  openCls: new Set(['9班', '10班']),
  // 出卷面板
  purpose: 'paper',       // paper 训练卷 | handout 刷题讲义 | notebook 错题本
  mode: 'per',            // per 一人一卷 | same 多人同卷
  p: { n: 10, maxD: 8, sameSkill: 1, answerMax: 2, exclN: 3,
       hCap: 30, hMaxD: 8, hWeak: 3, hCons: 6, hNew: 6, hAnsMax: 8, hExclN: 3 },
  nb: { exams: new Set(['e1', 'e2', 'e3']), src: true, blank: true },
  // 按章节训练
  chapSel: KT.chapters[0].sections[0].id, // 选中的章或节 id
  grpSort: 'count',       // count | weaker | close
  adopted: {},            // scopeKey -> group index（每组 id 稳定）
  grpRemoved: {},         // scopeKey+grpId -> Set(removed student ids)
  grpOpen: new Set(),     // 展开名单的组
  nbProgress: null,       // 错题本导出进度 {done,total} | null
};

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
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

// ---- 共享零件 ----
function tierBar(c) {
  const tot = c.weak + c.unsteady + c.stable + c.insufficient + c.none || 1;
  const v = k => ((c[k] || 0) / tot * 100).toFixed(1);
  return `<div class="tbar"><i class="w" style="width:${v('weak')}%"></i><i class="u" style="width:${v('unsteady')}%"></i><i class="s" style="width:${v('stable')}%"></i><i class="i" style="width:${(100 - v('weak') - v('unsteady') - v('stable')).toFixed(1)}%"></i></div>`;
}
function scoreBadge(rate) {
  if (rate == null) return '<span class="bd gray">无成绩</span>';
  const p = Math.round(rate * 100);
  const cls = p >= 80 ? 'ok' : (p >= 60 ? 'warn' : 'dng');
  return `<span class="bd ${cls}">${p}%</span>`;
}
function scopedStudents() { return KT.scopeStudents(S.cls, S.minScore); }
function selStudents() {
  const ids = S.sel;
  return KT.students.filter(s => ids.has(s.id));
}
// 当前训练范围包含的技能 id
function rangeSkillIds() {
  if (S.rangeMode === 'zh') {
    const idx = KT.LEARN_OPTIONS.indexOf(S.learnTo);
    const out = [];
    KT.chapters.forEach(ch => {
      const ci = KT.LEARN_OPTIONS.indexOf(ch.name);
      if (ci >= 0 && ci <= idx) out.push(...KT.chapterSkillIds(ch));
    });
    return out;
  }
  return [...S.zxSel];
}

// ---- 渲染调度 ----
function rerender() {
  // 范围变化后，剔出已选但不在当前范围的学生
  const ok = new Set(scopedStudents().map(s => s.id));
  S.sel.forEach(id => { if (!ok.has(id)) S.sel.delete(id); });
  if (S.demo !== 'ok') { showState(); return; }
  $$('#v-student,#v-chapter,#v-state').forEach(x => x.classList.remove('on'));
  $('#v-' + S.view).classList.add('on');
  if (S.view === 'student') renderStudentTab(); else renderChapterTab();
}
function showState() {
  $$('#v-student,#v-chapter').forEach(x => x.classList.remove('on'));
  const el = $('#v-state');
  el.classList.add('on');
  el.innerHTML = `<div class="statepane"><h3>未选择教学学期</h3><p>请先在顶部选择教学学期，本页将按该学期汇总学情并生成训练。</p></div>`;
}
function switchView(v) {
  S.view = v;
  $$('#tabs button[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  rerender();
}

// ---- 事件 ----
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
  rerender();
});
$('#minScore').addEventListener('input', e => {
  const v = e.target.value.trim();
  S.minScore = v === '' ? null : Math.max(0, Math.min(100, Number(v) || 0));
  rerender();
});
$$('#protoCtl input[name=ctlstate]').forEach(r => r.addEventListener('change', e => {
  S.demo = e.target.value;
  rerender();
}));
$('#pcToggle').addEventListener('click', () => $('#protoCtl').classList.toggle('open'));
$$('.navgrp a, .sidelow a').forEach(a => a.addEventListener('click', e => { e.preventDefault(); toast('原型未包含'); }));

// 初始渲染在最后加载的 chapter.js 末尾触发
