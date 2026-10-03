/* panel.js — 右列出卷面板（两个页签共用） */

const PURPOSE_NOTE = {
  paper: '打印 → 回收扫描批改 → 更新掌握度。批改调用模型、产生费用，批改前另行确认。',
  handout: '只打印，不回收，不更新掌握度。',
  notebook: '考试原题，只打印，不更新掌握度。',
};
const PURPOSE_LABEL = { paper: '训练卷', handout: '刷题讲义', notebook: '错题本' };

// 出卷对象：学生页=已选学生；章节页=已采用小组成员
function panelSubjects(tab) {
  if (tab === 'student') return { label: `已选 ${S.sel.size} 人`, students: selStudents() };
  const g = adoptedGroup();
  if (!g) return { label: '', students: null };
  return { label: `第${g.idx + 1}组 · ${g.members.length} 人`, students: g.members };
}

function renderPanel(tab, el) {
  const subj = panelSubjects(tab);
  const disabled = subj.students === null;
  const students = subj.students || [];
  const pur = S.purpose;
  const P = S.p;

  let h = `<div class="panel ${disabled ? 'disabled' : ''}">
    <div class="pt">给${subj.label || '小组'}出</div>`;
  if (disabled) h += `<div class="pdis">先在中间采用一个小组</div>`;
  h += `<div class="pb">
    <div class="pblock">
      <div class="seg" id="purSeg">
        <button data-p="paper" class="${pur === 'paper' ? 'on' : ''}">训练卷</button>
        <button data-p="handout" class="${pur === 'handout' ? 'on' : ''}">刷题讲义</button>
        <button data-p="notebook" class="${pur === 'notebook' ? 'on' : ''}">错题本</button>
      </div>
      <div class="pnote">${PURPOSE_NOTE[pur]}</div>
    </div>`;

  // ---- 出卷方式 ----
  if (pur !== 'notebook') {
    h += `<div class="pblock"><div class="pl">出卷方式</div>`;
    if (tab === 'student') {
      h += `<div class="seg" id="modeSeg">
        <button data-m="per" class="${S.mode === 'per' ? 'on' : ''}">一人一卷</button>
        <button data-m="same" class="${S.mode === 'same' ? 'on' : ''}">多人同卷</button>
      </div>`;
    } else h += `<div class="muted">同组一套卷</div>`;
    h += `</div>`;
  } else {
    h += `<div class="pblock"><div class="pl">出卷方式</div><div class="muted">每人一份 Word</div></div>`;
  }

  // ---- 参数 ----
  if (pur === 'paper') {
    h += `<div class="pblock"><div class="pl">参数</div>
      <div class="prow2">每卷题数<span class="pv"><input class="num" id="pN" type="number" value="${P.n}"><span class="hint">8–12 题</span></span></div>
      <div class="prow2">难度上限<span class="pv"><input class="num" id="pMaxD" type="number" value="${P.maxD}"><span class="hint">1–10</span></span></div>
      <details><summary>选题细则 · 同技能 ≤1 · 解答题 ≤2 · 排除最近 3 次原题</summary><div class="db">
        <div class="prow2">同一技能最多<span class="pv"><input class="num" id="pSame" type="number" value="${P.sameSkill}"></span></div>
        <div class="prow2">解答题最多<span class="pv"><input class="num" id="pAns" type="number" value="${P.answerMax}"></span></div>
        <div class="prow2">近期原题排除次数<span class="pv"><input class="num" id="pExcl" type="number" value="${P.exclN}"></span></div>
        <div class="pnote">0 = 不排除</div>
      </div></details>
    </div>`;
  } else if (pur === 'handout') {
    h += `<div class="pblock"><div class="pl">参数</div>
      <div class="prow2">每人题数上限<span class="pv"><input class="num" id="hCap" type="number" value="${P.hCap}"><span class="hint">≥1</span></span></div>
      <div class="prow2">难度上限<span class="pv"><input class="num" id="hMaxD" type="number" value="${P.hMaxD}"><span class="hint">1–10</span></span></div>
      <div class="psub"><div class="pt2">题量结构</div>
        <div class="prow2">每个薄弱技能<span class="pv"><input class="num" id="hWeak" type="number" value="${P.hWeak}"> 道</span></div>
        <div class="prow2">巩固题最多<span class="pv"><input class="num" id="hCons" type="number" value="${P.hCons}"> 道</span></div>
        <div class="prow2">新练习最多<span class="pv"><input class="num" id="hNew" type="number" value="${P.hNew}"> 道</span></div>
        <div class="pnote">先补弱，再按上限加入巩固与新练习；不用无关题凑数，不足保留缺口。</div>
      </div>
      <details><summary>细则 · 解答题与近期原题</summary><div class="db">
        <div class="prow2">解答题最多<span class="pv"><input class="num" id="hAns" type="number" value="${P.hAnsMax}"></span></div>
        <div class="prow2">近期原题排除次数<span class="pv"><input class="num" id="hExcl" type="number" value="${P.hExclN}"></span></div>
        <div class="pnote">只排除考试原题，可复用历史训练题。</div>
      </div></details>
      <div class="pnote">排版：按章 → 节 → 技能分节，节内由易到难，相似题相邻；答案解析集中在末尾。</div>
    </div>`;
  } else {
    h += `<div class="pblock"><div class="pl">参数 · 考试场次</div>`;
    const nbStat = notebookStats(students);
    for (const e of KT.EXAMS) {
      const n = nbStat.byExam[e.id] || 0;
      h += `<div class="examline"><input type="checkbox" id="nb_${e.id}" ${S.nb.exams.has(e.id) ? 'checked' : ''}>
        <label class="en2" for="nb_${e.id}">${e.name}<span class="muted"> · ${e.date}</span></label>
        <span class="ec">已选学生错题 ${n} 道</span></div>`;
    }
    h += `<div class="tglrow"><input type="checkbox" id="nbSrc" ${S.nb.src ? 'checked' : ''}><label for="nbSrc">每题标注来源（考试 · 题号）</label></div>
      <div class="tglrow"><input type="checkbox" id="nbBlank" ${S.nb.blank ? 'checked' : ''}><label for="nbBlank">每题后留作答空白</label></div>
      <div class="pnote">排版：按章 → 节 → 技能分节，节内由易到难，相似题相邻；同一原题多次失分只收一次；答案解析集中在末尾。</div>
    </div>`;
  }

  // ---- 预估 ----
  h += `<div class="estbox" id="estbox">${estimateHTML(tab, students)}</div>`;
  h += `</div><div class="pfoot" id="pfoot">${footHTML(tab, students)}</div></div>`;

  el.innerHTML = h;
  wirePanel(tab, el);
}

