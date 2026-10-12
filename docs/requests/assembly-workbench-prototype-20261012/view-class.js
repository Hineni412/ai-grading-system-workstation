/* 视图 1：班级组卷 —— 渲染入口、范围条与动作（左/中/右栏在各 view-*.js） */
(function () {
  const esc = s => PROTO.esc(s);
  PROTO.TT = { '选择题': 'tt--c', '填空题': 'tt--f', '解答题': 'tt--s' };

  /* ---------- 范围条 ---------- */
  function scopeBar() {
    const S = PROTO.S;
    const cls = PROTO.CLASSES.map(c =>
      `<button type="button" class="ca-chip ${S.selClasses.includes(c.id) ? 'selected' : ''}" aria-pressed="${S.selClasses.includes(c.id)}" onclick="PROTO.toggleClass('${c.id}')">${c.name}</button>`).join('');
    const allCls = S.selClasses.length === PROTO.CLASSES.length ? 'selected' : '';
    const exams = PROTO.EXAMS.map(e =>
      `<button type="button" class="ca-chip ca-exam-chip ${S.selExams.includes(e.id) ? 'selected' : ''}" aria-pressed="${S.selExams.includes(e.id)}" onclick="PROTO.toggleExam('${e.id}')"><b>${e.title}</b><small>${e.date} · ${e.students} 人</small></button>`).join('');
    const total = PROTO.CLASSES.filter(c => S.selClasses.includes(c.id)).reduce((s, c) => s + c.students, 0);
    let pill;
    if (S.refresh === 'busy') pill = '<span class="ca-status is-busy"><span class="spin"></span>后台更新中…</span>';
    else if (S.refresh === 'done') pill = `<span class="ca-status is-done">已更新 ${S.refreshTime || ''}</span>`;
    else pill = '<span class="ca-status">已是最新</span>';
    return `<div class="panel ca-scope"><div class="ca-scope__row">
      <span class="ca-label">班级</span>
      <button type="button" class="ca-chip ${allCls}" onclick="PROTO.setClasses()">全选</button>${cls}
      <span class="muted">合计 ${total} 人</span><span class="ca-sep">|</span>
      <span class="ca-label">依据考试</span>${exams}
      <label class="ca-training"><input type="checkbox" ${S.includeTraining ? 'checked' : ''} onchange="PROTO.set({includeTraining:this.checked})">叠加训练掌握情况</label>
      ${pill}</div></div>`;
  }

  function render(el) {
    // 右栏「这张卷」已并入试卷篮抽屉（view-papercol.js），本页只剩两栏
    el.innerHTML = `<section class="class-assembly">${scopeBar()}<div class="ca-columns">${PROTO.needsCol()}${PROTO.matchingCol()}</div></section>`;
  }

  /* ---------- 动作 ---------- */
  Object.assign(PROTO, {
    renderClass: render,
    toggleClass(id) { const s = PROTO.S.selClasses; PROTO.set({ selClasses: s.includes(id) ? s.filter(x => x !== id) : [...s, id] }); },
    setClasses() { PROTO.set({ selClasses: PROTO.CLASSES.map(c => c.id) }); },
    toggleExam(id) { const s = PROTO.S.selExams; PROTO.set({ selExams: s.includes(id) ? s.filter(x => x !== id) : [...s, id] }); },
    selectPoint(id) {
      const pt = PROTO.point(id);
      PROTO.set({ selectedPoint: id, selectedOrig: pt.related[0], origStemFull: false, candLimit: 6, candRel: '', candType: '' });
    },
    selectNoskill(key) { PROTO.set({ selectedPoint: null, selectedOrig: key, origStemFull: false }); },
    selectOrig(key) { PROTO.set({ selectedOrig: key, origStemFull: false, candLimit: 6, candRel: '' }); },
    toggleStem(qid) { const s = PROTO.S.expStems; s.has(qid) ? s.delete(qid) : s.add(qid); PROTO.emit('render'); },
    toggleAns(qid) { const s = PROTO.S.expAns; s.has(qid) ? s.delete(qid) : s.add(qid); PROTO.emit('render'); },
    quickDraft() {
      const vis = PROTO.visiblePoints();
      const open = vis.filter(p => !PROTO.coveredPoints().has(p.id))[0] || vis[0];
      const cand = PROTO.QUESTIONS.find(q => !PROTO.inBasket(q.id));
      if (open && cand) { PROTO.add(cand.id, open.id); PROTO.toast('已起草：' + cand.id + ' → ' + open.name); }
      else PROTO.toast('没有可补的题型或候选题');
    },
    /* W/S 切换选中点 */
    needStep(d) {
      const S = PROTO.S;
      const ids = [];
      for (const sec of PROTO.visiblePointSections()) ids.push(...sec.items.map(p => p.id));
      if (S.openNoskill) ids.push('noskill:oN1', 'noskill:oN2');
      const cur = S.selectedPoint || 'noskill:' + S.selectedOrig;
      const i = Math.max(0, ids.indexOf(cur));
      const next = ids[Math.max(0, Math.min(ids.length - 1, i + d))];
      if (!next) return;
      next.startsWith('noskill:') ? PROTO.selectNoskill(next.slice(8)) : PROTO.selectPoint(next);
      document.querySelector('#need-list .is-selected')?.scrollIntoView({ block: 'nearest' });
    },
  });
})();
