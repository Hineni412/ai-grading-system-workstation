<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { TrainingDiagnosis, TrainingWeakPoint } from '../../api/training'

interface KnowledgeNode {
  key: string
  label: string
  parentKey: string | null
  weak: TrainingWeakPoint | null
  children: KnowledgeNode[]
}

const props = defineProps<{
  diagnosis: TrainingDiagnosis
  modelValue: string[]
  title: string
  description: string
}>()
const emit = defineEmits<{
  'update:modelValue': [keys: string[]]
  focus: [key: string]
}>()

const activeRootKey = ref('')
const expandedSectionKeys = ref<string[]>([])

const tree = computed(() => {
  const weakByKey = new Map((props.diagnosis.group_weak_points ?? []).map((item) => [
    item.knowledge_key,
    item,
  ]))
  const nodes = new Map<string, KnowledgeNode>()
  const ensure = (key: string, label: string, parentKey: string | null): KnowledgeNode => {
    const existing = nodes.get(key)
    if (existing) {
      if (label) existing.label = label
      if (parentKey !== null) existing.parentKey = parentKey
      return existing
    }
    const node: KnowledgeNode = {
      key,
      label: label || key,
      parentKey,
      weak: weakByKey.get(key) ?? null,
      children: [],
    }
    nodes.set(key, node)
    return node
  }

  for (const item of props.diagnosis.knowledge_catalog ?? []) {
    ensure(item.knowledge_key, item.knowledge_point, item.parent_knowledge_key ?? null)
  }
  for (const weak of props.diagnosis.group_weak_points ?? []) {
    ensure(weak.knowledge_key, weak.knowledge_point, weak.parent_knowledge_key ?? null).weak = weak
  }
  for (const node of nodes.values()) {
    if (!node.parentKey || !nodes.has(node.parentKey)) continue
    nodes.get(node.parentKey)!.children.push(node)
  }
  return [...nodes.values()].filter((node) => !node.parentKey || !nodes.has(node.parentKey))
})

const activeRoot = computed(() => tree.value.find((node) => node.key === activeRootKey.value) ?? tree.value[0] ?? null)

watch(tree, (roots) => {
  if (!roots.some((node) => node.key === activeRootKey.value)) {
    activeRootKey.value = roots[0]?.key ?? ''
  }
}, { immediate: true })

watch(activeRoot, (root) => {
  expandedSectionKeys.value = root?.children.map((item) => item.key) ?? []
  if (root) emit('focus', root.key)
}, { immediate: true })

function masteryText(node: KnowledgeNode): string {
  return node.weak ? `${Math.round(node.weak.mastery * 100)}%` : '无证据'
}

function masteryClass(node: KnowledgeNode): string {
  if (!node.weak) return 'is-empty'
  if (node.weak.mastery < 0.6) return 'is-low'
  if (node.weak.mastery < 0.75) return 'is-mid'
  return 'is-good'
}

function toggleSection(key: string): void {
  expandedSectionKeys.value = expandedSectionKeys.value.includes(key)
    ? expandedSectionKeys.value.filter((item) => item !== key)
    : [...expandedSectionKeys.value, key]
}

function toggleTarget(key: string): void {
  emit('update:modelValue', props.modelValue.includes(key)
    ? props.modelValue.filter((item) => item !== key)
    : [...props.modelValue, key])
}

function selectableChildren(node: KnowledgeNode): KnowledgeNode[] {
  return node.children.length ? node.children : [node]
}
</script>

<template>
  <section class="knowledge-structure" aria-labelledby="training-structure-title">
    <header>
      <div>
        <p class="structure-eyebrow">完整结构，不替教师挑前三项</p>
        <h3 id="training-structure-title">{{ title }}</h3>
        <p>{{ description }}</p>
      </div>
      <strong>{{ modelValue.length }} 项已选</strong>
    </header>

    <div v-if="tree.length" class="structure-layout">
      <nav aria-label="章节选择">
        <button
          v-for="root in tree"
          :key="root.key"
          type="button"
          :class="{ 'is-active': activeRoot?.key === root.key }"
          @click="activeRootKey = root.key"
        >
          <span>{{ root.label }}</span>
          <b>{{ masteryText(root) }}</b>
        </button>
      </nav>

      <div v-if="activeRoot" class="structure-detail">
        <div class="structure-legend" aria-label="掌握度图例">
          <span><i class="is-low" />待补强 &lt; 60%</span>
          <span><i class="is-mid" />需巩固 60–74%</span>
          <span><i class="is-good" />较稳定 ≥ 75%</span>
          <span><i class="is-empty" />灰底未被覆盖，斜纹为无证据</span>
        </div>

        <article
          v-for="section in activeRoot.children"
          :key="section.key"
          class="structure-section"
        >
          <button type="button" class="structure-section-heading" @click="toggleSection(section.key)">
            <span>{{ expandedSectionKeys.includes(section.key) ? '−' : '+' }}</span>
            <strong>{{ section.label }}</strong>
            <b>{{ masteryText(section) }}</b>
          </button>
          <div v-if="expandedSectionKeys.includes(section.key)" class="structure-points">
            <label
              v-for="point in selectableChildren(section)"
              :key="point.key"
              :class="['structure-point', masteryClass(point)]"
            >
              <input
                type="checkbox"
                :checked="modelValue.includes(point.key)"
                @change="toggleTarget(point.key)"
              >
              <span class="structure-point-name">{{ point.label }}</span>
              <span class="structure-track" aria-hidden="true">
                <i v-if="point.weak" :style="{ width: `${Math.round(point.weak.mastery * 100)}%` }" />
                <b />
              </span>
              <strong>{{ masteryText(point) }}</strong>
              <small v-if="point.weak">{{ point.weak.evidence_count }} 条证据</small>
              <small v-else>暂无证据，不计入群体分母</small>
            </label>
          </div>
        </article>

        <label v-if="!activeRoot.children.length" :class="['structure-point', masteryClass(activeRoot)]">
          <input
            type="checkbox"
            :checked="modelValue.includes(activeRoot.key)"
            @change="toggleTarget(activeRoot.key)"
          >
          <span class="structure-point-name">{{ activeRoot.label }}</span>
          <span class="structure-track" aria-hidden="true">
            <i v-if="activeRoot.weak" :style="{ width: `${Math.round(activeRoot.weak.mastery * 100)}%` }" />
            <b />
          </span>
          <strong>{{ masteryText(activeRoot) }}</strong>
          <small v-if="activeRoot.weak">{{ activeRoot.weak.evidence_count }} 条证据</small>
          <small v-else>暂无证据，不计入群体分母</small>
        </label>
      </div>
    </div>
    <p v-else class="structure-empty">当前范围没有可展示的知识结构。</p>
  </section>
