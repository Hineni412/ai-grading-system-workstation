<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { GraphEdge, GraphNode } from '../../api/graph'

const props = defineProps<{
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedKey: string
}>()
const emit = defineEmits<{
  select: [key: string]
}>()

const CHAPTER_LEVEL_SECTION = '章级知识点'

interface NodePath {
  volume: string
  chapter: string
  section: string
  label: string
}

interface EnrichedNode {
  node: GraphNode
  path: NodePath
}

interface SectionGroup {
  name: string
  items: EnrichedNode[]
  mastery: number | null
  evidenceCount: number
}

interface ChapterGroup {
  key: string
  volume: string
  name: string
  items: EnrichedNode[]
  sections: SectionGroup[]
  mastery: number | null
}

const activeChapterKey = ref('')
const activeSectionName = ref('')
const expandedChapterKeys = ref<string[]>([])

function splitPath(value: string): string[] {
  return value.split(/[｜|/]/).map((part) => part.trim()).filter(Boolean)
}

function pathOf(node: GraphNode): NodePath {
  const displayPath = splitPath(node.display_name)
  if (displayPath.length >= 2) {
    return {
      volume: displayPath[0] ?? '',
      chapter: displayPath[1] ?? '未分章',
      section: displayPath[2] ?? '',
      label: displayPath[displayPath.length - 1] ?? node.display_name,
    }
  }
  const tagPath = splitPath(node.evidence.tag_context['教材章节']?.[0] ?? '')
  if (tagPath.length >= 2) {
    return {
      volume: tagPath[0] ?? '',
      chapter: tagPath[1] ?? '未分章',
      section: tagPath[2] ?? '',
      label: node.display_name,
    }
  }
  return {
    volume: tagPath[0] ?? '',
    chapter: node.curriculum_anchors.find((item) => item.includes('章')) ?? '未分章',
    section: node.evidence.tag_context['教材小节']?.[0] ?? '',
    label: node.display_name,
  }
}

function weightedMastery(nodes: GraphNode[]): number | null {
  const available = nodes.filter((node) => node.mastery.status === 'available' && node.mastery.value !== null)
  if (!available.length) return null
  const weight = available.reduce((total, node) => total + Math.max(node.mastery.evidence_count, 1), 0)
  return available.reduce((total, node) => (
    total + (node.mastery.value ?? 0) * Math.max(node.mastery.evidence_count, 1)
  ), 0) / weight
}

const chapters = computed<ChapterGroup[]>(() => {
  const chapterMap = new Map<string, { volume: string; name: string; items: EnrichedNode[] }>()
  for (const node of props.nodes) {
    const item: EnrichedNode = { node, path: pathOf(node) }
    const key = `${item.path.volume}｜${item.path.chapter}`
    const chapter = chapterMap.get(key) ?? { volume: item.path.volume, name: item.path.chapter, items: [] }
    chapter.items.push(item)
    chapterMap.set(key, chapter)
  }
  return [...chapterMap].map(([key, chapter]) => {
    const sectionMap = new Map<string, EnrichedNode[]>()
    for (const item of chapter.items) {
      const name = item.path.section || CHAPTER_LEVEL_SECTION
      sectionMap.set(name, [...(sectionMap.get(name) ?? []), item])
    }
    const sections = [...sectionMap].map(([name, items]) => ({
      name,
      items,
      mastery: weightedMastery(items.map((item) => item.node)),
      evidenceCount: items.reduce((total, item) => total + item.node.mastery.evidence_count, 0),
    }))
    return {
      key,
      volume: chapter.volume,
      name: chapter.name,
      items: chapter.items,
      sections,
      mastery: weightedMastery(chapter.items.map((item) => item.node)),
    }
  })
})

const volumeLabel = computed(() => {
  const volumes = [...new Set(chapters.value.map((chapter) => chapter.volume).filter(Boolean))]
  return volumes.length ? volumes.join('、') : '教材结构'
})

const activeChapter = computed(() => chapters.value.find((chapter) => chapter.key === activeChapterKey.value)
  ?? chapters.value[0]
  ?? null)

const activeSection = computed(() => activeChapter.value?.sections.find((section) => section.name === activeSectionName.value)
  ?? null)

const visibleItems = computed<EnrichedNode[]>(() => {
  if (activeSection.value) return activeSection.value.items
  return activeChapter.value?.items ?? []
})

const selectedNode = computed(() => props.nodes.find((node) => node.stable_key === props.selectedKey)
  ?? visibleItems.value[0]?.node
  ?? null)

