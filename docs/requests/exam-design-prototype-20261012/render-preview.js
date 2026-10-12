/* 命题训练原型 - ①考前预评：概要卡 + 考前提醒 */
(function () {
  'use strict';

  function weekLabel(title) {
    var m = /^(第.+?周)/.exec(title || '');
    return m ? m[1] : (title || '');
  }

  // 最近一次有回测数据的考试（数据里按时间顺序，取最后一个带 accuracy 的）
  function latestAccuracy() {
    var papers = window.ED.data.papers, acc = null;
    papers.forEach(function (p) { if (p.accuracy) acc = p; });
    return acc;
  }

  // 系统预计均分率：考试按分值加权，草稿按评分项简单平均；按当前班级口径
  function predictedMean(p, state) {
    var items = p.items.filter(function (it) { return it.pred; });
    if (!items.length) return null;
    if (p.kind === 'draft') {
      return window.ED.mean(items.map(function (it) { return window.ED.rate(it.pred); }));
    }
    var sw = 0, sp = 0;
    items.forEach(function (it) {
      var r = window.ED.rate(it.pred);
      if (r == null || it.max == null) return;
      sw += it.max; sp += r * it.max;
    });
    return sw ? sp / sw : null;
  }

  function basisText(p) {
    var b = p.basis || {};
    if (!(b.fit_sessions || []).length) return '本学期第一场考试，之前没有作答';
    return '依据：' + b.fit_sessions.join('、') + ' 的作答（' + b.fit_observations +
      ' 条记录）；' + b.students_with_history + '/' + b.students + ' 名学生有先前作答';
  }

  function accuracyCard(p) {
    var esc = window.ED.esc;
    var own = p.accuracy;
    if (!own) {
      var la = latestAccuracy();
      if (!la) return '<div class="sumcard"><h3>系统预估准不准</h3><div class="big">—</div>' +
        '<div class="sub">暂无回测数据</div></div>';
      var a = la.accuracy;
      var warn = a.system_mae != null && a.prior_mean_mae != null && a.system_mae >= a.prior_mean_mae;
      return '<div class="sumcard' + (warn ? ' warn' : '') + '"><h3>系统预估准不准</h3>' +
        '<div class="big">—</div><div class="sub">最近一次回测（' + esc(weekLabel(la.title)) + '）：' +
        '逐题平均误差 ' + Math.round(a.system_mae * 100) + ' 个百分点；直接按以往平均得分率猜为 ' +
        Math.round(a.prior_mean_mae * 100) + '；按难度档猜为 ' + Math.round(a.prior_band_mae * 100) +
        '；相关系数 ' + (a.correlation == null ? '—' : a.correlation.toFixed(2)) +
        (warn ? '。目前系统预估不比按以往平均猜更准，仅供参考' : '') + '</div></div>';
    }
    var warn2 = own.system_mae != null && own.prior_mean_mae != null && own.system_mae >= own.prior_mean_mae;
    return '<div class="sumcard' + (warn2 ? ' warn' : '') + '"><h3>系统预估准不准</h3>' +
      '<div class="big">' + Math.round(own.system_mae * 100) + ' 个百分点</div>' +
      '<div class="sub">回测逐题平均误差 ' + Math.round(own.system_mae * 100) +
      ' 个百分点；直接按以往平均得分率猜为 ' + Math.round(own.prior_mean_mae * 100) +
      '；按难度档猜为 ' + Math.round(own.prior_band_mae * 100) +
      '；相关系数 ' + (own.correlation == null ? '—' : own.correlation.toFixed(2)) +
      '（本场 ' + own.items + ' 项）' +
      (warn2 ? '。目前系统预估不比按以往平均猜更准，仅供参考' : '') + '</div></div>';
  }

  function cardsHTML(p, state) {
    var ED = window.ED, esc = ED.esc;
    var nQ = ED.groups(p).length, nI = p.items.length;

    var card1 = '<div class="sumcard"><h3>卷面</h3>' +
      '<div class="big">' + nQ + ' 道题 · ' + nI + ' 评分项</div>' +
      '<div class="sub">' + (p.full_score == null ? '未赋分（草稿）' : '满分 ' + p.full_score) +
      ' · ' + p.students + ' 名学生' +
      (p.note ? '<br>' + esc(p.note) : '') + '</div></div>';

    var pm = predictedMean(p, state);
    var card2 = '<div class="sumcard"><h3>系统预计均分率</h3>' +
      '<div class="big">' + (pm == null ? '—' : ED.pct(pm)) + '</div>' +
      '<div class="sub">' + (pm == null ? esc(p.items[0] ? p.items[0].pred_source : '') + '<br>' : '') +
      esc(basisText(p)) + '</div></div>';

    var cov = p.coverage || {};
    var missing = ((cov.detail || {}).missing || []).map(function (n) { return ED.stripPrefix(n); });
    var card3 = '<div class="sumcard"><h3>典型题覆盖</h3>' +
      '<div class="big">' + (cov.score == null ? '数据不足' : cov.score) + '</div>' +
      '<div class="sub">与试卷评价同一规则' +
      (missing.length ? '<br>更高频未选：' + esc(missing.join('、')) : '') + '</div></div>';

    return '<div class="sumcards">' + card1 + card2 + card3 + accuracyCard(p) + '</div>';
  }

  // 考前提醒：每条一行并点名题号
  function remindsHTML(p) {
    var ED = window.ED, esc = ED.esc;
    var lines = [];
    function add(cls, k, text, ids) {
      if (ids.length) lines.push('<div class="remind"><span class="rk ' + cls + '">' + esc(k) +
        '</span><span>' + esc(text) + ' <span class="ids">' + esc(ids.join('、')) + '</span></span></div>');
    }
    // 无预估：按原因分组
    var noPred = {};
    p.items.forEach(function (it) {
      if (it.pred == null) (noPred[it.pred_source] = noPred[it.pred_source] || []).push(it.id);
    });
    Object.keys(noPred).forEach(function (s) { add('na', '无法预估', s, noPred[s]); });

    add('', '可能没教到', '基础题预计得分率低于 60%：确认是否已教到',
      p.items.filter(function (it) { return it.difficulty != null && it.difficulty < 4 && it.pred && it.pred.all < 0.6; })
        .map(function (it) { return it.id; }));
    add('', '区分有限', '预计得分率高于 90%：区分作用可能有限',
      p.items.filter(function (it) { return it.pred && it.pred.all > 0.9; }).map(function (it) { return it.id; }));
    if (p.kind === 'draft') {
      add('info', '间接预估', '本班还没直接做过这类题，预估只靠相近知识与整体水平，较粗',
        p.items.filter(function (it) { return it.has_direct_evidence === false; }).map(function (it) { return it.id; }));
    }
    var fixIds = [], seenIds = [];
    p.items.forEach(function (it) {
      (it.history || []).forEach(function (h) {
        if (h.mark === 'fix' && fixIds.indexOf(it.id) < 0) fixIds.push(it.id);
        seenIds.push(it.id + '（' + (h.title || '') + ' ' + ED.pct(h.rate) + '）');
      });
    });
    add('', '曾标“改”', '以前考过并被标为“改”', fixIds);
    if (seenIds.length) {
      lines.push('<div class="remind"><span class="rk info">考过</span><span>以前考过 <span class="ids">' +
        esc(seenIds.join('、')) + '</span></span></div>');
    }
    return '<div class="card"><header><h2>考前提醒</h2>' +
      '<span class="hint">按系统预估与本班过往作答自动检查</span></header>' +
      (lines.length ? '<div class="remind-list">' + lines.join('') + '</div>'
        : '<div class="remind-none">暂无提醒</div>') + '</div>';
  }

  window.renderPreview = function (p, state) {
    return cardsHTML(p, state) + remindsHTML(p);
  };
})();
