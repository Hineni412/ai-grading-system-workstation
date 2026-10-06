<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  questionBankApi,
  type ChapterExamChapter,
  type ChapterExamProfile,
  type ChapterExamSection as ChapterExamSectionData,
  type ChapterExamStage,
  type ChapterExamStageStats,
  type QuestionBankFilters,
  type QuestionBankListItem,
  type QuestionBankListResponse,
} from '../../api/question-bank'
import type { TrainingOverviewNode } from '../../api/training'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../../stores/mastery-overview'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import AppIconButton from '../design-system/AppIconButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import OverviewScopeBar from '../knowledge-overview/OverviewScopeBar.vue'
import QuestionLedger from './QuestionLedger.vue'
import ChapterExamOverview from './ChapterExamOverview.vue'
import ChapterExamSection from './ChapterExamSection.vue'
import '../../styles/chapter-exam-profile.css'

const emit = defineEmits<{ skill: [key: string] }>()

const route = useRoute()
const router = useRouter()
const scope = useCurriculumScopeStore()
const bank = useQuestionBankStore()
const masteryStore = useMasteryOverviewStore()

const profile = ref<ChapterExamProfile | null>(null)
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
let controller: AbortController | null = null
const layout = ref<HTMLElement | null>(null)
const scrollEl = ref<HTMLElement | null>(null)
const chapterSwitcher = ref<HTMLDetailsElement | null>(null)
const railOpen = ref(false)
const ledgerKind = ref<'default' | 'cell'>('default')
const cellHeading = ref('')
const spySection = ref<string | null>(null)
const spyVisible = new Set<string>()
let observer: IntersectionObserver | null = null
let urlTimer: ReturnType<typeof setTimeout> | undefined
const chapterItems = shallowRef(new Map<number, QuestionBankListItem>())
let cacheKey = ''
let cachePromise: Promise<void> | null = null

const TIER_LABELS = { basic: '基础入口', mid: '常见变式', hard: '提高变化' } as const

type StageSelection = ChapterExamStage | 'all'
type TypicalPick = ChapterExamSectionData['skills'][number]['typical'][number]

