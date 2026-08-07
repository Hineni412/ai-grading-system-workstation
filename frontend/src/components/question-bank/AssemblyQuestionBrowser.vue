<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import {
  questionBankApi,
  type CurriculumCatalog,
  type CurriculumChapter,
  type QuestionBankFacet,
  type QuestionBankFacets,
  type QuestionBankFilters,
  type QuestionBankListItem,
  type QuestionBankSort,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import DifficultyRangeFilter from './DifficultyRangeFilter.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import QuestionSortControl from './QuestionSortControl.vue'

const emit = defineEmits<{
  edit: []
}>()

const assembly = useAssemblyStore()
const questions = ref<QuestionBankListItem[]>([])
const total = ref(0)
const totalPages = ref(0)
const state = ref<'loading' | 'ready' | 'empty' | 'error'>('loading')
const baseFacetsState = ref<'loading' | 'ready' | 'error'>('loading')
const facetsState = ref<'loading' | 'ready' | 'error'>('loading')
const facets = ref<QuestionBankFacets>({
  exam_scopes: [],
  curriculum_sections: [],
  curriculum_chapters: [],
  knowledge_points: [],
  abilities: [],
  methods: [],
  thoughts: [],
  models: [],
  special_types: [],
  student_levels: [],
  teaching_stages: [],
  sub_skills: [],
  question_types: [],
  years: [],
  exam_types: [],
  grades: [],
})
const baseFacets = ref<QuestionBankFacets>({
  exam_scopes: [],
  curriculum_sections: [],
  curriculum_chapters: [],
  knowledge_points: [],
  abilities: [],
  methods: [],
  thoughts: [],
  models: [],
  special_types: [],
  student_levels: [],
  teaching_stages: [],
  sub_skills: [],
  question_types: [],
  years: [],
  exam_types: [],
  grades: [],
})
const catalog = ref<CurriculumCatalog | null>(null)
const catalogState = ref<'loading' | 'ready' | 'error'>('loading')
const selectedVolumeId = ref('')
const selectedChapterId = ref('')
const selectedSectionId = ref('')
const selectedHistoricalScope = ref('')
const expandedChapterIds = ref(new Set<string>())
const activeTagDimension = ref<TagArrayFilterKey>('knowledgePoints')
const expandedAnswers = ref(new Set<number>())
const similarSource = ref<QuestionBankListItem | null>(null)
const similarItems = ref<SimilarQuestionItem[]>([])
const similarState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const filters = reactive({
  page: 1,
  keyword: '',
  knowledgePoints: [] as string[],
  abilities: [] as string[],
  methods: [] as string[],
  thoughts: [] as string[],
  models: [] as string[],
  specialTypes: [] as string[],
  difficultyMin: 1,
  difficultyMax: 10,
  questionType: '',
  year: '',
  examType: '',
  grade: '',
  tagStatus: 'all' as 'all' | 'tagged' | 'untagged',
  sort: 'difficulty_desc' as QuestionBankSort,
})

type TextFilterKey =
  | 'keyword'
  | 'questionType'
  | 'year'
  | 'examType'
  | 'grade'

type TagArrayFilterKey =
  | 'knowledgePoints'
  | 'abilities'
  | 'methods'
  | 'thoughts'
  | 'models'
  | 'specialTypes'

type ActiveFilterKey = TextFilterKey | TagArrayFilterKey | 'scope' | 'tagStatus' | 'difficulty'

interface ActiveFilter {
  id: string
  key: ActiveFilterKey
  label: string
  value: string
}

interface TagFilterRow {
  key: TagArrayFilterKey
  label: string
  items: QuestionBankFacet[]
  visibleLimit: number
}

let questionAbortController: AbortController | null = null
let questionRequestSerial = 0
let facetsAbortController: AbortController | null = null
let facetsRequestSerial = 0

const currentVolume = computed(() => (
  catalog.value?.volumes.find((volume) => volume.id === selectedVolumeId.value) ?? null
))

const selectedChapter = computed<CurriculumChapter | null>(() => {
  for (const volume of catalog.value?.volumes ?? []) {
    const chapter = volume.chapters.find((item) => item.id === selectedChapterId.value)
    if (chapter) return chapter
  }
  return null
})

const selectedSection = computed(() => {
  for (const volume of catalog.value?.volumes ?? []) {
    for (const chapter of volume.chapters) {
      const section = chapter.sections.find((item) => item.id === selectedSectionId.value)
      if (section) return section
    }
  }
  return null
})

const selectedExamScopes = computed(() => {
  if (selectedChapter.value) return [...selectedChapter.value.exam_scope_values]
  if (selectedHistoricalScope.value) return [selectedHistoricalScope.value]
  return []
})

const selectedScopeLabel = computed(() => (
  selectedChapter.value
    ? `${currentVolume.value?.label ?? ''} ${selectedChapter.value.label}${selectedSection.value ? ` / ${selectedSection.value.label}` : ''}`.trim()
    : selectedHistoricalScope.value
))

const chapterCounts = computed(() => new Map(
  baseFacets.value.curriculum_chapters.map((item) => [item.value, item.count]),
))

const sectionCounts = computed(() => new Map(
  baseFacets.value.curriculum_sections.map((item) => [item.value, item.count]),
))

const recognizedExamScopes = computed(() => {
  const values = new Set<string>()
  for (const volume of catalog.value?.volumes ?? []) {
    for (const chapter of volume.chapters) {
      for (const value of chapter.exam_scope_values) values.add(value)
    }
  }
  return values
})

const historicalScopes = computed(() => (
  baseFacets.value.exam_scopes.filter((item) => !recognizedExamScopes.value.has(item.value))
))

const primaryTagRows = computed<TagFilterRow[]>(() => [
  {
    key: 'knowledgePoints',
    label: '知识点',
    items: facets.value.knowledge_points,
    visibleLimit: 12,
  },
  {
    key: 'abilities',
    label: '能力',
    items: facets.value.abilities,
    visibleLimit: 10,
  },
  {
    key: 'methods',
    label: '解题方法',
    items: facets.value.methods,
    visibleLimit: 10,
  },
  {
    key: 'thoughts',
    label: '数学思想',
    items: facets.value.thoughts,
    visibleLimit: 10,
  },
  {
    key: 'models',
    label: '模型',
    items: facets.value.models,
    visibleLimit: 10,
  },
  {
    key: 'specialTypes',
    label: '特殊题型/考法',
    items: facets.value.special_types,
    visibleLimit: 10,
  },
])

const activeTagRow = computed(() => (
  primaryTagRows.value.find((row) => row.key === activeTagDimension.value)
  ?? primaryTagRows.value[0]
  ?? null
))

const activeFilterLabels: Record<TagArrayFilterKey, string> = {
  knowledgePoints: '知识点',
  abilities: '能力',
  methods: '方法',
  thoughts: '数学思想',
  models: '模型',
  specialTypes: '特殊题型/考法',
}

const activeFilters = computed<ActiveFilter[]>(() => {
  const result: ActiveFilter[] = []
  if (filters.keyword.trim()) {
    result.push({
      id: `keyword:${filters.keyword.trim()}`,
      key: 'keyword',
      label: '关键词',
      value: filters.keyword.trim(),
    })
  }
  if (selectedScopeLabel.value) {
    result.push({
      id: `scope:${selectedSectionId.value || selectedChapterId.value || selectedHistoricalScope.value}`,
      key: 'scope',
      label: selectedChapter.value ? '教材章节' : '历史范围',
      value: selectedScopeLabel.value,
    })
  }
  if (filters.questionType) {
    result.push({
      id: `questionType:${filters.questionType}`,
      key: 'questionType',
      label: '题型',
      value: filters.questionType,
    })
  }
  for (const key of Object.keys(activeFilterLabels) as TagArrayFilterKey[]) {
    for (const value of filters[key]) {
      result.push({
        id: `${key}:${value}`,
        key,
        label: activeFilterLabels[key],
        value,
      })
    }
  }
  if (filters.year) {
    result.push({ id: `year:${filters.year}`, key: 'year', label: '年份', value: filters.year })
  }
  if (filters.examType) {
    result.push({
      id: `examType:${filters.examType}`,
      key: 'examType',
      label: '试卷类型',
      value: filters.examType,
    })
  }
  if (filters.grade) {
    result.push({ id: `grade:${filters.grade}`, key: 'grade', label: '年级', value: filters.grade })
  }
  if (filters.tagStatus !== 'all') {
    result.push({
      id: `tagStatus:${filters.tagStatus}`,
      key: 'tagStatus',
      label: '标签状态',
      value: filters.tagStatus === 'tagged' ? '核心标签完整' : '标签待完善',
    })
  }
  if (filters.difficultyMin !== 1 || filters.difficultyMax !== 10) {
    result.push({
      id: `difficulty:${filters.difficultyMin}-${filters.difficultyMax}`,
      key: 'difficulty',
      label: '难度',
      value: `${filters.difficultyMin}–${filters.difficultyMax}`,
    })
  }
  return result
})

const activeFilterCount = computed(() => activeFilters.value.length)
let secondaryLoadHandle: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  // The first question page is independent from the catalog and facet
  // aggregates. Start it immediately so slow taxonomy statistics never keep
  // teachers staring at an empty browser.
  const initialQuestions = loadQuestions(false, false)
  void Promise.all([
    assembly.loadState === 'idle' ? assembly.load() : Promise.resolve(),
    initialQuestions,
  ])
  // The five-volume curriculum payload and facet aggregates are useful for
  // filtering but not for the first question cards. Start them shortly after
  // the first page resolves so they cannot monopolize parsing/network slots
  // during repeated refreshes.
  void initialQuestions.finally(() => {
    secondaryLoadHandle = setTimeout(() => {
      secondaryLoadHandle = null
      void Promise.all([loadCatalog(), loadFacets()]).then(chooseInitialVolume)
    }, 500)
  })
})