const selectedRelations = computed(() => {
  if (!selectedNode.value) return []
  return props.edges.filter((edge) => (
    edge.source_key === selectedNode.value?.stable_key || edge.target_key === selectedNode.value?.stable_key
  )).map((edge) => {
    const otherKey = edge.source_key === selectedNode.value?.stable_key ? edge.target_key : edge.source_key
    return {
      ...edge,
      other: props.nodes.find((node) => node.stable_key === otherKey)?.display_name ?? otherKey,
    }
  })
})

watch(chapters, (items) => {
  if (!items.some((item) => item.key === activeChapterKey.value)) {
    activeChapterKey.value = items[0]?.key ?? ''
    activeSectionName.value = ''
  }
  if (activeChapterKey.value && !expandedChapterKeys.value.includes(activeChapterKey.value)) {
    expandedChapterKeys.value = [...expandedChapterKeys.value, activeChapterKey.value]
  }
}, { immediate: true })

watch(activeChapter, (chapter) => {
  if (activeSectionName.value && !chapter?.sections.some((section) => section.name === activeSectionName.value)) {
    activeSectionName.value = ''
  }
})

watch(() => props.selectedKey, (key) => {
  const target = chapters.value
    .flatMap((chapter) => chapter.sections.map((section) => ({ chapter, section })))
    .find(({ section }) => section.items.some((item) => item.node.stable_key === key))
  if (!target) return
  activeChapterKey.value = target.chapter.key
  activeSectionName.value = target.section.name
  if (!expandedChapterKeys.value.includes(target.chapter.key)) {
    expandedChapterKeys.value = [...expandedChapterKeys.value, target.chapter.key]
  }
})

function percentage(value: number | null): string {
  return value === null ? '无证据' : `${Math.round(value * 100)}%`
}

function masteryClass(value: number | null): string {
  if (value === null) return 'is-empty'
  if (value < 0.6) return 'is-low'
  if (value < 0.75) return 'is-mid'
  return 'is-good'
}

function toggleChapter(key: string): void {
  expandedChapterKeys.value = expandedChapterKeys.value.includes(key)
    ? expandedChapterKeys.value.filter((item) => item !== key)
    : [...expandedChapterKeys.value, key]
}

function chooseChapter(chapter: ChapterGroup): void {
  activeChapterKey.value = chapter.key
  activeSectionName.value = ''
  if (!expandedChapterKeys.value.includes(chapter.key)) {
    expandedChapterKeys.value = [...expandedChapterKeys.value, chapter.key]
  }
}

function chooseSection(chapter: ChapterGroup, sectionName: string): void {
  activeChapterKey.value = chapter.key
  activeSectionName.value = sectionName
}
</script>

