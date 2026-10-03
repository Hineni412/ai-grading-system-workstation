/* report.js — 全屏个人报告阅读器；正文版式见 rbody.js */
function openReader(sid, exid, dataOnly) {
  S.readerOpen = true; S.rsid = sid; S.rexid = exid; S.dataOnly = !!dataOnly;
  $('#reader').classList.add('on');
  renderReader();
}
function closeReader() {
  S.readerOpen = false;
  $('#reader').classList.remove('on');
  $('#reader').innerHTML = '';
  if (S.rsid) openStudentDrawer(S.rsid); // 回到当前查看学生的抽屉并高亮矩阵行
}
function navStudent(d) {
  const list = visibleStudents();
  const i = list.findIndex(s => s.id === S.rsid);
  const nx = list[i + d];
  if (!nx) { toast(d < 0 ? '已经是第一位' : '已经是最后一位'); return; }
  S.rsid = nx.id; S.dataOnly = false;
  renderReader();
}

function sparkline(st) {
  const w = 260, h = 90, padL = 10, padR = 12, padT = 18, padB = 20;
  const pts = PR.EXAMS.map((ex, i) => {
    const grp = PR.scope(st.cls).filter(s => s['sc_' + ex.id] != null);
    const avg = grp.reduce((s, x) => s + x['sc_' + ex.id], 0) / (grp.length || 1);
    return {
      x: padL + i * (w - padL - padR) / 2,
      sc: st['sc_' + ex.id], avg,
      lab: ex.name.slice(0, 3), absent: st['abs_' + ex.id],
    };
  });
  const vals = pts.flatMap(p => [p.sc, p.avg].filter(v => v != null));
  const lo = Math.max(0, Math.floor(Math.min(...vals) - 6));
  const hi = Math.min(100, Math.ceil(Math.max(...vals) + 6));
  const y = v => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);
  let d = '', pen = false;
  pts.forEach(p => {
    if (p.sc == null) { pen = false; return; }
    d += (pen ? 'L' : 'M') + p.x.toFixed(1) + ',' + y(p.sc).toFixed(1);
    pen = true;
  });
  const avgd = pts.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(1) + ',' + y(p.avg).toFixed(1)).join(' ');
  return `<svg class="rchart" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <line x1="${padL}" y1="${y(hi)}" x2="${w - padR}" y2="${y(hi)}" stroke="#e9ede9"/>
    <line x1="${padL}" y1="${y(lo)}" x2="${w - padR}" y2="${y(lo)}" stroke="#e9ede9"/>
    <text x="${padL}" y="${y(hi) + 3}" font-size="8" fill="#9aa5a9" transform="translate(0,-3)">${hi}</text>
    <path d="${avgd}" fill="none" stroke="#9aa5a9" stroke-width="1.4" stroke-dasharray="3 2"/>
    <path d="${d}" fill="none" stroke="#176b75" stroke-width="1.8"/>
    ${pts.map(p => {
      let g = `<text x="${p.x}" y="${h - 6}" font-size="9" fill="#657176" text-anchor="middle">${p.lab}</text>`;
      if (p.sc == null) g += `<text x="${p.x}" y="${(h - padB) / 2 + padT}" font-size="9" fill="#b3402a" text-anchor="middle">缺考</text>`;
      else g += `<circle cx="${p.x}" cy="${y(p.sc)}" r="3" fill="#176b75"/>
        <text x="${p.x}" y="${y(p.sc) - 5}" font-size="9.5" font-weight="600" fill="#176b75" text-anchor="middle">${p.sc}</text>`;
      return g;
    }).join('')}
    <g font-size="8.5" fill="#657176">
      <line x1="${w - 88}" y1="8" x2="${w - 76}" y2="8" stroke="#176b75" stroke-width="1.8"/>
      <text x="${w - 72}" y="11">本人</text>
      <line x1="${w - 44}" y1="8" x2="${w - 32}" y2="8" stroke="#9aa5a9" stroke-width="1.4" stroke-dasharray="3 2"/>
      <text x="${w - 28}" y="11">班级平均</text>
    </g>
    <title>分数走势：实线=本人 · 虚线=班级平均</title></svg>`;
}

