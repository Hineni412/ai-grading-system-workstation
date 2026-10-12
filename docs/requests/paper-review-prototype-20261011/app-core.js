/* 试卷评价原型 - 核心：状态、分类规则、工具条、编排渲染 */
(function () {
  'use strict';

  var D = window.PAPER_REVIEW_DATA;
  var root = document.getElementById('app-root');

  if (!D || !D.exams || !D.exams.length) {
    root.innerHTML = '<div class="missing">缺少原型数据，请先运行 tmp_paper_review_prototype/build_data.py</div>';
    return;
  }

  // 测试开关：?nulltest=1 把第二场考试（第三周）的典型题覆盖分数置空
  if (/[?&]nulltest=1/.test(location.search)) {
    var t4 = D.exams.find(function (e) { return e.session_id === 4; });
    if (t4) t4.evaluation.dimensions.find(function (d) { return d.key === 'coverage'; }).score = null;
  }

  var state = {
    examIndex: D.exams.length - 1, // 默认最新一场
    cls: 'all',
    filter: 'all',
    expanded: null,
    openDim: null,
    openGroups: {},
    scatterParts: false,
  };

  var TYPE_NAMES = { choice: '选择', fill_blank: '填空', calculation: '计算', comprehensive: '综合' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function pct(v) { return (v * 100).toFixed(1) + '%'; }
  function stripPrefix(name) { return String(name || '').replace(/^题型·/, ''); }
  function exam() { return D.exams[state.examIndex]; }
  function displayRate(item) {
    if (state.cls === 'all') return item.rate;
    var cr = item.class_rates || {};
    return cr[state.cls] != null ? cr[state.cls] : item.rate;
  }
  function dotsHTML(r) {
    var n = r >= 0.4 ? 4 : r >= 0.3 ? 3 : r >= 0.2 ? 2 : 1;
    var s = '';
    for (var i = 0; i < 4; i++) s += '<b class="' + (i < n ? 'f' : '') + '"></b>';
    return '<span class="dots">' + s + '</span>';
  }
  function parentId(id) { return String(id).replace(/\(P\d+\)$/, ''); }
  function markKey(item) { return 'paper-review-mark:' + exam().session_id + ':' + parentId(item.id); }
  function getMark(item) { try { return localStorage.getItem(markKey(item)) || ''; } catch (e) { return ''; } }
  function setMark(item, v) {
    try {
      if (v) localStorage.setItem(markKey(item), v);
      else localStorage.removeItem(markKey(item));
    } catch (e) { /* file:// 下不可写时静默 */ }
  }

  // 分类规则（均按整场口径，不随班级切换）
  function quadrant(item) {
    var p = item.rate, r = item.r_rest;
    if (p >= 0.7) return r >= 0.4 ? 'good' : 'free';
    return r >= 0.4 ? 'high' : 'suspect';
  }
  var QUAD = {
    good:    { name: '基础能区分', cls: 'q-good' },
    free:    { name: '送分',       cls: 'q-free' },
    high:    { name: '有难度·能区分', cls: 'q-high' },
    suspect: { name: '可疑',       cls: 'q-suspect' },
  };
  function predFlag(item) {
    var d = item.predicted_difficulty;
    if (d == null) return { key: 'none', name: '无预估' };
    var tier = d < 4 ? 'basic' : d < 7 ? 'mid' : 'hard';
    var rate = item.rate;
    if (tier === 'basic' && rate < 0.6) return { key: 'harder', name: '比预估难' };
    if ((tier === 'mid' && rate >= 0.85) || (tier === 'hard' && rate >= 0.7))
      return { key: 'easier', name: '比预估容易' };
    return { key: 'fit', name: '与预估相符' };
  }
  function typicalError(item) {
    var e = item.errors && item.errors[0];
    if (!e) return null;
    return (e.count >= 5 && e.count >= 0.3 * item.lost_students) ? e : null;
  }
  function enrich(item) {
    return { item: item, quad: quadrant(item), pred: predFlag(item),
      typical: typicalError(item), mark: getMark(item) };
  }

  // 工具条
  var sel = document.getElementById('exam-select');
  D.exams.forEach(function (e, i) {
    var o = document.createElement('option');
    o.value = i;
    o.textContent = e.title + '（' + e.graded_at + '）';
    sel.appendChild(o);
  });
  sel.value = String(state.examIndex);
  sel.addEventListener('change', function () {
    state.examIndex = Number(sel.value);
    state.expanded = null;
    state.openDim = null;
    state.openGroups = {};
    render();
  });
  document.getElementById('class-seg').addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    state.cls = b.dataset.cls;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    render();
  });

  var RULES = [
    '典型题覆盖：每个小节按本卷实际选了几个题型（k 个）计，用所选题型的出现套数之和，比该节最高频 k 个题型的套数之和；常见难度 ≥7 的偏难题型没选不扣分；只作为次要题型考到算一半。',
    '题量与时间：总分居中 50% 的学生里，末尾连续空白达满分 15% 的人数比例；≤5% 得 100，≥40% 得 0。',
    '分数分布：均分率在 60%–80%、标准差在 0.12–0.25、满分段（≥95%）和 40% 以下各不超过 10% 时满分，三项取平均。',
    '易错暴露：题库预估难度 <4 的基础题里，得分率 0.40–0.85 且区分度 ≥0.30 的分值占比。',
    '题目区分：按分值加权，区分度 ≥0.4 记满分，0.3–0.4 记 0.8，0.2–0.3 记 0.4，<0.2 记 0。',
    '总分：五项按权重加权平均；数据不足的项不计入，权重按剩余项重新分配。',
    '题型常见难度：期中期末卷里该题型各题题库难度的中位数（按整题难度，含多问大题时可能偏高）。',
    '以上分界线是原型初值，待和老师一起校准。',
  ];
  function footerHTML() {
    return '<p class="footnote">本页依据本场学生作答，反映这批学生做这张卷的情况，不等于题目的绝对难度。</p>' +
      '<details class="rules"><summary>评分规则</summary><ul>' +
      RULES.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') +
      '</ul></details>';
  }

  function render() {
    var e = exam();
    var rows = e.items.map(enrich);
    root.innerHTML = window.renderHero(e, state) +
      window.renderWatch(e, rows, state) +
      window.renderTable(e, rows, state) +
      window.renderCoverage(e) +
      footerHTML();
    window.bindHero(e, state, render);
    window.bindWatch(e, state, render);
    window.bindTable(e, rows, state, render);
    window.bindCoverage();
  }

  // 定位到某行：属于多小问题时先展开父行
  function goToItem(id) {
    var e = exam();
    var grp = (e.groups || []).find(function (g) { return g.id === id || g.parts.indexOf(id) >= 0; });
    if (grp) state.openGroups[grp.id] = true;
    state.filter = 'all';
    state.expanded = (grp && grp.id === id) ? null : id;
    render();
    var row = document.getElementById('row-' + id);
    if (row) {
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
      row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
    }
  }

  window.PR = { state: state, esc: esc, pct: pct, stripPrefix: stripPrefix, QUAD: QUAD,
    displayRate: displayRate, dotsHTML: dotsHTML, getMark: getMark, setMark: setMark,
    TYPE_NAMES: TYPE_NAMES, typicalError: typicalError, predFlag: predFlag,
    quadrant: quadrant, parentId: parentId, goToItem: goToItem, render: render };

  render();
})();
