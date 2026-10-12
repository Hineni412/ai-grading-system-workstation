/* 视图 3：整理与导出 —— 方案 A「在卷面上直接排」/ 方案 B「卡片墙 + 卷面」 */
(function () {
  const esc = s => PROTO.esc(s);
  const T = () => PROTO.TT;

  /* ---------- 左右固定面板（两方案共用） ---------- */
  function settingsPanel() {
    const S = PROTO.S;
    const secs = (S.sectionNames || ['基础巩固', '综合提升']);
    return `<aside class="panel exp-panel"><header class="panel__head"><div><p class="exp-kicker">PAPER SETTINGS</p><h2>试卷设置</h2></div></header>
      <div class="panel__body">
        <label class="exp-field"><span>试卷标题</span><input class="app-input" value="${esc(S.title)}" onchange="PROTO.set({title:this.value})"></label>
        <label class="exp-field"><span>副标题</span><input class="app-input" value="${esc(S.subtitle)}" placeholder="如：限时 45 分钟" onchange="PROTO.set({subtitle:this.value})"></label>
        <details class="blk" open><summary>更多设置</summary>
          <label class="exp-field" style="margin-top:8px"><span>预览版本</span>
            <select class="app-input" onchange="PROTO.setMode(this.value)"><option value="student" ${S.mode === 'student' ? 'selected' : ''}>学生版</option><option value="teacher" ${S.mode === 'teacher' ? 'selected' : ''}>教师版</option></select></label>
          <label class="exp-field"><span>试题排版</span>
            <select class="app-input" onchange="PROTO.set({layout:this.value})">
              <option value="sequential" ${S.layout === 'sequential' ? 'selected' : ''}>按当前顺序</option>
              <option value="grouped_by_type" ${S.layout === 'grouped_by_type' ? 'selected' : ''}>按题型归组</option>
              <option value="sections" ${S.layout === 'sections' ? 'selected' : ''}>按手动分节</option></select></label>
          <label class="exp-check"><input type="checkbox" ${S.includeAnswer ? 'checked' : ''} onchange="PROTO.set({includeAnswer:this.checked})"><span>导出时包含答案</span></label>
        </details>
        <div class="exp-field"><span>分节</span><div class="sec-list">${secs.map((t, i) =>
          `<div><input class="app-input" value="${esc(t)}" onchange="PROTO.renameSection(${i},this.value)"><small>${i === 0 ? Math.ceil(S.order.length / 2) : Math.floor(S.order.length / 2)} 题</small><button type="button" onclick="PROTO.toast('原型：分节增删未实现')">×</button></div>`).join('')}</div></div>
        ${rulesBlock()}
      </div></aside>`;
  }

  /* 出卷设置：原「这张卷」右栏的规则与核对移到这里 */
  function rulesBlock() {
    const S = PROTO.S;
    const checks = PROTO.ruleChecks();
    const unmet = checks.filter(x => !x.ok).length;
    return `<details class="blk" id="exp-rules" ${S.rulesOpen ? 'open' : ''} ontoggle="PROTO.S.rulesOpen=this.open"><summary>出卷设置<small>${S.order.length} 题 · 同题型 ≤ 1 · 解答 ≤ 2 · ${unmet ? '有 ' + unmet + ' 条未满足' : '规则全部满足 ✓'}</small></summary>
      <div class="rules-grid"><label>用途<select class="app-input"><option>讲义 · 只打印</option><option>训练卷 · 回收批改</option></select></label>
      <label>每卷题数<input class="app-input" type="number" value="10"></label>
      <label>难度上限<input class="app-input" type="number" value="8"></label>
      <label>同一题型最多<input class="app-input" type="number" value="1"></label>
      <label>解答题最多<input class="app-input" type="number" value="2"></label>
      <label>近期原题排除次数<input class="app-input" type="number" value="3"></label></div>
      <div class="rules-checks">${checks.map(x => `<p class="${x.ok ? 'ok' : 'bad'}">${x.ok ? '✓' : '×'} ${x.t}</p>`).join('')}</div>
    </details>`;
  }
  function exportPanel() {
    return `<aside class="panel exp-panel"><header class="panel__head"><div><p class="exp-kicker">EXPORT</p><h2>导出</h2></div></header>
      <div class="panel__body">
        <select class="app-input" aria-label="导出格式"><option>Word（方便修改）</option><option>PDF（适合打印）</option></select>
        <button type="button" class="btn btn--primary" onclick="PROTO.toast('原型不生成真实文件')">导出 Word</button>
        <div class="exp-recs"><strong>导出记录</strong><p style="margin:5px 0 0">暂无导出记录。0 条</p></div>
      </div></aside>`;
  }

  /* ---------- 方案 A：在卷面上直接排 ---------- */
  function variantA() {
    return `<div id="exp-sheet"><div class="preview-top" style="margin-bottom:10px"><h2 style="font-size:15px;margin:0">卷面即顺序 · 直接拖拽调整</h2><span class="spacer"></span>${PROTO.modeControlsHTML()}</div><div id="sheet-root"></div></div>`;
  }

  /* ---------- 方案 B：卡片墙 + 卷面（跟随试题排版分组，可跨组拖拽） ---------- */
  function wallCard(e) {
    const i = PROTO.S.order.findIndex(x => x.qid === e.qid) + 1;
    return `<div class="wcard" data-qid="${e.qid}" draggable="true">
      <button type="button" class="wcard__x" aria-label="移出" onclick="PROTO.remove('${e.qid}')">×</button>
      <div class="wcard__top"><span class="wcard__num">${i}</span><span class="ttag ${T()[e.q.type]}">${e.q.type[0]}</span><span class="wcard__meta">难度 ${e.q.diff.toFixed(1)}</span></div>
      <div class="wcard__pt">${esc((PROTO.point(e.pointId) || {}).name || '—')}</div>
      <div class="wcard__stem">${esc(e.q.stem)}</div>
    </div>`;
  }
  function cardWall() {
    const groups = PROTO.sheetSections().map(sec =>
      (sec.title ? `<h3 class="wall-group-title">${esc(sec.title)}</h3>` : '') +
      `<div class="wall">${sec.items.map(wallCard).join('')}</div>`).join('');
    return `<p class="wall-caption">已按此顺序排版 · 整卡可拖，松手即换位置</p>
      <div class="wall-groups" data-dnd="wall" data-dnd-dir="x">${groups}</div>`;
  }
  function variantB() {
    const S = PROTO.S;
    return `<div id="exp-wall">${cardWall()}
      <details class="sheet-fold" ${S.exportOpen ? 'open' : ''} ontoggle="PROTO.S.exportOpen=this.open;if(this.open)PROTO.emit('render')">
        <summary>预览试卷 · ${S.mode === 'teacher' ? '教师版' : '学生版'}</summary>
        <div class="sheet-fold__body"><div class="preview-top" style="margin-bottom:8px"><span class="spacer"></span>${PROTO.modeControlsHTML()}</div><div id="sheet-root"></div></div>
      </details></div>`;
  }

  function render(el) {
    const S = PROTO.S;
    el.innerHTML = `<section class="export-page"><div class="export-grid">
      ${settingsPanel()}
      <main class="exp-center">${S.variant === 'a' ? variantA() : variantB()}</main>
      ${exportPanel()}
    </div></section>`;
    const root = el.querySelector('#sheet-root');
    if (root && (S.variant === 'a' || S.exportOpen)) PROTO.renderSheetWrap(root, { editable: true, embed: true });
    const wall = el.querySelector('[data-dnd="wall"]');
    if (wall) PROTO.dnd(wall, '.wcard');
  }

  Object.assign(PROTO, { renderExport: render,
    renameSection(i, v) { const s = PROTO.S.sectionNames || (PROTO.S.sectionNames = ['基础巩固', '综合提升']); s[i] = v; PROTO.emit('render'); } });
})();
