/* 试卷评价原型 - 评分总览（雷达图 + 总分 + 六维卡） */
(function () {
  'use strict';

  var VW = 340, VH = 268, CX = 170, CY = 132, R = 80, LR = 1.18;

  function ang(i, n) { return -Math.PI / 2 + i * 2 * Math.PI / n; } // n 轴，从正上方顺时针
  function pt(i, frac, n) {
    var a = ang(i, n);
    return [CX + R * frac * Math.cos(a), CY + R * frac * Math.sin(a)];
  }
  function f(v) { return v.toFixed(1); }

  function radarSVG(dims, openKey) {
    var esc = window.PR.esc;
    var p = [];
    var N = dims.length;
    // 环：20/40/60/80/100 五圈
    [20, 40, 60, 80, 100].forEach(function (ring) {
      var pts = [];
      for (var i = 0; i < N; i++) { var q = pt(i, ring / 100, N); pts.push(f(q[0]) + ',' + f(q[1])); }
      p.push('<polygon points="' + pts.join(' ') + '" fill="none" stroke="' +
        (ring === 100 ? '#c3ccc8' : '#e3e7e4') + '" stroke-width="1"/>');
    });
    // 轴与轴标签
    var pts2 = [];
    dims.forEach(function (d, i) {
      var na = d.score == null;
      var end = pt(i, 1, N);
      p.push('<line x1="' + CX + '" y1="' + CY + '" x2="' + f(end[0]) + '" y2="' + f(end[1]) +
        '" stroke="' + (na ? '#d1d9d5' : '#c3ccc8') + '"' + (na ? ' stroke-dasharray="4 4"' : '') + '/>');
      if (!na) pts2.push(f(pt(i, d.score / 100, N)[0]) + ',' + f(pt(i, d.score / 100, N)[1]));
      var lp = pt(i, LR, N);
      var anch = lp[0] < CX - 8 ? 'end' : lp[0] > CX + 8 ? 'start' : 'middle';
      var dy = lp[1] < CY - 55 ? -2 : lp[1] > CY + 55 ? 13 : 4;
      p.push('<g class="axis-lbl" data-dim="' + d.key + '">' +
        '<text x="' + f(lp[0]) + '" y="' + f(lp[1] + dy) + '" text-anchor="' + anch +
        '" font-size="12" fill="' + (na ? '#98a2a0' : openKey === d.key ? '#176b75' : '#59656b') +
        '"' + (openKey === d.key ? ' font-weight="700"' : '') + '>' + esc(d.name) + '</text>' +
        '<text x="' + f(lp[0]) + '" y="' + f(lp[1] + dy + 16) + '" text-anchor="' + anch +
        '" font-size="14" font-weight="700" fill="' + (na ? '#98a2a0' : '#202a30') + '">' +
        (na ? '数据不足' : d.score) + '</text></g>');
    });
    // 得分多边形（跳过 null，相邻直连）
    if (pts2.length > 1) {
      p.push('<polygon points="' + pts2.join(' ') + '" fill="rgba(23,107,117,.18)" stroke="#176b75" stroke-width="1.6"/>');
    }
    dims.forEach(function (d, i) {
      if (d.score == null) return;
      var v = pt(i, d.score / 100, N);
      p.push('<circle cx="' + f(v[0]) + '" cy="' + f(v[1]) + '" r="3" fill="#176b75"/>');
    });
    return '<svg viewBox="0 0 ' + VW + ' ' + VH + '" class="radar-svg" style="width:100%;height:auto">' + p.join('') + '</svg>';
  }

  function dimLine(d) {
    var PR = window.PR, dt = d.detail || {};
    switch (d.key) {
      case 'coverage':
        if (dt.missing && dt.missing.length) {
          var m0 = PR.stripPrefix(dt.missing[0]);
          return '漏选更高频：' + m0 + (dt.missing.length > 1 ? '等' + dt.missing.length + '项' : '');
        }
        return '已选的都是高频题型';
      case 'time_fit':
        return '中档生 ' + dt.unfinished + '/' + dt.mid_students + ' 人疑似没做完';
      case 'distribution':
        return '均分 ' + Math.round(dt.mean * 100) + '% · 40%以下占 ' + Math.round(dt.floor_share * 100) + '%';
      case 'basic_hits':
        return dt.hit_items.length + '/' + dt.basic_items.length + ' 道基础题考出了问题';
      case 'discrimination':
        return dt.low.length ? dt.low.length + ' 题区分偏弱：' + dt.low.join('、') : '每题都能分出强弱';
    }
    return '';
  }

  function band(score) {
    if (score == null) return 'band-na';
    return score >= 80 ? 'band-good' : score >= 60 ? 'band-amber' : 'band-red';
  }
  function grade(total) {
    return total >= 85 ? ['优秀', 'g-good'] : total >= 70 ? ['良好', 'g-ok'] :
      total >= 60 ? ['合格', 'g-pass'] : ['需改进', 'g-bad'];
  }

  window.renderHero = function (exam, state) {
    var PR = window.PR, esc = PR.esc;
    var ev = exam.evaluation, dims = ev.dimensions;
    var na = dims.filter(function (d) { return d.score == null; }).length;
    var known = dims.filter(function (d) { return d.score != null; });
    var total = na ? Math.round(known.reduce(function (s, d) { return s + d.score * d.weight; }, 0) /
      known.reduce(function (s, d) { return s + d.weight; }, 0)) : ev.total;
    var g = grade(total);
    var best = known.reduce(function (a, d) { return d.score > a.score ? d : a; }, known[0]);
    var worst = known.reduce(function (a, d) { return d.score < a.score ? d : a; }, known[0]);

    var cards = dims.map(function (d) {
      var b = band(d.score);
      var na2 = d.score == null;
      return '<div class="dim-card' + (state.openDim === d.key ? ' on' : '') + '" data-dim="' + d.key + '">' +
        '<div class="dim-head"><span class="dim-name">' + esc(d.name) + '</span>' +
        '<span class="dim-w">权重 ' + d.weight + '%</span></div>' +
        '<div class="dim-score ' + b + (na2 ? ' na' : '') + '">' + (na2 ? '数据不足' : d.score) + '</div>' +
        '<div class="dim-bar ' + b + '"><i style="width:' + (na2 ? 0 : d.score) + '%"></i></div>' +
        '<div class="dim-line" title="' + (na2 ? '' : esc(dimLine(d))) + '">' + (na2 ? '' : esc(dimLine(d))) + '</div></div>';
    }).join('');

    return '<section class="hero card">' +
      '<div class="hero-radar">' + radarSVG(dims, state.openDim) +
        (state.cls !== 'all' ? '<p class="hint" style="text-align:center;margin:0">雷达按全场计算</p>' : '') + '</div>' +
      '<div class="hero-total"><div class="total-num">' + total + '<small>/100</small></div>' +
        '<div class="total-cap">加权平均分</div>' +
        '<span class="grade-chip ' + g[1] + '">' + g[0] + '</span>' +
        '<div class="bw-line">最强 <span class="bw-chip ' + band(best.score) + '">' + esc(best.name) + ' ' + best.score + '</span></div>' +
        '<div class="bw-line">最需关注 <span class="bw-chip ' + band(worst.score) + '">' + esc(worst.name) + ' ' + worst.score + '</span></div>' +
        '<div class="total-sub">' + exam.students + ' 名学生 · ' + exam.items.length + ' 个评分项</div>' +
        (na ? '<div class="na-note">' + na + ' 项数据不足，未计入</div>' : '') + '</div>' +
      '<div class="dim-grid">' + cards + '</div></section>' +
      '<div id="dim-panel-slot">' + window.renderDimPanel(exam, state.openDim) + '</div>';
  };

  window.bindHero = function (exam, state, render) {
    document.querySelectorAll('[data-dim]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var k = el.dataset.dim;
        state.openDim = state.openDim === k ? null : k;
        render();
        var p = document.querySelector('.dim-panel');
        if (p) p.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
    });
    var x = document.querySelector('.dim-panel .close-x');
    if (x) x.addEventListener('click', function () { state.openDim = null; render(); });
    window.bindDimPanel(exam, state, render);
  };
})();
