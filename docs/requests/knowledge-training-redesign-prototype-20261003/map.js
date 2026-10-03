/* map.js — 知识结构（地图页）：知识地图（知识点/技能双泳道）+ 关联连线 + 抽屉 */
let selId = null;
const HEATC = ['#ffffff', '#fdf1ec', '#fbe3d8', '#f6cdbb', '#f0b099', '#e78d70', '#d96c4d'];

function renderMap() { renderLegend(); renderHeat(); }

function renderLegend() {
  $('#legend').innerHTML = `<span class="lg">底色越深＝明显薄弱的学生占比越高（分母：有证据学生）<span class="heatline"></span></span>
    <span class="lg">底部细条＝四档人数分布</span>
    <span class="lg"><span class="sw" style="background:repeating-linear-gradient(45deg,#f1f2f0,#f1f2f0 3px,#fafaf8 3px,#fafaf8 7px)"></span>无证据</span>`;
}

function heatLv(a) {
  if (!a.evidence) return -1;
  const r = a.counts.weak / a.evidence;
  return r <= 0 ? 0 : r < .08 ? 1 : r < .16 ? 2 : r < .24 ? 3 : r < .32 ? 4 : r < .42 ? 5 : 6;
}
function itemShown(it, a) {
  if (S.filter === 'skill' && it.kind !== 'skill') return false;
  if (S.filter === 'topic' && it.kind !== 'topic') return false;
  if (S.filter === 'weak' && !a.counts.weak) return false;
  return true;
}
function distBar(a) {
  if (!a.evidence) return '';
  const seg = k => `<i class="${k[0]}" style="width:${(a.counts[k] / a.evidence * 100).toFixed(1)}%"></i>`;
  return `<span class="db">${seg('weak')}${seg('unsteady')}${seg('stable')}${seg('insufficient')}</span>`;
}
function tileHtml(it, a) {
  const lv = heatLv(a);
  return `<button class="mtile ${it.kind === 'skill' ? 'sk' : 'tp'} ${lv < 0 ? 'hnone' : 'heat-' + lv}" data-iid="${it.id}" title="${esc(it.name)}">
    <span class="tn">${esc(it.name)}</span>
    ${a.counts.weak ? `<span class="wc">${a.counts.weak}</span>` : ''}
    ${distBar(a)}
  </button>`;
}

// ---- 关联高亮 / 连线 ----
function relSetOf(id) {
  const it = KT.itemById[id];
  const s = new Set([id]);
  if (it) for (const r of KT.relatedOf(it)) s.add(r.item.id);
  return s;
}
function refreshFx(hover) {
  const id = hover || selId;
  const rel = id ? relSetOf(id) : null;
  $$('#mapHeat .mtile').forEach(t => {
    t.classList.toggle('dim', !!rel && !rel.has(t.dataset.iid));
    t.classList.toggle('sel', t.dataset.iid === selId);
  });
}
function clearSel() {
  selId = null;
  refreshFx();
  const s = $('#connSvg'); if (s) s.innerHTML = '';
}
window.clearSel = clearSel;
function drawConn() {
  const host = $('#mapHeat'), svg = $('#connSvg');
  if (!svg) return;
  svg.innerHTML = '';
  if (!selId) return;
  const it = KT.itemById[selId]; if (!it) return;
  svg.setAttribute('width', host.scrollWidth);
  svg.setAttribute('height', host.scrollHeight);
  const hr = host.getBoundingClientRect();
  const from = host.querySelector(`.mtile[data-iid="${selId}"]`);
  if (!from) return;
  const fr = from.getBoundingClientRect();
  const x1 = fr.left - hr.left + fr.width / 2, y1 = fr.top - hr.top + fr.height / 2;
  let d = '';
  for (const r of KT.relatedOf(it)) {
    const to = host.querySelector(`.mtile[data-iid="${r.item.id}"]`);
    if (!to) continue;
    const tr = to.getBoundingClientRect();
    const x2 = tr.left - hr.left + tr.width / 2, y2 = tr.top - hr.top + tr.height / 2;
    const mx = (x1 + x2) / 2;
    d += `<path class="conn${r.basis === 'same_part' ? '' : ' dash'}" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`;
    d += `<circle class="cdot" cx="${x2}" cy="${y2}" r="3"/>`;
  }
  svg.innerHTML = d;
}
addEventListener('resize', () => { if (selId) drawConn(); });
$('.page').addEventListener('scroll', () => { if (selId) drawConn(); });
$('#mapHeat').addEventListener('click', e => {
  if (!e.target.closest('.mtile')) clearSel();
});

