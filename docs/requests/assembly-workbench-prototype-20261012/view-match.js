/* 视图 1 中栏：配题 —— 题型头、相关原题单行 chips、原题卡、候选题 */
(function () {
  const esc = s => PROTO.esc(s);

  function causeBar(causes) {
    const nz = PROTO.CAUSE_CATS.map((c, i) => ({ c, i, n: causes[c] || 0 })).filter(x => x.n > 0);
    const total = nz.reduce((s, x) => s + x.n, 0) || 1;
    return `<div class="stack">${nz.map(x => `<i style="background:${PROTO.CAUSE_COLORS[x.i]};flex:${x.n / total}"></i>`).join('')}</div>
      <div class="legend">${nz.map(x => `<span><i style="background:${PROTO.CAUSE_COLORS[x.i]}"></i>${x.c} ${x.n}</span>`).join('')}</div>`;
  }
  function causeHint(o) {
    const top = PROTO.CAUSE_CATS.map(c => ({ c, n: o.causes[c] || 0 })).sort((a, b) => b.n - a.n)[0];
    if (!top) return '';
    const map = { '未作答': '可能偏难或时间不够，建议配更基础的题', '计算与化简': '建议：配同类型计算变式', '概念理解': '建议：配概念辨析类变式', '方法与思路': '建议：配同思路小步引导题' };
    return map[top.c] || `以「${top.c}」为主，建议配同题型变式`;
  }
  function relStrip(pt) {
    const S = PROTO.S;
    const chips = pt.related.map(k => PROTO.ORIGINALS[k]).sort((a, b) => a.rate - b.rate);
    const shown = chips.slice(0, 3), rest = chips.slice(3);
    const chip = o => `<button type="button" class="rel-chip ${S.selectedOrig === o.key ? 'is-selected' : ''}" onclick="PROTO.selectOrig('${o.key}')">${PROTO.EXAM_SHORT[o.exam]} ${o.label} ${o.rate}%</button>`;
    let more = '';
    if (rest.length) more = `<button type="button" class="rel-chip rel-chip--more" onclick="this.nextElementSibling.hidden=!this.nextElementSibling.hidden">+${rest.length} 道</button>
      <span class="pop rel-pop" hidden>${rest.map(chip).join('')}</span>`;
    return `<div class="rel-strip"><span class="rel-label">相关失分题</span><span class="rel-strip__scroll">${shown.map(chip).join('')}</span>${more}</div>`;
  }
  function origCard() {
    const S = PROTO.S;
    const o = PROTO.ORIGINALS[S.selectedOrig];
    if (!o) return '';
    const head = `<div class="orig__head"><strong>${PROTO.EXAM_SHORT[o.exam]} · ${o.label}</strong><span class="sp">${o.type} · ${o.score} 分 · 合计 ${o.rate}%</span>
      <span class="tip" tabindex="0"><i class="tip__i">i</i><span class="tip__box">近 3 次已做过的原题不重复出</span></span>
      <button type="button" class="btn btn--ghost btn--small fold" onclick="PROTO.set({origOpen:${!S.origOpen}})">${S.origOpen ? '收起原题' : '展开原题'}</button></div>`;
    if (!S.origOpen) return `<div class="orig">${head}</div>`;
    const stem = `<div class="orig__stem ${S.origStemFull ? '' : 'clamp clamp--2'}">${esc(o.stem)}</div>${o.img ? `<div class="imgph">【图】${esc(o.img)}</div>` : ''}
      <button type="button" class="btn btn--ghost btn--small orig__more" onclick="PROTO.set({origStemFull:${!S.origStemFull}})">${S.origStemFull ? '收起题干' : '展开题干'}</button>`;
    return `<div class="orig">${head}<div class="orig__rates">10班 ${o.rates.c10}% · 9班 ${o.rates.c9}%</div>${stem}${causeBar(o.causes)}<span class="hint-tag">${causeHint(o)}</span></div>`;
  }
  function candCard(e, i) {
    const S = PROTO.S;
    const inB = PROTO.inBasket(e.id);
    const open = S.expStems.has(e.id);
    const opts = e.options ? `<div class="cand__opts">${e.options.map(o => `<span>${esc(o)}</span>`).join('')}</div>` : '';
    const f = PROTO.fitOf(e.id);
    return `<article class="cand ${inB ? 'is-in' : ''}">
      <div class="cand__head"><strong>候选 ${i + 1} · ${e.type} · 难度 ${e.diff.toFixed(1)}${PROTO.origDiff() != null ? ' · ' + PROTO.relTo(e.diff) : ''}</strong>
        <span class="badge is-neutral">${PROTO.matchOf(e.id)}</span>
        <button type="button" class="btn btn--small add ${inB ? '' : 'btn--primary'}" ${inB ? 'disabled' : `onclick="PROTO.add('${e.id}')"`}>${inB ? '已加入 ✓' : '加入'}</button>
        ${inB ? `<button type="button" class="btn btn--ghost btn--small cand__out" onclick="PROTO.remove('${e.id}')">移出</button>` : ''}</div>
      <div class="cand__stem ${open ? '' : 'clamp clamp--3'}">${esc(e.stem)}${e.img ? `<div class="imgph">【图】${esc(e.img)}</div>` : ''}${opts}</div>
      <button type="button" class="btn btn--ghost btn--small" onclick="PROTO.toggleStem('${e.id}')">${open ? '收起' : '展开'}</button>
      <div class="cand__foot"><span>适合 ${f.total} 人 · 补弱 ${f.w} · 巩固 ${f.k} · 新练习 ${f.n}</span>
        <button type="button" class="btn btn--ghost btn--small sim" onclick="PROTO.toast('原型：相似题占位')">相似 2 题</button></div>
    </article>`;
  }
  function matchingCol() {
    const S = PROTO.S;
    const pt = PROTO.point(S.selectedPoint);
    let head;
    if (pt) head = `<div class="pt-head"><strong>${esc(pt.name)}</strong><span class="muted">${esc(pt.section)} · ${pt.weak} 人需关注 / ${pt.evidence} 人有证据</span></div>${relStrip(pt)}`;
    else {
      const o = PROTO.ORIGINALS[S.selectedOrig];
      head = o ? `<div class="pt-head"><strong>${PROTO.EXAM_SHORT[o.exam]} · ${o.label} · ${o.type}</strong><span class="muted">未挂题型 · 只能加入原题</span></div>` : '';
    }
    const cands = pt ? PROTO.candidates() : [];
    const shown = cands.slice(0, S.candLimit);
    const bar = pt ? `<div class="cand-bar"><strong>候选题 ${cands.length}</strong>
      <div class="app-segmented app-segmented--small" role="group" aria-label="相对难度">${['', '更基础', '相当', '更难'].map(v =>
        `<button type="button" aria-pressed="${S.candRel === v}" onclick="PROTO.set({candRel:'${v}',candLimit:6})">${v || '全部'}</button>`).join('')}</div>
      <select class="app-input" aria-label="题型" onchange="PROTO.set({candType:this.value,candLimit:6})"><option value="">全部题型</option>${['选择题', '填空题', '解答题'].map(t =>
        `<option ${S.candType === t ? 'selected' : ''}>${t}</option>`).join('')}</select></div>` : '';
    const more = cands.length > shown.length ? `<button type="button" class="btn" style="width:100%" onclick="PROTO.set({candLimit:PROTO.S.candLimit+6})">显示更多候选题</button>` : '';
    return `<section class="panel ca-matching"><header class="panel__head"><h2>配题</h2><span class="muted">依据学情逐题搭配</span></header>
      <div class="panel__body">${head}${origCard()}${bar}${shown.map(candCard).join('')}${more}</div></section>`;
  }

  Object.assign(PROTO, { matchingCol });
})();