// ---------- 预估计算 ----------
function perStudentEstimate(st, skillIds, pur) {
  const P = S.p;
  const w = skillIds.filter(id => st.skills[id].tier === 'weak').length;
  const u = skillIds.filter(id => st.skills[id].tier === 'unsteady').length;
  if (pur === 'paper') {
    const cap = P.n;
    const a = Math.min(w * P.sameSkill, cap);
    const c = Math.min(4, cap - a, st.newPool);
    const total = a + c;
    let rs = '';
    if (total < cap) rs = w ? '薄弱技能少，合适新题不足' : '无薄弱技能，合适新题不足';
    return { a, b: 0, c, total, short: total < cap, rs };
  }
  const cap = P.hCap;
  const a = Math.min(w * P.hWeak, cap);
  const b = Math.min(u, P.hCons, Math.max(0, cap - a));
  const c = Math.min(P.hNew, Math.max(0, cap - a - b), st.newPool);
  const total = a + b + c;
  let rs = '';
  if (total < cap) rs = w ? '薄弱技能少，合适新题不足' : '无薄弱技能，合适新题不足';
  return { a, b, c, total, short: total < cap, rs };
}
function notebookStats(students) {
  const inRange = new Set(rangeSkillIds());
  const zx = S.rangeMode === 'zx';
  const inSc = w => !zx || inRange.has(w.skill); // 范围过滤（专项只收所勾章节）
  const byExam = {}, missing = [], noWrong = [];
  let total = 0;
  for (const st of students) {
    const seen = new Set();
    for (const w of st.wrongs) {
      if (!inSc(w)) continue;
      byExam[w.exam] = (byExam[w.exam] || 0) + 1; // 各场错题数（与勾选无关）
      if (!S.nb.exams.has(w.exam) || seen.has(w.qid)) continue;
      seen.add(w.qid);
      total++;
      if (w.missing) missing.push({ st, w });
    }
    if (!seen.size) noWrong.push(st);
  }
  return { byExam, total, missing, noWrong };
}