</template>

<style scoped>
.knowledge-structure { display: grid; gap: 1rem; margin: 1rem 0; padding: 1rem; border: 1px solid var(--color-border-default); border-radius: 14px; background: white; }
.knowledge-structure > header { display: flex; justify-content: space-between; gap: 1rem; align-items: start; }
.knowledge-structure h3, .knowledge-structure p { margin: .2rem 0; }
.knowledge-structure > header > strong { flex: 0 0 auto; padding: .4rem .65rem; border-radius: 999px; background: var(--color-accent-subtle); color: var(--color-accent); }
.structure-eyebrow { color: var(--color-text-secondary); font-size: .78rem; font-weight: 700; letter-spacing: .04em; }
.structure-layout { display: grid; grid-template-columns: minmax(180px, .42fr) minmax(0, 1.58fr); gap: 1rem; align-items: start; }
.structure-layout > nav { display: grid; gap: .45rem; position: sticky; top: calc(var(--shell-topbar-height, 64px) + 1rem); }
.structure-layout > nav button { display: flex; justify-content: space-between; gap: .75rem; padding: .65rem .75rem; border: 1px solid transparent; border-radius: 9px; background: var(--color-bg-subtle); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-layout > nav button.is-active { border-color: var(--color-accent); background: var(--color-accent-subtle); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-layout > nav b { white-space: nowrap; }
.structure-detail { min-width: 0; display: grid; gap: .65rem; }
.structure-legend { display: flex; flex-wrap: wrap; gap: .5rem 1rem; color: var(--color-text-secondary); font-size: .78rem; }
.structure-legend span { display: inline-flex; align-items: center; gap: .3rem; }
.structure-legend i { width: .7rem; height: .7rem; border-radius: 3px; background: var(--color-danger, #c85c55); }
.structure-legend i.is-mid { background: var(--color-warning, #c89428); }
.structure-legend i.is-good { background: var(--color-success, #2d8478); }
.structure-legend i.is-empty { border: 1px solid var(--color-border-default); background: repeating-linear-gradient(135deg, #eef1f2 0 4px, #fff 4px 8px); }
.structure-section { overflow: hidden; border: 1px solid var(--color-border-default); border-radius: 10px; }
.structure-section-heading { width: 100%; display: grid; grid-template-columns: 1.2rem 1fr auto; gap: .55rem; padding: .7rem .8rem; border: 0; background: var(--color-bg-subtle); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-points { display: grid; }
.structure-point { display: grid; grid-template-columns: auto minmax(140px, .72fr) minmax(160px, 1.4fr) 3.2rem 6.8rem; align-items: center; gap: .65rem; min-height: 2.9rem; padding: .45rem .75rem; border-top: 1px solid var(--color-border-default); cursor: pointer; }
.structure-point:hover { background: var(--color-bg-subtle); }
.structure-track { position: relative; height: .62rem; overflow: hidden; border-radius: 999px; background: #e8edef; }
.structure-track > i { display: block; height: 100%; background: var(--color-success, #2d8478); }
.structure-point.is-low .structure-track > i { background: var(--color-danger, #c85c55); }
.structure-point.is-mid .structure-track > i { background: var(--color-warning, #c89428); }
.structure-point.is-empty .structure-track { background: repeating-linear-gradient(135deg, #e8edef 0 6px, #f8f9f9 6px 12px); }
.structure-track > b { position: absolute; top: -2px; bottom: -2px; left: 70%; width: 2px; background: rgba(24, 38, 46, .45); }
.structure-point > strong, .structure-point > small { white-space: nowrap; }
.structure-point > small { color: var(--color-text-secondary); }
.structure-empty { padding: 1rem; color: var(--color-text-secondary); text-align: center; }
@media (max-width: 900px) {
  .structure-layout { grid-template-columns: 1fr; }
  .structure-layout > nav { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .structure-point { grid-template-columns: auto minmax(120px, 1fr) 3rem; }
  .structure-track, .structure-point > small { grid-column: 2 / -1; }
}
</style>
