/* 题库管理（示意）：简化列表，演示题库管理共用同一试卷篮抽屉 */
(function () {
  const esc = s => PROTO.esc(s);

  function card(q) {
    const inB = PROTO.inBasket(q.id);
    return `<article class="bankq ${inB ? 'is-in' : ''}">
      <div class="bankq__head"><strong>题号 ${q.id}</strong><span class="bankq__src">八年级上册 · 题库直选</span>
        <button type="button" class="btn btn--small ${inB ? '' : 'btn--primary'}"
          onclick="${inB ? `PROTO.remove('${q.id}')` : `PROTO.add('${q.id}', '', '题库直选')`}">${inB ? '已在试卷篮' : '加入试卷篮'}</button></div>
      <div class="bankq__stem clamp clamp--3">${esc(q.stem)}</div>
      <div class="bankq__tags"><span class="ttag ${PROTO.TT[q.type]}">${q.type[0]}</span><span class="chip">难度 ${q.diff.toFixed(1)}</span></div>
    </article>`;
  }

  function render(el) {
    el.innerHTML = `<section class="bank-page">
      <p class="bank-note">示意列表：演示题库管理与组卷工作台共用同一试卷篮抽屉（右缘开关或 B 键）。</p>
      <div class="bankq-grid">${PROTO.QUESTIONS.slice(0, 8).map(card).join('')}</div>
    </section>`;
  }

  Object.assign(PROTO, { renderBank: render });
})();
