/* 试卷评价原型 - 难度与区分度散点图（纯 SVG + 右侧图例） */
(function () {
  'use strict';

  var COLORS = { good: '#2f7d5c', free: '#9aa3a0', high: '#496579', suspect: '#b3402a' };
  var LEGEND = [
    ['good', '基础能区分', '多数人会，没掌握的人被筛出来'],
    ['free', '送分', '几乎都对，看不出差别'],
    ['high', '有难度·能区分', '做对的多是成绩好的学生'],
    ['suspect', '可疑', '较难，且好生差生都容易错；查题面或批改'],
  ];

  window.renderScatter = function (exam, rows) {
    var esc = window.PR.esc;
    var W = 900, H = 340;
    var ML = 48, MR = 16, MT = 14, MB = 40;
    var X0 = ML, X1 = W - MR, Y0 = MT, Y1 = H - MB;
    function sx(v) { return X0 + v * (X1 - X0); }
    function sy(v) { return Y1 - v * (Y1 - Y0); }
    var xT = sx(0.7), yT = sy(0.4);

    var p = [];
    p.push('<rect x="' + xT + '" y="' + Y0 + '" width="' + (X1 - xT) + '" height="' + (yT - Y0) + '" fill="rgba(47,125,92,.05)"/>');
    p.push('<rect x="' + X0 + '" y="' + yT + '" width="' + (xT - X0) + '" height="' + (Y1 - yT) + '" fill="rgba(179,64,42,.05)"/>');
    p.push('<line x1="' + xT + '" y1="' + Y0 + '" x2="' + xT + '" y2="' + Y1 + '" stroke="#c3ccc8" stroke-dasharray="5 4"/>');
    p.push('<line x1="' + X0 + '" y1="' + yT + '" x2="' + X1 + '" y2="' + yT + '" stroke="#c3ccc8" stroke-dasharray="5 4"/>');
    p.push('<text x="' + (xT + 4) + '" y="' + (Y0 + 12) + '" font-size="10" fill="#98a2a0">0.7</text>');
    p.push('<text x="' + (X0 + 4) + '" y="' + (yT - 4) + '" font-size="10" fill="#98a2a0">0.4</text>');
    p.push('<line x1="' + X0 + '" y1="' + Y1 + '" x2="' + X1 + '" y2="' + Y1 + '" stroke="#d1d9d5"/>');
    p.push('<line x1="' + X0 + '" y1="' + Y0 + '" x2="' + X0 + '" y2="' + Y1 + '" stroke="#d1d9d5"/>');
    [0, 0.2, 0.4, 0.6, 0.8, 1].forEach(function (t) {
      p.push('<text x="' + sx(t) + '" y="' + (Y1 + 15) + '" font-size="10" fill="#657176" text-anchor="middle">' + t.toFixed(1) + '</text>');
      p.push('<text x="' + (X0 - 6) + '" y="' + (sy(t) + 3) + '" font-size="10" fill="#657176" text-anchor="end">' + t.toFixed(1) + '</text>');
    });
    p.push('<text x="' + ((X0 + X1) / 2) + '" y="' + (H - 4) + '" font-size="12" fill="#59656b" text-anchor="middle">得分率（越右越容易）</text>');
    p.push('<text x="14" y="' + ((Y0 + Y1) / 2) + '" font-size="12" fill="#59656b" text-anchor="middle" transform="rotate(-90 14 ' + ((Y0 + Y1) / 2) + ')">区分度（越高越能分出强弱）</text>');
    var ql = 'font-size="12" font-weight="600" opacity=".8"';
    p.push('<text x="' + (X1 - 6) + '" y="' + (Y0 + 15) + '" ' + ql + ' fill="#2f7d5c" text-anchor="end">基础能区分</text>');
    p.push('<text x="' + (X1 - 6) + '" y="' + (Y1 - 8) + '" ' + ql + ' fill="#7a8380" text-anchor="end">送分</text>');
    p.push('<text x="' + (X0 + 6) + '" y="' + (Y0 + 15) + '" ' + ql + ' fill="#496579">有难度·能区分</text>');
    p.push('<text x="' + (X0 + 6) + '" y="' + (Y1 - 8) + '" ' + ql + ' fill="#b3402a">可疑</text>');

    // 默认合并多小问为整题点；state.scatterParts 时逐小问
    var partOf = {};
    (exam.groups || []).forEach(function (g) { g.parts.forEach(function (id) { partOf[id] = g; }); });
    var points = [], doneG = {};
    if (window.PR.state.scatterParts) {
      rows.forEach(function (r) {
        points.push({ id: r.item.id, rate: r.item.rate, r: r.item.r_rest, quad: r.quad, parts: 0 });
      });
    } else {
      rows.forEach(function (r) {
        var g = partOf[r.item.id];
        if (!g) points.push({ id: r.item.id, rate: r.item.rate, r: r.item.r_rest, quad: r.quad, parts: 0 });
        else if (!doneG[g.id]) {
          doneG[g.id] = 1;
          points.push({ id: g.id, rate: g.rate, r: g.r_rest,
            quad: window.PR.quadrant({ rate: g.rate, r_rest: g.r_rest }), parts: g.parts.length });
        }
      });
    }

    var placed = [];
    var offs = [[8, -6, 'start'], [8, 13, 'start'], [-8, -6, 'end'], [0, -12, 'middle'], [0, 21, 'middle']];
    points.forEach(function (pt) {
      var cx = sx(pt.rate), cy = sy(pt.r);
      var color = COLORS[pt.quad], label = pt.id;
      var lx = cx + 8, ly = cy - 6, anchor = 'start';
      for (var i = 0; i < offs.length; i++) {
        var tx = cx + offs[i][0], ty = cy + offs[i][1], ta = offs[i][2];
        var w = label.length * 7 + 4, h = 11;
        var bx = ta === 'end' ? tx - w : ta === 'middle' ? tx - w / 2 : tx;
        var box = { x: bx, y: ty - h + 2, w: w, h: h };
        var hit = placed.some(function (b) {
          return box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y;
        });
        if (!hit && box.x >= X0 - 2 && box.x + box.w <= X1 + 2) {
          lx = tx; ly = ty; anchor = ta; placed.push(box); break;
        }
      }
      if (pt.parts > 1) {
        p.push('<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="9.5" fill="none" stroke="' + color + '" stroke-width="1.4"/>');
      }
      p.push('<circle class="dot" data-item="' + esc(pt.id) + '" cx="' + cx.toFixed(1) +
        '" cy="' + cy.toFixed(1) + '" r="6" fill="' + color + '" fill-opacity=".85"/>');
      p.push('<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) + '" font-size="11" fill="' +
        color + '" text-anchor="' + anchor + '" pointer-events="none">' + esc(label) + '</text>');
    });

    var legend = '<div class="scatter-legend">' + LEGEND.map(function (l) {
      return '<div class="sl-row"><b style="background:' + COLORS[l[0]] + '"></b>' +
        '<div><strong>' + l[1] + '</strong><p>' + l[2] + '</p></div></div>';
    }).join('') + '</div>';

    return '<div class="scatter-wrap"><div class="scatter-flex">' +
      '<svg viewBox="0 0 ' + W + ' ' + H + '" style="flex:1;min-width:0;height:auto" role="img" aria-label="难度与区分度散点图">' +
      p.join('') + '</svg>' + legend + '</div></div>';
  };

  window.bindScatter = function (exam, state) {
    var svg = document.querySelector('.scatter-wrap svg');
    if (!svg) return;
    var tip = document.getElementById('scatter-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'scatter-tip';
      document.body.appendChild(tip);
    }
    svg.querySelectorAll('.dot').forEach(function (d) {
      var id = d.dataset.item;
      var it = exam.items.find(function (x) { return x.id === id; });
      var grp = (exam.groups || []).find(function (g) { return g.id === id; });
      if (!it && !grp) return;
      var rate = it ? it.rate : grp.rate;
      var rr = it ? it.r_rest : grp.r_rest;
      var quad = window.PR.QUAD[window.PR.quadrant({ rate: rate, r_rest: rr })].name;
      d.addEventListener('mousemove', function (ev) {
        tip.style.display = 'block';
        tip.style.left = (ev.clientX + 12) + 'px';
        tip.style.top = (ev.clientY + 12) + 'px';
        tip.textContent = id + ' · ' + (it ? window.PR.TYPE_NAMES[it.type] : grp.parts.length + ' 小问合并') +
          '\n得分率 ' + window.PR.pct(rate) + ' · 区分度 ' + rr.toFixed(3) + '\n' + quad;
      });
      d.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
      d.addEventListener('click', function () { window.PR.goToItem(id); });
    });
  };
})();