const queryText = (key: string) => (typeof route.query[key] === 'string' ? String(route.query[key]) : '')
const stage = computed<StageSelection>(() => {
  const value = queryText('stage')
  return value === 'midterm' || value === 'final' ? value : 'all'
})
const stages = computed<ChapterExamStageStats[]>(() => profile.value?.stages ?? [])
const stageInfo = computed(
  () => Object.fromEntries(stages.value.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)
const currentChapter = computed(() => {
  const chapters = profile.value?.chapters ?? []
  const sectionId = queryText('section')
  const bySection = sectionId
    ? chapters.find(chapter => chapter.sections.some(section => section.id === sectionId))
    : undefined
  const chapterId = queryText('chapter')
  return bySection ?? chapters.find(chapter => chapter.id === chapterId) ?? chapters[0]
})
const chapterSections = computed(
  () => (currentChapter.value?.sections ?? []).filter(section => section.main_count > 0),
)
const blockIds = computed(() => ['overview', ...chapterSections.value.map(section => section.id)])
const currentSection = computed(
  () => chapterSections.value.find(section => section.id === spySection.value),
)
const switcherLabel = computed(() => currentSection.value?.label || currentChapter.value?.label || '章节考情')
const subline = computed(() => {
  const first = stageInfo.value.midterm
  const second = stageInfo.value.final
  const main = currentChapter.value
    ? currentChapter.value.totals.midterm.main + currentChapter.value.totals.final.main
    : 0
  return `期中 ${first?.group_count ?? 0}${first?.unit ?? '组'} · 期末 ${second?.group_count ?? 0}${second?.unit ?? '组'} · 本章主考 ${main} 题`
})
const mergedGroups = computed(() => profile.value?.merged_groups ?? [])
const mergedSummary = computed(() => mergedGroups.value
  .map(group => `${group.stage === 'midterm' ? '期中' : '期末'} ${group.papers.length} 份同源卷合为 1 组`)
  .join('；'))
const mergedTitles = computed(() => mergedGroups.value
  .map(group => group.papers.map(paper => paper.title).join(' 与 '))
  .join('；'))

function sectionPicks(section: ChapterExamSectionData): TypicalPick[] {
  return section.skills.flatMap(skill => skill.typical)
}

function chapterPickIds(chapter: ChapterExamChapter | undefined): number[] {
  const seen = new Set<number>()
  const ids: number[] = []
  for (const section of chapter?.sections ?? []) {
    for (const pick of sectionPicks(section)) {
      if (!seen.has(pick.question_id)) {
        seen.add(pick.question_id)
        ids.push(pick.question_id)
      }
    }
  }
  return ids
}

const currentTypicalIds = computed(() => (
  currentSection.value
    ? sectionPicks(currentSection.value).map(pick => pick.question_id)
    : chapterPickIds(currentChapter.value)
))

function pickLabel(pick: TypicalPick, prefix = ''): string {
  const role = pick.role ?? TIER_LABELS[pick.tier]
  const reason = pick.reason ?? `同类 ${pick.same_tier_count} 题 · 出现在 ${pick.group_count} 套试卷`
  return prefix ? `${prefix} · ${role} · ${reason}` : `${role} · ${reason}`
}

const typicalLabels = computed<Record<number, string>>(() => {
  const labels: Record<number, string> = {}
  if (currentSection.value) {
    for (const pick of sectionPicks(currentSection.value)) labels[pick.question_id] = pickLabel(pick)
  } else {
    for (const section of chapterSections.value) {
      for (const pick of sectionPicks(section)) labels[pick.question_id] = pickLabel(pick, section.label)
    }
  }
  return labels
})

const paneTitle = computed(() => (
  cellHeading.value
  || (currentSection.value
    ? `${currentSection.value.label} · 典型题 · 共 ${currentTypicalIds.value.length} 题`
    : `${currentChapter.value?.label ?? ''} · 全章典型题`)
))

const masteryNodes = computed(() => new Map<string, TrainingOverviewNode>(
  (masteryStore.overview?.nodes ?? [])
    .filter(node => node.kind === 'skill')
    .map(node => [node.knowledge_key, node]),
))
const masteryState = computed<'idle' | 'loading' | 'ready' | 'error'>(() => {
  const state = masteryStore.loadState
  if (state === 'error') return 'error'
  if (state === 'ready' || state === 'stale-error') return 'ready'
  return 'loading'
})

function storageKey(): string {
  return `chapter-exam:last:${scope.selectedVolumeId}`
}

function persistLast(): void {
  const chapter = currentChapter.value
  if (!chapter || !scope.selectedVolumeId) return
  try {
    localStorage.setItem(storageKey(), JSON.stringify({
      chapterId: chapter.id,
      sectionId: spySection.value,
      stage: stage.value,
    }))
  } catch { /* 存储不可用时静默忽略 */ }
}

function restoreLast(): void {
  if (queryText('chapter') || queryText('section')) return
  let saved: { chapterId?: string; sectionId?: string | null; stage?: string } | null = null
  try {
    const raw = localStorage.getItem(storageKey())
    saved = raw ? JSON.parse(raw) : null
  } catch {
    saved = null
  }
  const chapter = saved?.chapterId
    ? profile.value?.chapters.find(row => row.id === saved!.chapterId)
    : undefined
  if (!chapter) return
  const section = saved?.sectionId && chapter.sections.some(row => row.id === saved!.sectionId)
    ? saved!.sectionId
    : undefined
  const savedStage = saved?.stage === 'midterm' || saved?.stage === 'final' ? saved.stage : undefined
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: chapter.id, section, stage: savedStage, question: undefined },
  })
}

async function loadProfile(): Promise<void> {
  controller?.abort()
  if (!scope.selectedVolumeId) {
    profile.value = null
    loadState.value = 'idle'
    return
  }
  const current = new AbortController()
  controller = current
  loadState.value = 'loading'
  errorMessage.value = ''
  try {
    const value = await questionBankApi.chapterExamProfile(scope.selectedVolumeId, current.signal)
    if (!current.signal.aborted) {
      profile.value = value
      loadState.value = 'ready'
      restoreLast()
    }
  } catch {
    if (!current.signal.aborted) {
      errorMessage.value = '章节考情暂时无法读取，请重试。'
      loadState.value = 'error'
    }
  }
}
watch(() => scope.selectedVolumeId, () => void loadProfile(), { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  observer?.disconnect()
  clearTimeout(urlTimer)
})

