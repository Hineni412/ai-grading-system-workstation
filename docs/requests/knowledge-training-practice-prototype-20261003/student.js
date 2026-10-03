/* student.js — 按学生训练页签：左列学生、中列训练范围、学生抽屉 */

function renderStudentTab() {
  renderStuList();
  renderStuRange();
  renderPanel('student', $('#panel-student'));
}

// ---------- 左列：学生 ----------
function stuVisible(st) {
  const q = S.stuQuery.trim();
  if (q && !st.name.includes(q) && !st.code.includes(q)) return false;
  if (S.stuOnlyWeak && KT.weakCount(st) === 0) return false;
  return true;
}
function renderStuList() {
  const el = $('#stuList');
  const scoped = scopedStudents();
  const byCls = {};
  for (const st of scoped) (byCls[st.cls] = byCls[st.cls] || []).push(st);
  let h = `<div class="box">
    <div class="box-h">学生<span class="sub">已选 ${S.sel.size} 人</span></div>
    <div class="stltools">
      <input class="app-input" id="stuQ" placeholder="搜索姓名 / 学号" value="${esc(S.stuQuery)}">
      <span><button class="fchip ${S.stuOnlyWeak ? 'on' : ''}" id="stuWeak">只看有明显薄弱点</button></span>
    </div>`;
  for (const cls of ['9班', '10班']) {
    const list = (byCls[cls] || []);
    const vis = list.filter(stuVisible);
    const closed = !S.openCls.has(cls);
    h += `<div class="clsgrp ${closed ? 'closed' : ''}" data-cls="${cls}">
      <div class="clsh"><span class="caret">${closed ? '▸' : '▾'}</span>${cls}<span class="cnt">（${list.length} 人）</span><a class="selall link" data-selall="${cls}">全选本班</a></div>
      <div class="srows">`;
    for (const st of vis) {
      const wk = KT.weakCount(st);
      h += `<div class="srow" data-sid="${st.id}">
        <input type="checkbox" data-chk="${st.id}" ${S.sel.has(st.id) ? 'checked' : ''}>
        <span class="nm" data-name="${st.id}">${esc(st.name)}</span>
        <span class="cd">${st.code}</span>
        ${scoreBadge(st.scoreRate)}
        <span class="wk">${wk ? '薄弱 ' + wk : ''}</span>
      </div>`;
    }
    if (!vis.length) h += `<div class="muted" style="padding:8px 14px">无匹配学生</div>`;
    h += `</div></div>`;
  }
  h += `<div class="stlfoot">已选 <b>${S.sel.size}</b> 人 · <a class="link" id="selClear">清空</a></div></div>`;
  el.innerHTML = h;

  $('#stuQ', el).addEventListener('input', e => { S.stuQuery = e.target.value; renderStuList(); });
  $('#stuWeak', el).addEventListener('click', () => { S.stuOnlyWeak = !S.stuOnlyWeak; renderStuList(); });
  $('#selClear', el).addEventListener('click', () => { S.sel.clear(); renderStudentTab(); });
  $$('.clsh', el).forEach(hd => hd.addEventListener('click', e => {
    if (e.target.closest('[data-selall]')) return;
    const cls = hd.parentElement.dataset.cls;
    S.openCls.has(cls) ? S.openCls.delete(cls) : S.openCls.add(cls);
    renderStuList();
  }));
  $$('[data-selall]', el).forEach(a => a.addEventListener('click', e => {
    e.stopPropagation();
    const cls = a.dataset.selall;
    scopedStudents().filter(s => s.cls === cls).forEach(s => S.sel.add(s.id));
    renderStudentTab();
  }));
  $$('[data-chk]', el).forEach(cb => cb.addEventListener('change', () => {
    cb.checked ? S.sel.add(cb.dataset.chk) : S.sel.delete(cb.dataset.chk);
    renderStudentTab();
  }));
  $$('[data-name]', el).forEach(nm => nm.addEventListener('click', () => openStuDrawer(nm.dataset.name)));
}