<template>
  <section class="structure-browser" aria-label="教材知识结构">
    <aside class="structure-browser__chapters">
      <header><strong>{{ volumeLabel }}</strong><span>{{ nodes.length }} 个知识点</span></header>
      <nav class="structure-browser__chapter-tree" aria-label="章节导航">
        <div v-for="chapter in chapters" :key="chapter.key" class="structure-browser__chapter-node">
          <div class="structure-browser__chapter-row">
            <button
              type="button"
              class="structure-browser__chapter-toggle"
              :aria-label="`${expandedChapterKeys.includes(chapter.key) ? '收起' : '展开'}${chapter.name}`"
              :aria-expanded="expandedChapterKeys.includes(chapter.key)"
              @click="toggleChapter(chapter.key)"
            >
              <span aria-hidden="true">{{ expandedChapterKeys.includes(chapter.key) ? '▾' : '▸' }}</span>
            </button>
            <button
              type="button"
              class="structure-browser__chapter-button"
              :class="{ 'is-active': chapter.key === activeChapter?.key && !activeSection }"
              @click="chooseChapter(chapter)"
            >
              <span>{{ chapter.name }}</span><b>{{ percentage(chapter.mastery) }}</b>
            </button>
          </div>
          <ul v-if="expandedChapterKeys.includes(chapter.key)" class="structure-browser__section-list">
            <li v-for="section in chapter.sections" :key="section.name">
              <button
                type="button"
                :class="{ 'is-active': chapter.key === activeChapter?.key && section.name === activeSectionName && activeSectionName }"
                @click="chooseSection(chapter, section.name)"
              >
                <span>{{ section.name }}</span><b>{{ percentage(section.mastery) }}</b>
              </button>
            </li>
          </ul>
        </div>
      </nav>
    </aside>

    <main class="structure-browser__tree">
      <header v-if="activeChapter">
        <div>
          <span>{{ activeSection ? '当前小节' : '当前章节' }}</span>
          <h2>{{ activeSection ? `${activeChapter.name} / ${activeSection.name}` : activeChapter.name }}</h2>
        </div>
        <strong v-if="activeSection">{{ activeSection.items.length }} 知识点</strong>
        <strong v-else>{{ activeChapter.sections.length }} 小节 · {{ activeChapter.items.length }} 知识点</strong>
      </header>
      <div class="structure-browser__legend">
        <span class="is-low">待补强</span><span class="is-mid">需巩固</span><span class="is-good">较稳定</span><span class="is-empty">无证据</span>
      </div>
      <button
        v-if="activeSection && activeChapter"
        type="button"
        class="structure-browser__back"
        @click="chooseChapter(activeChapter)"
      >
        ← 返回{{ activeChapter.name }}的小节列表
      </button>

      <template v-if="!activeSection && activeChapter">
        <button
          v-for="section in activeChapter.sections"
          :key="section.name"
          type="button"
          :class="['structure-browser__section-card', masteryClass(section.mastery)]"
          @click="chooseSection(activeChapter, section.name)"
        >
          <span>{{ section.name }}</span>
          <i><b :style="{ width: section.mastery === null ? '0%' : `${Math.round(section.mastery * 100)}%` }" /></i>
          <strong>{{ percentage(section.mastery) }}</strong>
          <small>{{ section.items.length }} 知识点 · {{ section.evidenceCount }} 条证据</small>
        </button>
      </template>

      <div v-else class="structure-browser__points">
        <button
          v-for="item in visibleItems"
          :key="item.node.stable_key"
          type="button"
          :class="['structure-browser__point', masteryClass(item.node.mastery.value), { 'is-selected': selectedNode?.stable_key === item.node.stable_key }]"
          :title="item.node.display_name"
          @click="emit('select', item.node.stable_key)"
        >
          <span>{{ item.path.label }}</span>
          <i><b :style="{ width: item.node.mastery.value === null ? '0%' : `${Math.round(item.node.mastery.value * 100)}%` }" /></i>
          <strong>{{ percentage(item.node.mastery.value) }}</strong>
          <small>{{ item.node.mastery.evidence_count }} 条证据</small>
        </button>
      </div>
    </main>

    <aside class="structure-browser__detail">
      <template v-if="selectedNode">
        <span>当前知识点</span>
        <h2>{{ selectedNode.display_name }}</h2>
        <div :class="['structure-browser__score', masteryClass(selectedNode.mastery.value)]">
          <strong>{{ percentage(selectedNode.mastery.value) }}</strong><span>群体掌握度</span>
        </div>
        <dl>
          <div><dt>证据</dt><dd>{{ selectedNode.mastery.evidence_count }} 条</dd></div>
          <div><dt>学生</dt><dd>{{ selectedNode.evidence.student_count }} 人</dd></div>
          <div><dt>扣分</dt><dd>{{ selectedNode.evidence.deduction_count }} 次</dd></div>
        </dl>
        <p>{{ selectedNode.definition }}</p>
        <section>
          <strong>局部知识联系</strong>
          <p v-if="!selectedRelations.length">当前没有已确认的直接关系。</p>
          <button v-for="relation in selectedRelations" :key="relation.relation_key" type="button" @click="emit('select', relation.source_key === selectedNode.stable_key ? relation.target_key : relation.source_key)">
            <span>{{ relation.relation_type === 'prerequisite' ? '先修' : relation.relation_type === 'parent' ? '上下位' : '相关' }}</span>
            <strong>{{ relation.other }}</strong>
          </button>
        </section>
        <RouterLink :to="{ name: 'training', query: { mode: 'chapter' } }">用本章安排训练</RouterLink>
      </template>
    </aside>
  </section>
</template>

