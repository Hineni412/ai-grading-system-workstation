/* 命题训练原型 - ③考后对照：误差概要、散点对照、最偏3题、命题能力画像 */
(function () {
  'use strict';

  var QUAD = {
    good: ['基础能区分', 'q-good'], free: ['送分', 'q-free'],
    high: ['有难度·能区分', 'q-high'], suspect: ['可疑', 'q-suspect'],
  };

  function mae(pairs) {
    var xs = pairs.filter(function (x) { return x[0] != null && x[1] != null; })
      .map(function (x) { return Math.abs(x[0] - x[1]); });
    return window.ED.mean(xs);
  }
  function bias(pairs) {
    var xs = pairs.filter(function (x) { return x[0] != null && x[1] != null; })
      .map(function (x) { return x[0] - x[1]; });
    return window.ED.mean(xs);
  }
  function biasText(v) {
    if (v == null) return '—';
    var n = Math.round(v * 100);
    if (n === 0) return '基本持平';
    return '平均估' + (n > 0 ? '高 ' : '低 ') + Math.abs(n) + ' 个百分点';
  }

  // 对照集合：填了我的预测且有系统预估的项；没填则用全部有系统预估的项
  function compItems(p, preds) {
    var mine = p.items.filter(function (it) { return preds[it.id] != null; });
    var both = mine.filter(function (it) { return it.pred; });
    return { mine: mine, sys: both.length ? both : p.items.filter(function (it) { return it.pred; }) };
  }

  function cardsHTML(p, preds) {
    var ED = window.ED;
    var c = compItems(p, preds);
    var myErr = mae(c.mine.map(function (it) { return [preds[it.id] / 100, it.actual && it.actual.all]; }));
    var sysErr = mae(c.sys.map(function (it) { return [it.pred && it.pred.all, it.actual && it.actual.all]; }));
    var sysScope = c.mine.length ? '与我的预测同口径 ' + c.sys.length + ' 项' : '全部 ' + c.sys.length + ' 项';

    var bandRows = ['basic', 'mid', 'hard'].map(function (b) {
      var myB = bias(c.mine.filter(function (it) { return ED.band(it.difficulty) === b; })
        .map(function (it) { return [preds[it.id] / 100, it.actual && it.actual.all]; }));
      var sysB = bias(c.sys.filter(function (it) { return ED.band(it.difficulty) === b; })
        .map(function (it) { return [it.pred && it.pred.all, it.actual && it.actual.all]; }));
      if (myB == null && sysB == null) return '';
      return '<div>' + ED.BAND_NAME[b] + '题：我 ' + biasText(myB) + ' · 系统 ' + biasText(sysB) + '</div>';
    }).join('');

    return '<div class="sumcards">' +
      '<div class="sumcard"><h3>我的平均误差</h3><div class="big">' +
        (myErr == null ? '—' : Math.round(myErr * 100) + ' 个百分点') + '</div>' +
        '<div class="sub">|我的预测 − 实际|，已填 ' + c.mine.length + ' 项</div></div>' +
      '<div class="sumcard"><h3>系统平均误差</h3><div class="big">' +
        (sysErr == null ? '—' : Math.round(sysErr * 100) + ' 个百分点') + '</div>' +
        '<div class="sub">|系统预估 − 实际|，' + sysScope + '</div></div>' +
      '<div class="sumcard" style="grid-column:span 2"><h3>按难度档偏差（估计 − 实际）</h3>' +
        '<div class="sub" style="font-size:13px;color:var(--text-2)">' + (bandRows || '—') + '</div></div>' +
      '</div>';
  }

  function plotHTML(p, preds) {
    var ED = window.ED, esc = ED.esc;
    var items = p.items;
    var rowH = 26, top = 30, bot = 8, left = 76, w = 920, pw = w - left - 24;
    var h = top + items.length * rowH + bot;
    var x = function (v) { return left + v / 100 * pw; };
    var s = ['<div class="plot-wrap"><svg class="dotplot" viewBox="0 0 ' + w + ' ' + h +
      '" width="100%" role="img" aria-label="逐题预测对照">'];
    [0, 25, 50, 75, 100].forEach(function (t) {
      s.push('<line class="grid" x1="' + x(t) + '" y1="' + top + '" x2="' + x(t) + '" y2="' + (h - bot) + '"/>');
      s.push('<text x="' + x(t) + '" y="' + (top - 8) + '" text-anchor="middle" font-size="11" fill="#657176">' + t + '%</text>');
    });
    items.forEach(function (it, i) {
      var y = top + i * rowH + rowH / 2;
      if (i % 2) s.push('<rect class="row-alt" x="0" y="' + (y - rowH / 2) + '" width="' + w + '" height="' + rowH + '"/>');
      s.push('<text x="' + (left - 8) + '" y="' + (y + 4) + '" text-anchor="end" font-size="12" fill="#59656b">' + esc(it.id) + '</text>');
      var a = it.actual && it.actual.all != null ? it.actual.all * 100 : null;
      var m = preds[it.id] != null ? preds[it.id] : null;
      var sy = it.pred && it.pred.all != null ? it.pred.all * 100 : null;
      if (m != null && a != null) {
        s.push('<line class="conn' + (Math.abs(m - a) >= 15 ? ' bad' : '') +
          '" x1="' + x(m) + '" y1="' + y + '" x2="' + x(a) + '" y2="' + y + '"/>');
      }
      if (a != null) s.push('<circle class="m-actual" cx="' + x(a) + '" cy="' + y + '" r="4"><title>' +
        esc(it.id) + ' 实际 ' + Math.round(a) + '%</title></circle>');
      if (m != null) s.push('<circle class="m-mine" cx="' + x(m) + '" cy="' + y + '" r="5"><title>' +
        esc(it.id) + ' 我的 ' + Math.round(m) + '%</title></circle>');
      if (sy != null) s.push('<path class="m-sys" d="M ' + x(sy) + ' ' + (y - 5) + ' L ' + (x(sy) + 5) +
        ' ' + y + ' L ' + x(sy) + ' ' + (y + 5) + ' L ' + (x(sy) - 5) + ' ' + y + ' Z"><title>' +
        esc(it.id) + ' 系统 ' + Math.round(sy) + '%</title></path>');
    });
    s.push('</svg></div>');
    return '<div class="card"><header><h2>逐题对照</h2>' +
      '<span class="hint">连线变红表示我的预测与实际差 ≥15 个百分点</span></header>' +
      '<div class="plot-legend"><span><i class="lg actual"></i>实际</span>' +
      '<span><i class="lg mine"></i>我的</span><span><i class="lg sys"></i>系统</span></div>' +
      s.join('') + '</div>';
  }

  function top3HTML(p, preds) {
    var ED = window.ED, esc = ED.esc;
    var useMine = Object.keys(preds).length > 0;
    var rows = p.items.filter(function (it) { return it.actual && it.actual.all != null; })
      .map(function (it) {
        var est = useMine && preds[it.id] != null ? preds[it.id] / 100 : (it.pred ? it.pred.all : null);
        return { it: it, est: est, d: est == null ? null : Math.abs(est - it.actual.all) };
      }).filter(function (r) { return r.d != null; })
      .sort(function (a, b) { return b.d - a.d; }).slice(0, 3);
    var list = rows.map(function (r) {
      var q = QUAD[r.it.quadrant] || [r.it.quadrant || '—', ''];
      return '<div class="top3-item"><div><b class="mono">' + esc(r.it.id) + '</b>' +
        ' <span class="chip ' + q[1] + '">' + esc(q[0]) + '</span>' +
        ' <a href="../paper-review-prototype-20261011/index.html">到试卷评价看详情</a></div>' +
        '<div class="nums"><span>我的 <b>' + (preds[r.it.id] != null ? Math.round(preds[r.it.id]) + '%' : '—') + '</b></span>' +
        '<span>系统 <b>' + ED.pct(r.it.pred && r.it.pred.all) + '</b></span>' +
        '<span>实际 <b>' + ED.pct(r.it.actual.all) + '</b></span></div>' +
        '<div class="stem-snip">' + esc((r.it.stem || '').slice(0, 60)) + '</div></div>';
    }).join('');
    return '<div class="card"><header><h2>估得最偏的 3 题</h2>' +
      '<span class="hint">' + (useMine ? '按 |我的 − 实际| 排序' : '没有你的预测，按 |系统 − 实际| 排序') + '</span></header>' +
      '<div class="top3">' + (list || '<div class="watch-empty">没有可对照的题</div>') + '</div></div>';
  }

  function profileHTML() {
    var ED = window.ED, esc = ED.esc;
    var rows = [];
    ED.data.papers.forEach(function (p) {
      if (p.kind !== 'session') return;
      var rec = ED.loadRecord(p.id);
      if (!rec || !rec.predictions) return;
      var preds = rec.predictions;
      var mine = p.items.filter(function (it) { return preds[it.id] != null; });
      var sys = mine.filter(function (it) { return it.pred; });
      var myErr = mae(mine.map(function (it) { return [preds[it.id] / 100, it.actual && it.actual.all]; }));
      var sysErr = mae(sys.map(function (it) { return [it.pred.all, it.actual && it.actual.all]; }));
      var b = bias(mine.filter(function (it) { return ED.band(it.difficulty) === 'basic'; })
        .map(function (it) { return [preds[it.id] / 100, it.actual && it.actual.all]; }));
      rows.push('<tr><td>' + esc(p.title) + '</td><td>' + mine.length + ' / ' + p.items.length + '</td>' +
        '<td>' + (myErr == null ? '—' : Math.round(myErr * 100) + ' 个百分点') + '</td>' +
        '<td>' + (sysErr == null ? '—' : Math.round(sysErr * 100) + ' 个百分点') + '</td>' +
        '<td>' + (b == null ? '—' : '基础题' + biasText(b)) + '</td></tr>');
    });
    return '<div class="card"><header><h2>命题能力画像</h2>' +
      '<span class="hint">跨考试汇总本浏览器保存的命题记录</span></header>' +
      '<div class="body" style="padding:0"><table class="items"><thead><tr>' +
      '<th>考试</th><th>填了几项</th><th>我的误差</th><th>系统误差</th><th>基础题偏差</th></tr></thead>' +
      '<tbody>' + (rows.join('') || '<tr><td colspan="5" class="watch-empty">还没有已保存的命题记录</td></tr>') +
      '</tbody></table></div>' +
      (rows.length < 2 ? '<p class="muted-line">至少两场有预测后再看趋势</p>' : '') + '</div>';
  }

  window.renderCompare = function (p) {
    var ED = window.ED;
    var rec = ED.loadRecord(p.id);
    var preds = (rec && rec.predictions) || {};
    var banner = rec ? '' :
      '<p class="banner">还没有你的预测。可回到②填写；下面先显示系统预估与实际。</p>';
    return banner + cardsHTML(p, preds) + plotHTML(p, preds) + top3HTML(p, preds) + profileHTML();
  };
})();
