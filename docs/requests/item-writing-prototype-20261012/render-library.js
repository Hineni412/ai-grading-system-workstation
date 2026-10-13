/* 命题训练原型 - 情境库屏：材料卡、状态页签、筛选、编辑/录入/命题要求弹窗、我的作品 */
(function () {
  'use strict';
  var st = { tab: 'available', kind: '', chapter: '' };
  var TABS = [['available', '可用'], ['pending', '待核对'], ['disabled', '已停用']];

  function esc(s) { return IW.esc(s); }
  function refresh() { IW.render(); }
  function dlg(title, html) {
    IW.showDlg('<h2 style="margin:0 0 10px;font-size:15px">' + esc(title) + '</h2>' + html);
  }

  function cardActions(m) {
    var b = '';
    if (m.status === 'pending') b += '<button class="mini-btn" data-ok="' + m.id + '">标为可用</button>';
    if (m.status === 'disabled') b += '<button class="mini-btn" data-ok="' + m.id + '">改为可用</button>';
    if (m.status === 'available') {
      b += '<button class="mini-btn pri" data-start="' + m.id + '">' +
        (IW.loadWork(m.id) ? '继续命题' : '开始命题') + '</button>';
      if (m.id === 'm1') b += '<button class="mini-btn" data-sample="' + m.id + '">载入示例作品</button>';
      b += '<button class="mini-btn" data-edit="' + m.id + '">编辑</button>' +
        '<button class="mini-btn" data-off="' + m.id + '">停用</button>';
    }
    if (m.status === 'pending') {
      b += '<button class="mini-btn" data-edit="' + m.id + '">编辑</button>' +
        '<button class="mini-btn" data-off="' + m.id + '">停用</button>';
    }
    return b;
  }
  function cardHtml(m) {
    var tags = '<span class="tag blue">' + esc(m.kind) + '</span>';
    if (m.context_kind) tags += '<span class="tag">' + esc(m.context_kind) + '</span>';
    if (m.status === 'pending') tags += '<span class="tag orange">待核对</span>';
    if (m.needs_figure) tags += '<span class="tag orange">需配图</span>';
    if (m.origin === 'demo') tags += '<span class="tag grey">演示：非真实抽取</span>';
    if (m.origin === 'ai_extract') tags += '<span class="tag green">题库抽取</span>';
    var meta = '';
    if (m.section) meta += '<div>来源：' + esc(m.chapter || '') + ' · ' + esc(m.section) + '</div>';
    if (m.source_question_ids && m.source_question_ids.length)
      meta += '<div>来源 ' + m.source_question_ids.length + ' 题，原题评价后显示</div>';
    if (m.local_issues && m.local_issues.length)
      meta += '<div class="warn">待核对：' + esc(m.local_issues.join('、')) + '</div>';
    if (m.status === 'disabled' && m.disabled_reason)
      meta += '<div class="off-r">停用原因：' + esc(m.disabled_reason) + '</div>';
    var rev = (m.revision || 1) > 1 ? '<span class="rev-badge">r' + m.revision + '</span>' : '';
    return '<div class="lib-card' + (m.status === 'disabled' ? ' off' : '') + '">' + rev +
      '<div class="lib-tags">' + tags + '</div><h4>' + esc(m.title) + '</h4>' +
      '<div class="lib-snip">' + esc((m.situation || (m.sentences && m.sentences[0]) || '').slice(0, 80)) + '</div>' +
      '<div class="lib-meta">' + meta + '</div><div class="lib-actions">' + cardActions(m) + '</div></div>';
  }
  function worksHtml() {
    var rows = IW.lib.works().map(function (e) {
      var w = e.work, latest = IW.lib.review(e.materialId);
      var vs = IW.lib.reviews(e.materialId).map(function (r) {
        return 'v' + r.version + ' ' + r.total + '分'; });
      var status;
      if (latest && latest.transferred) status = '已转入题库 ' + latest.transferred;
      else if (latest) status = '已评价 第 ' + latest.version + ' 版 ' + latest.total + ' 分';
      else status = '作答中 ' + IW.doneCount(w) + '/8 步';
      var title = (w.taskCard && w.taskCard.materialSnapshot && w.taskCard.materialSnapshot.title) ||
        ((IW.lib.byId(e.materialId) || {}).title) || e.materialId;
      return '<div class="work-row"><span class="wt">' + esc(title) + '</span>' +
        '<span class="ws">' + esc(status) + '</span>' +
        '<span class="wv">' + (vs.length ? '版本：' + vs.join('，') : '尚无评价') + '</span>' +
        '<button class="mini-btn" data-resume="' + e.materialId + '">继续</button></div>';
    });
    if (!rows.length) rows.push('<div class="work-row"><span class="ws">还没有保存的作品；点“开始命题”建一份。</span></div>');
    return '<div class="card" style="margin-top:14px"><header><h2>我的作品</h2>' +
      '<span class="hint">成长档案 · 只列版本与总分</span></header><div class="body">' + rows.join('') + '</div></div>';
  }
  function filtered() {
    return IW.lib.list().filter(function (m) {
      return m.status === st.tab && (!st.kind || m.context_kind === st.kind) &&
        (!st.chapter || m.chapter === st.chapter);
    });
  }
  function chapterOptions() {
    var set = [];
    IW.lib.list().forEach(function (m) { if (m.chapter && set.indexOf(m.chapter) < 0) set.push(m.chapter); });
    return set;
  }
  window.renderMaterials = function () {
    var c = IW.lib.counts();
    var h = '<div class="lib-tabs">' + TABS.map(function (t) {
      return '<button class="lib-tab' + (st.tab === t[0] ? ' on' : '') + '" data-ltab="' + t[0] + '">' +
        t[1] + '<em>' + (c[t[0]] || 0) + '</em></button>'; }).join('') + '</div>';
    h += '<div class="lib-tools"><button class="mini-btn pri" data-extract' +
      (IW.state.extracting ? ' disabled' : '') + '>' + (IW.state.extracting ? '抽取中' : '从题库抽取情境') + '</button>' +
      '<button class="mini-btn" data-enter>录入材料</button>' +
      '<select data-fkind><option value="">全部情境类别</option>' +
      ['生活情境', '数学文化', '科学跨学科'].map(function (k) {
        return '<option' + (st.kind === k ? ' selected' : '') + '>' + k + '</option>'; }).join('') + '</select>' +
      '<select data-fchapter><option value="">全部章</option>' + chapterOptions().map(function (k) {
        return '<option' + (st.chapter === k ? ' selected' : '') + '>' + esc(k) + '</option>'; }).join('') + '</select></div>';
    if (IW.state.extractResult) {
      var r = IW.state.extractResult;
      h += '<div class="extract-result"><span>情境抽取完成：新增待核对 ' + r.pending + ' 份、不适用 ' + r.disabled + ' 道' +
        (r.failedBatch ? '、第 ' + r.failedBatch + ' 批失败' +
          (r.skipped ? '，后续 ' + r.skipped + ' 批未执行' : '') +
          '；已完成的批次已保存，未完成的题下次仍会出现在待抽中' : '、无失败批次') +
        '</span><span class="x" data-close-result>✕</span></div>';
    }
    var list = filtered();
    h += '<div class="lib-grid">' + list.map(cardHtml).join('') +
      '<div class="lib-card upload"><h4>上传我的材料</h4><div class="lib-meta">正式版支持录入或上传 Word、PDF、图片</div></div></div>';
    if (!list.length) h += '<div class="iw-banner" style="margin-top:10px">当前筛选下没有材料。</div>';
    return h + worksHtml();
  };

  function editDialog(m) {
    var kinds = ['生活情境', '数学文化', '科学跨学科', '无（纯数学）'];
    dlg('编辑材料', '<div class="tc-grid">' +
      '<label>标题</label><input id="ed-title" value="' + esc(m.title) + '">' +
      '<label>描述</label><textarea id="ed-sit" rows="4" class="tc-ta">' +
        esc(m.situation || m.sentences.join('')) + '</textarea>' +
      '<label>情境类别</label><select id="ed-kind">' + kinds.map(function (k) {
        return '<option' + (m.context_kind === k ? ' selected' : '') + '>' + k + '</option>'; }).join('') + '</select></div>' +
      '<div class="ops"><button class="btn" id="ed-cancel">取消</button>' +
      '<button class="btn primary" id="ed-save">保存（修订号 +1）</button></div>');
    document.getElementById('ed-cancel').addEventListener('click', IW.hideDlg);
    document.getElementById('ed-save').addEventListener('click', function () {
      IW.lib.update(m.id, { title: document.getElementById('ed-title').value.trim() || m.title,
        situation: document.getElementById('ed-sit').value.trim(),
        context_kind: document.getElementById('ed-kind').value });
      IW.hideDlg(); refresh();
    });
  }
  function enterDialog() {
    var secs = [];
    ITEM_CATALOG.forEach(function (ch) { ch.sections.forEach(function (s) { secs.push({ name: s, chapter: ch.chapter }); }); });
    dlg('录入材料', '<div class="tc-grid">' +
      '<label>类型</label><select id="en-kind"><option>情境素材</option><option>母题</option><option>主题限定</option></select>' +
      '<label>标题</label><input id="en-title" placeholder="材料标题">' +
      '<label>正文</label><textarea id="en-text" rows="5" class="tc-ta" placeholder="粘贴或录入材料正文"></textarea>' +
      '<label>情境类别</label><select id="en-ckind"><option>生活情境</option><option>数学文化</option><option>科学跨学科</option><option>无（纯数学）</option></select>' +
      '<label>小节</label><select id="en-sec">' + secs.map(function (s) { return '<option>' + esc(s.name) + '</option>'; }).join('') + '</select></div>' +
      '<div class="ops"><button class="btn" id="en-cancel">取消</button>' +
      '<button class="btn primary" id="en-save">保存为可用</button></div>');
    document.getElementById('en-cancel').addEventListener('click', IW.hideDlg);
    document.getElementById('en-save').addEventListener('click', function () {
      var secName = document.getElementById('en-sec').value;
      var sec = secs.filter(function (s) { return s.name === secName; })[0];
      IW.lib.add({ kind: document.getElementById('en-kind').value,
        title: document.getElementById('en-title').value.trim() || '未命名材料',
        situation: document.getElementById('en-text').value.trim(),
        context_kind: document.getElementById('en-ckind').value,
        section: secName, chapter: sec ? sec.chapter : '', origin: 'entered', status: 'available' });
      st.tab = 'available'; IW.hideDlg(); refresh();
    });
  }
  function taskCardDialog(m, loadSample) {
    var req = m.requirement || {};
    dlg('命题要求', '<div class="hint" style="margin-bottom:6px">为「' + esc(m.title) +
      '」设定本次命题要求；材料快照与要求写入作品，之后材料被编辑不影响已开作品。</div>' +
      '<div class="tc-grid">' +
      '<label>范围</label><input id="tc-scope" value="' + esc(req.scope || m.chapter || '第四章 一次函数') + '">' +
      '<label>题型</label><select id="tc-type"><option>解答题</option><option>选择题</option><option>填空题</option></select>' +
      '<label>分值</label><input id="tc-score" type="number" min="1" max="30" value="' + (req.score || 10) + '">' +
      '<label>小问</label><input id="tc-parts" value="' + esc(req.parts || '2–3 问') + '">' +
      '<label>情境要求</label><input id="tc-ctx" value="' + esc(req.context || m.context_kind || '实际生活情境') + '">' +
      '<label>限时</label><input id="tc-min" type="number" min="10" max="180" value="' + (req.minutes || 60) + '"></div>' +
      '<div class="ops"><button class="btn" id="tc-cancel">取消</button>' +
      '<button class="btn primary" id="tc-ok">确定，开始命题</button></div>');
    document.getElementById('tc-cancel').addEventListener('click', IW.hideDlg);
    document.getElementById('tc-ok').addEventListener('click', function () {
      var requirement = { scope: document.getElementById('tc-scope').value.trim(),
        type: document.getElementById('tc-type').value,
        score: +document.getElementById('tc-score').value || 10,
        parts: document.getElementById('tc-parts').value.trim() || '2–3 问',
        context: document.getElementById('tc-ctx').value.trim(),
        minutes: +document.getElementById('tc-min').value || 60 };
      IW.hideDlg(); IW.startWork(m, requirement, loadSample);
    });
  }
  window.setLibTab = function (t) { st.tab = t; };
  window.bindMaterials = function () {
    document.querySelectorAll('[data-ltab]').forEach(function (b) {
      b.addEventListener('click', function () { st.tab = b.getAttribute('data-ltab'); refresh(); });
    });
    document.querySelectorAll('[data-fkind]').forEach(function (s) {
      s.addEventListener('change', function () { st.kind = s.value; refresh(); });
    });
    document.querySelectorAll('[data-fchapter]').forEach(function (s) {
      s.addEventListener('change', function () { st.chapter = s.value; refresh(); });
    });
    document.querySelectorAll('[data-close-result]').forEach(function (b) {
      b.addEventListener('click', function () { IW.state.extractResult = null; refresh(); });
    });
    document.querySelectorAll('[data-extract]').forEach(function (b) {
      if (!b.disabled) b.addEventListener('click', function () { IW.extract.open(); });
    });
    document.querySelectorAll('[data-enter]').forEach(function (b) {
      b.addEventListener('click', enterDialog);
    });
    document.querySelectorAll('[data-edit]').forEach(function (b) {
      b.addEventListener('click', function () { editDialog(IW.lib.byId(b.getAttribute('data-edit'))); });
    });
    document.querySelectorAll('[data-ok]').forEach(function (b) {
      b.addEventListener('click', function () { IW.lib.setStatus(b.getAttribute('data-ok'), 'available'); refresh(); });
    });
    document.querySelectorAll('[data-off]').forEach(function (b) {
      b.addEventListener('click', function () { IW.lib.setStatus(b.getAttribute('data-off'), 'disabled', '教师手动停用'); refresh(); });
    });
    document.querySelectorAll('[data-start]').forEach(function (b) {
      b.addEventListener('click', function () {
        var m = IW.lib.byId(b.getAttribute('data-start'));
        if (IW.loadWork(m.id)) { IW.resumeWork(m.id); return; }
        taskCardDialog(m, false);
      });
    });
    document.querySelectorAll('[data-sample]').forEach(function (b) {
      b.addEventListener('click', function () { taskCardDialog(IW.lib.byId('m1'), true); });
    });
    document.querySelectorAll('[data-resume]').forEach(function (b) {
      b.addEventListener('click', function () { IW.resumeWork(b.getAttribute('data-resume')); });
    });
  };
})();
