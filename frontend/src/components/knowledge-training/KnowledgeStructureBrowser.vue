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

const activeChapter = ref('')
const openSections = ref<string[]>([])

function chapterOf(node: GraphNode): string {
  return node.evidence.tag_context['教材章节']?.[0]
    ?? node.curriculum_anchors.find((item) => item.includes('章'))
    ?? '未分章'
}

function sectionOf(node: GraphNode): string {
  return node.evidence.tag_context['教材小节']?.[0]
    ?? node.display_name.split(/[·：]/)[0]
    ?? node.display_name
}

function weightedMastery(nodes: GraphNode[]): number | null {
  const available = nodes.filter((node) => node.mastery.status === 'available' && node.mastery.value !== null)
  if (!available.length) return null
  const weight = available.reduce((total, node) => total + Math.max(node.mastery.evidence_count, 1), 0)
  return available.reduce((total, node) => (
    total + (node.mastery.value ?? 0) * Math.max(node.mastery.evidence_count, 1)
  ), 0) / weight
}

const chapters = computed(() => {
  const result = new Map<string, GraphNode[]>()
  for (const node of props.nodes) result.set(chapterOf(node), [...(result.get(chapterOf(node)) ?? []), node])
  return [...result].map(([name, nodes]) => ({ name, nodes, mastery: weightedMastery(nodes) }))
})

const sections = computed(() => {
  const result = new Map<string, GraphNode[]>()
  for (const node of chapters.value.find((item) => item.name === activeChapter.value)?.nodes ?? []) {
    result.set(sectionOf(node), [...(result.get(sectionOf(node)) ?? []), node])
  }
  return [...result].map(([name, nodes]) => ({ name, nodes, mastery: weightedMastery(nodes) }))
})

const selectedNode = computed(() => props.nodes.find((node) => node.stable_key === props.selectedKey)
  ?? sections.value[0]?.nodes[0]
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
  if (!items.some((item) => item.name === activeChapter.value)) activeChapter.value = items[0]?.name ?? ''
}, { immediate: true })

watch(sections, (items) => {
  openSections.value = items.map((item) => item.name)
  const selectedInChapter = items.some((section) => section.nodes.some((node) => node.stable_key === props.selectedKey))
  if (!selectedInChapter && items[0]?.nodes[0]) emit('select', items[0].nodes[0].stable_key)
}, { immediate: true })

function percentage(value: number | null): string {
  return value === null ? '无证据' : `${Math.round(value * 100)}%`
}

function masteryClass(value: number | null): string {
  if (value === null) return 'is-empty'
  if (value < 0.6) return 'is-low'
  if (value < 0.75) return 'is-mid'
  return 'is-good'
}

function toggleSection(name: string): void {
  openSections.value = openSections.value.includes(name)
    ? openSections.value.filter((item) => item !== name)
    : [...openSections.value, name]
}
</script>

