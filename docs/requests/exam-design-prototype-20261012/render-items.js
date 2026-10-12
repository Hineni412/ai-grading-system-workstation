/* 命题训练原型 - ①考前预评：逐题表 + 高频题型覆盖 */
(function () {
  'use strict';

  function targetText(it) {
    var ED = window.ED;
    var segs = (it.targets || []).map(function (t) { return ED.stripPrefix(ED.lastSeg(t)); });
    segs = segs.filter(function (s, i) { return s && segs.indexOf(s) === i; });
    return (it.section ? it.section : '—') + (segs.length ? ' · ' + segs.slice(0, 2).join('、') : '');
  }

  function diffCell(d) {
    if (d == null) return '—';
    return '<span class="mono">' + d + '</span>' + window.ED.bandTag(d);
  }

  function predCell(it) {
    var ED = window.ED;
    if (!it.pred) return '<span title="' + ED.esc(it.pred_source || '') + '">—</span>';
    var r = ED.rate(it.pred);
    return ED.rbar(r) + ' <span class="mono">' + ED.pct(r) + '</span>';
  }

  function actualCell(p, it) {
    var ED = window.ED;
    if (p.kind === 'session') {
      if (!it.actual) return '—';
      var r = ED.rate(it.actual);
      return ED.rbar(r) + ' <span class="mono">' + ED.pct(r) + '</span>';
    }
    var h = (it.history || [])[it.history.length - 1];
    if (!h) return '—';
    return '<span class="mono">' + ED.pct(h.rate) + '</span> <span class="hint">' +
      ED.esc(h.title || '') + '</span>';
  }

  var TYPE_NAMES = { choice: '选择题', fill_blank: '填空题', calculation: '计算题', comprehensive: '综合题' };
  function typeName(t) { return TYPE_NAMES[t] || t || '—'; }

  function detailRow(it) {
    var ED = window.ED, esc = ED.esc;
    return '<tr class="detail-row"><td colspan="5"><div class="detail">' +
      '<div class="stem-full" style="white-space:pre-wrap">' + esc(it.stem || '（无题面）') + '</div>' +
      '<div class="kv">题库 #' + esc(it.bank_question_id == null ? '—' : it.bank_question_id) +
      ' · ' + esc(typeName(it.type)) + ' · 难度来源：' + esc(it.difficulty_source || '—') +
      (it.max != null ? ' · 满分 ' + it.max : ' · 未赋分') + '</div></div></td></tr>';
  }

  function leafRow(p, it, part) {
    var ED = window.ED, esc = ED.esc;
    var open = ED.state.expanded === it.id;
    return '<tr class="item-row' + (part ? ' part-row open' : '') + '" id="row-' + esc(it.id) +
      '" data-leaf="' + esc(it.id) + '">' +
      '<td>' + (part ? '<span class="sub-i">' + esc(it.id) + '</span>'
        : '<span class="caret">' + (open ? '▾' : '▸') + '</span><span class="pid">' + esc(it.id) + '</span>') + '</td>' +
      '<td>' + esc(targetText(it)) + '</td>' +
      '<td>' + diffCell(it.difficulty) + '</td>' +
      '<td>' + predCell(it) + '</td>' +
      '<td>' + actualCell(p, it) + '</td></tr>' +
      (open ? detailRow(it) : '');
  }

  function parentRow(p, g) {
    var ED = window.ED, esc = ED.esc;
    var open = !!ED.state.openGroups[g.id];
    var ds = g.items.map(function (i) { return i.difficulty; }).filter(function (d) { return d != null; });
    var dcell = ds.length
      ? '<span class="mono">' + Math.min.apply(null, ds) + '–' + Math.max.apply(null, ds) + '</span>'
      : '—';
    var preds = g.items.filter(function (i) { return i.pred; });
    var pr = preds.length ? ED.mean(preds.map(function (i) { return ED.rate(i.pred); })) : null;
    var pc = pr == null ? '—' : '<span class="mono">' + ED.pct(pr) + '</span>';
    var ac = '—';
    if (p.kind === 'session') {
      var acts = g.items.filter(function (i) { return i.actual; });
      var ar = acts.length ? ED.mean(acts.map(function (i) { return ED.rate(i.actual); })) : null;
      if (ar != null) ac = '<span class="mono">' + ED.pct(ar) + '</span>';
    }
    var sec = g.items[0].section || '—';
    return '<tr class="item-row" data-group="' + esc(g.id) + '">' +
      '<td><span class="caret">' + (open ? '▾' : '▸') + '</span><span class="pid">' + esc(g.id) +
      '</span><span class="gcount">' + g.items.length + '小问</span></td>' +
      '<td>' + esc(sec) + '</td><td>' + dcell + '</td><td>' + pc + '</td><td>' + ac + '</td></tr>';
  }

  function tableHTML(p) {
    var ED = window.ED;
    var rows = [];
    ED.groups(p).forEach(function (g) {
      if (g.items.length === 1) {
        rows.push(leafRow(p, g.items[0], false));
      } else {
        rows.push(parentRow(p, g));
        if (ED.state.openGroups[g.id]) {
          g.items.forEach(function (it) { rows.push(leafRow(p, it, true)); });
        }
      }
    });
    return '<div class="card"><header><h2>逐题</h2>' +
      '<span class="hint">点击多小题展开；点击小题看题面</span></header>' +
      '<div class="body" style="padding:0"><table class="items"><thead><tr>' +
      '<th>题号</th><th>小节·目标</th><th>题库难度</th><th>系统预估</th><th>历史实测</th>' +
      '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div></div>';
  }

  // 覆盖 chips（复用 style.css 的 ctile）
  function tileHTML(t) {
    var ED = window.ED, esc = ED.esc;
    var name = esc(ED.stripPrefix(t.name));
    var freq = '<span class="fb">' + t.group_count + '套</span>';
    var d = t.difficulty == null ? '' : '<span class="fb">' + ED.BAND_NAME[ED.band(t.difficulty)] + '</span>';
    if (t.credit > 0) {
      return '<span class="ctile ' + (t.credit === 1 ? 'cov' : 'half') + '">' +
        '<span class="nm">' + name + '</span>' + freq + d +
        (t.credit === 0.5 ? '<span class="fb tag">次要</span>' : '') + '</span>';
    }
    if (t.skipped) {
      return '<span class="ctile skip"><span class="nm">' + name + '</span>' + freq + d +
        '<span class="fb tag">更高频未选</span></span>';
    }
    return '<span class="ctile exempt"><span class="nm">' + name + '</span>' + freq + d +
      (t.hard ? '<span class="fb tag">偏难·可不选</span>' : '') + '</span>';
  }

  function coverageHTML(p) {
    var ED = window.ED, esc = ED.esc;
    var secs = ((p.coverage || {}).detail || {}).sections || [];
    if (!secs.length) return '';
    var parts = ['<section><h2 style="margin:0 0 4px;font-size:17px">高频题型覆盖</h2>' +
      '<p class="hint" style="margin:0 0 8px">与试卷评价同一规则：只列到本卷所选最低频的题型为止</p>'];
    secs.forEach(function (s) {
      var shown = s.types.filter(function (t) { return t.shown; });
      var hidden = s.types.length - shown.length;
      var picked = s.types.filter(function (t) { return t.credit > 0; }).length;
      parts.push('<div class="card cov-card"><header><h2>' + esc(s.section) +
        ' · 选题效率 ' + Math.round(s.ratio * 100) + '%</h2>' +
        '<span class="cov-count">本卷选了 ' + picked + ' 个题型</span></header>' +
        '<div class="body"><div class="cov-tiles">' +
        shown.map(tileHTML).join('') + '</div>' +
        (hidden ? '<p class="muted-line" style="padding:8px 0 0">另有 ' + hidden +
          ' 个题型低于本卷最低频选题（' + s.floor + '套），未列出</p>' : '') +
        '</div></div>');
    });
    parts.push('</section>');
    return parts.join('');
  }

  window.renderItems = function (p) {
    return tableHTML(p) + coverageHTML(p);
  };

  window.bindItems = function (p, state, rerender) {
    document.querySelectorAll('tr[data-group]').forEach(function (tr) {
      tr.addEventListener('click', function () {
        var id = tr.dataset.group;
        state.openGroups[id] = !state.openGroups[id];
        rerender();
      });
    });
    document.querySelectorAll('tr[data-leaf]').forEach(function (tr) {
      tr.addEventListener('click', function () {
        var id = tr.dataset.leaf;
        state.expanded = state.expanded === id ? null : id;
        rerender();
      });
    });
  };
})();
