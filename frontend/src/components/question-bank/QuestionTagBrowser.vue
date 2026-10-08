<script setup lang="ts">
import { ChevronDown } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { knowledgeLeafLabel, questionBankApi, type QuestionBankFacets, type QuestionBankFilters, type QuestionBankSort, type QuestionSkillIndex } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import { TAG_DIMENSIONS, chapterLabel, migrateTagQuery, normalizeChapters, parseTagSelection, selectionQuery, toggleChapter, type TagDimensionKey, type TagSelection } from './question-tag-filters'
import QuestionChapterTree from './QuestionChapterTree.vue'
import QuestionTagBar from './QuestionTagBar.vue'
import DifficultyRangeFilter from './DifficultyRangeFilter.vue'
import QuestionSortControl from './QuestionSortControl.vue'
import QuestionLedger from './QuestionLedger.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import StatePanel from '../design-system/StatePanel.vue'
import AppIconButton from '../design-system/AppIconButton.vue'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{ index: QuestionSkillIndex | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: [] }>()
const bank = useQuestionBankStore()
const scope = useCurriculumScopeStore()
const route = useRoute()
const router = useRouter()
const selection = computed(() => parseTagSelection(route.query))
const selectionKey = computed(() => JSON.stringify(selection.value))
const railOpen = ref(false)
const layout = ref<HTMLElement | null>(null)
const results = ref<HTMLElement | null>(null)
const notice = ref('')
const sourcePopover = ref<HTMLDetailsElement | null>(null)
const sourceSearchInput = ref<HTMLInputElement | null>(null)
const sourceSearch = ref('')
const facets = ref<QuestionBankFacets | null>(null)
const facetsError = ref('')
const filters = reactive<{ keyword: string; questionTypes: string[]; difficultyMin: number; difficultyMax: number; paper: string; collapse: boolean; progress: string; tagStatus: NonNullable<QuestionBankFilters['tagStatus']>; criteriaReview: boolean; questionSort: QuestionBankSort }>({ keyword: '', questionTypes: [], difficultyMin: 1, difficultyMax: 10,
  paper: '', collapse: true, progress: '', tagStatus: 'all', criteriaReview: false, questionSort: 'newest' })