// ---------- 学生抽屉 ----------
function openStuDrawer(sid) {
  const st = KT.students.find(s => s.id === sid);
  const weak = [], unsteady = [];
  for (const sk of KT.allSkills) {
    const o = st.skills[sk.id];
    if (o.tier === 'weak') weak.push({ sk, o });
    else if (o.tier === 'unsteady') unsteady.push({ sk, o });
  }
  weak.sort((a, b) => a.o.m - b.o.m); unsteady.sort((a, b) => a.o.m - b.o.m);
  const skLine = x => `<div class="dskl"><span class="kn">${esc(x.sk.name)}<span class="muted"> · ${esc(x.sk.secName)}</span></span><span class="km">${pct(x.o.m)}</span></div>`;
  const examLines = KT.EXAMS.map(e => {
    const n = st.wrongs.filter(w => w.exam === e.id).length;
    return `<div class="dkv"><span class="k">${e.name}</span><span>${st.scores[e.id] == null ? '无成绩' : '得分率 ' + pct(st.scores[e.id]) + ' · 错题 ' + n + ' 道'}</span></div>`;
  }).join('');
  openDrawer(`<div class="dhead"><b>${esc(st.name)}</b> <span class="muted">${st.cls} · 学号 ${st.code}</span></div>
    <div class="dbody">
      <div class="dkv"><span class="k">考试得分率</span><span>${scoreBadge(st.scoreRate)}</span></div>
      <div class="dt2">明显薄弱（${weak.length}）</div>
      ${weak.length ? weak.map(skLine).join('') : '<div class="muted">无</div>'}
      <div class="dt2">还不稳（${unsteady.length}）</div>
      ${unsteady.length ? unsteady.map(skLine).join('') : '<div class="muted">无</div>'}
      <div class="dt2">各场考试错题</div>
      ${examLines}
    </div>
    <div class="dfoot">
      <button class="btn primary small" id="dOne">为此人导出错题本</button>
      <button class="btn small" id="dEv">查看作答证据</button>
    </div>`);
  $('#dOne').addEventListener('click', () => {
    S.sel = new Set([st.id]);
    S.purpose = 'notebook';
    closeDrawer();
    renderStudentTab();
    toast(`已选中 ${st.name} 1 人，出卷用途已切换为"错题本"`);
  });
  $('#dEv').addEventListener('click', () => toast('原型未包含'));
}

// ---------- 中列：训练范围 ----------
function sectionLearned(sec) {
  if (S.rangeMode !== 'zh') return true;
  const idx = KT.LEARN_OPTIONS.indexOf(S.learnTo);
  const ci = KT.LEARN_OPTIONS.indexOf(sec.chName);
  return ci >= 0 && ci <= idx;
}
function secAllChecked(sec) { return KT.sectionSkillIds(sec).every(id => S.zxSel.has(id)); }
function chapAllChecked(ch) { return KT.chapterSkillIds(ch).every(id => S.zxSel.has(id)); }

