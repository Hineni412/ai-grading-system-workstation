/* 试卷评价原型 - 高频题型覆盖（选题效率 tile 版） */
(function () {
  'use strict';

  // exam.coverage 里按 key 取 covered_by / secondary_by / typical
  function typeMap(exam) {
    var m = {};
    (exam.coverage || []).forEach(function (s) {
      (s.types || []).forEach(function (t) { m[t.key] = t; });
    });
    return m;
  }

  function diffBadge(d) {
    if (d == null) return '';
    var n = d < 4 ? '基础' : d < 7 ? '中档' : '偏难';
    return '<span class="fb dfb">' + n + '</span>';
  }

  function tileHTML(t, cmap) {
    var PR = window.PR, esc = PR.esc;
    var name = esc(PR.stripPrefix(t.name));
    var freq = '<span class="fb">' + t.group_count + '套</span>';
    var ref = cmap[t.key];
    var pop = '';
    if (ref && ref.typical && ref.typical[0]) {
      pop = '<span class="cov-pop" hidden><b>可参考：' + esc(ref.typical[0].role) +
        '</b>' + esc(ref.typical[0].stem) + '</span>';
    }
    if (t.credit > 0) {
      var allIds = ref ? (ref.covered_by || []).concat(ref.secondary_by || []) : [];
      var seen = {}, parents = [];
      allIds.forEach(function (id) {
        var p = window.PR.parentId(id);
        if (!seen[p]) { seen[p] = 1; parents.push(p); }
      });
      var ids = parents.map(function (id) { return '<span class="kn">' + esc(id) + '</span>'; }).join('');
      return '<span class="ctile ' + (t.credit === 1 ? 'cov' : 'half') + '" title="本卷题目：' +
        esc(allIds.join(' ')) + '">' +
        '<span class="nm">' + name + '</span>' + freq + diffBadge(t.difficulty) +
        (t.credit === 0.5 ? '<span class="fb tag">次要</span>' : '') + ids + '</span>';
    }
    if (t.skipped) {
      return '<span class="ctile skip" title="更高频但没选，点击看参考题">' +
        '<span class="nm">' + name + '</span>' + freq + diffBadge(t.difficulty) +
        '<span class="fb tag">更高频未选</span>' + pop + '</span>';
    }
    return '<span class="ctile exempt"' + (t.hard ? ' title="偏难，可不选，点击看参考题"' : '') + '>' +
      '<span class="nm">' + name + '</span>' + freq + diffBadge(t.difficulty) +
      (t.hard ? '<span class="fb tag">偏难·可不选</span>' : '') + pop + '</span>';
  }

  window.renderCoverage = function (exam) {
    var esc = window.PR.esc;
    var dim = exam.evaluation.dimensions.find(function (d) { return d.key === 'coverage'; });
    var sections = (dim && dim.detail.sections) || [];
    var cmap = typeMap(exam);
    var outside = (exam.coverage || []).filter(function (s) { return s.chapter == null; });

    var parts = ['<section id="coverage-bottom"><h2 style="margin:0 0 4px;font-size:17px">高频题型覆盖</h2>' +
      '<p class="hint" style="margin:0 0 8px">按期中期末出现套数从高到低，只列到本卷所选最低频的题型为止</p>'];

    sections.forEach(function (s, si) {
      var shown = s.types.filter(function (t) { return t.shown; });
      var hidden = s.types.filter(function (t) { return !t.shown; });
      var picked = s.types.filter(function (t) { return t.credit > 0; }).length;
      parts.push('<div class="card cov-card"><header><h2>' + esc(s.section) +
        ' · 选题效率 ' + Math.round(s.ratio * 100) + '%</h2>' +
        '<span class="cov-count">本卷选了 ' + picked + ' 个题型</span></header>' +
        '<div class="body"><div class="cov-tiles">' +
        shown.map(function (t) { return tileHTML(t, cmap); }).join('') + '</div>' +
        (hidden.length ? '<div class="cov-tiles cov-low" data-low="' + si + '" hidden>' +
          hidden.map(function (t) { return tileHTML(t, cmap); }).join('') + '</div>' +
          '<button class="cov-toggle" data-low="' + si + '">另有 ' + hidden.length +
          ' 个题型低于本卷最低频选题（' + s.floor + '套），未列出</button>' : '') +
        '</div></div>');
    });

    if (outside.length) {
      parts.push('<p class="muted-line">另有 ' + outside.length + ' 个小节未关联章节，未做对照。</p>');
    }
    parts.push('</section>');
    return parts.join('');
  };

  window.bindCoverage = function () {
    document.querySelectorAll('.cov-toggle').forEach(function (b) {
      b.addEventListener('click', function () {
        var rest = document.querySelector('.cov-low[data-low="' + b.dataset.low + '"]');
        if (!rest) return;
        var open = rest.hidden;
        rest.hidden = !open;
        if (open) b.textContent = '收起';
      });
    });
    document.querySelectorAll('.ctile.skip, .ctile.exempt').forEach(function (t) {
      t.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var pop = t.querySelector('.cov-pop');
        if (!pop) return;
        var wasHidden = pop.hidden;
        document.querySelectorAll('.cov-pop').forEach(function (x) { x.hidden = true; });
        pop.hidden = !wasHidden;
      });
    });
    if (!window.__covDocBound) {
      window.__covDocBound = true;
      document.addEventListener('click', function () {
        document.querySelectorAll('.cov-pop').forEach(function (x) { x.hidden = true; });
      });
    }
  };
})();
