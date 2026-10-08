<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { knowledgeLeafLabel, type QuestionBankFacets, type QuestionSkillIndex } from '../../api/question-bank'
import { TAG_DIMENSIONS, selectedSections, type TagDimensionKey, type TagSelection } from './question-tag-filters'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{ index: QuestionSkillIndex; volumeLabel: string; facets: QuestionBankFacets | null; selection: TagSelection; error: string }>()
const emit = defineEmits<{ toggle: [dimension: TagDimensionKey, value: string]; clear: [dimension: TagDimensionKey]; retry: [] }>()
const active = ref<TagDimensionKey>('typeKeys')
const expanded = reactive<Record<TagDimensionKey, boolean>>({ typeKeys: false, knowledgePoints: false, abilities: false, thoughts: false, methods: false, models: false, specialTypes: false, errorPatternCategories: false })
const search = reactive({ typeKeys: '', knowledgePoints: '' })
const needle = computed(() => active.value === 'typeKeys' || active.value === 'knowledgePoints' ? search[active.value].trim() : '')
const current = computed(() => TAG_DIMENSIONS.find(dimension => dimension.key === active.value) ?? TAG_DIMENSIONS[0])
const typeIndex = computed(() => new Map(props.index.types?.map(type => [type.value, type]) ?? []))
const selected = computed(() => props.selection[active.value])
const pickedSections = computed(() => selectedSections(props.index.chapters, props.selection.sections))
const box = ref<HTMLElement | null>(null)
const canExpand = ref(false)
const hidden = ref(new Set<string>())
const open = computed(() => expanded[active.value] || Boolean(needle.value))

