/* 命题训练原型 - 作答表单 步骤 1–4 */
(function () {
  'use strict';

  function chips(f, vals, cur, multi) {
    var esc = window.IW.esc;
    return '<div class="iw-chips">' + vals.map(function (v) {
      var on = multi ? cur.indexOf(v) >= 0 : cur === v;
      return '<button type="button" class="chip-filter' + (on ? ' on' : '') + '" data-f="' + f +
        '" data-v="' + esc(v) + '"' + (multi ? ' data-multi="1"' : '') + '>' + esc(v) + '</button>';
    }).join('') + '</div>';
  }
  function nav(prev, next) {
    return '<div class="iw-nav">' +
      (prev ? '<button class="btn" id="iw-prev">上一步</button>' : '<span></span>') +
      '<button class="btn primary" id="iw-next">' + (next ? '下一步' : '去评价') + '</button></div>';
  }

  function step1() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work, m = IW.mat();
    var sents = m.sentences.map(function (s, i) {
      var on = w.picked.indexOf(i) >= 0;
      return '<button type="button" class="schip' + (on ? ' on' : '') + '" data-sent="' + i + '">' +
        '<span class="tag">' + (on ? '✓ 可用信息' : '点击标记') + '</span>' + esc(s) + '</button>';
    }).join('');
    var cat = window.ITEM_CATALOG.map(function (c) {
      return '<div class="iw-cat"><div class="ch">' + esc(c.chapter) + '</div>' +
        c.sections.map(function (s) {
          return '<label><input type="checkbox" data-b="sections" value="' + esc(s) + '"' +
            (w.sections.indexOf(s) >= 0 ? ' checked' : '') + '>' + esc(s) + '</label>';
        }).join('') + '</div>';
    }).join('');
    return '<div class="iw-f"><label>点选材料中可用的数学信息</label>' +
      '<div class="iw-sents">' + sents + '</div></div>' +
      '<div class="iw-f"><label>关联章节与知识点 <span class="sub">（教材目录，正式版计时期间只显示它与课标要点）</span></label>' + cat + '</div>' +
      '<div class="iw-f"><label>核心想法</label><textarea data-b="coreIdea">' + esc(w.coreIdea) + '</textarea></div>' +
      nav(false, true);
  }

  function step2() {
    var w = window.IW.state.work;
    return '<div class="iw-f"><label>考查意图</label><textarea data-b="intent">' +
      window.IW.esc(w.intent) + '</textarea></div>' +
      '<div class="iw-f"><label>核心素养 <span class="sub">（课标初中阶段九项素养表现，多选）</span></label>' +
      chips('competencies', window.IW.O.competencies, w.competencies, true) + '</div>' +
      '<div class="iw-f"><label>学业要求层次</label>' +
      chips('requirement', window.IW.O.requirement, w.requirement, false) + '</div>' + nav(true, true);
  }

  function step3() {
    var w = window.IW.state.work, O = window.IW.O;
    return '<div class="iw-f"><label>情境类别</label>' +
      chips('contextKind', O.contextKinds, w.contextKind, false) + '</div>' +
      '<div class="iw-f"><label>材料运用方式</label>' +
      chips('materialUse', O.materialUse, w.materialUse, false) + '</div>' +
      '<div class="iw-f"><label>改编方法（母题必选，其余可选）</label>' +
      chips('adaptMethods', O.adaptMethods, w.adaptMethods, true) + '</div>' +
      '<div class="iw-f"><label>真实性说明</label><textarea data-b="realism">' +
      window.IW.esc(w.realism) + '</textarea></div>' + nav(true, true);
  }

  function step4() {
    var IW = window.IW, esc = IW.esc, w = IW.state.work, m = IW.mat();
    var parts = w.parts.map(function (p, i) {
      var soloOpts = IW.O.solo.map(function (s) {
        return '<option' + (p.solo === s ? ' selected' : '') + '>' + esc(s) + '</option>';
      }).join('');
      return '<div class="iw-part"><div class="hd">第（' + (i + 1) + '）问' +
        (w.parts.length > 1 ? '<button class="btn" data-rm-part="' + i + '">删除本问</button>' : '') +
        '</div>' +
        '<div class="iw-f"><label>设问</label><textarea data-p="' + i + '" data-b="ask">' + esc(p.ask) + '</textarea></div>' +
        '<div class="row"><span>目标 SOLO</span><select data-p="' + i + '" data-b="solo">' +
        '<option value="">—</option>' + soloOpts + '</select>' +
        '<span>分值</span><input type="number" min="0" data-p="' + i + '" data-b="score" value="' +
        (p.score == null ? '' : p.score) + '"></div></div>';
    }).join('');
    var sum = w.parts.reduce(function (a, p) { return a + (Number(p.score) || 0); }, 0);
    var ok = sum === m.requirement.score;
    return '<div class="iw-f"><label>题型</label>' +
      chips('questionType', IW.O.questionTypes, w.questionType, false) + '</div>' +
      '<div class="iw-f"><label>开放性</label>' +
      chips('openness', IW.O.openness, w.openness, false) + '</div>' +
      '<div class="iw-f"><label>小问列表</label>' + parts +
      '<div><button class="btn" id="iw-add-part">+ 增加一小问</button>' +
      '<span class="sum-line ' + (ok ? 'ok' : 'bad') + '" id="iw-psum" style="margin-left:12px">小问分值合计 ' +
      sum + ' / 要求 ' + m.requirement.score + ' 分</span></div></div>' + nav(true, true);
  }

  window.renderForm14 = function () {
    return [step1, step2, step3, step4][window.IW.state.step - 1]();
  };

  // 小问分值合计 / 评分步骤合计的实时刷新（不重渲染，保焦点）
  window.updateSums = function () {
    var IW = window.IW, w = IW.state.work, m = IW.mat();
    if (!w || !m) return;
    var ps = document.getElementById('iw-psum');
    if (ps) {
      var sum = w.parts.reduce(function (a, p) { return a + (Number(p.score) || 0); }, 0);
      var ok = sum === m.requirement.score;
      ps.textContent = '小问分值合计 ' + sum + ' / 要求 ' + m.requirement.score + ' 分';
      ps.className = 'sum-line ' + (ok ? 'ok' : 'bad');
    }
    document.querySelectorAll('.iw-ssum').forEach(function (el) {
      var p = w.parts[Number(el.dataset.i)];
      if (!p) return;
      var ss = p.steps.reduce(function (a, s) { return a + (Number(s[1]) || 0); }, 0);
      var ok2 = ss === Number(p.score);
      el.textContent = '步骤合计 ' + ss + ' / 该问 ' + (p.score == null ? '—' : p.score) + ' 分';
      el.className = 'sum-line iw-ssum ' + (ok2 ? 'ok' : 'bad');
    });
  };

  window.bindForm14 = function () {
    var IW = window.IW, w = IW.state.work;
    document.querySelectorAll('.schip').forEach(function (b) {
      b.addEventListener('click', function () {
        var i = Number(b.dataset.sent), k = w.picked.indexOf(i);
        if (k >= 0) w.picked.splice(k, 1); else w.picked.push(i);
        b.classList.toggle('on');
        b.querySelector('.tag').textContent = k >= 0 ? '点击标记' : '✓ 可用信息';
        IW.saveWork(); IW.updateRail();
      });
    });
    document.querySelectorAll('.chip-filter[data-f]').forEach(function (b) {
      b.addEventListener('click', function () {
        var f = b.dataset.f, v = b.dataset.v;
        if (b.dataset.multi) {
          var i = w[f].indexOf(v);
          if (i >= 0) w[f].splice(i, 1); else w[f].push(v);
          b.classList.toggle('on');
        } else {
          w[f] = v;
          b.parentNode.querySelectorAll('.chip-filter').forEach(function (x) { x.classList.remove('on'); });
          b.classList.add('on');
        }
        IW.saveWork(); IW.updateRail();
      });
    });
    var add = document.getElementById('iw-add-part');
    if (add) add.addEventListener('click', function () {
      w.parts.push(IW.newPart()); IW.saveWork(); IW.render();
    });
    document.querySelectorAll('[data-rm-part]').forEach(function (b) {
      b.addEventListener('click', function () {
        w.parts.splice(Number(b.dataset.rmPart), 1); IW.saveWork(); IW.render();
      });
    });
    var prev = document.getElementById('iw-prev');
    if (prev) prev.addEventListener('click', function () { IW.state.step--; IW.render(); });
    var next = document.getElementById('iw-next');
    if (next) next.addEventListener('click', function () {
      if (IW.state.step < 8) { IW.state.step++; IW.render(); }
      else { IW.state.screen = 'eval'; IW.render(); }
    });
  };
})();
