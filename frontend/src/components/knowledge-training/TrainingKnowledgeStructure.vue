<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { knowledgeLeafLabel } from '../../api/question-bank'
import type { TrainingDiagnosis, TrainingWeakPoint } from '../../api/training'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'

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
  selectionKind?: 'targets' | 'range'
}>()
const emit = defineEmits<{
  'update:modelValue': [keys: string[]]
  focus: [key: string]
}>()

const selectionKind = computed(() => props.selectionKind ?? 'targets')
const selectedCountLabel = computed(() => (
  selectionKind.value === 'range'
    ? `${props.modelValue.length} 个范围`
    : `${props.modelValue.length} 项已选`
))

const activeRootKey = ref('')
const expandedSectionKeys = ref<string[]>([])
const showEmptyPoints = ref(false)

const curriculumScope = useCurriculumScopeStore()
onMounted(() => { void curriculumScope.initialize() })

// 目录标签是"册｜章｜小节｜细分点"全路径；按顶部学期（册别）过滤章。
function volumeOf(label: string): string {
  return label.split(/[|｜]/).map((part) => part.trim()).filter(Boolean)[0] ?? ''
}

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
  const roots = [...nodes.values()].filter((node) => !node.parentKey || !nodes.has(node.parentKey))
  const volume = curriculumScope.selectedVolume?.label
  if (!volume) return roots
  // 无路径分隔符的旧数据无法判定册别，保留显示。
  return roots.filter((node) => !/[|｜]/.test(node.label) || volumeOf(node.label) === volume)
})

const activeRoot = computed(() => tree.value.find((node) => node.key === activeRootKey.value) ?? tree.value[0] ?? null)

// 默认只展示有证据的知识点，避免整页被无证据项占满；可用开关查看完整结构。
const displaySections = computed(() => {
  const root = activeRoot.value
  if (!root) return []
  return root.children
    .map((section) => {
      const candidates = selectableChildren(section)
      return {
        section,
        points: showEmptyPoints.value ? candidates : candidates.filter((point) => point.weak !== null),
      }
    })
    .filter((entry) => showEmptyPoints.value || entry.points.length > 0)
})

const hiddenPointCount = computed(() => {
  const root = activeRoot.value
  if (!root) return 0
  return root.children.reduce((total, section) => (
    total + selectableChildren(section).filter((point) => point.weak === null).length
  ), 0)
})

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

// 叶子且有证据的行才提供群体错题入口；父级汇总行不重复展示。
function hasGroupEvidence(node: KnowledgeNode): boolean {
  return node.weak !== null
    && node.weak.evidence_count > 0
    && node.weak.hierarchy_kind !== 'parent_summary'
}
</script>

