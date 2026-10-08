<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { QuestionBankFacets } from '../../api/question-bank'
import { selectedSections, type Chapters } from './question-tag-filters'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{ chapters: Chapters; counts: QuestionBankFacets['primary_sections']; selection: string[]; error: string }>()
const emit = defineEmits<{ exclusive: [id: string]; toggle: [id: string]; all: []; retry: [] }>()
const expanded = ref(new Set<string>())
const counts = computed(() => new Map(props.counts.map(item => [item.value, item.count])))
const sections = computed(() => selectedSections(props.chapters, props.selection))
const ownIds = computed(() => new Set(props.chapters.flatMap(chapter => chapter.sections.map(section => section.id))))
const chapterCount = (chapter: Chapters[number]) => chapter.sections.reduce((total, section) => total + (counts.value.get(section.id) ?? 0), 0)
const total = computed(() => props.chapters.reduce((total, chapter) => total + chapterCount(chapter), 0))
const other = computed(() => props.counts.filter(item => !ownIds.value.has(item.value)).reduce((total, item) => total + item.count, 0))
const checked = (chapter: Chapters[number]) => props.selection.includes(chapter.id) || (chapter.sections.length > 0 && chapter.sections.every(section => sections.value.has(section.id)))
const partial = (chapter: Chapters[number]) => !checked(chapter) && chapter.sections.some(section => sections.value.has(section.id))
function toggleOpen(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
watch(() => props.selection, () => {
  for (const chapter of props.chapters) if (props.selection.includes(chapter.id) || chapter.sections.some(section => props.selection.includes(section.id))) expanded.value.add(chapter.id)
}, { immediate: true })
</script>

<template>
  <header class="qb-chapter-heading"><strong>章节</strong><small>按主考小节统计</small></header>
  <div v-if="error" class="qb-count-error" role="status">题数暂时无法读取 <AppButton size="small" variant="ghost" @click="emit('retry')">重试</AppButton></div>
  <nav class="qb-chapter-options" aria-label="章节范围">
    <div class="qb-chapter-row is-root" :class="{ 'is-active': !selection.length }">
      <button type="button" class="qb-chapter-name" :aria-pressed="!selection.length" @click="emit('all')">本学期全部章节</button><small>{{ error ? '—' : total }}</small>
    </div>
    <div v-for="chapter in chapters" :key="chapter.id" class="qb-chapter-group">
      <div class="qb-chapter-row" :class="{ 'is-active': checked(chapter), 'is-zero': !error && !chapterCount(chapter) }">
        <button type="button" class="qb-chapter-toggle" :aria-label="`${expanded.has(chapter.id) ? '收起' : '展开'}${chapter.label}`" :aria-expanded="expanded.has(chapter.id)" @click="toggleOpen(chapter.id)">{{ expanded.has(chapter.id) ? '▾' : '▸' }}</button>
        <input type="checkbox" :aria-label="`同时看${chapter.label}`" :checked="checked(chapter)" :indeterminate="partial(chapter)" @change="emit('toggle', chapter.id)">
        <button type="button" class="qb-chapter-name" :aria-pressed="checked(chapter)" @click="emit('exclusive', chapter.id)">{{ chapter.label }}</button><small>{{ error ? '—' : chapterCount(chapter) }}</small>
      </div>
      <div v-if="expanded.has(chapter.id)" class="qb-chapter-sections">
        <div v-for="section in chapter.sections" :key="section.id" class="qb-chapter-row is-section" :class="{ 'is-active': sections.has(section.id), 'is-zero': !error && !(counts.get(section.id) ?? 0) }">
          <input type="checkbox" :aria-label="`同时看${chapter.label} · ${section.label}`" :checked="sections.has(section.id)" @change="emit('toggle', section.id)">
          <button type="button" class="qb-chapter-name" :aria-pressed="sections.has(section.id)" @click="emit('exclusive', section.id)">{{ section.label }}</button><small>{{ error ? '—' : counts.get(section.id) ?? 0 }}</small>
        </div>
      </div>
    </div>
  </nav>
  <p v-if="other && !error" class="qb-chapter-foot">另有 {{ other }} 题的主考小节不在本册</p>
</template>