function renderHeat() {
  const studs = currentStudents();
  const aggs = new Map(KT.evidenceItems.map(it => [it.id, KT.agg(it, studs)]));
  let h = '<svg class="connlayer" id="connSvg"></svg>';
  for (const ch of KT.chapters) {
    if (!ch.examined) continue;
    let ms = 0, mn = 0, ev = 0, tot = 0, shownAny = false;
    let body = '';
    for (const sec of ch.sections) {
      const ts = sec.topics.filter(it => itemShown(it, aggs.get(it.id)));
      const ss = sec.skills.filter(it => itemShown(it, aggs.get(it.id)));
      tot += sec.topics.length + sec.skills.length;
      for (const it of [...sec.topics, ...sec.skills]) {
        const a = aggs.get(it.id);
        if (a.evidence) ev++;
        if (a.groupMastery != null) { ms += a.groupMastery; mn++; }
      }
      if (!ts.length && !ss.length) continue;
      shownAny = true;
      body += `<div class="secrow"><div class="snm">${esc(sec.name)}</div><div class="lanes">
        <div class="lane"><div class="llab">知识点 · 学什么</div><div class="tiles2">${ts.map(it => tileHtml(it, aggs.get(it.id))).join('') || '<span class="lnone">无</span>'}</div></div>
        <div class="lane"><div class="llab">技能 · 会做什么</div><div class="tiles2">${ss.map(it => tileHtml(it, aggs.get(it.id))).join('') || '<span class="lnone">无</span>'}</div></div>
      </div></div>`;
    }
    if (!shownAny) continue;
    h += `<div class="mapchap"><div class="chh"><span class="chn">${esc(ch.name)}</span>
      <span class="chm">群体掌握度 ${pct(mn ? ms / mn : null)}</span>
      <span class="chev">有证据 ${ev}/${tot} 项 · 与学情总览同一口径</span></div>${body}</div>`;
  }
  // 未考查章
  const dorm = KT.chapters.filter(c => !c.examined);
  h += `<details class="priorbox"><summary>尚未考查的 ${dorm.length} 章（无证据，不计入上方数字）</summary><div class="pb">`;
  for (const d of dorm)
    h += `<div class="dormline"><b>${esc(d.name)}</b><span>${d.counts.secs} 节 · ${d.counts.t} 个知识点 · ${d.counts.s} 项技能 · 无证据</span></div>`;
  h += `</div></details>`;
  // 往届内容
  const priorShown = KT.prior.items.filter(it => itemShown(it, aggs.get(it.id)));
  h += `<details class="priorbox"><summary>往届内容（本学期考试涉及，${esc(KT.prior.vol)} · ${KT.prior.items.length} 项；不计入本册数字）</summary>
    <div class="pb"><div class="tiles2">${priorShown.map(it => tileHtml(it, aggs.get(it.id))).join('') || '<span style="color:var(--ink3);font-size:12px">当前筛选无匹配项</span>'}</div></div></details>`;
  $('#mapHeat').innerHTML = h;
  $$('#mapHeat .mtile').forEach(t => {
    const it = KT.itemById[t.dataset.iid];
    t.addEventListener('mouseenter', () => refreshFx(it.id));
    t.addEventListener('mouseleave', () => refreshFx());
    t.addEventListener('click', e => {
      e.stopPropagation();
      selId = it.id;
      openItemDrawer(it);
      refreshFx();
      drawConn();
    });
  });
}

