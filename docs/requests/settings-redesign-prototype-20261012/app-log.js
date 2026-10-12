/* 设置页改版原型 · 调用记录（主从列表，方案 A/B 共用） */
'use strict';
(function () {
  var A = window.ProtoApp;
  var D = A.data;
  var B = window.ProtoLogBodies;
  B.init(D);

  var st = { kind: '', outcome: '', sel: null, tab: 'request' };
  var mounts = [];
  var OUTCOME_LABEL = { success: '成功', failure: '失败', pending: '等待中' };
  var OUTCOME_ORDER = [
    { key: '', label: '全部' },
    { key: 'failure', label: '失败' },
    { key: 'pending', label: '等待中' },
    { key: 'success', label: '成功' },
  ];

  function fmtDur(ms) {
    if (!ms) return '—';
    if (ms < 1000) return ms + ' 毫秒';
    return (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' 秒';
  }
  function filtered() {
    return D.calls.filter(function (c) {
      return (!st.kind || c.kind === st.kind) && (!st.outcome || c.outcome === st.outcome);
    });
  }
  function findCall(id) {
    for (var i = 0; i < D.calls.length; i++) if (D.calls[i].id === id) return D.calls[i];
    return null;
  }
  function ensureSelVisible() {
    if (st.sel && !filtered().some(function (c) { return c.id === st.sel; })) st.sel = null;
  }
  function setKind(k) { st.kind = k; ensureSelVisible(); rerender(); }
  function setOutcome(o) { st.outcome = o; ensureSelVisible(); rerender(); }

  /* ---------- 工具条 ---------- */
  function counts(key) {
    return D.calls.filter(function (c) { return !key || c.kind === key; }).length;
  }
  function toolbar() {
    var h = A.h;
    var seg = h('div', { class: 'app-segmented', role: 'group', 'aria-label': '按用途筛选' });
    D.kindGroups.forEach(function (g) {
      seg.append(h('button', {
        type: 'button', class: st.kind === g.key ? 'is-active' : '',
        onclick: function () { setKind(g.key); },
      }, [g.label, h('span', { class: 'log-chip-count', text: counts(g.key) })]));
    });
    var segO = h('div', { class: 'app-segmented', role: 'group', 'aria-label': '按结果筛选' });
    OUTCOME_ORDER.forEach(function (o) {
      segO.append(h('button', {
        type: 'button', class: st.outcome === o.key ? 'is-active' : '',
        onclick: function () { setOutcome(o.key); },
      }, o.label));
    });
    var refresh = h('button', {
      type: 'button', class: 'app-button', 'data-variant': 'secondary', text: '刷新',
      onclick: function (e) {
        var b = e.currentTarget;
        b.disabled = true; b.textContent = '正在刷新…';
        setTimeout(function () { b.disabled = false; b.textContent = '刷新'; A.toast('已刷新（演示数据不变）'); }, 600);
      },
    });
    var scope = h('div', { class: 'log-scope' });
    var pop = null;
    scope.append(h('button', {
      type: 'button', class: 'app-button ghost-mini', 'data-variant': 'ghost', text: '记录范围',
      onclick: function (e) {
        e.stopPropagation();
        if (pop) { pop.remove(); pop = null; return; }
        pop = h('div', { class: 'log-scope__pop', role: 'note' },
          '记录只保存在本机 logs/llm_diagnostics.jsonl；密钥、图片正文和本机路径不保留。这里只列出最近 100 次。“成功”只表示请求完成，不代表结果正确。');
        scope.append(pop);
        setTimeout(function () {
          document.addEventListener('click', function close(ev) {
            if (pop && !pop.contains(ev.target)) { pop.remove(); pop = null; }
            document.removeEventListener('click', close);
          });
        }, 0);
      },
    }));
    return h('div', { class: 'log-toolbar' }, [
      seg, h('div', { class: 'log-toolbar__end' }, [segO, refresh, scope]),
    ]);
  }

  function summary() {
    var h = A.h;
    var n = { success: 0, failure: 0, pending: 0 };
    D.calls.forEach(function (c) { n[c.outcome]++; });
    var last = D.calls[0];
    return h('p', { class: 'log-summary' }, [
      '共 ' + D.calls.length + ' 次 · 成功 ' + n.success + ' · ',
      h('button', { type: 'button', title: '只看失败的调用', onclick: function () { setOutcome('failure'); } }, '失败 ' + n.failure),
      ' · 等待中 ' + n.pending + ' · 最近一次 ' + last.date + ' ' + last.time.slice(0, 5),
    ]);
  }

  /* ---------- 左侧清单 ---------- */
  function callRow(c) {
    var h = A.h;
    var cls = 'log-call';
    if (c.outcome === 'failure') cls += ' log-call--failure';
    if (st.sel === c.id) cls += ' log-call--selected';
    var meta = [h('span', { text: c.time })];
    if (c.ms) meta.push(h('span', { text: fmtDur(c.ms) }));
    if (c.images) meta.push(h('span', { text: c.images + ' 张图片' }));
    if (c.attempt > 1) meta.push(h('span', { text: '第 ' + c.attempt + ' 次尝试' }));
    return h('button', {
      type: 'button', class: cls, 'data-id': c.id,
      onclick: function () { selectCall(c.id); },
    }, [
      h('span', { class: 'log-call__l1' }, [
        h('strong', { text: c.label }),
        h('span', { class: 'ai-diagnostic-status ai-diagnostic-status--' + c.outcome, text: OUTCOME_LABEL[c.outcome] }),
      ]),
      h('span', { class: 'log-call__model', text: c.model }),
      h('span', { class: 'log-call__meta' }, meta),
    ]);
  }
  function ledger() {
    var h = A.h;
    var el = h('aside', { class: 'log-ledger', 'aria-label': '调用列表' });
    var list = filtered();
    if (!list.length) {
      el.append(h('p', { class: 'log-ledger__empty', text: '没有符合筛选条件的调用。' }));
      return el;
    }
    var day = null;
    list.forEach(function (c) {
      if (c.day !== day) {
        day = c.day;
        el.append(h('div', { class: 'log-day', text: day }));
      }
      el.append(callRow(c));
    });
    return el;
  }

  /* ---------- 右侧详情 ---------- */
  var DETAIL_TABS = [
    { key: 'request', label: '发送内容' },
    { key: 'attachments', label: '附件' },
    { key: 'response', label: '返回内容' },
    { key: 'parsed', label: '解析结果' },
    { key: 'error', label: '错误与重试' },
  ];
  function selectCall(id) {
    st.sel = id;
    var c = findCall(id);
    st.tab = c && c.outcome === 'failure' ? 'error' : 'request';
    live().forEach(function (m) {
      m.el.querySelectorAll('.log-call').forEach(function (row) {
        row.classList.toggle('log-call--selected', row.dataset.id === id);
      });
      renderDetail(m);
    });
  }

  function copyBtn(text) {
    return A.h('button', {
      type: 'button', class: 'app-button ghost-mini', 'data-variant': 'ghost', text: '复制',
      onclick: function () {
        var done = function () { A.toast('已复制'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { A.toast('复制失败'); });
        } else {
          var ta = document.createElement('textarea');
          ta.value = text; document.body.append(ta); ta.select();
          try { document.execCommand('copy'); done(); } catch (e) { A.toast('复制失败'); }
          ta.remove();
        }
      },
    });
  }
  function preBlock(text) {
    return A.h('div', { class: 'log-pre-wrap' }, [copyBtn(text), A.h('pre', { class: 'log-pre', text: text })]);
  }
  function cell(t, v) {
    return A.h('div', null, [A.h('dt', { text: t }), A.h('dd', { text: v })]);
  }

  function detailBody(c) {
    var h = A.h;
    var body = h('div', { class: 'log-detail__body' });
    if (st.tab === 'request') {
      body.append(h('p', { text: '图片正文已替换为附件编号；其余内容是发送给模型的文本和参数。' }));
      body.append(preBlock(B.requestBody(c)));
    } else if (st.tab === 'attachments') {
      var atts = B.attachments(c);
      if (!atts.length) body.append(h('p', { text: '这次请求没有发送图片附件。' }));
      else {
        var wrap = h('div', { class: 'log-attach' });
        atts.forEach(function (a) {
          wrap.append(h('div', null, [
            h('strong', { text: a.id + ' · ' + a.purpose }),
            h('span', { text: a.mime + ' · ' + a.bytes }),
            h('code', { text: a.sha }),
          ]));
        });
        body.append(wrap);
      }
    } else if (st.tab === 'response') {
      body.append(h('p', { text: c.resp ? c.resp + ' 个字符' : '没有收到可显示的文本返回' }));
      body.append(preBlock(B.responseText(c)));
    } else if (st.tab === 'parsed') {
      body.append(h('p', { text: c.outcome === 'success' ? '解析状态：parsed · 无需本地修复' : '解析状态：未完成' }));
      body.append(preBlock(B.parsedBody(c)));
    } else {
      var r = c.retry || { idx: c.attempt - 1, limit: 2, willRetry: false, delay: 0 };
      body.append(h('dl', { class: 'log-retry' }, [
        h('div', null, [h('dt', { text: '当前尝试' }), h('dd', { text: (r.idx + 1) + ' / ' + (r.limit + 1) })]),
        h('div', null, [h('dt', { text: '是否继续重试' }), h('dd', { text: r.willRetry ? '是' : '否' })]),
        h('div', null, [h('dt', { text: '重试等待' }), h('dd', { text: r.delay ? fmtDur(r.delay) : '—' })]),
      ]));
      if (c.error) {
        body.append(h('div', { class: 'log-error-card' }, [
          h('strong', { text: c.error.cat + ' · ' + c.error.ex }),
          c.error.http ? h('span', { text: 'HTTP ' + c.error.http }) : null,
          h('p', { text: c.error.msg }),
        ]));
      } else {
        body.append(h('p', { text: c.outcome === 'pending' ? '请求仍在等待中，还没有错误记录。' : '这次调用没有记录到错误。' }));
      }
    }
    return body;
  }

  function renderDetail(m) {
    var h = A.h;
    var old = m.el.querySelector('.log-detail');
    if (old) old.remove();
    var detail = h('article', { class: 'log-detail', 'aria-live': 'polite' });
    var c = st.sel ? findCall(st.sel) : null;
    if (!c) {
      detail.append(h('div', { class: 'log-detail__empty', text: '选择左侧一条调用记录' }));
    } else {
      var metaGrid = h('dl', { class: 'log-meta-grid' }, [
        cell('服务地址', c.host),
        cell('尝试', c.attempt + '/' + ((c.retry ? c.retry.limit : 2) + 1)),
        cell('图片', c.images ? c.images + ' 张' : '无'),
        cell('返回', c.resp ? c.resp + ' 字符' : '—'),
      ]);
      var tabs = h('div', { class: 'ai-diagnostic-tabs', role: 'tablist', 'aria-label': '调用详情' });
      DETAIL_TABS.forEach(function (t) {
        var label = t.label + (t.key === 'attachments' ? ' ' + c.images : '');
        tabs.append(h('button', {
          type: 'button', role: 'tab',
          'aria-selected': st.tab === t.key ? 'true' : 'false',
          onclick: function () { st.tab = t.key; live().forEach(renderDetail); },
        }, label));
      });
      detail.append(
        h('header', { class: 'log-detail__head' }, [
          h('div', null, [
            h('span', { class: 'log-detail__meta', text: c.label + ' · ' + c.date + ' ' + c.time + ' · ' + fmtDur(c.ms) + ' · ' + c.protocol }),
            h('h3', { text: c.model }),
          ]),
          h('span', { class: 'ai-diagnostic-status ai-diagnostic-status--' + c.outcome, text: OUTCOME_LABEL[c.outcome] }),
        ]),
        metaGrid, tabs, detailBody(c));
    }
    m.el.querySelector('.log-workspace').append(detail);
  }

  /* ---------- 挂载 ---------- */
  function render(m) {
    var h = A.h;
    m.el.innerHTML = '';
    m.el.append(toolbar(), summary(), h('div', { class: 'log-workspace' }, [ledger()]));
    renderDetail(m);
  }
  function live() {
    mounts = mounts.filter(function (m) { return m.el.isConnected; });
    return mounts;
  }
  function rerender() { live().forEach(render); }
  function mount(container) {
    mounts = mounts.filter(function (m) { return m.el !== container && m.el.isConnected; });
    var m = { el: container };
    mounts.push(m);
    render(m);
  }

  window.ProtoApp.modules.log = { mount: mount };
})();
