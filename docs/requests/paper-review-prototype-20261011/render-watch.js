/* 试卷评价原型 - 可能要改 / 值得保留 题卡 + 全场散点图 */
(function () {
  'use strict';

  function markPair(r) {
    return '<span class="marks">' +
      '<button class="mark-btn keep' + (r.mark === 'keep' ? ' on' : '') + '" data-mark="keep" title="下次组卷还想用这题">留</button>' +
      '<button class="mark-btn fix' + (r.mark === 'fix' ? ' on' : '') + '" data-mark="fix" title="题面、难度或位置要调整">改</button></span>';
  }

  function cardHTML(r, reason) {
    var PR = window.PR, esc = PR.esc, it = r.item;
    var rate = PR.displayRate(it);
    return '<div class="watch-card" data-item="' + esc(it.id) + '" title="点击展开逐题评价中的详情">' +
      '<span class="wid">' + esc(it.id) + '</span>' +
      '<span class="wbar"><span class="rbar"><i style="width:' + (rate * 100) + '%"></i></span> ' +
        '<b class="mono">' + PR.pct(rate) + '</b></span>' +
      '<span class="wdots" title="区分度 ' + it.r_rest.toFixed(3) + '">' + PR.dotsHTML(it.r_rest) + '</span>' +
      '<span class="werr">' + esc(reason) + '</span>' + markPair(r) + '</div>';
  }

  window.renderWatch = function (exam, rows, state) {
    var esc = window.PR.esc;
    var bh = exam.evaluation.dimensions.find(function (d) { return d.key === 'basic_hits'; });
    var weakIds = bh ? bh.detail.weak : [];
    var hitIds = bh ? bh.detail.hit_items : [];
    var byId = {};
    rows.forEach(function (r) { byId[r.item.id] = r; });

    // 可能要改：可疑在前，其后基础题没分出强弱
    var qualifiesFix = function (r) { return r.quad === 'suspect' || weakIds.indexOf(r.item.id) >= 0; };
    var suspects = rows.filter(function (r) { return r.quad === 'suspect'; });
    var weaks = weakIds.map(function (id) { return byId[id]; }).filter(Boolean);
    var seen = {}, fixList = [];
    suspects.concat(weaks).forEach(function (r) {
      if (!seen[r.item.id] && fixList.length < 3) { seen[r.item.id] = 1; fixList.push(r); }
    });
    var fixCards = fixList.map(function (r) {
      var reason;
      if (r.quad === 'suspect') {
        reason = r.item.r_rest >= 0.3
          ? '区分度略低（靠近误差范围）：可抽查题面或批改'
          : '难度中等却分不开强弱：查题面或抽查批改';
      } else {
        reason = r.item.rate < 0.40 ? '基础题却很少人做对：查是否超纲或还没教到' : '基础题分不开强弱';
      }
      return cardHTML(r, reason);
    }).join('');

    // 值得保留：考出问题的题中，排除一切够格进“要改”的（可疑或没分出强弱）
    var keepList = hitIds.map(function (id) { return byId[id]; })
      .filter(function (r) { return r && !qualifiesFix(r); })
      .sort(function (a, b) {
        var ea = a.item.errors && a.item.errors[0], eb = b.item.errors && b.item.errors[0];
        return (eb ? eb.count : b.item.lost_students) - (ea ? ea.count : a.item.lost_students);
      }).slice(0, 3);
    var keepCards = keepList.map(function (r) {
      var e = r.item.errors && r.item.errors[0];
      return cardHTML(r, e ? e.count + '人同错：' + e.reason : '基础题，考出了差距');
    }).join('');

    function card(title, hint, body, empty) {
      return '<div class="card"><header><h2>' + title + '</h2><span class="hint">' + hint + '</span></header>' +
        (body ? '<div class="watch-list">' + body + '</div>'
          : '<div class="watch-empty">' + empty + '</div>') + '</div>';
    }

    return '<section class="watch2">' +
      card('可能要改的题', '从出题角度：哪些题下次要改、哪些值得保留', fixCards, '✓ 没有明显要改的题') +
      card('值得保留的题', '基础题里考出差距的', keepCards, '✓ 暂无') +
      '</section>' +
      '<section class="card"><header><h2>难度与区分度</h2>' +
      '<label class="hint" style="margin-left:auto;cursor:pointer"><input type="checkbox" id="scatter-parts"' +
      (state.scatterParts ? ' checked' : '') + '> 按小问显示</label>' +
      '<span class="hint" style="margin-left:8px">点圆点定位到逐题评价</span></header>' +
      window.renderScatter(exam, rows, {}) +
      '<p class="scatter-note">区分度＝本题得分与其余题总分的相关系数；约 ' + exam.students +
      ' 人时误差约 ±0.1，靠线的题不下结论。</p></section>';
  };

  window.bindWatch = function (exam, state, render) {
    var PR = window.PR;
    var sp = document.getElementById('scatter-parts');
    if (sp) sp.addEventListener('change', function () { state.scatterParts = sp.checked; render(); });
    document.querySelectorAll('.watch-card').forEach(function (c) {
      c.addEventListener('click', function (ev) {
        if (ev.target.closest('.mark-btn')) return;
        PR.goToItem(c.dataset.item);
      });
      c.querySelectorAll('.mark-btn').forEach(function (b) {
        b.addEventListener('click', function (ev) {
          ev.stopPropagation();
          var it = exam.items.find(function (x) { return x.id === c.dataset.item; });
          var v = b.dataset.mark;
          PR.setMark(it, PR.getMark(it) === v ? '' : v);
          render();
        });
      });
    });
    window.bindScatter(exam, state);
  };
})();
