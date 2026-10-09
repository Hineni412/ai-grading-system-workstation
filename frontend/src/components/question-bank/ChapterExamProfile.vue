<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronDown } from '@lucide/vue'

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
import { useAssemblyStore } from '../../stores/assembly'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../../stores/mastery-overview'
import { useQuestionBankStore } from '../../stores/question-bank'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import OverviewScopeBar from '../knowledge-overview/OverviewScopeBar.vue'
import QuestionLedger from './QuestionLedger.vue'
import QuestionSimilarSheet from './QuestionSimilarSheet.vue'
import ChapterExamOverview from './ChapterExamOverview.vue'
import ChapterExamSection from './ChapterExamSection.vue'
import '../../styles/chapter-exam-profile.css'


const route = useRoute()
const router = useRouter()
const scope = useCurriculumScopeStore()
const bank = useQuestionBankStore()
const assembly = useAssemblyStore()
const masteryStore = useMasteryOverviewStore()

const profile = ref<ChapterExamProfile | null>(null)
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
let controller: AbortController | null = null
const scrollEl = ref<HTMLElement | null>(null)
const chapterSwitcher = ref<HTMLDetailsElement | null>(null)
const drawerOpen = ref(false)
const cellHeading = ref('')
const spySection = ref<string | null>(null)
const spyVisible = new Set<string>()
let observer: IntersectionObserver | null = null
let urlTimer: ReturnType<typeof setTimeout> | undefined
let spyMuted = false
let spySettleTimer: ReturnType<typeof setTimeout> | undefined
let scrollTargetId: string | null | undefined
let scrollRealigns = 0
const chapterItems = shallowRef(new Map<number, QuestionBankListItem>())
const itemsState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let cacheKey = ''
let cachePromise: Promise<void> | null = null
let questionReadVersion: number | undefined
let mounted = true

const similarSource = ref<QuestionBankListItem | null>(null)

const queryText = (key: string) => (typeof route.query[key] === 'string' ? String(route.query[key]) : '')
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

type TypicalPick = ChapterExamSectionData['skills'][number]['typical'][number]

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
    }))
  } catch { /* 存储不可用时静默忽略 */ }
}

function restoreLast(): void {
  if (queryText('chapter') || queryText('section')) return
  let saved: { chapterId?: string; sectionId?: string | null } | null = null
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
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: chapter.id, section, stage: undefined, question: undefined },
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
    const value = await bank.loadChapterExamProfile(scope.selectedVolumeId, current.signal)
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
watch(() => scope.selectedVolumeId, volumeId => {
  profile.value = volumeId ? bank.chapterExamProfiles.get(volumeId) ?? null : null
  chapterItems.value = new Map()
  itemsState.value = 'idle'
  cacheKey = ''
  cachePromise = null
  if (profile.value) restoreLast()
  void loadProfile()
}, { immediate: true })
watch(() => bank.readRevision, () => {
  cacheKey = ''
  cachePromise = null
  void loadProfile()
})
onBeforeUnmount(() => {
  mounted = false
  document.removeEventListener('keydown', onEscape)
  if (questionReadVersion !== undefined) bank.cancelQuestionRead(questionReadVersion)
  controller?.abort()
  observer?.disconnect()
  clearTimeout(urlTimer)
  clearTimeout(spySettleTimer)
  scrollEl.value?.removeEventListener('scrollend', settleSpy)
})
onMounted(() => document.addEventListener('keydown', onEscape))

function onEscape(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || event.defaultPrevented) return
  const popover = document.querySelector<HTMLDetailsElement>('.qb-label-popover[open], .qb-card-menu[open], .qb-paper-more[open]')
  if (popover) {
    popover.open = false
    popover.querySelector<HTMLElement>('summary')?.focus()
    event.preventDefault()
    return
  }
  if (similarSource.value) closeSimilar()
  else if (!document.querySelector('[role="dialog"][aria-modal="true"]')) void bank.selectQuestion(null)
}

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

