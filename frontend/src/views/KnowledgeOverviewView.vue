<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { assemblyApi, type AssemblyQuestion } from '../api/assembly'
import {
  trainingApi,
  type TrainingKeyStepTypeStatsResponse,
  type TrainingOverviewNode,
  type TrainingOverviewRequest,
  type TrainingOverviewStudent,
} from '../api/training'
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
import { compareFocusNodes, formatPercent, masteryDetail, shortNodeName } from '../components/knowledge-overview/model'
import { RATE_BANDS, chapterMetrics, nodeMastery, rateBand, isItem, itemWeakCount, nodeLocation, relatedNodes, scoreOpportunities, volumeItems } from '../components/knowledge-overview/metrics'
import { saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { presetFocusedTraining } from '../features/training/paper-selection-session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import '../styles/knowledge-overview.css'
import '../styles/knowledge-graph.css'
const route = useRoute(), router = useRouter()
const QuestionContentRenderer = defineAsyncComponent(() => import('../components/question-bank/QuestionContentRenderer.vue'))
const curriculum = useCurriculumScopeStore(), store = useMasteryOverviewStore()
const overview = computed(() => store.overview)
// Typed releases present question types instead of the topic/skill columns.
const typeMode = computed(() => overview.value?.target_kind === 'type')
const arrangement = computed(() => route.query.view === 'chapters' ? 'chapters' : 'weak')
const showAllItems = ref(false)
const targets = computed(() => volumeItems(overview.value?.nodes ?? [])
  .filter(node => node.target_kind === 'knowledge' ? node.kind === 'topic'
    : node.kind === (node.target_kind === 'type' || typeMode.value ? 'type' : 'skill')))
const ranked = computed(() => visible(targets.value).sort(compareFocusNodes))
const priorities = computed(() => ranked.value.filter(node => node.distribution.weak > 0).slice(0, 5))
const priorityRanks = computed(() => new Map(priorities.value.map((node, index) => [node.knowledge_key, index + 1])))
const listed = computed(() => showAllItems.value ? ranked.value : ranked.value.slice(0, 10))
const opportunities = computed(() => scoreOpportunities(overview.value?.students ?? [], targets.value))
const studentSearch = ref('')
const studentMatches = computed(() => {
  const needle = studentSearch.value.trim().toLowerCase()
  if (!needle) return []
  return (overview.value?.students ?? [])
    .filter(student => student.student_name.toLowerCase().includes(needle)
      || student.student_code.toLowerCase().includes(needle))
    .sort((a, b) => a.student_code.localeCompare(b.student_code, 'zh'))
})
const filters = computed(() => typeMode.value
  ? [{ key: 'all', label: '全部' }, { key: 'weak', label: '只看有学生明显薄弱' }]
  : [{ key: 'all', label: '全部' }, { key: 'type', label: '只看题型' }, { key: 'skill', label: '只看技能' }, { key: 'topic', label: '只看知识点' }, { key: 'weak', label: '只看有学生明显薄弱' }])
const filter = computed(() => filters.value.some(f => f.key === route.query.filter) ? String(route.query.filter) : 'all')
const selectedKey = computed(() => typeof route.query.focus === 'string' ? route.query.focus : '')
const selected = computed(() => overview.value?.nodes.find(n => isItem(n) && n.knowledge_key === selectedKey.value) ?? null)
const hoveredKey = ref('')
const activeKey = computed(() => hoveredKey.value || selectedKey.value)
const activeKeys = computed(() => new Set([activeKey.value, ...(typeMode.value ? [] : (overview.value ? relatedNodes(overview.value, activeKey.value).map(r => r.node.knowledge_key) : []))]))
const related = computed(() => !typeMode.value && overview.value && selected.value ? relatedNodes(overview.value, selectedKey.value) : [])
const keyStepStats = ref<TrainingKeyStepTypeStatsResponse | null>(null)
const keyStepStatsLoading = ref(false)
const definitionOpen = ref(false)
const definitionClamped = ref(false)
const keyStepsAll = ref(false)
const openKeyStep = ref('')
const typicalOpen = ref(false)
const openOtherGroups = ref<Set<string>>(new Set())
let keyStepStatsScope = ''
let keyStepController: AbortController | null = null

function overviewRequestBody(): TrainingOverviewRequest | null {
  if (!curriculum.selectedVolumeId) return null
  const scope = store.scopeSelection === 'all'
    ? { mode: 'all' as const }
    : { mode: 'class' as const, class_id: store.scopeSelection, class_ids: [store.scopeSelection] }
  const query = semesterEvidenceQuery(scope, curriculum.selectedVolumeId)
  return {
    scope: { student_ids: [], ...query.scope },
    exam_scope: { session_ids: [], ...query.exam_scope },
  }
}

async function loadKeyStepStats(): Promise<void> {
  const body = overviewRequestBody()
  if (!body) return
  const scopeKey = JSON.stringify(body)
  if (keyStepStatsScope === scopeKey && keyStepStats.value) return
  keyStepController?.abort()
  const active = new AbortController()
  keyStepController = active
  keyStepStatsLoading.value = true
  try {
    const result = await trainingApi.keyStepTypeStats(body, active.signal)
    if (active.signal.aborted) return
    keyStepStats.value = result
    keyStepStatsScope = scopeKey
  } catch {
    if (!active.signal.aborted) keyStepStats.value = null
  } finally {
    if (keyStepController === active) keyStepStatsLoading.value = false
  }
}

const typeKeySteps = computed(() => {
  const rows = selected.value?.kind === 'type'
    ? keyStepStats.value?.types[selected.value.knowledge_key] ?? []
    : []
  return rows.filter(step => step.observed > 0).slice().sort((a, b) => b.rate - a.rate)
})
const visibleTypeKeySteps = computed(() => keyStepsAll.value ? typeKeySteps.value : typeKeySteps.value.slice(0, 5))

function openKeyStepStudent(studentId: string, key: string): void {
  void router.push({ name: 'student-evidence', params: { studentId }, query: { keyStep: key } })
}

function sessionShortName(name: string): string {
  return name.replace(/学情反馈$/, '') || name
}

function questionParent(label: string): string {
  return label.replace(/[（(].*$/, '').replace(/^Q/i, '')
}

function measureDefinitionClamp(): void {
  if (definitionOpen.value) return
  const element = drawer.value?.querySelector<HTMLElement>('.mastery-map-definition')
  definitionClamped.value = Boolean(element && element.scrollHeight > element.clientHeight + 1)
}

function toggleDefinition(): void {
  definitionOpen.value = !definitionOpen.value
  if (!definitionOpen.value) void nextTick(measureDefinitionClamp)
}

const otherQuestionGroups = computed(() => {
  const items = selected.value?.other_questions ?? []
  const groups = new Map<string, { session: string; parent: string; items: typeof items; min: number | null }>()
  for (const item of items) {
    const parent = questionParent(item.question_label)
    const key = `${item.session_name}|${parent}`
    const group = groups.get(key) ?? { session: item.session_name, parent, items: [], min: null as number | null }
    group.items.push(item)
    if (item.class_rate !== null && (group.min === null || item.class_rate < group.min)) group.min = item.class_rate
    groups.set(key, group)
  }
  return [...groups.entries()].map(([key, group]) => {
    const rates = group.items.map(item => item.class_rate).filter((rate): rate is number => rate !== null).sort((a, b) => a - b)
    const low = rates[0]
    const high = rates[rates.length - 1]
    return {
      key,
      session: sessionShortName(group.session),
      parent: group.parent,
      items: group.items,
      min: group.min ?? 0,
      range: low === undefined ? '暂无' : low === high ? formatPercent(low) : `${formatPercent(low)}–${formatPercent(high)}`,
    }
  }).sort((a, b) => a.min - b.min)
})

function toggleOtherGroup(key: string): void {
  const next = new Set(openOtherGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openOtherGroups.value = next
}

watch([selected, () => curriculum.selectedVolumeId, () => store.scopeSelection], ([node]) => {
  definitionOpen.value = false
  definitionClamped.value = false
  void nextTick(measureDefinitionClamp)
  keyStepsAll.value = false
  openKeyStep.value = ''
  typicalOpen.value = false
  openOtherGroups.value = new Set()
  if (node?.kind === 'type') void loadKeyStepStats()
})
const hiddenTypeCount = computed(() => typeMode.value
  ? (overview.value?.nodes ?? []).filter(n => n.kind === 'type' && n.in_volume !== false && n.evidence_student_count === 0).length
  : 0)
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
  return nodes.filter(n => {
    if (typeMode.value && n.kind === 'type' && n.evidence_student_count === 0) return false
    return filter.value === 'all' || (filter.value === 'weak' ? n.distribution.weak > 0 : n.kind === filter.value)
  })
}
function queryFor(focus = selectedKey.value, chosenFilter = filter.value) {
  return { ...(arrangement.value === 'chapters' ? { view: 'chapters' } : {}),
    ...(chosenFilter === 'all' ? {} : { filter: chosenFilter }), ...(focus ? { focus } : {}) }
}
function select(node: TrainingOverviewNode, event?: MouseEvent) {
  if (event?.currentTarget instanceof HTMLElement) originalCell = event.currentTarget
  else if (!selected.value) originalCell = findCell(node.knowledge_key)
  hoveredKey.value = ''
  void router.replace({ name: 'knowledge-overview', query: queryFor(node.knowledge_key) })
}
async function close() {
  if (!selectedKey.value) return
  hoveredKey.value = ''
  await router.replace({ name: 'knowledge-overview', query: queryFor('') })
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
  frame = requestAnimationFrame(() => { frame = null; drawConnections(); measureDefinitionClamp() })
}
function train(node: TrainingOverviewNode) {
  const student_ids = node.students.filter(s => s.tier === 'weak').map(s => s.student_id)
  if (!student_ids.length || (node.kind !== 'skill' && node.kind !== 'type' && node.target_kind !== 'knowledge')) return
  saveEvidenceScope(semesterEvidenceQuery({ mode: 'selected', student_ids }, curriculum.selectedVolumeId))
  presetFocusedTraining({ targetKeys: [node.knowledge_key], rangeKeys: node.section_key ? [node.section_key] : [] })
  void router.push({ name: 'training', query: { mode: 'setup' } })
}
function questions(node: TrainingOverviewNode) {
  void router.push({ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: node.knowledge_key, klabel: node.display_name, from: 'overview' } })
}
function scoreRateText(student: TrainingOverviewStudent) {
  return student.score_rate === null ? '无成绩' : formatPercent(student.score_rate)
}
function openStudent(student: TrainingOverviewStudent) {
  void router.push({ name: 'student-evidence', params: { studentId: student.student_id }, query: { from: 'overview' } })
}
const typicalQuestion = ref<AssemblyQuestion | null>(null)
let typicalRequest = 0
watch(() => selected.value?.typical_question?.bank_question_id, async (id) => {
  const ticket = ++typicalRequest
  typicalQuestion.value = null
  if (!id) return
  try {
    const result = await assemblyApi.resolveQuestions([id])
    if (ticket === typicalRequest) typicalQuestion.value = result.items.find(q => q.id === id) ?? null
  } catch { /* The detail panel still works when the preview cannot load. */ }
}, { immediate: true })
watch([selected, filter, overview], async ([node], [previous]) => {
  await nextTick()
  if (node && node !== previous) drawer.value?.focus()
  scheduleLines()
  if (mapRoot.value) observer?.observe(mapRoot.value)
})
onMounted(() => {
  void router.replace({ name: 'knowledge-overview', query: queryFor() })
  observer = new ResizeObserver(scheduleLines)
  if (mapRoot.value) observer.observe(mapRoot.value)
  window.addEventListener('resize', scheduleLines)
  window.addEventListener('scroll', scheduleLines, true)
  document.addEventListener('keydown', onEscape)
})
onBeforeUnmount(() => {
  keyStepController?.abort()
  observer?.disconnect()
  if (frame !== null) cancelAnimationFrame(frame)
  window.removeEventListener('resize', scheduleLines)
  window.removeEventListener('scroll', scheduleLines, true)
  document.removeEventListener('keydown', onEscape)
})
</script>
<template>
  <section class="knowledge-graph-view knowledge-overview-view knowledge-training-page" aria-labelledby="knowledge-graph-title" @click="onBlank">
    <PageHeader title="学情" title-id="knowledge-graph-title" class="knowledge-training-header">
      <template #navigation><KnowledgeTrainingTabs /></template>
    </PageHeader>
    <OverviewScopeBar />

    <StatePanel v-if="!curriculum.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
    <StatePanel v-else-if="store.loadState === 'loading' && !overview" kind="loading" title="正在汇总本学期掌握度…" />
    <StatePanel
      v-else-if="store.loadState === 'error'"
      kind="error"
      :title="store.errorMessage || '学情暂时无法读取'"
      retry-label="重新加载"
      @retry="store.load(curriculum.selectedVolumeId, true)"
    />
    <template v-if="overview">
      <FeedbackBanner
        v-if="store.loadState === 'stale-error'"
        tone="warning"
        title="当前显示上次成功读取的结果，最新内容暂时无法确认。"
       
        action-label="重新加载"
        @action="store.load(curriculum.selectedVolumeId, true)"
      />
      <FeedbackBanner
        v-if="store.refreshing || store.refreshMessage"
        tone="info"
        :title="store.refreshMessage || '数据有更新，正在重新计算。当前显示上次结果，算完自动刷新。'"
      />
      <div class="mastery-map-toolbar">
        <div class="app-segmented" role="group" aria-label="题型清单排列">
          <button type="button" :aria-pressed="arrangement === 'weak'" :class="{ 'is-active': arrangement === 'weak' }" @click="router.replace({ query: { ...queryFor(), view: 'weak' } })">薄弱优先</button>
          <button type="button" :aria-pressed="arrangement === 'chapters'" :class="{ 'is-active': arrangement === 'chapters' }" @click="router.replace({ query: { ...queryFor(), view: 'chapters' } })">按教材章节</button>
        </div>
        <div class="app-segmented" role="group" aria-label="地图筛选"><button v-for="option in filters" :key="option.key" type="button" :aria-pressed="filter === option.key" :class="{ 'is-active': filter === option.key }" @click="router.replace({ name: 'knowledge-overview', query: queryFor(selectedKey, option.key) })">{{ option.label }}</button></div>
        <details class="overview-reading-key">
          <summary>配色说明</summary>
          <div class="mastery-map-legend" aria-label="掌握度与得分率数值配色"><span v-for="band in RATE_BANDS" :key="band.key"><i :class="`rate-${band.key}`" />{{ band.label }}</span><span><i class="rate-none" />暂无数据</span></div>
          <p>题型数字：群体掌握度。学生数字：考试得分率。数值配色不改变掌握档位。</p>
          <p v-if="!typeMode">实线：同一小问。虚线：仅同题出现。</p>
        </details>
        <div class="mastery-tier-legend overview-distribution-key"><span>人数分布</span><span class="is-weak">明显薄弱</span><span class="is-unsteady">还不稳</span><span class="is-stable">较稳定</span><span class="is-insufficient">证据不足</span></div>
      </div>
      <p v-if="typeMode && hiddenTypeCount" class="mastery-map-hidden-note">尚未考查的 {{ hiddenTypeCount }} 个题型已隐藏</p>
      <div class="mastery-map-layout has-drawer">
        <section v-if="arrangement === 'weak'" class="overview-card overview-priorities" aria-label="薄弱优先题型清单">
          <header class="overview-card__header"><h2>题型清单</h2><span>明显薄弱人数、还不稳人数优先</span></header>
          <StatePanel v-if="!listed.length" kind="empty" compact title="当前范围没有可显示的训练目标。" />
          <article v-for="(node, index) in listed" :key="node.knowledge_key" class="overview-priority" :class="{ 'is-compact': index >= 5, 'is-selected': selectedKey === node.knowledge_key }" :data-knowledge="node.knowledge_key">
            <button type="button" class="overview-priority-select" :aria-pressed="selectedKey === node.knowledge_key" @click="select(node, $event)">
              <span class="overview-rank">{{ priorityRanks.get(node.knowledge_key) ?? '·' }}</span>
              <span><strong>{{ shortNodeName(node) }}</strong><small>{{ nodeLocation(node, overview.nodes) }}</small></span>
              <span class="overview-priority-mastery"><span class="overview-mastery-value" :class="`rate-${rateBand(nodeMastery(node))}`">掌握度 {{ formatPercent(nodeMastery(node)) }}</span><OverviewTierBar :distribution="node.distribution" /></span>
              <span class="overview-weak-count">{{ node.distribution.weak }} 人明显薄弱</span>
            </button>
            <details v-if="priorityRanks.has(node.knowledge_key)" class="overview-student-details">
              <summary>查看明显薄弱学生</summary>
              <p>学生数字：考试得分率</p>
              <StudentTierChips :node="node" :students="overview.students" weak-only :limit="8" />
            </details>
          </article>
          <AppButton v-if="ranked.length > 10" variant="ghost" @click="showAllItems = !showAllItems">{{ showAllItems ? '收起清单' : '显示全部 ' + ranked.length + ' 项' }}</AppButton>
        </section>
        <div v-else ref="mapRoot" class="mastery-map-root" @toggle.capture="scheduleLines">
          <svg v-if="lines.length" class="mastery-map-connections" aria-hidden="true"><path v-for="line in lines" :key="line.key" :d="line.path" :class="{ 'is-dashed': line.dashed }" /></svg>
          <section v-for="row in examined" :key="row.chapter.knowledge_key" class="mastery-map-chapter">
            <header><h2>{{ shortNodeName(row.chapter) }}</h2><span>群体掌握度 {{ formatPercent(row.chapter.group_mastery) }} · 有证据 {{ row.evidence }}/{{ row.items.length }} 项</span></header>
            <OverviewMapRows :nodes="visible(row.items)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" :priority-ranks="priorityRanks" @select="select" @hover="hoveredKey = $event" />
            <StatePanel v-if="!visible(row.items).length" kind="empty" compact title="当前筛选下没有项目。" />
          </section>
          <details v-if="unexamined.length" class="mastery-map-fold"><summary>尚未考查的 {{ unexamined.length }} 章</summary>
            <section v-for="row in unexamined" :key="row.chapter.knowledge_key" class="mastery-map-chapter"><header><h2>{{ shortNodeName(row.chapter) }}</h2><span>有证据 0/{{ row.items.length }} 项</span></header>
              <OverviewMapRows :nodes="visible(row.items)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" :priority-ranks="priorityRanks" @select="select" @hover="hoveredKey = $event" />
            </section>
          </details>
          <details v-if="legacy.length" class="mastery-map-fold mastery-map-legacy"><summary>往届内容（本学期考试涉及 · {{ legacy.reduce((sum, g) => sum + g.nodes.length, 0) }} 项）</summary>
            <p>往届内容不计入本册数字。</p>
            <section v-for="group in legacy" :key="group.label" class="mastery-map-chapter"><header><h2>{{ group.label }}</h2></header>
              <OverviewMapRows :nodes="visible(group.nodes)" :catalog="overview.nodes" :selected-key="selectedKey" :active-keys="activeKeys" :has-active="!!activeKey" :priority-ranks="priorityRanks" @select="select" @hover="hoveredKey = $event" />
            </section>
          </details>
        </div>
        <aside v-if="!selected" class="overview-card overview-opportunity" aria-label="提分机会">
          <header class="overview-card__header"><h2>提分机会</h2><span>考试得分率 40% 及以上、有明显薄弱项 · {{ opportunities.length }} 人，得分率从高到低</span></header>
          <input v-model="studentSearch" class="app-input" type="search" placeholder="找学生：姓名或学号" aria-label="找学生" />
          <template v-if="studentSearch.trim()">
            <StatePanel v-if="!studentMatches.length" kind="empty" compact title="没有匹配的学生。" />
            <button v-for="student in studentMatches" :key="student.student_id" type="button" class="overview-opportunity-student" @click="openStudent(student)">
              <span class="overview-opportunity-heading"><strong>{{ student.student_name }}</strong><span class="overview-score-rate" :class="`rate-${rateBand(student.score_rate)}`">{{ scoreRateText(student) }}</span><b>明显薄弱 {{ itemWeakCount(student) }} 项</b></span>
              <span class="overview-location">{{ student.class_id || '未分班' }} · {{ student.student_code }}</span>
            </button>
          </template>
          <template v-else>
            <StatePanel v-if="!opportunities.length" kind="empty" compact title="当前范围没有得分率 40% 及以上且有明显薄弱项的学生。" />
            <article v-for="opportunity in opportunities" :key="opportunity.student.student_id" class="overview-opportunity-row">
              <button type="button" class="overview-opportunity-name" @click="openStudent(opportunity.student)">
                <span class="overview-opportunity-heading"><strong>{{ opportunity.student.student_name }}</strong><b class="overview-score-rate" :class="`rate-${rateBand(opportunity.student.score_rate)}`">{{ scoreRateText(opportunity.student) }}</b></span>
                <span class="overview-location">{{ opportunity.student.class_id || '未分班' }} · {{ opportunity.student.student_code }}</span>
              </button>
              <div class="overview-opportunity-items">
                <button v-for="item in opportunity.items" :key="item.node.knowledge_key" type="button" class="overview-opportunity-item"
                  :title="masteryDetail({ mastery: item.mastery, tier: 'weak' })" @click.stop="select(item.node, $event)">
                  {{ shortNodeName(item.node) }}
                </button>
              </div>
            </article>
          </template>
        </aside>
        <aside v-else ref="drawer" class="mastery-map-drawer fx-drawer-right" tabindex="-1" role="region" :aria-label="`${shortNodeName(selected)}详情`">
          <header class="mastery-map-drawer__top"><div><span class="mastery-map-type">{{ selected.kind === 'skill' ? '技能' : selected.kind === 'type' ? '题型' : '知识点' }}</span><h2>{{ shortNodeName(selected) }}</h2><p class="overview-location">属于 {{ nodeLocation(selected, overview.nodes) }}</p></div><AppIconButton label="关闭详情" @click="close" icon="close" /></header>
          <template v-if="selected.definition">
            <p class="mastery-map-definition" :class="{ 'is-clamped': !definitionOpen }">{{ selected.definition }}</p>
            <button v-if="definitionOpen || definitionClamped" type="button" class="drawer-inline-toggle" @click="toggleDefinition">{{ definitionOpen ? '收起' : '展开' }}</button>
          </template>
          <div class="mastery-map-overview">
            <div class="mastery-map-mastery"><span>群体掌握度</span><strong>{{ formatPercent(selected.group_mastery) }}</strong><small>80% 群体区间 {{ formatPercent(selected.group_interval_low) }}–{{ formatPercent(selected.group_interval_high) }}</small></div>
            <p class="overview-location">有证据 {{ selected.evidence_student_count }} 人</p>
          </div>
          <div class="mastery-map-tier-strip"><OverviewTierBar :distribution="selected.distribution" show-counts /></div>
          <div class="mastery-map-drawer-actions mastery-map-drawer-actions--top"><AppButton v-if="(selected.kind === 'skill' || selected.kind === 'type' || selected.target_kind === 'knowledge') && selected.distribution.weak > 0" variant="primary" @click="train(selected)">给明显薄弱的 {{ selected.distribution.weak }} 人出训练卷</AppButton><AppButton @click="questions(selected)">看错题</AppButton></div>
          <section v-if="selected.kind === 'type' && typeKeySteps.length" class="drawer-key-steps" data-testid="drawer-key-steps">
            <h3>卡在哪一步</h3>
            <div v-for="step in visibleTypeKeySteps" :key="step.key" class="drawer-key-step">
              <button type="button" class="drawer-key-step__head" :aria-expanded="openKeyStep === step.key" :title="`独立失分 ${step.independent} 人 · 有记录 ${step.observed} 人，点击展开反复卡住的学生与出现位置`" @click="openKeyStep = openKeyStep === step.key ? '' : step.key">
                <span class="drawer-key-step__name">{{ step.name }}</span>
                <span class="drawer-key-step__bar" aria-hidden="true"><i :style="{ width: formatPercent(step.rate) }"></i></span>
                <span class="drawer-key-step__count">{{ step.independent }}/{{ step.observed }} 人</span>
                <span v-if="step.common" class="drawer-key-step__common" title="同一关键步骤在 ≥2 个题型中独立失分率 ≥30%：该步骤是跨题型的共性卡点，不只在这一类题上卡住学生">共性</span>
              </button>
              <div v-if="openKeyStep === step.key" class="drawer-key-step__detail">
                <template v-if="step.repeated_students.length">
                  <p class="drawer-key-step__label">其中反复卡住</p>
                  <div class="student-tier-chips drawer-key-step__chips">
                    <button v-for="student in step.repeated_students" :key="student.student_id" type="button" class="student-tier-chip" :title="`打开 ${student.student_name} 的作答证据并定位到此关键步骤`" @click="openKeyStepStudent(student.student_id, step.key)">{{ student.student_name }}</button>
                  </div>
                </template>
                <p v-else class="drawer-key-step__label drawer-key-step__label--muted">本关键步骤暂无反复卡住的学生（需同一学生 ≥2 道小问独立失分）。</p>
                <template v-if="step.questions.length">
                  <p class="drawer-key-step__label">出现在</p>
                  <ul class="drawer-key-step__questions"><li v-for="question in step.questions" :key="`${question.session_id}|${question.parent_label}`">{{ sessionShortName(question.session_name) }} 第{{ questionParent(question.parent_label) }}题 · {{ question.sub_count }} 个小问</li></ul>
                </template>
              </div>
            </div>
            <button v-if="typeKeySteps.length > 5" type="button" class="drawer-inline-toggle" @click="keyStepsAll = !keyStepsAll">{{ keyStepsAll ? '收起' : `展开全部（${typeKeySteps.length}）` }}</button>
          </section>
          <p v-else-if="selected.kind === 'type' && keyStepStatsLoading" class="overview-location">正在汇总关键步骤…</p>
          <p v-if="selected.kind === 'type'" class="drawer-section-label">明显薄弱学生</p>
          <StudentTierChips :node="selected" :students="overview.students" :weak-only="selected.kind === 'type'" />
          <section v-if="selected.kind === 'type' && selected.typical_question" class="mastery-map-typical">
            <p class="mastery-map-typical-lead">典型题：本班考过的这类题中得分率最低的一道 · {{ sessionShortName(selected.typical_question.session_name) }} 第{{ questionParent(selected.typical_question.question_label) }}题 · 本班得分率 {{ formatPercent(selected.typical_question.class_rate) }}</p>
            <div class="mastery-map-typical-stem" :class="{ 'is-clamped': !typicalOpen }"><QuestionContentRenderer v-if="typicalQuestion" :blocks="typicalQuestion.rich_content?.question_blocks" :fallback="typicalQuestion.question_text" media-mode="detail" typeset-text /></div>
            <button type="button" class="drawer-inline-toggle" data-testid="typical-toggle" @click="typicalOpen = !typicalOpen">{{ typicalOpen ? '收起' : '展开全文' }}</button>
            <template v-if="otherQuestionGroups.length"><p class="mastery-map-typical-lead">本班还考过这类题：</p>
              <div v-for="group in otherQuestionGroups" :key="group.key" class="drawer-other-group">
                <button type="button" class="drawer-other-group__head" :aria-expanded="openOtherGroups.has(group.key)" @click="toggleOtherGroup(group.key)">
                  {{ group.session }} 第{{ group.parent }}题 · {{ group.items.length }} 个小问 · 得分率 {{ group.range }}
                </button>
                <ul v-if="openOtherGroups.has(group.key)" class="drawer-other-group__items"><li v-for="item in group.items" :key="`${item.session_name}|${item.question_label}`">{{ item.session_name }} 第{{ item.question_label }}题 · 得分率 {{ formatPercent(item.class_rate) }}</li></ul>
              </div>
            </template>
          </section>
          <section v-if="!typeMode && related.length" class="mastery-map-related"><h3>{{ selected.kind === 'skill' ? '相关知识点' : '相关技能' }}</h3>
            <button v-for="item in related" :key="item.node.knowledge_key" type="button" @click="select(item.node)">
              <i :class="`rate-${rateBand(nodeMastery(item.node))}`" /><span><strong>{{ shortNodeName(item.node) }}</strong><small>掌握度 {{ formatPercent(nodeMastery(item.node)) }} · 明显薄弱 {{ item.node.distribution.weak }} 人 · {{ item.association.basis === 'same_part' ? `同一小问 ${item.association.same_part_question_count} 题` : `仅同题出现 ${item.association.question_count} 题（虚线）` }}</small></span>
            </button>
            <p>关联来自题库里同时考查两者的题目，不代表两者掌握度相同。</p>
          </section>
        </aside>
      </div>
      <details v-if="overview.warnings.length" class="overview-data-notes"><summary>数据说明（{{ overview.warnings.length }}）</summary><ul><li v-for="warning in overview.warnings" :key="warning">{{ warning }}</li></ul></details>
    </template>
  </section>
</template>