function baseFilters(ids: number[]): QuestionBankFilters {
  return {
    page: 1,
    pageSize: 100,
    questionIds: ids,
    tagStatus: 'all',
    collapseDuplicates: false,
    includeSkills: true,
    sort: 'difficulty_asc',
  }
}

async function fetchAllPages(
  filters: QuestionBankFilters,
  signal?: AbortSignal,
): Promise<QuestionBankListResponse> {
  const first = await questionBankApi.listQuestions(filters, signal)
  const items = [...first.items]
  for (let page = 2; page <= first.total_pages; page += 1) {
    items.push(...(await questionBankApi.listQuestions({ ...filters, page }, signal)).items)
  }
  return { ...first, items, total_pages: 1 }
}

function clearLedger(): void {
  bank.questions = []
  bank.total = 0
  bank.totalPages = 1
  bank.listState = 'idle'
}

function ensureChapterItems(chapter: ChapterExamChapter): Promise<void> {
  if (cacheKey === chapter.id && cachePromise) return cachePromise
  cacheKey = chapter.id
  const ids = chapterPickIds(chapter).slice(0, 500)
  chapterItems.value = new Map()
  cachePromise = (async () => {
    if (!ids.length) return
    try {
      const result = await fetchAllPages(baseFilters(ids))
      if (cacheKey === chapter.id) {
        chapterItems.value = new Map(result.items.map(item => [item.id, item]))
      }
    } catch { /* 缓存失败时明细加载回退到实时请求 */ }
  })()
  return cachePromise
}

async function applyTypical(): Promise<void> {
  const ids = currentTypicalIds.value
  if (!ids.length) {
    clearLedger()
    bank.listState = 'empty'
    return
  }
  const chapter = currentChapter.value
  if (chapter) await ensureChapterItems(chapter)
  const cached = chapterItems.value
  await bank.loadQuestions(baseFilters(ids), async (filters, signal) => {
    const items = ids
      .map(id => cached.get(id))
      .filter((item): item is QuestionBankListItem => !!item)
    if (items.length) {
      return {
        items,
        total: items.length,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 100,
        total_pages: 1,
      }
    }
    return fetchAllPages(filters, signal)
  })
  await nextTick()
  if (!bank.selectedQuestionId) {
    layout.value?.querySelector('.qb-question-list')?.scrollTo?.({ top: 0 })
  }
}

function openCell(title: string, ids: number[]): void {
  if (!ids.length) return
  ledgerKind.value = 'cell'
  cellHeading.value = `${title} ${ids.length} 题`
  void bank.selectQuestion(null)
  void bank.loadQuestions(baseFilters(ids.slice(0, 500)), fetchAllPages)
}

function backToDefault(): void {
  void bank.selectQuestion(null)
  ledgerKind.value = 'default'
  cellHeading.value = ''
  void applyTypical()
}

function queueUrlWrite(): void {
  clearTimeout(urlTimer)
  urlTimer = setTimeout(() => {
    const chapter = currentChapter.value
    if (!chapter) return
    void router.replace({
      query: {
        ...route.query,
        tab: 'exam',
        chapter: chapter.id,
        section: spySection.value ?? undefined,
        question: undefined,
      },
    })
  }, 300)
}

function setCurrent(id: string | null): void {
  if (spySection.value === id) return
  spySection.value = id
  persistLast()
  queueUrlWrite()
  if (ledgerKind.value !== 'cell') void applyTypical()
}

function scrollToBlock(id: string | null, behavior: ScrollBehavior = 'smooth'): void {
  const root = scrollEl.value
  if (!root) return
  if (!id) {
    root.scrollTo?.({ top: 0, behavior })
    return
  }
  const target = root.querySelector<HTMLElement>(`[data-cep-block="${id.replace(/"/g, '')}"]`)
  target?.scrollIntoView?.({ behavior, block: 'start' })
}

