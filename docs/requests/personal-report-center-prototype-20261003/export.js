/* export.js — 顶部"导出"菜单 + 生成确认/模拟进度 + 导出对话框（方式A） */
let genRunning = false;

// ---- 导出菜单 ----
function openExportPop(anchor) {
  const c = PR.statusCounts('e4', 'all');
  const pop = showPop(anchor, `
    <div class="exitem dead"><div class="exi-t">成绩表</div><div class="exi-m">表格 · 当前筛选范围</div></div>
    <div class="exitem dead"><div class="exi-t">批注原卷</div><div class="exi-m">PDF · 按学生打包</div></div>
    <div class="exitem rep">
      <div class="exi-t">学生个人分析报告 <span class="ailab">AI 分析</span></div>
      <div class="exi-m" id="exMeta">本场：已生成 ${c.done} · 需重新生成 ${c.stale} · 未生成 ${c.none} · 缺考 ${c.absent}（人）</div>
      <div class="exi-acts">
        ${c.stale + c.none ? `<button class="btn small" id="exGen" ${genRunning ? 'disabled' : ''}>生成（${c.stale + c.none} 人）</button>` : ''}
        <button class="btn primary small" id="exExp">导出…</button>
      </div>
      <div id="exProg"></div>
    </div>`);
  $$('.exitem.dead', pop).forEach(el => el.addEventListener('click', () => toast('原型未包含')));
  const g = $('#exGen', pop);
  if (g) g.addEventListener('click', openGenDlg);
  $('#exExp', pop).addEventListener('click', () => { hidePop(); openExportDlg(); });
}
function refreshPopMeta() {
  const m = $('#exMeta'); if (!m) return;
  const c = PR.statusCounts('e4', 'all');
  m.textContent = `本场：已生成 ${c.done} · 需重新生成 ${c.stale} · 未生成 ${c.none} · 缺考 ${c.absent}（人）`;
}

// ---- 生成确认 → 模拟进度 ----
function openGenDlg() {
  const c = PR.statusCounts('e4', 'all');
  const n = c.stale + c.none;
  showDlg(`<h3>生成本场个人报告</h3>
    <div class="drow">将为 <b>${n}</b> 人生成"第四周学情反馈"的个人报告（未生成 ${c.none} + 需重新生成 ${c.stale}）。</div>
    <div class="est">使用模型"内容生成"服务：<br>
      · 预计错因整理 ${n} 次 + 报告叙述 ${n} 次调用<br>
      · 约 ${(n * 0.6).toFixed(1)} 万 token（图片另计）<br>
      · 已生成 ${c.done} 人命中缓存，不再调用</div>
    <div class="drow">生成期间可以离开本页，进度见任务中心。</div>
    <div class="dfoot"><button class="btn small" onclick="hideDlg()">取消</button>
      <button class="btn primary small" id="genStart">开始生成</button></div>`);
  $('#genStart').addEventListener('click', () => {
    hideDlg(); genRunning = true;
    let done = 0;
    const tick = () => {
      done++;
      const pr = $('#exProg');
      if (pr) pr.innerHTML = `正在生成 <span class="progress"><i style="width:${Math.round(done / n * 100)}%"></i></span> <b>${done}/${n}</b> 人 · 原型模拟`;
      if (done < n) setTimeout(tick, 140);
      else {
        for (const st of PR.students) if (st.st_e4 === 'none' || st.st_e4 === 'stale') st.st_e4 = 'done';
        genRunning = false;
        toast(`原型：${n} 份报告已生成（模拟）`);
        drawTable(); refreshPopMeta();
        const g = $('#exGen'); if (g) g.remove();
        if (S.drawerSid) openStudentDrawer(S.drawerSid);
        if (S.readerOpen) renderReader();
      }
    };
    setTimeout(tick, 300);
  });
}

