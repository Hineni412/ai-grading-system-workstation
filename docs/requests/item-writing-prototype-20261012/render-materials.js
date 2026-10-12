/* 命题训练原型 - 选材料（起始屏） */
(function () {
  'use strict';

  function reqText(r) {
    return '范围：' + r.scope + '<br>题型：' + r.type + ' · 分值：' + r.score +
      ' 分 · 小问：' + r.parts + '<br>情境：' + r.context + ' · 限时 ' + r.minutes + ' 分钟';
  }

  window.renderMaterials = function () {
    var esc = window.IW.esc;
    var cards = window.ITEM_MATERIALS.map(function (m) {
      var has = !!window.IW.loadWork(m.id);
      var ops = '<button class="btn primary" data-start="' + m.id + '">' +
        (has ? '继续命题' : '开始命题') + '</button>';
      if (m.id === 'm1') ops += '<button class="btn" data-sample="m1">载入示例作品</button>';
      return '<div class="iw-mcard"><span class="iw-kind">' + esc(m.kind) + '</span>' +
        '<h3>' + esc(m.title) + '</h3>' +
        '<div class="snip">' + esc(m.sentences[0]) + '</div>' +
        '<div class="req">' + reqText(m.requirement) + '</div>' +
        '<div class="ops">' + ops + '</div></div>';
    }).join('');
    cards += '<div class="iw-mcard off"><span class="iw-kind">上传</span><h3>上传我的材料</h3>' +
      '<div class="req">正式版支持录入或上传 Word、PDF、图片</div>' +
      '<div class="ops"><button class="btn" disabled>上传我的材料</button></div></div>';
    return '<div class="card"><header><h2>选材料</h2>' +
      '<span class="hint">读一份给定材料，完成一道题的命制</span></header>' +
      '<div class="body"><div class="iw-mats">' + cards + '</div></div></div>';
  };

  window.bindMaterials = function () {
    document.querySelectorAll('[data-start]').forEach(function (b) {
      b.addEventListener('click', function () { window.IW.startWork(b.dataset.start, false); });
    });
    document.querySelectorAll('[data-sample]').forEach(function (b) {
      b.addEventListener('click', function () { window.IW.startWork(b.dataset.sample, true); });
    });
  };
})();
