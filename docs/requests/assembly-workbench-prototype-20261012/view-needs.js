/* 视图 1 左栏：要补的点 —— 章节筛选、排序（薄弱人数/原题得分率/区域常考）、
   只看未配题、得分率门槛收进「更多 ▾」，行内药丸随排序指标变化 */
(function () {
  const esc = s => PROTO.esc(s);

  function minRate(p) { return p.related && p.related.length ? Math.min(...p.related.map(k => PROTO.ORIGINALS[k].rate)) : 100; }
  function chapterOf(p) { return (p.section.split('·')[1] || '').trim(); }
  PROTO.minRate = minRate;

  const SORTS = {
    weak: (a, b) => b.strong - a.strong || minRate(a) - minRate(b),
    rate: (a, b) => minRate(a) - minRate(b) || b.strong - a.strong,
    region: (a, b) => b.groups - a.groups || b.strong - a.strong,
  };

  /* 可见题型：门槛 → 章节 → 只看未配题（当前选中项豁免，避免配题后消失）→ 排序 */
  function visiblePoints() {
    const S = PROTO.S;
    let pts = PROTO.POINTS.filter(p => S.rateFilter === 100 || minRate(p) < S.rateFilter);
    if (S.needChapters.length) pts = pts.filter(p => S.needChapters.includes(chapterOf(p)));
    if (S.uncoveredOnly) pts = pts.filter(p => !PROTO.coveredByPoint(p.id) || p.id === S.selectedPoint);
    return pts.sort(SORTS[S.needSort] || SORTS.weak);
  }
  function visiblePointSections() { return [{ label: '', items: visiblePoints() }]; }
  function visibleExtras() {
    const cs = PROTO.S.needChapters;
    return PROTO.EXTRA_POINTS
      .filter(p => !cs.length || cs.includes(chapterOf(p)))
      .sort((a, b) => b.strong - a.strong);
  }

  /* 右侧药丸 = 当前排序指标（其他题型无原题得分率，退回人数） */
  function pill(p, extra) {
    const S = PROTO.S;
    if (S.needSort === 'rate' && !extra) {
      const r = minRate(p);
      return `<span class="rate-pill ${r < 60 ? 'is-red' : 'is-amber'}">${r}%</span>`;
    }
    if (S.needSort === 'region') return `<span class="rate-pill is-num">${p.groups} 套</span>`;
    return `<span class="rate-pill is-num">${p.strong} 人</span>`;
  }
  /* 副行：药丸以外的指标 */
  function subLine(p, extra) {
    const S = PROTO.S;
    const skipStrong = S.needSort === 'weak' || (extra && S.needSort === 'rate');
    const bits = [(skipStrong ? '' : `明显薄弱 ${p.strong} / `) + `需关注 ${p.weak} 人`];
    if (p.related) bits.push(`失分题 ${p.related.length} 道`);
    if (S.needSort !== 'region') bits.push(`区域 ${p.groups} 套`);
    return bits.join(' · ');
  }

  function needRow(p) {
    const S = PROTO.S;
    const cov = PROTO.coveredByPoint(p.id);
    const sub = subLine(p);
    return `<button type="button" class="need ${S.selectedPoint === p.id ? 'is-selected' : ''}" data-need="${p.id}" title="${esc(sub)}" onclick="PROTO.selectPoint('${p.id}')">
      <div class="need__top"><strong>${esc(p.name)}</strong>${pill(p)}</div>
      <div class="need__sub"><span class="need__sub-t">${esc(sub)}</span>${cov ? `<span class="badge is-accent">已配 ${cov}</span>` : ''}</div>
    </button>`;
  }
  function noskillRow(o) {
    const S = PROTO.S;
    return `<button type="button" class="need ${!S.selectedPoint && S.selectedOrig === o.key ? 'is-selected' : ''}" data-need="noskill:${o.key}" onclick="PROTO.selectNoskill('${o.key}')">
      <div class="need__top"><strong>${PROTO.EXAM_SHORT[o.exam]} · ${o.label}</strong><span class="rate-pill ${o.rate < 60 ? 'is-red' : 'is-amber'}">${o.rate}%</span></div>
      <div class="need__sub"><span class="need__sub-t">${o.type} · ${o.score} 分 · 未挂题型</span></div>
    </button>`;
  }
  function extraRow(p) {
    const sub = subLine(p, true);
    return `<button type="button" class="need" title="${esc(sub)}" onclick="PROTO.toast('原型：其他题型只可查看')">
      <div class="need__top"><strong>${esc(p.name)}</strong>${pill(p, true)}</div>
      <div class="need__sub"><span class="need__sub-t">${esc(sub)}</span></div>
    </button>`;
  }

  function filters(visCount) {
    const S = PROTO.S;
    const chaps = [...new Set([...PROTO.POINTS, ...PROTO.EXTRA_POINTS].map(chapterOf))];
    const sortSeg = [['weak', '薄弱人数'], ['rate', '原题得分率'], ['region', '区域常考']].map(([v, l]) =>
      `<button type="button" aria-pressed="${S.needSort === v}" onclick="PROTO.set({needSort:'${v}'})">${l}</button>`).join('');
    const moreLabel = S.rateFilter === 70 ? '更多 ▾' : `更多 · ${S.rateFilter === 100 ? '全部' : '<' + S.rateFilter + '%'} ▾`;
    const rates = [[60, '<60%'], [70, '<70%'], [80, '<80%'], [100, '全部']].map(([v, l]) =>
      `<button type="button" aria-pressed="${S.rateFilter === v}" onclick="PROTO.set({rateFilter:${v}})">${l}</button>`).join('');
    return `<div class="need-filters">
      <div class="nf-row"><span class="nf-label">章节</span><div class="nf-opts nf-opts--wrap">
        <button type="button" aria-pressed="${!S.needChapters.length}" onclick="PROTO.set({needChapters:[]})">全部</button>
        ${chaps.map(c => `<button type="button" aria-pressed="${S.needChapters.includes(c)}" onclick="PROTO.toggleChapter('${c}')">${esc(c)}</button>`).join('')}
      </div></div>
      <div class="nf-row"><span class="nf-label">排序</span><div class="app-segmented app-segmented--small" role="group" aria-label="排序">${sortSeg}</div></div>
      <div class="nf-row"><span class="nf-label">筛选</span>
        <button type="button" class="ca-chip ${S.uncoveredOnly ? 'selected' : ''}" aria-pressed="${S.uncoveredOnly}" onclick="PROTO.set({uncoveredOnly:${!S.uncoveredOnly}})">只看未配题</button>
        <details class="nf-more"><summary>${moreLabel}</summary>
          <div class="nf-more__pop"><p class="nf-pop-label">失分原题门槛</p>
            <div class="nf-opts">${rates}</div>
            <p class="nf-pop-note">相关原题本班得分率低于门槛才算失分题，决定题型是否进入清单。</p>
          </div></details>
      </div>
      <div class="need-note"><span>${visCount} / ${PROTO.POINTS.length} 个题型 · 本学期考试${S.includeTraining ? '+训练' : ''}</span>
        <span class="tip" tabindex="0"><i class="tip__i">i</i><span class="tip__box">明显薄弱 = 掌握度明显偏低的学生；需关注 = 还不稳 + 明显薄弱。反复卡点只作说明，不参与排序。W / S 键上下切换</span></span></div>
    </div>`;
  }

  function needsCol() {
    const S = PROTO.S;
    const vis = visiblePoints();
    let rows = vis.length ? vis.map(needRow).join('') : '<p class="muted" style="padding:8px 2px">没有符合当前条件的题型</p>';
    const noskill = ['oN1', 'oN2'].map(k => PROTO.ORIGINALS[k]);
    rows += `<button type="button" class="need-group need-group--fold ${S.openNoskill ? 'open' : ''}" onclick="PROTO.set({openNoskill:${!S.openNoskill}})">未挂题型的题（${noskill.length}）</button>`;
    if (S.openNoskill) rows += noskill.map(noskillRow).join('');
    const extras = visibleExtras();
    if (S.showExtra) rows += `<h3 class="need-group">其他需关注题型</h3>` + extras.map(extraRow).join('');
    const moreLink = S.showExtra ? '' :
      `<button type="button" class="btn btn--ghost btn--small need-more" onclick="PROTO.set({showExtra:true})">显示其他需关注题型（${extras.length}）</button>`;
    return `<section class="panel"><header class="panel__head"><h2>要补的点</h2><button type="button" class="btn btn--small" onclick="PROTO.quickDraft()">快速起草</button></header>
      <div class="panel__body">${filters(vis.length)}<div class="need-list" id="need-list">${rows}${moreLink}</div></div></section>`;
  }

  Object.assign(PROTO, { needsCol, visiblePoints, visiblePointSections,
    toggleChapter(c) {
      const s = PROTO.S.needChapters;
      PROTO.set({ needChapters: s.includes(c) ? s.filter(x => x !== c) : [...s, c] });
    } });
})();