onBeforeUnmount(() => {
  if (secondaryLoadHandle !== null) clearTimeout(secondaryLoadHandle)
  questionAbortController?.abort()
  facetsAbortController?.abort()
})

function queryFilters(): QuestionBankFilters {
  return {
    page: filters.page,
    pageSize: 12,
    keyword: filters.keyword,
    knowledgePoints: [...filters.knowledgePoints],
    abilities: [...filters.abilities],
    methods: [...filters.methods],
    thoughts: [...filters.thoughts],
    models: [...filters.models],
    specialTypes: [...filters.specialTypes],
    difficultyMin: filters.difficultyMin === 1 && filters.difficultyMax === 10
      ? undefined : filters.difficultyMin,
    difficultyMax: filters.difficultyMin === 1 && filters.difficultyMax === 10
      ? undefined : filters.difficultyMax,
    questionTypes: filters.questionType ? [filters.questionType] : [],
    years: filters.year ? [filters.year] : [],
    examTypes: filters.examType ? [filters.examType] : [],
    grades: filters.grade ? [filters.grade] : [],
    examScopes: selectedExamScopes.value,
    curriculumSections: selectedSectionId.value ? [selectedSectionId.value] : [],
    tagStatus: filters.tagStatus,
    sort: filters.sort,
  }
}

