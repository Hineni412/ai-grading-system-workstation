/* overview.js — 学情总览（行动页） */
function chipPair(list, max, pairs) {
  let h = '';
  list.slice(0, max).forEach(x => {
    pairs.push(x);
    h += `<span class="stuchip ${x.o.tier}" data-obs>${esc(x.st.name)} <b>${Math.round(x.o.m * 100)}%</b></span>`;
  });
  if (list.length > max) h += `<span class="stuchip more">+${list.length - max}</span>`;
  return h;
}
function chipGroups(a, pairs) {
  let h = '';
  for (const t of KT.TIERS) {
    const list = a.byTier[t];
    if (!list.length) continue;
    h += `<div class="pgroup"><div class="gt">${KT.TIER_LABEL[t]}（${list.length} 人）</div><div>${chipPair(list, 24, pairs)}</div></div>`;
  }
  return h;
}

function renderOverview() {
  const studs = currentStudents();
  const k = KT.kpis(studs);
  const pairs = [];

  // ---- KPI ----
  let h = `<div class="kpis">
    <div class="kpi"><div class="kl">有证据学生</div><div class="kv">${k.withEv}<small> / ${k.total} 人</small></div><div class="ks">本学期有作答证据的人数</div></div>
    <div class="kpi"><div class="kl">本学期平均得分率</div><div class="kv">${k.scoreRate == null ? '—' : Math.round(k.scoreRate * 100) + '%'}</div><div class="ks">有证据 ${k.withEv} 人参与</div></div>
    <div class="kpi"><div class="kl">至少 1 项明显薄弱的学生</div><div class="kv">${k.weakAny}<small> 人</small></div><div class="ks">明显薄弱 = 把握上界低于 60%</div></div>
    <div class="kpi"><div class="kl">有学生明显薄弱</div><div class="kv">${k.weakSkills}<small> 项技能</small> · ${k.weakTopics}<small> 项知识点</small></div><div class="ks">至少 1 人明显薄弱即计入</div></div>
  </div>`;

  h += `<div class="ovcols"><div>`;

  // ---- 本周建议优先处理 ----
  const top5 = k.weakSkillItems.slice(0, 5);
  h += `<div class="sec-t">本周建议优先处理<span class="sub">按明显薄弱人数排序 · 技能</span></div>`;
  if (!top5.length) h += `<div class="pcard"><div class="ph">当前范围没有"有学生明显薄弱"的技能。</div></div>`;
  top5.forEach((x, i) => {
    const { it, a } = x;
    const wn = a.counts.weak;
    const relT = KT.relatedOf(it).map(r => r.item.name).slice(0, 3).join('、');
    h += `<div class="pcard" data-pi="${i}">
      <div class="ph"><span class="rank">${i + 1}</span>
        <div><div class="pname">${esc(it.name)}</div>
        <div class="pbelong">属于 ${esc(it.secName)} · ${esc(it.chName)}</div>
        ${relT ? `<div class="pbelong">相关知识点：${esc(relT)}</div>` : ''}</div>
        <span class="pev">有证据 ${a.evidence} 人</span></div>
      <div class="pbar">${tierBar(a.counts)}</div>
      <div class="pcounts">${tierCounts(a.counts)}</div>
      <div class="pchips">${chipPair(a.byTier.weak, 8, pairs)}</div>
      <div class="pact">
        <button class="btn primary small" data-act="train">给这 ${wn} 人出训练卷</button>
        <button class="btn small" data-act="wrong">看错题</button>
        <span class="link" data-act="more">展开分层名单</span>
      </div>
      <div class="pdetail">${chipGroups(a, pairs)}</div>
    </div>`;
  });
  h += `<a class="link" id="seeAllWeak">查看全部 ${k.weakSkills + k.weakTopics} 项有学生明显薄弱 →</a>`;
  h += `</div><div>`;

  // ---- 需要个别关注的学生 ----
  const attn = studs.map(st => ({ st, sa: KT.studentAgg(st) }))
    .filter(x => x.sa.weak > 0)
    .sort((x, y) => y.sa.weak - x.sa.weak || x.st.scoreRate - y.st.scoreRate)
    .slice(0, 8);
  h += `<div class="sec-t">需要个别关注的学生<span class="sub">按明显薄弱项数排序</span></div><div class="attcard">`;
  for (const x of attn) {
    const c = { weak: x.sa.weak, unsteady: x.sa.unsteady, stable: x.sa.stable, insufficient: x.sa.insufficient };
    h += `<div class="attrow" data-sid="${x.st.id}">
      <div><span class="anm">${esc(x.st.name)}</span> <span class="acls">${x.st.cls} · ${x.st.code}</span></div>
      <div class="ascore">得分率 ${pct(x.st.scoreRate)}</div>
      <div class="aweak">明显薄弱 ${x.sa.weak} 项</div>
      ${tierBar(c, true)}
    </div>`;
  }
  h += `</div>`;

  // ---- 本学期考查进度：计数单位一律为"项"（与顶部 KPI 同一口径） ----
  h += `<div class="sec-t">本学期考查进度<span class="sub">计数单位为项；条为该章各项的群体档位分布</span></div><div class="prog">`;
  const dormant = KT.chapters.filter(c => !c.examined);
  for (const ch of KT.chapters.filter(c => c.examined)) {
    const itemTiers = { weak: 0, unsteady: 0, stable: 0, insufficient: 0 };
    let ms = 0, mn = 0, evItems = 0, totItems = 0, weakItems = 0;
    for (const sec of ch.sections) for (const it of [...sec.topics, ...sec.skills]) {
      totItems++;
      const a = KT.agg(it, studs);
      if (a.evidence) evItems++;
      if (a.counts.weak > 0) weakItems++;
      itemTiers[KT.groupTier(a)]++;
      if (a.groupMastery != null) { ms += a.groupMastery; mn++; }
    }
    h += `<div class="prow"><div class="pnm">${esc(ch.name)}<span class="pm">群体掌握度 ${pct(mn ? ms / mn : null)} · 有证据 ${evItems}/${totItems} 项 · 有学生明显薄弱的 ${weakItems} 项</span></div>
      ${tierBar(itemTiers)}
      <div class="tcount"><span style="color:var(--ink3)">按项目的群体档位：</span>
        <span class="cw">明显薄弱 <b>${itemTiers.weak}</b> 项</span>
        <span class="cu">还不稳 <b>${itemTiers.unsteady}</b> 项</span>
        <span class="cs">较稳定 <b>${itemTiers.stable}</b> 项</span>
        <span class="ci">证据不足 <b>${itemTiers.insufficient}</b> 项</span></div></div>`;
  }
  const dn = dormant.reduce((s, d) => s + d.counts.t + d.counts.s, 0);
  h += `<div class="prow dorm">尚未考查：${dormant.map(d => esc(d.name)).join('、')}（${dormant.length} 章，共 ${dn} 项）</div></div>`;
  h += `</div></div>`;

  // ---- 全部学生 ----
  h += `<details class="allstu"><summary>全部 ${studs.length} 名学生</summary><div class="asbody">
    <p style="margin:6px 0 8px"><input class="app-input" id="stuQ" placeholder="姓名或学号" style="width:180px">
    <span style="font-size:11.5px;color:var(--ink3);margin-left:10px">点表头排序</span></p>
    <table class="dt" id="stuTable"><thead><tr>
      <th data-k="name">姓名（学号）</th><th data-k="cls">班级</th><th data-k="score">得分率</th>
      <th>知识点 薄/稳/较/不</th><th>技能 薄/稳/较/不</th><th data-k="weak">明显薄弱</th></tr></thead><tbody></tbody></table>
  </div></details>`;

  $('#v-overview').innerHTML = h;

  // ---- 交互 ----
  wireChips($('#v-overview'), pairs);
  $$('#v-overview .pcard').forEach(card => {
    card.addEventListener('click', e => {
      const act = e.target.closest('[data-act]');
      if (act) {
        e.stopPropagation();
        const wn = card.querySelector('[data-act=train]').textContent.match(/\d+/)[0];
        if (act.dataset.act === 'train') toast(`原型：将带着这 ${wn} 人和该技能进入按章节训练`);
        else if (act.dataset.act === 'wrong') toast('原型：打开该技能的错题列表');
        else card.classList.toggle('open');
        return;
      }
      card.classList.toggle('open');
    });
  });
  $$('#v-overview .attrow').forEach(r => r.addEventListener('click', () => toast('原型：进入学生作答证据')));
  const see = $('#seeAllWeak');
  if (see) see.addEventListener('click', () => {
    S.filter = 'weak';
    $$('#filterChips .fchip').forEach(x => x.classList.toggle('on', x.dataset.f === 'weak'));
    switchView('map');
  });

  // 学生表
  const tb = $('#stuTable tbody');
  const data = studs.map(st => ({ st, sa: KT.studentAgg(st) }));
  let sortK = 'name', asc = true, q = '';
  function byKind(st, kind) {
    const c = { weak: 0, unsteady: 0, stable: 0, insufficient: 0 };
    for (const it of KT.evidenceItems) {
      if (it.kind !== kind) continue;
      const o = it.obs[st.id]; if (o) c[o.tier]++;
    }
    return `${c.weak}/${c.unsteady}/${c.stable}/${c.insufficient}`;
  }
  function drawRows() {
    let list = data.filter(x => !q || x.st.name.includes(q) || x.st.code.includes(q));
    list.sort((x, y) => {
      let d = 0;
      if (sortK === 'name') d = x.st.code.localeCompare(y.st.code);
      else if (sortK === 'cls') d = x.st.cls.localeCompare(y.st.cls, 'zh');
      else if (sortK === 'score') d = x.st.scoreRate - y.st.scoreRate;
      else d = x.sa.weak - y.sa.weak;
      return asc ? d : -d;
    });
    tb.innerHTML = list.map(x => `<tr>
      <td>${esc(x.st.name)}<span style="color:var(--ink3)">（${x.st.code}）</span></td>
      <td>${x.st.cls}</td><td>${pct(x.st.scoreRate)}</td>
      <td>${byKind(x.st, 'topic')}</td><td>${byKind(x.st, 'skill')}</td>
      <td><b style="color:${x.sa.weak ? '#a63a20' : 'inherit'}">${x.sa.weak}</b></td></tr>`).join('');
  }
  $('#stuQ').addEventListener('input', e => { q = e.target.value.trim(); drawRows(); });
  $$('#stuTable th[data-k]').forEach(th => th.addEventListener('click', () => {
    const k2 = th.dataset.k;
    if (sortK === k2) asc = !asc; else { sortK = k2; asc = true; }
    drawRows();
  }));
  drawRows();
}