const volumeLabel = computed(() => {
  const volume = scope.selectedVolume
  return volume ? `${volume.grade}${volume.semester.startsWith('上') ? '上册' : volume.semester.startsWith('下') ? '下册' : ''}` : ''
})
const chapters = computed(() => props.index?.chapters ?? [])
const railLabel = computed(() => {
  if (!selection.value.sections.length) return '全部章节'
  if (selection.value.sections.length > 1) return '多个章节'
  const id = selection.value.sections[0]
  const chapter = chapters.value.find(chapter => chapter.id === id || chapter.sections.some(section => section.id === id))
  return chapter?.label.match(/^第[^章]+章/)?.[0] ?? chapter?.label ?? '所选章节'
})
const duplicateCount = computed(() => bank.questions.reduce((count, question) => count + (question.duplicate_members?.length ?? 0), 0))
const sourcePapers = computed(() => bank.papers.filter(paper => paper.curriculum_volume_id === scope.selectedVolumeId))
const filteredSourcePapers = computed(() => {
  const needle = sourceSearch.value.trim().toLowerCase()
  return sourcePapers.value.filter(paper => !needle || (paper.title ?? '').toLowerCase().includes(needle))
})
const sourceSummary = computed(() => sourcePapers.value.find(paper => String(paper.id) === filters.paper)?.title ?? '指定试卷…')
const singleType = computed(() => selection.value.typeKeys.length === 1 ? props.index?.types?.find(type => type.value === selection.value.typeKeys[0]) : undefined)
function tagLabel(key: TagDimensionKey, value: string): string {
  if (key === 'typeKeys') return (props.index?.types?.find(type => type.value === value)?.label ?? value).replace(/^题型[·：:]\s*/, '')
  return key === 'knowledgePoints' ? knowledgeLeafLabel(value) : value
}
const legacyLabel = (key: string) => chapters.value.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).find(skill => skill.stable_key === key)?.display_name ?? key
const chips = computed(() => [
  ...selection.value.sections.map(value => ({ key: 'sections' as const, value, label: `章节：${chapterLabel(chapters.value, value)}` })),
  ...TAG_DIMENSIONS.flatMap(dimension => selection.value[dimension.key].map(value => ({ key: dimension.key, value, label: `${dimension.label}：${tagLabel(dimension.key, value)}` }))),
  ...selection.value.legacySkills.map(value => ({ key: 'legacySkills' as const, value, label: `技能：${legacyLabel(value)}（旧链接）` })),
])
function writeSelection(next: TagSelection) {
  bank.clearSelection()
  void bank.selectQuestion(null)
  const query = { ...route.query }
  delete query.question
  void router.replace({ query: selectionQuery(query, next) })
}
function toggleTag(key: TagDimensionKey, value: string) {
  notice.value = ''
  const current = selection.value[key]
  writeSelection({ ...selection.value, [key]: current.includes(value) ? current.filter(item => item !== value) : [...current, value] })
}
function removeChip(key: keyof TagSelection, value: string) {
  notice.value = ''
  writeSelection({ ...selection.value, [key]: selection.value[key].filter(item => item !== value) })
}
function chooseChapters(values: string[]) {
  notice.value = ''
  writeSelection({ ...selection.value, sections: normalizeChapters(chapters.value, values) })
}
function clearConditions() {
  notice.value = ''
  Object.assign(filters, { keyword: '', questionTypes: [], difficultyMin: 1, difficultyMax: 10, paper: '', progress: '', tagStatus: 'all', criteriaReview: false, questionSort: 'newest' })
  writeSelection(parseTagSelection({}))
}
function requestFilters(): QuestionBankFilters {
  const picked = selection.value
  return { page: 1, pageSize: 20, includeSkills: true, scopeMode: 'primary',
    curriculumVolumeIds: scope.selectedVolumeId ? [scope.selectedVolumeId] : [], curriculumSections: [...picked.sections], skillKeys: [...picked.legacySkills],
    typeKeys: [...picked.typeKeys], knowledgePoints: [...picked.knowledgePoints], abilities: [...picked.abilities], thoughts: [...picked.thoughts],
    methods: [...picked.methods], models: [...picked.models], specialTypes: [...picked.specialTypes], errorPatternCategories: [...picked.errorPatternCategories],
    keyword: filters.keyword, questionTypes: [...filters.questionTypes], difficultyMin: filters.difficultyMin, difficultyMax: filters.difficultyMax,
    paperIds: filters.paper ? [Number(filters.paper)] : [], collapseDuplicates: filters.collapse, teachingProgressChapter: filters.progress,
    tagStatus: filters.tagStatus, criteriaNeedsReview: filters.criteriaReview, sort: filters.questionSort }
}
let facetController: AbortController | null = null
let questionReadVersion: number | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let lastChapterKey: string | undefined
let pruneOnRead = false
async function reload() {
  clearTimeout(timer)
  if (!scope.selectedVolumeId || !props.index || (route.query.tab && route.query.tab !== 'tag' && route.query.tab !== 'skill')) return
  const migration = migrateTagQuery(route.query, props.index)
  if (migration.changed) {
    if (migration.notice) notice.value = migration.notice
    await router.replace({ query: migration.query })
    return
  }
  const chapterKey = JSON.stringify(selection.value.sections)
  if (lastChapterKey !== undefined && chapterKey !== lastChapterKey) pruneOnRead = true
  lastChapterKey = chapterKey
  const next = requestFilters()
  const reading = bank.loadQuestions(next)
  questionReadVersion = bank.questionReadVersion
  void reading.then(() => nextTick(() => { if (!bank.selectedQuestionId) results.value?.scrollTo?.({ top: 0 }) }))
  facetController?.abort()
  const controller = new AbortController()
  facetController = controller
  facetsError.value = ''
  try {
    const result = await questionBankApi.listFacets(next, controller.signal)
    if (controller.signal.aborted) return
    facets.value = result
    if (pruneOnRead) {
      const chapterFacets = selection.value.typeKeys.length || selection.value.knowledgePoints.length
        ? await questionBankApi.listFacets({ curriculumVolumeIds: next.curriculumVolumeIds,
          curriculumSections: next.curriculumSections, scopeMode: 'primary', difficultyMin: 1,
          difficultyMax: 10, collapseDuplicates: filters.collapse, tagStatus: 'all' }, controller.signal)
        : result
      if (controller.signal.aborted) return
      pruneOnRead = false
      const typeCounts = new Map(chapterFacets.task_types?.map(item => [item.value, item.count]) ?? [])
      const knowledgeCounts = new Map(chapterFacets.knowledge_points.map(item => [item.value, item.count]))
      const droppedTypes = selection.value.typeKeys.filter(value => !(typeCounts.get(value) ?? 0))
      const droppedKnowledge = selection.value.knowledgePoints.filter(value => !(knowledgeCounts.get(value) ?? 0))
      if (droppedTypes.length || droppedKnowledge.length) {
        notice.value = `已去掉在所选章节里没有题的条件：${[...droppedTypes.map(value => tagLabel('typeKeys', value)), ...droppedKnowledge.map(knowledgeLeafLabel)].join('、')}`
        writeSelection({ ...selection.value, typeKeys: selection.value.typeKeys.filter(value => !droppedTypes.includes(value)), knowledgePoints: selection.value.knowledgePoints.filter(value => !droppedKnowledge.includes(value)) })
      }
    }
  } catch { if (!controller.signal.aborted) facetsError.value = '题数暂时无法读取' }
}
watch([selectionKey, () => props.index, () => bank.readRevision, () => route.query.tab], () => { bank.clearSelection(); void reload() }, { immediate: true })
watch(filters, () => {
  bank.clearSelection()
  void bank.selectQuestion(null)
  clearTimeout(timer)
  timer = setTimeout(() => void reload(), 250)
})
function toggleChoice() {
  if (filters.questionTypes.some(type => /选择/.test(type))) filters.questionTypes = filters.questionTypes.filter(type => !/选择/.test(type))
  else filters.questionTypes.push('选择题', '多选题')
}
function toggleAnswerType(value: string) {
  filters.questionTypes = filters.questionTypes.includes(value) ? filters.questionTypes.filter(type => type !== value) : [...filters.questionTypes, value]
}
function onSourceToggle() {
  if (!sourcePopover.value?.open) return
  if (bank.papersState === 'idle') void bank.loadPapers()
  void nextTick(() => sourceSearchInput.value?.focus())
}
function chooseSource(value: string) {
  filters.paper = value
  sourceSearch.value = ''
  if (sourcePopover.value) { sourcePopover.value.open = false; sourcePopover.value.querySelector<HTMLElement>('summary')?.focus() }
}
function selectPage(event: Event) { if (event.target instanceof HTMLInputElement) bank.selectCurrentPage(event.target.checked) }
function closePopovers(event: PointerEvent | KeyboardEvent) {
  if (event instanceof KeyboardEvent && (event.key !== 'Escape' || event.defaultPrevented)) return
  for (const popover of layout.value?.querySelectorAll<HTMLDetailsElement>('details.qb-label-popover[open]') ?? []) {
    if (event.type === 'pointerdown' && event.target instanceof Node && popover.contains(event.target)) continue
    popover.open = false
    if (event instanceof KeyboardEvent) { popover.querySelector<HTMLElement>('summary')?.focus(); event.preventDefault(); break }
  }
}
onMounted(() => { document.addEventListener('pointerdown', closePopovers); document.addEventListener('keydown', closePopovers) })
onBeforeUnmount(() => {
  if (questionReadVersion !== undefined) bank.cancelQuestionRead(questionReadVersion)
  clearTimeout(timer); facetController?.abort()
  document.removeEventListener('pointerdown', closePopovers); document.removeEventListener('keydown', closePopovers)
})
</script>

