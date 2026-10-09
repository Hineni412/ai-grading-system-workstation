// “出训练”页（合并原按章节训练与按学生训练）。依赖 overview.js 中的共享状态与工具函数。
const T = S.tr
const SORTS = { class: ['班级与学号', (a, b) => a.code.localeCompare(b.code)], scoreAsc: ['得分率 低→高', (a, b) => (a.score ?? 2) - (b.score ?? 2)],
  scoreDesc: ['得分率 高→低', (a, b) => (b.score ?? -1) - (a.score ?? -1)], weak: ['明显薄弱 多→少', (a, b) => weakCount(b) - weakCount(a) || (a.score ?? 2) - (b.score ?? 2)] }
const learnedSections = () => D.chapters.filter(c => c.learned).flatMap(c => c.sections.map(s => s.id))
const rangeTypes = () => D.types.filter(t => T.range.has(t.section.id) && (!T.presetTarget || t.key === T.presetTarget))

function filtered() {
  const min = T.min === '' ? null : Number(T.min) / 100, max = T.max === '' ? null : Number(T.max) / 100
  const q = T.search.trim()
  return D.students.filter(s => (!T.cls || s.cls === T.cls) && (!q || `${s.name}${s.code}`.includes(q))
    && (min == null || (s.score != null && s.score >= min)) && (max == null || (s.score != null && s.score <= max))
    && (!T.onlyWeak || weakCount(s) > 0)).sort(SORTS[T.sort][1])
}
function dropAdoption(reason) { if (T.adopted) { T.adopted = null; toast(reason) } }

actions.trPick = mode => { T.pickMode = mode; render() }
actions.trCls = cls => { T.cls = cls; render() }
actions.trPreset = arg => { const [min, max] = arg.split('-'); T.min = min; T.max = max; render() }
actions.trRow = (id, event) => {
  const rows = filtered().map(s => s.id)
  const next = !T.selected.has(id)
  const ids = event.shiftKey && T.anchor && rows.includes(T.anchor)
    ? rows.slice(Math.min(rows.indexOf(T.anchor), rows.indexOf(id)), Math.max(rows.indexOf(T.anchor), rows.indexOf(id)) + 1) : [id]
  for (const key of ids) next ? T.selected.add(key) : T.selected.delete(key)
  T.anchor = id
  dropAdoption('名单已改动：按普通多人同卷出卷，不再沿用推荐小组的核对。')
  render()
}
actions.trSelectFiltered = () => { filtered().forEach(s => T.selected.add(s.id)); dropAdoption('名单已改动：不再沿用推荐小组。'); render() }
actions.trUnselectFiltered = () => { filtered().forEach(s => T.selected.delete(s.id)); dropAdoption('名单已改动：不再沿用推荐小组。'); render() }
actions.trClear = () => { T.selected.clear(); T.anchor = null; dropAdoption('已清空名单。'); render() }
actions.trRangeLearned = () => { T.range = new Set(learnedSections()); T.presetTarget = null; dropAdoption('训练范围已变化，请重新采用推荐小组。'); render() }
actions.trRangeClear = () => { T.range.clear(); T.presetTarget = null; dropAdoption('训练范围已变化，请重新采用推荐小组。'); render() }
actions.trTargetClear = () => { T.presetTarget = null; render() }
actions.trChapter = id => {
  const ids = D.chapters.find(c => c.id === id).sections.map(s => s.id)
  const all = ids.every(key => T.range.has(key))
  ids.forEach(key => all ? T.range.delete(key) : T.range.add(key))
  T.presetTarget = null; dropAdoption('训练范围已变化，请重新采用推荐小组。'); render()
}
actions.trSection = id => { T.range.has(id) ? T.range.delete(id) : T.range.add(id); T.presetTarget = null; dropAdoption('训练范围已变化，请重新采用推荐小组。'); render() }
actions.trPurpose = p => { T.purpose = p; if (p === 'handout' && T.questionCount === 10) T.questionCount = 30; if (p === 'training' && T.questionCount > 12) T.questionCount = 10; render() }
actions.trMode = m => { T.paperMode = m; if (m === 'individual') dropAdoption('已改为一人一卷：不再沿用推荐小组。'); render() }
actions.trAdopt = id => {
  const group = groups().list.find(g => g.id === id)
  T.selected = new Set(group.members.map(s => s.id)); T.adopted = { id, size: group.members.length }; T.paperMode = 'shared'
  render(); toast(`已勾选该组 ${group.members.length} 人，出卷方式切到多人同卷；改动名单后按普通多人同卷处理。`)
}
actions.trGenerate = () => toast(T.purpose === 'wrong_book' ? `原型：为 ${T.selected.size} 名学生导出错题本。`
  : `原型：生成 ${T.paperMode === 'shared' ? 1 : T.selected.size} 份${T.purpose === 'handout' ? '讲义' : '训练卷'}草稿，进入“生成试卷”审核。`)

