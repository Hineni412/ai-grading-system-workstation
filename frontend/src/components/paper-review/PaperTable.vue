<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { PaperReview, PaperItem, PaperGroup } from '../../api/paper-review'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import { displayRate, dots, pct, quadrantNames } from './display'
const props = defineProps<{ review: PaperReview; cls: string }>()
type Filter = 'all' | 'suspect' | 'harder' | 'typical'
type Row = { kind: 'group'; value: PaperGroup } | { kind: 'item'; value: PaperItem }
const filter = ref<Filter>('all'), expanded = ref<string | null>(null), openGroups = ref(new Set<string>())
const groups = computed(() => new Map(props.review.groups.map(g => [g.id, g])))
const tabs: {id: Filter; label: string}[] = [{id:'all',label:'全部'}, {id:'suspect',label:'可疑'}, {id:'harder',label:'比预估难'}, {id:'typical',label:'暴露典型错误'}]
const count = computed(() => new Set(props.review.items.map(i => i.parent_id)).size)
function matches(item: PaperItem, key: Filter) {
  return key === 'all' || key === 'suspect' && item.quadrant === 'suspect' || key === 'harder' && item.pred_flag === 'harder'
    || key === 'typical' && item.typical_error != null
}
function tabCount(key: Filter) { return key === 'all' ? count.value : props.review.items.filter(i => matches(i,key)).length }
const rows = computed<Row[]>(() => {
  if (filter.value !== 'all') return props.review.items.filter(i => matches(i,filter.value)).map(value => ({kind:'item',value}))
  const result: Row[] = [], seen = new Set<string>()
  for (const item of props.review.items) {
    const group = groups.value.get(item.parent_id)
    if (!group) { result.push({kind:'item',value:item}); continue }
    if (seen.has(group.id)) continue
    seen.add(group.id)
    result.push({kind:'group',value:group})
    if (openGroups.value.has(group.id)) for (const part of props.review.items.filter(i => group.parts.includes(i.id))) result.push({kind:'item',value:part})
  }
  return result
})
function toggle(row: Row) {
  if (row.kind === 'item') { expanded.value = expanded.value === row.value.id ? null : row.value.id; return }
  const next = new Set(openGroups.value)
  if (next.has(row.value.id)) next.delete(row.value.id); else next.add(row.value.id)
  openGroups.value = next
}
function errorFor(row: Row) { return row.kind === 'group' ? row.value.top_error : row.value.typical_error }
function partLabel(parent: string, part: string) { return part.startsWith(parent) ? part.slice(parent.length) : part }
function errorText(row: Row) {
  const e = errorFor(row)
  if (!e) return ''
  return row.kind === 'group' && 'part_id' in e ? `${partLabel(row.value.id, e.part_id)} ${e.reason}` : e.reason
}
function errorGroups(errors: PaperItem['errors']) {
  const byCategory = new Map<string, {name: string; total: number; errors: PaperItem['errors']}>()
  for (const error of errors) {
    const name = error.category || '未分类'
    const group = byCategory.get(name) ?? {name, total: 0, errors: []}
    group.total += error.count
    group.errors.push(error)
    byCategory.set(name, group)
  }
  return [...byCategory.values()].sort((a, b) => b.total - a.total)
}
async function go(id: string) {
  filter.value = 'all'
  const item = props.review.items.find(i => i.id === id)
  const parent = item?.parent_id ?? id
  if (groups.value.has(parent)) openGroups.value = new Set([...openGroups.value,parent])
  expanded.value = item?.id ?? null
  await nextTick()
  const element = document.getElementById(`pr-row-${id}`)
  element?.scrollIntoView({behavior:'smooth',block:'center'})
  element?.focus({preventScroll:true})
}
defineExpose({go})
</script>
<template>
  <section class="pr-card pr-table-card">
    <header><h2>逐题评价</h2><span class="pr-hint">全部 {{ count }} 题（{{ review.items.length }} 个小问）</span></header>
    <nav class="pr-filters" aria-label="逐题筛选"><button v-for="tab in tabs" :key="tab.id" :class="{on:filter===tab.id}" :aria-pressed="filter===tab.id" @click="filter=tab.id;expanded=null">{{ tab.label }} <small>{{ tabCount(tab.id) }}</small></button></nav>
    <div class="pr-table-scroll"><table><thead><tr><th>题号</th><th>得分率 <small>附各班刻度</small></th><th>区分度</th><th>类别</th><th>预估</th><th>错法</th></tr></thead>
      <tbody><template v-for="row in rows" :key="row.value.id">
        <tr :id="`pr-row-${row.value.id}`" :class="{'pr-group-row':row.kind==='group','pr-part-row':row.kind==='item' && row.value.id!==row.value.parent_id}" tabindex="0" :aria-expanded="row.kind==='group' ? openGroups.has(row.value.id) : expanded===row.value.id" @click="toggle(row)" @keydown.enter="toggle(row)">
          <td><span v-if="row.kind==='group'">{{ openGroups.has(row.value.id) ? '▾' : '▸' }} <b>{{ row.value.id }}</b> · {{ row.value.parts.length }}小问 <i v-if="row.value.flagged" class="pr-red-dot" title="有小问需要注意"></i></span><span v-else>{{ row.value.id }}</span></td>
          <td><div class="pr-rate"><span class="pr-bar"><i :style="{width:`${(displayRate(row.value,cls)??0)*100}%`}"></i><b v-for="(rate,name) in row.value.class_rates" v-show="rate!=null" :key="name" :style="{left:`${(rate??0)*100}%`}" :title="`${name}：${pct(rate)}`"></b></span><span>{{ pct(displayRate(row.value,cls)) }}</span></div></td>
          <td><span class="pr-dots" :title="row.value.r_rest==null ? '数据不足' : String(row.value.r_rest)"><i v-for="n in 4" :key="n" :class="{filled:n<=dots(row.value.r_rest)}"></i></span><small>{{ row.value.r_rest?.toFixed(2) ?? '数据不足' }}</small></td>
          <td><span class="pr-quad" :class="row.value.quadrant">{{ quadrantNames[row.value.quadrant] }}</span></td>
          <td :title="row.kind==='item' ? row.value.predicted_source : '整题难度'"><span v-if="row.value.pred_flag==='harder'" class="red">↑ 比预估难</span><span v-else-if="row.value.pred_flag==='easier'" class="amber">↓ 比预估容易</span><small v-else-if="row.value.pred_flag==='fit'">{{ row.value.predicted_difficulty?.toFixed(1) }}</small></td>
          <td class="pr-err-cell"><span v-if="errorFor(row)" class="red pr-err-text" :title="`⚠ ${errorFor(row)?.count}人 ${errorText(row)}`">⚠ {{ errorFor(row)?.count }}人 {{ errorText(row) }}</span><span v-else class="pr-hint">—</span></td>
        </tr>
        <tr v-if="row.kind==='item' && expanded===row.value.id" class="pr-detail-row"><td colspan="6"><div class="pr-item-detail"><div><h3>{{ row.value.id }} · 题干</h3><QuestionContentRenderer :fallback="row.value.stem" typeset-text /><p v-if="row.value.answer">参考答案：{{ row.value.answer }}</p><p class="pr-hint">预估难度 {{ row.value.predicted_difficulty?.toFixed(1) ?? '暂无' }} · {{ row.value.predicted_source }}</p></div><div class="pr-detail-errors"><h3>已保存错法（前 5 条）</h3><template v-if="row.value.errors.length"><section class="pr-err-group" v-for="group in errorGroups(row.value.errors)" :key="group.name"><h4><span class="pr-chip pr-cat">{{ group.name }}</span><span class="pr-err-total">共 {{ group.total }} 人次</span></h4><ol><li v-for="(error,i) in group.errors" :key="i"><b>{{ error.count }}人</b> {{ error.reason }}</li></ol></section></template><p v-else class="pr-hint">本项没有已保存的错法。</p><template v-if="row.value.distractors"><h3>选项分布</h3><p v-for="(n,key) in row.value.distractors.counts" :key="key">{{ key }}：{{ n }}人</p><p>{{ row.value.distractors.note }}</p></template></div></div></td></tr>
      </template><tr v-if="!rows.length"><td colspan="6" class="pr-empty">没有符合条件的评分项。</td></tr></tbody>
    </table></div>
    <p class="pr-table-note">大题得分率按小问分值加权，区分度用整题得分对其余题总分；逐小问区分度也剔除同一大题的全部小问。大题预估用整题难度。</p>
  </section>
</template>
