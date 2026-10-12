/* 命题训练原型 - 核心：状态、工具条、步骤条、页脚、渲染编排 */
(function () {
  'use strict';

  var D = window.EXAM_DESIGN_DATA;
  var root = document.getElementById('app-root');

  if (!D || !D.papers || !D.papers.length) {
    root.innerHTML = '<div class="missing">缺少原型数据，请先运行 docs/requests/exam-design-prototype-20261012/build_data.py</div>';
    return;
  }

  var state = {
    paperIndex: 0,      // 默认组卷草稿
    cls: 'all',
    step: 1,
    openGroups: {},     // 逐题表父行展开
    expanded: null,     // 逐题表展开的题面行
    showSys: false,     // ② 显示系统预估
    unlocked: false,    // ② 已保存后点“修改（考前）”解锁
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function pct(v) { return v == null ? '—' : Math.round(v * 100) + '%'; }
  function pts(v) { return v == null ? '—' : Math.round(v * 100) + ' 个百分点'; }
  function paper() { return D.papers[state.paperIndex]; }
  function isDraft() { return paper().kind === 'draft'; }
  function stripPrefix(n) { return String(n || '').replace(/^题型·/, ''); }
  function lastSeg(t) { var s = String(t || ''); var i = s.lastIndexOf('｜'); return i >= 0 ? s.slice(i + 1) : s; }
  function parentId(id) { return String(id).replace(/\(P\d+\)$/, ''); }
  function mean(xs) {
    xs = (xs || []).filter(function (x) { return x != null; });
    return xs.length ? xs.reduce(function (a, b) { return a + b; }, 0) / xs.length : null;
  }
  // 难度档：<4 基础，4–7 中档，≥7 提高
  function band(d) { return d == null ? null : d < 4 ? 'basic' : d < 7 ? 'mid' : 'hard'; }
  var BAND_NAME = { basic: '基础', mid: '中档', hard: '提高' };
  function bandTag(d) {
    var b = band(d);
    return b ? '<span class="dband ' + b + '">' + BAND_NAME[b] + '</span>' : '';
  }
  // 得分率对象 {all, classes:{name:rate}} → 当前所选班级的值（②③ 传 'all' 固定全场）
  function rate(obj, cls) {
    if (!obj) return null;
    cls = cls === undefined ? state.cls : cls;
    if (cls === 'all') return obj.all;
    var c = (obj.classes || {})[cls];
    return c != null ? c : obj.all;
  }
  // 逐题表分组：[{id, items:[...]}]，保持卷面顺序
  function groups(p) {
    var out = [], cur = null;
    p.items.forEach(function (it) {
      if (!cur || cur.id !== it.parent_id) { cur = { id: it.parent_id, items: [] }; out.push(cur); }
      cur.items.push(it);
    });
    return out;
  }
  function rbar(v, clsName) {
    if (v == null) return '';
    var w = Math.max(0, Math.min(100, Math.round(v * 100)));
    return '<span class="rbar"><i style="width:' + w + '%' + (clsName ? ';background:' + clsName : '') + '"></i></span>';
  }

  // localStorage 命题记录：{predictions:{itemId:number}, saved_at}
  function storageKey(id) { return 'exam-design-proto:' + id; }
  function loadRecord(id) {
    try { var t = localStorage.getItem(storageKey(id)); return t ? JSON.parse(t) : null; }
    catch (e) { return null; }
  }
  function saveRecord(id, rec) {
    try { localStorage.setItem(storageKey(id), JSON.stringify(rec)); return true; }
    catch (e) { return false; }
  }
  function clearRecord(id) {
    try { localStorage.removeItem(storageKey(id)); } catch (e) { /* 忽略 */ }
  }

  // 工具条：卷子下拉（草稿 + 已建考试，新的在前）
  var sel = document.getElementById('paper-select');
  var gDraft = document.createElement('optgroup');
  gDraft.label = '组卷草稿';
  var gSess = document.createElement('optgroup');
  gSess.label = '已建考试（原型把已批改考试当作考前回测）';
  D.papers.forEach(function (p, i) {
    var o = document.createElement('option');
    o.value = i;
    o.textContent = p.title;
    (p.kind === 'draft' ? gDraft : gSess).appendChild(o);
  });
  sel.appendChild(gDraft);
  var sessOpts = Array.prototype.slice.call(gSess.children).reverse();
  sessOpts.forEach(function (o) { gSess.appendChild(o); });
  sel.appendChild(gSess);
  sel.value = '0';
  sel.addEventListener('change', function () {
    state.paperIndex = Number(sel.value);
    state.expanded = null;
    state.openGroups = {};
    state.unlocked = false;
    var p = paper();
    if (!p.classes[state.cls]) state.cls = 'all';
    if (p.kind === 'draft' && state.step === 3) state.step = 1;
    buildClassSeg(p);
    render();
  });

  // 班级分段：随卷子重建
  function buildClassSeg(p) {
    var seg = document.getElementById('class-seg');
    var names = Object.keys(p.classes || {}).sort();
    seg.innerHTML = '<button data-cls="all"' + (state.cls === 'all' ? ' class="on"' : '') + '>全部</button>' +
      names.map(function (n) {
        return '<button data-cls="' + esc(n) + '"' + (state.cls === n ? ' class="on"' : '') + '>' +
          esc(n) + '班</button>';
      }).join('');
  }
  document.getElementById('class-seg').addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    state.cls = b.dataset.cls;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    render();
  });

  // 步骤条
  document.getElementById('steps').addEventListener('click', function (ev) {
    var b = ev.target.closest('.step');
    if (!b || b.classList.contains('off')) return;
    state.step = Number(b.dataset.step);
    state.expanded = null;
    render();
  });
  function syncSteps(p) {
    document.querySelectorAll('#steps .step').forEach(function (b) {
      var n = Number(b.dataset.step);
      b.classList.toggle('on', n === state.step);
      b.classList.toggle('off', n === 3 && p.kind === 'draft');
    });
    var hint = document.getElementById('step-hint');
    if (p.kind === 'draft') hint.textContent = '③ 考试批改后才能对照';
    else if (state.step === 3) hint.textContent = '你的预测为全场口径，班级切换不影响本页';
    else hint.textContent = '';
  }

  var RULES = [
    '系统预估：用本场之前考试的作答拟合掌握度模型，再按每道题挂的训练目标和题库难度估计每名学生做对的可能性，取平均。原型把已批改考试当作考前回测，不读本场作答结果。',
    '系统预估按判定点等权计算；多判定点的解答题与按分值计算的实际得分率口径不完全一致。',
    '模拟草稿取自题库一份期中卷第一、二章的题，当前试卷篮为空；草稿未赋分，均分率按评分项简单平均。',
    '第二周是本学期第一场考试，之前没有作答，系统无法预估。',
    '你的预测只保存在本浏览器（原型）；正式版会保存为命题记录。',
  ];
  function footerHTML() {
    return '<details class="rules"><summary>口径说明</summary><ul>' +
      RULES.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') +
      '</ul></details>';
  }

  function render() {
    var p = paper();
    syncSteps(p);
    var html;
    if (state.step === 1) {
      html = window.renderPreview(p, state) + window.renderItems(p, state);
    } else if (state.step === 2) {
      html = window.renderPredict(p, state);
    } else {
      html = window.renderCompare(p, state);
    }
    root.innerHTML = html + footerHTML();
    if (state.step === 1 && window.bindItems) window.bindItems(p, state, render);
    if (state.step === 2 && window.bindPredict) window.bindPredict(p, state, render);
    if (state.step === 3 && window.bindCompare) window.bindCompare(p, state, render);
  }

  window.ED = {
    data: D, state: state, esc: esc, pct: pct, pts: pts, paper: paper, isDraft: isDraft,
    stripPrefix: stripPrefix, lastSeg: lastSeg, parentId: parentId, mean: mean,
    band: band, BAND_NAME: BAND_NAME, bandTag: bandTag, rate: rate, groups: groups,
    rbar: rbar, loadRecord: loadRecord, saveRecord: saveRecord, clearRecord: clearRecord,
    render: render,
  };

  buildClassSeg(paper());
  render();
})();
