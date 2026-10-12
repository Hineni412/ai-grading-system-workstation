/* 设置页改版原型 · 数据与空间 */
'use strict';
(function () {
  var A = window.ProtoApp;
  var D = A.data;
  var selected = {};

  /* ---------- 空间占用 ---------- */
  function spacePanel() {
    var h = A.h;
    var bar = h('div', { class: 'settings-space-bar', 'aria-hidden': 'true' },
      D.space.categories.map(function (c) {
        return h('i', { style: 'width:' + c.pct + '%;background:' + c.color });
      }));
    var legend = h('dl', { class: 'settings-space-legend proto-space-legend' },
      D.space.categories.map(function (c) {
        return h('div', { class: 'settings-space-legend__item' }, [
          h('dt', null, [h('i', { 'aria-hidden': 'true', style: 'background:' + c.color }), c.label]),
          h('dd', { text: c.value }),
        ]);
      }));
    return h('section', { class: 'settings-panel' }, [
      h('header', { class: 'settings-panel__heading' }, [h('h2', { text: '空间占用' })]),
      h('div', { class: 'settings-panel__body' }, [
        h('p', { class: 'settings-space-total', text: '业务数据共 ' + D.space.total }),
        h('p', { class: 'settings-note settings-space-scope', text: '仅统计业务数据，不含程序和开发临时文件。' }),
        bar, legend,
        h('div', { class: 'proto-legacy-row' }, [
          h('span', { class: 'settings-note', text: D.space.legacy }),
          h('button', {
            type: 'button', class: 'app-button ghost-mini', 'data-variant': 'ghost', text: '清理',
            onclick: function () { A.toast('（原型不执行清理）'); },
          }),
        ]),
      ]),
    ]);
  }

  /* ---------- 备份 ---------- */
  function backupPanel() {
    var h = A.h;
    var body = h('div', { class: 'settings-panel__body' });
    var actions = h('div', { class: 'backup-actions' });
    var backupBtn = h('button', { type: 'button', class: 'app-button', 'data-variant': 'primary', text: '立即备份' });
    actions.append(backupBtn);
    backupBtn.addEventListener('click', function () {
      backupBtn.disabled = true;
      var bar = h('div', { class: 'proto-progress' }, [h('i')]);
      var label = h('span', { class: 'settings-note', text: '正在备份…' });
      actions.append(bar, label);
      var i = bar.firstChild, pct = 0;
      var timer = setInterval(function () {
        pct = Math.min(100, pct + 8);
        i.style.width = pct + '%';
        if (pct >= 100) {
          clearInterval(timer);
          bar.remove(); label.remove();
          actions.append(
            h('span', { class: 'settings-note settings-saved', text: '备份完成 · 45.3 MB' }),
            h('button', {
              type: 'button', class: 'app-button', 'data-variant': 'secondary', text: '下载',
              onclick: function () { A.toast('（原型不生成文件）'); },
            }));
          backupBtn.disabled = false;
        }
      }, 160);
    });
    body.append(
      h('div', { class: 'backup-row' }, [
        h('span', { class: 'backup-row__label', text: '最近备份' }),
        h('span', { class: 'backup-row__value', text: '10-11 19:20 · 45.2 MB · 手动备份' }),
      ]),
      actions,
      h('div', { class: 'backup-restore' }, [
        h('button', {
          type: 'button', class: 'app-button ghost-mini settings-danger-ghost', 'data-variant': 'ghost',
          text: '从备份恢复…', onclick: openRestore,
        }),
      ]));
    return h('section', { class: 'settings-panel' }, [
      h('header', { class: 'settings-panel__heading' }, [h('h2', { text: '备份' })]),
      body,
    ]);
  }

  /* ---------- 恢复对话框 ---------- */
  function openRestore() {
    var h = A.h;
    var root = document.getElementById('dialog-root');
    var picked = null, phrase = '';
    var confirmBtn = h('button', {
      type: 'button', class: 'app-button', 'data-variant': 'danger', disabled: true,
      text: '恢复所选备份',
      onclick: function () {
        close();
        A.toast('（正式版在重启应用后生效，重启前可撤销）');
      },
    });
    var cancelBtn = h('button', {
      type: 'button', class: 'app-button', 'data-variant': 'secondary', text: '取消',
      onclick: close,
    });
    function check() { confirmBtn.disabled = !(picked && phrase === '确认恢复'); }
    function close() { root.innerHTML = ''; document.removeEventListener('keydown', onKey); }
    function onKey(e) { if (e.key === 'Escape') close(); }

    var rows = D.backups.map(function (b, i) {
      return h('tr', null, [
        h('td', null, [h('input', {
          type: 'radio', name: 'restore-backup',
          'aria-label': '选择 ' + b.time + ' 的备份',
          onchange: function () { picked = i; check(); },
        })]),
        h('td', { text: b.time }),
        h('td', { text: b.reason }),
        h('td', { class: 'is-num', text: b.size }),
      ]);
    });
    var input = h('input', {
      class: 'app-input', 'aria-label': '输入确认恢复', autocomplete: 'off',
      oninput: function (e) { phrase = e.target.value.trim(); check(); },
    });
    var mask = h('div', {
      class: 'proto-dialog-mask',
      onclick: function (e) { if (e.target === mask) close(); },
    }, [
      h('div', { class: 'proto-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': '恢复所选备份' }, [
        h('div', { class: 'proto-dialog__head' }, [h('h2', { text: '恢复所选备份' })]),
        h('div', { class: 'proto-dialog__body' }, [
          h('div', { class: 'proto-dialog__scroll' }, [
            h('table', { class: 'app-table' }, [
              h('thead', null, [h('tr', null, [h('th'), h('th', { text: '时间' }), h('th', { text: '原因' }), h('th', { class: 'is-num', text: '大小' })])]),
              h('tbody', null, rows),
            ]),
          ]),
          h('p', { class: 'proto-dialog__note', text: '用这份备份覆盖当前同名数据。重启应用后才生效，重启前可以撤销。' }),
          h('label', { class: 'proto-dialog__confirm' }, [h('span', { text: '输入「确认恢复」继续' }), input]),
        ]),
        h('div', { class: 'proto-dialog__foot' }, [cancelBtn, confirmBtn]),
      ]),
    ]);
    root.append(mask);
    document.addEventListener('keydown', onKey);
    cancelBtn.focus();
  }

  /* ---------- 考试原卷 ---------- */
  function selectionBar() {
    var h = A.h;
    var rows = D.exams.filter(function (e) { return selected[e.id]; });
    if (!rows.length) return null;
    var freed = rows.reduce(function (s, e) { return s + e.freed; }, 0);
    return h('div', { class: 'settings-selection-bar' }, [
      h('span', { text: '已选 ' + rows.length + ' 场 · 可腾出 ' + freed.toFixed(1) + ' MB' }),
      h('div', null, [
        h('button', {
          type: 'button', class: 'app-button ghost-mini', 'data-variant': 'secondary', text: '释放扫描文件',
          disabled: !rows.every(function (e) { return e.canRelease; }),
          onclick: function () { A.toast('（原型不执行释放）'); },
        }),
        h('button', {
          type: 'button', class: 'app-button ghost-mini settings-danger-outline', 'data-variant': 'danger', text: '清除原卷',
          disabled: !rows.every(function (e) { return e.canClear; }),
          onclick: function () { A.toast('（原型不执行清除）'); },
        }),
      ]),
    ]);
  }

  function examPanel() {
    var h = A.h;
    var host = h('div');
    function refresh() {
      host.innerHTML = '';
      var bar = selectionBar();
      if (bar) host.append(bar);
      host.append(h('div', { class: 'settings-table-scroll' }, [table()]));
    }
    function table() {
      var selectable = D.exams.filter(function (e) { return e.canRelease || e.canClear; });
      var allOn = selectable.length && selectable.every(function (e) { return selected[e.id]; });
      return h('table', { class: 'app-table settings-originals-table' }, [
        h('thead', null, [h('tr', null, [
          h('th', null, [h('input', {
            type: 'checkbox', 'aria-label': '全选可清理的考试',
            checked: allOn ? '' : null,
            onchange: function (e) {
              selectable.forEach(function (r) { selected[r.id] = e.target.checked; });
              refresh();
            },
          })]),
          h('th', { text: '考试' }), h('th', { text: '状态' }),
          h('th', { class: 'is-num', text: '扫描文件' }),
          h('th', { class: 'is-num', text: '页面与旧批注图' }),
          h('th', { text: '原卷' }), h('th', { text: '操作' }),
        ])]),
        h('tbody', null, D.exams.map(function (row) {
          var actionable = row.canRelease || row.canClear;
          return h('tr', null, [
            h('td', null, [h('input', {
              type: 'checkbox', 'aria-label': '选择 ' + row.name,
              checked: selected[row.id] ? '' : null, disabled: !actionable,
              onchange: function (e) { selected[row.id] = e.target.checked; refresh(); },
            })]),
            h('td', null, [h('strong', { text: row.name }), h('small', { text: row.date })]),
            h('td', null, [A.badge(row.statusTone, row.status)]),
            h('td', { class: 'settings-num is-num', text: row.scan || '—' }),
            h('td', { class: 'settings-num is-num', text: row.pages || '—' }),
            h('td', null, [A.badge(row.origTone, row.orig)]),
            h('td', null, [h('div', { class: 'settings-row-actions' }, [
              row.canRelease ? h('button', {
                type: 'button', class: 'app-button ghost-mini', 'data-variant': 'secondary', text: '释放扫描文件',
                onclick: function () { A.toast('（原型不执行释放）'); },
              }) : null,
              row.canClear ? h('button', {
                type: 'button', class: 'app-button ghost-mini settings-danger-outline', 'data-variant': 'danger', text: '清除原卷',
                onclick: function () { A.toast('（原型不执行清除）'); },
              }) : null,
            ])]),
          ]);
        })),
      ]);
    }
    refresh();
    return h('section', { class: 'settings-panel exam-panel' }, [
      h('header', { class: 'settings-panel__heading' }, [
        h('h2', { text: '考试原卷' }),
        h('span', { class: 'settings-note', text: '可腾出最多 1.2 GB' }),
      ]),
      host,
    ]);
  }

  function mount(container) {
    var h = A.h;
    container.innerHTML = '';
    container.append(h('div', { class: 'settings-data' }, [
      h('div', { class: 'data-top' }, [spacePanel(), backupPanel()]),
      examPanel(),
    ]));
  }

  window.ProtoApp.modules.data = { mount: mount };
})();
