/* 命题训练原型 - 核心 */
(function () {
  'use strict';

  var O = window.ITEM_OPTIONS;
  var root = document.getElementById('app-root');
  var state = { screen: 'materials', step: 1, work: null, imagePreview: null, reviewing: false,
    evalMsg: null, evalProg: null, evalFail: null, extracting: false, extractResult: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function material(id) { return IW.lib.byId(id) ||
    (window.ITEM_MATERIALS || []).find(function (m) { return m.id === id; }) || null; }
  function reqOf(w) {
    if (!w) return {};
    if (w.taskCard && w.taskCard.requirement) return w.taskCard.requirement;
    var m = material(w.material);
    return (m && m.requirement) || {};
  }
  function mat() {
    var w = state.work;
    if (!w) return null;
    var m = material(w.material) || {};
    var snap = (w.taskCard && w.taskCard.materialSnapshot) || {};
    return { id: m.id, kind: snap.kind || m.kind, title: snap.title || m.title,
      sentences: snap.sentences || m.sentences || [], table: snap.table || m.table,
      section: snap.section || m.section, requirement: reqOf(w) };
  }
  function key(id) { return 'item-writing-proto:work:' + id; }
  function loadWork(id) {
    try { var t = localStorage.getItem(key(id)); return t ? JSON.parse(t) : null; } catch (e) { return null; }
  }
  function saveWork() {
    if (!state.work) return;
    if (state.screen === 'work' && IW.lib.review(state.work.material)) state.work.dirty = true;
    try { localStorage.setItem(key(state.work.material), JSON.stringify(state.work)); } catch (e) { /* 忽略 */ }
    var el = document.getElementById('iw-saved');
    if (el) el.textContent = '已保存 · ' +
      ('0' + new Date().getHours()).slice(-2) + ':' + ('0' + new Date().getMinutes()).slice(-2);
  }
  function newPart() { return { ask: '', solo: '', score: null, answer: '', steps: [['', null]] }; }
  function emptyWork(id) {
    return { material: id, version: 1, startedAt: Date.now(), picked: [], sections: [], coreIdea: '',
      intent: '', competencies: [], requirement: '', contextKind: '', materialUse: '',
      adaptMethods: [], realism: '', questionType: '', openness: '', parts: [newPart()],
      stem: '', options: ['', '', '', ''], correctOption: null, otherSolutions: '',
      difficulty: 5, predictedRate: null, errors: [], statement: '', talkChecked: [],
      selfEval: {}, ocr: null, taskCard: null };
  }
  function makeTaskCard(m, req) {
    return { materialId: m.id, materialRevision: m.revision || 1,
      materialSnapshot: { id: m.id, kind: m.kind, title: m.title,
        sentences: (m.sentences || []).slice(), table: m.table || null,
        section: m.section || '', context_kind: m.context_kind || '', situation: m.situation || '' },
      requirement: req };
  }
  function ensureTaskCard(w) {
    if (w && !w.taskCard) { var m = material(w.material) || {}; w.taskCard = makeTaskCard(m, m.requirement || {}); }
    return w;
  }
  function startWork(m, req, sample) {
    var w = sample ? JSON.parse(JSON.stringify(window.ITEM_SAMPLE_WORK)) : loadWork(m.id);
    w = ensureTaskCard(Object.assign(emptyWork(m.id), w || {}));
    if (req) w.taskCard = makeTaskCard(m, req);
    if (sample) w.sample = true;
    state.work = w; state.screen = 'work'; state.step = 1;
    state.imagePreview = null; state.reviewing = false; state.evalMsg = null;
    state.evalProg = null; state.evalFail = null;
    saveWork(); render();
  }
  function resumeWork(id) {
    var w = loadWork(id);
    if (!w) return;
    state.work = ensureTaskCard(Object.assign(emptyWork(id), w));
    state.screen = 'work'; state.step = 1; state.reviewing = false; state.evalMsg = null;
    state.evalProg = null; state.evalFail = null;
    render();
  }

  function elapsed() {
    var w = state.work;
    if (!w) return 0;
    if (w.elapsedMinutes != null) return w.elapsedMinutes;
    return Math.max(0, Math.floor((Date.now() - (w.startedAt || Date.now())) / 60000));
  }
  function updateTimer() {
    var el = document.getElementById('iw-timer');
    if (!el) return;
    if (state.screen === 'materials' || !state.work) { el.textContent = '—'; el.classList.remove('over'); return; }
    var e = elapsed(), lim = +reqOf(state.work).minutes || 0;
    el.textContent = '已用 ' + e + ' / 限时 ' + lim + ' 分钟';
    el.classList.toggle('over', e > lim);
  }
  setInterval(function () { if (state.screen === 'work') { updateTimer(); updateRail(); } }, 20000);

  function doneFor(w, n) {
    var m = { kind: w.taskCard ? w.taskCard.materialSnapshot.kind : null };
    if (!m.kind && material(w.material)) m.kind = material(w.material).kind;
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
  function stepDone(n) { return state.work ? doneFor(state.work, n) : false; }
  function doneCount(w) { return O.steps.reduce(function (n, _, i) { return n + (doneFor(w, i + 1) ? 1 : 0); }, 0); }

  function selfCheck() {
    var w = state.work, r = reqOf(w), out = [];
    if (!w) return out;
    var m = mat();
    var und = O.steps.filter(function (_, i) { return !stepDone(i + 1); });
    if (und.length) out.push({ cls: 'bad', text: '未完成：' + und.join('、') });
    var ps = w.parts.reduce(function (a, p) { return a + (Number(p.score) || 0); }, 0);
    if (w.parts.some(function (p) { return p.score != null; }) && ps !== +r.score) {
      out.push({ cls: 'bad', text: '小问分值合计 ' + ps + ' / 要求 ' + r.score + ' 分' });
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
    if (m && m.kind === '母题' && !w.adaptMethods.length) out.push({ cls: 'bad', text: '母题材料未选改编方法' });
    var e = elapsed();
    if (r.minutes && e > +r.minutes) {
      out.push({ cls: 'bad', text: '已用 ' + e + ' 分钟，超过限时 ' + r.minutes + ' 分钟' });
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
    return '<div class="iw-rail"><button class="btn" id="iw-back-mats" style="width:100%">‹ 返回情境库</button>' +
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
      '<div class="iw-req">要求：' + esc(r.scope || '—') + ' · ' + esc(r.type || '—') + ' · ' + (r.score || 0) +
      ' 分 · ' + esc(r.parts || '—') + ' · ' + esc(r.context || '—') + ' · 限时 ' + (r.minutes || '—') + ' 分钟</div>' +
      '</div></div>';
  }

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
    var d = document.getElementById('iw-dlg'), box = document.getElementById('iw-dlg-box');
    box.className = 'iw-dlg-box'; box.innerHTML = html;
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
      RULES.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul></details>' +
      '<details class="rules"><summary>原型演示开关</summary><div id="iw-switches"></div></details>';
  }

  function render() {
    var badge = document.querySelector('.proto-badge');
    if (badge) badge.textContent = '原型 · ' +
      (IW.lib.isReal() ? '本机试抽数据' : '合成数据') + ' · 不调用模型';
    var crumb = document.getElementById('iw-crumb');
    if (state.screen === 'materials') {
      crumb.textContent = '本机情境库';
      root.innerHTML = window.renderMaterials() + footerHTML();
      window.bindMaterials();
    } else if (state.screen === 'work') {
      var m = mat();
      crumb.textContent = m ? m.title + '（' + m.kind + '）' : '—';
      var form = state.step <= 4 ? window.renderForm14() : window.renderForm58();
      var banner = '';
      var rev = state.work && IW.lib.review(state.work.material);
      if (rev && state.work.dirty) {
        banner = '<div class="version-banner">该作品已评价（第 ' + rev.version +
          ' 版），现在的修改将保存为第 ' + ((state.work.version || 1) + 1) + ' 版。</div>';
      }
      root.innerHTML = '<div class="iw-grid">' + matPanelHTML() +
        '<div class="iw-form card"><header><h2>' + state.step + ' ' + esc(O.steps[state.step - 1]) +
        '</h2></header><div class="body iw-form">' + banner + form + '</div></div>' +
        railHTML() + '</div>' + footerHTML();
      if (state.step <= 4) window.bindForm14(); else window.bindForm58();
      bindRail();
    } else {
      crumb.textContent = '评价';
      root.innerHTML = window.renderEval() + footerHTML();
      window.bindEval();
    }
    if (IW.renderControls) IW.renderControls();
    updateTimer();
  }

  Object.assign(window.IW, {
    state: state, O: O, esc: esc, mat: mat, material: material, reqOf: reqOf,
    emptyWork: emptyWork, newPart: newPart, startWork: startWork, resumeWork: resumeWork,
    loadWork: loadWork, saveWork: saveWork, elapsed: elapsed, doneCount: doneCount,
    stepDone: stepDone, selfCheck: selfCheck, railHTML: railHTML,
    showDlg: showDlg, hideDlg: hideDlg, render: render, updateRail: updateRail,
  });

  bindInputs();
  render();
})();