async function loadQuestions(
  resetPage = false,
  refreshFacets = true,
): Promise<void> {
  if (resetPage) filters.page = 1
  const requestFilters = queryFilters()
  if (refreshFacets) void loadFilteredFacets(requestFilters)
  questionAbortController?.abort()
  const controller = new AbortController()
  const requestSerial = ++questionRequestSerial
  questionAbortController = controller
  state.value = 'loading'
  try {
    const result = await questionBankApi.listQuestions(
      requestFilters,
      controller.signal,
    )
    if (controller.signal.aborted || requestSerial !== questionRequestSerial) return
    questions.value = result.items
    total.value = result.total
    totalPages.value = result.total_pages
    state.value = result.items.length ? 'ready' : 'empty'
  } catch {
    if (controller.signal.aborted || requestSerial !== questionRequestSerial) return
    state.value = 'error'
  } finally {
    if (requestSerial === questionRequestSerial) questionAbortController = null
  }
}

async function loadFilteredFacets(requestFilters: QuestionBankFilters): Promise<void> {
  facetsAbortController?.abort()
  const controller = new AbortController()
  const requestSerial = ++facetsRequestSerial
  facetsAbortController = controller
  facetsState.value = 'loading'
  try {
    const loaded = await questionBankApi.listFacets(
      requestFilters,
      controller.signal,
    )
    if (controller.signal.aborted || requestSerial !== facetsRequestSerial) return
    facets.value = loaded
    facetsState.value = 'ready'
  } catch {
    if (controller.signal.aborted || requestSerial !== facetsRequestSerial) return
    facetsState.value = 'error'
  } finally {
    if (requestSerial === facetsRequestSerial) facetsAbortController = null
  }
}

async function loadCatalog(): Promise<void> {
  catalogState.value = 'loading'
  try {
    catalog.value = await questionBankApi.getCurriculum(undefined, false)
    catalogState.value = 'ready'
  } catch {
    catalog.value = null
    catalogState.value = 'error'
  }
}

async function loadFacets(): Promise<void> {
  baseFacetsState.value = 'loading'
  facetsState.value = 'loading'
  try {
    const loaded = await questionBankApi.listFacets({ tagStatus: 'all' })
    baseFacets.value = loaded
    facets.value = loaded
    baseFacetsState.value = 'ready'
    facetsState.value = 'ready'
  } catch {
    baseFacetsState.value = 'error'
    facetsState.value = 'error'
  }
}

async function retryCurriculum(): Promise<void> {
  await Promise.all([loadCatalog(), loadFacets()])
  chooseInitialVolume()
}

function chooseInitialVolume(): void {
  if (selectedVolumeId.value || !catalog.value?.volumes.length) return
  const ranked = catalog.value.volumes.map((volume, index) => ({
    id: volume.id,
    index,
    count: volume.chapters.reduce(
      (sum, chapter) => sum + (chapterCounts.value.get(chapter.id) ?? 0),
      0,
    ),
  })).sort((left, right) => right.count - left.count || left.index - right.index)
  selectedVolumeId.value = ranked[0]?.id ?? catalog.value.volumes[0]?.id ?? ''
}

function chooseVolume(value: string): void {
  selectedVolumeId.value = value
  selectedChapterId.value = ''
  selectedSectionId.value = ''
  selectedHistoricalScope.value = ''
  expandedChapterIds.value = new Set()
  void loadQuestions(true)
}

function toggleChapter(chapterId: string): void {
  const next = new Set(expandedChapterIds.value)
  if (next.has(chapterId)) next.delete(chapterId)
  else next.add(chapterId)
  expandedChapterIds.value = next
}