function ensureChapterItems(chapter: ChapterExamChapter): Promise<void> {
  const ids = chapterPickIds(chapter).slice(0, 500)
  const key = `${scope.selectedVolumeId}:${chapter.id}:${ids.join(',')}`
  if (cacheKey === key && cachePromise) return cachePromise
  cacheKey = key
  chapterItems.value = new Map()
  itemsState.value = ids.length ? 'loading' : 'ready'
  cachePromise = (async () => {
    if (!ids.length) return
    try {
      const result = await fetchAllPages(baseFilters(ids))
      if (mounted && cacheKey === key) {
        chapterItems.value = new Map(result.items.map(item => [item.id, item]))
        itemsState.value = 'ready'
      }
    } catch {
      if (mounted && cacheKey === key) itemsState.value = 'error'
    }
  })()
  return cachePromise
}

function retryChapterItems(): void {
  cacheKey = ''
  cachePromise = null
  const chapter = currentChapter.value
  if (chapter) void ensureChapterItems(chapter)
}

function openCell(title: string, ids: number[]): void {
  const unique = [...new Set(ids)]
  if (!unique.length) return
  cellHeading.value = `${title} · ${unique.length} 题`
  drawerOpen.value = true
  void bank.selectQuestion(null)
  void bank.loadQuestions(baseFilters(unique.slice(0, 500)), fetchAllPages)
  questionReadVersion = bank.questionReadVersion
}

function onDrawerUpdate(open: boolean): void {
  drawerOpen.value = open
  if (!open) {
    cellHeading.value = ''
    void bank.selectQuestion(null)
  }
}

async function ensureAssembly(): Promise<void> {
  if (assembly.loadState === 'idle') await assembly.load()
}

async function addSelectionToAssembly(): Promise<void> {
  if (bank.selectedCount === 0 || assembly.saveState === 'saving') return
  await ensureAssembly()
  await assembly.addQuestions(bank.selectedQuestionIds)
}

function openSimilar(question: QuestionBankListItem): void {
  similarSource.value = question
}

function closeSimilar(): void {
  similarSource.value = null
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
        stage: undefined,
      },
    })
  }, 300)
}

function setCurrent(id: string | null): void {
  if (spySection.value === id) return
  spySection.value = id
  persistLast()
  queueUrlWrite()
}

function blockEl(id: string): HTMLElement | null {
  return scrollEl.value?.querySelector<HTMLElement>(`[data-cep-block="${id.replace(/"/g, '')}"]`) ?? null
}

function doScroll(id: string | null, behavior: ScrollBehavior): void {
  const root = scrollEl.value
  if (!root) return
  if (!id) {
    root.scrollTo?.({ top: 0, behavior })
    return
  }
  blockEl(id)?.scrollIntoView?.({ behavior, block: 'start' })
}

function scrollToBlock(id: string | null, behavior: ScrollBehavior = 'smooth'): void {
  if (!scrollEl.value) return
  scrollTargetId = id
  scrollRealigns = 0
  muteSpyWhileScrolling()
  doScroll(id, behavior)
}

function jumpTo(id: string | null): void {
  setCurrent(id)
  scrollToBlock(id)
}

function evaluateSpy(): void {
  const current = [...blockIds.value].reverse().find(id => spyVisible.has(id))
  if (current !== undefined) setCurrent(current === 'overview' ? null : current)
}

// 程序化滚动途中忽略 spy 结果：scrollend 或兜底计时后统一评估一次，避免滚动经过的小节被写进 URL。
function muteSpyWhileScrolling(): void {
  spyMuted = true
  clearTimeout(spySettleTimer)
  spySettleTimer = setTimeout(settleSpy, 800)
}

// 滚动后才渲染完的内容（典型题、公式）会让目标块漂移，落定后校正位置再评估。
function realignScrollTarget(): boolean {
  const root = scrollEl.value
  const id = scrollTargetId
  if (!root || id === undefined || scrollRealigns >= 3) return false
  const el = id === null ? null : blockEl(id)
  if (id !== null && !el) return false
  const offset = id === null
    ? root.scrollTop
    : el!.getBoundingClientRect().top - root.getBoundingClientRect().top
  if (Math.abs(offset) <= 24) return false
  scrollRealigns += 1
  muteSpyWhileScrolling()
  doScroll(id, 'auto')
  return true
}

function settleSpy(): void {
  clearTimeout(spySettleTimer)
  spySettleTimer = undefined
  if (!spyMuted) return
  spyMuted = false
  if (realignScrollTarget()) return
  scrollTargetId = undefined
  scrollRealigns = 0
  evaluateSpy()
}