function jumpTo(id: string | null): void {
  setCurrent(id)
  scrollToBlock(id)
}

function onSpy(entries: IntersectionObserverEntry[]): void {
  for (const entry of entries) {
    const id = (entry.target as HTMLElement).dataset.cepBlock
    if (!id) continue
    if (entry.isIntersecting) spyVisible.add(id)
    else spyVisible.delete(id)
  }
  const current = [...blockIds.value].reverse().find(id => spyVisible.has(id))
  if (current !== undefined) setCurrent(current === 'overview' ? null : current)
}

function setupSpy(): void {
  observer?.disconnect()
  observer = null
  const root = scrollEl.value
  if (!root || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(onSpy, { rootMargin: '-10% 0px -60% 0px' })
  root.querySelectorAll<HTMLElement>('[data-cep-block]').forEach(el => observer!.observe(el))
}

watch(currentChapter, (chapter, previous) => {
  if (!chapter || chapter.id === previous?.id) return
  spyVisible.clear()
  const raw = queryText('section')
  spySection.value = raw && chapterSections.value.some(section => section.id === raw) ? raw : null
  bank.clearSelection()
  void bank.selectQuestion(null)
  ledgerKind.value = 'default'
  cellHeading.value = ''
  void nextTick(() => {
    setupSpy()
    scrollToBlock(spySection.value, 'auto')
    void applyTypical()
  })
  persistLast()
})

watch(() => route.query.section, (raw) => {
  const id = typeof raw === 'string' && raw ? raw : null
  const valid = id && chapterSections.value.some(section => section.id === id) ? id : null
  if (valid === spySection.value) return
  spySection.value = valid
  persistLast()
  void nextTick(() => scrollToBlock(valid))
  if (ledgerKind.value !== 'cell') void applyTypical()
})

watch(stage, () => persistLast())

function selectChapter(id: string): void {
  void bank.selectQuestion(null)
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: id, section: undefined, question: undefined },
  })
}

function choose(id: string): void {
  selectChapter(id)
  const popover = chapterSwitcher.value
  if (popover) {
    popover.open = false
    popover.querySelector<HTMLElement>('summary')?.focus()
  }
}

function setStage(next: StageSelection): void {
  void router.replace({ query: { ...route.query, stage: next === 'all' ? undefined : next } })
}

function chipLabel(section: ChapterExamSectionData): string {
  return section.label.split(/\s+/)[0] || section.label
}

function sectionCoverage(section: ChapterExamSectionData, stageKey: ChapterExamStage): string {
  const cell = section.coverage[stageKey]
  const percent = cell.percent === null ? '—' : `${cell.percent}%`
  return `${cell.groups}/${cell.of}${stageInfo.value[stageKey]?.unit ?? '份'}（${percent}）`
}

const narrowRail = computed(() => Boolean(bank.selectedQuestionId) && !railOpen.value)
</script>

