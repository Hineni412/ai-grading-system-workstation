<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  questionBankApi,
  type ChapterExamProfile,
  type ChapterExamSection as ChapterExamSectionData,
  type ChapterExamStage,
  type ChapterExamStageStats,
  type QuestionBankFilters,
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
const sectionSwitcher = ref<HTMLDetailsElement | null>(null)
const railOpen = ref(false)
const ledgerKind = ref<'default' | 'cell'>('default')
const cellHeading = ref('')

const TIER_LABELS = { basic: '基础入口', mid: '常见变式', hard: '提高变化' } as const

type StageSelection = ChapterExamStage | 'all'

const queryText = (key: string) => (typeof route.query[key] === 'string' ? String(route.query[key]) : '')
const stage = computed<StageSelection>(() => {
  const value = queryText('stage')
  return value === 'midterm' || value === 'final' ? value : 'all'
})
const stages = computed<ChapterExamStageStats[]>(() => profile.value?.stages ?? [])
const stageInfo = computed(
  () => Object.fromEntries(stages.value.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)
const allSections = computed(
  () => profile.value?.chapters.flatMap(chapter => chapter.sections) ?? [],
)
const selectedSection = computed<ChapterExamSectionData | undefined>(() => {
  const id = queryText('section')
  return id ? allSections.value.find(section => section.id === id) : undefined
})
const currentChapter = computed(() => {
  if (selectedSection.value) {
    return profile.value?.chapters.find(
      chapter => chapter.sections.some(section => section.id === selectedSection.value!.id),
    )
  }
  const id = queryText('chapter')
  return profile.value?.chapters.find(chapter => chapter.id === id) ?? profile.value?.chapters[0]
})
const switcherLabel = computed(() => (
  selectedSection.value?.label
  || (currentChapter.value ? `${currentChapter.value.label} · 本章总览` : '章节考情')
))
const subline = computed(() => {
  const first = stageInfo.value.midterm
  const second = stageInfo.value.final
  const main = selectedSection.value?.main_count
    ?? (currentChapter.value ? currentChapter.value.totals.midterm.main + currentChapter.value.totals.final.main : 0)
  const unit = selectedSection.value ? '本节主考' : '本章主考'
  return `期中 ${first?.group_count ?? 0}${first?.unit ?? '组'} · 期末 ${second?.group_count ?? 0}${second?.unit ?? '组'} · ${unit} ${main} 题`
})
const mergedGroups = computed(() => profile.value?.merged_groups ?? [])
const mergedSummary = computed(() => mergedGroups.value
  .map(group => `${group.stage === 'midterm' ? '期中' : '期末'} ${group.papers.length} 份同源卷合为 1 组`)
  .join('；'))
const mergedTitles = computed(() => mergedGroups.value
  .map(group => group.papers.map(paper => paper.title).join(' 与 '))
  .join('；'))

const typicalPicks = computed(() => (
  (selectedSection.value?.skills ?? [])
    .flatMap(skill => skill.typical)
))
const typicalLabels = computed<Record<number, string>>(() => Object.fromEntries(
  typicalPicks.value.map(pick => [
    pick.question_id,
    `${TIER_LABELS[pick.tier]} · 同类 ${pick.same_tier_count} 题 · 出现在 ${pick.group_count} 套试卷`,
  ]),
))
const showQuestionPane = computed(() => Boolean(selectedSection.value) || ledgerKind.value === 'cell')
const paneTitle = computed(() => (
  cellHeading.value
  || (selectedSection.value ? `${selectedSection.value.label} · 典型题`
    : currentChapter.value ? `${currentChapter.value.label} · 本章总览` : '章节考情')
))

const masteryNodes = computed(() => new Map<string, TrainingOverviewNode>(
  (masteryStore.overview?.nodes ?? [])
    .filter(node => node.kind === 'skill')
    .map(node => [node.knowledge_key, node]),
))
const masteryState = computed<'idle' | 'loading' | 'ready' | 'error'>(() => {
  if (!selectedSection.value) return 'idle'
  const state = masteryStore.loadState
  if (state === 'error') return 'error'
  if (state === 'ready' || state === 'stale-error') return 'ready'
  return 'loading'
})

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
    }
  } catch {
    if (!current.signal.aborted) {
      errorMessage.value = '章节考情暂时无法读取，请重试。'
      loadState.value = 'error'
    }
  }
}
watch(() => scope.selectedVolumeId, () => void loadProfile(), { immediate: true })
onBeforeUnmount(() => controller?.abort())

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

