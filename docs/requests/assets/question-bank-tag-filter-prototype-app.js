// 原型交互：只在浏览器内存中筛选合成数据，演示分面筛选的组合规则与布局。
(() => {
  const D = window.QB_PROTO
  const GROUPS = [
    { key: 'sec', label: '章节', param: 'sec' }, { key: 'type', label: '题型', param: 'type', grouped: true },
    { key: 'kp', label: '知识点', param: 'kp', grouped: true }, { key: 'ability', label: '能力', param: 'ability' },
    { key: 'thought', label: '思想', param: 'thought' }, { key: 'method', label: '方法', param: 'method' },
    { key: 'model', label: '模型', param: 'model' }, { key: 'special', label: '特殊考法', param: 'special' },
    { key: 'error', label: '错因', param: 'error', hint: '按已确认典型错法的错误大类统计' },
  ]
  const sections = new Map(), chapterOf = new Map(), chapterIndex = new Map()
  D.chapters.forEach((chapter, index) => {
    chapterIndex.set(chapter.id, index)
    chapter.sections.forEach(section => { sections.set(section.id, section); chapterOf.set(section.id, chapter) })
  })
  const state = {
    sel: Object.fromEntries(GROUPS.map(group => [group.key, new Set()])), open: new Set(['sec', 'type']),
    expandedChapters: new Set(), search: {}, showAll: new Set(), legacy: null, notice: '',
    keyword: '', form: 'all', dmin: 1, dmax: 10, paper: '', progress: '', collapse: true,
    limit: 20, expanded: null, basket: new Set(),
  }
  const esc = value => String(value).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch])
  const sectionSet = () => {
    const result = new Set()
    for (const value of state.sel.sec) {
      const chapter = D.chapters.find(item => item.id === value)
      if (chapter) chapter.sections.forEach(section => result.add(section.id)); else result.add(value)
    }
    return result
  }
  const values = (q, key) => (key === 'sec' ? [q.section] : q[key])
  function baseMatch(q) {
    if (state.keyword && !q.stem.includes(state.keyword)) return false
    if (state.form !== 'all' && q.qtype !== state.form) return false
    if (q.difficulty < state.dmin || q.difficulty > state.dmax) return false
    if (state.paper && q.paper !== state.paper) return false
    if (state.progress && chapterIndex.get(q.chapter) > chapterIndex.get(state.progress)) return false
    return !state.legacy || q.section === state.legacy.section
  }
  function matches(q, exclude) {
    if (!baseMatch(q)) return false
    const secs = sectionSet()
    return GROUPS.every(({ key }) => {
      if (key === exclude || !state.sel[key].size) return true
      return key === 'sec' ? secs.has(q.section) : values(q, key).some(value => state.sel[key].has(value))
    })
  }
  function counts(key) {
    const map = new Map()
    for (const q of D.questions) if (matches(q, key)) for (const value of new Set(values(q, key))) map.set(value, (map.get(value) ?? 0) + 1)
    return map
  }
  const selectedCount = () => GROUPS.reduce((sum, { key }) => sum + state.sel[key].size, 0) + (state.legacy ? 1 : 0)
  const chapterSum = (chapter, map) => chapter.sections.reduce((sum, section) => sum + (map.get(section.id) ?? 0), 0)

  function row(key, value, label, count, extra = '') {
    const on = state.sel[key].has(value)
    if (!on && !count) return ''
    return `<label class="row${on ? ' on' : ''}${count ? '' : ' zero'}${extra}"><input type="checkbox" data-act="check" data-key="${key}" data-value="${esc(value)}"${on ? ' checked' : ''}><span class="label" title="${esc(label)}">${esc(label)}</span><span class="count">${count}</span></label>`
  }
  function chapterTree() {
    const map = counts('sec'), secs = sectionSet()
    return D.chapters.map(chapter => {
      const total = chapterSum(chapter, map)
      const picked = chapter.sections.filter(section => secs.has(section.id)).length
      const full = picked === chapter.sections.length, open = state.expandedChapters.has(chapter.id)
      const head = `<div class="row chapter${full ? ' on' : ''}${total ? '' : ' zero'}"><button type="button" class="twist" data-act="twist" data-ch="${chapter.id}" aria-expanded="${open}" aria-label="展开${esc(chapter.label)}">${open ? '▾' : '▸'}</button><input type="checkbox" data-act="chapter" data-ch="${chapter.id}"${full ? ' checked' : ''}${picked && !full ? ' data-mixed="1"' : ''} aria-label="${esc(chapter.label)}"><span class="label">${esc(chapter.label)}</span><span class="count">${total}</span></div>`
      const body = open ? chapter.sections.map(section => {
        const on = secs.has(section.id), count = map.get(section.id) ?? 0
        if (!on && !count) return ''
        return `<label class="row section${on ? ' on' : ''}"><input type="checkbox" data-act="section" data-sec="${section.id}"${on ? ' checked' : ''}><span class="label">${esc(section.label)}</span><span class="count">${count}</span></label>`
      }).join('') : ''
      return head + body
    }).join('')
  }
  function groupedList(key) {
    const map = counts(key), needle = (state.search[key] ?? '').trim(), secs = sectionSet()
    const blocks = []
    for (const chapter of D.chapters) for (const section of chapter.sections) {
      const items = (key === 'type' ? section.types : section.points).filter(value => !needle || value.includes(needle))
      const rows = items.map(value => row(key, value, value, map.get(value) ?? 0)).join('')
      if (rows) blocks.push({ inScope: !secs.size || secs.has(section.id), html: `<div class="subhead">${esc(chapter.label.split(' ')[0])} · ${esc(section.label)}</div>${rows}` })
    }
    if (key === 'kp') {
      const rows = D.earlierPoints.filter(value => !needle || value.includes(needle)).map(value => row(key, value, value, map.get(value) ?? 0)).join('')
      if (rows) blocks.push({ inScope: !secs.size, html: `<div class="subhead">前置知识（往届教材）</div>${rows}` })
    }
    const all = state.showAll.has(key) || needle
    const visible = all ? blocks : blocks.filter(block => block.inScope).slice(0, secs.size ? 99 : 4)
    const hidden = blocks.length - visible.length
    return `<input class="search" type="search" placeholder="搜${key === 'type' ? '题型' : '知识点'}" data-field="search:${key}" value="${esc(state.search[key] ?? '')}" aria-label="搜${key === 'type' ? '题型' : '知识点'}">`
      + (secs.size && !all ? '<div class="hint">只列所选章节内的项</div>' : '')
      + (visible.map(block => block.html).join('') || '<div class="hint">当前条件下没有可选项</div>')
      + (hidden > 0 ? `<button type="button" class="more-link" data-act="all" data-key="${key}">显示其余 ${hidden} 组</button>` : '')
  }
  function flatList(key) {
    const map = counts(key)
    const list = [...D.dims[key]].sort((a, b) => (map.get(b) ?? 0) - (map.get(a) ?? 0))
    return list.map(value => row(key, value, value, map.get(value) ?? 0)).join('') || '<div class="hint">当前条件下没有可选项</div>'
  }
  function renderFacets() {
    const n = selectedCount()
    let html = `<div class="facet-head"><strong>筛选</strong><small>${n ? `已选 ${n} 项` : '未选条件'}</small>${n ? '<button type="button" class="btn ghost" data-act="clear">清空</button>' : ''}</div>`
    for (const group of GROUPS) {
      const open = state.open.has(group.key), size = state.sel[group.key].size
      html += `<section class="group${open ? ' open' : ''}"><button type="button" class="group-toggle" data-act="group" data-key="${group.key}" aria-expanded="${open}"><span>${group.label}</span>${size ? `<em>已选 ${size}</em>` : ''}<i>▸</i></button>`
      if (open) html += `<div class="group-body">${group.hint ? `<div class="hint">${group.hint}</div>` : ''}${group.key === 'sec' ? chapterTree() : group.grouped ? groupedList(group.key) : flatList(group.key)}</div>`
      html += '</section>'
    }
    document.getElementById('facet-pane').innerHTML = html
    document.querySelectorAll('[data-mixed]').forEach(input => { input.indeterminate = true })
  }
  function chipLabel(key, value) {
    if (key !== 'sec') return value
    const chapter = D.chapters.find(item => item.id === value)
    return chapter ? chapter.label : `${chapterOf.get(value).label.split(' ')[0]} · ${sections.get(value).label}`
  }
  function card(q) {
    const open = state.expanded === q.id, inBasket = state.basket.has(q.id)
    const labels = [`<span class="cap type">题型 · ${esc(q.type[0])}</span>`, ...q.type.slice(1).map(value => `<span class="cap">次题型 · ${esc(value)}</span>`),
      ...q.kp.slice(0, 2).map(value => `<span class="cap">知识点 · ${esc(value)}</span>`), q.kp.length > 2 ? `<span>+${q.kp.length - 2}</span>` : ''].join('')
    const annot = open ? `<div class="annot"><div class="hint">展开后的标注区沿用现有布局；判定点旁只显示知识点与前置知识，不再显示直接技能。</div>${Array.from({ length: q.points }, (_, i) => `<div class="point"><b>判定点 ${i + 1}</b><span>（合成）写出关键步骤</span><span class="labels"><span class="cap">知识点 · ${esc(q.kp[i % q.kp.length])}</span></span></div>`).join('')}</div>` : ''
    return `<article class="card"><header><b>#${q.id}</b><span>${q.qtype} · 难度 ${q.difficulty} · ${esc(q.paper)}</span><label><input type="checkbox" data-act="basket" data-id="${q.id}"${inBasket ? ' checked' : ''}>选入</label></header>`
      + `<div class="stem">${esc(q.stem)}</div><div class="labels">${labels}</div>`
      + `<footer><span>判定点 ${q.points}</span>${q.duplicates && state.collapse ? `<span class="dup">· 另有 ${q.duplicates} 道相同题 ▸</span>` : ''}<span class="acts"><button type="button" class="btn ghost" data-act="expand" data-id="${q.id}">${open ? '收起标注' : '展开标注'}</button><button type="button" class="btn ghost">相似题</button><button type="button" class="btn" data-act="basket-btn" data-id="${q.id}">${inBasket ? '已在试卷篮' : '加入试卷篮'}</button></span></footer>${annot}</article>`
  }
  function urlPreview() {
    const params = [['tab', 'tag'], ...GROUPS.flatMap(group => [...state.sel[group.key]].map(value => [group.param, value]))]
    if (state.legacy) params.push(['skill', 'sk_…'])
    return '?' + params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  }
  function renderResults() {
    const result = D.questions.filter(q => matches(q, null))
    const dups = result.reduce((sum, q) => sum + q.duplicates, 0)
    const total = state.collapse ? result.length : result.length + dups
    const chips = GROUPS.flatMap(group => [...state.sel[group.key]].map(value => `<span class="chip">${group.label}：${esc(chipLabel(group.key, value))}<button type="button" data-act="remove" data-key="${group.key}" data-value="${esc(value)}" aria-label="移除${esc(chipLabel(group.key, value))}">×</button></span>`))
    if (state.legacy) chips.push(`<span class="chip legacy">技能：${esc(state.legacy.label)} <small>旧链接</small><button type="button" data-act="legacy-off" aria-label="移除旧链接技能条件">×</button></span>`)
    const option = (value, label, current) => `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(label)}</option>`
    const diff = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    document.getElementById('result-pane').innerHTML = `<div class="result-head">
      <div class="title-line"><h2>${selectedCount() ? '筛选结果' : '本学期全部题目'}</h2><span class="total">共 <b>${total}</b> 题${state.collapse && dups ? ` · 已折叠 ${dups} 道重复题` : ''}</span></div>
      ${state.notice ? `<div class="hint">${esc(state.notice)}</div>` : ''}
      <div class="chips">${chips.join('') || '<span class="hint">未选条件，显示本学期全部题目</span>'}${chips.length ? '<button type="button" class="btn ghost" data-act="clear">清空条件</button>' : ''}</div>
      <div class="filter-line"><input type="search" placeholder="搜题干" data-field="keyword" value="${esc(state.keyword)}" aria-label="搜题干">
        <span class="seg">${[['all', '全部'], ['选择题', '选择'], ['填空题', '填空'], ['解答题', '解答']].map(([v, l]) => `<button type="button" data-act="form" data-value="${v}" class="${state.form === v ? 'on' : ''}">${l}</button>`).join('')}</span>
        <span>难度 <select data-field="dmin" aria-label="最低难度">${diff.map(v => option(String(v), String(v), String(state.dmin))).join('')}</select>–<select data-field="dmax" aria-label="最高难度">${diff.map(v => option(String(v), String(v), String(state.dmax))).join('')}</select></span>
        <select data-field="paper" aria-label="来源">${option('', '来源：全部', state.paper)}${D.papers.map(p => option(p, p, state.paper)).join('')}</select>
        <select data-field="progress" aria-label="教学进度">${option('', '教学进度：不限', state.progress)}${D.chapters.map(c => option(c.id, `已学到${c.label}`, state.progress)).join('')}</select>
        <label class="toggle"><input type="checkbox" data-field="collapse"${state.collapse ? ' checked' : ''}>折叠重复题</label>
        <button type="button" class="btn" title="标签完整度、判定点待审核、多选题、排序、选择本页：与现有“更多”一致">更多 ▾</button></div>
    </div><div class="list">${result.length ? result.slice(0, state.limit).map(card).join('') + (result.length > state.limit ? `<button type="button" class="btn" data-act="page">再显示 20 题（共 ${result.length}）</button>` : '') : '<div class="empty"><p>没有同时满足全部条件的题</p><button type="button" class="btn" data-act="clear">清空条件</button></div>'}</div>`
    document.getElementById('url-preview').textContent = urlPreview()
    document.getElementById('volume-count').textContent = D.questions.length
    document.getElementById('basket-count').textContent = state.basket.size
  }
  function render() {
    const active = document.activeElement?.dataset?.field, caret = document.activeElement?.selectionStart
    renderFacets(); renderResults()
    if (active) { const el = document.querySelector(`[data-field="${active}"]`); if (el) { el.focus(); if (caret != null && el.setSelectionRange) el.setSelectionRange(caret, caret) } }
  }
  function toggleChapter(id) {
    const chapter = D.chapters.find(item => item.id === id), secs = sectionSet(), sel = state.sel.sec
    const full = chapter.sections.every(section => secs.has(section.id))
    sel.delete(id); chapter.sections.forEach(section => sel.delete(section.id))
    if (!full) sel.add(id)
  }
  function toggleSection(id) {
    const chapter = chapterOf.get(id), sel = state.sel.sec
    if (sel.has(chapter.id)) { sel.delete(chapter.id); chapter.sections.forEach(section => { if (section.id !== id) sel.add(section.id) }) }
    else if (sel.has(id)) sel.delete(id)
    else { sel.add(id); if (chapter.sections.every(section => sel.has(section.id))) { chapter.sections.forEach(section => sel.delete(section.id)); sel.add(chapter.id) } }
  }
  document.addEventListener('click', event => {
    const el = event.target.closest('[data-act]')
    if (!el) return
    const { act, key, value, id } = el.dataset
    state.notice = ''
    if (act === 'group') state.open.has(key) ? state.open.delete(key) : state.open.add(key)
    else if (act === 'twist') state.expandedChapters.has(el.dataset.ch) ? state.expandedChapters.delete(el.dataset.ch) : state.expandedChapters.add(el.dataset.ch)
    else if (act === 'chapter') toggleChapter(el.dataset.ch)
    else if (act === 'section') toggleSection(el.dataset.sec)
    else if (act === 'check' || act === 'remove') state.sel[key].has(value) ? state.sel[key].delete(value) : state.sel[key].add(value)
    else if (act === 'clear') { GROUPS.forEach(group => state.sel[group.key].clear()); state.legacy = null }
    else if (act === 'legacy-off') state.legacy = null
    else if (act === 'all') state.showAll.add(key)
    else if (act === 'form') state.form = value
    else if (act === 'page') state.limit += 20
    else if (act === 'expand') state.expanded = state.expanded === Number(id) ? null : Number(id)
    else if (act === 'basket' || act === 'basket-btn') state.basket.has(Number(id)) ? state.basket.delete(Number(id)) : state.basket.add(Number(id))
    else return
    if (['check', 'remove', 'clear', 'chapter', 'section', 'legacy-off', 'form'].includes(act)) state.limit = 20
    render()
  })
  document.addEventListener('input', event => {
    const field = event.target.dataset?.field
    if (!field) return
    if (field.startsWith('search:')) state.search[field.slice(7)] = event.target.value
    else if (field === 'collapse') state.collapse = event.target.checked
    else if (field === 'dmin' || field === 'dmax') state[field] = Number(event.target.value)
    else state[field] = event.target.value
    state.limit = 20
    render()
  })
  document.getElementById('demo-legacy-skill').addEventListener('click', () => { state.legacy = { label: '求算术平方根', section: 'c2s2' }; state.notice = ''; render() })
  document.getElementById('demo-legacy-topic').addEventListener('click', () => { state.sel.kp.add('无理数'); state.open.add('kp'); state.notice = '旧链接 ?topic= 已换算为知识点条件“无理数”。'; render() })
  render()
})()
