/* util.js — DOM 助手、判定点表、计数值 */

const $  = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
const srcTag = s => `<span class="srctag">${esc(s)}</span>`;
const pill = (t, tone) => `<span class="bd ${tone}">${esc(t)}</span>`;

// ---- 判定点表：每份 judged 答卷一张 "q:p" -> {st, unc, locked, note} ----
// st: hit 达成 / miss 未达成 / unread 无法辨认 / null 未定（AI 拿不准或无法辨认，无按下段）
// unc: null | 'ai-unc' AI 拿不准 | 'ai-unread' AI 无法辨认 | 't-unc' 教师标为不确定
function ptab(sub) {
  if (sub._pt) return sub._pt;
  const m = {};
  for (let q = 0; q < QS.length; q++) for (let p = 0; p < QN[q]; p++) {
    const hit = (q * 7 + p * 3 + sub.ix) % 4 !== 0; // 约四分之三达成
    m[q + ':' + p] = { st: hit ? 'hit' : 'miss', unc: null, locked: false, note: '' };
  }
  (sub.spec || []).forEach(([q, p, kind]) => {
    m[q + ':' + p] = { st: null, unc: kind === 'unread' ? 'ai-unread' : 'ai-unc', locked: false, note: '' };
  });
  return sub._pt = m;
}
const judged = sub => ['review','update','done'].includes(sub.status);
const pendOf = sub => Object.keys(ptab(sub)).filter(k => { const p = ptab(sub)[k]; return p.st === null && !p.locked; });
const gotOf  = sub => Object.values(ptab(sub)).filter(p => p.st === 'hit').length;
const detQ   = sub => QS.reduce((n, _, q) =>
  n + (Array.from({ length: QN[q] }, (_, p) => ptab(sub)[q + ':' + p]).every(p => p.st !== null) ? 1 : 0), 0);

// AI 对该点的称呼（用于锁定提示）：AI 达成 / AI 未达成 / AI 拿不准 / AI 无法辨认
function aiCallOf(p) {
  if (p.unc === 'ai-unread') return 'AI 无法辨认';
  if (p.unc) return 'AI 不确定';
  if (p.st === 'hit') return 'AI 达成';
  if (p.st === 'miss') return 'AI 未达成';
  return 'AI 未判定';
}
const SEG_TXT = { hit:'达成', miss:'未达成', unread:'无法辨认' };

// ---- 汇总计数（汇总行与队列共用）----
function counts(W) {
  const c = { anom: W.anoms.length, missing:0, pending:0, judging:0, failed:0, review:0, update:0, done:0, canceled:0 };
  W.subs.forEach(s => c[s.status]++);
  return c;
}
// 队列排序：需要处理在前（缺页,判定失败,待复核,待判定,待更新），再判定中、已完成、已取消；同档按学号
const sortSubs = subs => subs.slice().sort((a, b) => STATUS[a.status].rank - STATUS[b.status].rank || a.id.localeCompare(b.id));