<template>
  <section class="structure-browser" aria-label="教材知识结构">
    <aside class="structure-browser__chapters">
      <header><strong>七年级下册</strong><span>{{ nodes.length }} 个知识点</span></header>
      <button
        v-for="chapter in chapters"
        :key="chapter.name"
        type="button"
        :class="{ 'is-active': chapter.name === activeChapter }"
        @click="activeChapter = chapter.name"
      >
        <span>{{ chapter.name }}</span><b>{{ percentage(chapter.mastery) }}</b>
      </button>
    </aside>

    <main class="structure-browser__tree">
      <header>
        <div><span>当前章节</span><h2>{{ activeChapter }}</h2></div>
        <strong>{{ sections.length }} 小节 · {{ sections.reduce((total, item) => total + item.nodes.length, 0) }} 知识点</strong>
      </header>
      <div class="structure-browser__legend">
        <span class="is-low">待补强</span><span class="is-mid">需巩固</span><span class="is-good">较稳定</span><span class="is-empty">无证据</span>
      </div>
      <article v-for="section in sections" :key="section.name" class="structure-browser__section">
        <button type="button" @click="toggleSection(section.name)">
          <span>{{ openSections.includes(section.name) ? '−' : '+' }}</span>
          <strong>{{ section.name }}</strong>
          <b>{{ percentage(section.mastery) }}</b>
        </button>
        <div v-if="openSections.includes(section.name)" class="structure-browser__points">
          <button
            v-for="node in section.nodes"
            :key="node.stable_key"
            type="button"
            :class="['structure-browser__point', masteryClass(node.mastery.value), { 'is-selected': selectedNode?.stable_key === node.stable_key }]"
            @click="emit('select', node.stable_key)"
          >
            <span>{{ node.display_name }}</span>
            <i><b :style="{ width: node.mastery.value === null ? '0%' : `${Math.round(node.mastery.value * 100)}%` }" /></i>
            <strong>{{ percentage(node.mastery.value) }}</strong>
            <small>{{ node.mastery.evidence_count }} 条证据</small>
          </button>
        </div>
      </article>
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
.structure-browser { display: grid; grid-template-columns: 220px minmax(460px, 1fr) 280px; min-height: 620px; overflow: hidden; border: 1px solid var(--color-border-default); border-radius: 14px; background: white; }
.structure-browser__chapters { padding: .85rem; border-right: 1px solid var(--color-border-default); background: #f4f7f7; }
.structure-browser__chapters header { display: grid; gap: .15rem; padding: .35rem .45rem .8rem; }
.structure-browser__chapters header span, .structure-browser__tree header span, .structure-browser__detail > span { color: var(--color-text-secondary); font-size: .78rem; }
.structure-browser__chapters button { width: 100%; display: flex; justify-content: space-between; gap: .5rem; margin-bottom: .35rem; padding: .65rem .7rem; border: 1px solid transparent; border-radius: 9px; background: transparent; color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__chapters button.is-active { border-color: var(--color-accent); background: white; box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__tree { min-width: 0; padding: 1rem; }
.structure-browser__tree > header { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding-bottom: .8rem; border-bottom: 1px solid var(--color-border-default); }
.structure-browser h2 { margin: .15rem 0 0; font-size: 1.25rem; }
.structure-browser__legend { display: flex; gap: .45rem; padding: .65rem 0; }
.structure-browser__legend span { padding: .22rem .48rem; border-radius: 999px; background: #eef1f2; font-size: .74rem; }
.structure-browser__legend .is-low { color: #a83f3b; background: #fae8e6; }.structure-browser__legend .is-mid { color: #8a5a08; background: #fff1d6; }.structure-browser__legend .is-good { color: #17695e; background: #e1f2ef; }
.structure-browser__section { overflow: hidden; margin-bottom: .6rem; border: 1px solid var(--color-border-default); border-radius: 10px; }
.structure-browser__section > button { width: 100%; display: grid; grid-template-columns: 1.2rem 1fr auto; gap: .5rem; padding: .65rem .75rem; border: 0; background: #f7f9f9; color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__points { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; background: var(--color-border-default); }
.structure-browser__point { display: grid; grid-template-columns: minmax(0, 1fr) 4rem auto; gap: .35rem .55rem; align-items: center; padding: .65rem; border: 0; background: white; color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-browser__point.is-selected { background: var(--color-accent-subtle); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-browser__point > span { overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.structure-browser__point small { grid-column: 1 / -1; color: var(--color-text-secondary); }
.structure-browser__point > i { height: .42rem; overflow: hidden; border-radius: 999px; background: #e6ebec; }.structure-browser__point > i b { display: block; height: 100%; background: #2d8478; }.structure-browser__point.is-mid > i b { background: #b47a16; }.structure-browser__point.is-low > i b { background: #c5524d; }
.structure-browser__detail { padding: 1rem; border-left: 1px solid var(--color-border-default); background: #fbfcfc; }.structure-browser__score { display: grid; gap: .15rem; margin: 1rem 0; padding: .85rem; border-left: 4px solid #2d8478; background: #edf6f4; }.structure-browser__score.is-mid { border-color: #b47a16; background: #fff7e8; }.structure-browser__score.is-low { border-color: #c5524d; background: #fbeceb; }.structure-browser__score strong { font-size: 1.65rem; }
.structure-browser__detail dl { display: grid; gap: .35rem; margin: 0; }.structure-browser__detail dl div { display: flex; justify-content: space-between; padding: .45rem 0; border-bottom: 1px solid var(--color-border-default); }.structure-browser__detail p { color: var(--color-text-secondary); line-height: 1.65; }
.structure-browser__detail section { display: grid; gap: .4rem; margin: 1rem 0; }.structure-browser__detail section button { display: grid; gap: .1rem; padding: .55rem; border: 1px solid var(--color-border-default); border-radius: 8px; background: white; color: var(--color-text-primary); text-align: left; cursor: pointer; }.structure-browser__detail section button span { color: var(--color-text-secondary); font-size: .72rem; }.structure-browser__detail > a { display: flex; justify-content: center; padding: .65rem; border-radius: 8px; background: var(--color-accent); color: white; text-decoration: none; }
@media (max-width: 1100px) { .structure-browser { grid-template-columns: 190px 1fr; }.structure-browser__detail { grid-column: 1 / -1; border-top: 1px solid var(--color-border-default); border-left: 0; }.structure-browser__points { grid-template-columns: 1fr; } }
</style>
