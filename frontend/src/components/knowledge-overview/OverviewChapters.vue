<script setup lang="ts">
import { computed, ref } from 'vue'

import type { TrainingOverviewNode } from '../../api/training'
import { formatPercent, shortNodeName, tierOf } from './model'
import OverviewTierBar from './OverviewTierBar.vue'

export type ChapterFilter =
  | { type: 'all' }
  | { type: 'chapter'; key: string }
  | { type: 'section'; key: string }

const props = defineProps<{
  nodes: TrainingOverviewNode[]
  filter: ChapterFilter
}>()

const emit = defineEmits<{
  select: [filter: ChapterFilter]
}>()

const chapters = computed(() => props.nodes.filter(node => node.kind === 'chapter'))
const sectionsByChapter = computed(() => {
  const map = new Map<string, TrainingOverviewNode[]>()
  for (const node of props.nodes) {
    if (node.kind !== 'section') continue
    const list = map.get(node.chapter_key) ?? []
    list.push(node)
    map.set(node.chapter_key, list)
  }
  return map
})

const expanded = ref<Set<string>>(new Set())

function selectChapter(node: TrainingOverviewNode): void {
  const next = new Set(expanded.value)
  if (next.has(node.knowledge_key)) {
    next.delete(node.knowledge_key)
  } else {
    next.add(node.knowledge_key)
  }
  expanded.value = next
  emit('select', { type: 'chapter', key: node.knowledge_key })
}

function selectSection(node: TrainingOverviewNode): void {
  emit('select', { type: 'section', key: node.knowledge_key })
}

function clearFilter(): void {
  emit('select', { type: 'all' })
}

function masteryClass(node: TrainingOverviewNode): string {
  const tier = tierOf(node.tier)
  return tier ? `is-${tier}` : 'is-insufficient'
}
</script>

<template>
  <section class="overview-card" aria-labelledby="overview-chapters-title">
    <header class="overview-card__header">
      <h2 id="overview-chapters-title">章节概览</h2>
      <button
        v-if="filter.type !== 'all'"
        type="button"
        class="overview-link-button"
        @click="clearFilter"
      >
        全部章节
      </button>
    </header>
    <p class="overview-legend">
      较稳定 · 还不稳 · 明显薄弱 · 证据不足（含估计不确定，不按 0 计）
    </p>
    <ul class="overview-chapter-list">
      <li v-for="chapter in chapters" :key="chapter.knowledge_key">
        <button
          type="button"
          class="overview-node-row"
          :class="{ 'is-selected': filter.type === 'chapter' && filter.key === chapter.knowledge_key }"
          :aria-expanded="expanded.has(chapter.knowledge_key)"
          @click="selectChapter(chapter)"
        >
          <span class="overview-node-row__name">{{ shortNodeName(chapter) }}</span>
          <span class="overview-node-row__mastery" :class="masteryClass(chapter)">
            {{ formatPercent(chapter.group_mastery) }}
          </span>
          <OverviewTierBar :distribution="chapter.distribution" />
        </button>
        <ul v-if="expanded.has(chapter.knowledge_key)" class="overview-section-list">
          <li v-for="section in sectionsByChapter.get(chapter.knowledge_key) ?? []" :key="section.knowledge_key">
            <button
              type="button"
              class="overview-node-row overview-node-row--section"
              :class="{ 'is-selected': filter.type === 'section' && filter.key === section.knowledge_key }"
              @click="selectSection(section)"
            >
              <span class="overview-node-row__name">{{ shortNodeName(section) }}</span>
              <span class="overview-node-row__mastery" :class="masteryClass(section)">
                {{ formatPercent(section.group_mastery) }}
              </span>
              <OverviewTierBar :distribution="section.distribution" />
            </button>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>
