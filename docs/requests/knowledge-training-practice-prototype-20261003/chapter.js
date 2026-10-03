/* chapter.js — 按章节训练页签：左列章节、中列推荐小组 */

function renderChapterTab() {
  renderChapList();
  renderChapGroups();
  renderPanel('chapter', $('#panel-chapter'));
}

// 当前选中章/节 → {title, skillIds, secKey}
function chapTarget() {
  for (const ch of KT.chapters) {
    if (ch.id === S.chapSel) return { title: ch.name, skillIds: KT.chapterSkillIds(ch), key: ch.id };
    for (const sec of ch.sections)
      if (sec.id === S.chapSel) return { title: ch.name + ' · ' + sec.name, skillIds: KT.sectionSkillIds(sec), key: sec.id };
  }
  const sec = KT.chapters[0].sections[0];
  return { title: KT.chapters[0].name + ' · ' + sec.name, skillIds: KT.sectionSkillIds(sec), key: sec.id };
}
const grpKey = (key, i) => S.cls + '|' + (S.minScore ?? '') + '|' + key + '|' + i;

// 当前范围的小组（含成员删除应用）
function currentGroups() {
  const t = chapTarget();
  const { groups, ungrouped } = KT.buildGroups(t.skillIds, scopedStudents());
  return {
    t, ungrouped,
    groups: groups.map((g, i) => {
      const rem = S.grpRemoved[grpKey(t.key, i)];
      const members = rem ? g.members.filter(st => !rem.has(st.id)) : g.members;
      return { idx: i, targets: g.targets, members };
    }).filter(g => g.members.length >= 2),
  };
}
function adoptedGroup() {
  const { t, groups } = currentGroups();
  const ai = S.adopted[t.key];
  return ai != null ? groups.find(g => g.idx === ai) || null : null;
}

// ---------- 左列：章节 ----------
function renderChapList() {
  const el = $('#chapList');
  const scoped = scopedStudents();
  let h = `<div class="box chaplist"><div class="box-h">章 / 节</div>
    <div class="tblegend">
      <span class="lg"><i class="sw w"></i>明显薄弱</span><span class="lg"><i class="sw u"></i>还不稳</span><span class="lg"><i class="sw s"></i>较稳定</span><span class="lg"><i class="sw i"></i>证据不足</span>
      <span class="lb">按当前范围学生统计</span>
    </div>`;
  for (const ch of KT.chapters) {
    const cc = KT.tierCounts(scoped, KT.chapterSkillIds(ch));
    h += `<div class="chitem">
      <div class="chnm ${S.chapSel === ch.id ? 'sel' : ''}" data-sel="${ch.id}">
        ${esc(ch.name)}<span class="cweak">${cc.weak ? '明显薄弱 ' + cc.weak + ' 人' : ''}</span>
      </div>`;
    for (const sec of ch.sections) {
      const c = KT.tierCounts(scoped, KT.sectionSkillIds(sec));
      h += `<div class="sitem ${S.chapSel === sec.id ? 'sel' : ''}" data-sel="${sec.id}">
        <span class="sn">${esc(sec.name)}</span>
        ${tierBar(c)}
        <span class="sw">${c.weak ? '明显薄弱 ' + c.weak + ' 人' : ''}</span>
      </div>`;
    }
    h += `</div>`;
  }
  h += `</div>`;
  el.innerHTML = h;
  $$('[data-sel]', el).forEach(x => x.addEventListener('click', () => {
    S.chapSel = x.dataset.sel;
    renderChapterTab();
  }));
}

// ---------- 中列：推荐小组 ----------
function sortGroups(groups) {
  const arr = groups.slice();
  const key = {
    count: g => -KT.groupStats(g).n,
    weaker: g => KT.groupStats(g).avgMastery ?? 9,
    close: g => KT.groupStats(g).spread ?? 9,
  }[S.grpSort];
  return arr.sort((a, b) => key(a) - key(b));
}

