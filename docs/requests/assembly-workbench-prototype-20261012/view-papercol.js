/* 试卷篮抽屉：原班级组卷右栏「这张卷」并入抽屉，组卷工作台与题库管理共用。
   推开式右栏（不占位时宽 0），复用整卷预览的卷面 + 题号轨道渲染。 */
(function () {
  const esc = s => PROTO.esc(s);
  const ctx = () => (PROTO.S.view === 'bank' ? 'bank' : 'class');

  /* 摘要：题数时长 · 覆盖（仅班级组卷）· 构成 · 规则状态 */
  function summaryHTML() {
    const S = PROTO.S;
    const c = PROTO.typeCounts();
    const [dMin, dMax] = PROTO.diffMinMax();
    const unmet = PROTO.ruleChecks().filter(x => !x.ok).length;
    const cov = ctx() === 'class' ? ` · 覆盖失分题 ${PROTO.coveredCount()}/${PROTO.totalFail()}` : '';
    return `<div class="basket__sum">
      <p class="basket__line"><b>${S.order.length} 题</b> · 约 ${PROTO.estMinutes()} 分钟${cov}</p>
      <p class="basket__comp muted">选 ${c['选择题'] || 0} · 填 ${c['填空题'] || 0} · 解 ${c['解答题'] || 0}/2 · 难度 ${dMin.toFixed(1)}–${dMax.toFixed(1)}</p>
      <button type="button" class="badge basket__rules ${unmet ? 'is-warning' : 'is-success'}" title="查看出卷设置" onclick="PROTO.goExportRules()">${unmet ? `有 ${unmet} 条未满足` : '规则全部满足 ✓'}</button>
    </div>`;
  }

  /* 每次 render 都重建抽屉：题数/顺序变化立即反映 */
  function renderBasket() {
    const S = PROTO.S;
    const app = document.querySelector('.app');
    const aside = document.getElementById('basket-root');
    const tab = document.getElementById('basket-tab');
    if (!aside || !tab) return;
    const allowed = S.view === 'class' || S.view === 'bank';
    if (!allowed) S.basketOpen = false;                 // 整卷预览 / 整理与导出整页即卷面，自动收起
    const open = allowed && S.basketOpen;
    app.classList.toggle('has-basket', open);
    aside.setAttribute('aria-hidden', open ? 'false' : 'true');
    tab.hidden = !allowed || open;                      // 仅班级组卷 / 题库管理可见右缘开关
    tab.querySelector('b').textContent = S.order.length;
    tab.querySelector('small').textContent = ctx() === 'class' ? PROTO.coveredCount() + '/' + PROTO.totalFail() : '';
    if (!open) { aside.innerHTML = ''; return; }
    aside.innerHTML = `<div class="basket__inner">
      <header class="basket__head"><h2>试卷篮</h2><span class="basket__pt" title="${esc(S.title)}">${esc(S.title)}</span>
        <button type="button" class="basket__x" aria-label="收起试卷篮" onclick="PROTO.toggleBasket(false)">×</button></header>
      <div class="basket__scroll">${summaryHTML()}
        <div class="basket__modes">${PROTO.modeControlsHTML({ metaKey: 'basketMeta' })}</div>
        <div id="basket-sheet"></div></div>
      <footer class="basket__foot">
        <button type="button" class="btn" onclick="PROTO.goPreview(PROTO.S.view)">整卷预览 ↗</button>
        <button type="button" class="btn btn--primary" onclick="PROTO.go('export')">去整理与导出 →</button>
        <button type="button" class="btn btn--ghost is-danger" onclick="PROTO.clearBasket()">清空</button>
      </footer></div>`;
    PROTO.renderSheetWrap(aside.querySelector('#basket-sheet'),
      { editable: true, embed: true, drawer: true, metaKey: 'basketMeta', swap: ctx() === 'class' });
  }

  Object.assign(PROTO, {
    renderBasket,
    toggleBasket(v) { PROTO.set({ basketOpen: v === undefined ? !PROTO.S.basketOpen : !!v }); },
    goExportRules() {
      PROTO.set({ rulesOpen: true }, false);
      PROTO.go('export');
      setTimeout(() => document.getElementById('exp-rules')
        ?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 80);
    },
  });
})();