<style scoped>
.structure-browser { display: grid; grid-template-columns: 240px minmax(460px, 1fr) 280px; min-height: 620px; overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.structure-browser__chapters { padding: .85rem; border-right: 1px solid var(--color-border-default); background: var(--color-bg-subtle); }
.structure-browser__chapters header { display: grid; gap: .15rem; padding: .35rem .45rem .8rem; }
.structure-browser__chapters header span, .structure-browser__tree header span, .structure-browser__detail > span { color: var(--color-text-secondary); font-size: .78rem; }
.structure-browser__chapter-node { margin-bottom: .25rem; }
.structure-browser__chapter-row { display: grid; grid-template-columns: 1.4rem 1fr; align-items: stretch; }
.structure-browser__chapter-toggle { border: 0; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.structure-browser__chapter-button { display: flex; justify-content: space-between; gap: .5rem; padding: .6rem .6rem; border: 1px solid transparent; border-radius: var(--radius-control); background: transparent; color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__chapter-button.is-active { border-color: var(--color-accent); background: var(--color-bg-surface); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__section-list { display: grid; gap: .15rem; margin: .15rem 0 .4rem; padding: 0 0 0 1.4rem; list-style: none; }
.structure-browser__section-list button { width: 100%; display: flex; justify-content: space-between; gap: .5rem; padding: .45rem .6rem; border: 1px solid transparent; border-radius: var(--radius-control); background: transparent; color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__section-list button.is-active { border-color: var(--color-accent); background: var(--color-bg-surface); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__section-list button span, .structure-browser__chapter-button span { overflow: hidden; text-overflow: ellipsis; }
.structure-browser__tree { min-width: 0; padding: 1rem; }
.structure-browser__tree > header { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding-bottom: .8rem; border-bottom: 1px solid var(--color-border-default); }
.structure-browser h2 { margin: .15rem 0 0; font-size: 1.25rem; }
.structure-browser__legend { display: flex; gap: .45rem; padding: .65rem 0; }
.structure-browser__legend span { padding: .22rem .48rem; border-radius: 999px; background: var(--color-bg-subtle); font-size: .74rem; }
.structure-browser__legend .is-low { color: var(--color-danger); background: var(--color-danger-subtle); }.structure-browser__legend .is-mid { color: var(--color-warning); background: var(--color-warning-subtle); }.structure-browser__legend .is-good { color: var(--color-success); background: var(--color-success-subtle); }
.structure-browser__back { margin-bottom: .6rem; padding: .4rem .7rem; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-secondary); cursor: pointer; }
.structure-browser__section-card { width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) 8rem auto; gap: .35rem .75rem; align-items: center; margin-bottom: .6rem; padding: .75rem .85rem; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__section-card:hover { border-color: var(--color-accent); }
.structure-browser__section-card > span { overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.structure-browser__section-card small { grid-column: 1 / -1; color: var(--color-text-secondary); }
.structure-browser__section-card > i { height: .42rem; overflow: hidden; border-radius: 999px; background: var(--color-bg-selected); }
.structure-browser__section-card > i b { display: block; height: 100%; background: var(--color-success); }
.structure-browser__section-card.is-mid > i b { background: var(--color-warning); }.structure-browser__section-card.is-low > i b { background: var(--color-danger); }
.structure-browser__points { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-border-default); }
.structure-browser__point { display: grid; grid-template-columns: minmax(0, 1fr) 4rem auto; gap: .35rem .55rem; align-items: center; padding: .65rem; border: 0; background: var(--color-bg-surface); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__point.is-selected { background: var(--color-accent-subtle); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__point > span { overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.structure-browser__point small { grid-column: 1 / -1; color: var(--color-text-secondary); }
.structure-browser__point > i { height: .42rem; overflow: hidden; border-radius: 999px; background: var(--color-bg-selected); }.structure-browser__point > i b { display: block; height: 100%; background: var(--color-success); }.structure-browser__point.is-mid > i b { background: var(--color-warning); }.structure-browser__point.is-low > i b { background: var(--color-danger); }
.structure-browser__detail { padding: 1rem; border-left: 1px solid var(--color-border-default); background: var(--color-bg-subtle); }.structure-browser__score { display: grid; gap: .15rem; margin: 1rem 0; padding: .85rem; border-left: 4px solid var(--color-success); background: var(--color-success-subtle); }.structure-browser__score.is-mid { border-color: var(--color-warning); background: var(--color-warning-subtle); }.structure-browser__score.is-low { border-color: var(--color-danger); background: var(--color-danger-subtle); }.structure-browser__score strong { font-size: 1.65rem; }
.structure-browser__detail dl { display: grid; gap: .35rem; margin: 0; }.structure-browser__detail dl div { display: flex; justify-content: space-between; padding: .45rem 0; border-bottom: 1px solid var(--color-border-default); }.structure-browser__detail p { color: var(--color-text-secondary); line-height: 1.65; }
.structure-browser__detail section { display: grid; gap: .4rem; margin: 1rem 0; }.structure-browser__detail section button { display: grid; gap: .1rem; padding: .55rem; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); text-align: left; cursor: pointer; }.structure-browser__detail section button span { color: var(--color-text-secondary); font-size: .72rem; }.structure-browser__detail > a { display: flex; justify-content: center; padding: .65rem; border-radius: var(--radius-control); background: var(--color-accent); color: var(--primary-foreground); text-decoration: none; }
@media (max-width: 1100px) { .structure-browser { grid-template-columns: 200px 1fr; }.structure-browser__detail { grid-column: 1 / -1; border-top: 1px solid var(--color-border-default); border-left: 0; }.structure-browser__points { grid-template-columns: 1fr; } }
</style>