<template>
  <section class="knowledge-structure" aria-labelledby="training-structure-title">
    <header>
      <div>
        <p class="structure-eyebrow">{{ selectionKind === 'range' ? '先圈定章或小节，系统按每人细点掌握情况配题' : '完整结构，不替教师挑前三项' }}</p>
        <h3 id="training-structure-title">{{ title }}</h3>
        <p>{{ description }}</p>
      </div>
      <strong>{{ selectedCountLabel }}</strong>
    </header>

    <div v-if="tree.length" class="structure-layout">
      <nav aria-label="章节选择">
        <button
          v-for="root in tree"
          :key="root.key"
          type="button"
          :class="{ 'is-active': activeRoot?.key === root.key, 'is-checked': selectionKind === 'range' && modelValue.includes(root.key) }"
          :title="root.label"
          @click="activeRootKey = root.key"
        >
          <label v-if="selectionKind === 'range'" class="structure-range-check" @click.stop>
            <input type="checkbox" :checked="modelValue.includes(root.key)" @change="toggleTarget(root.key)">
            <span class="visually-hidden">将{{ knowledgeLeafLabel(root.label) }}加入训练范围</span>
          </label>
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
          <label class="structure-toggle">
            <input v-model="showEmptyPoints" type="checkbox">
            显示无证据知识点<template v-if="hiddenPointCount">（{{ hiddenPointCount }}）</template>
          </label>
        </div>

        <p v-if="!displaySections.length" class="structure-empty">当前章的知识点都没有证据；勾选上方“显示无证据知识点”可查看完整结构。</p>

        <article
          v-for="entry in displaySections"
          :key="entry.section.key"
          class="structure-section"
        >
          <button type="button" class="structure-section-heading" :class="{ 'has-range-check': selectionKind === 'range' }" :title="entry.section.label" @click="toggleSection(entry.section.key)">
            <span>{{ expandedSectionKeys.includes(entry.section.key) ? '−' : '+' }}</span>
            <label v-if="selectionKind === 'range'" class="structure-range-check" @click.stop>
              <input type="checkbox" :checked="modelValue.includes(entry.section.key)" @change="toggleTarget(entry.section.key)">
              <span class="visually-hidden">将{{ knowledgeLeafLabel(entry.section.label) }}加入训练范围</span>
            </label>
            <strong>{{ knowledgeLeafLabel(entry.section.label) }}</strong>
            <b>{{ masteryText(entry.section) }}</b>
          </button>
          <div v-if="expandedSectionKeys.includes(entry.section.key)" class="structure-points">
            <label
              v-for="point in entry.points"
              :key="point.key"
              :class="['structure-point', masteryClass(point)]"
              :title="point.label"
            >
              <input
                v-if="selectionKind === 'targets'"
                type="checkbox"
                :checked="modelValue.includes(point.key)"
                @change="toggleTarget(point.key)"
              >
              <span class="structure-point-name">{{ knowledgeLeafLabel(point.label) }}</span>
              <span class="structure-track" aria-hidden="true">
                <i v-if="point.weak" :style="{ width: `${Math.round(point.weak.mastery * 100)}%` }" />
                <b />
              </span>
              <strong>{{ masteryText(point) }}</strong>
              <small v-if="point.weak">{{ point.weak.evidence_count }} 条证据</small>
              <small v-else>暂无证据，不计入群体分母</small>
              <RouterLink
                v-if="hasGroupEvidence(point)"
                class="structure-point-evidence"
                :to="{ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: point.key, klabel: knowledgeLeafLabel(point.label), from: 'student' } }"
                @click.stop
              >证据</RouterLink>
            </label>
          </div>
        </article>

        <label v-if="!activeRoot.children.length" :class="['structure-point', masteryClass(activeRoot)]" :title="activeRoot.label">
          <input
            type="checkbox"
            :checked="modelValue.includes(activeRoot.key)"
            @change="toggleTarget(activeRoot.key)"
          >
          <span class="structure-point-name">{{ knowledgeLeafLabel(activeRoot.label) }}</span>
          <span class="structure-track" aria-hidden="true">
            <i v-if="activeRoot.weak" :style="{ width: `${Math.round(activeRoot.weak.mastery * 100)}%` }" />
            <b />
          </span>
          <strong>{{ masteryText(activeRoot) }}</strong>
          <small v-if="activeRoot.weak">{{ activeRoot.weak.evidence_count }} 条证据</small>
          <small v-else>暂无证据，不计入群体分母</small>
          <RouterLink
            v-if="hasGroupEvidence(activeRoot)"
            class="structure-point-evidence"
            :to="{ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: activeRoot.key, klabel: knowledgeLeafLabel(activeRoot.label), from: 'student' } }"
            @click.stop
          >证据</RouterLink>
        </label>
      </div>
    </div>
    <p v-else class="structure-empty">当前范围没有可展示的知识结构。</p>
  </section>
</template>

