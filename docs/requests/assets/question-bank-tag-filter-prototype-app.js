// 原型交互：只在浏览器内存中筛选合成数据，演示“左侧章节树 + 右侧按维度切换标签”的组合规则与布局。
(() => {
  const D = window.QB_PROTO
  const DIMS = [
    { key: 'type', label: '题型', grouped: true }, { key: 'kp', label: '知识点', grouped: true },
    { key: 'ability', label: '能力' }, { key: 'thought', label: '思想' }, { key: 'method', label: '方法' },
    { key: 'model', label: '模型' }, { key: 'special', label: '特殊考法' },
    { key: 'error', label: '错因', hint: '按已确认典型错法的错误大类统计' },
  ]
  const KEYS = ['sec', ...DIMS.map(dim => dim.key)]
  const LABEL = { sec: '章节', ...Object.fromEntries(DIMS.map(dim => [dim.key, dim.label])) }
  const FORMS = [['选择题', '选择'], ['填空题', '填空'], ['解答题', '解答']]
  const sections = new Map(), chapterOf = new Map(), chapterIndex = new Map()
  D.chapters.forEach((chapter, index) => {
    chapterIndex.set(chapter.id, index)
    chapter.sections.forEach(section => { sections.set(section.id, section); chapterOf.set(section.id, chapter) })
  })
  const state = {
    sel: Object.fromEntries(KEYS.map(key => [key, new Set()])), dim: 'type', unclamped: new Set(), expandedChapters: new Set(),
    search: '', legacy: null, notice: '', keyword: '', forms: new Set(), dmin: 1, dmax: 10, paper: '', progress: '',
    collapse: true, limit: 20, expanded: null, basket: new Set(),
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
    if (state.forms.size && !state.forms.has(q.qtype)) return false
    if (q.difficulty < state.dmin || q.difficulty > state.dmax) return false
    if (state.paper && q.paper !== state.paper) return false
    if (state.progress && chapterIndex.get(q.chapter) > chapterIndex.get(state.progress)) return false
    return !state.legacy || q.section === state.legacy.section
  }
  function matches(q, exclude) {
    if (!baseMatch(q)) return false
    const secs = sectionSet()
    return KEYS.every(key => {
      if (key === exclude || !state.sel[key].size) return true
      return key === 'sec' ? secs.has(q.section) : values(q, key).some(value => state.sel[key].has(value))
    })
  }
  function counts(key) {
    const map = new Map()
    for (const q of D.questions) if (matches(q, key)) for (const value of new Set(values(q, key))) map.set(value, (map.get(value) ?? 0) + 1)
    return map
  }
  const tagCount = () => DIMS.reduce((sum, { key }) => sum + state.sel[key].size, 0) + (state.legacy ? 1 : 0)
  const chapterSum = (chapter, map) => chapter.sections.reduce((sum, section) => sum + (map.get(section.id) ?? 0), 0)
  const shortChapter = chapter => chapter.label.split(' ')[0]

  function renderTree() {
    const map = counts('sec'), secs = sectionSet()
    const total = [...map.values()].reduce((sum, value) => sum + value, 0)
    let html = '<div class="tree-head"><strong>章节</strong><small>按主考小节统计</small></div><div class="tree">'
    html += `<div class="row root${secs.size ? '' : ' on'}"><button type="button" class="name" data-act="only" data-target="">本学期全部章节</button><span class="count">${total}</span></div>`
    for (const chapter of D.chapters) {
      const sum = chapterSum(chapter, map), picked = chapter.sections.filter(section => secs.has(section.id)).length
      const full = picked === chapter.sections.length, open = state.expandedChapters.has(chapter.id)
      html += `<div class="row chapter${full ? ' on' : ''}${sum ? '' : ' zero'}"><button type="button" class="twist" data-act="twist" data-ch="${chapter.id}" aria-expanded="${open}" aria-label="展开${esc(chapter.label)}">${open ? '▾' : '▸'}</button>`
        + `<input type="checkbox" data-act="chapter" data-ch="${chapter.id}"${full ? ' checked' : ''}${picked && !full ? ' data-mixed="1"' : ''} aria-label="同时看${esc(chapter.label)}" title="同时看多个章节">`
        + `<button type="button" class="name" data-act="only" data-target="${chapter.id}" title="只看${esc(chapter.label)}">${esc(chapter.label)}</button><span class="count">${sum}</span></div>`
      if (open) for (const section of chapter.sections) {
        const on = secs.has(section.id), count = map.get(section.id) ?? 0
        html += `<div class="row section${on && !full ? ' on' : ''}${count ? '' : ' zero'}"><input type="checkbox" data-act="section" data-sec="${section.id}"${on ? ' checked' : ''} aria-label="同时看${esc(section.label)}" title="同时看多个小节">`
          + `<button type="button" class="name" data-act="only" data-target="${section.id}" title="只看${esc(section.label)}">${esc(section.label)}</button><span class="count">${count}</span></div>`
      }
    }
    document.getElementById('tree-pane').innerHTML = html + '</div>'
    document.querySelectorAll('[data-mixed]').forEach(input => { input.indeterminate = true })
  }

  function tagButton(key, value, count) {
    const on = state.sel[key].has(value)
    return `<button type="button" class="tag${on ? ' on' : ''}" data-act="tag" data-key="${key}" data-value="${esc(value)}" aria-pressed="${on}">${esc(value)}<small>${count}</small></button>`
  }
  function groups(key, map) {
    const needle = state.search.trim(), secs = sectionSet()
    const keep = value => (map.get(value) || state.sel[key].has(value)) && (!needle || value.includes(needle))
    const result = [], other = []
    for (const chapter of D.chapters) for (const section of chapter.sections) {
      const items = (key === 'type' ? section.types : section.points).filter(keep)
      if (!items.length) continue
      if (needle || !secs.size || secs.has(section.id)) result.push({ name: `${shortChapter(chapter)} · ${section.label}`, items })
      else other.push(...items)
    }
    if (key === 'kp') { const items = D.earlierPoints.filter(keep); if (items.length) result.push({ name: '前置知识（往届教材）', items }) }
    if (other.length) result.push({ name: '其他小节（本范围的题也涉及）', items: other })
    return result
  }
  function renderPanelTop() {
    const dim = DIMS.find(item => item.key === state.dim), key = dim.key, map = counts(key), secs = sectionSet()
    const tabs = DIMS.map(item => {
      const size = state.sel[item.key].size, on = item.key === key
      return `<button type="button" class="${on ? 'on' : ''}" data-act="dim" data-key="${item.key}" aria-pressed="${on}">${item.label}${size ? `<sup aria-label="已选 ${size} 项">${size}</sup>` : ''}</button>`
    }).join('')
    let lines, itemTotal
    if (dim.grouped) {
      const list = groups(key, map)
      itemTotal = list.reduce((sum, group) => sum + group.items.length, 0)
      lines = list.map(group => `<div class="tag-line">${list.length > 1 ? `<span class="tag-group">${esc(group.name)}</span>` : ''}${group.items.map(value => tagButton(key, value, map.get(value) ?? 0)).join('')}</div>`).join('')
    } else {
      const items = D.dims[key].filter(value => map.get(value) || state.sel[key].has(value)).sort((a, b) => (map.get(b) ?? 0) - (map.get(a) ?? 0))
      itemTotal = items.length
      lines = items.length ? `<div class="tag-line">${items.map(value => tagButton(key, value, map.get(value) ?? 0)).join('')}</div>` : ''
    }
    const hint = dim.grouped ? (state.search.trim() ? '搜索时列出全部章节的项' : secs.size ? '只列所选章节涉及的项' : '未选章节：列出全部小节，可先在左侧选章节') : (dim.hint ?? '')
    const clamp = !state.unclamped.has(key) && !state.search.trim()
    return `<div class="dim-tabs" role="group" aria-label="标签维度">${tabs}</div>`
      + `<div class="tag-tools"><button type="button" class="tag${state.sel[key].size ? '' : ' on'}" data-act="dim-all" data-key="${key}" aria-pressed="${!state.sel[key].size}">全部</button><span>${esc(hint)}</span>`
      + (dim.grouped ? `<input type="search" placeholder="搜${dim.label}" data-field="search" value="${esc(state.search)}" aria-label="搜${dim.label}">` : '') + '</div>'
      + `<div class="tag-box${clamp ? ' clamp' : ''}" id="tag-box">${lines || '<span class="hint">当前条件下没有可选项</span>'}</div>`
      + `<button type="button" class="more-link" id="tag-more" data-act="unclamp" data-key="${key}" data-total="${itemTotal}" hidden></button>`
  }
  function chipLabel(key, value) {
    if (key !== 'sec') return value
    const chapter = D.chapters.find(item => item.id === value)
    return chapter ? chapter.label : `${shortChapter(chapterOf.get(value))} · ${sections.get(value).label}`
  }
  function renderPanelRest(result, total, dups) {
    const chips = KEYS.flatMap(key => [...state.sel[key]].map(value => `<span class="chip">${LABEL[key]}：${esc(chipLabel(key, value))}<button type="button" data-act="remove" data-key="${key}" data-value="${esc(value)}" aria-label="移除${esc(chipLabel(key, value))}">×</button></span>`))
    if (state.legacy) chips.push(`<span class="chip legacy">技能：${esc(state.legacy.label)} <small>旧链接</small><button type="button" data-act="legacy-off" aria-label="移除旧链接技能条件">×</button></span>`)
    const option = (value, label, current) => `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(label)}</option>`
    const diff = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(String)
    const plain = (act, label, on, value = '') => `<button type="button" class="tag${on ? ' on' : ''}" data-act="${act}" data-value="${esc(value)}" aria-pressed="${on}">${label}</button>`
    return (state.notice ? `<div class="notice" role="status">${esc(state.notice)}</div>` : '')
      + `<div class="chips"><b>已选</b>${chips.join('') || '<span class="hint">未选条件，显示本学期全部题目</span>'}${chips.length ? '<button type="button" class="btn ghost" data-act="clear">清空条件</button>' : ''}</div>`
      + '<div class="frows">'
      + `<div class="frow"><span>作答形式</span>${plain('form-all', '全部', !state.forms.size)}${FORMS.map(([value, label]) => plain('form', label, state.forms.has(value), value)).join('')}</div>`
      + `<div class="frow"><span>难度</span><select data-field="dmin" aria-label="最低难度">${diff.map(v => option(v, v, String(state.dmin))).join('')}</select>–<select data-field="dmax" aria-label="最高难度">${diff.map(v => option(v, v, String(state.dmax))).join('')}</select><span class="hint">沿用现有难度区间（1–10）</span></div>`
      + `<div class="frow"><span>来源</span>${plain('paper-all', '全部', !state.paper)}<select data-field="paper" aria-label="指定试卷">${option('', '指定试卷…', state.paper)}${D.papers.map(p => option(p, p, state.paper)).join('')}</select></div>`
      + '</div>'
      + `<div class="bottom-line"><input type="search" placeholder="搜题干" data-field="keyword" value="${esc(state.keyword)}" aria-label="搜题干">`
      + `<select data-field="progress" aria-label="教学进度">${option('', '教学进度：不限', state.progress)}${D.chapters.map(c => option(c.id, `已学到${c.label}`, state.progress)).join('')}</select>`
      + `<label class="toggle"><input type="checkbox" data-field="collapse"${state.collapse ? ' checked' : ''}>折叠重复题</label>`
      + '<button type="button" class="btn" title="标签完整度、判定点待审核、多选题、排序、选择本页：与现有“更多”一致">更多 ▾</button>'
      + `<span class="total">${state.sel.sec.size || tagCount() ? '筛选结果' : '本学期全部题目'} · 共 <b>${total}</b> 题${state.collapse && dups ? ` · 已折叠 ${dups} 道重复题` : ''}</span></div>`
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
    const params = [['tab', 'tag'], ...KEYS.flatMap(key => [...state.sel[key]].map(value => [key, value]))]
    if (state.legacy) params.push(['skill', 'sk_…'])
    return '?' + params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  }
  const signature = el => {
    if (!el?.dataset) return null
    if (el.dataset.field) return `[data-field="${el.dataset.field}"]`
    if (!el.dataset.act) return null
    return ['act', 'key', 'value', 'target', 'ch', 'sec', 'id'].filter(k => el.dataset[k] != null).map(k => `[data-${k}="${CSS.escape(el.dataset[k])}"]`).join('')
  }
  function render() {
    const active = document.activeElement, sig = signature(active), caret = active?.selectionStart
    const result = D.questions.filter(q => matches(q, null))
    const dups = result.reduce((sum, q) => sum + q.duplicates, 0)
    const total = state.collapse ? result.length : result.length + dups
    renderTree()
    document.getElementById('filter-panel').innerHTML = renderPanelTop() + renderPanelRest(result, total, dups)
    document.getElementById('question-list').innerHTML = result.length
      ? result.slice(0, state.limit).map(card).join('') + (result.length > state.limit ? `<button type="button" class="btn" data-act="page">再显示 20 题（共 ${result.length}）</button>` : '')
      : '<div class="empty"><p>没有同时满足全部条件的题</p><button type="button" class="btn" data-act="clear">清空条件</button></div>'
    const box = document.getElementById('tag-box'), more = document.getElementById('tag-more')
    const tall = box.scrollHeight > box.clientHeight + 2, unclamped = state.unclamped.has(state.dim)
    if (tall || (unclamped && box.scrollHeight > 70)) { more.hidden = false; more.textContent = unclamped ? '收起' : `展开全部 ${more.dataset.total} 项` }
    document.getElementById('url-preview').textContent = urlPreview()
    document.getElementById('volume-count').textContent = D.questions.length
    document.getElementById('basket-count').textContent = state.basket.size
    if (sig) { const el = document.querySelector(sig); if (el) { el.focus(); if (caret != null && el.setSelectionRange) el.setSelectionRange(caret, caret) } }
  }
  function prune() {
    if (!state.sel.sec.size) return
    const removed = []
    for (const key of ['type', 'kp']) {
      const map = counts(key)
      for (const value of [...state.sel[key]]) if (!map.get(value)) { state.sel[key].delete(value); removed.push(value) }
    }
    if (removed.length) state.notice = `已去掉在所选章节里没有题的条件：${removed.join('、')}`
  }
  function onlyHere(target) {
    state.sel.sec = new Set(target ? [target] : [])
    if (D.chapters.some(chapter => chapter.id === target)) state.expandedChapters.add(target)
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
  const toggle = (set, value) => (set.has(value) ? set.delete(value) : set.add(value))
  document.addEventListener('click', event => {
    const el = event.target.closest('[data-act]')
    if (!el) return
    const { act, key, value, id } = el.dataset
    state.notice = ''
    if (act === 'only') onlyHere(el.dataset.target)
    else if (act === 'twist') toggle(state.expandedChapters, el.dataset.ch)
    else if (act === 'chapter') toggleChapter(el.dataset.ch)
    else if (act === 'section') toggleSection(el.dataset.sec)
    else if (act === 'dim') { state.dim = key; state.search = '' }
    else if (act === 'dim-all') state.sel[key].clear()
    else if (act === 'unclamp') toggle(state.unclamped, key)
    else if (act === 'tag' || act === 'remove') toggle(state.sel[key], value)
    else if (act === 'clear') { KEYS.forEach(k => state.sel[k].clear()); state.legacy = null }
    else if (act === 'legacy-off') state.legacy = null
    else if (act === 'form') toggle(state.forms, value)
    else if (act === 'form-all') state.forms.clear()
    else if (act === 'paper-all') state.paper = ''
    else if (act === 'page') state.limit += 20
    else if (act === 'expand') state.expanded = state.expanded === Number(id) ? null : Number(id)
    else if (act === 'basket' || act === 'basket-btn') toggle(state.basket, Number(id))
    else return
    if (['only', 'chapter', 'section'].includes(act) || (act === 'remove' && key === 'sec')) prune()
    if (!['twist', 'dim', 'unclamp', 'page', 'expand', 'basket', 'basket-btn'].includes(act)) state.limit = 20
    render()
  })
  document.addEventListener('input', event => {
    const field = event.target.dataset?.field
    if (!field) return
    if (field === 'collapse') state.collapse = event.target.checked
    else if (field === 'dmin' || field === 'dmax') state[field] = Number(event.target.value)
    else state[field] = event.target.value
    if (field !== 'search') state.limit = 20
    render()
  })
  document.getElementById('demo-legacy-skill').addEventListener('click', () => { state.legacy = { label: '求算术平方根', section: 'c2s2' }; state.notice = ''; render() })
  document.getElementById('demo-legacy-topic').addEventListener('click', () => { state.sel.kp.add('无理数'); state.dim = 'kp'; state.notice = '旧链接 ?topic= 已换算为知识点条件“无理数”。'; render() })
  render()
})()
