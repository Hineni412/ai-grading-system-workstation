/* 命题训练原型 - 页脚“原型演示开关”：模拟失败路径与重置 */
window.IW = window.IW || {};
(function () {
  'use strict';
  var ITEMS = [
    ['failBatch2', '模拟第 2 批抽取失败'],
    ['bankChanged', '模拟题库已变化'],
    ['failStep2', '模拟 AI 评价在第 2 步失败'],
    ['ocrOff', '模拟识别不可用']
  ];
  IW.renderControls = function () {
    var host = document.getElementById('iw-switches');
    if (!host) return;
    host.innerHTML = '<div class="sw-list">' + ITEMS.map(function (it) {
      return '<label><input type="checkbox" data-sw="' + it[0] + '"' + (IW.lib.sw(it[0]) ? ' checked' : '') + '> ' + it[1] + '</label>';
    }).join('') + '</div><button class="mini-btn" id="iw-reset">重置原型数据</button>' +
      '<span class="iw-meta" style="margin-left:8px">清掉本原型在浏览器的全部保存（作品、材料改动、开关）。</span>';
    host.querySelectorAll('[data-sw]').forEach(function (cb) {
      cb.onchange = function () { IW.lib.setSw(cb.getAttribute('data-sw'), cb.checked); };
    });
    document.getElementById('iw-reset').onclick = function () {
      IW.lib.resetAll(); location.reload();
    };
  };
})();