function renderReader() {
  const st = PR.byId(S.rsid);
  const ex = PR.EXAMS.find(e => e.id === S.rexid);
  const stt = effStatus(st, ex.id);
  const list = visibleStudents();
  const idx = list.findIndex(s => s.id === st.id);

  let h = `<div class="rd-top">
    <button class="btn ghost small" id="rdBack">← 返回成绩明细（Esc）</button>
    <span class="who"><b>${esc(st.name)}</b><span>${st.code} · ${st.cls}</span></span>
    <span class="rdpos">第 ${idx + 1} / ${list.length} 人（按当前排序和筛选）</span>
    <button class="btn small" id="rdPrev" ${idx <= 0 ? 'disabled' : ''}>上一位</button>
    <button class="btn small" id="rdNext" ${idx >= list.length - 1 ? 'disabled' : ''}>下一位</button>
    <div class="acts">
      ${stt === 'stale' ? `<button class="btn small" id="rpRegen">重新生成（1 次调用）</button>` : ''}
      <button class="btn small" onclick="toast('原型：导出该生本场 HTML')">导出本场 HTML</button>
      <button class="btn small" onclick="toast('原型：将打开打印对话框')">打印</button>
    </div>
  </div>
  <div class="rd-scroll"><div class="rd-in">
  <div class="exchips">
    ${PR.EXAMS.map(e => {
      const s2 = effStatus(st, e.id);
      return `<button class="exchip ${e.id === ex.id ? 'on' : ''}" data-ex="${e.id}">
        <span class="en">${e.name}</span>
        <span class="em">${s2 === 'absent' ? '缺考' : (st['sc_' + e.id] != null ? st['sc_' + e.id] + ' 分 · ' : '') + STXT[s2]}</span>
      </button>`;
    }).join('')}
    ${sparkline(st)}
  </div>`;

  if (stt === 'absent') {
    h += `<div class="rpbody"><div class="emptypane"><h3>本场缺考，无报告</h3><p>${ex.name} · ${ex.date}</p></div></div>`;
  } else if (stt === 'none' && !S.dataOnly) {
    h += `<div class="rpbody"><div class="emptypane"><h3>本场报告尚未生成</h3>
      <p>${ex.name} · ${ex.date} · 成绩已就绪</p>
      <div class="btns"><button class="btn primary small" id="rpGenOne">生成该生报告（1 次调用）</button>
      <button class="btn small" id="rpData">先看数据版（无 AI 叙述）</button></div></div></div>`;
  } else {
    if (stt === 'stale') h += `<div class="warnban">成绩已变化，显示的是上次生成的内容。<button class="btn small" id="rpRegen2">重新生成（1 次调用）</button></div>`;
    if (S.dataOnly && stt === 'none') h += `<div class="warnban" style="border-color:var(--bd3);background:var(--subtle);color:var(--ink2)">数据版预览：无 AI 叙述与"重点跟进"。</div>`;
    h += `<div class="rpbody">${reportBody(st, ex, { dataOnly: stt === 'none', released: imagesReleased(ex.id) })}</div>`;
  }
  h += `</div></div>`;
  $('#reader').innerHTML = h;

  $('#rdBack').addEventListener('click', closeReader);
  $('#rdPrev').addEventListener('click', () => navStudent(-1));
  $('#rdNext').addEventListener('click', () => navStudent(1));
  $$('#reader [data-ex]').forEach(b => b.addEventListener('click', () => {
    S.rexid = b.dataset.ex; S.dataOnly = false;
    renderReader();
  }));
  const g1 = $('#rpGenOne'); if (g1) g1.addEventListener('click', () => toast('原型：已加入任务中心（1 次调用）'));
  const gd = $('#rpData'); if (gd) gd.addEventListener('click', () => { S.dataOnly = true; renderReader(); });
  const rg = $('#rpRegen'); if (rg) rg.addEventListener('click', () => toast('原型：已加入任务中心（1 次调用）'));
  const rg2 = $('#rpRegen2'); if (rg2) rg2.addEventListener('click', () => toast('原型：已加入任务中心（1 次调用）'));
  wireReportBody();
}