// 合成的推荐分组：仅示意“按所选范围、在左侧筛选结果内分组”的交互，不代表真实分组规则。
function groups() {
  const keys = new Set(rangeTypes().map(t => t.key))
  const pool = filtered().filter(s => s.score != null && weakCount(s, keys) > 0).sort((a, b) => a.ability - b.ability)
  const sizes = [Math.min(24, Math.ceil(pool.length * 0.4)), 10, 4, 3, 3]
  const list = []; let at = 0
  for (const size of sizes) {
    if (at + size > pool.length || size < 2) break
    const members = pool.slice(at, at + size); at += size + Math.floor(size / 3)
    const masteries = members.flatMap(s => [...keys].map(k => s.tiers[k]).filter(Boolean).map(t => t.mastery))
    const avg = masteries.reduce((a, v) => a + v, 0) / (masteries.length || 1)
    const targets = [...keys].filter(k => members.some(s => ['weak', 'unsteady'].includes(s.tiers[k]?.tier))).length
    list.push({ id: `g${list.length + 1}`, members, avg, spread: Math.max(...masteries) - Math.min(...masteries), targets,
      score: members.reduce((a, s) => a + s.score, 0) / members.length, questions: 6 + (members.length % 3) })
  }
  const grouped = new Set(list.flatMap(g => g.members.map(s => s.id)))
  return { list, unassigned: filtered().filter(s => !grouped.has(s.id)) }
}

