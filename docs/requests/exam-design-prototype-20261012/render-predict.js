/* 命题训练原型 - ②我的预测：盲估 → 保存命题记录（localStorage） */
(function () {
  'use strict';

  function fmtTime(iso) {
    var d = new Date(iso);
    return isNaN(d) ? String(iso) : d.toLocaleString('zh-CN', { hour12: false });
  }

  window.renderPredict = function (p, state) {
    var ED = window.ED, esc = ED.esc;
    var rec = ED.loadRecord(p.id);
    var locked = !!(rec && !state.unlocked);
    var preds = (rec && rec.predictions) || {};
    var M = p.items.length;
    var filled = p.items.filter(function (it) { return preds[it.id] != null; }).length;

    var bar;
    if (locked) {
      bar = '<span class="saved-line">已保存于 ' + esc(fmtTime(rec.saved_at)) +
        '；考后对照使用这份记录</span>' +
        '<button class="btn" id="pd-edit">修改（考前）</button>' +
        '<button class="btn" id="pd-clear">清空</button>';
    } else {
      bar = '<button class="btn primary" id="pd-save">保存命题记录</button>' +
        (rec ? '<button class="btn" id="pd-clear">清空</button>' : '');
    }

    var rows = p.items.map(function (it) {
      var v = preds[it.id];
      var sys = it.pred ? '<span class="mono">' + ED.pct(it.pred.all) + '</span>'
        : '<span title="' + esc(it.pred_source || '') + '">—</span>';
      return '<tr>' +
        '<td><span class="pid">' + esc(it.id) + '</span></td>' +
        '<td>' + esc(it.section || '—') + '</td>' +
        '<td>' + (it.difficulty == null ? '—' : '<span class="mono">' + it.difficulty + '</span>' + ED.bandTag(it.difficulty)) + '</td>' +
        '<td><input class="pred-in" type="number" min="0" max="100" step="1" data-item="' + esc(it.id) + '"' +
          (v != null ? ' value="' + v + '"' : '') + (locked ? ' disabled' : '') + '><span class="unit">%</span></td>' +
        '<td class="sys-col">' + sys + '</td></tr>';
    }).join('');

    return '<p class="hint" style="margin:0">先自己估，再看系统预估。每个评分项填你估计的全场得分率（0–100）。</p>' +
      '<div class="card"><header><h2>我的预测</h2><span class="hint">按评分项计，多小题逐问填写</span></header>' +
      '<div class="body"><div class="pred-bar">' +
        '<span class="switch' + (state.showSys ? ' on' : '') + '" id="pd-sys"><span class="sw"></span>显示系统预估</span>' +
        '<span class="cnt" id="pd-cnt">已填 ' + filled + ' / ' + M + '</span>' + bar +
      '</div></div>' +
      '<div class="body" style="padding:0"><table class="items' + (state.showSys ? ' sys-col-on' : '') + '">' +
      '<thead><tr><th>题号</th><th>小节</th><th>题库难度</th><th>我的预测</th><th class="sys-col">系统预估</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div></div>';
  };

  window.bindPredict = function (p, state, rerender) {
    var ED = window.ED;
    var M = p.items.length;

    function countFilled() {
      var k = 0;
      document.querySelectorAll('.pred-in').forEach(function (inp) {
        var v = inp.value.trim();
        var bad = v !== '' && (isNaN(Number(v)) || Number(v) < 0 || Number(v) > 100);
        inp.classList.toggle('bad', bad);
        if (v !== '' && !bad) k++;
      });
      var c = document.getElementById('pd-cnt');
      if (c) c.textContent = '已填 ' + k + ' / ' + M;
      return k;
    }

    var sw = document.getElementById('pd-sys');
    if (sw) sw.addEventListener('click', function () {
      state.showSys = !state.showSys;
      rerender();
    });

    document.querySelectorAll('.pred-in').forEach(function (inp) {
      inp.addEventListener('input', countFilled);
    });

    var save = document.getElementById('pd-save');
    if (save) save.addEventListener('click', function () {
      var preds = {};
      document.querySelectorAll('.pred-in').forEach(function (inp) {
        var v = inp.value.trim();
        if (v === '') return;
        var n = Number(v);
        if (isNaN(n) || n < 0 || n > 100) { inp.classList.add('bad'); return; }
        preds[inp.dataset.item] = n;
      });
      ED.saveRecord(p.id, { predictions: preds, saved_at: new Date().toISOString() });
      state.unlocked = false;
      rerender();
    });

    var edit = document.getElementById('pd-edit');
    if (edit) edit.addEventListener('click', function () {
      state.unlocked = true;
      rerender();
    });

    var clear = document.getElementById('pd-clear');
    if (clear) clear.addEventListener('click', function () {
      ED.clearRecord(p.id);
      state.unlocked = false;
      rerender();
    });
  };
})();
