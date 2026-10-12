/* 试卷评价原型 - 逐题评价（多小问合并 + 展开详情） */
(function () {
  'use strict';

  var FILTERS = [
    ['all', '全部'], ['suspect', '可疑'], ['harder', '比预估难'],
    ['typical', '暴露典型错误'], ['fix', '要改'],
  ];

  function match(r, f) {
    if (f === 'all') return true;
    if (f === 'suspect') return r.quad === 'suspect';
    if (f === 'harder') return r.pred.key === 'harder';
    if (f === 'typical') return !!r.typical;
    if (f === 'fix') return r.mark === 'fix';
    return true;
  }

  function rateCell(o) {
    var PR = window.PR, v = PR.displayRate(o);
    var ticks = '';
    if (PR.state.cls === 'all' && o.class_rates) {
      ticks = '<span class="tick" style="left:' + (o.class_rates['9'] * 100) + '%" title="9班 ' +
        PR.pct(o.class_rates['9']) + '"></span>' +
        '<span class="tick t10" style="left:' + (o.class_rates['10'] * 100) +
        '%" title="10班 ' + PR.pct(o.class_rates['10']) + '"></span>';
    }
    return '<span class="rbar" title="得分率 ' + PR.pct(v) + '"><i style="width:' + (v * 100) + '%"></i>' +
      ticks + '</span> <b class="mono">' + PR.pct(v) + '</b>';
  }

  var PRED_ICON = {
    harder: ['↑', 'pred-hard', '比预估难'],
    easier: ['↓', 'pred-easy', '比预估容易'],
    fit: ['', '', '与预估相符'],
    none: ['', 'pred-none', '无预估'],
  };
  function predCell(pred) {
    var ic = PRED_ICON[pred.key] || PRED_ICON.none;
    return '<span class="pred-i ' + ic[1] + '" title="' + ic[2] + '">' + ic[0] + '</span>';
  }

  function markPair(mark, withBtns) {
    if (!withBtns) return '';
    return '<span class="marks">' +
      '<button class="mark-btn keep' + (mark === 'keep' ? ' on' : '') + '" data-mark="keep" title="下次组卷还想用这题">留</button>' +
      '<button class="mark-btn fix' + (mark === 'fix' ? ' on' : '') + '" data-mark="fix" title="题面、难度或位置要调整">改</button></span>';
  }

  function optionBars(it) {
    var d = it.distractors, esc = window.PR.esc;
    var exam = window.PAPER_REVIEW_DATA.exams[window.PR.state.examIndex];
    if (!d) return '';
    if (!d.counts) return '<div><h4>选项选择情况</h4><div class="kv">' + esc(d.note || '无记录') + '</div></div>';
    var keys = Object.keys(d.counts).filter(function (k) { return k !== '未识别或空白'; });
    keys.push('未识别或空白');
    var max = Math.max.apply(null, keys.map(function (k) { return d.counts[k] || 0; }).concat([1]));
    var rows = keys.map(function (k) {
      var n = d.counts[k] || 0, isAns = k === d.answer;
      var rare = !isAns && k !== '未识别或空白' && n / exam.students <= 0.03;
      var w = Math.max(2, Math.round(n / max * 180));
      return '<div class="optbar"><span class="opt-name">' + esc(k) + (isAns ? '（答案）' : '') +
        '</span><span class="bar ' + (isAns ? 'ans' : 'wrong') + '" style="width:' + w + 'px"></span>' +
        '<span class="cnt">' + n + '人</span>' +
        (rare ? '<span class="rare">几乎没人选</span>' : '') + '</div>';
    }).join('');
    return '<div><h4>选项选择情况</h4>' + rows + '</div>';
  }

  function detailHTML(it) {
    var PR = window.PR, esc = PR.esc;
    var parts = ['<tr class="detail-row"><td colspan="7"><div class="detail">'];
    parts.push('<div class="stem-full">' + esc(it.stem) + '</div>');
    if (it.errors && it.errors.length) {
      var lis = it.errors.slice(0, 5).map(function (e) {
        return '<li>' + esc(e.reason) + '（' + esc(e.category) + '·' + e.count + '人）</li>';
      }).join('');
      parts.push('<div><h4>错法记录（前 5 条）</h4><ul>' + lis + '</ul></div>');
    }
    parts.push(optionBars(it));
    var prim = (it.primary_types || []).map(function (t) { return esc(PR.stripPrefix(t.name)); }).join('、') || '—';
    var sec = (it.secondary_types || []).map(function (t) { return esc(PR.stripPrefix(t.name)); }).join('、');
    var meta = '<div class="kv"><b>题型</b>：' + (PR.TYPE_NAMES[it.type] || it.type) +
      '　<b>主题型</b>：' + prim;
    if (sec) meta += '　<b>次题型</b>：' + sec;
    meta += '<br><b>预估难度</b>：' + (it.predicted_difficulty == null ? '无' : it.predicted_difficulty) +
      '（' + esc(it.predicted_source || '无来源') + '）　<b>题库题号</b>：#' + esc(it.bank_question_id) +
      '　<b>失分人数</b>：' + it.lost_students + '　<b>满分</b>：' + it.max + '</div>';
    parts.push(meta);
    parts.push('</div></td></tr>');
    return parts.join('');
  }

  // 单题行（含小问行；小问行无标记按钮）
  function itemRowHTML(r, state, isPart) {
    var PR = window.PR, esc = PR.esc;
    var it = r.item, q = PR.QUAD[r.quad];
    var tr = '<tr class="item-row' + (isPart ? ' part-row' + (state.openGroups[PR.parentId(it.id)] ? ' open' : '') : '') +
      '" id="row-' + esc(it.id) + '" data-item="' + esc(it.id) + '">' +
      '<td class="mono"><b>' + esc(it.id) + '</b></td>' +
      '<td>' + rateCell(it) + '</td>' +
      '<td><span title="区分度 ' + it.r_rest.toFixed(3) + '">' + PR.dotsHTML(it.r_rest) + '</span></td>' +
      '<td><span class="chip ' + q.cls + '" title="' + (r.quad === 'suspect' ? '建议看题面或抽查批改' : q.name) + '">' + q.name + '</span></td>' +
      '<td>' + predCell(r.pred) + '</td>' +
      '<td>' + (r.typical ? '<span class="warn-cnt" title="主要错法：' + esc(r.typical.reason) +
        '">⚠ ' + r.typical.count + '人</span>' : '') + '</td>' +
      '<td>' + markPair(r.mark, !isPart || state.filter !== 'all') + '</td></tr>';
    if (state.expanded === it.id) tr += detailHTML(it);
    return tr;
  }

  window.renderTable = function (exam, rows, state) {
    var PR = window.PR, esc = PR.esc;
    var groups = exam.groups || [];
    var partOf = {};
    groups.forEach(function (g) { g.parts.forEach(function (id) { partOf[id] = g; }); });
    var grouped = state.filter === 'all' && groups.length;
    var byId = {};
    rows.forEach(function (r) { byId[r.item.id] = r; });

    var chips = FILTERS.map(function (f) {
      var n = f[0] === 'all' ? rows.length : rows.filter(function (r) { return match(r, f[0]); }).length;
      return '<button class="chip-filter' + (state.filter === f[0] ? ' on' : '') +
        '" data-filter="' + f[0] + '">' + f[1] + ' ' + n + '</button>';
    }).join('');

    var body = '';
    if (!grouped) {
      var visible = rows.filter(function (r) { return match(r, state.filter); });
      body = visible.map(function (r) {
        return itemRowHTML(r, state, false);
      }).join('') || '<tr><td colspan="7" class="muted-line">当前筛选下没有题。</td></tr>';
    } else {
      var done = {};
      exam.items.forEach(function (it) {
        var g = partOf[it.id];
        if (!g) { body += itemRowHTML(byId[it.id], state, false); return; }
        if (done[g.id]) { body += itemRowHTML(byId[it.id], state, true); return; }
        done[g.id] = 1;
        // 父行（其后所有小问照常以小问行渲染）
        var pit = { id: g.id, rate: g.rate, class_rates: g.class_rates, r_rest: g.r_rest,
          predicted_difficulty: g.predicted_difficulty };
        var quad = PR.quadrant(pit), pred = PR.predFlag(pit);
        var open = !!state.openGroups[g.id];
        // 错法：小问里主要错法人数最多的一条
        var top = null;
        g.parts.forEach(function (id) {
          var r = byId[id];
          if (r && r.typical && (!top || r.typical.count > top.count)) top = { id: id, e: r.typical };
        });
        var bhDim = exam.evaluation.dimensions.find(function (d) { return d.key === 'basic_hits'; });
        var weakIds = bhDim ? (bhDim.detail.weak || []) : [];
        var flagged = g.parts.some(function (id) {
          var r = byId[id];
          return r && (r.quad === 'suspect' || weakIds.indexOf(id) >= 0);
        });
        body += '<tr class="item-row grow" id="row-' + esc(g.id) + '" data-group="' + esc(g.id) + '">' +
          '<td class="mono"><span class="caret">' + (open ? '▾' : '▸') + '</span><b>' + esc(g.id) + '</b>' +
            '<span class="gcount">' + g.parts.length + '小问</span>' +
            (flagged ? '<span class="flag-dot" title="含需要注意的小问"></span>' : '') + '</td>' +
          '<td>' + rateCell(pit) + '</td>' +
          '<td><span title="整题区分度 ' + g.r_rest.toFixed(3) + '">' + PR.dotsHTML(g.r_rest) + '</span></td>' +
          '<td><span class="chip ' + PR.QUAD[quad].cls + '">' + PR.QUAD[quad].name + '</span></td>' +
          '<td>' + predCell(pred) + '</td>' +
          '<td>' + (top ? '<span class="warn-cnt" title="' + esc(top.id) + '：' + esc(top.e.reason) +
            '">⚠ ' + top.e.count + '人</span>' : '') + '</td>' +
          '<td>' + markPair(PR.getMark(pit), true) + '</td></tr>';
        body += itemRowHTML(byId[it.id], state, true);
      });
    }

    var qCount = groups.length ? exam.items.length -
      groups.reduce(function (s, g) { return s + g.parts.length; }, 0) + groups.length : 0;
    var headHint = groups.length
      ? '全部 ' + qCount + ' 题（' + exam.items.length + ' 个小问）'
      : '点击行展开题干、错法与选项';

    return '<section class="card" id="items-card"><header><h2>逐题评价</h2>' +
      '<div class="filters">' + chips + '</div>' +
      '<span class="hint">' + headHint + '</span></header>' +
      '<div style="overflow-x:auto"><table class="items"><thead><tr>' +
      '<th>题号</th><th>得分率</th><th>区分度</th><th>类别</th><th>预估</th><th>错法</th>' +
      '<th title="标记只存在本机浏览器，原型阶段不影响组卷">标记</th>' +
      '</tr></thead><tbody>' + body + '</tbody></table></div>' +
      (groups.length ? '<p class="scatter-note">多小问大题按整题合并：得分率按分值加权，区分度用整题得分对其余题总分计算；合并后的区分度通常比单个小问高，不要和单题直接比较。</p>' : '') +
      '</section>';
  };

  window.bindTable = function (exam, rows, state, render) {
    var PR = window.PR;
    var sec = document.getElementById('items-card');
    if (!sec) return;
    sec.querySelectorAll('.chip-filter').forEach(function (b) {
      b.addEventListener('click', function () {
        state.filter = b.dataset.filter; state.expanded = null; render();
      });
    });
    sec.querySelectorAll('tr.grow').forEach(function (tr) {
      tr.addEventListener('click', function (ev) {
        if (ev.target.closest('.mark-btn')) return;
        var id = tr.dataset.group;
        state.openGroups[id] = !state.openGroups[id];
        render();
      });
    });
    sec.querySelectorAll('tr.item-row:not(.grow)').forEach(function (tr) {
      tr.addEventListener('click', function (ev) {
        if (ev.target.closest('.mark-btn')) return;
        var id = tr.dataset.item;
        state.expanded = state.expanded === id ? null : id;
        render();
        if (state.expanded) {
          var el = document.getElementById('row-' + id);
          if (el) el.scrollIntoView({ block: 'nearest' });
        }
      });
    });
    sec.querySelectorAll('.mark-btn').forEach(function (b) {
      b.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var tr = b.closest('tr');
        var id = tr.dataset.item || tr.dataset.group;
        var it = exam.items.find(function (x) { return x.id === id; }) || { id: id };
        var v = b.dataset.mark;
        PR.setMark(it, PR.getMark(it) === v ? '' : v);
        render();
      });
    });
  };
})();