function renderStuRange() {
  const el = $('#stuRange');
  const sel = selStudents();
  const zh = S.rangeMode === 'zh';
  let h = `<div class="box">
    <div class="box-h">训练范围</div>
    <div class="rangetools">
      <div class="seg" id="rangeSeg">
        <button data-m="zh" class="${zh ? 'on' : ''}">综合训练</button>
        <button data-m="zx" class="${zh ? '' : 'on'}">专项训练</button>
      </div>
      ${zh ? `<select class="app-input" id="learnTo" style="height:30px">${KT.LEARN_OPTIONS.map(o =>
        `<option ${o === S.learnTo ? 'selected' : ''}>${o}</option>`).join('')}</select>` : ''}
    </div>`;
  if (S.purpose === 'notebook')
    h += `<div class="nbnote">错题本：综合 = 所选考试的全部错题；专项 = 只收所勾章节的错题</div>`;
  h += `<div class="tblegend">
    <span class="lg"><i class="sw w"></i>明显薄弱</span><span class="lg"><i class="sw u"></i>还不稳</span><span class="lg"><i class="sw s"></i>较稳定</span><span class="lg"><i class="sw i"></i>证据不足</span>
    <span class="lb">按已选学生统计</span>
  </div>
  <div class="rangebody">`;
  const learnIdx = KT.LEARN_OPTIONS.indexOf(S.learnTo);
  for (const ch of KT.chapters) {
    const chLearned = !zh || KT.LEARN_OPTIONS.indexOf(ch.name) <= learnIdx;
    h += `<div class="chrow">${zh ? esc(ch.name)
      : `<label><input type="checkbox" data-zxch="${ch.id}" ${chapAllChecked(ch) ? 'checked' : ''}>${esc(ch.name)}</label>`}</div>`;
    for (const sec of ch.sections) {
      const learned = !zh || chLearned;
      const c = KT.tierCounts(sel, KT.sectionSkillIds(sec));
      const open = S.openSecs.has(sec.id);
      h += `<div class="secrow2 ${learned ? '' : 'dorm'}" data-sec="${sec.id}">
        <span class="snm"><span class="cr">${open ? '▾' : '▸'}</span>${!zh ? `<input type="checkbox" data-zxsec="${sec.id}" ${secAllChecked(sec) ? 'checked' : ''}>` : ''}${esc(sec.name)}${learned ? '' : ' <span class="muted">未学</span>'}</span>
        <span class="swk">${c.weak ? '明显薄弱 ' + c.weak + ' 人' : ''}</span>
        ${tierBar(c)}
        <span></span>
      </div>`;
      if (open) {
        h += `<div class="skrows">`;
        for (const sk of sec.skills) {
          let w = 0, u = 0;
          for (const st of sel) { const t = st.skills[sk.id].tier; if (t === 'weak') w++; else if (t === 'unsteady') u++; }
          h += `<div class="skrow"><span class="kn">${esc(sk.name)}</span><span class="kc"><b>${w ? '明显薄弱 ' + w + ' 人' : ''}</b>${w && u ? ' · ' : ''}${u ? '还不稳 ' + u + ' 人' : ''}${!w && !u ? '已选学生中无薄弱' : ''}</span></div>`;
        }
        h += `</div>`;
      }
    }
  }
  h += `</div></div>`;
  el.innerHTML = h;

  $('#rangeSeg').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.rangeMode = b.dataset.m; renderStuRange(); renderPanel('student', $('#panel-student'));
  });
  const lt = $('#learnTo');
  if (lt) lt.addEventListener('change', e => { S.learnTo = e.target.value; renderStuRange(); renderPanel('student', $('#panel-student')); });
  $$('.secrow2', el).forEach(r => r.addEventListener('click', e => {
    if (e.target.closest('input')) return;
    if (r.classList.contains('dorm')) return;
    const id = r.dataset.sec;
    S.openSecs.has(id) ? S.openSecs.delete(id) : S.openSecs.add(id);
    renderStuRange();
  }));
  $$('[data-zxch]', el).forEach(cb => cb.addEventListener('change', () => {
    const ch = KT.chapters.find(c => c.id === cb.dataset.zxch);
    KT.chapterSkillIds(ch).forEach(id => cb.checked ? S.zxSel.add(id) : S.zxSel.delete(id));
    renderStuRange(); renderPanel('student', $('#panel-student'));
  }));
  $$('[data-zxsec]', el).forEach(cb => cb.addEventListener('change', () => {
    const sec = KT.chapters.flatMap(c => c.sections).find(s => s.id === cb.dataset.zxsec);
    KT.sectionSkillIds(sec).forEach(id => cb.checked ? S.zxSel.add(id) : S.zxSel.delete(id));
    renderStuRange(); renderPanel('student', $('#panel-student'));
  }));
}