<style scoped>
.knowledge-structure { display: grid; gap: 1rem; margin: 1rem 0; padding: 1rem; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.knowledge-structure > header { display: flex; justify-content: space-between; gap: 1rem; align-items: start; }
.knowledge-structure h3, .knowledge-structure p { margin: .2rem 0; }
.knowledge-structure > header > strong { flex: 0 0 auto; padding: .4rem .65rem; border-radius: 999px; background: var(--color-accent-subtle); color: var(--color-accent); }
.structure-eyebrow { color: var(--color-text-secondary); font-size: .78rem; font-weight: 700; letter-spacing: .04em; }
.structure-layout { display: grid; grid-template-columns: minmax(180px, .42fr) minmax(0, 1.58fr); gap: 1rem; align-items: start; }
.structure-layout > nav { display: grid; gap: .45rem; position: sticky; top: calc(var(--shell-topbar-height, 64px) + 1rem); }
.structure-layout > nav button { display: flex; justify-content: space-between; align-items: center; gap: .75rem; padding: .65rem .75rem; border: 1px solid transparent; border-radius: var(--radius-control); background: var(--color-bg-subtle); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-layout > nav button.is-active { border-color: var(--color-accent); background: var(--color-accent-subtle); box-shadow: inset 3px 0 0 var(--color-accent); }
.structure-layout > nav button.is-checked { border-color: var(--color-accent); }
.structure-range-check { display: inline-flex; align-items: center; cursor: pointer; }
.visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.structure-layout > nav b { white-space: nowrap; }
.structure-detail { min-width: 0; display: grid; gap: .65rem; }
.structure-legend { display: flex; flex-wrap: wrap; gap: .5rem 1rem; color: var(--color-text-secondary); font-size: .78rem; }
.structure-legend span { display: inline-flex; align-items: center; gap: .3rem; }
.structure-legend i { width: .7rem; height: .7rem; border-radius: 3px; background: var(--color-danger); }
.structure-legend i.is-mid { background: var(--color-warning); }
.structure-legend i.is-good { background: var(--color-success); }
.structure-legend i.is-empty { border: 1px solid var(--color-border-default); background: repeating-linear-gradient(135deg, var(--color-border-subtle) 0 4px, var(--color-bg-surface) 4px 8px); }
.structure-toggle { display: inline-flex; align-items: center; gap: .3rem; margin-left: auto; color: var(--color-text-secondary); font-size: .78rem; cursor: pointer; }
.structure-section { overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); }
.structure-section-heading { width: 100%; display: grid; grid-template-columns: 1.2rem 1fr auto; gap: .55rem; padding: .7rem .8rem; border: 0; background: var(--color-bg-subtle); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.structure-section-heading.has-range-check { grid-template-columns: 1.2rem auto 1fr auto; }
.structure-points { display: grid; }
.structure-point { display: grid; grid-template-columns: auto minmax(140px, .72fr) minmax(160px, 1.4fr) 3.2rem 6.8rem auto; align-items: center; gap: .65rem; min-height: 2.9rem; padding: .45rem .75rem; border-top: 1px solid var(--color-border-default); cursor: pointer; }
.structure-point:hover { background: var(--color-bg-subtle); }
.structure-point-evidence { font-size: var(--font-size-caption); color: var(--color-accent-active); text-decoration: none; white-space: nowrap; }
.structure-point-evidence:hover { text-decoration: underline; }
.structure-track { position: relative; height: .62rem; overflow: hidden; border-radius: 999px; background: var(--color-bg-selected); }
.structure-track > i { display: block; height: 100%; background: var(--color-success); }
.structure-point.is-low .structure-track > i { background: var(--color-danger); }
.structure-point.is-mid .structure-track > i { background: var(--color-warning); }
.structure-point.is-empty .structure-track { background: repeating-linear-gradient(135deg, var(--color-border-subtle) 0 6px, var(--color-bg-subtle) 6px 12px); }
.structure-track > b { position: absolute; top: -2px; bottom: -2px; left: 70%; width: 2px; background: var(--color-text-muted); }
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
