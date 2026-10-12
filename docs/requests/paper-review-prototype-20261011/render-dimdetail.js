/* 试卷评价原型 - 六维详情面板（点击维度卡展开） */
(function () {
  'use strict';

  function pct(v) { return Math.round(v * 100) + '%'; }

  var PANELS = {
    coverage: function (d) {
      var PR = window.PR, esc = PR.esc;
      var rows = (d.detail.sections || []).map(function (s) {
        var skipped = (s.types || []).filter(function (t) { return t.skipped; })
          .map(function (t) { return '<span class="chip skip-chip">' + esc(PR.stripPrefix(t.name)) + '</span>'; }).join('');
        return '<div class="dp-covrow"><span class="sec-nm">' + esc(s.section) + '</span>' +
          '<span class="ratio-bar" title="选题效率 ' + pct(s.ratio) + '"><i style="width:' +
          (s.ratio * 100) + '%"></i></span>' +
          '<b class="ratio-pct">' + pct(s.ratio) + '</b>' +
          '<span class="sec-chips">' + skipped + '</span></div>';
      }).join('');
      return rows + '<a class="dp-link" href="#coverage-bottom">查看全部题型 ↓</a>';
    },

    time_fit: function (d) {
      var dt = d.detail, esc = window.PR.esc;
      var w1 = 5 / 45 * 100, w2 = 35 / 45 * 100; // 标尺 0-45%：0-5 绿 / 5-40 黄 / >40 红
      var pos = Math.min(dt.share, 0.45) / 0.45 * 100;
      return '<div class="zgauge">' +
        '<div class="zone z1" style="width:' + w1 + '%"></div>' +
        '<div class="zone z2" style="left:' + w1 + '%;width:' + w2 + '%"></div>' +
        '<div class="zone z3" style="left:' + (w1 + w2) + '%;width:' + (100 - w1 - w2) + '%"></div>' +
        '<span class="zlbl" style="left:' + w1 + '%">5%</span>' +
        '<span class="zlbl" style="left:' + (w1 + w2) + '%">40%</span>' +
        '<div class="ptr" style="left:' + pos + '%"><b></b><span>' + pct(dt.share) + '</span></div>' +
        '</div>' +
        '<p class="dp-line">最后一题 ' + esc(dt.last_item) + '：' + dt.last_item_blank_mid + ' 名中档生空白</p>' +
        '<p class="dp-line mut">空白也可能因为不会做，不全是时间不够。</p>';
    },

    distribution: function (d) {
      var dt = d.detail, esc = window.PR.esc;
      var W = 560, H = 150, PLOT = H - 22, max = Math.max.apply(null, dt.bins.concat([1]));
      var bw = W / 10, p = [];
      // 60-80% 目标带
      p.push('<rect x="' + (6 * bw) + '" y="14" width="' + (2 * bw) + '" height="' + (PLOT - 14) + '" fill="rgba(47,125,92,.08)"/>');
      p.push('<text x="' + (8 * bw - 4) + '" y="9" font-size="10" fill="#2f7d5c" text-anchor="end">均分目标 60–80%</text>');
      dt.bins.forEach(function (n, i) {
        var h = (PLOT - 16) * n / max;
        var x = i * bw + 8, w = bw - 16;
        var fill = i < 4 ? '#d08a76' : '#176b75';
        p.push('<rect x="' + x + '" y="' + (PLOT - h) + '" width="' + w + '" height="' + h + '" rx="3" fill="' + fill + '"/>');
        p.push('<text x="' + (i * bw + bw / 2) + '" y="' + (H - 6) + '" font-size="10" fill="#657176" text-anchor="middle">' + (i * 10) + '</text>');
        if (n) p.push('<text x="' + (i * bw + bw / 2) + '" y="' + (PLOT - h - 4) + '" font-size="10" fill="#59656b" text-anchor="middle">' + n + '</text>');
      });
      var mx = dt.mean * 10 * bw;
      p.push('<line x1="' + mx + '" y1="14" x2="' + mx + '" y2="' + PLOT + '" stroke="#202a30" stroke-width="1.5" stroke-dasharray="4 3"/>');
      p.push('<text x="' + mx + '" y="9" font-size="10" font-weight="600" fill="#202a30" text-anchor="' +
        (mx > W - 60 ? 'end' : mx < 60 ? 'start' : 'middle') + '">均分 ' + pct(dt.mean) + '</text>');
      var parts = dt.parts;
      function mg(name, v) {
        var b = v >= 80 ? 'band-good' : v >= 60 ? 'band-amber' : 'band-red';
        return '<div class="mg"><div class="mg-name"><span>' + name + '</span><b class="' + b + '">' + v + '</b></div>' +
          '<div class="mg-bar ' + b + '"><i style="width:' + v + '%"></i></div></div>';
      }
      return '<div style="max-width:640px"><svg class="hist-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">' + p.join('') + '</svg></div>' +
        '<p class="dp-line mut">红色 = 低于 40% · 阴影 = 均分目标区间 · x 轴为得分率%</p>' +
        '<div class="mini-gauges">' + mg('均分', parts.mean) + mg('拉开差距', parts.spread) + mg('两端人数', parts.extremes) + '</div>';
    },

    basic_hits: function (d) {
      var dt = d.detail, esc = window.PR.esc;
      function grp(ids, cls, name) {
        if (!ids.length) return '';
        return '<div class="dp-group"><p>' + name + '（' + ids.length + '）</p><div class="idchips">' +
          ids.map(function (id) { return '<span class="idchip2 ' + cls + '" data-item="' + esc(id) + '">' + esc(id) + '</span>'; }).join('') +
          '</div></div>';
      }
      return grp(dt.hit_items, 'hit', '考出问题') +
        grp(dt.too_easy, 'easy', '太容易 >85%') +
        grp(dt.weak, 'weak', '没分出强弱');
    },

    discrimination: function (d) {
      var esc = window.PR.esc, low = d.detail.low;
      if (!low.length) return '<p class="dp-line">✓ 无</p>';
      return '<div class="idchips">' + low.map(function (id) {
        return '<span class="idchip2 weak" data-item="' + esc(id) + '">' + esc(id) + '</span>';
      }).join('') + '</div>';
    },
  };

  window.renderDimPanel = function (exam, key) {
    if (!key) return '';
    var dim = exam.evaluation.dimensions.find(function (d) { return d.key === key; });
    if (!dim) return '';
    var body = dim.score == null
      ? '<p class="dp-line mut">该维度数据不足，未计入总分。</p>'
      : (PANELS[key] ? PANELS[key](dim) : '');
    return '<div class="dim-panel"><button class="close-x" title="关闭">✕</button>' +
      '<h3>' + window.PR.esc(dim.name) + ' · ' + (dim.score == null ? '数据不足' : dim.score + ' 分') + '</h3>' +
      body + '</div>';
  };

  window.bindDimPanel = function (exam, state, render) {
    // 题号 chips → 定位到表格行
    document.querySelectorAll('.dim-panel .idchip2[data-item]').forEach(function (c) {
      c.addEventListener('click', function (ev) {
        ev.stopPropagation();
        window.PR.goToItem(c.dataset.item);
      });
    });
  };
})();
