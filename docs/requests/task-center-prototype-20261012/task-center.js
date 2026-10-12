/* 已选方案：右下角环钮胶囊 + 锚定浮层面板（进行中 / 需要处理 / 已结束） */
(function () {
  'use strict';
  var E = window.TCEngine, S = window.TCShared;
  var pill, panel, open = false, offs = [], docOff = null;
  var suppress = { until: 0 };

  function render() {
    var mode = S.pillMode();
    pill.style.display = mode === 'hidden' ? 'none' : '';
    pill.className = 'pill';
    pill.innerHTML = S.pillHtml();
    pill.title = S.pillText();
    pill.setAttribute('aria-label', S.pillText());
    if (open && !S.suppressed(suppress)) {
      panel.querySelector('.panel__body').innerHTML = S.groupedList();
    }
    var clearBtn = panel.querySelector('[data-x="clear-done"]');
    if (clearBtn) clearBtn.disabled = !E.tasks.some(E.finished);
  }

  function setOpen(v) {
    open = v;
    panel.style.display = v ? '' : 'none';
    if (v) { E.markAllRead(); render(); }
  }

  function clearDone() {
    E.tasks = E.tasks.filter(E.active);
    E.notify();
  }

  window.TCCenter = {
    mount: function (root) {
      open = false;
      pill = document.createElement('button');
      pill.className = 'pill';
      pill.setAttribute('aria-label', '任务中心');
      panel = document.createElement('div');
      panel.className = 'panel';
      panel.style.display = 'none';
      panel.innerHTML = '<div class="panel__head"><strong>任务中心</strong>'
        + '<button class="btn btn--ghost btn--small" data-x="clear-done">清除已结束</button></div>'
        + '<div class="panel__body"></div>';
      root.appendChild(pill);
      root.appendChild(panel);
      pill.onclick = function () { setOpen(!open); };
      panel.querySelector('[data-x="clear-done"]').onclick = clearDone;
      S.bindActions(panel);
      docOff = function (ev) {
        if (open && !panel.contains(ev.target) && !pill.contains(ev.target)) setOpen(false);
      };
      document.addEventListener('mousedown', docOff);
      offs.push(E.on('finished', function (t) { if (E.wantsCard(t)) S.showCard(t); }));
      offs.push(E.on('cleared', function () { setOpen(false); S.clearCards(); }));
      offs.push(S.bindExpiring(panel, suppress, render));
      render();
    },
    update: render,
    openList: function () { setOpen(true); },
  };
})();