function chooseChapter(chapter: CurriculumChapter): void {
  const clear = selectedChapterId.value === chapter.id && !selectedSectionId.value
  selectedChapterId.value = clear ? '' : chapter.id
  selectedSectionId.value = ''
  selectedHistoricalScope.value = ''
  if (!clear) {
    const next = new Set(expandedChapterIds.value)
    next.add(chapter.id)
    expandedChapterIds.value = next
  }
  void loadQuestions(true)
}

function chooseSection(chapter: CurriculumChapter, sectionId: string): void {
  const clear = selectedSectionId.value === sectionId
  selectedChapterId.value = clear ? '' : chapter.id
  selectedSectionId.value = clear ? '' : sectionId
  selectedHistoricalScope.value = ''
  void loadQuestions(true)
}

function clearScope(): void {
  selectedChapterId.value = ''
  selectedSectionId.value = ''
  selectedHistoricalScope.value = ''
  void loadQuestions(true)
}

function chooseHistoricalScope(value: string): void {
  const clear = selectedHistoricalScope.value === value
  selectedHistoricalScope.value = clear ? '' : value
  selectedChapterId.value = ''
  selectedSectionId.value = ''
  void loadQuestions(true)
}

function chooseTextFilter(key: Exclude<TextFilterKey, 'keyword'>, value: string): void {
  filters[key] = filters[key] === value ? '' : value
  void loadQuestions(true)
}

function toggleTagFilter(key: TagArrayFilterKey, value: string): void {
  const next = new Set(filters[key])
  if (next.has(value)) next.delete(value)
  else next.add(value)
  filters[key] = [...next]
  void loadQuestions(true)
}

function clearTagFilter(key: TagArrayFilterKey): void {
  if (filters[key].length === 0) return
  filters[key] = []
  void loadQuestions(true)
}

function chooseTagStatus(value: 'all' | 'tagged' | 'untagged'): void {
  filters.tagStatus = value
  void loadQuestions(true)
}

function clearActiveFilter(filter: ActiveFilter): void {
  if (filter.key === 'scope') {
    selectedChapterId.value = ''
    selectedSectionId.value = ''
    selectedHistoricalScope.value = ''
  } else if (filter.key === 'tagStatus') {
    filters.tagStatus = 'all'
  } else if (filter.key === 'difficulty') {
    filters.difficultyMin = 1
    filters.difficultyMax = 10
  } else if (filter.key in activeFilterLabels) {
    const key = filter.key as TagArrayFilterKey
    filters[key] = filters[key].filter((value) => value !== filter.value)
  } else {
    filters[filter.key as TextFilterKey] = ''
  }
  void loadQuestions(true)
}

function changePage(page: number): void {
  if (page < 1 || page > totalPages.value || page === filters.page) return
  filters.page = page
  void loadQuestions(false, false)
}

function resetFilters(): void {
  Object.assign(filters, {
    page: 1,
    keyword: '',
    knowledgePoints: [],
    abilities: [],
    methods: [],
    thoughts: [],
    models: [],
    specialTypes: [],
    difficultyMin: 1,
    difficultyMax: 10,
    questionType: '',
    year: '',
    examType: '',
    grade: '',
    tagStatus: 'all',
    sort: 'difficulty_desc',
  })
  selectedChapterId.value = ''
  selectedSectionId.value = ''
  selectedHistoricalScope.value = ''
  void loadQuestions()
}

function changeSort(): void {
  void loadQuestions(true, false)
}

function chapterCount(chapterId: string): number {
  return chapterCounts.value.get(chapterId) ?? 0
}

function sectionCount(sectionId: string): number {
  return sectionCounts.value.get(sectionId) ?? 0
}

function unassignedSectionCount(chapter: CurriculumChapter): number {
  const assigned = chapter.sections.reduce((sum, section) => sum + sectionCount(section.id), 0)
  return Math.max(0, chapterCount(chapter.id) - assigned)
}

function isInBasket(questionId: number): boolean {
  return assembly.draft.basket_ids.includes(questionId)
}

async function toggleBasket(questionId: number): Promise<void> {
  if (isInBasket(questionId)) {
    await assembly.removeQuestion(questionId)
  } else {
    await assembly.addQuestions([questionId])
  }
}

async function clearBasket(): Promise<void> {
  if (!assembly.selectedQuestionCount) return
  const confirmed = window.confirm('确认清空当前试卷篮吗？试卷标题和导出记录不会被删除。')
  if (!confirmed) return
  await assembly.save({
    ...assembly.draft,
    basket_ids: [],
    order_ids: [],
    sections: [],
  })
}

