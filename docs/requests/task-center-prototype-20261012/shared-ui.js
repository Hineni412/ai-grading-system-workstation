/* 方案共用的渲染助手：胶囊、任务条目、分组、完成通知卡片 */
(function () {
  'use strict';
  var E = window.TCEngine;
  var S = {};



  S.esc = function (s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };

  S.badge = function (t) {
    if (E.finished(t)) {
      var o = t.outcome || { label: '已结束', tone: 'neutral' };
      return '<span class="badge is-' + o.tone + '">' + S.esc(o.label) + '</span>';
    }
    if (t.status === 'cancelling') return '<span class="badge is-neutral">正在取消</span>';
    return '<span class="badge is-run">进行中</span>';
  };

  // 单条任务 HTML；opts.rich 追加阶段历史（抽屉用）
  S.titem = function (t, opts) {
    opts = opts || {};
    var h = '<div class="titem" data-id="' + t.id + '">';
    h += '<div class="titem__line"><strong>' + S.esc(t.name) + '</strong>' + S.badge(t) + '</div>';
    h += '<p class="titem__scope">' + S.esc(t.scope) + '</p>';
    var detail = E.finished(t) ? (t.outcome && t.outcome.note || '') : E.stageText(t);
    if (detail) h += '<p class="titem__detail">' + S.esc(detail) + '</p>';
    if (E.active(t)) {
      var pct = Math.round(t.progress * 100);
      h += '<div class="titem__row"><span class="bar"><i style="width:' + pct + '%"></i></span>'
        + '<span class="titem__pct">' + pct + '%</span>';
      if (t.status !== 'cancelling') {
        h += '<span class="titem__actions"><button class="btn btn--ghost btn--small" data-act="cancel">取消任务</button></span>';
      }
      h += '</div>';
    }
    var meta = '已用时 ' + E.fmtElapsed(t.elapsed) + ' · 开始于 ' + t.startedClock;
    if (E.finished(t) && t.read) meta += ' · 已查看 · 稍后自动移出列表';
    h += '<div class="titem__row"><span class="titem__meta">' + meta + '</span><span class="titem__actions">';
    if (E.finished(t)) {
      if (t.download) h += '<button class="btn btn--small" data-act="download">下载</button>';
      h += '<button class="btn btn--primary btn--small" data-act="goto">去查看</button>';
    }
    h += '</span></div>';
    if (opts.rich && t.stageLog.length) {
      var log = t.stageLog.slice(-3);
      h += '<ul class="titem__stages">' + log.map(function (s, i) {
        return '<li class="' + (i === log.length - 1 ? 'is-now' : '') + '">' + S.esc(s.label)
          + ' · ' + E.fmtElapsed(s.at) + '</li>';
      }).join('') + '</ul>';
    }
    return h + '</div>';
  };

  S.bindActions = function (root) {
    root.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-act]');
      if (!btn) return;
      var item = ev.target.closest('[data-id]');
      var t = item && E.byId(Number(item.dataset.id));
      if (!t) return;
      var act = btn.dataset.act;
      if (act === 'cancel') E.cancel(t.id);
      else if (act === 'goto') S.goTo(t);
      else if (act === 'download') S.download(t);
    });
  };

  S.goTo = function (t) {
    E.markRead(t.id);
    window.TCShell.navigate(t.goTo, true);
  };
  S.download = function (t) {
    E.markRead(t.id);
    window.TCShell.toast('已开始下载：' + (t.download || '文件'));
  };

  // 分组：进行中 / 需要处理 / 已结束（正在移出的不再显示）
  S.groups = function () {
    var run = [], attention = [], done = [];
    E.tasks.forEach(function (t) {
      if (t.expiring) return;
      if (E.active(t)) run.push(t);
      else if (E.needsAttention(t)) attention.push(t);
      else done.push(t);
    });
    return [
      { title: '进行中', items: run },
      { title: '需要处理', items: attention },
      { title: '已结束', items: done },
    ];
  };

  S.LIST_FOOT = '<p class="list-foot">已查看的完成任务会在 10 分钟后自动移出列表'
    + '（原型缩短为约 1 分钟），不影响已保存的结果。</p>';

  S.groupedList = function (opts) {
    var h = '', any = false;
    S.groups().forEach(function (g) {
      if (!g.items.length) return;
      any = true;
      h += '<div class="tgroup"><p>' + g.title + '（' + g.items.length + '）</p>'
        + g.items.map(function (t) { return S.titem(t, opts); }).join('') + '</div>';
    });
    if (!any) return '<p class="panel__empty">当前没有任务</p>';
    return h + S.LIST_FOOT;
  };

  /* ---- 胶囊（40px 圆钮 + 进度环，悬停展开一行文字） ---- */
  var RING_C = 2 * Math.PI * 8;
  S.pillState = function () {
    var run = E.running(), un = E.unread();
    var parts = [], avg = 0, ring = 'idle';
    if (run.length) {
      avg = Math.round(run.reduce(function (s, t) { return s + t.progress; }, 0) / run.length * 100);
      parts.push(run.length + ' 个任务进行中，平均 ' + avg + '%');
      ring = 'run';
    }
    if (un.length) {
      parts.push(un.length + ' 项结果待查看');
      if (!run.length) ring = un.some(E.needsAttention) ? 'warn' : 'ok';
    }
    if (!parts.length) parts.push('没有未读的任务结果');
    return { running: run.length, avg: avg, unread: un.length, ring: ring, text: parts.join('；') };
  };
  S.pillText = function () { return S.pillState().text; };
  S.pillHtml = function () {
    var st = S.pillState();
    var color = st.ring === 'run' ? 'var(--accent)'
      : st.ring === 'ok' ? 'var(--success)'
      : st.ring === 'warn' ? 'var(--warning)' : 'var(--border-strong)';
    var frac = st.ring === 'run' ? st.avg / 100 : 1;
    var mid = st.ring === 'ok' ? '✓' : st.ring === 'warn' ? '!' : st.ring === 'idle' ? '☷' : '';
    var off = RING_C * (1 - frac);
    return '<span class="pill__ring"><svg width="20" height="20">'
      + '<circle class="trk" cx="10" cy="10" r="8"/>'
      + '<circle class="val" cx="10" cy="10" r="8" style="stroke:' + color + '"'
      + ' stroke-dasharray="' + RING_C.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1)
      + '"/></svg>'
      + (mid ? '<span class="mid" style="color:' + color + '">' + mid + '</span>' : '')
      + '</span>'
      + '<span class="pill__text">' + st.text + '</span>'
      + (st.unread ? '<span class="pill__dot">' + st.unread + '</span>' : '');
  };
  S.pillMode = function () { return E.tasks.length ? 'on' : 'hidden'; };

  /* ---- 完成通知卡片（A/C/D 共用） ----
     倒计时用 CSS 动画驱动：动画可以在新插入元素上直接播放，
     悬停由 .card:hover 的 animation-play-state 暂停，
     animationend 触发收起，条与收起永不脱节 */
  var CARD_MAX = 3;
  S.showCard = function (t) {
    var root = document.getElementById('cards-root');
    while (root.children.length >= CARD_MAX) root.removeChild(root.firstChild);
    var o = t.outcome || { label: '已结束', tone: 'neutral', note: '' };
    var el = document.createElement('div');
    el.className = 'card';
    el.dataset.id = t.id;
    el.innerHTML = '<div class="card__head"><strong>' + S.esc(t.name) + '</strong>'
      + '<span class="badge is-' + o.tone + '">' + S.esc(o.label) + '</span>'
      + '<button class="card__close" aria-label="关闭">×</button></div>'
      + (o.note ? '<p class="card__note">' + S.esc(o.note) + '</p>' : '')
      + '<div class="card__foot"><button class="btn btn--primary btn--small" data-act="goto">去查看</button>'
      + '<span class="titem__meta">已用时 ' + E.fmtElapsed(t.elapsed) + '</span></div>'
      + '<span class="card__life"><i class="is-' + o.tone + '"></i></span>';
    root.appendChild(el);
    function collapse() {
      el.classList.add('is-out');
      setTimeout(function () { el.remove(); }, 260);
    }
    el.querySelector('.card__life > i').addEventListener('animationend', collapse);
    el.querySelector('.card__close').addEventListener('click', function () {
      if (!E.needsAttention(t)) E.markRead(t.id);
      collapse();
    });
    S.bindActions(el);
  };
  S.clearCards = function () {
    document.getElementById('cards-root').innerHTML = '';
  };

  // 「自动移出列表」动画：给命中的条目加 is-out，期间抑制列表重建，结束后重渲染
  S.bindExpiring = function (container, suppress, rerender) {
    return E.on('expiring', function (t) {
      var el = container && container.querySelector('[data-id="' + t.id + '"]');
      if (el) el.classList.add('is-out');
      suppress.until = Math.max(suppress.until, Date.now() + 340);
      setTimeout(rerender, 360);
    });
  };
  S.suppressed = function (suppress) { return Date.now() < suppress.until; };

  window.TCShared = S;
})();
