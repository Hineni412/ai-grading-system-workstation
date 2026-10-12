/* 启动：hash 路由、视图切换胶囊、原型状态面板、键盘快捷键、模拟后台重算 */
(function () {
  const S = PROTO.S;
  const root = document.getElementById('page-root');
  const RENDER = { class: () => PROTO.renderClass, preview: () => PROTO.renderPreview, export: () => PROTO.renderExport, bank: () => PROTO.renderBank };

  /* ---------- 路由：#class | #preview | #export?variant=a|b ---------- */
  function parseHash() {
    const h = location.hash.replace(/^#/, '') || 'class';
    const [view, qs] = h.split('?');
    const variant = new URLSearchParams(qs || '').get('variant');
    S.view = RENDER[view] ? view : 'class';
    if (variant === 'a' || variant === 'b') S.variant = variant;
  }
  function go(view) {
    if (view === 'export') location.hash = '#export?variant=' + S.variant;
    else location.hash = '#' + view;
  }
  function goPreview(from) { S.previewFrom = from; go('preview'); }
  function setVariant(v) {
    S.variant = v;
    history.replaceState(null, '', '#export?variant=' + v);
    render();
  }
  function cycleVariant(d) { setVariant(S.variant === 'a' ? 'b' : 'a'); }

  /* ---------- 渲染 ---------- */
  function render() {
    document.querySelectorAll('.chrome__views button').forEach(b =>
      b.classList.toggle('is-active', b.dataset.view === S.view));
    root.innerHTML = PROTO.renderHeader() + '<div id="view-root"></div>';
    RENDER[S.view]()(document.getElementById('view-root'));
    PROTO.renderBasket();
    document.getElementById('variant-switcher').hidden = S.view !== 'export';
    if (S.view === 'export') {
      document.getElementById('vsw-label').textContent =
        S.variant === 'a' ? '方案 A · 在卷面上直接排' : '方案 B · 卡片墙 + 卷面';
    }
    root.scrollTop = 0;
    updateState();
  }
  function updateState() {
    const st = document.getElementById('pstate-pre');
    if (st) st.textContent =
      '顺序: ' + S.order.map(e => e.qid).join(', ') +
      '\n修订: V' + S.revision + ' · ' + (S.saveState === 'saving' ? '保存中…' : '已保存 V' + S.revision) +
      '\n视图: ' + S.view + (S.view === 'export' ? ' · 方案 ' + S.variant.toUpperCase() : '') +
      '\n试卷篮: ' + (S.basketOpen ? '抽屉打开' : '收起');
  }

  /* ---------- 动作 ---------- */
  Object.assign(PROTO, { go, goPreview, setVariant, cycleVariant,
    setMode(v) { PROTO.set({ mode: v, showMeta: v === 'teacher' }); },
    setAnsMode(v) { PROTO.set({ ansMode: v }); },
    simulateRefresh() {
      if (S.refresh === 'busy') return;
      S.refresh = 'busy'; PROTO.emit('render');
      setTimeout(() => {
        const t = new Date();
        S.refresh = 'done'; S.refreshTime = String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
        PROTO.emit('render');
        setTimeout(() => { S.refresh = 'idle'; PROTO.emit('render'); }, 3500);
      }, 4000);
    },
  });

  /* ---------- 键盘 ---------- */
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t instanceof Element && t.closest('input,select,textarea,[contenteditable]')) return;
    if (S.view === 'export' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); cycleVariant(); return; }
    if (e.key.toLowerCase() === 'b' && (S.view === 'class' || S.view === 'bank')) { e.preventDefault(); PROTO.toggleBasket(); return; }
    if (S.view === 'class') {
      const k = e.key.toLowerCase();
      if (k === 'w') { e.preventDefault(); PROTO.needStep(-1); }
      else if (k === 's') { e.preventDefault(); PROTO.needStep(1); }
    }
  });

  /* ---------- 启动 ---------- */
  PROTO.renderSidebar();
  document.querySelectorAll('.chrome__views button').forEach(b =>
    b.addEventListener('click', () => go(b.dataset.view)));
  document.getElementById('sim-refresh').addEventListener('click', PROTO.simulateRefresh);
  document.getElementById('vsw-prev').addEventListener('click', () => cycleVariant(-1));
  document.getElementById('vsw-next').addEventListener('click', () => cycleVariant(1));
  PROTO.on('render', render);
  PROTO.on('save', updateState);
  window.addEventListener('hashchange', () => { parseHash(); render(); });
  parseHash();
  render();
})();