function toggleAnswer(questionId: number): void {
  const next = new Set(expandedAnswers.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedAnswers.value = next
}

async function openSimilar(question: QuestionBankListItem): Promise<void> {
  similarSource.value = question
  similarItems.value = []
  similarState.value = 'loading'
  try {
    similarItems.value = (await questionBankApi.listSimilar(question.id)).items
    similarState.value = 'ready'
  } catch {
    similarState.value = 'error'
  }
}

function closeSimilar(): void {
  similarSource.value = null
  similarItems.value = []
  similarState.value = 'idle'
}

function tagsFor(question: QuestionBankListItem, tagType: string): string[] {
  return question.tags
    .filter((tag) => tag.tag_type === tagType)
    .map((tag) => (
      tagType === 'knowledge_point'
        ? knowledgeLeafLabel(tag.tag_value)
        : tag.tag_value
    ))
}

function knowledgeLeafLabel(value: string): string {
  const parts = value.split(/[|｜]/).map((part) => part.trim()).filter(Boolean)
  return parts[parts.length - 1] ?? value
}
</script>

<template>
  <div class="assembly-browser">
    <aside class="assembly-chapters" aria-labelledby="assembly-chapter-title">
      <header>
        <div>
          <p class="assembly-kicker">CURRICULUM INDEX</p>
          <h2 id="assembly-chapter-title">教材章节</h2>
        </div>
        <button v-if="selectedScopeLabel" type="button" class="assembly-link" @click="clearScope">清除</button>
      </header>
      <label v-if="catalogState === 'ready' && catalog" class="assembly-volume-select">
        <span>教材册别</span>
        <select
          :value="selectedVolumeId"
          aria-label="选择教材册别"
          @change="chooseVolume(($event.currentTarget as HTMLSelectElement).value)"
        >
          <option v-for="volume in catalog.volumes" :key="volume.id" :value="volume.id">
            {{ volume.label }}
          </option>
        </select>
      </label>
      <p class="assembly-chapters__hint">
        北师大版 2024 目录保存在本机。章级兼容历史标签；小节未标定时不会猜测归属。
      </p>
      <div v-if="catalogState === 'loading' || baseFacetsState === 'loading'" class="assembly-compact-state">
        正在读取教材目录…
      </div>
      <div v-else-if="catalogState === 'error' || baseFacetsState === 'error'" class="assembly-compact-state">
        <span>教材目录或章节计数暂不可用</span>
        <button
          type="button"
          class="assembly-link"
          @click="retryCurriculum"
        >
          重试
        </button>
      </div>
      <nav v-else-if="currentVolume" class="assembly-curriculum-tree" aria-label="教材章节筛选">
        <button
          type="button"
          class="assembly-curriculum-tree__all"
          :class="{ 'is-active': !selectedScopeLabel }"
          @click="clearScope"
        >
          <span>不限定章节</span>
          <small>查看全部题目</small>
        </button>

        <div
          v-for="chapter in currentVolume.chapters"
          :key="chapter.id"
          class="assembly-curriculum-node"
        >
          <div class="assembly-curriculum-node__heading">
            <button
              type="button"
              class="assembly-curriculum-node__toggle"
              :aria-label="`${expandedChapterIds.has(chapter.id) ? '收起' : '展开'}${chapter.label}`"
              :aria-expanded="expandedChapterIds.has(chapter.id)"
              @click="toggleChapter(chapter.id)"
            >
              <span aria-hidden="true">›</span>
            </button>
            <button
              type="button"
              class="assembly-curriculum-node__chapter"
              :class="{ 'is-active': selectedChapterId === chapter.id }"
              :aria-pressed="selectedChapterId === chapter.id"
              @click="chooseChapter(chapter)"
            >
              <span>{{ chapter.label }}</span>
              <small>{{ chapterCount(chapter.id) }} 题</small>
            </button>
          </div>
          <ul v-if="expandedChapterIds.has(chapter.id)" class="assembly-curriculum-sections">
            <li class="assembly-curriculum-sections__status">
              其中 {{ unassignedSectionCount(chapter) }} 题待标定小节
            </li>
            <li v-for="section in chapter.sections" :key="section.id">
              <button
                type="button"
                :class="{ 'is-active': selectedSectionId === section.id }"
                :aria-pressed="selectedSectionId === section.id"
                @click="chooseSection(chapter, section.id)"
              >
                <span>{{ section.label }}</span>
                <small>{{ sectionCount(section.id) }} 题</small>
              </button>
            </li>
          </ul>
        </div>

        <details v-if="historicalScopes.length" class="assembly-historical-scopes">
          <summary>其他／历史范围（{{ historicalScopes.length }}）</summary>
          <div>
            <button
              v-for="item in historicalScopes"
              :key="item.value"
              type="button"
              :class="{ 'is-active': selectedHistoricalScope === item.value }"
              @click="chooseHistoricalScope(item.value)"
            >
              <span>{{ item.value }}</span><small>{{ item.count }}</small>
            </button>
          </div>
        </details>
      </nav>
    </aside>

    <main class="assembly-browser__main">
      <section class="assembly-filter-panel" aria-label="试题筛选">
        <header class="assembly-filter-panel__heading">
          <div>
            <p class="assembly-kicker">QUESTION LABELS</p>
            <h2>按标签筛题</h2>
            <p>同一行可多选、满足任一项；不同行同时满足。数字为当前题库标签计数。</p>
          </div>
          <strong v-if="activeFilterCount">{{ activeFilterCount }} 项已选</strong>
        </header>

        <div v-if="facetsState === 'loading'" class="assembly-filter-state" role="status">
          正在读取筛选标签…
        </div>
        <div v-else-if="facetsState === 'error'" class="assembly-filter-state" role="alert">
          <span>筛选标签暂时无法读取。</span>
          <button type="button" class="assembly-link" @click="loadFacets">重新读取</button>
        </div>
        <template v-else>
          <div class="assembly-filter-row">
            <span class="assembly-filter-label">题型</span>
            <div class="assembly-filter-chips">
              <button
                type="button"
                :class="{ 'is-active': !filters.questionType }"
                :aria-pressed="!filters.questionType"
                @click="chooseTextFilter('questionType', '')"
              >
                全部 <small>{{ total }}</small>
              </button>
              <button
                v-for="item in facets.question_types"
                :key="item.value"
                type="button"
                :class="{ 'is-active': filters.questionType === item.value }"
                :aria-pressed="filters.questionType === item.value"
                @click="chooseTextFilter('questionType', item.value)"
              >
                {{ item.value }} <small>{{ item.count }}</small>
              </button>
              <span v-if="facets.question_types.length === 0" class="assembly-filter-empty">暂无题型标签</span>
            </div>
          </div>

          <div class="assembly-tag-dimensions" role="tablist" aria-label="标签筛选维度">
            <button
              v-for="row in primaryTagRows"
              :key="row.key"
              type="button"
              role="tab"
              :aria-selected="activeTagDimension === row.key"
              :class="{ 'is-active': activeTagDimension === row.key }"
              @click="activeTagDimension = row.key"
            >
              {{ row.label }}
              <small v-if="filters[row.key].length">{{ filters[row.key].length }}</small>
            </button>
          </div>

          <div v-if="activeTagRow" class="assembly-filter-row is-tag-dimension">
            <span class="assembly-filter-label">{{ activeTagRow.label }}</span>
            <div class="assembly-filter-chips">
              <button
                type="button"
                :class="{ 'is-active': filters[activeTagRow.key].length === 0 }"
                :aria-pressed="filters[activeTagRow.key].length === 0"
                @click="clearTagFilter(activeTagRow.key)"
              >
                全部 <small>{{ total }}</small>
              </button>
              <button
                v-for="item in activeTagRow.items.slice(0, activeTagRow.visibleLimit)"
                :key="item.value"
                type="button"
                :class="{ 'is-active': filters[activeTagRow.key].includes(item.value) }"
                :aria-pressed="filters[activeTagRow.key].includes(item.value)"
                @click="toggleTagFilter(activeTagRow.key, item.value)"
              >
                <span :title="activeTagRow.key === 'knowledgePoints' ? item.value : undefined">
                  {{ activeTagRow.key === 'knowledgePoints' ? knowledgeLeafLabel(item.value) : item.value }}
                </span> <small>{{ item.count }}</small>
              </button>
              <details
                v-if="activeTagRow.items.length > activeTagRow.visibleLimit"
                class="assembly-filter-more"
              >
                <summary>
                  更多{{ activeTagRow.label }}（{{ activeTagRow.items.length - activeTagRow.visibleLimit }}）
                </summary>
                <div>
                  <button
                    v-for="item in activeTagRow.items.slice(activeTagRow.visibleLimit)"
                    :key="item.value"
                    type="button"
                    :class="{ 'is-active': filters[activeTagRow.key].includes(item.value) }"
                    :aria-pressed="filters[activeTagRow.key].includes(item.value)"
                    @click="toggleTagFilter(activeTagRow.key, item.value)"
                  >
                    <span :title="activeTagRow.key === 'knowledgePoints' ? item.value : undefined">
                      {{ activeTagRow.key === 'knowledgePoints' ? knowledgeLeafLabel(item.value) : item.value }}
                    </span> <small>{{ item.count }}</small>
                  </button>
                </div>
              </details>
              <span v-if="activeTagRow.items.length === 0" class="assembly-filter-empty">
                暂无{{ activeTagRow.label }}标签
              </span>
            </div>
          </div>

          <div class="assembly-filter-row is-difficulty">
            <DifficultyRangeFilter
              v-model:min="filters.difficultyMin"
              v-model:max="filters.difficultyMax"
              @change="loadQuestions(true)"
            />
          </div>

          <details class="assembly-filter-groups">
            <summary>更多筛选：试卷来源与标注状态</summary>
            <div class="assembly-filter-groups__body">
              <div class="assembly-filter-row">
                <span class="assembly-filter-label">年份</span>
                <div class="assembly-filter-chips">
                  <button type="button" :class="{ 'is-active': !filters.year }" @click="chooseTextFilter('year', '')">
                    全部 <small>{{ total }}</small>
                  </button>
                  <button
                    v-for="item in facets.years"
                    :key="item.value"
                    type="button"
                    :class="{ 'is-active': filters.year === item.value }"
                    @click="chooseTextFilter('year', item.value)"
                  >
                    {{ item.value }} <small>{{ item.count }}</small>
                  </button>
                </div>
              </div>

              <div class="assembly-filter-row">
                <span class="assembly-filter-label">试卷类型</span>
                <div class="assembly-filter-chips">
                  <button type="button" :class="{ 'is-active': !filters.examType }" @click="chooseTextFilter('examType', '')">
                    全部 <small>{{ total }}</small>
                  </button>
                  <button
                    v-for="item in facets.exam_types"
                    :key="item.value"
                    type="button"
                    :class="{ 'is-active': filters.examType === item.value }"
                    @click="chooseTextFilter('examType', item.value)"
                  >
                    {{ item.value }} <small>{{ item.count }}</small>
                  </button>
                </div>
              </div>

              <div class="assembly-filter-row">
                <span class="assembly-filter-label">年级</span>
                <div class="assembly-filter-chips">
                  <button type="button" :class="{ 'is-active': !filters.grade }" @click="chooseTextFilter('grade', '')">
                    全部 <small>{{ total }}</small>
                  </button>
                  <button
                    v-for="item in facets.grades"
                    :key="item.value"
                    type="button"
                    :class="{ 'is-active': filters.grade === item.value }"
                    @click="chooseTextFilter('grade', item.value)"
                  >
                    {{ item.value }} <small>{{ item.count }}</small>
                  </button>
                </div>
              </div>

              <div class="assembly-filter-row">
                <span class="assembly-filter-label">标注状态</span>
                <div class="assembly-filter-chips">
                  <button
                    type="button"
                    :class="{ 'is-active': filters.tagStatus === 'all' }"
                    @click="chooseTagStatus('all')"
                  >
                    全部
                  </button>
                  <button
                    type="button"
                    :class="{ 'is-active': filters.tagStatus === 'tagged' }"
                    @click="chooseTagStatus('tagged')"
                  >
                    核心标签完整
                  </button>
                  <button
                    type="button"
                    :class="{ 'is-active': filters.tagStatus === 'untagged' }"
                    @click="chooseTagStatus('untagged')"
                  >
                    标签待完善
                  </button>
                </div>
              </div>
            </div>
          </details>
        </template>

        <div v-if="activeFilters.length" class="assembly-active-filters" aria-label="已选筛选条件">
          <span>已选条件</span>
          <button
            v-for="filter in activeFilters"
            :key="filter.id"
            type="button"
            :aria-label="`清除${filter.label}：${filter.value}`"
            @click="clearActiveFilter(filter)"
          >
            <small>{{ filter.label }}</small>
            {{ filter.key === 'knowledgePoints' ? knowledgeLeafLabel(filter.value) : filter.value }}
            <b aria-hidden="true">×</b>
          </button>
          <button type="button" class="assembly-active-filters__clear" @click="resetFilters">
            清除全部
          </button>
        </div>

        <div class="assembly-filter-row is-search">
          <span class="assembly-filter-label">搜索</span>
          <input v-model="filters.keyword" type="search" placeholder="输入试题关键词" @keyup.enter="loadQuestions(true)">
          <QuestionSortControl v-model="filters.sort" @change="changeSort" />
          <button type="button" class="assembly-button is-primary" @click="loadQuestions(true)">搜索</button>
        </div>
      </section>

      <div class="assembly-results-heading">
        <div>
          <h2>题库试题</h2>
          <p>共 {{ total }} 道；加入后会立即保存到本机试卷篮。</p>
        </div>
        <span v-if="selectedScopeLabel">{{ selectedScopeLabel }}</span>
      </div>

      <div v-if="state === 'loading'" class="assembly-state" role="status">正在读取试题…</div>
      <div v-else-if="state === 'error'" class="assembly-state" role="alert">
        <span>试题暂时无法读取。</span>
        <button type="button" class="assembly-link" @click="loadQuestions()">重新读取</button>
      </div>
      <div v-else-if="state === 'empty'" class="assembly-state">
        当前筛选下没有试题，可以清除筛选后再查看。
      </div>
      <div v-else class="assembly-question-results">
        <article
          v-for="question in questions"
          :key="question.id"
          class="assembly-result-card"
          :class="{ 'is-in-basket': isInBasket(question.id) }"
        >
          <header>
            <div>
              <strong>第 {{ question.question_number || question.id }} 题</strong>
              <span>{{ question.paper_title || '未命名试卷' }}</span>
            </div>
            <span>{{ [question.year, question.exam_type, question.grade].filter(Boolean).join(' · ') }}</span>
          </header>

          <div class="assembly-result-card__content">
            <QuestionContentRenderer
              :blocks="question.rich_content?.question_blocks"
              :fallback="question.question_text"
              image-alt="题目配图"
              media-mode="list"
              paper-media-flow
              dense
            />
          </div>

          <div class="assembly-result-card__meta">
            <span>{{ question.question_type || '未分类' }}</span>
            <span>难度 {{ question.difficulty || '待定' }}</span>
            <span v-for="tag in tagsFor(question, 'knowledge_point').slice(0, 3)" :key="tag">{{ tag }}</span>
          </div>

          <div v-if="expandedAnswers.has(question.id)" class="assembly-result-card__answer">
            <strong>答案与解析</strong>
            <QuestionContentRenderer
              :blocks="question.rich_content?.answer_blocks"
              :fallback="question.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
              media-mode="detail"
              compact
            />
          </div>

          <footer>
            <span>
              {{ question.tags.length }} 个标签
              <template v-if="question.has_images"> · 含图片</template>
            </span>
            <span>
              <button type="button" class="assembly-link" @click="toggleAnswer(question.id)">
                {{ expandedAnswers.has(question.id) ? '收起解析' : '查看解析' }}
              </button>
              <button type="button" class="assembly-link" @click="openSimilar(question)">相似题</button>
              <button
                type="button"
                class="assembly-button"
                :class="{ 'is-selected': isInBasket(question.id) }"
                :disabled="assembly.saveState === 'saving'"
                @click="toggleBasket(question.id)"
              >
                {{ isInBasket(question.id) ? '移出试卷篮' : '加入试卷篮' }}
              </button>
            </span>
          </footer>
        </article>
      </div>

      <footer v-if="totalPages > 1" class="assembly-pagination">
        <button type="button" class="assembly-button" :disabled="filters.page <= 1" @click="changePage(filters.page - 1)">上一页</button>
        <span>第 {{ filters.page }} / {{ totalPages }} 页</span>
        <button type="button" class="assembly-button" :disabled="filters.page >= totalPages" @click="changePage(filters.page + 1)">下一页</button>
      </footer>
    </main>

    <aside class="assembly-basket" aria-labelledby="assembly-basket-title">
      <header>
        <div>
          <p class="assembly-kicker">PAPER BASKET</p>
          <h2 id="assembly-basket-title">试卷篮</h2>
        </div>
        <strong>{{ assembly.selectedQuestionCount }}</strong>
      </header>
      <div class="assembly-basket__summary">
        <span><strong>{{ assembly.selectedQuestionCount }}</strong> 道题</span>
        <span><strong>{{ assembly.totalScore }}</strong> 已识别分值</span>
      </div>
      <ol v-if="assembly.orderedQuestions.length" class="assembly-basket__list">
        <li v-for="question in assembly.orderedQuestions.slice(0, 12)" :key="question.id">
          <span>
            <strong>第 {{ question.question_number || question.id }} 题</strong>
            <small>{{ question.question_type || '未分类' }} · {{ question.paper_title || '未命名试卷' }}</small>
          </span>
          <button type="button" aria-label="移出试卷篮" @click="assembly.removeQuestion(question.id)">×</button>
        </li>
      </ol>
      <p v-else class="assembly-basket__empty">从左侧题卡加入试题，这里会即时显示。</p>
      <p v-if="assembly.orderedQuestions.length > 12" class="assembly-basket__more">
        另有 {{ assembly.orderedQuestions.length - 12 }} 道题
      </p>
      <footer>
        <button type="button" class="assembly-button" :disabled="!assembly.selectedQuestionCount" @click="clearBasket">清空</button>
        <button
          type="button"
          class="assembly-button is-primary"
          :disabled="!assembly.selectedQuestionCount"
          @click="emit('edit')"
        >
          进入编辑与导出
        </button>
      </footer>
    </aside>
  </div>

  <Teleport to="body">
    <div v-if="similarSource" class="assembly-drawer-layer" role="presentation" @click.self="closeSimilar">
      <aside class="assembly-similar-drawer" role="dialog" aria-modal="true" aria-labelledby="assembly-similar-title">
        <header>
          <div>
            <p class="assembly-kicker">LOCAL SIMILARITY</p>
            <h2 id="assembly-similar-title">相似题推荐</h2>
            <p>保留原题浏览位置；推荐在本机完成，不会产生模型费用。</p>
          </div>
          <button type="button" class="assembly-drawer-close" aria-label="关闭相似题" @click="closeSimilar">×</button>
        </header>
        <div v-if="similarState === 'loading'" class="assembly-state">正在查找相似题…</div>
        <div v-else-if="similarState === 'error'" class="assembly-state">
          <span>相似题暂时无法读取。</span>
          <button type="button" class="assembly-link" @click="openSimilar(similarSource)">重新查找</button>
        </div>
        <div v-else-if="similarItems.length === 0" class="assembly-state">当前题库没有找到足够相似的题目。</div>
        <div v-else class="assembly-similar-list">
          <article v-for="item in similarItems" :key="item.id">
            <div class="assembly-similar-score">
              <strong>{{ Math.round(item.similarity_score * 100) }}%</strong>
              <span>{{ item.similarity_reasons.join(' · ') || '内容相近' }}</span>
            </div>
            <QuestionContentRenderer
              :blocks="item.rich_content?.question_blocks"
              :fallback="item.question_text"
              image-alt="相似题配图"
              media-mode="list"
              paper-media-flow
              dense
            />
            <footer>
              <span>{{ item.paper_title || '未命名试卷' }}</span>
              <button
                type="button"
                class="assembly-button"
                :class="{ 'is-selected': isInBasket(item.id) }"
                @click="toggleBasket(item.id)"
              >
                {{ isInBasket(item.id) ? '移出试卷篮' : '加入试卷篮' }}
              </button>
            </footer>
          </article>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
