/* rbody.js — 个人报告正文（合成版式，非 iframe） */
function segOf(sc) {
  if (sc == null) return '—';
  if (sc < 60) return '分数＜60'; if (sc < 80) return '60≤分数＜80';
  if (sc < 90) return '80≤分数＜90'; return '分数≥90';
}
function svgCrop(id, w, h) {
  let s = 777 + id * 131;
  const r = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
  let paths = '';
  for (let i = 0; i < 5; i++) {
    const y = 12 + i * (h - 22) / 5;
    let d = `M6 ${y}`;
    for (let x = 6; x < w - 8; x += 7) d += ` q3 ${(r() - 0.5) * 7} 7 ${(r() - 0.5) * 3}`;
    paths += `<path d="${d}" fill="none" stroke="#5a6b72" stroke-width="${0.8 + r() * 0.7}" stroke-linecap="round" opacity="${0.55 + r() * 0.3}"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">${paths}</svg>`;
}
const TASK = ['把课本上这一节的 3 道例题重做一遍，盖住答案写全过程。',
  '找出作业本中同类错题 2 道，写出当时错在哪一步。',
  '用一句话向同桌讲清这个知识点的适用条件，再各做 2 题。'];
const CHECK = ['三天后小测 2 题，全对即过。', '下一张训练卷同类题不再失分。',
  '能说出错因并举一个反例。'];

let curQ = null;
function reportBody(st, ex, opt) {
  const sc = st['sc_' + ex.id];
  const grp = PR.scope(st.cls).filter(s => s['sc_' + ex.id] != null);
  const avg = grp.reduce((s, x) => s + x['sc_' + ex.id], 0) / (grp.length || 1);
  const qs = PR.questions(st, ex);
  const prevId = PR.EXAMS[PR.EXAMS.indexOf(ex) - 1]?.id;
  const prevSc = prevId ? st['sc_' + prevId] : null;
  const delta = (sc != null && prevSc != null) ? sc - prevSc : null;
  const ai = opt.dataOnly ? '' : '<span class="ailab">AI 分析 · 仅供参考</span>';

  let h = `<div class="rpsec"><h3>成绩</h3><div class="scorecard">
    <div class="sc"><div class="sl">总分</div><div class="sv">${sc}<small> / 100</small></div></div>
    <div class="sc"><div class="sl">本班名次</div><div class="sv">${st['rk_' + ex.id] ?? '—'}<small> / ${grp.length}</small></div></div>
    <div class="sc"><div class="sl">班级平均</div><div class="sv">${avg.toFixed(1)}</div></div>
    <div class="sc"><div class="sl">较上次</div><div class="sv ${delta == null ? '' : delta >= 0 ? 'delta up' : 'delta down'}">${delta == null ? '—' : (delta >= 0 ? '+' : '') + delta}</div><div class="ss">vs ${prevId ? PR.EXAMS.find(e => e.id === prevId).name : '—'}</div></div>
    <div class="sc"><div class="sl">分数段</div><div class="sv" style="font-size:14px;line-height:1.9">${segOf(sc)}</div></div>
  </div></div>`;

  // 答题一览
  h += `<div class="rpsec"><h3>答题一览<span style="font-size:11px;color:var(--ink3);font-weight:400">点题号看单题；图=在线读取本机原卷</span></h3>
    <div class="qgrid">${qs.map(q => {
      const cls = q.got == null ? 'a' : q.got === q.full ? 'f' : q.got === 0 ? 'z' : 'p';
      return `<div class="qq ${cls} ${curQ === q.n ? 'sel' : ''}" data-q="${q.n}" title="第${q.n}题 ${q.got ?? '—'}/${q.full} 分">${q.n}</div>`;
    }).join('')}</div><div id="qdet">${qDetail(st, ex, qs, curQ, opt.released)}</div></div>`;

  // 重点跟进
  if (!opt.dataOnly) {
    const lost = qs.filter(q => q.got != null && q.got < q.full).slice(0, 3);
    h += `<div class="rpsec"><h3>这次重点跟进 ${ai}</h3><div class="focus3">${lost.map((q, i) => `
      <div class="fcard"><h4>${esc(q.kp)}</h4>
        <p class="fr">发现：第 ${q.n} 题${esc(PR.QERR[q.n % PR.QERR.length])}，同考点已连错 ${1 + (q.n % 3)} 次。</p>
        <p class="ft"><b>先做的小任务</b>：${TASK[(q.n + i) % TASK.length]}</p>
        <p class="fc">完成检查：${CHECK[q.n % CHECK.length]}</p>
        <details><summary>看具体题目</summary><p style="font-size:11.5px;margin-top:5px">第 ${q.n} 题（${q.got}/${q.full} 分）${q.n + 3 <= qs.length ? `、第 ${q.n + 3} 题` : ''}，均考"${esc(q.kp)}"。</p></details>
      </div>`).join('') || '<p style="color:var(--ink3);font-size:12px">本场没有明显失分题。</p>'}</div></div>`;
  }

  // 本次考查点
  const good = [], part = [], need = [], unk = [];
  const seen = new Set();
  for (const q of qs) {
    if (seen.has(q.kp)) continue; seen.add(q.kp);
    if (q.got == null) unk.push(q.kp);
    else if (q.got === q.full) good.push(q.kp);
    else if (q.got === 0) need.push(q.kp);
    else part.push(q.kp);
  }
  h += `<div class="rpsec"><h3>本次考查点</h3><div class="kpgrid">
    <div class="kpcol good"><h5>做得好</h5>${good.map(k => `<span class="kptag">${esc(k)}</span>`).join('') || '<span style="font-size:11px;color:var(--ink3)">—</span>'}</div>
    <div class="kpcol part"><h5>部分做到</h5>${part.map(k => `<span class="kptag">${esc(k)}</span>`).join('') || '<span style="font-size:11px;color:var(--ink3)">—</span>'}</div>
    <div class="kpcol need"><h5>需要补</h5>${need.map(k => `<span class="kptag">${esc(k)}</span>`).join('') || '<span style="font-size:11px;color:var(--ink3)">—</span>'}</div>
    <div class="kpcol unk"><h5>暂不能判断</h5>${unk.map(k => `<span class="kptag">？${esc(k)}</span>`).join('') || '<span style="font-size:11px;color:var(--ink3)">—</span>'}</div>
  </div></div>`;

  // 失分题附录
  const lostAll = qs.filter(q => q.got != null && q.got < q.full);
  h += `<div class="rpsec"><details class="appendix"><summary>失分题附录（${lostAll.length} 题）</summary>
    <table><thead><tr><th>题号</th><th>得分</th><th>考查点</th><th>错因（原型示例）</th><th></th></tr></thead><tbody>
    ${lostAll.map(q => `<tr><td>第 ${q.n} 题</td><td>${q.got} / ${q.full}</td><td>${esc(q.kp)}</td><td>${esc(PR.QERR[q.n % PR.QERR.length])}</td>
      <td><a class="link" data-revq="${q.n}">去复核这题 ›</a></td></tr>`).join('')}
    </tbody></table></details></div>`;
  return h;
}

function qDetail(st, ex, qs, qn, released) {
  const q = qs.find(x => x.n === qn);
  if (!q) return '';
  let imgs = '';
  if (released) imgs = `<p style="font-size:12px;color:var(--warn)">原卷已释放，无法显示作答图；分数与批语不受影响。</p>`;
  else imgs = `<div class="qimgs">${[0, 1].map(i =>
    `<div class="qimg" data-z="${q.n * 10 + i}" data-cap="第 ${q.n} 题作答 · 原卷裁切 · 在线读取本机原卷，不另存副本">
      ${svgCrop(q.n * 10 + i, 150, 64)}<div class="cap">原卷裁切 · 在线读取本机原卷，不另存副本</div></div>`).join('')}</div>`;
  return `<div class="qdetail"><div class="qd-t">第 ${q.n} 题 · ${q.got ?? '—'} / ${q.full} 分
    <a class="link" data-revq="${q.n}" style="margin-left:10px">去复核这题 ›</a></div>
    <div class="qd-kp">考查点：${esc(q.kp)} · ${q.got === 0 ? '全部失分' : q.got === q.full ? '满分' : '部分得分'}${q.got < q.full ? ' · ' + esc(PR.QERR[q.n % PR.QERR.length]) : ''}</div>${imgs}</div>`;
}

function wireReportBody() {
  $$('#reader .qq').forEach(el => el.addEventListener('click', () => {
    curQ = curQ === +el.dataset.q ? null : +el.dataset.q;
    $$('#reader .qq').forEach(x => x.classList.toggle('sel', +x.dataset.q === curQ));
    const st = PR.byId(S.rsid), ex = PR.EXAMS.find(e => e.id === S.rexid);
    $('#qdet').innerHTML = qDetail(st, ex, PR.questions(st, ex), curQ, imagesReleased(ex.id));
    wireZoom();
    wireRevLinks();
  }));
  wireZoom();
  wireRevLinks();
}
function wireZoom() {
  $$('#reader .qimg').forEach(el => el.addEventListener('click', () => {
    openZoom(svgCrop(+el.dataset.z, 720, 300), el.dataset.cap);
  }));
}
function wireRevLinks() {
  $$('#reader [data-revq]').forEach(el => {
    if (el.dataset.bound) return;
    el.dataset.bound = '1';
    el.addEventListener('click', e => { e.stopPropagation(); goReviewToast(el.dataset.revq, true); });
  });
}
