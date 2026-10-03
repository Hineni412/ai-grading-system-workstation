/* details.js — 成绩明细：学生×题目矩阵 + 筛选 + 右侧学生抽屉 */
function visibleStudents() {
  return PR.students.filter(st => {
    if (S.cls !== 'all' && st.cls !== S.cls) return false;
    if (S.q && !(st.name.includes(S.q) || st.code.includes(S.q) || st.cls.includes(S.q))) return false;
    if (S.pend && !(st.abs_e4 || (st.sc_e4 != null && st.sc_e4 < 60))) return false;
    if (S.nogen && !(st.st_e4 === 'none' || st.st_e4 === 'stale')) return false;
    return true;
  });
}

function renderDetails() {
  const ng = PR.statusCounts('e4', S.cls);
  let h = `<div class="rtoolbar">
    ${['all', '9班', '10班'].map(v => `<button class="scp ${S.cls === v ? 'on' : ''}" data-cls="${v}">${v === 'all' ? '全部' : v}</button>`).join('')}
    <input class="app-input" id="q" placeholder="姓名、学号、班级或拼音首字母" style="width:190px" value="${esc(S.q)}">
    <button class="scp ${S.pend ? 'on' : ''}" id="fPend">只看待处理学生</button>
    <button class="scp rchip ${S.nogen ? 'on' : ''}" id="fNogen">报告未生成或需重新生成（${ng.stale + ng.none}）</button>
    <span class="dcount" id="dcount"></span>
  </div>
  <div class="dhint">点姓名看成绩详情 · 点 ✓ 直接打开个人报告 · 点分数进入成绩复核</div>
  <div class="dwrap"><table class="dtab"><thead id="dhead"></thead><tbody id="dbody2"></tbody></table></div>`;
  $('#v-details').innerHTML = h;
  drawTable();
  $$('#v-details [data-cls]').forEach(b => b.addEventListener('click', () => { S.cls = b.dataset.cls; renderDetails(); }));
  $('#q').addEventListener('input', e => { S.q = e.target.value.trim(); drawTable(); });
  $('#fPend').addEventListener('click', () => { S.pend = !S.pend; renderDetails(); });
  $('#fNogen').addEventListener('click', () => { S.nogen = !S.nogen; renderDetails(); });
}

function stIcon(stt, btn) {
  if (stt === 'absent') return `<span class="ric absent" title="个人报告：缺考">缺考</span>`;
  if (!btn) return `<span class="ric ${stt}" title="个人报告：${STXT[stt]}">${SICO[stt]}</span>`;
  return `<button class="ric ${stt} ribtn" data-rep title="查看个人报告（${STXT[stt]}）">${SICO[stt]}</button>`;
}
function goReviewToast(qn, back) {
  toast(`原型：进入现有成绩复核页（学生作答 · Q${qn}），${back ? '返回后回到本报告' : '行为不变'}`);
}

function drawTable() {
  const list = visibleStudents();
  const meta = PR.qmeta('e4');
  $('#dhead').innerHTML = `<tr><th class="idcol">学生<span class="qavg" style="display:inline;margin-left:6px">${list.length} 人</span></th>
    <th class="tot">总分<span class="qavg">/100</span></th>
    ${meta.map(q => `<th class="qc" title="第 ${q.n} 题 · 满分 ${q.full}">Q${q.n}<span class="qavg">${q.avg.toFixed(1)}/${q.full}</span></th>`).join('')}</tr>`;
  $('#dbody2').innerHTML = list.map(st => {
    const qs = PR.questions(st, PR.e4());
    return `<tr data-sid="${st.id}" class="${S.drawerSid === st.id ? 'cur' : ''}">
      <td class="idcol"><span class="idwrap">
        <span class="srow"><button class="sbtn" data-open="${st.id}"><b>${esc(st.name)}</b></button>${stIcon(st.st_e4, true)}</span>
        <span class="ssub">${st.code} · ${st.cls}</span></span></td>
      <td class="tot">${st.sc_e4 == null ? '<span class="abs">缺考</span>' : `<b>${st.sc_e4}</b>`}</td>
      ${qs.map(q => {
        const c = q.got == null ? 'na' : q.got === q.full ? 'qf' : q.got === 0 ? 'qz' : 'qp';
        const got = q.got == null ? '—' : q.got;
        return `<td class="qc ${c}"><button class="qcbtn" data-rev="${q.n}"
          aria-label="${esc(st.name)}，Q${q.n}，${q.got == null ? '缺考' : '得分 ' + q.got}，查看作答">${got}</button></td>`;
      }).join('')}</tr>`;
  }).join('');
  $('#dcount').textContent = `显示 ${list.length} / ${PR.students.length} 人`;
  $$('#dbody2 [data-open]').forEach(b => b.addEventListener('click', () => openStudentDrawer(b.dataset.open)));
  $$('#dbody2 [data-rev]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation(); goReviewToast(b.dataset.rev, false);
  }));
  $$('#dbody2 [data-rep]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    const sid = b.closest('tr').dataset.sid;
    openReader(sid, 'e4', false);
  }));
}