// ---- 抽屉 ----
function openItemDrawer(it) {
  const studs = currentStudents();
  const a = KT.agg(it, studs);
  const pairs = [];
  const obs = studs.map(st => ({ st, o: it.obs[st.id] })).filter(x => x.o && x.o.tier !== 'insufficient');
  const avgLo = obs.length ? obs.reduce((s, x) => s + x.o.lo, 0) / obs.length : null;
  const avgHi = obs.length ? obs.reduce((s, x) => s + x.o.hi, 0) / obs.length : null;
  let groups = '';
  for (const t of KT.TIERS) {
    const list = a.byTier[t];
    if (!list.length) continue;
    groups += `<div class="pgroup"><div class="gt">${KT.TIER_LABEL[t]}（${list.length} 人）</div><div>${chipPair(list, 30, pairs)}</div></div>`;
  }
  // 相关技能/知识点
  const rels = KT.relatedOf(it);
  let rel = '';
  if (rels.length) {
    rel = `<h4 style="margin:14px 0 6px;font-size:12.5px">${it.kind === 'skill' ? '相关知识点' : '相关技能'}</h4>` +
      rels.map(r => {
        const ra = KT.agg(r.item, studs), lv = heatLv(ra);
        return `<button class="relrow" data-rel="${r.item.id}">
          <span class="swatch" style="background:${lv < 0 ? 'repeating-linear-gradient(45deg,#f1f2f0,#f1f2f0 2px,#fafaf8 2px,#fafaf8 5px)' : HEATC[lv]}"></span>
          <span class="rtxt"><span class="rn">${esc(r.item.name)}</span>
          <span class="rb">${r.basis === 'same_part' ? `同一小问 ${r.qc} 题` : `仅同题出现 ${r.qc} 题（虚线）`}</span></span>
          <span class="rw">${ra.counts.weak ? `薄弱 ${ra.counts.weak}` : ''}</span>
        </button>`;
      }).join('') +
      `<p style="font-size:11px;color:var(--ink3);margin:0 0 10px">关联来自题库里同时考查两者的题目，不代表两者掌握度相同。</p>`;
  }
  $('#drawer').innerHTML = `
    <div class="dhead">
      <div style="display:flex;align-items:baseline;gap:8px">
        <h3 style="font-size:15px">${esc(it.name)}</h3>
        <span class="bd ${it.kind === 'skill' ? 'info' : 'gray'}">${it.kind === 'skill' ? '技能' : '知识点'}</span>
        <button class="btn ghost small" style="margin-left:auto" onclick="closeDrawer()">关闭</button>
      </div>
      <div style="font-size:11.5px;color:var(--ink3);margin-top:3px">属于 ${it.prior ? KT.prior.vol + '（往届内容）' : esc(it.secName) + ' · ' + esc(it.chName)}</div>
    </div>
    <div class="dbody">
      <p style="font-size:12px;color:var(--ink2);margin-bottom:10px">${esc(it.def)}</p>
      <div style="font-size:26px;font-weight:600;font-variant-numeric:tabular-nums">${pct(a.groupMastery)}
        <span style="font-size:12px;color:var(--ink3);font-weight:400">群体掌握度 · 80% 区间 ${avgLo == null ? '—' : Math.round(avgLo * 100) + '%–' + Math.round(avgHi * 100) + '%'}</span></div>
      <div style="margin:8px 0 4px">${tierBar(a.counts)}</div>
      ${tierCounts(a.counts)}
      <p style="font-size:11px;color:var(--ink3);margin:8px 0 12px">有证据 ${a.evidence} 人 · 与学情总览同一口径</p>
      ${groups}
      ${rel}
    </div>
    <div class="dfoot">
      <button class="btn primary small" id="dTrain">给明显薄弱的 ${a.counts.weak} 人出训练卷</button>
      <button class="btn small" id="dWrong">看错题</button>
    </div>`;
  $('#drawerWrap').classList.add('on');
  wireChips($('#drawer'), pairs);
  $('#dTrain').addEventListener('click', () => toast(`原型：将带着这 ${a.counts.weak} 人和该项进入按章节训练`));
  $('#dWrong').addEventListener('click', () => toast('原型：打开该项的错题列表'));
  $$('#drawer [data-rel]').forEach(b => b.addEventListener('click', () => {
    const nid = b.dataset.rel;
    selId = nid;
    openItemDrawer(KT.itemById[nid]);
    refreshFx();
    drawConn();
  }));
}

rerender();