<template>
  <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
  <StatePanel v-else-if="loading && !index" kind="loading" title="正在读取题目…" />
  <StatePanel v-else-if="error && !index" kind="error" :title="error" retry-label="重新加载" @retry="emit('retry')" />
  <div v-else-if="index" ref="layout" class="qb-tag-layout" :class="{ 'is-reading': bank.selectedQuestionId, 'is-chapter-narrow': bank.selectedQuestionId && !railOpen }">
    <aside class="qb-chapter-pane qb-browse-pane">
      <div v-if="bank.selectedQuestionId" class="qb-chapter-rail">
        <AppIconButton class="qb-rail-toggle" :label="railOpen ? '收窄章节列表' : `展开章节列表 · ${railLabel}`" icon="book-open" variant="secondary" :aria-expanded="railOpen" @click="railOpen = !railOpen" />
        <span v-if="!railOpen">{{ railLabel }}</span>
      </div>
      <QuestionChapterTree :chapters="index.chapters" :counts="facets?.primary_sections ?? []" :selection="selection.sections" :error="facetsError" @exclusive="chooseChapters([$event])" @toggle="chooseChapters(toggleChapter(chapters, selection.sections, $event))" @all="chooseChapters([])" @retry="reload" />
    </aside>
    <main ref="results" class="qb-tag-results qb-question-pane qb-browse-pane">
      <section class="qb-tag-panel" aria-label="筛选题目">
        <QuestionTagBar :index="index" :volume-label="volumeLabel" :facets="facets" :selection="selection" :error="facetsError" @toggle="toggleTag" @clear="writeSelection({ ...selection, [$event]: [] })" @retry="reload" />
        <FeedbackBanner v-if="notice" role="status" tone="warning" :description="notice" />
        <div class="qb-selected-conditions">
          <small>已选</small>
          <span v-if="!chips.length" class="qb-tag-hint">未选条件，显示本学期全部题目</span>
          <span v-for="chip in chips" :key="`${chip.key}:${chip.value}`" class="qb-selected-chip" :class="{ 'is-legacy': chip.key === 'legacySkills' }">
            {{ chip.label }}<button type="button" :aria-label="`删除${chip.label}`" @click="removeChip(chip.key, chip.value)">×</button>
          </span>
          <AppButton v-if="chips.length" variant="ghost" size="small" @click="clearConditions">清空条件</AppButton>
        </div>
        <details v-if="singleType" class="qb-type-knowledge"><summary>常涉及的知识点（按主题型题目统计）</summary><p v-for="point in singleType.common_knowledge_points" :key="point.key">{{ point.label }} · {{ point.question_count }} / {{ singleType.count }} 题</p></details>
        <div class="qb-permanent-filters">
          <div class="qb-filter-row"><small>作答形式</small><div class="qb-type-chips">
            <button type="button" class="qb-filter-chip" :class="{ 'is-active': !filters.questionTypes.length }" :aria-pressed="!filters.questionTypes.length" @click="filters.questionTypes = []">全部</button>
            <button type="button" class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.some(type => /选择/.test(type)) }" :aria-pressed="filters.questionTypes.some(type => /选择/.test(type))" @click="toggleChoice">选择</button>
            <button v-for="type in ['填空题', '解答题']" :key="type" type="button" class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.includes(type) }" :aria-pressed="filters.questionTypes.includes(type)" @click="toggleAnswerType(type)">{{ type.slice(0, 2) }}</button>
          </div></div>
          <div class="qb-filter-row"><small>难度</small><DifficultyRangeFilter v-model:min="filters.difficultyMin" v-model:max="filters.difficultyMax" compact /></div>
          <div class="qb-filter-row"><small>来源</small>
            <button type="button" class="qb-filter-chip" :class="{ 'is-active': !filters.paper }" :aria-pressed="!filters.paper" @click="chooseSource('')">全部</button>
            <details ref="sourcePopover" class="qb-label-popover qb-source-filter" @toggle="onSourceToggle"><summary :title="sourceSummary"><span class="qb-source-summary">{{ sourceSummary }}</span><ChevronDown class="qb-caret" aria-hidden="true" /></summary><div>
              <input ref="sourceSearchInput" v-model="sourceSearch" type="search" class="app-input" aria-label="筛选来源" placeholder="筛选来源">
              <button type="button" class="qb-source-row" :class="{ 'is-active': !filters.paper }" @click="chooseSource('')">全部来源</button>
              <div class="qb-source-list"><button v-for="paper in filteredSourcePapers" :key="paper.id" type="button" class="qb-source-row" :class="{ 'is-active': filters.paper === String(paper.id) }" :aria-pressed="filters.paper === String(paper.id)" :title="paper.title ?? undefined" @click="chooseSource(String(paper.id))"><span>{{ paper.title }}</span><small>{{ paper.question_count }} 题</small></button><p v-if="!filteredSourcePapers.length" class="qb-help">没有匹配的来源。</p></div>
            </div></details>
          </div>
        </div>
        <div class="qb-filter-line qb-tag-bottom">
          <input v-model="filters.keyword" type="search" class="app-input qb-stem-search" aria-label="搜题干" placeholder="搜题干">
          <label class="qb-progress-filter">教学进度<select v-model="filters.progress" class="app-input" aria-label="教学进度"><option value="">不限</option><option v-for="chapter in scope.selectedVolume?.chapters ?? []" :key="chapter.id" :value="chapter.id">已学到{{ chapter.label }}</option></select></label>
          <label class="qb-duplicate-toggle" :class="{ 'is-active': filters.collapse }"><input v-model="filters.collapse" type="checkbox">折叠重复题</label>
          <details class="qb-label-popover qb-more-filter"><summary>更多 <ChevronDown class="qb-caret" aria-hidden="true" /></summary><div>
            <label>标签完整度<select v-model="filters.tagStatus" class="app-input"><option value="all">全部</option><option value="tagged">完整</option><option value="untagged">待完善</option></select></label>
            <label><input v-model="filters.criteriaReview" type="checkbox">判定点待审核</label><label><input v-model="filters.questionTypes" type="checkbox" value="多选题">多选题</label>
            <QuestionSortControl v-model="filters.questionSort" />
            <label><input type="checkbox" :checked="bank.questions.length > 0 && bank.questions.every(question => bank.selectedQuestionIds.includes(question.id))" @change="selectPage">选择本页</label>
          </div></details>
          <span class="qb-result-total">{{ chips.length || filters.keyword || filters.questionTypes.length || filters.paper || filters.progress || filters.criteriaReview || filters.tagStatus !== 'all' || filters.difficultyMin !== 1 || filters.difficultyMax !== 10 ? '筛选结果' : '本学期全部题目' }} · 共 <b>{{ bank.total }}</b> 题<small v-if="filters.collapse && duplicateCount"> · 本页已折叠 {{ duplicateCount }} 道重复题</small></span>
        </div>
      </section>
      <StatePanel v-if="bank.listState === 'empty'" compact kind="empty" title="没有同时满足全部条件的题"><template #actions><AppButton variant="secondary" @click="clearConditions">清空条件</AppButton></template></StatePanel>
      <QuestionLedger v-else />
    </main>
  </div>
</template>