function estimateHTML(tab, students) {
  const pur = S.purpose;
  if (!students || !students.length)
    return `<div class="el1">未选择学生。</div>`;
  if (pur === 'notebook') {
    const ns = notebookStats(students);
    let h = `<div class="el1"><b>${students.length}</b> 人 · 可导出 <b>${ns.total}</b> 道错题 · 缺题库原题 <b>${ns.missing.length}</b> 道 · <b>${ns.noWrong.length}</b> 人无错题不生成</div>`;
    if (ns.missing.length || ns.noWrong.length) {
      h += `<details><summary>明细</summary>`;
      for (const x of ns.missing)
        h += `<div class="erow"><span class="enm">缺原题</span><span>${esc(x.st.name)} · ${KT.EXAMS.find(e => e.id === x.w.exam).name} · ${x.w.qno}</span></div>`;
      for (const st of ns.noWrong)
        h += `<div class="erow"><span class="enm">无错题</span><span>${esc(st.name)} · ${st.cls}</span><span class="ers">不生成</span></div>`;
      h += `</details>`;
    }
    return h;
  }
  const same = tab === 'chapter' || S.mode === 'same';
  const skillIds = tab === 'chapter' ? adoptedGroup().targets : rangeSkillIds();
  const cap = pur === 'paper' ? S.p.n : S.p.hCap;
  if (same) {
    const m = new Set();
    for (const st of students) for (const id of skillIds)
      if (st.skills[id].tier === 'weak' || st.skills[id].tier === 'unsteady') m.add(id);
    return `<div class="el1">一套${pur === 'paper' ? '卷' : '讲义'} <b>${cap}</b> 题，覆盖 <b>${students.length}</b> 名成员的 <b>${m.size}</b> 项需要。</div>`;
  }
  const est = students.map(st => ({ st, e: perStudentEstimate(st, skillIds, pur) }));
  const avg = k => Math.round(est.reduce((s, x) => s + x.e[k], 0) / est.length);
  const tots = est.map(x => x.e.total);
  const avgT = Math.round(tots.reduce((a, b) => a + b, 0) / tots.length);
  const shortK = est.filter(x => x.e.short).length;
  let h = `<div class="el1">平均每人：补弱 <b>${avg('a')}</b> · 巩固 <b>${avg('b')}</b> · 新练习 <b>${avg('c')}</b>（共约 <b>${avgT}</b> 题）；每人 <b>${Math.min(...tots)}–${Math.max(...tots)}</b> 题；<b>${shortK}</b> 人不足上限</div>`;
  h += `<details><summary>逐人预计</summary>`;
  for (const x of est)
    h += `<div class="erow"><span class="enm">${esc(x.st.name)}</span><span class="enum">补弱 ${x.e.a} · 巩固 ${x.e.b} · 新 ${x.e.c} ＝ ${x.e.total} 题</span>${x.e.short ? `<span class="ers">${esc(x.e.rs)}</span>` : ''}</div>`;
  h += `</details>`;
  return h;
}

