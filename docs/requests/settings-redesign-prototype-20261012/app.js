/* 设置页改版原型 · 页面骨架与页签切换 */
'use strict';
window.ProtoApp = (function () {
  var state = { variant: 'a', tab: 'log' };
  var TABS_A = [
    { key: 'log', label: '调用记录' },
    { key: 'ai', label: 'AI 服务' },
    { key: 'students', label: '学生名单' },
    { key: 'data', label: '数据与空间' },
  ];
  var TABS_B = TABS_A.filter(function (t) { return t.key !== 'log'; });
  var modules = {};

  /* ---------- 工具 ---------- */
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'html') el.innerHTML = v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (k === 'disabled') { if (v) el.disabled = true; }
        else el.setAttribute(k, v);
      });
    }
    appendKids(el, Array.isArray(children) ? children : [children]);
    return el;
  }

  function appendKids(el, kids) {
    kids.forEach(function (c) {
      if (c == null || c === false) return;
      if (Array.isArray(c)) { appendKids(el, c); return; }
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    });
  }

  var toastTimer = null;
  function toast(msg) {
    var root = document.getElementById('toast-root');
    root.innerHTML = '';
    var el = h('div', { class: 'proto-toast', role: 'status', text: msg });
    root.append(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.remove(); }, 2400);
  }

  function badge(tone, label) {
    return h('span', { class: 'status-badge', 'data-tone': tone, text: label });
  }

  /* ---------- 页签 ---------- */
  function tabsDef() { return state.variant === 'a' ? TABS_A : TABS_B; }

  function isFillTab() {
    return state.tab === 'log' || (state.tab === 'ai' && state.variant === 'b');
  }

  function renderTabs() {
    var nav = document.getElementById('page-tabs');
    nav.innerHTML = '';
    tabsDef().forEach(function (t) {
      nav.append(h('button', {
        type: 'button', role: 'tab',
        class: state.tab === t.key ? 'is-active' : '',
        'aria-selected': state.tab === t.key ? 'true' : 'false',
        onclick: function () { setTab(t.key); },
      }, t.label));
    });
  }

  function setTab(key) {
    state.tab = key;
    apply();
  }

  function setVariant(v) {
    if (state.variant === v) return;
    state.variant = v;
    if (v === 'b' && state.tab === 'log') state.tab = 'ai';
    apply();
  }

  function apply() {
    renderTabs();
    var content = document.getElementById('settings-content');
    content.classList.toggle('settings-content--fill', isFillTab());

    var panes = {
      log: document.getElementById('pane-log'),
      ai: document.getElementById('pane-ai'),
      students: document.getElementById('pane-students'),
      data: document.getElementById('pane-data'),
    };
    panes.log.hidden = !(state.tab === 'log' && state.variant === 'a');
    panes.ai.hidden = state.tab !== 'ai';
    panes.students.hidden = state.tab !== 'students';
    panes.data.hidden = state.tab !== 'data';

    /* 对比开关只在 调用记录 / AI 服务 两个页签显示 */
    var compare = document.getElementById('compare-bar');
    compare.hidden = !(state.tab === 'log' || state.tab === 'ai');
    document.querySelectorAll('#compare-seg button').forEach(function (b) {
      b.classList.toggle('is-active', b.dataset.variant === state.variant);
    });

    if (!panes.log.hidden) modules.log.mount(panes.log);
    if (!panes.ai.hidden) {
      if (state.variant === 'a') modules.ai.mountA(panes.ai);
      else modules.ai.mountB(panes.ai);
    }
    if (!panes.students.hidden) modules.students.mount(panes.students);
    if (!panes.data.hidden) modules.data.mount(panes.data);
  }

  function init() {
    document.querySelectorAll('#compare-seg button').forEach(function (b) {
      b.addEventListener('click', function () { setVariant(b.dataset.variant); });
    });
    apply();
  }

  return {
    h: h,
    toast: toast,
    badge: badge,
    init: init,
    modules: modules,
    state: state,
    data: window.PROTO_DATA,
  };
})();