function loadTypicals(): void {
  ledgerKind.value = 'default'
  cellHeading.value = ''
  const order = typicalPicks.value.map(pick => pick.question_id)
  if (!order.length) {
    clearLedger()
    bank.listState = 'empty'
    return
  }
  void bank.loadQuestions(baseFilters(order), async (filters, signal) => {
    const result = await fetchAllPages(filters, signal)
    const rank = new Map(order.map((id, index) => [id, index]))
    result.items.sort(
      (a, b) => (rank.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.id) ?? Number.MAX_SAFE_INTEGER),
    )
    return result
  }).then(async () => {
    await nextTick()
    if (!bank.selectedQuestionId) {
      layout.value?.querySelector('.qb-question-list')?.scrollTo?.({ top: 0 })
    }
  })
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
  if (selectedSection.value) loadTypicals()
  else clearLedger()
}

watch([selectedSection, currentChapter, stage], () => {
  bank.clearSelection()
  void bank.selectQuestion(null)
  ledgerKind.value = 'default'
  cellHeading.value = ''
  if (selectedSection.value) loadTypicals()
  else clearLedger()
})

function selectChapter(id: string): void {
  void bank.selectQuestion(null)
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: id, section: undefined, question: undefined },
  })
}

function selectSection(id: string): void {
  void bank.selectQuestion(null)
  void router.replace({
    query: { ...route.query, tab: 'exam', chapter: undefined, section: id, question: undefined },
  })
}

function choose(id: string, kind: 'chapter' | 'section'): void {
  if (kind === 'section') selectSection(id)
  else selectChapter(id)
  const popover = sectionSwitcher.value
  if (popover) {
    popover.open = false
    popover.querySelector<HTMLElement>('summary')?.focus()
  }
}

function setStage(next: StageSelection): void {
  void router.replace({ query: { ...route.query, stage: next === 'all' ? undefined : next } })
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
    retry-label="重新读取"
    @retry="loadProfile"
  />
  <div
    v-else-if="profile"
    ref="layout"
    class="qb-skill-layout chapter-exam"
    :class="{ 'is-reading': bank.selectedQuestionId, 'is-skill-narrow': narrowRail, 'is-full': !showQuestionPane }"
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
        <details ref="sectionSwitcher" class="qb-label-popover qb-section-switcher">
          <summary>
            <span class="qb-switcher-current"><small>{{ currentChapter?.label ?? '教材章节' }}</small><b>{{ switcherLabel }}</b></span>
            <span class="qb-switcher-caret">▾</span>
          </summary>
          <div>
            <div class="qb-chapter-tree">
              <details
                v-for="chapter in profile.chapters"
                :key="chapter.id"
                :open="chapter.id === currentChapter?.id"
              >
                <summary><span>{{ chapter.label }}</span><small>主考 {{ chapter.totals.midterm.main + chapter.totals.final.main }}</small></summary>
                <button
                  type="button"
                  class="qb-tree-row"
                  :class="{ 'is-active': !selectedSection && chapter.id === currentChapter?.id }"
                  @click="choose(chapter.id, 'chapter')"
                >本章总览</button>
                <button
                  v-for="section in chapter.sections"
                  :key="section.id"
                  type="button"
                  class="qb-tree-row"
                  :class="{ 'is-active': section.id === selectedSection?.id }"
                  :disabled="!section.main_count"
                  @click="choose(section.id, 'section')"
                >{{ section.label }} <small>{{ section.main_count || '无主考题' }}</small></button>
              </details>
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
        <OverviewScopeBar v-if="selectedSection" />
        <div v-if="mergedSummary" class="cep-note">
          同源卷合并：{{ mergedSummary }}
          <details v-if="mergedTitles" class="cep-merged-details">
            <summary>查看</summary>
            <span>{{ mergedTitles }}</span>
          </details>
        </div>
      </div>
      <div class="cep-scroll">
        <ChapterExamSection
          v-if="selectedSection"
          :section="selectedSection"
          :stage="stage"
          :stages="stages"
          :mastery="masteryNodes"
          :mastery-state="masteryState"
          @cell="openCell"
        />
        <ChapterExamOverview
          v-else-if="currentChapter"
          :chapter="currentChapter"
          :stage="stage"
          :stages="stages"
          @section="selectSection"
          @cell="openCell"
        />
      </div>
    </aside>
    <main v-if="showQuestionPane" class="qb-question-pane qb-browse-pane">
      <header class="qb-target-heading">
        <div class="qb-target-heading__line">
          <h2>{{ paneTitle }}</h2>
          <span v-if="selectedSection && ledgerKind === 'default'" class="qb-target-count">共 {{ bank.total }} 题</span>
          <AppButton
            v-if="ledgerKind === 'cell'"
            variant="ghost"
            size="small"
            @click="backToDefault"
          >{{ selectedSection ? '回到典型题' : '回到本章总览' }}</AppButton>
        </div>
      </header>
      <QuestionLedger
        :question-labels="ledgerKind === 'default' ? typicalLabels : undefined"
        @skill="emit('skill', $event)"
      />
    </main>
  </div>
</template>
