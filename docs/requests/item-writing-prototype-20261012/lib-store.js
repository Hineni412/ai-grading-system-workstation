/* 命题训练原型 - 情境库与原型状态的 localStorage 读写 */
window.IW = window.IW || {};
(function () {
  'use strict';
  var P = 'item-writing-proto:';
  var LKEY = P + 'library', EKEY = P + 'extract', RKEY = P + 'review:', SKEY = P + 'switches';
  var lib = null;

  function src() { return window.ITEM_LIBRARY_REAL || window.ITEM_LIBRARY_DEMO; }
  function stripHtml(s) { return String(s || '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim(); }
  function splitSentences(text) {
    var out = [];
    String(text || '').split('\n').forEach(function (ln) {
      ln.replace(/[^。；！？]*[。；！？]|[^。；！？]+$/g, function (m) { var t = m.trim(); if (t) out.push(t); });
    });
    return out.length ? out : ['（空材料）'];
  }
  function normMat(m) {
    var o = Object.assign({ kind: '情境材料', title: '未命名材料', status: 'available', revision: 1,
      local_issues: [], source_question_ids: [], needs_figure: false, origin: 'demo' }, m);
    if (!o.sentences || !o.sentences.length) o.sentences = splitSentences(o.situation || o.text || '');
    return o;
  }
  function seed() {
    var built = ITEM_MATERIALS.map(function (m) {
      return normMat({ id: m.id, kind: m.kind, title: m.title, sentences: m.sentences.slice(),
        table: m.table || null, requirement: Object.assign({}, m.requirement),
        context_kind: m.requirement.context, chapter: m.requirement.scope,
        status: 'available', origin: 'builtin' });
    });
    (src().materials || []).forEach(function (m) { built.push(normMat(m)); });
    return { source: src().source_note, materials: built, seq: 100 };
  }
  function load() {
    if (lib) return lib;
    try { lib = JSON.parse(localStorage.getItem(LKEY)); } catch (e) { lib = null; }
    if (!lib || lib.source !== src().source_note) { lib = seed(); persist(); }
    lib.materials.forEach(function (m, i) { lib.materials[i] = normMat(m); });
    return lib;
  }
  function persist() { try { localStorage.setItem(LKEY, JSON.stringify(lib)); } catch (e) {} }
  function exLoad() {
    var e = null;
    try { e = JSON.parse(localStorage.getItem(EKEY)); } catch (err) { e = null; }
    if (!e || e.source !== src().source_note) {
      e = { source: src().source_note, byChapter: {} };
      var t = src().terms['八年级上册'];
      (t ? t.chapters : []).forEach(function (c) { e.byChapter[c.name] = c.extracted; });
      try { localStorage.setItem(EKEY, JSON.stringify(e)); } catch (err2) {}
    }
    return e;
  }
  function exSave(e) { try { localStorage.setItem(EKEY, JSON.stringify(e)); } catch (err) {} }
  function getSw() { try { return JSON.parse(localStorage.getItem(SKEY)) || {}; } catch (e) { return {}; } }

  IW.lib = {
    src: src, isReal: function () { return !!window.ITEM_LIBRARY_REAL; },
    stripHtml: stripHtml, splitSentences: splitSentences,
    list: function () { return load().materials; },
    byId: function (id) { return load().materials.filter(function (m) { return m.id === id; })[0] || null; },
    add: function (m) { var L = load(); var o = normMat(m); o.id = 'x' + (L.seq++); L.materials.push(o); persist(); return o; },
    update: function (id, f) { var m = IW.lib.byId(id); if (!m) return;
      ['title', 'situation', 'context_kind', 'section', 'chapter'].forEach(function (k) { if (f[k] !== undefined) m[k] = f[k]; });
      if (f.situation !== undefined) m.sentences = splitSentences(f.situation);
      m.revision = (m.revision || 1) + 1; persist(); },
    setStatus: function (id, st, reason) { var m = IW.lib.byId(id); if (!m) return;
      m.status = st; m.disabled_reason = st === 'disabled' ? (reason || m.disabled_reason || '教师手动停用') : null; persist(); },
    counts: function () { var c = { available: 0, pending: 0, disabled: 0 };
      load().materials.forEach(function (m) { c[m.status] = (c[m.status] || 0) + 1; }); return c; },
    terms: function () { return src().terms; },
    chapterStats: function (term) {
      var t = src().terms[term]; if (!t || !t.has_candidates) return [];
      var ex = exLoad().byChapter;
      return t.chapters.map(function (c) {
        var done = ex[c.name] !== undefined ? ex[c.name] : c.extracted;
        var left = Math.max(0, c.dedup - done);
        return { name: c.name, fit: c.fit, dedup: c.dedup, extracted: done, left: left, req: Math.ceil(left / 10) };
      });
    },
    addExtracted: function (name, n) { var e = exLoad(); e.byChapter[name] = (e.byChapter[name] || 0) + n; exSave(e); },
    reviews: function (mid) { try { return JSON.parse(localStorage.getItem(RKEY + mid)) || []; } catch (e) { return []; } },
    review: function (mid) { var a = IW.lib.reviews(mid); return a.length ? a[a.length - 1] : null; },
    pushReview: function (mid, r) { var a = IW.lib.reviews(mid); a.push(r);
      try { localStorage.setItem(RKEY + mid, JSON.stringify(a)); } catch (e) {} },
    markTransferred: function (mid, tid) { var a = IW.lib.reviews(mid); if (!a.length) return;
      a[a.length - 1].transferred = tid; try { localStorage.setItem(RKEY + mid, JSON.stringify(a)); } catch (e) {} },
    sw: function (name) { return !!getSw()[name]; },
    setSw: function (name, v) { var s = getSw(); s[name] = !!v; try { localStorage.setItem(SKEY, JSON.stringify(s)); } catch (e) {} },
    works: function () {
      var out = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(P + 'work:') === 0) {
          try { out.push({ materialId: k.slice((P + 'work:').length), work: JSON.parse(localStorage.getItem(k)) }); } catch (e) {}
        }
      }
      return out;
    },
    resetAll: function () {
      var del = [];
      for (var i = 0; i < localStorage.length; i++) { var k = localStorage.key(i); if (k && k.indexOf(P) === 0) del.push(k); }
      del.forEach(function (k) { localStorage.removeItem(k); });
    }
  };
})();
