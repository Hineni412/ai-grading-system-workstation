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
  evidenceCount: number
}

interface ChapterGroup {
  key: string
  volume: string
  name: string
  items: EnrichedNode[]
  sections: SectionGroup[]
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

function ownMastery(node: GraphNode): number | null {
  return node.mastery.status === 'available' && node.mastery.evidence_count > 0
    ? node.mastery.value : null
}
function observedCount(items: EnrichedNode[]): number {
  return items.filter(item => ownMastery(item.node) !== null).length
}

const chapters = computed<ChapterGroup[]>(() => {
  const chapterMap = new Map<string, { volume: string; name: string; items: EnrichedNode[] }>()
  const parents = new Set(props.edges.filter(edge => edge.relation_type === 'parent').map(edge => edge.target_key))
  const paths = props.nodes.map(node => ({ key: node.stable_key, parts: splitPath(node.display_name) }))
  for (const path of paths) {
    if (path.parts.length > 1 && paths.some(other => other.parts.length > path.parts.length
      && path.parts.every((part, index) => part === other.parts[index]))) parents.add(path.key)
  }
  for (const node of [...props.nodes].filter(node => !parents.has(node.stable_key)).sort((a, b) => a.stable_key.localeCompare(b.stable_key, 'zh-CN', { numeric: true }))) {
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
      evidenceCount: items.reduce((total, item) => total + item.node.mastery.evidence_count, 0),
    }))
    return {
      key,
      volume: chapter.volume,
      name: chapter.name,
      items: chapter.items,
      sections,
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

const selectedNode = computed(() => visibleItems.value.find((item) => item.node.stable_key === props.selectedKey)?.node
  ?? visibleItems.value[0]?.node
  ?? null)

const visibleSections = computed(() => activeSection.value ? [activeSection.value] : activeChapter.value?.sections ?? [])

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
  if (activeChapterKey.value === target.chapter.key && (!activeSectionName.value || activeSectionName.value === target.section.name)) return
  activeChapterKey.value = target.chapter.key
  activeSectionName.value = target.section.name
  if (!expandedChapterKeys.value.includes(target.chapter.key)) {
    expandedChapterKeys.value = [...expandedChapterKeys.value, target.chapter.key]
  }
})