// ---- 学生抽屉 ----
function openStudentDrawer(sid) {
  const st = PR.byId(sid);
  S.drawerSid = sid;
  const ex = PR.e4();
  const stt = effStatus(st, ex.id);
  const qs = PR.questions(st, ex);
  const d = PR.rankDelta(st);
  const prevSc = st.sc_e3;
  const sd = (st.sc_e4 != null && prevSc != null) ? st.sc_e4 - prevSc : null;
  const unexpected = qs.filter(q => q.got === 0 && q.full >= 6).slice(0, 3).map(q => 'Q' + q.n).join('、');

  const RTXT = { done: '已生成', stale: '成绩已变化，报告需重新生成', none: '本场报告未生成', absent: '本场缺考，无报告' };
  const rbtn = stt === 'done' ? `<button class="btn primary small" id="dView">查看个人报告</button>`
    : stt === 'stale' ? `<button class="btn primary small" id="dView">查看个人报告（上次生成）</button>`
    : stt === 'none' ? `<button class="btn small" id="dData">先看数据版</button>` : '';

  $('#drawer').innerHTML = `
    <div class="dhead"><div style="display:flex;align-items:baseline;gap:8px">
      <h3 style="font-size:15px">${esc(st.name)}</h3>
      <span style="font-size:11.5px;color:var(--ink3)">${st.code} · ${st.cls}</span>
      <button class="btn ghost small" style="margin-left:auto" onclick="closeStudentDrawer()">✕</button></div>
    </div>
    <div class="dbody">
      <div class="dtot"><span class="sv">${st.sc_e4 == null ? '缺考' : st.sc_e4 + '<small> / 100</small>'}</span>
        <span class="cmp">较第三周学情反馈：${sd == null ? '—' : (sd >= 0 ? '+' : '') + sd + ' 分'} · 本班名次 ${d == null ? '—' : d > 0 ? '↑' + d : d < 0 ? '↓' + (-d) : '持平'}</span></div>
      <div class="rptblk"><div class="rl">个人报告</div>
        <div class="rs">${RTXT[stt]}</div>${rbtn}</div>
      <div class="dunexp">意外失分：${unexpected || '无'}</div>
      <h4 style="font-size:12.5px;margin:14px 0 4px;color:var(--ink2)">逐题得分</h4>
      ${qs.map(q => `<div class="qrow" data-rev="${q.n}" title="查看作答"><span class="qn">Q${q.n}</span>
        <span class="qg">${q.got == null ? '—' : q.got} / ${q.full}</span>
        <span class="qr">${q.got == null ? '缺考' : q.got === q.full ? '' : esc(PR.QERR[q.n % PR.QERR.length])}</span><span class="qgo">›</span></div>`).join('')}
    </div>`;
  $('#drawerWrap').classList.add('on');
  $$('#dbody2 tr.cur').forEach(tr => tr.classList.remove('cur'));
  const tr = $(`#dbody2 tr[data-sid="${sid}"]`);
  if (tr) tr.classList.add('cur');
  const dv = $('#dView');
  if (dv) dv.addEventListener('click', () => openReader(sid, 'e4', false));
  const dd = $('#dData');
  if (dd) dd.addEventListener('click', () => openReader(sid, 'e4', true));
  $$('#drawer .qrow[data-rev]').forEach(r => r.addEventListener('click', () => goReviewToast(r.dataset.rev, false)));
}
function closeStudentDrawer() {
  S.drawerSid = null;
  $('#drawerWrap').classList.remove('on');
  $$('#dbody2 tr.cur').forEach(tr => tr.classList.remove('cur'));
}
$('#drawerMask').addEventListener('click', closeStudentDrawer);