function label(value: string): string {
  if (active.value === 'typeKeys') return (typeIndex.value.get(value)?.label ?? value).replace(/^题型[·：:]\s*/, '')
  return active.value === 'knowledgePoints' ? knowledgeLeafLabel(value) : value
}
function sectionFor(value: string): string {
  if (active.value === 'typeKeys') return typeIndex.value.get(value)?.section_id ?? ''
  if (active.value !== 'knowledgePoints') return ''
  const path = value.split(/[|｜/]/).map(part => part.trim()).filter(Boolean)
  const normalize = (text: string) => text.replace(/^第[一二三四五六七八九十百\d]+章\s*/, '').replace(/^\d+[.、\s]*/, '').replace(/\s/g, '')
  if (props.volumeLabel && path[0] !== props.volumeLabel) return ''
  const sectionName = normalize(path[path.length - 2] ?? '')
  const chapterName = normalize(path[path.length - 3] ?? '')
  for (const chapter of props.index.chapters) {
    if (chapterName !== normalize(chapter.label)) continue
    const section = chapter.sections.find(section => sectionName === normalize(section.label))
    if (section) return section.id
  }
  return ''
}
const rows = computed(() => {
  const source = props.facets?.[current.value.facet] ?? []
  const values = new Map(source.filter(item => item.count > 0 || selected.value.includes(item.value)).map(item => [item.value, item.count]))
  for (const value of selected.value) if (!values.has(value)) values.set(value, 0)
  return [...values].map(([value, count]) => ({ value, count, label: label(value), section: sectionFor(value) }))
    .filter(item => !needle.value || item.label.includes(needle.value) || item.value.includes(needle.value))
    .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label, 'zh-CN'))
})
const groups = computed(() => {
  if (active.value !== 'typeKeys' && active.value !== 'knowledgePoints') return [{ id: '', label: '', rows: rows.value }]
  const result: { id: string; label: string; rows: typeof rows.value }[] = []
  const used = new Set<string>()
  for (const chapter of props.index.chapters) {
    for (const section of chapter.sections) {
      if (props.selection.sections.length && !needle.value && !pickedSections.value.has(section.id)) continue
      const items = rows.value.filter(item => item.section === section.id)
      if (items.length) {
        result.push({ id: section.id, label: `${chapter.label.match(/^第[^章]+章/)?.[0] ?? chapter.label} · ${section.label}`, rows: items })
        for (const item of items) used.add(item.value)
      }
    }
    if (!props.selection.sections.length || needle.value || props.selection.sections.includes(chapter.id)) {
      const items = rows.value.filter(item => item.section === chapter.id)
      if (items.length) {
        result.push({ id: chapter.id, label: chapter.label, rows: items })
        for (const item of items) used.add(item.value)
      }
    }
  }
  const others = rows.value.filter(item => item.section && !used.has(item.value))
  if (others.length) result.push({ id: 'other', label: '其他小节（本范围的题也涉及）', rows: others })
  const missing = rows.value.filter(item => !item.section)
  if (missing.length) result.push({ id: 'missing', label: active.value === 'knowledgePoints' ? '前置知识（往届教材）' : '未归小节', rows: missing })
  if (result.length === 1 && result[0]?.id !== 'missing' && result[0]?.id !== 'other') return result.map(group => ({ ...group, label: '' }))
  return result
})
function measure() {
  if (!box.value) return
  canExpand.value = box.value.scrollHeight > 58
  if (open.value) { hidden.value = new Set(); return }
  const top = box.value.getBoundingClientRect().top
  hidden.value = new Set([...box.value.querySelectorAll<HTMLButtonElement>('[data-tag-value]')].filter(button => button.getBoundingClientRect().bottom - top > 58).map(button => button.dataset.tagValue ?? ''))
}
let observer: ResizeObserver | undefined
watch(box, (element, previous) => {
  if (previous) observer?.unobserve(previous)
  if (element) observer?.observe(element)
  void nextTick(measure)
})
watch([groups, active, open], () => void nextTick(measure), { flush: 'post' })
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined' && box.value) { observer = new ResizeObserver(measure); observer.observe(box.value) }
  measure()
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div class="qb-tag-bar">
    <nav class="qb-dimension-tabs" aria-label="标签维度">
      <button v-for="dimension in TAG_DIMENSIONS" :key="dimension.key" type="button" :class="{ 'is-active': active === dimension.key }" :aria-pressed="active === dimension.key" @click="active = dimension.key">{{ dimension.label }}<sup v-if="selection[dimension.key].length">{{ selection[dimension.key].length }}</sup></button>
    </nav>
    <div class="qb-tag-bar-tools">
      <button type="button" class="qb-filter-chip" :class="{ 'is-active': !selected.length }" :aria-pressed="!selected.length" :aria-label="`不限${current.label}`" @click="emit('clear', active)">全部</button>
      <small>{{ selection.sections.length ? '只列所选章节涉及的项' : '未选章节：列出全部小节，可先在左侧选章节' }}</small>
      <input v-if="active === 'typeKeys' || active === 'knowledgePoints'" v-model="search[active]" class="app-input" type="search" :aria-label="`搜${current.label}`" :placeholder="`搜${current.label}`">
    </div>
    <small v-if="active === 'errorPatternCategories'" class="qb-tag-hint">按已确认典型错法的错误大类统计</small>
    <div v-if="error" class="qb-count-error" role="status">题数暂时无法读取 <AppButton variant="ghost" size="small" @click="emit('retry')">重试</AppButton></div>
    <div v-else ref="box" class="qb-tag-lines" :class="{ 'is-clamped': !open }">
      <div v-for="group in groups" :key="group.id" class="qb-tag-row">
        <small v-if="group.label" class="qb-tag-section-label" :title="group.id === 'missing' && active === 'knowledgePoints' ? '本册以外的知识点，包括往届教材和其他册的关联。' : group.label">{{ group.label }}</small>
        <button v-for="item in group.rows" :key="item.value" type="button" class="qb-filter-chip" :class="{ 'is-active': selected.includes(item.value) }" :title="item.value" :aria-pressed="selected.includes(item.value)" :data-tag-value="item.value" :tabindex="hidden.has(item.value) ? -1 : 0" :aria-hidden="hidden.has(item.value) ? true : undefined" @click="emit('toggle', active, item.value)">{{ item.label }} <small>{{ item.count }}</small></button>
      </div>
      <p v-if="!rows.length" class="qb-help">{{ facets ? '这个范围暂无可用标签。' : '正在读取题数…' }}</p>
    </div>
    <AppButton v-if="!error && !needle && (canExpand || open)" variant="ghost" size="small" class="qb-tag-expand" :aria-expanded="open" @click="expanded[active] = !expanded[active]">{{ open ? '收起' : `展开全部 ${rows.length} 项` }}</AppButton>
  </div>
</template>
