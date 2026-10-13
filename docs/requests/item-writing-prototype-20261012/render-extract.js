/* 命题训练原型 - “从题库抽取情境”弹窗与任务中心模拟 */
window.IW = window.IW || {};
(function () {
  'use strict';
  var RULES = ['只抽所选教材册、所选章里未删除的题。',
    '至少有一个小问属于生活情境、数学文化或科学跨学科；只含“新定义”小问的题不送。',
    '题干去掉选项和分值标注后不少于 60 字，太短不送。',
    '抽过的题不再送（包括被停用材料对应的题）。',
    '同一题模板的题只送一道：优先解答题、其次无配图、再按题号从小到大。'];

  function esc(s) { return IW.esc(s); }
  function est(chapters, limit) {
    var left = chapters.reduce(function (s, c) { return s + c.left; }, 0);
    var z = Math.min(limit, left);
    var b = Math.ceil(z / 10) || 0;
    return { z: z, b: b, left: left,
      text: '本次抽取 ' + z + ' 道，共 ' + b + ' 批，预计 ' + b + ' 次模型请求；题干与选项会发送给模型，答案、解析与配图不发送。' };
  }
  function open(errMsg) {
    var terms = IW.lib.terms();
    var term = '八年级上册';
    var sel = {};
    IW.lib.chapterStats(term).forEach(function (c) { sel[c.name] = false; });
    function body() {
      var stats = IW.lib.chapterStats(term);
      var anyLeft = stats.some(function (c) { return c.left > 0; });
      var termOpts = Object.keys(terms).map(function (t) {
        var dis = !terms[t].has_candidates;
        return '<option value="' + esc(t) + '"' + (t === term ? ' selected' : '') + (dis ? ' disabled' : '') + '>' + esc(t) + (dis ? '（无候选题）' : '') + '</option>';
      }).join('');
      var rows = stats.map(function (c) {
        var dis = c.left === 0;
        return '<div class="x-row' + (dis ? ' dis' : '') + '"><input type="checkbox" data-ch="' + esc(c.name) + '"' +
          (sel[c.name] ? ' checked' : '') + (dis ? ' disabled' : '') + '>' +
          '<span class="cname">' + esc(c.name) + '</span>' +
          '<span class="cnums">符合 ' + c.fit + ' 道 · 已抽过 ' + c.extracted + ' 道 · 待抽 ' + c.left + ' 道 · 约 ' + c.req + ' 次请求</span></div>';
      }).join('');
      var chosen = stats.filter(function (c) { return sel[c.name]; });
      var defLimit = chosen.reduce(function (s, c) { return s + c.left; }, 0);
      var limEl = document.getElementById('x-limit');
      var lim = limEl ? (+limEl.value || 0) : defLimit;
      return { html: '<div class="x-head"><label>学期</label><select id="x-term">' + termOpts + '</select>' +
          '<label class="all-sel"><input type="checkbox" id="x-all"' + (anyLeft ? '' : ' disabled') + '> 全选有待抽题的章</label></div>' +
          '<div id="x-chapters">' + rows + '</div>' +
          '<div class="x-limit">本次上限 <input id="x-limit" type="number" min="1" max="' + defLimit + '" value="' + (lim || defLimit) + '"> 道（不超过所选章待抽总数 ' + defLimit + '）</div>' +
          '<div class="x-est" id="x-est">' + est(chosen, lim || defLimit).text + '</div>' +
          (errMsg ? '<div class="x-error">' + esc(errMsg) + '</div>' : '') +
          '<details class="x-rules"><summary>筛选规则（本机筛选，不产生费用）</summary><ol>' +
          RULES.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ol></details>' +
          '<div class="ops"><button class="btn" id="x-cancel">取消</button>' +
          '<button class="btn primary" id="x-ok"' + (chosen.length ? '' : ' disabled') + '>确认抽取</button></div>',
        chosen: chosen, defLimit: defLimit };
    }
    function paint() {
      var d = body();
      var box = document.getElementById('iw-dlg-body');
      if (box) { box.innerHTML = d.html; wire(); }
    }
    function wire() {
      document.getElementById('x-cancel').onclick = IW.hideDlg;
      document.getElementById('x-term').onchange = function () {
        term = this.value; sel = {};
        IW.lib.chapterStats(term).forEach(function (c) { sel[c.name] = false; });
        paint();
      };
      var all = document.getElementById('x-all');
      if (all) all.onchange = function () {
        IW.lib.chapterStats(term).forEach(function (c) { sel[c.name] = this.checked && c.left > 0; }, this);
        paint();
      };
      document.querySelectorAll('#x-chapters input').forEach(function (cb) {
        cb.onchange = function () { sel[cb.getAttribute('data-ch')] = cb.checked;
          var lim = document.getElementById('x-limit'); if (lim) lim.value = '';
          paint(); };
      });
      var lim = document.getElementById('x-limit');
      if (lim) lim.oninput = function () {
        var d = body();
        var chosen = IW.lib.chapterStats(term).filter(function (c) { return sel[c.name]; });
        var limv = Math.min(+lim.value || 0, d.defLimit);
        document.getElementById('x-est').textContent = est(chosen, limv).text;
      };
      document.getElementById('x-ok').onclick = function () {
        var stats = IW.lib.chapterStats(term);
        var chosen = stats.filter(function (c) { return sel[c.name]; });
        var limv = Math.min(+document.getElementById('x-limit').value || 0,
          chosen.reduce(function (s, c) { return s + c.left; }, 0));
        var e = est(chosen, limv);
        IW.showDlg('<h2 style="margin:0 0 10px;font-size:15px">确认抽取</h2>' +
          '<div class="x-est">' + esc(e.text) + '</div>' +
          '<div class="ops"><button class="btn" id="x2-cancel">取消</button>' +
          '<button class="btn primary" id="x2-ok">确认，提交任务</button></div>');
        document.getElementById('x2-cancel').onclick = function () { open(); };
        document.getElementById('x2-ok').onclick = function () {
          if (IW.lib.sw('bankChanged')) { open('题库已变化，请重新查看'); return; }
          IW.hideDlg(); runTask(chosen, e);
        };
      };
    }
    IW.showDlg('<h2 style="margin:0 0 10px;font-size:15px">从题库抽取情境</h2><div id="iw-dlg-body" class="x-dlg"></div>');
    document.getElementById('iw-dlg-box').classList.add('x-wide');
    paint();
  }

  function runTask(chapters, e) {
    IW.state.extracting = true;
    IW.render();
    var fl = document.createElement('div');
    fl.className = 'task-float'; fl.id = 'iw-task';
    document.body.appendChild(fl);
    var b = 0, produced = [];
    function tick() {
      b++;
      var last = b === e.b;
      var failNow = IW.lib.sw('failBatch2') && b === 2;
      fl.innerHTML = '<div class="tt">情境抽取</div><div class="tp"><i style="width:' + Math.round(b / e.b * 100) + '%"></i></div>' +
        '<div class="ts">' + (failNow ? '第 ' + b + ' 批失败，已停止' : '第 ' + b + ' / ' + e.b + ' 批') + '</div>';
      if (failNow) {
        fl.className = 'task-float fail';
        finish(produced, { failedBatch: b, skipped: e.b - b }, fl); return;
      }
      produced = produced.concat(genBatch(chapters, b, last ? e.z - 10 * (e.b - 1) : 10));
      var quota = last ? e.z - 10 * (e.b - 1) : 10;
      chapters.forEach(function (c) {
        if (quota > 0 && c.left > 0) {
          var take = Math.min(c.left, quota);
          IW.lib.addExtracted(c.name, take); c.left -= take; quota -= take;
        }
      });
      if (last) { fl.className = 'task-float done'; finish(produced, null, fl); return; }
      setTimeout(tick, 800);
    }
    setTimeout(tick, 400);
  }
  function genBatch(chapters, batchNo, qcount) {
    var pool = (window.ITEM_LIBRARY_DEMO && ITEM_LIBRARY_DEMO.pool) || {};
    var withLeft = chapters.filter(function (c) { return c.left > 0; });
    var list = withLeft.length ? withLeft : chapters;
    var n = 1 + (batchNo % 3), out = [];
    for (var i = 0; i < n; i++) {
      var ch = list[(batchNo + i) % list.length];
      var items = pool[ch.name] || [];
      if (!items.length) continue;
      var p = items[(batchNo + i) % items.length];
      var pending = (IW.extractSeq = (IW.extractSeq || 0) + 1) % 3 !== 0;
      out.push(IW.lib.add({ kind: '情境材料', title: p.title, situation: p.situation,
        section: p.section, chapter: ch.name, context_kind: p.context_kind,
        needs_figure: p.needs_figure, origin: 'demo', status: pending ? 'pending' : 'disabled',
        disabled_reason: pending ? null : '情境过于单薄，离开原题不成立（演示）',
        local_issues: out.length === 0 && batchNo === 1 && pending ? ['描述含数字'] : [],
        source_question_ids: [92000 + batchNo * 10 + i] }));
    }
    return out;
  }
  function finish(produced, fail, fl) {
    var pending = produced.filter(function (m) { return m.status === 'pending'; }).length;
    IW.state.extracting = false;
    IW.state.extractResult = { pending: pending, disabled: produced.length - pending,
      failedBatch: fail ? fail.failedBatch : 0, skipped: fail ? fail.skipped : 0 };
    if (window.setLibTab) window.setLibTab('pending');
    setTimeout(function () { if (fl.parentNode) fl.parentNode.removeChild(fl); IW.render(); }, 1200);
  }
  IW.extract = { open: open };
})();
