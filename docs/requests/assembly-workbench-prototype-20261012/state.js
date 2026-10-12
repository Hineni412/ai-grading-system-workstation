/* 共享内存状态：题目顺序、选中点、视图参数、乐观保存（无后端） */
(function () {
  const qById = Object.fromEntries(PROTO.QUESTIONS.map(q => [q.id, q]));
  const ptById = Object.fromEntries(PROTO.POINTS.map(p => [p.id, p]));

  const S = {
    view: 'class',            // class | preview | export
    variant: 'a',             // export 视图方案 a|b
    previewFrom: 'class',     // 预览页返回目标

    // 试卷篮：有序条目 {qid, pointId, source}
    order: PROTO.DRAFT.map(d => ({ ...d })),

    // 班级组卷页
    selClasses: ['9', '10'],
    selExams: ['w2', 'w3', 'w4'],
    includeTraining: true,
    rateFilter: 70,           // 60|70|80|100 失分原题门槛（更多▾内）
    needSort: 'weak',         // weak|rate|region
    needChapters: [],         // 章节多选，空=全部
    uncoveredOnly: false,     // 只看未配题
    showExtra: false,
    openNoskill: true,
    selectedPoint: 'p1',
    selectedOrig: 'o1a',
    origOpen: true,
    origStemFull: false,
    candRel: '',              // ''|更基础|相当|更难
    candType: '',             // ''|选择题|填空题|解答题
    candLimit: 6,

    // 卷面
    mode: 'student',          // student|teacher
    ansMode: 'answer',        // answer|analysis（教师版）
    showMeta: false,
    layout: 'sequential',     // sequential|grouped_by_type|sections
    title: '八年级9、10班 补偿练习',
    subtitle: '',
    includeAnswer: true,
    expStems: new Set(),      // 展开的题干
    expAns: new Set(),        // 展开的解析
    exportOpen: false,        // 方案B 预览展开
    composeOpen: false,
    rulesOpen: false,
    basketOpen: false,        // 试卷篮抽屉（跨视图保持）
    basketMeta: true,         // 抽屉内默认显示题目信息行

    // 乐观保存
    revision: 1,
    saveState: 'saved',       // saved|saving
    refresh: 'idle',          // idle|busy|done 模拟后台重算
  };

  const L = {};               // listeners: render / save
  function on(name, fn) { (L[name] = L[name] || []).push(fn); }
  function emit(name) { (L[name] || []).forEach(fn => fn()); }

  function save() {
    S.revision += 1;
    S.saveState = 'saving';
    emit('save');
    clearTimeout(save._t);
    save._t = setTimeout(() => { S.saveState = 'saved'; emit('save'); }, 300);
  }

  /* ===== 试卷篮操作（全部乐观，立即生效） ===== */
  function entry(qid) { return S.order.find(e => e.qid === qid); }
  function inBasket(qid) { return !!entry(qid); }

  function add(qid, pointId, source) {
    if (inBasket(qid)) return;
    // pointId 省略时挂当前选中点；传 '' 表示题库直选、不挂点
    const pt = pointId == null ? ptById[S.selectedPoint] : ptById[pointId];
    const orig = PROTO.ORIGINALS[S.selectedOrig];
    S.order.push({
      qid, pointId: pt ? pt.id : '',
      source: source || (orig ? '补 ' + PROTO.EXAM_SHORT[orig.exam] + '·' + orig.label : '题库直选'),
    });
    save(); emit('render');
  }
  function remove(qid) {
    const i = S.order.findIndex(e => e.qid === qid);
    if (i < 0) return;
    S.order.splice(i, 1); save(); emit('render');
  }
  function move(qid, beforeQid) {           // beforeQid=null → 移到末尾
    const i = S.order.findIndex(e => e.qid === qid);
    if (i < 0) return;
    const [it] = S.order.splice(i, 1);
    const j = beforeQid == null ? S.order.length : S.order.findIndex(e => e.qid === beforeQid);
    S.order.splice(j, 0, it);
    save(); emit('render');
  }
  function moveBy(qid, delta) {
    const i = S.order.findIndex(e => e.qid === qid);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= S.order.length) return;
    const [it] = S.order.splice(i, 1);
    S.order.splice(j, 0, it);
    save(); emit('render');
  }

  /* ===== 派生数据 ===== */
  const q = id => qById[id];
  const point = id => ptById[id];
  function orderedQuestions() { return S.order.map(e => ({ ...e, q: qById[e.qid] })); }
  function typeCounts() {
    const c = { '选择题': 0, '填空题': 0, '解答题': 0 };
    S.order.forEach(e => { c[qById[e.qid].type] = (c[qById[e.qid].type] || 0) + 1; });
    return c;
  }
  function estMinutes() {
    const m = { '选择题': 2, '填空题': 3, '解答题': 7 };
    return S.order.reduce((s, e) => s + (m[qById[e.qid].type] || 3), 0);
  }
  function coveredPoints() {
    const set = new Set(S.order.map(e => e.pointId).filter(Boolean));
    return set;
  }
  function coveredCount() {
    let n = 0;
    coveredPoints().forEach(pid => { n += (ptById[pid] || {}).failTotal || 0; });
    return n;
  }
  function totalFail() { return PROTO.POINTS.reduce((s, p) => s + p.failTotal, 0); }
  function coveredByPoint(pid) { return S.order.filter(e => e.pointId === pid).length; }
  function clearBasket() {
    if (!S.order.length) return;
    if (!confirm('清空试卷篮中的 ' + S.order.length + ' 道题？（原型，仅内存）')) return;
    S.order.splice(0); save(); emit('render');
  }
  /* 出卷设置规则核对：抽屉状态胶囊与整理与导出的出卷设置块共用 */
  function ruleChecks() {
    const c = typeCounts(), [dMin, dMax] = diffMinMax();
    return [
      { t: '题数达到 ' + S.order.length + ' / 10', ok: S.order.length >= 10 },
      { t: '同一题型不超过 1', ok: new Set(S.order.map(e => e.qid)).size === S.order.length },
      { t: '解答题不超过 2（当前 ' + (c['解答题'] || 0) + '）', ok: (c['解答题'] || 0) <= 2 },
      { t: '难度上限 8.0（当前最高 ' + dMax.toFixed(1) + '）', ok: dMax <= 8 },
      { t: '无近 3 次重复原题', ok: true },
    ];
  }
  function diffMinMax() {
    const ds = S.order.map(e => qById[e.qid].diff);
    return ds.length ? [Math.min(...ds), Math.max(...ds)] : [0, 0];
  }
  function diffHist() {
    const h = new Array(10).fill(0);
    S.order.forEach(e => { const b = Math.min(10, Math.max(1, Math.ceil(qById[e.qid].diff))); h[b - 1]++; });
    return h;
  }
  /* 候选题 = 题库中未在初稿卷内的固定题集；加入后卡片保留并显示「已加入」 */
  const CAND_POOL = PROTO.QUESTIONS.filter(qq => !PROTO.DRAFT.some(d => d.qid === qq.id));
  function candidates() {
    const pt = ptById[S.selectedPoint];
    const off = pt ? pt.id.charCodeAt(1) * 5 : 0;
    const rot = CAND_POOL.map((_, i) => CAND_POOL[(i + off) % CAND_POOL.length]);
    return rot
      .filter(qq => !S.candType || qq.type === S.candType)
      .filter(qq => !S.candRel || relTo(qq.diff) === S.candRel);
  }
  function origDiff() {
    const o = PROTO.ORIGINALS[S.selectedOrig];
    return o ? o.diff : null;
  }
  function relTo(d) {
    const b = origDiff();
    if (b == null) return '';
    return d < b - 0.8 ? '更基础' : d > b + 0.8 ? '更难' : '相当';
  }
  /* 确定性伪随机：按 qid+pid 生成匹配与适合人数 */
  function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; }
  function matchOf(qid) {
    const h = hash(qid + S.selectedPoint);
    const lvl = ['1级 · 直接考查', '1级 · 直接考查', '2级 · 关联考查', '2级 · 变式巩固', '3级 · 拓展'];
    return lvl[h % lvl.length];
  }
  function fitOf(qid) {
    const h = hash('fit' + qid);
    const w = 8 + h % 14, k = 5 + (h >>> 3) % 10, n = 4 + (h >>> 6) % 9;
    return { total: w + k + n, w, k, n };
  }

  /* ===== 视图内易变标记（不触发保存） ===== */
  function set(patch, rerender = true) { Object.assign(S, patch); if (rerender) emit('render'); }

  /* ===== HTML5 拖拽：listEl 内 [data-qid] 可拖动，落点给出 before ===== */
  function dnd(listEl, itemSel) {
    let dragId = null;
    listEl.addEventListener('dragstart', e => {
      const it = e.target.closest(itemSel); if (!it) return;
      dragId = it.dataset.qid; it.classList.add('is-drag-source');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', dragId); } catch (_) {}
    });
    listEl.addEventListener('dragend', () => {
      dragId = null;
      listEl.querySelectorAll('.is-drag-source,.drop-before,.drop-after').forEach(n => n.classList.remove('is-drag-source', 'drop-before', 'drop-after'));
    });
    listEl.addEventListener('dragover', e => {
      if (!dragId) return;
      e.preventDefault(); e.dataTransfer.dropEffect = 'move';
      const it = e.target.closest(itemSel);
      listEl.querySelectorAll('.drop-before,.drop-after').forEach(n => n.classList.remove('drop-before', 'drop-after'));
      if (!it || it.dataset.qid === dragId) return;
      const r = it.getBoundingClientRect();
      const horiz = listEl.dataset.dndDir === 'x';
      const after = horiz ? (e.clientX > r.left + r.width / 2) : (e.clientY > r.top + r.height / 2);
      it.classList.add(after ? 'drop-after' : 'drop-before');
    });
    listEl.addEventListener('drop', e => {
      if (!dragId) return;
      e.preventDefault();
      const it = e.target.closest(itemSel);
      let before = null;
      if (it && it.dataset.qid !== dragId) {
        const r = it.getBoundingClientRect();
        const horiz = listEl.dataset.dndDir === 'x';
        const after = horiz ? (e.clientX > r.left + r.width / 2) : (e.clientY > r.top + r.height / 2);
        if (!after) before = it.dataset.qid;
        else {
          const idx = S.order.findIndex(x => x.qid === it.dataset.qid);
          before = (S.order[idx + 1] || {}).qid ?? null;
        }
      }
      if (before === dragId) return;
      move(dragId, before);
    });
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg;
    document.getElementById('toast-root').appendChild(t);
    setTimeout(() => t.remove(), 3400);
  }

  Object.assign(PROTO, { S, on, emit, save, add, remove, move, moveBy, set, dnd, toast,
    q, point, orderedQuestions, typeCounts, estMinutes, coveredPoints, coveredCount, totalFail,
    coveredByPoint, diffMinMax, diffHist, candidates, origDiff, relTo, matchOf, fitOf, inBasket, entry,
    clearBasket, ruleChecks });
})();