function percentage(value: number | null): string {
  return value === null ? '证据不足' : `${Math.round(value * 100)}%`
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
      <header><strong>{{ volumeLabel }}</strong><span>{{ chapters.reduce((sum, chapter) => sum + chapter.items.length, 0) }} 个知识点</span></header>
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
              <span>{{ chapter.name }}</span><b>{{ observedCount(chapter.items) }}/{{ chapter.items.length }}</b>
            </button>
          </div>
          <ul v-if="expandedChapterKeys.includes(chapter.key)" class="structure-browser__section-list">
            <li v-for="section in chapter.sections" :key="section.name">
              <button
                type="button"
                :class="{ 'is-active': chapter.key === activeChapter?.key && section.name === activeSectionName && activeSectionName }"
                @click="chooseSection(chapter, section.name)"
              >
                <span>{{ section.name }}</span><b>{{ observedCount(section.items) }}/{{ section.items.length }}</b>
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
        <span class="is-low">待补强</span><span class="is-mid">需巩固</span><span class="is-good">较稳定</span><span class="is-empty">证据不足</span>
      </div>
      <button
        v-if="activeSection && activeChapter"
        type="button"
        class="structure-browser__back"
        @click="chooseChapter(activeChapter)"
      >
        ← 返回{{ activeChapter.name }}的小节列表
      </button>

      <p class="structure-browser__evidence-note">每格只显示该知识点自身证据；目录数字表示有证据的知识点数 / 总数。相邻或相关知识点互不推断。</p>
      <section v-for="section in visibleSections" :key="section.name" class="structure-browser__heatmap-section">
        <h3>{{ section.name }} <small>{{ observedCount(section.items) }}/{{ section.items.length }} 项有证据</small></h3>
        <div class="structure-browser__points">
          <button v-for="item in section.items" :key="item.node.stable_key" type="button"
            :class="['structure-browser__point', masteryClass(ownMastery(item.node)), { 'is-selected': selectedNode?.stable_key === item.node.stable_key }]"
            :title="item.node.display_name" @click="emit('select', item.node.stable_key)">
            <span>{{ item.path.label }}</span>
            <i><b :style="{ width: `${Math.round((ownMastery(item.node) ?? 0) * 100)}%` }" /></i>
            <strong>{{ percentage(ownMastery(item.node)) }}</strong>
            <small>{{ item.node.mastery.evidence_count }} 条自身证据</small>
          </button>
        </div>
      </section>
    </main>

    <aside class="structure-browser__detail">
      <template v-if="selectedNode">
        <span>当前知识点</span>
        <h2>{{ selectedNode.display_name }}</h2>
        <div :class="['structure-browser__score', masteryClass(ownMastery(selectedNode))]">
          <strong>{{ percentage(ownMastery(selectedNode)) }}</strong><span>本知识点掌握状况</span>
        </div>
        <dl>
          <div><dt>证据</dt><dd>{{ selectedNode.mastery.evidence_count }} 条</dd></div>
          <div><dt>学生</dt><dd>{{ selectedNode.evidence.student_count }} 人</dd></div>
          <div><dt>扣分</dt><dd>{{ selectedNode.evidence.deduction_count }} 次</dd></div>
        </dl>
        <p>{{ selectedNode.definition }}</p>
        <p v-if="ownMastery(selectedNode) === null">本知识点证据不足，不依据先修、相关关系或相邻章节推断掌握情况。</p>
        <p v-else>仅根据本知识点已有作答与训练证据显示；安排练习时还会核对实际错题、解题要求与难度。</p>
        <RouterLink :to="{ name: 'training', query: { mode: 'chapter' } }">用本章安排训练</RouterLink>
      </template>
    </aside>
  </section>
</template>

<style scoped>
.structure-browser__evidence-note { color: var(--color-text-secondary); font-size: .8rem; }
.structure-browser__heatmap-section h3 { display: flex; justify-content: space-between; gap: var(--space-2); font-size: .95rem; }
.structure-browser__heatmap-section h3 small { color: var(--color-text-secondary); font-weight: normal; }

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
.structure-browser__point.is-low { background: var(--color-danger-subtle); }
.structure-browser__point.is-mid { background: var(--color-warning-subtle); }
.structure-browser__point.is-good { background: var(--color-success-subtle); }
.structure-browser__point.is-empty { background: var(--color-bg-subtle); }
.structure-browser__point.is-selected { outline: 1px solid var(--color-accent); outline-offset: -1px; box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__point > span { overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.structure-browser__point small { grid-column: 1 / -1; color: var(--color-text-secondary); }
.structure-browser__point > i { height: .42rem; overflow: hidden; border-radius: 999px; background: var(--color-bg-selected); }.structure-browser__point > i b { display: block; height: 100%; background: var(--color-success); }.structure-browser__point.is-mid > i b { background: var(--color-warning); }.structure-browser__point.is-low > i b { background: var(--color-danger); }
.structure-browser__detail { padding: 1rem; border-left: 1px solid var(--color-border-default); background: var(--color-bg-subtle); }.structure-browser__score { display: grid; gap: .15rem; margin: 1rem 0; padding: .85rem; border-left: 4px solid var(--color-success); background: var(--color-success-subtle); }.structure-browser__score.is-mid { border-color: var(--color-warning); background: var(--color-warning-subtle); }.structure-browser__score.is-low { border-color: var(--color-danger); background: var(--color-danger-subtle); }.structure-browser__score.is-empty { border-color: var(--color-border-default); background: var(--color-bg-subtle); }.structure-browser__score strong { font-size: 1.65rem; }
.structure-browser__detail dl { display: grid; gap: .35rem; margin: 0; }.structure-browser__detail dl div { display: flex; justify-content: space-between; padding: .45rem 0; border-bottom: 1px solid var(--color-border-default); }.structure-browser__detail p { color: var(--color-text-secondary); line-height: 1.65; }
.structure-browser__detail section { display: grid; gap: .4rem; margin: 1rem 0; }.structure-browser__detail section button { display: grid; gap: .1rem; padding: .55rem; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); text-align: left; cursor: pointer; }.structure-browser__detail section button span { color: var(--color-text-secondary); font-size: .72rem; }.structure-browser__detail > a { display: flex; justify-content: center; padding: .65rem; border-radius: var(--radius-control); background: var(--color-accent); color: var(--primary-foreground); text-decoration: none; }
@media (max-width: 1100px) { .structure-browser { grid-template-columns: 200px 1fr; }.structure-browser__detail { grid-column: 1 / -1; border-top: 1px solid var(--color-border-default); border-left: 0; }.structure-browser__points { grid-template-columns: 1fr; } }
@media (max-width: 700px) {
  .structure-browser { grid-template-columns: minmax(0, 1fr); min-height: 0; }
  .structure-browser__chapters { border-right: 0; border-bottom: 1px solid var(--color-border-default); }
  .structure-browser__chapter-tree { max-height: 230px; overflow-y: auto; }
  .structure-browser__tree > header { align-items: start; flex-wrap: wrap; gap: .35rem; }
  .structure-browser__legend { flex-wrap: wrap; }
  .structure-browser__heatmap-section h3 { flex-wrap: wrap; }
}
</style>