function onSpy(entries: IntersectionObserverEntry[]): void {
  for (const entry of entries) {
    const id = (entry.target as HTMLElement).dataset.cepBlock
    if (!id) continue
    if (entry.isIntersecting) spyVisible.add(id)
    else spyVisible.delete(id)
  }
  if (!spyMuted) evaluateSpy()
}

function setupSpy(): void {
  observer?.disconnect()
  observer = null
  const root = scrollEl.value
  if (!root || typeof IntersectionObserver === 'undefined') return
  root.addEventListener('scrollend', settleSpy)
  observer = new IntersectionObserver(onSpy, { rootMargin: '-10% 0px -60% 0px' })
  root.querySelectorAll<HTMLElement>('[data-cep-block]').forEach(el => observer!.observe(el))
}

watch(currentChapter, (chapter, previous) => {
  if (!chapter || chapter === previous) return
  if (chapter.id === previous?.id) return
  spyVisible.clear()
  const raw = queryText('section')
  spySection.value = raw && chapterSections.value.some(section => section.id === raw) ? raw : null
  bank.clearSelection()
  void bank.selectQuestion(null)
  drawerOpen.value = false
  cellHeading.value = ''
  void ensureChapterItems(chapter)
  void nextTick(() => {
    setupSpy()
    scrollToBlock(spySection.value, 'auto')
  })
  persistLast()
}, { immediate: true })

watch(() => route.query.section, (raw) => {
  const id = typeof raw === 'string' && raw ? raw : null
  const valid = id && chapterSections.value.some(section => section.id === id) ? id : null
  if (valid === spySection.value) return
  spySection.value = valid
  persistLast()
  void nextTick(() => scrollToBlock(valid))
})

function selectChapter(id: string): void {
  void bank.selectQuestion(null)
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: id, section: undefined, stage: undefined, question: undefined },
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

function chipLabel(section: ChapterExamSectionData): string {
  return section.label.split(/\s+/)[0] || section.label
}

function sectionCoverage(section: ChapterExamSectionData, stageKey: ChapterExamStage): string {
  const cell = section.coverage[stageKey]
  const percent = cell.percent === null ? '—' : `${cell.percent}%`
  return `${cell.groups}/${cell.of}${stageInfo.value[stageKey]?.unit ?? '份'}（${percent}）`
}
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
  <div v-else-if="profile" class="chapter-exam qb-browse-pane">
    <header class="cep-head">
      <div class="cep-head-top">
        <details ref="chapterSwitcher" class="qb-label-popover qb-section-switcher">
          <summary>
            <span class="qb-switcher-current"><small>教材章节</small><b>{{ currentChapter?.label ?? '章节考情' }}</b></span>
            <ChevronDown class="qb-switcher-caret" aria-hidden="true" />
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
        <p class="cep-subline">{{ subline }}</p>
      </div>
      <OverviewScopeBar />
      <div v-if="mergedSummary" class="cep-note">
        同源卷合并：{{ mergedSummary }}
        <details v-if="mergedTitles" class="cep-merged-details">
          <summary>查看</summary>
          <span>{{ mergedTitles }}</span>
        </details>
      </div>
    </header>
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
          :stages="stages"
          :mastery="masteryNodes"
          :mastery-state="masteryState"
          :items="chapterItems"
          :items-state="itemsState"
          @cell="openCell"
          @retry-items="retryChapterItems"
          @similar="openSimilar"
        />
      </section>
    </div>
    <footer v-if="bank.selectedCount && !drawerOpen" class="cep-selection" role="status">
      <span>已选 <strong>{{ bank.selectedCount }}</strong> 题</span>
      <span class="cep-selection__actions">
        <AppButton
          variant="primary"
          size="small"
          :disabled="assembly.saveState === 'saving'"
          @click="addSelectionToAssembly"
        >加入试卷篮</AppButton>
        <AppButton type="button" variant="ghost" size="small" @click="bank.clearSelection">清空选择</AppButton>
      </span>
    </footer>
  </div>

  <Sheet :open="drawerOpen" @update:open="onDrawerUpdate">
    <SheetContent class="cep-cell-drawer gap-0" :aria-describedby="undefined">
      <SheetHeader class="cep-drawer-head">
        <SheetTitle as="h2">{{ cellHeading }}</SheetTitle>
      </SheetHeader>
      <QuestionLedger />
    </SheetContent>
  </Sheet>

  <QuestionSimilarSheet :source="similarSource" @close="closeSimilar" />
</template>
