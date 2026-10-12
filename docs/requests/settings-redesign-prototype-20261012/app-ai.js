/* 设置页改版原型 · AI 服务（方案 A 独立页签 / 方案 B 与调用记录同页） */
'use strict';
(function () {
  var A = window.ProtoApp;
  var D = A.data;

  var saved = {};
  D.tasks.forEach(function (t) { saved[t.key] = { account: t.account, model: t.model }; });
  var draft = JSON.parse(JSON.stringify(saved));
  var savedShown = false;
  var stripRefs = [];

  function isDirty() {
    return D.tasks.some(function (t) {
      return draft[t.key].account !== saved[t.key].account || draft[t.key].model !== saved[t.key].model;
    });
  }
  function usageFor(accName) {
    var titles = D.tasks.filter(function (t) { return draft[t.key].account === accName; })
      .map(function (t) { return t.title; });
    return titles.length ? '用于：' + titles.join('、') : '暂未用于任何任务';
  }

  /* ---------- 配置面板（A / B 展开共用） ---------- */
  function taskBlock(t, onChange) {
    var h = A.h;
    var sel = h('select', {
      class: 'app-input', 'aria-label': t.title + '使用的服务账号',
      onchange: function (e) { draft[t.key].account = e.target.value; onChange(); },
    }, D.accounts.map(function (a) {
      return h('option', { value: a.name, selected: a.name === draft[t.key].account ? '' : null }, a.name);
    }));
    var input = h('input', {
      class: 'app-input', 'aria-label': t.title + '使用的模型',
      value: draft[t.key].model,
      oninput: function (e) { draft[t.key].model = e.target.value; onChange(); },
    });
    return h('section', { class: 'ai-task' }, [
      h('h3', { class: 'ai-task__title', text: t.title }),
      h('div', { class: 'ai-chips' }, t.features.map(function (f) {
        return h('span', { class: 'ai-chip', text: f });
      })),
      h('div', { class: 'ai-task__fields' }, [
        h('label', { class: 'settings-field' }, [h('span', { text: '服务账号' }), sel]),
        h('label', { class: 'settings-field' }, [h('span', { text: '模型' }), input]),
      ]),
    ]);
  }

  function accountRow(acc, onChange) {
    var h = A.h;
    var usage = h('p', { class: 'model-account-usage', text: usageFor(acc.name) });
    usage.dataset.account = acc.name;
    return h('div', { class: 'model-account-row' }, [
      h('strong', { text: acc.name }),
      h('span', { class: 'model-account-host', text: acc.host }),
      acc.key ? A.badge('success', '密钥已保存') : A.badge('warning', '未保存密钥'),
      acc.dft ? A.badge('neutral', '默认') : null,
      acc.running + acc.queued > 0
        ? h('span', { class: 'model-account-runtime', text: '运行中 ' + acc.running + ' 个请求，排队 ' + acc.queued + ' 个' })
        : null,
      h('div', { class: 'model-account-actions' }, [
        h('button', {
          type: 'button', class: 'app-button ghost-mini', 'data-variant': 'ghost', text: '编辑',
          onclick: function () { A.toast('（正式版沿用现有 380px 编辑抽屉）'); },
        }),
        h('button', {
          type: 'button', class: 'app-button ghost-mini settings-danger-ghost', 'data-variant': 'ghost', text: '删除',
          onclick: function () { A.toast('（原型不执行删除）'); },
        }),
      ]),
      usage,
    ]);
  }

  function buildConfig() {
    var h = A.h;
    var saveBtn = h('button', {
      type: 'button', class: 'app-button', 'data-variant': 'primary',
      disabled: !isDirty(), text: '保存',
      onclick: function () {
        saved = JSON.parse(JSON.stringify(draft));
        savedShown = true;
        refresh();
      },
    });
    var savedText = h('span', {
      class: 'settings-note settings-saved', role: 'status',
      text: '已保存',
      style: savedShown && !isDirty() ? '' : 'visibility:hidden',
    });
    function onChange() {
      var dirty = isDirty();
      saveBtn.disabled = !dirty;
      if (dirty) savedShown = false;
      savedText.style.visibility = savedShown && !dirty ? 'visible' : 'hidden';
      grid.querySelectorAll('.model-account-usage').forEach(function (u) {
        u.textContent = usageFor(u.dataset.account);
      });
      refreshStrips();
    }
    var grid = h('div', { class: 'ai-grid' }, [
      h('section', { class: 'settings-panel' }, [
        h('header', { class: 'settings-panel__heading' }, [h('h2', { text: '用哪个 AI' })]),
        D.tasks.map(function (t) { return taskBlock(t, onChange); }),
        h('div', { class: 'ai-panel__foot' }, [saveBtn, savedText]),
      ]),
      h('section', { class: 'settings-panel' }, [
        h('header', { class: 'settings-panel__heading' }, [
          h('h2', { text: '服务账号' }),
          h('button', {
            type: 'button', class: 'app-button', 'data-variant': 'secondary', text: '添加服务',
            onclick: function () { A.toast('（正式版沿用现有 380px 编辑抽屉）'); },
          }),
        ]),
        h('div', { class: 'model-accounts' }, D.accounts.map(function (a) { return accountRow(a, onChange); })),
      ]),
    ]);
    function refresh() {
      saveBtn.disabled = true;
      savedText.style.visibility = 'visible';
      refreshStrips();
    }
    return grid;
  }

  /* ---------- 方案 A ---------- */
  function mountA(container) {
    container.innerHTML = '';
    container.append(buildConfig());
  }

  /* ---------- 方案 B ---------- */
  function stripLine(t) {
    var h = A.h;
    return h('div', { class: 'ai-strip__line' }, [
      h('strong', { text: t.title }),
      h('span', { class: 'arrow', text: '→' }),
      h('code', { text: saved[t.key].account + ' · ' + saved[t.key].model }),
    ]);
  }
  function accChip(a) {
    var h = A.h;
    return h('span', { class: 'ai-acc-chip' }, [
      h('strong', { text: a.name }),
      h('span', { text: a.dft ? '默认' : (a.key ? '密钥已保存' : '未保存密钥') }),
    ]);
  }
  function fillStrip(strip) {
    var h = A.h;
    strip.innerHTML = '';
    var lines = h('div', { class: 'ai-strip__tasks' }, D.tasks.map(stripLine));
    var accs = h('div', { class: 'ai-strip__accounts' },
      D.accounts.map(accChip).concat([
        h('button', {
          type: 'button', class: 'app-button ghost-mini', 'data-variant': 'secondary', text: '修改',
          onclick: function (e) { toggleExpand(strip, e.currentTarget); },
        }),
      ]));
    strip.append(h('div', { class: 'ai-strip__body' }, [lines, accs]));
  }
  function toggleExpand(strip, btn) {
    var expand = strip.parentNode.querySelector('.ai-config-expand');
    var open = expand.hidden;
    expand.hidden = !open;
    btn.textContent = open ? '收起' : '修改';
  }
  function refreshStrips() {
    stripRefs = stripRefs.filter(function (s) { return s.isConnected; });
    stripRefs.forEach(fillStrip);
  }

  function mountB(container) {
    var h = A.h;
    container.innerHTML = '';
    var pane = h('div', { class: 'pane-ai-b' });
    var strip = h('section', { class: 'settings-panel ai-strip', 'aria-label': 'AI 服务配置' });
    fillStrip(strip);
    stripRefs.push(strip);
    var expand = h('div', { class: 'ai-config-expand', hidden: '' }, [buildConfig()]);
    var logHost = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column;' });
    pane.append(strip, expand, logHost);
    container.append(pane);
    window.ProtoApp.modules.log.mount(logHost);
  }

  window.ProtoApp.modules.ai = { mountA: mountA, mountB: mountB };
})();
