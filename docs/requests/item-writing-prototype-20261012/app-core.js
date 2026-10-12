/* 命题训练原型 - 核心 */
(function () {
  'use strict';

  var O = window.ITEM_OPTIONS;
  var root = document.getElementById('app-root');
  var state = { screen: 'materials', step: 1, work: null, imagePreview: null, reviewing: false };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function material(id) { return window.ITEM_MATERIALS.find(function (m) { return m.id === id; }); }
  function mat() { return state.work ? material(state.work.material) : null; }
  function key(id) { return 'item-writing-proto:work:' + id; }
  function loadWork(id) {
    try { var t = localStorage.getItem(key(id)); return t ? JSON.parse(t) : null; } catch (e) { return null; }
  }
  function saveWork() {
    if (!state.work) return;
    try { localStorage.setItem(key(state.work.material), JSON.stringify(state.work)); } catch (e) { /* 忽略 */ }
  }
  function newPart() { return { ask: '', solo: '', score: null, answer: '', steps: [['', null]] }; }
  function emptyWork(id) {
    return { material: id, startedAt: Date.now(), picked: [], sections: [], coreIdea: '',
      intent: '', competencies: [], requirement: '', contextKind: '', materialUse: '',
      adaptMethods: [], realism: '', questionType: '', openness: '', parts: [newPart()],
      stem: '', options: ['', '', '', ''], correctOption: null, otherSolutions: '',
      difficulty: 5, predictedRate: null, errors: [], statement: '', talkChecked: [],
      selfEval: {}, ocr: null };
  }
  function startWork(id, sample) {
    var w = sample ? JSON.parse(JSON.stringify(window.ITEM_SAMPLE_WORK)) : loadWork(id);
    state.work = Object.assign(emptyWork(id), w || {});
    if (sample) state.work.sample = true;
    state.screen = 'work'; state.step = 1; state.imagePreview = null; state.reviewing = false;
    saveWork(); render();
  }

  function elapsed() {
    var w = state.work;
    if (!w) return 0;
    if (w.elapsedMinutes != null) return w.elapsedMinutes;
    return Math.max(0, Math.floor((Date.now() - (w.startedAt || Date.now())) / 60000));
  }
  function updateTimer() {
    var m = mat(), el = document.getElementById('iw-timer');
    if (!el) return;
    if (!m) { el.textContent = '—'; el.classList.remove('over'); return; }
    var e = elapsed(), lim = m.requirement.minutes;
    el.textContent = '已用 ' + e + ' / 限时 ' + lim + ' 分钟';
    el.classList.toggle('over', e > lim);
  }
  setInterval(function () { if (state.screen === 'work') { updateTimer(); updateRail(); } }, 20000);

  function stepDone(n) {
    var w = state.work, m = mat();
    if (!w || !m) return false;
    switch (n) {
      case 1: return w.picked.length > 0 && w.sections.length > 0 && !!w.coreIdea.trim();
      case 2: return !!w.intent.trim() && w.competencies.length > 0 && !!w.requirement;
      case 3: return !!w.contextKind && !!w.materialUse && !!w.realism.trim() &&
        (m.kind === '母题' ? w.adaptMethods.length > 0 : true);
      case 4: return !!w.questionType && !!w.openness && w.parts.length > 0 &&
        w.parts.every(function (p) { return p.ask.trim() && p.solo && Number(p.score) > 0; });
      case 5: return !!w.stem.trim() && (w.questionType !== '选择题' ||
        (w.options.every(function (o) { return o.trim(); }) && w.correctOption != null));
      case 6: return w.parts.length > 0 && w.parts.every(function (p) {
        return p.answer.trim() && p.steps.some(function (s) { return String(s[0]).trim(); }); });
      case 7: return w.predictedRate != null && w.errors.length > 0 &&
        w.errors.every(function (e) { return String(e[0]).trim() && e[1]; });
      case 8: return !!w.statement.trim() && w.talkChecked.length === O.talkPoints.length;
    }
    return false;
  }

  // 本机自检（作答时即时显示）
  function selfCheck() {
    var w = state.work, m = mat(), out = [];
    if (!w || !m) return out;
    var und = O.steps.filter(function (_, i) { return !stepDone(i + 1); });
    if (und.length) out.push({ cls: 'bad', text: '未完成：' + und.join('、') });
    var ps = w.parts.reduce(function (a, p) { return a + (Number(p.score) || 0); }, 0);
    if (w.parts.some(function (p) { return p.score != null; }) && ps !== m.requirement.score) {
      out.push({ cls: 'bad', text: '小问分值合计 ' + ps + ' / 要求 ' + m.requirement.score + ' 分' });
    }
    w.parts.forEach(function (p, i) {
      var ss = p.steps.reduce(function (a, s) { return a + (Number(s[1]) || 0); }, 0);
      if (Number(p.score) > 0 && ss !== Number(p.score)) {
        out.push({ cls: 'bad', text: '第（' + (i + 1) + '）问评分步骤合计 ' + ss +
          ' 分，与该问分值 ' + p.score + ' 分不符' });
      }
    });
    if (w.questionType === '选择题' && w.correctOption == null) {
      out.push({ cls: 'bad', text: '选择题未选正确项' });
    }
    if (m.kind === '母题' && !w.adaptMethods.length) out.push({ cls: 'bad', text: '母题材料未选改编方法' });
    var e = elapsed();
    if (e > m.requirement.minutes) {
      out.push({ cls: 'bad', text: '已用 ' + e + ' 分钟，超过限时 ' + m.requirement.minutes + ' 分钟' });
    }
    if (!out.some(function (x) { return x.cls === 'bad'; })) {
      out.push({ cls: 'ok', text: '本机自检通过' });
    }
    out.push({ cls: 'na', text: '与题库相似度：正式版在评价时检查（原型不检查）' });
    return out;
  }

  function railHTML() {
    var steps = O.steps.map(function (n, i) {
      var done = stepDone(i + 1);
      return '<button class="iw-st' + (state.step === i + 1 ? ' on' : '') + (done ? ' done' : '') +
        '" data-jump="' + (i + 1) + '"><span class="n">' + (i + 1) + '</span>' + esc(n) +
        '<span class="st-mark">' + (done ? '完成' : '未完成') + '</span></button>';
    }).join('');
    var checks = selfCheck().map(function (c) {
      return '<div class="' + c.cls + '">' + esc(c.text) + '</div>';
    }).join('');
    return '<div class="iw-rail"><button class="btn" id="iw-back-mats" style="width:100%">‹ 返回选材料</button>' +
      '<div class="card"><header><h2>进度</h2></header>' +
      '<div class="body iw-steps">' + steps + '</div></div>' +
      '<div class="card"><header><h2>本机自检</h2></header><div class="body iw-check">' + checks + '</div></div>' +
      '<button class="btn primary" id="iw-go-eval" style="width:100%">去评价</button></div>';
  }
  function updateRail() {
    var rail = document.querySelector('.iw-rail');
    if (rail && state.screen === 'work') { rail.outerHTML = railHTML(); bindRail(); }
  }
  function bindRail() {
    var bm = document.getElementById('iw-back-mats');
    if (bm) bm.addEventListener('click', function () { state.screen = 'materials'; render(); });
    document.querySelectorAll('.iw-st').forEach(function (b) {
      b.addEventListener('click', function () { state.step = Number(b.dataset.jump); render(); });
    });
    var g = document.getElementById('iw-go-eval');
    if (g) g.addEventListener('click', function () { state.screen = 'eval'; render(); });
  }

  function matPanelHTML() {
    var m = mat();
    if (!m) return '';
    var tbl = m.table ? '<table><tr>' + m.table.head.map(function (h) { return '<td>' + esc(h) + '</td>'; }).join('') +
      '</tr>' + m.table.rows.map(function (r) {
        return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>';
      }).join('') + '</table>' : '';
    var r = m.requirement;
    return '<div class="card iw-mat"><header><h2>' + esc(m.title) + '</h2>' +
      '<span class="iw-kind">' + esc(m.kind) + '</span></header><div class="body">' +
      m.sentences.map(function (s) { return '<div class="sent">' + esc(s) + '</div>'; }).join('') + tbl +
      '<div class="iw-req">要求：' + esc(r.scope) + ' · ' + esc(r.type) + ' · ' + r.score +
      ' 分 · ' + esc(r.parts) + ' · ' + esc(r.context) + ' · 限时 ' + r.minutes + ' 分钟</div>' +
      '</div></div>';
  }

  // 通用绑定：data-b=字段，data-p=小问，data-ps="i:j"步骤，data-e=错误行，data-o=选项
  function applyInput(el) {
    var w = state.work, f = el.dataset.b;
    if (!w || !f) return;
    var v = el.type === 'number' ? (el.value === '' ? null : Number(el.value)) : el.value;
    if (el.dataset.ps) {
      var ps = el.dataset.ps.split(':').map(Number);
      w.parts[ps[0]].steps[ps[1]][Number(el.dataset.col)] = v;
    } else if (el.dataset.e != null) {
      w.errors[Number(el.dataset.e)][Number(el.dataset.col)] = v;
    } else if (el.dataset.p != null) {
      w.parts[Number(el.dataset.p)][f] = v;
    } else if (el.dataset.o != null) {
      w.options[Number(el.dataset.o)] = v;
    } else if (el.type === 'checkbox') {
      var arr = w[f], i = arr.indexOf(el.value);
      if (el.checked && i < 0) arr.push(el.value);
      if (!el.checked && i >= 0) arr.splice(i, 1);
    } else if (el.type === 'radio') {
      if (el.checked) w[f] = el.dataset.num != null ? Number(el.value) : el.value;
    } else {
      w[f] = v;
    }
    saveWork(); updateRail();
    if (window.updateSums) window.updateSums();
  }
  function bindInputs() {
    root.addEventListener('input', function (ev) {
      var el = ev.target.closest('[data-b]');
      if (!el) return;
      if (el.type === 'checkbox' || el.type === 'radio' || el.tagName === 'SELECT') return;
      applyInput(el);
    });
    root.addEventListener('change', function (ev) {
      var el = ev.target.closest('select[data-b],input[type="radio"][data-b],input[type="checkbox"][data-b]');
      if (el) applyInput(el);
    });
  }

  function showDlg(html) {
    var d = document.getElementById('iw-dlg');
    document.getElementById('iw-dlg-box').innerHTML = html;
    d.hidden = false;
  }
  function hideDlg() { document.getElementById('iw-dlg').hidden = true; }
  document.getElementById('iw-dlg').addEventListener('click', function (ev) {
    if (ev.target === this) hideDlg();
  });

  var RULES = [
    '量表的 9 个维度、权重和水平描述是本方案综合教基〔2019〕15号、课标命题建议与各地比赛要求提出的建议，不是官方评分细则。',
    '原型中的材料、示例作品、识别结果与评价都是演示内容，不是真实数据，也不是模型输出。',
    '计时期间正式版只显示教材目录与课标要点，靠本人自觉，不做强制锁定。',
  ];
  function footerHTML() {
    return '<details class="rules"><summary>口径说明</summary><ul>' +
      RULES.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul></details>';
  }

  function render() {
    var crumb = document.getElementById('iw-crumb');
    var m = mat();
    crumb.textContent = m ? m.title + '（' + m.kind + '）' : '—';
    if (state.screen === 'materials') {
      root.innerHTML = window.renderMaterials() + footerHTML();
      window.bindMaterials();
    } else if (state.screen === 'work') {
      var form = state.step <= 4 ? window.renderForm14() : window.renderForm58();
      root.innerHTML = '<div class="iw-grid">' + matPanelHTML() +
        '<div class="iw-form card"><header><h2>' + state.step + ' ' + esc(O.steps[state.step - 1]) +
        '</h2></header><div class="body iw-form">' + form + '</div></div>' +
        railHTML() + '</div>' + footerHTML();
      if (state.step <= 4) window.bindForm14(); else window.bindForm58();
      bindRail();
    } else {
      root.innerHTML = window.renderEval() + footerHTML();
      window.bindEval();
    }
    updateTimer();
  }

  window.IW = {
    state: state, O: O, esc: esc, mat: mat, material: material,
    emptyWork: emptyWork, newPart: newPart, startWork: startWork,
    loadWork: loadWork, saveWork: saveWork, elapsed: elapsed,
    stepDone: stepDone, selfCheck: selfCheck, railHTML: railHTML,
    showDlg: showDlg, hideDlg: hideDlg, render: render, updateRail: updateRail,
  };

  bindInputs();
  render();
})();
