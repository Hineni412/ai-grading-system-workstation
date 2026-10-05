<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { TrainingOverviewNode } from '../api/training'
import AppIconButton from '../components/design-system/AppIconButton.vue'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import OverviewScopeBar from '../components/knowledge-overview/OverviewScopeBar.vue'
import OverviewMapRows from '../components/knowledge-overview/OverviewMapRows.vue'
import OverviewTierBar from '../components/knowledge-overview/OverviewTierBar.vue'
import StudentTierChips from '../components/knowledge-overview/StudentTierChips.vue'
import { formatPercent, shortNodeName } from '../components/knowledge-overview/model'
import { WEAK_HEAT_LABELS, chapterMetrics, heatLevel, isItem, nodeLocation, overviewMetrics, relatedNodes } from '../components/knowledge-overview/metrics'
import { saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { presetFocusedTraining } from '../features/training/paper-selection-session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import '../styles/knowledge-overview.css'
import '../styles/knowledge-graph.css'
const route = useRoute(), router = useRouter()
const curriculum = useCurriculumScopeStore(), store = useMasteryOverviewStore()
const overview = computed(() => store.overview)
const metrics = computed(() => overview.value ? overviewMetrics(overview.value) : null)
const filters = [{ key: 'all', label: '全部' }, { key: 'skill', label: '只看技能' }, { key: 'topic', label: '只看知识点' }, { key: 'weak', label: '只看有学生明显薄弱' }]
const filter = computed(() => filters.some(f => f.key === route.query.filter) ? String(route.query.filter) : 'all')
const selectedKey = computed(() => typeof route.query.focus === 'string' ? route.query.focus : '')
const selected = computed(() => overview.value?.nodes.find(n => isItem(n) && n.knowledge_key === selectedKey.value) ?? null)
const hoveredKey = ref('')
const activeKey = computed(() => hoveredKey.value || selectedKey.value)
const activeKeys = computed(() => new Set([activeKey.value, ...(overview.value ? relatedNodes(overview.value, activeKey.value).map(r => r.node.knowledge_key) : [])]))
const related = computed(() => overview.value && selected.value ? relatedNodes(overview.value, selectedKey.value) : [])
const chapters = computed(() => chapterMetrics(overview.value?.nodes ?? []))
const examined = computed(() => chapters.value.filter(c => c.evidence > 0))
const unexamined = computed(() => chapters.value.filter(c => c.evidence === 0))
const legacy = computed(() => {
  const groups = new Map<string, TrainingOverviewNode[]>()
  for (const node of overview.value?.nodes ?? []) {
    if (node.in_volume !== false || !isItem(node)) continue
    const key = node.display_name.split(/[|｜]/).slice(0, 2).join('｜')
    groups.set(key, [...(groups.get(key) ?? []), node])
  }
  return [...groups].map(([label, nodes]) => ({ label, nodes }))
})
const mapRoot = ref<HTMLElement | null>(null), drawer = ref<HTMLElement | null>(null)
const lines = ref<Array<{ key: string; path: string; dashed: boolean }>>([])
let originalCell: HTMLElement | null = null
let observer: ResizeObserver | null = null
let frame: number | null = null
function visible(nodes: TrainingOverviewNode[]) {
  return nodes.filter(n => filter.value === 'all' || (filter.value === 'weak' ? n.distribution.weak > 0 : n.kind === filter.value))
}
function queryFor(focus = selectedKey.value, chosenFilter = filter.value) {
  return { ...(chosenFilter === 'all' ? {} : { filter: chosenFilter }), ...(focus ? { focus } : {}) }
}
function select(node: TrainingOverviewNode, event?: MouseEvent) {
  if (event?.currentTarget instanceof HTMLElement) originalCell = event.currentTarget
  else if (!selected.value) originalCell = findCell(node.knowledge_key)
  hoveredKey.value = ''
  void router.replace({ name: 'knowledge-graph', query: queryFor(node.knowledge_key) })
}
async function close() {
  if (!selectedKey.value) return
  hoveredKey.value = ''
  await router.replace({ name: 'knowledge-graph', query: queryFor('') })
  await nextTick()
  if (originalCell?.isConnected) originalCell.focus()
}
function onBlank(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.closest('button, summary, a, .mastery-map-drawer, .overview-scope-definition')) void close()
}
function onEscape(event: KeyboardEvent) { if (event.key === 'Escape') { event.preventDefault(); void close() } }
function findCell(key: string): HTMLElement | null {
  return [...(mapRoot.value?.querySelectorAll<HTMLElement>('.mastery-map-node') ?? [])].find(n => n.dataset.knowledge === key) ?? null
}
function drawConnections() {
  const root = mapRoot.value, source = findCell(selectedKey.value)
  if (!root || !source || !selected.value || window.innerWidth < 960 || !source.getClientRects().length) { lines.value = []; return }
  const bounds = root.getBoundingClientRect(), from = source.getBoundingClientRect()
  const column = source.closest('.mastery-map-column')?.getBoundingClientRect()
  const railX = column ? (selected.value.kind === 'topic' ? column.right + 8 : column.left - 8) - bounds.left : (from.right - bounds.left + 8)
  lines.value = related.value.flatMap(({ node, association }) => {
    const target = findCell(node.knowledge_key)
    if (!target?.getClientRects().length) return []
    const to = target.getBoundingClientRect()
    let path: string
    if (Math.abs(from.top - to.top) < 8) {
      const x1 = from.left + from.width / 2 - bounds.left, x2 = to.left + to.width / 2 - bounds.left
      const y = Math.min(from.top, to.top) - bounds.top, arc = y - 26
      path = `M ${x1} ${y} C ${x1} ${arc}, ${x2} ${arc}, ${x2} ${y}`
    } else {
      const x1 = from.left + from.width / 2 - bounds.left, x2 = to.left + to.width / 2 - bounds.left
      const y1 = from.top - bounds.top, y2 = to.top - bounds.top
      // Travel in the gaps above each wrapped row, then along the column gutter.
      // A side exit would cross a neighbouring cell when that column wraps.
      path = `M ${x1} ${y1} L ${x1} ${y1 - 4} L ${railX} ${y1 - 4} L ${railX} ${y2 - 4} L ${x2} ${y2 - 4} L ${x2} ${y2}`
    }
    return [{ key: node.knowledge_key, path, dashed: association.basis === 'question_cooccurrence' }]
  })
}
function scheduleLines() {
  if (frame !== null) cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => { frame = null; drawConnections() })
}
function train(node: TrainingOverviewNode) {
  const student_ids = node.students.filter(s => s.tier === 'weak').map(s => s.student_id)
  if (!student_ids.length || node.kind !== 'skill') return
  saveEvidenceScope(semesterEvidenceQuery({ mode: 'selected', student_ids }, curriculum.selectedVolumeId))
  presetFocusedTraining({ targetKeys: [node.knowledge_key], rangeKeys: node.section_key ? [node.section_key] : [] })
  void router.push({ name: 'training', query: { mode: 'student' } })
}
function questions(node: TrainingOverviewNode) {
  void router.push({ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: node.knowledge_key, klabel: node.display_name, from: 'overview' } })
}
watch([selected, filter, overview], async ([node], [previous]) => {
  await nextTick()
  if (node && node !== previous) drawer.value?.focus()
  scheduleLines()
  if (mapRoot.value) observer?.observe(mapRoot.value)
})
onMounted(() => {
  void router.replace({ name: 'knowledge-graph', query: queryFor() })
  observer = new ResizeObserver(scheduleLines)
  if (mapRoot.value) observer.observe(mapRoot.value)
  window.addEventListener('resize', scheduleLines)
  window.addEventListener('scroll', scheduleLines, true)
  document.addEventListener('keydown', onEscape)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame !== null) cancelAnimationFrame(frame)
  window.removeEventListener('resize', scheduleLines)
  window.removeEventListener('scroll', scheduleLines, true)
  document.removeEventListener('keydown', onEscape)
})
</script>
<template>
  <section class="knowledge-graph-view knowledge-overview-view knowledge-training-page" aria-labelledby="knowledge-graph-title" @click="onBlank">
    <PageHeader title="知识结构" title-id="knowledge-graph-title" class="knowledge-training-header">
      <template #navigation><KnowledgeTrainingTabs /></template>
    </PageHeader>
    <OverviewScopeBar />
    <p v-if="metrics" class="mastery-map-summary" aria-label="知识结构汇总">本册 {{ metrics.total }} 项 · 有证据 {{ metrics.evidence }} 项 · 有学生明显薄弱 {{ metrics.weak }} 项<span>与学情总览同一口径</span></p>
    <StatePanel v-if="!curriculum.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
    <StatePanel v-else-if="store.loadState === 'loading' && !overview" kind="loading" title="正在汇总本学期掌握度…" />
    <StatePanel
      v-else-if="store.loadState === 'error'"
      kind="error"
      :title="store.errorMessage || '知识结构暂时无法读取'"
      retry-label="重新加载"
      @retry="store.load(curriculum.selectedVolumeId, true)"
    />
    <template v-if="overview">
      <FeedbackBanner
        v-if="store.loadState === 'stale-error'"
        tone="warning"
        title="当前显示上次成功读取的结果，最新内容暂时无法确认。"
        description=""
        action-label="重新加载"
        @action="store.load(curriculum.selectedVolumeId, true)"
      />
      <div class="mastery-map-toolbar">
        <div class="app-segmented" role="group" aria-label="地图筛选"><button v-for="option in filters" :key="option.key" type="button" :aria-pressed="filter === option.key" :class="{ 'is-active': filter === option.key }" @click="router.replace({ name: 'knowledge-graph', query: queryFor(selectedKey, option.key) })">{{ option.label }}</button></div>
        <div class="mastery-map-legend" aria-label="热度图例"><span v-for="(label, index) in WEAK_HEAT_LABELS" :key="label"><i :class="`heat-${index}`" />{{ label }}</span><span><i class="heat--1" />无证据</span></div>
        <p>底色越深＝明显薄弱的学生占比越高（分母：有证据学生）；底部细条＝四档人数分布。</p>
        <div class="mastery-tier-legend"><span class="is-weak">明显薄弱</span><span class="is-unsteady">还不稳</span><span class="is-stable">较稳定</span><span class="is-insufficient">证据不足</span><span>实线：同一小问 · 虚线：仅同题出现</span></div>
      </div>
      <div class="mastery-map-layout" :class="{ 'has-drawer': selected }">
        <div ref="mapRoot" class="mastery-map-root" @toggle.capture="scheduleLines">
          <svg v-if="lines.length" class="mastery-map-connections" aria-hidden="true"><path v-for="line in lines" :key="line.key" :d="line.path" :class="{ 'is-dashed': line.dashed }" /></svg>
          <section v-for="row in examined" :key="row.chapter.knowledge_key" class="mastery-map-chapter">
            <header><h2>{{ shortNodeName(row.chapter) }}</h2><span>群体掌握度 {{ formatPercent(row.chapter.group_mastery) }} · 有证据 {{ row.evidence }}/{{ row.items.length }} 项</span></header>
            <OverviewMapRows :nodes="visible(row.items)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" @select="select" @hover="hoveredKey = $event" />
            <StatePanel v-if="!visible(row.items).length" kind="empty" compact title="当前筛选下没有项目。" />
          </section>
          <details v-if="unexamined.length" class="mastery-map-fold"><summary>尚未考查的 {{ unexamined.length }} 章</summary>
            <section v-for="row in unexamined" :key="row.chapter.knowledge_key" class="mastery-map-chapter"><header><h2>{{ shortNodeName(row.chapter) }}</h2><span>有证据 0/{{ row.items.length }} 项</span></header>
              <OverviewMapRows :nodes="visible(row.items)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" @select="select" @hover="hoveredKey = $event" />
            </section>
          </details>
          <details v-if="legacy.length" class="mastery-map-fold mastery-map-legacy"><summary>往届内容（本学期考试涉及 · {{ legacy.reduce((sum, g) => sum + g.nodes.length, 0) }} 项）</summary>
            <p>往届内容不计入本册数字。</p>
            <section v-for="group in legacy" :key="group.label" class="mastery-map-chapter"><header><h2>{{ group.label }}</h2></header>
              <OverviewMapRows :nodes="visible(group.nodes)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" @select="select" @hover="hoveredKey = $event" />
            </section>
          </details>
        </div>
        <aside v-if="selected" ref="drawer" class="mastery-map-drawer fx-drawer-right" tabindex="-1" role="region" :aria-label="`${shortNodeName(selected)}详情`">
          <header><div><span class="mastery-map-type">{{ selected.kind === 'skill' ? '技能' : '知识点' }}</span><h2>{{ shortNodeName(selected) }}</h2></div><AppIconButton label="关闭详情" @click="close" icon="close" /></header>
          <p class="overview-location">属于 {{ nodeLocation(selected, overview.nodes) }}</p>
          <p v-if="selected.definition" class="mastery-map-definition">{{ selected.definition }}</p>
          <div class="mastery-map-mastery"><span>群体掌握度</span><strong>{{ formatPercent(selected.group_mastery) }}</strong><small>80% 群体区间 {{ formatPercent(selected.group_interval_low) }}–{{ formatPercent(selected.group_interval_high) }}</small></div>
          <OverviewTierBar :distribution="selected.distribution" show-counts /><p class="overview-location">有证据 {{ selected.evidence_student_count }} 人</p>
          <StudentTierChips :node="selected" :students="overview.students" />
          <section class="mastery-map-related"><h3>{{ selected.kind === 'skill' ? '相关知识点' : '相关技能' }}</h3>
            <StatePanel v-if="!related.length" kind="empty" compact title="题库暂无关联。" />
            <button v-for="item in related" :key="item.node.knowledge_key" type="button" @click="select(item.node)">
              <i :class="`heat-${heatLevel(item.node)}`" /><span><strong>{{ shortNodeName(item.node) }}</strong><small>明显薄弱 {{ item.node.distribution.weak }} 人 · {{ item.association.basis === 'same_part' ? `同一小问 ${item.association.same_part_question_count} 题` : `仅同题出现 ${item.association.question_count} 题（虚线）` }}</small></span>
            </button>
            <p>关联来自题库里同时考查两者的题目，不代表两者掌握度相同。</p>
          </section>
          <div class="mastery-map-drawer-actions"><AppButton v-if="selected.kind === 'skill' && selected.distribution.weak > 0" variant="primary" @click="train(selected)">给明显薄弱的 {{ selected.distribution.weak }} 人出训练卷</AppButton><AppButton @click="questions(selected)">看错题</AppButton></div>
        </aside>
      </div>
      <details v-if="overview.warnings.length" class="overview-data-notes"><summary>数据说明（{{ overview.warnings.length }}）</summary><ul><li v-for="warning in overview.warnings" :key="warning">{{ warning }}</li></ul></details>
    </template>
  </section>
</template>