// ---------- 底部按钮 ----------
function validateParams() {
  const P = S.p;
  if (S.purpose === 'paper' && (P.n < 8 || P.n > 12)) return '每卷题数需为 8–12';
  if (S.purpose === 'handout' && P.hCap < 1) return '每人题数上限需 ≥1';
  if (S.purpose === 'notebook' && !S.nb.exams.size) return '请至少勾选一场考试';
  return null;
}
function footHTML(tab, students) {
  const pur = S.purpose;
  const n = students ? students.length : 0;
  const invalid = validateParams();
  let btn, why = null;
  if (pur === 'notebook') btn = `导出错题本（${n} 份 Word · ZIP）`;
  else btn = pur === 'paper' ? '生成训练卷草稿 →' : '生成讲义草稿 →';
  if (!n) why = tab === 'chapter' ? '先在中间采用一个小组' : '请先选择学生';
  else if (invalid) why = invalid;
  let h = `<button class="btn primary" id="pGo" ${why ? 'disabled' : ''}>${btn}</button>`;
  if (why) h += `<div class="why">${why}</div>`;
  h += `<div class="progwrap" id="progwrap"></div>`;
  return h;
}

// ---------- 事件 ----------
function wirePanel(tab, el) {
  const rerenderPanel = () => {
    renderPanel(tab, el);
    if (tab === 'student') renderStuRange(); // 错题本提示行随用途变化
  };
  $('#purSeg', el).addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.purpose = b.dataset.p; rerenderPanel();
  });
  const ms = $('#modeSeg', el);
  if (ms) ms.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.mode = b.dataset.m; rerenderPanel();
  });
  const num = (id, key, min) => {
    const i = $(id, el);
    if (i) i.addEventListener('change', () => {
      let v = parseInt(i.value, 10);
      if (isNaN(v)) v = S.p[key];
      if (min != null) v = Math.max(min, v);
      S.p[key] = v; rerenderPanel();
    });
  };
  num('#pN', 'n'); num('#pMaxD', 'maxD'); num('#pSame', 'sameSkill', 0);
  num('#pAns', 'answerMax', 0); num('#pExcl', 'exclN', 0);
  num('#hCap', 'hCap'); num('#hMaxD', 'hMaxD'); num('#hWeak', 'hWeak', 0);
  num('#hCons', 'hCons', 0); num('#hNew', 'hNew', 0); num('#hAns', 'hAnsMax', 0); num('#hExcl', 'hExclN', 0);
  for (const e of KT.EXAMS) {
    const cb = $('#nb_' + e.id, el);
    if (cb) cb.addEventListener('change', () => {
      cb.checked ? S.nb.exams.add(e.id) : S.nb.exams.delete(e.id);
      rerenderPanel();
    });
  }
  const src = $('#nbSrc', el), blk = $('#nbBlank', el);
  if (src) src.addEventListener('change', () => { S.nb.src = src.checked; });
  if (blk) blk.addEventListener('change', () => { S.nb.blank = blk.checked; });

  const go = $('#pGo', el);
  if (go) go.addEventListener('click', () => {
    if (S.purpose === 'notebook') return runNotebookExport(tab, el);
    toast('原型：进入生成试卷页审核草稿（沿用现有审核 → 打印 → 回收流程）');
  });
}

function runNotebookExport(tab, el) {
  const subj = panelSubjects(tab);
  const total = (subj.students || []).length;
  const wrap = $('#progwrap', el);
  const go = $('#pGo', el);
  go.disabled = true;
  let done = 0;
  wrap.innerHTML = `<div class="pgt">已处理 0/${total} 名学生</div><div class="pbar"><i style="width:0%"></i></div>`;
  const bar = wrap.querySelector('.pbar i'), gt = wrap.querySelector('.pgt');
  const timer = setInterval(() => {
    done++;
    bar.style.width = (done / total * 100) + '%';
    gt.textContent = `已处理 ${done}/${total} 名学生`;
    if (done >= total) {
      clearInterval(timer);
      gt.textContent = `已完成 ${total} 份错题本`;
      go.disabled = false;
      go.textContent = '下载全部错题本';
      go.onclick = () => toast('原型：开始下载 ZIP（未真实生成文件）');
    }
  }, 140);
}