function renderChapGroups() {
  const el = $('#chapGroups');
  const { t, groups, ungrouped } = currentGroups();
  const ung = ungrouped.noEvidence.length + ungrouped.unfit.length;
  const adopted = S.adopted[t.key];
  let h = `<div class="grphead">
    <span class="gt">${esc(t.title)}</span>
    <span class="gs">推荐 ${groups.length} 组 · 暂未成组 ${ung} 人</span>
    <select id="grpSort">
      <option value="count" ${S.grpSort === 'count' ? 'selected' : ''}>人数多优先</option>
      <option value="weaker" ${S.grpSort === 'weaker' ? 'selected' : ''}>成员更薄弱优先</option>
      <option value="close" ${S.grpSort === 'close' ? 'selected' : ''}>组内水平更接近优先</option>
    </select>
  </div>`;

  if (!groups.length) h += `<div class="box"><div class="muted" style="padding:16px 14px">当前范围内没有可推荐的同质小组。</div></div>`;
  for (const g of sortGroups(groups)) {
    const st = KT.groupStats(g);
    const isAd = adopted === g.idx;
    const open = S.grpOpen.has(grpKey(t.key, g.idx));
    const clsTxt = Object.entries(st.clsC).map(([c, n]) => `${c} ${n}`).join(' · ');
    h += `<div class="gcard ${isAd ? 'adopted' : ''} ${open ? 'mopen' : ''}" data-g="${g.idx}">
      <div class="gh">
        <div class="gt1">
          <span class="gnm">第${g.idx + 1}组 · ${st.n} 人</span>
          <span class="gcls">（${clsTxt}）</span>
          ${isAd ? '<span class="gadopt">已采用</span>' : ''}
        </div>
        <div class="gchips">${g.targets.map(id => `<span class="skchip">${esc(KT.skillById[id].name)}</span>`).join('')}</div>
        <div class="gstats">
          <span>目标平均掌握度 <b>${pct(st.avgMastery)}</b>${st.minM != null ? `（${pct(st.minM)}–${pct(st.maxM)}）` : ''}</span>
          <span>组内差距 <b>${pct(st.spread)}</b></span>
          <span>考试平均得分率 ${scoreBadge(st.scoreRate)}</span>
          <span>适合难度 <b>${st.band[0]}–${st.band[1]}</b> 级</span>
          <span>适用题 <b>${st.bank}</b> 道</span>
        </div>
      </div>
      <div class="gact">
        <button class="btn small" data-mem="${g.idx}">${open ? '收起名单' : '名单'}</button>
        <button class="btn small ${isAd ? 'ghost' : 'primary'}" data-adopt="${g.idx}">${isAd ? '取消采用' : '采用此组'}</button>
      </div>
      <div class="gmem">`;
    for (const { st: m, m: mv } of st.perM) {
      const wnames = KT.weakSkillsOf(m, g.targets).map(id => KT.skillById[id].name).join('、');
      h += `<div class="mrow"><span class="mnm">${esc(m.name)}</span><span class="mcls">${m.cls}</span>
        <span class="mweak" title="${esc(wnames)}">${esc(wnames || '—')}</span>
        <span class="muted">${pct(mv)}</span>
        <span class="mx" data-rm="${g.idx}|${m.id}" title="移出本组">×</span></div>`;
    }
    h += `</div></div>`;
  }

  h += `<details class="ungrp" id="ungrp"><summary>暂未成组 ${ung} 人</summary><div class="ub">
    <div class="ubt">无直接失分证据（${ungrouped.noEvidence.length}）</div>`;
  for (const x of ungrouped.noEvidence)
    h += `<div class="urow"><span class="unm">${esc(x.st.name)}</span> <span class="muted">${x.st.cls}</span><span class="urs">${esc(x.reason)}</span></div>`;
  if (!ungrouped.noEvidence.length) h += `<div class="muted">无</div>`;
  h += `<div class="ubt">技能需要或适合难度未匹配（${ungrouped.unfit.length}）</div>`;
  for (const x of ungrouped.unfit)
    h += `<div class="urow"><span class="unm">${esc(x.st.name)}</span> <span class="muted">${x.st.cls}</span><span class="urs">${esc(x.reason)}</span></div>`;
  if (!ungrouped.unfit.length) h += `<div class="muted">无</div>`;
  h += `</div></details>`;

  el.innerHTML = h;
  $('#grpSort').addEventListener('change', e => { S.grpSort = e.target.value; renderChapGroups(); });
  $$('[data-mem]', el).forEach(b => b.addEventListener('click', () => {
    const k = grpKey(t.key, Number(b.dataset.mem));
    S.grpOpen.has(k) ? S.grpOpen.delete(k) : S.grpOpen.add(k);
    renderChapGroups();
  }));
  $$('[data-adopt]', el).forEach(b => b.addEventListener('click', () => {
    const i = Number(b.dataset.adopt);
    if (S.adopted[t.key] === i) delete S.adopted[t.key];
    else { S.adopted[t.key] = i; toast(`已采用第${i + 1}组，右侧面板将按该组出卷`); }
    renderChapterTab();
  }));
  $$('[data-rm]', el).forEach(x => x.addEventListener('click', () => {
    const [gi, sid] = x.dataset.rm.split('|');
    const k = grpKey(t.key, Number(gi));
    (S.grpRemoved[k] = S.grpRemoved[k] || new Set()).add(sid);
    renderChapterTab(); renderPanel('chapter', $('#panel-chapter'));
  }));
}

// 所有脚本就绪后初始渲染（默认页签：按学生训练）
rerender();