// ---- 导出对话框（方式 A：范围×考试×格式） ----
function openExportDlg() {
  const st = { mode: 'all', cls: new Set(['9班']), pick: new Set(), q: '', exs: new Set(['e4']), fmt: 'zip' };
  const nonAbs = PR.students.filter(s => s.st_e4 !== 'absent').length;
  showDlg(`<h3>导出个人报告</h3>
    <div class="drow"><b>学生范围</b>
      <label><input type="radio" name="esc" value="all" checked> 全部学生（${nonAbs} 人有报告）</label>
      <label><input type="radio" name="esc" value="cls"> 按班级
        ${['9班', '10班'].map(c => `<span class="clscheck"><input type="checkbox" data-cls="${c}" ${st.cls.has(c) ? 'checked' : ''}> ${c}（${PR.scope(c).length} 人）</span>`).join('')}</label>
      <label><input type="radio" name="esc" value="pick"> 指定学生</label>
      <div id="pickBox" style="display:none">
        <input class="app-input" id="pickQ" placeholder="姓名 / 学号 / 拼音首字母" style="width:200px;margin-top:4px">
        <div class="selchips" id="pickChips"></div>
        <div class="picklist" id="pickList"></div>
      </div></div>
    <div class="drow"><b>考试</b>（可多选）
      ${PR.EXAMS.slice().reverse().map(ex => `<label><input type="checkbox" data-ex="${ex.id}" ${ex.current ? 'checked' : ''}> ${ex.name}${ex.current ? '（本场，默认勾选）' : ''}<span class="exn" data-exn="${ex.id}"></span></label>`).join('')}</div>
    <div class="drow"><b>格式</b>
      <label><input type="radio" name="efmt" value="zip" checked> 每人一份 HTML（离线可打开，图片嵌入文件）打包 ZIP</label>
      <label><input type="radio" name="efmt" value="pdf" disabled> 合并为一个 PDF 便于打印 <span class="bd warn">待评估</span></label></div>
    <div class="drow" id="exSum"></div>
    <div class="fname" id="exName"></div>
    <div class="dfoot"><button class="btn small" onclick="hideDlg()">取消</button>
      <button class="btn primary small" id="exOk">确认导出</button></div>`, true);

  const pool = () => {
    if (st.mode === 'all') return PR.students;
    if (st.mode === 'cls') return PR.students.filter(s => st.cls.has(s.cls));
    return [...st.pick].map(PR.byId);
  };
  function drawPick() {
    $('#pickBox').style.display = st.mode === 'pick' ? '' : 'none';
    $('#pickChips').innerHTML = [...st.pick].map(id => {
      const s = PR.byId(id);
      return `<span class="selchip">${esc(s.name)}<b data-rm="${id}">✕</b></span>`;
    }).join('') + (st.pick.size ? `<span style="font-size:11px;color:var(--ink3);align-self:center">已选 ${st.pick.size} 人</span>` : '');
    $('#pickList').innerHTML = ['9班', '10班'].map(cls => {
      const rows = PR.scope(cls).filter(s => !st.q || s.name.includes(st.q) || s.code.includes(st.q));
      if (!rows.length) return '';
      return `<div class="pickgrp"><div class="gh">${cls}（${rows.length} 人）<a class="link" data-all="${cls}">全选本班</a></div>
        ${rows.map(s => `<label class="pickrow"><input type="checkbox" data-pk="${s.id}" ${st.pick.has(s.id) ? 'checked' : ''}>
          ${esc(s.name)} <span class="code">${s.code}</span>${stIcon(s.st_e4)}</label>`).join('')}</div>`;
    }).join('');
    $$('#pickChips [data-rm]').forEach(x => x.addEventListener('click', () => { st.pick.delete(x.dataset.rm); drawPick(); refresh(); }));
    $$('#pickList [data-pk]').forEach(x => x.addEventListener('change', () => { x.checked ? st.pick.add(x.dataset.pk) : st.pick.delete(x.dataset.pk); drawPick(); refresh(); }));
    $$('#pickList [data-all]').forEach(x => x.addEventListener('click', e => {
      e.preventDefault();
      PR.scope(x.dataset.all).forEach(s => st.pick.add(s.id)); drawPick(); refresh();
    }));
  }
  function refresh() {
    const p = pool(), exs = [...st.exs], N = p.length, E = exs.length;
    let skip = 0;
    for (const s of p) for (const ex of exs) if (s['st_' + ex] === 'none' || s['st_' + ex] === 'absent') skip++;
    for (const ex of PR.EXAMS) {
      const el = $(`#dlg [data-exn="${ex.id}"]`);
      if (el) el.textContent = `（可导出 ${p.filter(s => s['st_' + ex.id] === 'done' || s['st_' + ex.id] === 'stale').length} 人）`;
    }
    $('#exSum').innerHTML = `将导出 <b>${N}</b> 人 × <b>${E}</b> 场 = <b>${N * E}</b> 份；其中 ${skip} 份报告未生成或缺考，不导出，会列入清单。`;
    const scopeTxt = st.mode === 'all' ? `全部_${N}人` : st.mode === 'cls' ? `${[...st.cls].join('+') || '未选'}_${N}人` : `指定${N}人`;
    $('#exName').textContent = E === 1
      ? `${PR.EXAMS.find(e => e.id === exs[0]).name}_个人报告_${scopeTxt}.zip`
      : `八年级上册_个人报告_${E}场_${N}人.zip`;
  }
  $$('#dlg [name=esc]').forEach(r => r.addEventListener('change', () => { st.mode = r.value; drawPick(); refresh(); }));
  $$('#dlg [data-cls]').forEach(x => x.addEventListener('change', () => { x.checked ? st.cls.add(x.dataset.cls) : st.cls.delete(x.dataset.cls); refresh(); }));
  $$('#dlg [data-ex]').forEach(x => x.addEventListener('change', () => { x.checked ? st.exs.add(x.dataset.ex) : st.exs.delete(x.dataset.ex); refresh(); }));
  $('#pickQ').addEventListener('input', e => { st.q = e.target.value.trim(); drawPick(); refresh(); });
  drawPick(); refresh();
  $('#exOk').addEventListener('click', () => { hideDlg(); toast('原型：已加入任务中心，完成后在任务中心下载'); });
}
