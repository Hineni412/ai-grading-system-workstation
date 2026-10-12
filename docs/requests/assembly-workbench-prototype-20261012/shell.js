/* 假应用外壳：侧栏导航 + 页头（照 navigation.ts 与 PageHeader.vue 观感） */
(function () {
  const GROUPS = [
    { label: '', items: [['workbench', '工作台', '⌂']] },
    { label: '考试与阅卷', items: [['sessions', '考试配置', '📋'], ['grading', '考试批改', '✎'], ['results', '成绩中心', '📊']] },
    { label: '题库与组卷', items: [['bank', '题库管理', '🗂'], ['assembly', '组卷工作台', '🧩']] },
    { label: '教学分析', items: [['knowledge', '知识与训练', '◈'], ['research', '教研提升', '✦']] },
  ];
  const BOTTOM = [['students', '学生名单', '👥'], ['settings', '设置', '⚙']];

  function renderSidebar() {
    const nav = document.getElementById('sbar-nav');
    nav.innerHTML = GROUPS.map(g =>
      '<div class="sbar__nav-group">' + (g.label ? `<p>${g.label}</p>` : '') +
      g.items.map(([id, label, ico]) =>
        `<a class="sbar__link" href="javascript:void(0)" ${id === 'assembly' ? 'aria-current="page"' : ''}><span class="ico">${ico}</span>${label}</a>`
      ).join('') + '</div>'
    ).join('');
    document.getElementById('sbar-settings').innerHTML =
      BOTTOM.map(([id, label, ico]) => `<a class="sbar__link" href="javascript:void(0)"><span class="ico">${ico}</span>${label}</a>`).join('');
  }

  /* 页头：组卷工作台 · 当前试卷篮 N 题 · 页签 · 试卷篮/去题库选题；题库管理（示意）独立页头 */
  function renderHeader() {
    const S = PROTO.S;
    const n = S.order.length;
    if (S.view === 'bank') {
      return `<header class="page-header">
        <div class="page-header__lead">
          <h1 class="page-header__title">题库管理</h1>
          <div class="page-header__meta">八年级上册</div>
        </div>
        <div class="page-header__navigation"><nav class="page-tabs" aria-label="题库浏览">
          <button type="button" class="is-active" aria-current="page">按标签</button>
          <button type="button" onclick="PROTO.toast('原型未实现')">按试卷</button></nav></div>
        <div class="page-header__actions"><button type="button" class="btn" onclick="PROTO.toggleBasket()">试卷篮 · ${n}</button></div>
      </header>`;
    }
    const tabs = [['class', '班级组卷'], ['export', '整理与导出']];
    const basketBtn = S.view === 'class'
      ? `<button type="button" class="btn" onclick="PROTO.toggleBasket()">试卷篮 · ${n}</button>` : '';
    return `<header class="page-header">
      <div class="page-header__lead">
        <h1 class="page-header__title">组卷工作台</h1>
        <div class="page-header__meta">当前试卷篮 ${n} 题</div>
      </div>
      <div class="page-header__navigation"><nav class="page-tabs" aria-label="组卷流程">${tabs.map(([v, l]) =>
        `<button type="button" class="${S.view === v ? 'is-active' : ''}" ${S.view === v ? 'aria-current="page"' : ''} onclick="PROTO.go('${v}')">${l}</button>`
      ).join('')}</nav></div>
      <div class="page-header__actions">${basketBtn}<button type="button" class="btn" onclick="PROTO.go('bank')">去题库选题</button></div>
    </header>`;
  }

  Object.assign(PROTO, { renderSidebar, renderHeader });
})();
