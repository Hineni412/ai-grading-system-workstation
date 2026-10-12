/* 共享模拟引擎：任务状态、进度推进、取消、已读；与具体方案无关 */
(function () {
  'use strict';

  var E = {
    tasks: [],
    speed: 1,
    clock: 0,          // 模拟时钟（毫秒，已乘速度）
    CLEAR_AFTER: 60000, // 已读完成任务自动移出列表的模拟时长
    _seq: 0,
    _subs: [],
    _evs: {},
  };

  E.subscribe = function (fn) { E._subs.push(fn); };
  E.on = function (evt, fn) {
    (E._evs[evt] = E._evs[evt] || []).push(fn);
    return function () {
      var list = E._evs[evt], i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    };
  };
  E._emit = function (evt, arg) { (E._evs[evt] || []).forEach(function (f) { f(arg); }); };
  E.notify = function () { E._subs.forEach(function (f) { f(); }); };

  E.fmtElapsed = function (ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ' 分 ' + String(s % 60).padStart(2, '0') + ' 秒';
  };

  E.start = function (tplId, opts) {
    opts = opts || {};
    var tpl = window.TC_TEMPLATES[tplId];
    var t = {
      id: ++E._seq,
      tplId: tplId,
      name: tpl.name, kind: tpl.kind, cat: tpl.cat,
      dur: tpl.dur, scope: tpl.scope,
      origin: tpl.origin, goTo: tpl.goTo,
      stages: tpl.stages.slice(), batches: tpl.batches || 0,
      okOutcome: tpl.outcome, failOutcome: tpl.failOutcome || null,
      download: tpl.download || null,
      willFail: !!opts.fail,
      status: 'running', progress: 0, elapsed: 0,
      startedClock: new Date().toTimeString().slice(0, 5),
      cancelElapsed: 0, stageIdx: 0, stageLog: [],
      read: false, seenInDialog: false, dialogOpen: false,
      outcome: null,
    };
    E.tasks.unshift(t);
    E._emit('started', t);
    E.notify();
    return t;
  };

  E.cancel = function (id) {
    var t = E.byId(id);
    if (!t || !E.active(t)) return;
    t.status = 'cancelling';
    t.cancelElapsed = 0;
    E.notify();
  };

  E.clear = function () {
    E.tasks = [];
    E._emit('cleared');
    E.notify();
  };

  E.byId = function (id) {
    return E.tasks.find(function (t) { return t.id === id; });
  };
  E.active = function (t) { return t.status === 'running' || t.status === 'cancelling'; };
  E.finished = function (t) { return !E.active(t); };
  E.running = function () { return E.tasks.filter(E.active); };
  E.unread = function () {
    return E.tasks.filter(function (t) { return E.finished(t) && !t.read; });
  };
  E.needsAttention = function (t) {
    return t.outcome && (t.outcome.label === '部分完成'
      || t.outcome.label === '等待教师核对' || t.outcome.label === '未完成');
  };
  // 完成卡片资格：>5s 才弹卡；已取消与「用户正盯着对话框看到结果」不弹
  E.wantsCard = function (t) {
    return E.finished(t) && t.dur > 5 && t.status !== 'cancelled' && !t.seenInDialog;
  };
  E.markRead = function (id) {
    var t = E.byId(id);
    if (t && !t.read) { t.read = true; t.readAt = E.clock; E.notify(); }
  };
  // 打开列表时标记已读：需要处理的任务不自动已读，保留未读点直到「去查看」
  E.markAllRead = function () {
    var changed = false;
    E.tasks.forEach(function (t) {
      if (E.finished(t) && !t.read && !E.needsAttention(t)) {
        t.read = true; t.readAt = E.clock; changed = true;
      }
    });
    if (changed) E.notify();
  };

  E.stageText = function (t) {
    if (t.status === 'cancelling') return '正在取消';
    if (t.batches) {
      var b = Math.min(t.batches, Math.floor(t.progress * t.batches) + 1);
      return '正在处理第 ' + b + ' / ' + t.batches + ' 批。';
    }
    return t.stages[t.stageIdx] || t.stages[0];
  };

  E.runLabel = function (t) {
    if (t.status === 'cancelling') return '正在取消';
    return '进行中';
  };

  function finish(t) {
    t.progress = 1;
    if (t.dialogOpen) t.seenInDialog = true;
    if (t.willFail && t.failOutcome) {
      t.status = 'failed';
      t.outcome = t.failOutcome;
    } else {
      t.status = 'succeeded';
      t.outcome = t.okOutcome;
    }
    t.stageLog.push({ label: t.outcome.label, at: t.elapsed });
    E._emit('finished', t);
  }

  function expirePass() {
    var expired = E.tasks.filter(function (t) {
      return E.finished(t) && t.read && t.readAt != null && !t.expiring
        && E.clock - t.readAt >= E.CLEAR_AFTER;
    });
    expired.forEach(function (t) {
      t.expiring = true;
      E._emit('expiring', t);
      setTimeout(function () {
        var i = E.tasks.indexOf(t);
        if (i >= 0) { E.tasks.splice(i, 1); E.notify(); }
      }, 230);
    });
    // 「已结束」分组最多 5 条，更旧的立即丢弃；需要处理的不计入
    var done = E.tasks.filter(function (t) {
      return E.finished(t) && !E.needsAttention(t) && !t.expiring;
    });
    var dropped = false;
    for (var i = 5; i < done.length; i++) {
      var j = E.tasks.indexOf(done[i]);
      if (j >= 0) { E.tasks.splice(j, 1); dropped = true; }
    }
    if (dropped) E.notify();
  }

  var last = performance.now();
  setInterval(function () {
    var now = performance.now();
    var dt = (now - last) * E.speed;
    last = now;
    E.clock += dt;
    var changed = false;
    E.tasks.forEach(function (t) {
      if (t.status === 'cancelling') {
        t.cancelElapsed += dt;
        if (t.cancelElapsed >= 2000) {
          t.status = 'cancelled';
          t.outcome = { label: '已取消', tone: 'neutral', note: '本次处理已取消。' };
          t.stageLog.push({ label: '已取消', at: t.elapsed });
          E._emit('finished', t);
        }
        changed = true;
      } else if (t.status === 'running') {
        t.elapsed += dt;
        t.progress = Math.min(1, t.elapsed / (t.dur * 1000));
        var idx = Math.min(t.stages.length - 1, Math.floor(t.progress * t.stages.length));
        if (idx !== t.stageIdx || t.stageLog.length === 0) {
          t.stageIdx = idx;
          if (!t.stageLog.length || t.stageLog[t.stageLog.length - 1].label !== t.stages[idx]) {
            t.stageLog.push({ label: t.stages[idx], at: t.elapsed });
          }
        }
        if (t.progress >= 1) finish(t);
        changed = true;
      }
    });
    expirePass();
    if (changed) E.notify();
  }, 250);

  window.TCEngine = E;
})();
