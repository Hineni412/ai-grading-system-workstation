// 原型共享状态、工具函数与“学情”页（合并原学情总览与知识结构）。
const D = window.PROTO
const S = window.APP = {
  tab: 'overview',
  ov: { cls: '', order: 'weak', focus: null, showAllItems: false, showTable: false, tableSort: 'weak' },
  tr: { pickMode: 'list', cls: '', min: '', max: '', onlyWeak: false, sort: 'class', search: '', selected: new Set(), anchor: null,
    range: new Set(D.chapters.filter(c => c.learned).flatMap(c => c.sections.map(s => s.id))), presetTarget: null,
    adopted: null, purpose: 'training', paperMode: 'individual', questionCount: 10, difficultyMax: 8 },
}
const actions = {}
const esc = text => String(text).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch])
const pct = value => value == null ? '—' : `${Math.round(value * 100)}%`
const TIERS = ['weak', 'unsteady', 'stable', 'insufficient']
const TIER_LABEL = { weak: '明显薄弱', unsteady: '还不稳', stable: '较稳定', insufficient: '证据不足' }
const clsLabel = cls => `${cls} 班`
const byId = new Map(D.students.map(s => [s.id, s]))
const typeByKey = new Map(D.types.map(t => [t.key, t]))

function scopeStudents(cls) { return D.students.filter(s => !cls || s.cls === cls) }
function stats(type, list) {
  const out = { weak: 0, unsteady: 0, stable: 0, insufficient: 0, evidence: 0, mastery: null, members: [] }
  let sum = 0
  for (const s of list) {
    const t = s.tiers[type.key]
    if (!t) continue
    out[t.tier] += 1; out.evidence += 1; sum += t.mastery
    out.members.push({ s, tier: t.tier, mastery: t.mastery })
  }
  if (out.evidence) out.mastery = sum / out.evidence
  return out
}
function heat(st) {
  if (!st.evidence) return 'none'
  const r = st.weak / st.evidence
  return r === 0 ? 0 : r <= 0.1 ? 1 : r <= 0.2 ? 2 : r <= 0.3 ? 3 : r <= 0.4 ? 4 : 5
}
function tierBar(counts, big = false) {
  const total = TIERS.reduce((sum, t) => sum + (counts[t] || 0), 0)
  if (!total) return '<div class="tierbar"></div>'
  return `<div class="tierbar${big ? ' big' : ''}" aria-label="${TIERS.map(t => `${TIER_LABEL[t]} ${counts[t] || 0} 人`).join('，')}">${TIERS.filter(t => counts[t])
    .map(t => `<i class="t-${t}" style="flex:${counts[t]}">${big ? counts[t] : ''}</i>`).join('')}</div>`
}
function studentCounts(s, keys) {
  const c = { weak: 0, unsteady: 0, stable: 0, insufficient: 0 }
  for (const [key, t] of Object.entries(s.tiers)) if (!keys || keys.has(key)) c[t.tier] += 1
  return c
}
const weakCount = (s, keys) => studentCounts(s, keys).weak
const hasEvidence = s => Object.keys(s.tiers).length > 0
function scoreClass(s) { return s.score == null ? 'none' : s.score >= 0.8 ? 'high' : s.score >= 0.6 ? 'mid' : 'low' }
function chip(s, tier, extra = '') { return `<span class="chip ${tier}">${esc(s.name)} <small>${pct(s.score)}${extra}</small></span>` }
let toastTimer = null
function toast(message) {
  document.querySelector('.toast')?.remove()
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = message; document.body.append(el)
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.remove(), 3200)
}

const TABS = [['overview', '学情'], ['training', '出训练'], ['paper', '生成试卷']]
function render() {
  document.getElementById('tabs').innerHTML = TABS.map(([id, label]) => `<button type="button" class="${S.tab === id ? 'on' : ''}" data-act="tab" data-arg="${id}">${label}</button>`).join('')
  document.getElementById('page-title').textContent = TABS.find(t => t[0] === S.tab)[1]
  document.getElementById('student-total').textContent = D.students.length
  const view = document.getElementById('view')
  const scroll = [...view.querySelectorAll('[data-keep-scroll]')].map(el => [el.dataset.keepScroll, el.scrollTop])
  view.innerHTML = S.tab === 'overview' ? renderOverview() : S.tab === 'training' ? window.renderTraining() : '<div class="card empty">“生成试卷”页保持现状（审核草稿、打印、回收），原型中不展开。</div>'
  for (const [key, top] of scroll) { const el = view.querySelector(`[data-keep-scroll="${key}"]`); if (el) el.scrollTop = top }
}
actions.tab = id => { S.tab = id; render() }

