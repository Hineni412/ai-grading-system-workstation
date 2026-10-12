/* 启动：挂载已选方案（胶囊+浮层面板），接管顶部模拟控件 */
(function () {
  'use strict';
  var E = window.TCEngine;
  var center = window.TCCenter;

  center.mount(document.getElementById('variant-root'));

  document.querySelector('.chrome__sim').addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    if (b.id === 'speed-toggle') {
      E.speed = E.speed === 1 ? 4 : 1;
      b.textContent = '速度 ' + E.speed + '×';
    } else if (b.id === 'sim-clear') {
      window.TCShell.closeDialog();
      E.clear();
    } else if (b.dataset.sim) {
      window.TCShell.startFromSim(b.dataset.sim, b.dataset.fail === '1');
    }
  });

  E.subscribe(center.update);

  window.TCApp = {
    openList: function () { center.openList(); },
  };
})();