<template>
  <StatePanel
    v-if="!scope.selectedVolumeId"
    kind="empty"
    title="请先选择教学学期"
    description="在左侧栏“当前考试”中选择教学学期。"
  />
  <StatePanel v-else-if="loadState === 'loading' && !profile" kind="loading" title="正在统计本册期中与期末卷…" />
  <StatePanel
    v-else-if="loadState === 'error' && !profile"
    kind="error"
    :title="errorMessage"
    retry-label="重新加载"
    @retry="loadProfile"
  />
  <div
    v-else-if="profile"
    ref="layout"
    class="qb-skill-layout chapter-exam"
    :class="{ 'is-reading': bank.selectedQuestionId, 'is-skill-narrow': narrowRail }"
  >
    <aside class="qb-skill-pane qb-browse-pane">
      <div v-if="bank.selectedQuestionId" class="qb-skill-rail">
        <AppIconButton
          class="qb-rail-toggle"
          :label="railOpen ? '收窄章节考情' : '展开章节考情'"
          icon="book-open"
          variant="secondary"
          :aria-expanded="railOpen"
          @click="railOpen = !railOpen"
        />
        <span v-if="!railOpen">{{ switcherLabel }}</span>
      </div>
      <header class="qb-skill-list-heading">
        <details ref="chapterSwitcher" class="qb-label-popover qb-section-switcher">
          <summary>
            <span class="qb-switcher-current"><small>教材章节</small><b>{{ currentChapter?.label ?? '章节考情' }}</b></span>
            <span class="qb-switcher-caret">▾</span>
          </summary>
          <div>
            <div class="qb-chapter-tree">
              <button
                v-for="chapter in profile.chapters"
                :key="chapter.id"
                type="button"
                class="qb-tree-row"
                :class="{ 'is-active': chapter.id === currentChapter?.id }"
                @click="choose(chapter.id)"
              >{{ chapter.label }} <small>主考 {{ chapter.totals.midterm.main + chapter.totals.final.main }}</small></button>
            </div>
          </div>
        </details>
        <div class="qb-skill-subline"><p>{{ subline }}</p></div>
      </header>
      <div class="cep-tools">
        <div class="app-segmented" role="group" aria-label="考试阶段">
          <button
            v-for="item in ([{ key: 'midterm', label: '期中' }, { key: 'final', label: '期末' }, { key: 'all', label: '合计' }] as const)"
            :key="item.key"
            type="button"
            :aria-pressed="stage === item.key"
            :class="{ 'is-active': stage === item.key }"
            @click="setStage(item.key)"
          >{{ item.label }}</button>
        </div>
        <OverviewScopeBar />
        <div v-if="mergedSummary" class="cep-note">
          同源卷合并：{{ mergedSummary }}
          <details v-if="mergedTitles" class="cep-merged-details">
            <summary>查看</summary>
            <span>{{ mergedTitles }}</span>
          </details>
        </div>
      </div>
      <nav v-if="currentChapter" class="cep-index" aria-label="小节索引">
        <button
          type="button"
          class="qb-filter-chip cep-index-chip"
          :class="{ 'is-active': !currentSection }"
          :aria-current="!currentSection ? 'true' : undefined"
          @click="jumpTo(null)"
        >本章总览</button>
        <button
          v-for="section in currentChapter.sections"
          :key="section.id"
          type="button"
          class="qb-filter-chip cep-index-chip"
          :class="{ 'is-active': currentSection?.id === section.id }"
          :aria-current="currentSection?.id === section.id ? 'true' : undefined"
          :disabled="!section.main_count"
          :title="section.label"
          @click="jumpTo(section.id)"
        >{{ chipLabel(section) }}</button>
      </nav>
      <div ref="scrollEl" class="cep-scroll">
        <section v-if="currentChapter" class="cep-block" data-cep-block="overview">
          <h3 class="cep-block-title">本章总览</h3>
          <ChapterExamOverview
            :chapter="currentChapter"
            :stage="stage"
            :stages="stages"
            @section="jumpTo"
            @cell="openCell"
          />
        </section>
        <section
          v-for="section in chapterSections"
          :key="section.id"
          class="cep-block cep-section-block"
          :data-cep-block="section.id"
        >
          <header class="cep-section-head">
            <h3 class="cep-block-title">{{ section.label }}</h3>
            <p class="cep-coverage">
              <span>本节主考 <b>{{ section.main_count }}</b> 题</span>
              <span>出卷率：期中 <b>{{ sectionCoverage(section, 'midterm') }}</b></span>
              <span>期末 <b>{{ sectionCoverage(section, 'final') }}</b></span>
            </p>
          </header>
          <ChapterExamSection
            :section="section"
            :stage="stage"
            :stages="stages"
            :mastery="masteryNodes"
            :mastery-state="masteryState"
            @cell="openCell"
          />
        </section>
      </div>
    </aside>
    <main class="qb-question-pane qb-browse-pane">
      <header class="qb-target-heading">
        <div class="qb-target-heading__line">
          <h2>{{ paneTitle }}</h2>
          <AppButton
            v-if="ledgerKind === 'cell'"
            variant="ghost"
            size="small"
            @click="backToDefault"
          >回到典型题</AppButton>
        </div>
      </header>
      <QuestionLedger
        :question-labels="ledgerKind === 'default' ? typicalLabels : undefined"
        @skill="emit('skill', $event)"
      />
    </main>
  </div>
</template>