// ---------- 学情页 ----------
function rankedTypes(list) {
  return D.types.filter(t => t.examined).map(t => ({ t, st: stats(t, list) })).filter(x => x.st.evidence)
    .sort((a, b) => b.st.weak - a.st.weak || b.st.unsteady - a.st.unsteady || a.st.mastery - b.st.mastery)
}
function scopeBar(cls, act) {
  const classes = [...new Set(D.students.map(s => s.cls))]
  return `<div class="scope-bar" role="group" aria-label="学生范围"><strong>八年级上册</strong><span class="muted">本学期 ${D.sessionCount} 场考试 + 已发布训练</span>
    <button type="button" class="pill ${!cls ? 'on' : ''}" data-act="${act}" data-arg="">全部学生</button>${classes.map(c => `<button type="button" class="pill ${cls === c ? 'on' : ''}" data-act="${act}" data-arg="${c}">${clsLabel(c)}</button>`).join('')}
    <button type="button" class="link" style="margin-left:auto" data-act="toast" data-arg="口径说明：有证据学生＝该项有作答观测的学生；明显薄弱占比＝明显薄弱人数 ÷ 该项有证据人数。与原两页口径相同。">口径说明</button></div>`
}
actions.toast = message => toast(message)
actions.ovClass = cls => { S.ov.cls = cls; S.ov.focus = null; render() }
actions.ovOrder = order => { S.ov.order = order; render() }
actions.ovFocus = key => { S.ov.focus = S.ov.focus === key ? null : key; render() }
actions.ovClose = () => { S.ov.focus = null; render() }
actions.ovMore = () => { S.ov.showAllItems = !S.ov.showAllItems; render() }
actions.ovTable = () => { S.ov.showTable = !S.ov.showTable; render() }
actions.ovSort = key => { S.ov.tableSort = key; render() }
actions.student = id => toast(`原型：进入“${byId.get(id).name}”的作答证据页，返回入口为学情。`)
actions.wrong = key => toast(`原型：进入“${typeByKey.get(key).name}”的群体错题，返回学情。`)
actions.ovTrain = key => {
  const type = typeByKey.get(key)
  const weak = stats(type, scopeStudents(S.ov.cls)).members.filter(m => m.tier === 'weak').map(m => m.s.id)
  Object.assign(S.tr, { pickMode: 'list', selected: new Set(weak), range: new Set([type.section.id]), presetTarget: key, adopted: null, paperMode: 'individual' })
  S.tab = 'training'; render()
  toast(`已带入 ${weak.length} 名明显薄弱学生，训练范围限定为“${type.section.label}”中的“${type.name}”。`)
}