function pickList() {
  const rows = filtered()
  const allIn = rows.length && rows.every(s => T.selected.has(s.id))
  return `<div class="pick-tools">
    <div class="line"><input type="search" data-model="search" value="${esc(T.search)}" placeholder="检索姓名／学号" aria-label="检索姓名或学号"></div>
    <div class="line"><span class="hint">班级</span>${['', ...new Set(D.students.map(s => s.cls))].map(c => `<button type="button" class="pill ${T.cls === c ? 'on' : ''}" data-act="trCls" data-arg="${c}">${c ? clsLabel(c) : '全部'}</button>`).join('')}</div>
    <div class="line"><span class="hint">考试得分率</span><input type="number" min="0" max="100" data-model="min" value="${T.min}" placeholder="不限" aria-label="得分率下限">% –<input type="number" min="0" max="100" data-model="max" value="${T.max}" placeholder="不限" aria-label="得分率上限">%</div>
    <div class="line"><span class="hint">快捷</span>${[['', '60', '低于 60%'], ['60', '80', '60–80%'], ['80', '', '80% 以上'], ['', '', '不限']].map(([a, b, l]) => `<button type="button" class="btn small" data-act="trPreset" data-arg="${a}-${b}">${l}</button>`).join('')}</div>
    <div class="line"><label><input type="checkbox" data-model="onlyWeak" ${T.onlyWeak ? 'checked' : ''}> 只看有明显薄弱</label><span style="margin-left:auto" class="hint">排序</span><select data-model="sort" aria-label="排序">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}" ${T.sort === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div></div>
    <div class="pick-summary"><span class="grow">筛出 <b>${rows.length}</b> 人 · 已选 <b>${T.selected.size}</b> 人</span>
    ${allIn ? `<button type="button" class="btn small" data-act="trUnselectFiltered">取消勾选这 ${rows.length} 人</button>` : `<button type="button" class="btn small primary" ${rows.length ? '' : 'disabled'} data-act="trSelectFiltered">勾选这 ${rows.length} 人</button>`}
    <button type="button" class="btn small" ${T.selected.size ? '' : 'disabled'} data-act="trClear">清空</button></div>
    <p class="hint" style="margin:6px 14px">点一行即勾选；按住 Shift 再点另一行，可连续勾选或取消中间所有人。</p>
    <div class="roster-list" data-keep-scroll="roster">${rows.map(s => `<div class="roster-row ${T.selected.has(s.id) ? 'on' : ''}" data-act="trRow" data-arg="${s.id}" role="checkbox" aria-checked="${T.selected.has(s.id)}" tabindex="0">
      <input type="checkbox" tabindex="-1" ${T.selected.has(s.id) ? 'checked' : ''} aria-hidden="true"><span><strong>${esc(s.name)}</strong><small>${clsLabel(s.cls)} · ${s.code}</small></span>
      <span class="score ${scoreClass(s)}">${s.score == null ? '无成绩' : pct(s.score)}</span><span class="weakn">${hasEvidence(s) ? `薄弱 ${weakCount(s)}` : '无证据'}</span></div>`).join('') || '<p class="empty">没有符合条件的学生</p>'}</div>`
}
function pickGroups() {
  const { list, unassigned } = groups()
  return `<p class="hint" style="margin:10px 14px 0">按中间勾选的训练范围，在 ${T.cls ? clsLabel(T.cls) : '全部学生'}${T.min || T.max ? `、得分率 ${T.min || 0}%–${T.max || 100}%` : ''} 中推荐可共用一卷的小组。采用后自动勾选成员并切到多人同卷。</p>
    ${list.map(g => `<div class="group-card ${T.adopted?.id === g.id ? 'on' : ''}"><strong>${[...new Set(g.members.map(s => s.cls))].map(c => `${clsLabel(c)} ${g.members.filter(s => s.cls === c).length} 人`).join(' · ')}</strong>
      <div class="stats"><span>${g.targets} 个训练目标</span><span>平均掌握度 <b>${pct(g.avg)}</b></span><span>组内差距 <b>${Math.round(g.spread * 100)}</b> 个百分点</span><span>考试得分率 <b>${pct(g.score)}</b></span><span>${g.questions} 道可用题</span></div>
      <div class="rank-chips" style="grid-column:auto">${g.members.slice(0, 10).map(s => chip(s, 'insufficient')).join('')}${g.members.length > 10 ? `<span class="hint">+${g.members.length - 10}</span>` : ''}</div>
      <div class="actions"><button type="button" class="btn ${T.adopted?.id === g.id ? '' : 'primary'} small" data-act="trAdopt" data-arg="${g.id}">${T.adopted?.id === g.id ? '已采用' : `采用（勾选这 ${g.members.length} 人）`}</button><button type="button" class="btn small" data-act="toast" data-arg="原型：展开名单与目标，可增减成员、删减目标（沿用现有“查看小组”）。">查看小组</button></div></div>`).join('') || '<p class="empty">当前范围与筛选下没有可成组的学生</p>'}
    <details class="fold"><summary>暂未推荐 ${unassigned.length} 人 · 查看原因</summary><p class="hint">正式页面沿用现有原因分桶：无直接失分证据、共用卷练习不足。可切回“名单勾选”单独安排。</p></details>`
}
function rangePanel() {
  const chosen = [...T.selected].map(id => byId.get(id))
  const sectionStat = section => {
    const keys = D.types.filter(t => t.section === section).map(t => t.key)
    const counts = { weak: 0, unsteady: 0, stable: 0, insufficient: 0 }
    for (const s of chosen) {
      const tiers = keys.map(k => s.tiers[k]?.tier).filter(Boolean)
      if (tiers.length) counts[TIERS.find(t => tiers.includes(t))] += 1
    }
    return counts
  }
  const learned = D.chapters.filter(c => c.learned)
  const target = T.presetTarget && typeByKey.get(T.presetTarget)
  return `<header><h2>训练范围</h2><span class="hint">已勾 ${T.range.size} 节</span></header>
    <div class="range-tools"><button type="button" class="btn small" data-act="trRangeLearned">全选已学（${esc(learned[0].label.split(' ')[0])}–${esc(learned.at(-1).label.split(' ')[0])}）</button><button type="button" class="btn small" data-act="trRangeClear">清空</button>
    <span class="legend" style="margin-left:auto">${TIERS.map(t => `<span><i class="t-${t}"></i>${TIER_LABEL[t]}</span>`).join('')}</span></div>
    ${target ? `<div class="adopted" style="margin:8px 14px">只练“${esc(target.name)}”（来自学情页） <button type="button" class="link" data-act="trTargetClear">改为整节</button></div>` : ''}
    <p class="hint" style="margin:6px 14px">${chosen.length ? `按已选 ${chosen.length} 名学生统计，每人取本节最弱档位` : '勾选学生后显示各节人数分布'}</p>
    ${D.chapters.map(chapter => {
      const ids = chapter.sections.map(s => s.id), n = ids.filter(id => T.range.has(id)).length
      return `<div class="range-chapter ${chapter.learned ? '' : 'unlearned'}"><div class="range-row ch"><label><input type="checkbox" data-act="trChapter" data-arg="${chapter.id}" ${n === ids.length ? 'checked' : ''} ${n && n < ids.length ? 'data-indeterminate="1"' : ''}>${esc(chapter.label)}${chapter.learned ? '' : ' <span class="hint">未学</span>'}</label><span></span></div>
      ${chapter.sections.map(section => { const c = sectionStat(section); return `<div class="range-row sec"><label><input type="checkbox" data-act="trSection" data-arg="${section.id}" ${T.range.has(section.id) ? 'checked' : ''}>${esc(section.label)}</label>
        <span class="stat">${chosen.length && TIERS.some(t => c[t]) ? `${tierBar(c)}${c.weak ? `<small>明显薄弱 ${c.weak} 人</small>` : ''}` : ''}</span></div>` }).join('')}</div>`
    }).join('')}`
}
function paperPanel() {
  const n = T.selected.size
  const reason = !n ? '请先在左侧勾选学生' : !T.range.size ? '请勾选训练范围' : T.purpose === 'training' && (T.questionCount < 8 || T.questionCount > 12) ? '训练卷题数为 8–12 题' : ''
  const note = { training: '回收批改，更新掌握度。批改调用模型、产生费用，批改前另行确认。', handout: '只打印，答案解析在末尾；不回收、不更新掌握度。', wrong_book: '收所选考试中、勾选章节内的错题；全册勾满时等同全部错题。每人一份 Word。' }[T.purpose]
  return `<header><h2>给已选学生 · ${n} 人出</h2></header><div class="panel-body">
    <div class="seg wide" role="group" aria-label="出卷用途">${[['training', '训练卷'], ['handout', '刷题讲义'], ['wrong_book', '错题本']].map(([k, l]) => `<button type="button" class="${T.purpose === k ? 'on' : ''}" data-act="trPurpose" data-arg="${k}">${l}</button>`).join('')}</div>
    <p class="note">${note}</p>
    ${T.purpose === 'wrong_book' ? '' : `<div class="seg wide" role="group" aria-label="出卷方式">${[['individual', '一人一卷'], ['shared', '多人同卷']].map(([k, l]) => `<button type="button" class="${T.paperMode === k ? 'on' : ''}" data-act="trMode" data-arg="${k}">${l}</button>`).join('')}</div>
    ${T.adopted ? `<div class="adopted">已采用推荐小组（${T.adopted.size} 人）。生成前按现有规则重新核对小组。</div>` : ''}
    <div class="grid2"><label>每卷题数<input type="number" data-model="questionCount" value="${T.questionCount}"><span class="hint">${T.purpose === 'training' ? '8–12 题' : '至少 1 题'}</span></label><label>难度上限<input type="number" data-model="difficultyMax" value="${T.difficultyMax}"><span class="hint">1–10 级</span></label></div>
    <details><summary>选题细则</summary><p class="note">同一题型最多 1 道 · 解答题最多 2 道 · 排除最近 3 次原题（与现有面板相同）</p></details>`}
    ${reason ? `<p class="blocked">${reason}</p>` : ''}
    <button type="button" class="btn primary" ${reason ? 'disabled' : ''} data-act="trGenerate">${T.purpose === 'wrong_book' ? '预览并导出错题本' : T.purpose === 'handout' ? '生成讲义草稿 →' : '生成训练卷草稿 →'}</button></div>`
}
window.renderTraining = () => `<div class="scope-bar"><strong>八年级上册</strong><span class="muted">本学期 ${D.sessionCount} 场考试 + 已发布训练</span><span class="hint" style="margin-left:auto">班级与得分率筛选已移到左侧学生栏</span></div>
  <div class="tr-layout"><section class="card"><header><h2>选学生</h2><div class="seg" role="group" aria-label="选人方式"><button type="button" class="${T.pickMode === 'list' ? 'on' : ''}" data-act="trPick" data-arg="list">名单勾选</button><button type="button" class="${T.pickMode === 'group' ? 'on' : ''}" data-act="trPick" data-arg="group">推荐小组</button></div></header>
  ${T.pickMode === 'list' ? pickList() : pickGroups()}</section><section class="card">${rangePanel()}</section><aside class="card tr-right">${paperPanel()}</aside></div>`

function onModel(event) {
  const el = event.target.closest('[data-model]')
  if (!el) return
  const key = el.dataset.model
  if (event.type === 'input' && el.type !== 'search') return
  T[key] = el.type === 'checkbox' ? el.checked : ['questionCount', 'difficultyMax'].includes(key) ? Number(el.value) : el.value
  const caret = el.type === 'search' ? el.selectionStart : null
  render()
  if (caret != null) { const next = document.querySelector('[data-model="search"]'); next.focus(); next.setSelectionRange(caret, caret) }
}
document.addEventListener('input', onModel)
document.addEventListener('change', onModel)
document.addEventListener('keydown', event => { if ((event.key === ' ' || event.key === 'Enter') && event.target.matches('.roster-row')) { event.preventDefault(); actions.trRow(event.target.dataset.arg, event) } })
new MutationObserver(() => document.querySelectorAll('[data-indeterminate]').forEach(el => { el.indeterminate = true })).observe(document.getElementById('view'), { childList: true, subtree: true })
render()
