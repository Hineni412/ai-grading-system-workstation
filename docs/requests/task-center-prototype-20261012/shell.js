/* 假应用外壳：侧栏导航、页面渲染、发起任务的对话框、轻提示 */
(function () {
  'use strict';
  var E = window.TCEngine;
  var H = { page: 'workbench' };

  /* ---- 侧栏与页面 ---- */
  function renderNav() {
    var nav = document.getElementById('sbar-nav');
    nav.innerHTML = window.TC_NAV.map(function (g) {
      return '<div class="sbar__nav-group"><p>' + g.group + '</p>' + g.items.map(function (it) {
        return '<a class="sbar__link" href="javascript:void(0)" data-page="' + it[0] + '"'
          + (H.page === it[0] ? ' aria-current="page"' : '')
          + '><span class="ico">' + it[2] + '</span><span>' + it[1] + '</span></a>';
      }).join('') + '</div>';
    }).join('');
    var set = document.getElementById('sbar-settings');
    set.innerHTML = window.TC_SETTINGS_NAV.map(function (it) {
      return '<a class="sbar__link" href="javascript:void(0)" data-page="' + it[0] + '"'
        + (H.page === it[0] ? ' aria-current="page"' : '')
        + '><span class="ico">' + it[2] + '</span><span>' + it[1] + '</span></a>';
    }).join('');
  }

  function renderPage() {
    var p = window.TC_PAGES[H.page];
    var root = document.getElementById('page-root');
    root.innerHTML = '<header class="page-head"><h1>' + p.title + '</h1><p>' + p.sub + '</p></header>'
      + '<div class="page-grid">' + p.cards.map(function (c) {
        return '<section class="fcard" id="fcard-' + c.id + '"><h3>' + c.title + '</h3>'
          + c.body.map(function (line) { return '<p>' + line + '</p>'; }).join('') + '</section>';
      }).join('') + '</div>';
  }

  H.navigate = function (pageId, highlight) {
    if (!window.TC_PAGES[pageId]) return;
    H.page = pageId;
    renderNav();
    renderPage();
    if (highlight) {
      var el = document.getElementById('fcard-go-card')
        || document.querySelector('.page-grid .fcard');
      if (el) el.classList.add('is-highlighted');
    }
    E.notify();
  };

  H.toast = function (msg) {
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.getElementById('toast-root').appendChild(el);
    setTimeout(function () { el.remove(); }, 3400);
  };

  /* ---- 发起任务的对话框 ---- */
  var dlg = { task: null, phase: null }; // phase: confirm | run | done
  var dlgRoot = function () { return document.getElementById('dialog-root'); };

  function closeDialog() {
    if (dlg.task) dlg.task.dialogOpen = false;
    dlg.task = null; dlg.phase = null;
    dlgRoot().innerHTML = '';
  }
  H.closeDialog = closeDialog;

  function dialogShell(title, bodyHtml, footHtml) {
    dlgRoot().innerHTML = '<div class="dlg-overlay"></div>'
      + '<div class="dlg" role="dialog" aria-modal="true" aria-label="' + title + '">'
      + '<header class="dlg__head"><h2>' + title + '</h2>'
      + '<button class="dlg__close" aria-label="关闭">×</button></header>'
      + '<div class="dlg__body">' + bodyHtml + '</div>'
      + '<footer class="dlg__foot">' + footHtml + '</footer></div>';
    dlgRoot().querySelector('.dlg__close').onclick = closeDialog;
    dlgRoot().querySelector('.dlg-overlay').onclick = closeDialog;
  }

  function openConfirm(tplId, fail) {
    closeDialog();
    var tpl = window.TC_TEMPLATES[tplId];
    dialogShell(tpl.name,
      '<p><strong>' + tpl.scope + '</strong></p>'
      + '<p class="fee">确认后将使用配置的 AI 模型，按服务商计费。</p>',
      '<button class="btn" data-x="cancel">取消</button>'
      + '<button class="btn btn--primary" data-x="ok">确认</button>');
    dlg.phase = 'confirm';
    dlgRoot().querySelector('[data-x="ok"]').onclick = function () {
      var t = E.start(tplId, { fail: fail });
      t.dialogOpen = true;
      dlg.task = t; dlg.phase = 'run';
      renderRunDialog();
    };
    dlgRoot().querySelector('[data-x="cancel"]').onclick = closeDialog;
  }

  function renderRunDialog() {
    var t = dlg.task;
    if (!t || dlg.phase !== 'run') return;
    var pct = Math.round(t.progress * 100);
    dialogShell(t.name,
      '<p><strong>' + t.scope + '</strong></p>'
      + '<p class="dlg__stage">' + E.stageText(t) + '</p>'
      + '<div class="bar"><i style="width:' + pct + '%"></i></div>'
      + '<div class="dlg__meta"><span>' + pct + '%</span>'
      + '<span>已用时 ' + E.fmtElapsed(t.elapsed) + '</span></div>',
      '<button class="btn btn--primary" data-x="bg">转到后台</button>');
    // 关闭（右上角 × 或点遮罩）与「转到后台」行为一致：任务在后台继续
    function toBackground() {
      closeDialog();
      H.toast('已在后台开始：' + t.name + '，可以去做别的事');
    }
    dlgRoot().querySelector('[data-x="bg"]').onclick = toBackground;
    dlgRoot().querySelector('.dlg__close').onclick = toBackground;
    dlgRoot().querySelector('.dlg-overlay').onclick = toBackground;
  }

  function renderDoneDialog() {
    var t = dlg.task;
    var o = t.outcome || { label: '已结束', tone: 'neutral', note: '' };
    dialogShell(t.name,
      '<div class="dlg__result"><span class="badge is-' + o.tone + '">' + o.label + '</span>'
      + (o.note ? '<p>' + o.note + '</p>' : '')
      + '<p class="titem__meta">已用时 ' + E.fmtElapsed(t.elapsed) + '</p></div>',
      '<button class="btn" data-x="close">关闭</button>'
      + '<button class="btn btn--primary" data-x="goto">去查看</button>');
    dlgRoot().querySelector('[data-x="goto"]').onclick = function () {
      closeDialog();
      window.TCShared.goTo(t);
    };
    dlgRoot().querySelector('[data-x="close"]').onclick = closeDialog;
  }

  E.subscribe(function () {
    if (!dlg.task || dlg.phase !== 'run') return;
    if (E.finished(dlg.task)) {
      dlg.phase = 'done';
      renderDoneDialog();
    } else {
      renderRunDialog();
    }
  });

  /* ---- 模拟入口 ---- */
  H.startFromSim = function (tplId, fail) {
    var tpl = window.TC_TEMPLATES[tplId];
    if (tpl.kind === 'short') {
      var t = E.start(tplId, {});
      H.toast('已在后台开始：' + t.name + '，可以去做别的事');
      return;
    }
    H.navigate(tpl.origin, false);
    openConfirm(tplId, fail);
  };

  /* ---- 事件绑定与初始化 ---- */
  document.getElementById('sbar-nav').addEventListener('click', function (ev) {
    var a = ev.target.closest('[data-page]');
    if (a) H.navigate(a.dataset.page, false);
  });
  document.getElementById('sbar-settings').addEventListener('click', function (ev) {
    var a = ev.target.closest('[data-page]');
    if (a) H.navigate(a.dataset.page, false);
  });
  renderNav();
  renderPage();

  window.TCShell = H;
})();