function renderOverview() {
  const list = scopeStudents(S.ov.cls)
  const evidence = list.filter(hasEvidence)
  const scored = list.filter(s => s.score != null)
  const weakStudents = evidence.filter(s => weakCount(s) > 0)
  const ranked = rankedTypes(list)
  const top = new Map(ranked.filter(x => x.st.weak > 0).slice(0, 5).map((x, i) => [x.t.key, i + 1]))
  const metrics = `<dl class="metrics">
    <div class="card metric"><dt>有证据学生</dt><dd>${evidence.length}<small>/ ${list.length} 人</small></dd></div>
    <div class="card metric"><dt>本学期平均得分率</dt><dd>${pct(scored.reduce((a, s) => a + s.score, 0) / (scored.length || 1))}<small>有成绩 ${scored.length} 人</small></dd></div>
    <div class="card metric"><dt>至少 1 项明显薄弱的学生</dt><dd>${weakStudents.length}<small>人 / 有证据 ${evidence.length} 人</small></dd></div></dl>`
  const head = `<header><h2>题型</h2><div class="seg" role="group" aria-label="排列方式"><button type="button" class="${S.ov.order === 'weak' ? 'on' : ''}" data-act="ovOrder" data-arg="weak">薄弱优先</button><button type="button" class="${S.ov.order === 'map' ? 'on' : ''}" data-act="ovOrder" data-arg="map">按教材章节</button></div>
    ${S.ov.order === 'weak' ? '<span class="hint">按明显薄弱人数排序；前 5 项为本周建议优先处理</span>' : `<span class="legend hint">${[0, 1, 2, 3, 4, 5].map(i => `<span><i class="heat-${i}"></i>${['0', '≤10%', '≤20%', '≤30%', '≤40%', '>40%'][i]}</span>`).join('')}<span><i class="heat-none"></i>无证据</span></span>`}</header>`
  const body = S.ov.order === 'weak' ? weakList(ranked, top, list) : mapView(list, top)
  return `${scopeBar(S.ov.cls, 'ovClass')}${metrics}<div class="ov-layout"><section class="card">${head}${body}</section><aside class="card ov-side" data-keep-scroll="ov-side">${S.ov.focus ? detail(typeByKey.get(S.ov.focus), list, top) : attention(list)}</aside></div>`
}
function weakList(ranked, top, list) {
  const shown = S.ov.showAllItems ? ranked : ranked.slice(0, 10)
  const unexamined = D.chapters.filter(c => !c.learned)
  return `<div class="rank-list">${shown.map(({ t, st }, i) => {
    const rank = top.get(t.key)
    const weak = st.members.filter(m => m.tier === 'weak').sort((a, b) => (a.s.score ?? 1) - (b.s.score ?? 1))
    return `<button type="button" class="rank-row ${rank ? 'top' : ''} ${S.ov.focus === t.key ? 'on' : ''}" data-act="ovFocus" data-arg="${t.key}">
      <span class="rank-no">${i + 1}</span><span class="rank-name"><strong>${esc(t.name)}</strong><small>${esc(t.chapter.label)} · ${esc(t.section.label)}</small></span>
      ${tierBar(st)}<span class="rank-weak"><b>${st.weak} 人明显薄弱</b>有证据 ${st.evidence} 人</span>
      ${rank ? `<span class="rank-chips">${weak.slice(0, 8).map(m => chip(m.s, 'weak')).join('')}${weak.length > 8 ? `<span class="hint">+${weak.length - 8}</span>` : ''}</span>` : ''}</button>`
  }).join('')}</div>
  ${ranked.length > 10 ? `<div class="more-row"><button type="button" class="link" data-act="ovMore">${S.ov.showAllItems ? '收起' : `显示其余 ${ranked.length - 10} 项`}</button></div>` : ''}
  <p class="hint" style="padding:0 14px 12px">尚未考查：${unexamined.map(c => esc(c.label)).join('、')}（${unexamined.length} 章，共 ${D.types.filter(t => !t.examined).length} 项）。切换“按教材章节”可查看全册结构。</p>`
}
function mapView(list, top) {
  const chapterBlock = chapter => {
    const items = D.types.filter(t => t.chapter === chapter)
    const all = items.map(t => stats(t, list))
    const ev = all.filter(st => st.evidence)
    const mastery = ev.length ? ev.reduce((a, st) => a + st.mastery, 0) / ev.length : null
    return `<div class="map-chapter"><h3>${esc(chapter.label)}<span>${ev.length ? `群体掌握度 ${pct(mastery)} · ` : ''}有证据 ${ev.length}/${items.length} 项</span></h3>
      ${chapter.sections.map(section => `<div class="map-section"><span>${esc(section.label)}</span><div class="map-cells">${items.filter(t => t.section === section).map(t => {
        const st = stats(t, list); const rank = top.get(t.key)
        return `<button type="button" class="cell heat-${heat(st)} ${S.ov.focus === t.key ? 'on' : ''}" data-act="ovFocus" data-arg="${t.key}">${rank ? `<span class="badge">优先 ${rank}</span>` : ''}<strong>${esc(t.name)}</strong><small>${st.evidence ? `明显薄弱 ${st.weak} / 有证据 ${st.evidence} 人` : '尚无证据'}</small>${tierBar(st)}</button>`
      }).join('')}</div></div>`).join('')}</div>`
  }
  const examined = D.chapters.filter(c => c.learned), rest = D.chapters.filter(c => !c.learned)
  return `${examined.map(chapterBlock).join('')}<details class="fold"><summary>尚未考查的 ${rest.length} 章</summary>${rest.map(chapterBlock).join('')}</details>`
}
function attention(list) {
  const rows = list.filter(s => weakCount(s) > 0).sort((a, b) => weakCount(b) - weakCount(a) || (a.score ?? 1) - (b.score ?? 1))
  const sorters = { name: (a, b) => a.code.localeCompare(b.code), score: (a, b) => (a.score ?? 2) - (b.score ?? 2), weak: (a, b) => weakCount(b) - weakCount(a) }
  const table = S.ov.showTable ? `<table class="roster"><thead><tr>${[['name', '姓名 / 学号'], ['score', '得分率'], ['weak', '明显薄弱']].map(([k, l]) => `<th><button type="button" data-act="ovSort" data-arg="${k}">${l}${S.ov.tableSort === k ? ' ▾' : ''}</button></th>`).join('')}</tr></thead>
    <tbody>${[...list].sort(sorters[S.ov.tableSort]).map(s => `<tr><td><button type="button" class="link" data-act="student" data-arg="${s.id}">${esc(s.name)}</button> <span class="hint">${clsLabel(s.cls)} · ${s.code}</span></td><td>${s.score == null ? '无成绩' : pct(s.score)}</td><td>${hasEvidence(s) ? weakCount(s) + ' 项' : '无证据'}</td></tr>`).join('')}</tbody></table>` : ''
  return `<section class="side-section"><h3>需要个别关注的学生</h3><span class="hint">有明显薄弱项的前 8 名，按明显薄弱项数、得分率排序；点题型可看该题型详情</span>
    ${rows.slice(0, 8).map(s => `<button type="button" class="student-btn" data-act="student" data-arg="${s.id}"><strong>${esc(s.name)}</strong><span>${pct(s.score)}</span><b>明显薄弱 ${weakCount(s)} 项</b><small>${clsLabel(s.cls)} · ${s.code}</small>${tierBar(studentCounts(s))}</button>`).join('')}</section>
    <section class="side-section"><button type="button" class="link" data-act="ovTable">${S.ov.showTable ? '收起全部学生' : `全部 ${list.length} 名学生 ▸`}</button>${table}</section>`
}
function detail(type, list, top) {
  const st = stats(type, list)
  const lo = Math.max(0, st.mastery - 0.12), hi = Math.min(1, st.mastery + 0.1)
  const group = tier => st.members.filter(m => m.tier === tier).sort((a, b) => (a.s.score ?? 1) - (b.s.score ?? 1))
  const weak = group('weak')
  return `<section class="side-section"><div class="detail-title"><div><span class="detail-type">题型${top.get(type.key) ? ` · 本周优先 ${top.get(type.key)}` : ''}</span><h2>${esc(type.name)}</h2></div><button type="button" class="btn small" data-act="ovClose" aria-label="关闭详情">✕</button></div>
    <span class="hint">属于 ${esc(type.chapter.label)} · ${esc(type.section.label)}</span><p class="note" style="margin:0">${esc(type.definition)}</p>
    <div class="kv"><span class="hint">群体掌握度</span><strong>${pct(st.mastery)}</strong><span class="hint">80% 群体区间 ${pct(lo)}–${pct(hi)}</span></div>
    ${tierBar(st, true)}<span class="hint">有证据 ${st.evidence} 人 · ${TIERS.map(t => `${TIER_LABEL[t]} ${st[t]}`).join(' · ')}</span></section>
    <section class="side-section"><h3>明显薄弱的 ${weak.length} 人</h3><div class="rank-chips" style="grid-column:auto">${weak.map(m => chip(m.s, 'weak')).join('') || '<span class="hint">无</span>'}</div>
    <details><summary class="hint">展开分层名单（还不稳 ${st.unsteady} · 较稳定 ${st.stable} · 证据不足 ${st.insufficient}）</summary><div class="rank-chips" style="grid-column:auto;margin-top:6px">${['unsteady', 'stable', 'insufficient'].flatMap(t => group(t).map(m => chip(m.s, t))).join('')}</div></details>
    <div class="actions"><button type="button" class="btn primary" ${weak.length ? '' : 'disabled'} data-act="ovTrain" data-arg="${type.key}">给这 ${weak.length} 人出训练卷</button><button type="button" class="btn" data-act="wrong" data-arg="${type.key}">看错题</button></div></section>
    ${type.typical ? `<section class="side-section"><h3>典型题</h3><span class="hint">本范围考过的这类题中得分率最低的一道 · ${type.typical.exam} 第 ${type.typical.no} 题 · 得分率 ${pct(type.typical.rate)}</span><div class="question">${esc(type.typical.text)}</div>
    <span class="hint">还考过：${type.others.map(o => `${o.exam} 第 ${o.no} 题（${pct(o.rate)}）`).join('；')}</span></section>` : ''}`
}

document.addEventListener('click', event => {
  const el = event.target.closest('[data-act]')
  if (!el || el.disabled) return
  actions[el.dataset.act]?.(el.dataset.arg ?? '', event, el)
})
