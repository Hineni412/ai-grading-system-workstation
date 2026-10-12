/* 设置页改版原型 · 学生名单 */
'use strict';
(function () {
  var A = window.ProtoApp;
  var D = A.data;
  var st = { q: '', cls: '' };
  var PENCIL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';

  function esc(s) { return s.replace(/[&<>"]/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }
  function marked(text, q) {
    if (!q) return esc(text);
    var i = text.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
  }
  function match(s, q) {
    return !q || s.code.indexOf(q) >= 0 || s.name.toLowerCase().indexOf(q.toLowerCase()) >= 0;
  }

  function entry(s) {
    var h = A.h;
    var btn = h('button', {
      type: 'button', class: 'stu-entry',
      onclick: function () { A.toast('（正式版打开现有编辑弹窗）'); },
    });
    btn.append(
      h('span', { class: 'stu-entry__code', html: marked(s.code, st.q) }),
      h('span', { class: 'stu-entry__name', html: marked(s.name, st.q) }),
      h('span', { class: 'stu-entry__icon', html: PENCIL })
    );
    return btn;
  }

  function renderList(listEl) {
    var h = A.h;
    listEl.innerHTML = '';
    var shown = 0;
    var wrap = h('div', { class: 'stu-sections' });
    D.classes.forEach(function (cls) {
      if (st.cls && cls.name !== st.cls) return;
      var list = cls.students.filter(function (s) { return match(s, st.q); });
      if (!list.length) return;
      shown += list.length;
      wrap.append(h('section', { class: 'stu-section' }, [
        h('header', { class: 'stu-section__head' }, [
          cls.name + ' ', h('span', { text: '· ' + list.length + ' 人' }),
        ]),
        h('div', { class: 'stu-list' }, list.map(entry)),
      ]));
    });
    if (!shown) {
      listEl.append(h('div', { class: 'stu-sections' }, [
        h('div', { class: 'stu-empty' }, [
          h('span', { text: '没有符合条件的学生' }),
          h('button', {
            type: 'button', class: 'app-button ghost-mini', 'data-variant': 'ghost', text: '清除筛选',
            onclick: function () {
              st.q = ''; st.cls = '';
              var input = listEl.parentNode.querySelector('.stu-toolbar .app-input');
              if (input) input.value = '';
              refreshSeg(listEl.parentNode);
              renderList(listEl);
            },
          }),
        ]),
      ]));
      return;
    }
    listEl.append(wrap);
  }

  function refreshSeg(root) {
    root.querySelectorAll('.stu-cls button').forEach(function (b) {
      b.classList.toggle('is-active', b.dataset.cls === st.cls);
    });
  }

  function mount(container) {
    var h = A.h;
    container.innerHTML = '';
    var listEl = h('div');

    var input = h('input', {
      class: 'app-input', type: 'search', placeholder: '搜索学号或姓名',
      'aria-label': '搜索学号或姓名', value: st.q,
      oninput: function (e) { st.q = e.target.value.trim(); renderList(listEl); },
    });

    var seg = h('div', { class: 'app-segmented stu-cls', role: 'group', 'aria-label': '按班级筛选' });
    var chips = [{ key: '', label: '全部', n: D.classes.reduce(function (s, c) { return s + c.students.length; }, 0) }]
      .concat(D.classes.map(function (c) { return { key: c.name, label: c.name, n: c.students.length }; }));
    chips.forEach(function (c) {
      seg.append(h('button', {
        type: 'button', 'data-cls': c.key, class: st.cls === c.key ? 'is-active' : '',
        onclick: function () {
          st.cls = c.key;
          refreshSeg(container);
          renderList(listEl);
        },
      }, [c.label, h('span', { class: 'log-chip-count', text: c.n })]));
    });

    container.append(
      h('div', { class: 'stu-toolbar' }, [
        input, seg,
        h('div', { class: 'stu-toolbar__end' }, [
          h('span', { text: 'CSV / Excel' }),
          h('button', {
            type: 'button', class: 'app-button', 'data-variant': 'secondary', text: '导入名单',
            onclick: function () { A.toast('（正式版打开现有导入弹窗）'); },
          }),
        ]),
      ]),
      listEl
    );
    renderList(listEl);
  }

  window.ProtoApp.modules.students = { mount: mount };
})();
