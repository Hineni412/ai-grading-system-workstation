/* 视图 2：整卷预览（新页面）—— 返回栏、版本切换、题号轨道 + 完整卷面 */
(function () {
  function render(el) {
    const S = PROTO.S;
    const back = S.previewFrom === 'export' ? ['export', '整理与导出'] : S.previewFrom === 'bank' ? ['bank', '题库管理'] : ['class', '班级组卷'];
    el.innerHTML = `<section class="preview-page">
      <div class="preview-top">
        <button type="button" class="btn btn--ghost back" onclick="PROTO.go('${back[0]}')">← 返回${back[1]}</button>
        <h2>${PROTO.esc(S.title || '未命名试卷')}</h2>
        <span class="meta">${S.order.length} 题 · 约 ${PROTO.estMinutes()} 分钟</span>
        <span class="spacer"></span>
        ${PROTO.modeControlsHTML()}
      </div>
      <div id="sheet-root"></div>
    </section>`;
    PROTO.renderSheetWrap(el.querySelector('#sheet-root'), { editable: true });
  }
  Object.assign(PROTO, { renderPreview: render });
})();
