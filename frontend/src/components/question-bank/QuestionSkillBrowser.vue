<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { knowledgeLeafLabel, questionBankApi, type QuestionBankFacets, type QuestionBankFilters, type QuestionBankSort, type QuestionSkillIndex, type QuestionSkillEntry, type QuestionTopicEntry } from '../../api/question-bank'
import { trainingApi, type TrainingOverviewNode } from '../../api/training'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import DifficultyRangeFilter from './DifficultyRangeFilter.vue'
import QuestionSortControl from './QuestionSortControl.vue'
import QuestionLedger from './QuestionLedger.vue'
import StatePanel from '../design-system/StatePanel.vue'
import AppIconButton from '../design-system/AppIconButton.vue'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{ index: QuestionSkillIndex | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: []; skill: [key: string]; repair: [] }>()
const bank = useQuestionBankStore()
const scope = useCurriculumScopeStore()
const route = useRoute()
const router = useRouter()
const skillRailOpen = ref(false)
const layout = ref<HTMLElement | null>(null)
const sectionSwitcher = ref<HTMLDetailsElement | null>(null)
const sourcePopover = ref<HTMLDetailsElement | null>(null)
const sourceSearchInput = ref<HTMLInputElement | null>(null)
const sourceSearch = ref('')
const sort = ref('curriculum')
const facetTab = ref<'abilities' | 'methods' | 'models' | 'thoughts' | 'specialTypes'>('abilities')
const overview = ref<TrainingOverviewNode[]>([])
const mastery = computed(() => new Map(overview.value.map(node => [node.knowledge_key, node])))
let overviewController: AbortController | null = null
let idleTimer: ReturnType<typeof setTimeout> | undefined
const mode = computed(() => route.query.tagDim ? 'tag' : route.query.topic ? 'topic' : 'skill')
const queryText = (key: string) => typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const unlinked = computed(() => queryText('skill') === 'unlinked')
const sections = computed(() => props.index?.chapters.flatMap(chapter => chapter.sections) ?? [])
const sectionId = computed(() => queryText('section') || sections.value.find(section => section.skills.length)?.id || sections.value[0]?.id || '')
const selectedSection = computed(() => sections.value.find(section => section.id === sectionId.value))
const selectedChapter = computed(() => props.index?.chapters.find(chapter => chapter.id === sectionId.value))
const currentChapter = computed(() => selectedChapter.value ?? props.index?.chapters.find(chapter => chapter.sections.some(section => section.id === sectionId.value)))
type Entry = QuestionSkillEntry | QuestionTopicEntry
const allSkills = computed(() => props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]) ?? [])
const allTopics = computed(() => sections.value.flatMap(section => section.topics))
const selected = computed<Entry | undefined>(() => {
  if (unlinked.value || mode.value === 'tag') return undefined
  const rows = mode.value === 'topic' ? allTopics.value : allSkills.value
  const key = queryText(mode.value === 'topic' ? 'topic' : 'skill')
  return key ? rows.find(row => row.stable_key === key) : (mode.value === 'topic' ? selectedSection.value?.topics[0] : selectedSection.value?.skills[0] ?? selectedChapter.value?.cross_section_skills[0])
})
const entries = computed<Entry[]>(() => {
  const rows: Entry[] = mode.value === 'topic' ? selectedSection.value?.topics ?? []
    : selectedSection.value?.skills ?? selectedChapter.value?.cross_section_skills ?? []
  if (sort.value === 'count') return [...rows].sort((a, b) => b.question_count - a.question_count)
  if (sort.value === 'mastery' && overview.value.length) return [...rows].sort((a, b) => (mastery.value.get(a.stable_key)?.group_mastery ?? 2) - (mastery.value.get(b.stable_key)?.group_mastery ?? 2))
  return rows
})
const filters = reactive({ keyword: '', questionTypes: [] as string[], difficultyMin: 1, difficultyMax: 10,
  paper: '', collapse: true, progress: '', tagStatus: 'all' as QuestionBankFilters['tagStatus'], criteriaReview: false,
  abilities: [] as string[], methods: [] as string[], models: [] as string[], thoughts: [] as string[], specialTypes: [] as string[], questionSort: 'newest' as QuestionBankSort })
const facetDimensions = [{ key: 'abilities', label: '能力' }, { key: 'methods', label: '方法' }, { key: 'models', label: '模型' }, { key: 'thoughts', label: '思想' }, { key: 'specialTypes', label: '特殊考法' }] as const
const facets = ref<QuestionBankFacets | null>(null)
const facetsError = ref('')
const tagDimensions = [{ key: 'specialTypes', label: '特殊考法' }, { key: 'thoughts', label: '思想' }, { key: 'methods', label: '方法' }, { key: 'models', label: '模型' }, { key: 'knowledgePoints', label: '知识点' }, { key: 'errorPatternCategories', label: '错因' }] as const
type TagDimensionKey = typeof tagDimensions[number]['key']
const tagFacetFields: Record<TagDimensionKey, keyof QuestionBankFacets> = { specialTypes: 'special_types', thoughts: 'thoughts', methods: 'methods', models: 'models', knowledgePoints: 'knowledge_points', errorPatternCategories: 'error_pattern_categories' }
const tagDimension = computed(() => tagDimensions.find(dimension => dimension.key === queryText('tagDim')) ?? tagDimensions[0])
const tagValue = computed(() => mode.value === 'tag' ? queryText('tag') : '')
const tagScope = computed(() => {
  if (tagDimension.value.key === 'knowledgePoints') return 'volume'
  const requested = queryText('scope')
  if (requested === 'section') return selectedSection.value ? 'section' : currentChapter.value ? 'chapter' : 'volume'
  if (requested === 'chapter' && currentChapter.value) return 'chapter'
  return 'volume'
})
const tagSearch = ref('')
const tagRowLabel = (value: string) => tagDimension.value.key === 'knowledgePoints' ? knowledgeLeafLabel(value) : value
const tagItems = computed(() => facets.value?.[tagFacetFields[tagDimension.value.key]] ?? [])
const tagRows = computed(() => {
  const needle = tagSearch.value.trim()
  return tagItems.value.filter(item => !needle || tagRowLabel(item.value).includes(needle) || item.value.includes(needle))
})
const tagScopeLabel = computed(() => tagScope.value === 'section' ? (selectedSection.value?.label ?? '整个学期') : tagScope.value === 'chapter' ? (currentChapter.value?.label ?? '整个学期') : '整个学期')
const tagHeading = computed(() => tagValue.value ? `${tagDimension.value.label}：${tagRowLabel(tagValue.value)}` : '请选择标签')
const middleSubline = computed(() => mode.value === 'tag' ? `${tagScopeLabel.value} · ${tagItems.value.length} 个标签` : `${entries.value.length} 项${mode.value === 'skill' ? '技能' : '主题'} · ${selectedSection.value?.question_count ?? selectedChapter.value?.question_count ?? 0} 题`)
const tagContext = computed(() => mode.value === 'tag' ? `${tagDimension.value.key}|${tagValue.value}|${tagScope.value}|${sectionId.value}` : '')
const duplicateCount = computed(() => bank.questions.reduce((count, question) => count + (question.duplicate_members?.length ?? 0), 0))
const unlinkedCount = computed(() => (props.index?.unlinked.no_usable_evidence ?? 0) + (props.index?.unlinked.no_skill_link ?? 0))
const switcherChapterLabel = computed(() => currentChapter.value?.label ?? scope.selectedVolume?.label ?? '教材章节')
const switcherLabel = computed(() => selectedSection.value?.label || (selectedChapter.value ? `${selectedChapter.value.label} · 跨小节` : '教材章节'))
const sourcePapers = computed(() => bank.papers.filter(paper => paper.curriculum_volume_id === scope.selectedVolumeId))
const filteredSourcePapers = computed(() => {
  const needle = sourceSearch.value.trim().toLowerCase()
  return needle ? sourcePapers.value.filter(paper => (paper.title ?? '').toLowerCase().includes(needle)) : sourcePapers.value
})
const sourceSummary = computed(() => {
  if (!filters.paper) return '来源：全部'
  return sourcePapers.value.find(paper => String(paper.id) === filters.paper)?.title ?? '来源：全部'
})
let facetController: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
function requestFilters(): QuestionBankFilters {
  const entry = selected.value
  const result: QuestionBankFilters = { page: 1, pageSize: 20, includeSkills: true, skillKeys: mode.value === 'skill' && entry ? [entry.stable_key] : [],
    skillUnlinked: unlinked.value, knowledgePoints: entry && 'filter_value' in entry ? [entry.filter_value] : [],
    curriculumVolumeIds: scope.selectedVolumeId ? [scope.selectedVolumeId] : [],
    keyword: filters.keyword, questionTypes: [...filters.questionTypes], difficultyMin: filters.difficultyMin, difficultyMax: filters.difficultyMax,
    paperIds: filters.paper ? [Number(filters.paper)] : [], collapseDuplicates: filters.collapse, teachingProgressChapter: filters.progress,
    tagStatus: filters.tagStatus, criteriaNeedsReview: filters.criteriaReview, sort: filters.questionSort,
    abilities: [...filters.abilities], methods: [...filters.methods], models: [...filters.models], thoughts: [...filters.thoughts], specialTypes: [...filters.specialTypes] }
  if (mode.value === 'tag') {
    if (tagDimension.value.key === 'knowledgePoints') {
      result.scopeMode = 'any'
      result.curriculumSections = []
    } else {
      result.curriculumSections = tagScope.value === 'section' && selectedSection.value ? [selectedSection.value.id]
        : tagScope.value === 'chapter' && currentChapter.value ? [currentChapter.value.id] : []
    }
    if (tagValue.value) result[tagDimension.value.key] = [...new Set([...(result[tagDimension.value.key] ?? []), tagValue.value])]
  }
  return result
}
async function reload() {
  if (!props.index || (mode.value !== 'tag' && !selected.value && !unlinked.value)) { bank.questions = []; bank.total = 0; bank.listState = 'empty'; return }
  const next = requestFilters()
  if (mode.value === 'tag' && !tagValue.value) { bank.questions = []; bank.total = 0; bank.listState = 'empty' }
  else void bank.loadQuestions(next).then(async () => {
    await nextTick()
    if (!bank.selectedQuestionId) layout.value?.querySelector('.qb-question-list')?.scrollTo?.({ top: 0 })
  })
  facetController?.abort()
  const controller = new AbortController()
  facetController = controller
  facetsError.value = ''
  try { const result = await questionBankApi.listFacets(next, controller.signal); if (!controller.signal.aborted) facets.value = result }
  catch { if (!controller.signal.aborted) facetsError.value = '标签计数暂时无法读取'; }
}
watch([selected, unlinked, () => props.index, tagContext], () => { bank.clearSelection(); void reload() }, { immediate: true })
watch(filters, () => { clearTimeout(timer); timer = setTimeout(() => void reload(), 250) })
function selectSection(id: string) {
  void bank.selectQuestion(null)
  void router.replace({ query: mode.value === 'tag' ? tagQuery({ section: id }) : { tab: 'skill', section: id } })
}
function chooseSection(id: string) {
  selectSection(id)
  const popover = sectionSwitcher.value
  if (popover) {
    popover.open = false
    popover.querySelector<HTMLElement>('summary')?.focus()
  }
}
function onSourceToggle() {
  if (!sourcePopover.value?.open) return
  if (bank.papersState === 'idle') void bank.loadPapers()
  void nextTick(() => sourceSearchInput.value?.focus())
}
function chooseSource(id: string) {
  filters.paper = id
  sourceSearch.value = ''
  const popover = sourcePopover.value
  if (popover) {
    popover.open = false
    popover.querySelector<HTMLElement>('summary')?.focus()
  }
}
function closePopoverOnPointerDown(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  for (const popover of layout.value?.querySelectorAll<HTMLDetailsElement>('details.qb-label-popover[open]') ?? []) {
    if (!popover.contains(target)) popover.open = false
  }
}
function closePopoverOnEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented) return
  const popover = layout.value?.querySelector<HTMLDetailsElement>('details.qb-label-popover[open]')
  if (!popover) return
  popover.open = false
  popover.querySelector<HTMLElement>('summary')?.focus()
  event.preventDefault()
}
function selectEntry(entry: Entry) {
  void bank.selectQuestion(null)
  void router.replace({ query: { tab: 'skill', section: sectionId.value, ...('filter_value' in entry ? { topic: entry.stable_key } : { skill: entry.stable_key }) } })
}
function setMode(next: 'skill' | 'topic' | 'tag') {
  if (next === 'tag') { void router.replace({ query: { tab: 'skill', section: sectionId.value, tagDim: 'specialTypes' } }); return }
  const first = next === 'topic' ? selectedSection.value?.topics[0] : selectedSection.value?.skills[0]
  void router.replace({ query: { tab: 'skill', section: sectionId.value, ...(next === 'topic' ? { topic: first?.stable_key || 'none' } : {}) } })
}
function tagQuery(patch: { section?: string; tagDim?: TagDimensionKey; tag?: string | null; scope?: string | null } = {}) {
  const query: Record<string, string> = { tab: 'skill', section: patch.section ?? sectionId.value, tagDim: patch.tagDim ?? tagDimension.value.key }
  const tag = patch.tag === undefined ? tagValue.value : patch.tag
  const scopeValue = patch.scope === undefined ? queryText('scope') : patch.scope
  if (tag) query.tag = tag
  if (scopeValue) query.scope = scopeValue
  return query
}
function selectTag(value: string) { void bank.selectQuestion(null); void router.replace({ query: tagQuery({ tag: value }) }) }
function setTagDimension(key: TagDimensionKey) { void bank.selectQuestion(null); void router.replace({ query: tagQuery({ tagDim: key, tag: null }) }) }
function setTagScope(value: 'volume' | 'chapter' | 'section') { void bank.selectQuestion(null); void router.replace({ query: tagQuery({ scope: value === 'volume' ? null : value }) }) }
function toggleType(value: string) { const i = filters.questionTypes.indexOf(value); if (i < 0) filters.questionTypes.push(value); else filters.questionTypes.splice(i, 1) }
function toggleChoice() {
  if (filters.questionTypes.some(type => /选择/.test(type))) filters.questionTypes = filters.questionTypes.filter(type => !/选择/.test(type))
  else filters.questionTypes.push('选择题', '多选题')
}
function toggleFacet(key: typeof facetTab.value, value: string) { const i = filters[key].indexOf(value); if (i < 0) filters[key].push(value); else filters[key].splice(i, 1) }
function facetItems(key: typeof facetDimensions[number]['key']) { return facets.value?.[key === 'specialTypes' ? 'special_types' : key] ?? [] }
async function loadMastery() {
  if (!scope.selectedVolumeId) return
  overviewController?.abort()
  const controller = new AbortController()
  overviewController = controller
  try {
    const result = await trainingApi.overview({ scope: { mode: 'all', student_ids: [] }, exam_scope: { mode: 'semester', session_ids: [], curriculum_volume_id: scope.selectedVolumeId }, include_student_detail: false }, controller.signal)
    if (!controller.signal.aborted) overview.value = result.nodes
  } catch { if (!controller.signal.aborted) { overview.value = []; sort.value = 'curriculum' } }
}
watch(() => scope.selectedVolumeId, () => { overviewController?.abort(); overview.value = []; sort.value = 'curriculum'; clearTimeout(idleTimer); idleTimer = setTimeout(() => void loadMastery(), 1500) }, { immediate: true })
onMounted(() => {
  document.addEventListener('pointerdown', closePopoverOnPointerDown)
  document.addEventListener('keydown', closePopoverOnEscape)
})
onBeforeUnmount(() => {
  clearTimeout(timer); clearTimeout(idleTimer); facetController?.abort(); overviewController?.abort()
  document.removeEventListener('pointerdown', closePopoverOnPointerDown)
  document.removeEventListener('keydown', closePopoverOnEscape)
})
</script>

<template>
  <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
  <StatePanel v-else-if="loading && !index" kind="loading" title="正在读取技能与题目…" description="" />
  <StatePanel v-else-if="error && !index" kind="error" :title="error" description="" retry-label="重新读取" @retry="emit('retry')" />
  <div v-else ref="layout" class="qb-skill-layout" :class="{ 'is-reading': bank.selectedQuestionId, 'is-skill-narrow': bank.selectedQuestionId && !skillRailOpen, 'is-unlinked': unlinked }">
    <aside class="qb-skill-pane qb-browse-pane">
      <div v-if="bank.selectedQuestionId" class="qb-skill-rail"><AppIconButton class="qb-rail-toggle" :label="skillRailOpen ? '收窄技能列表' : '展开技能列表'" icon="book-open" variant="secondary" :aria-expanded="skillRailOpen" @click="skillRailOpen = !skillRailOpen" /><span v-if="!skillRailOpen">{{ mode === 'tag' ? '标签' : switcherLabel }}</span><button v-if="!skillRailOpen" class="qb-rail-unlinked" :aria-label="`未挂技能 · ${unlinkedCount} 题`" title="未挂技能" @click="router.replace({ query: { tab: 'skill', skill: 'unlinked' } })">⚠<small>{{ unlinkedCount }}</small></button></div>
      <header class="qb-skill-list-heading">
        <details ref="sectionSwitcher" class="qb-label-popover qb-section-switcher"><summary><span class="qb-switcher-current"><small>{{ switcherChapterLabel }}</small><b>{{ switcherLabel }}</b></span><span class="qb-switcher-caret">▾</span></summary><div>
          <div class="qb-chapter-tree"><details v-for="chapter in index?.chapters ?? []" :key="chapter.id" :open="chapter.id === currentChapter?.id">
            <summary><span>{{ chapter.label }}</span><small>{{ chapter.question_count }}</small></summary>
            <button v-for="section in chapter.sections" :key="section.id" class="qb-tree-row" :class="{ 'is-active': section.id === sectionId }" :aria-pressed="section.id === sectionId" @click="chooseSection(section.id)">{{ section.label }} <small>{{ section.question_count }}</small></button>
            <button v-if="chapter.cross_section_skills.length" class="qb-tree-row" :class="{ 'is-active': chapter.id === sectionId }" @click="chooseSection(chapter.id)">跨小节 <small>{{ chapter.cross_section_skills.length }} 项技能</small></button>
          </details></div>
        </div></details>
        <div class="qb-skill-subline"><p>{{ middleSubline }}</p><select v-if="mode !== 'tag'" v-model="sort" class="app-input" aria-label="技能排序"><option value="curriculum">教材顺序</option><option value="count">题数多 → 少</option><option v-if="overview.length" value="mastery">掌握度低 → 高</option></select></div>
        <div class="qb-skill-list-tools"><div class="qb-segment"><button :aria-pressed="mode === 'skill'" :class="{ 'is-active': mode === 'skill' }" @click="setMode('skill')">按技能</button><button :aria-pressed="mode === 'topic'" :class="{ 'is-active': mode === 'topic' }" @click="setMode('topic')">按知识主题</button><button :aria-pressed="mode === 'tag'" :class="{ 'is-active': mode === 'tag' }" @click="setMode('tag')">按标签</button></div></div>
        <div v-if="mode === 'tag'" class="qb-tag-tools"><div class="qb-segment"><button v-for="dimension in tagDimensions" :key="dimension.key" :aria-pressed="tagDimension.key === dimension.key" :class="{ 'is-active': tagDimension.key === dimension.key }" @click="setTagDimension(dimension.key)">{{ dimension.label }}</button></div><p v-if="tagDimension.key === 'errorPatternCategories'">按已确认的典型错误归类统计</p><div class="qb-segment"><button :aria-pressed="tagScope === 'volume'" :class="{ 'is-active': tagScope === 'volume' }" @click="setTagScope('volume')">整个学期</button><button :disabled="tagDimension.key === 'knowledgePoints' || !currentChapter" :title="tagDimension.key === 'knowledgePoints' ? '知识点按整个学期统计' : undefined" :aria-pressed="tagScope === 'chapter'" :class="{ 'is-active': tagScope === 'chapter' }" @click="setTagScope('chapter')">本章</button><button :disabled="tagDimension.key === 'knowledgePoints' || !selectedSection" :title="tagDimension.key === 'knowledgePoints' ? '知识点按整个学期统计' : undefined" :aria-pressed="tagScope === 'section'" :class="{ 'is-active': tagScope === 'section' }" @click="setTagScope('section')">本小节</button></div><input v-model="tagSearch" type="search" class="app-input" placeholder="筛选标签" aria-label="筛选标签"></div>
      </header>
      <div v-if="mode !== 'tag'" class="qb-skill-rows"><button v-for="entry in entries" :key="entry.stable_key" class="qb-skill-row" :class="{ 'is-active': entry.stable_key === selected?.stable_key }" :aria-pressed="entry.stable_key === selected?.stable_key" @click="selectEntry(entry)">
        <div class="qb-skill-row__title"><strong>{{ entry.display_name }}</strong><span class="qb-row-badges"><span :class="entry.question_count === 0 ? 'is-empty' : entry.question_count < 5 ? 'is-low' : 'is-ok'">{{ entry.question_count === 0 ? '暂无题' : entry.question_count < 5 ? '题量少' : '正常' }}</span><span v-if="entry.criteria_needs_review_count" class="is-low">{{ entry.criteria_needs_review_count }} 待审</span><span v-if="'cross_section' in entry && entry.cross_section" class="is-empty">跨小节</span></span></div>
        <div class="qb-skill-row__meta"><span><b>{{ entry.question_count }}</b> 题</span><span>选{{ (entry.type_counts['选择题'] ?? 0) + (entry.type_counts['多选题'] ?? 0) }}·填{{ entry.type_counts['填空题'] ?? 0 }}·解{{ entry.type_counts['解答题'] ?? 0 }}</span><template v-if="entry.difficulty"><span>难度 {{ entry.difficulty.min }}–{{ entry.difficulty.max }}</span><span class="qb-mini-range" :title="`难度 ${entry.difficulty.min}–${entry.difficulty.max}，中位 ${entry.difficulty.median}`"><i :style="{ left: `${(entry.difficulty.min - 1) / 9 * 100}%`, width: `${(entry.difficulty.max - entry.difficulty.min) / 9 * 100}%` }" /><b :style="{ left: `${(entry.difficulty.median - 1) / 9 * 100}%` }" /></span></template></div>
        <span v-if="mastery.get(entry.stable_key)?.evidence_student_count" class="qb-mastery-copy">{{ mastery.get(entry.stable_key)?.evidence_student_count }} 人有证据 · 均值 {{ Math.round((mastery.get(entry.stable_key)?.group_mastery ?? 0) * 100) }}%</span>
      </button><p v-if="!entries.length" class="qb-help">这个范围暂无{{ mode === 'skill' ? '技能' : '知识主题' }}。</p></div>
      <div v-else class="qb-skill-rows"><p v-if="facetsError" role="alert">{{ facetsError }}</p><button v-for="row in tagRows" :key="row.value" class="qb-skill-row" :class="{ 'is-active': row.value === tagValue }" :aria-pressed="row.value === tagValue" :title="row.value" @click="selectTag(row.value)"><div class="qb-skill-row__title"><strong>{{ tagRowLabel(row.value) }}</strong></div><div class="qb-skill-row__meta"><span><b>{{ row.count }}</b> 题</span></div></button><p v-if="!facetsError && !tagRows.length" class="qb-help">这个范围暂无可用标签。</p></div>
      <button class="qb-unlinked" :class="{ 'is-active': unlinked }" :aria-pressed="unlinked" @click="router.replace({ query: { tab: 'skill', skill: 'unlinked' } })"><span>⚠ 未挂技能</span><strong>{{ unlinkedCount }} 题</strong></button>
    </aside>
    <main class="qb-question-pane qb-browse-pane">
      <header class="qb-target-heading"><div class="qb-target-heading__line"><h2>{{ unlinked ? '未挂技能的题目' : mode === 'tag' ? tagHeading : selected?.display_name || '请选择技能或知识主题' }}</h2><span v-if="selected" class="qb-row-badges"><span :class="!selected.question_count ? 'is-empty' : selected.question_count < 5 ? 'is-low' : 'is-ok'">{{ !selected.question_count ? '暂无题' : selected.question_count < 5 ? '题量少' : '正常' }}</span></span><span v-if="selected" class="qb-target-count">共 {{ selected.question_count }} 题</span><span v-if="mode === 'tag' && tagValue" class="qb-target-count">共 {{ bank.total }} 题</span><RouterLink v-if="selected && mode === 'skill'" class="qb-link" :to="{ path: '/question-assembly', query: { mode: 'assistant', skill: selected.stable_key } }">按班级学情挑这个技能的题 →</RouterLink></div>
        <div v-if="unlinked" class="qb-repair-entry"><span>共 {{ (index?.unlinked.no_usable_evidence ?? 0) + (index?.unlinked.no_skill_link ?? 0) }} 题 · 先查看缺失部分，再只补缺失</span><AppButton variant="primary" @click="emit('repair')">AI 补挂技能</AppButton></div>
        <details v-if="selected && 'definition' in selected" class="qb-skill-definition"><summary><b>技能定义</b><span>{{ selected.definition.observable_evidence || '展开查看技能的纳入与排除范围' }}</span></summary><div><p>可观察操作：{{ selected.definition.observable_evidence || '待补充' }}</p><p>纳入：{{ selected.definition.include_scope || '待补充' }}</p><p>不纳入：{{ selected.definition.exclude_scope || '待补充' }}</p></div></details>
        <p v-if="mode === 'tag' && !tagValue" class="qb-help">选中中间列的标签后，列出范围内所有带该标签的题目（跨技能）</p>
      </header>
      <div v-if="selected || unlinked || mode === 'tag'" class="qb-skill-filters">
        <div class="qb-filter-line"><input v-model="filters.keyword" type="search" class="app-input qb-stem-search" aria-label="搜题干" placeholder="搜题干"><div class="qb-type-chips"><button class="qb-filter-chip" :class="{ 'is-active': !filters.questionTypes.length }" :aria-pressed="!filters.questionTypes.length" @click="filters.questionTypes = []">全部</button><button class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.some(type => /选择/.test(type)) }" :aria-pressed="filters.questionTypes.some(type => /选择/.test(type))" @click="toggleChoice">选择</button><button v-for="type in ['填空题', '解答题']" :key="type" class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.includes(type) }" :aria-pressed="filters.questionTypes.includes(type)" @click="toggleType(type)">{{ type.slice(0, 2) }}</button></div><DifficultyRangeFilter v-model:min="filters.difficultyMin" v-model:max="filters.difficultyMax" compact /><details ref="sourcePopover" class="qb-label-popover qb-source-filter" @toggle="onSourceToggle"><summary :title="sourceSummary"><span class="qb-source-summary">{{ sourceSummary }}</span><span>⌄</span></summary><div><input ref="sourceSearchInput" v-model="sourceSearch" type="search" class="app-input" aria-label="筛选来源" placeholder="筛选来源"><button class="qb-source-row" :class="{ 'is-active': !filters.paper }" :aria-pressed="!filters.paper" @click="chooseSource('')">全部来源</button><div class="qb-source-list"><button v-for="paper in filteredSourcePapers" :key="paper.id" class="qb-source-row" :class="{ 'is-active': filters.paper === String(paper.id) }" :aria-pressed="filters.paper === String(paper.id)" :title="paper.title ?? undefined" @click="chooseSource(String(paper.id))"><span>{{ paper.title }}</span><small>{{ paper.question_count }} 题</small></button><p v-if="!filteredSourcePapers.length" class="qb-help">没有匹配的来源。</p></div></div></details>
        <details class="qb-label-popover qb-more-filter"><summary>更多 <span>⌄</span></summary><div><label>标签完整度<select v-model="filters.tagStatus" class="app-input"><option value="all">全部</option><option value="tagged">完整</option><option value="untagged">待完善</option></select></label><label><input v-model="filters.criteriaReview" type="checkbox">判定点待审核</label><label><input v-model="filters.questionTypes" type="checkbox" value="多选题">多选题</label><QuestionSortControl v-model="filters.questionSort" /><label><input type="checkbox" :checked="bank.questions.length > 0 && bank.questions.every(question => bank.selectedQuestionIds.includes(question.id))" @change="bank.selectCurrentPage(($event.target as HTMLInputElement).checked)">选择本页</label></div></details>
        <label class="qb-duplicate-toggle" :class="{ 'is-active': filters.collapse }"><input v-model="filters.collapse" type="checkbox">折叠重复题<template v-if="filters.collapse && duplicateCount"> · 已折叠 {{ duplicateCount }}</template></label><label class="qb-progress-filter">教学进度：<select v-model="filters.progress" class="app-input" aria-label="教学进度"><option value="">不限</option><option v-for="chapter in scope.selectedVolume?.chapters ?? []" :key="chapter.id" :value="chapter.id">已学到{{ chapter.label }}</option></select></label>
        <details class="qb-label-popover qb-tag-filter"><summary>标签筛选</summary><div><p v-if="facetsError" role="alert">{{ facetsError }}</p><div class="qb-segment"><button v-for="dimension in facetDimensions" :key="dimension.key" :class="{ 'is-active': facetTab === dimension.key }" :aria-pressed="facetTab === dimension.key" @click="facetTab = dimension.key">{{ dimension.label }}</button></div><div class="qb-facet-chips"><button v-for="item in facetItems(facetTab)" :key="item.value" class="qb-filter-chip" :class="{ 'is-active': filters[facetTab].includes(item.value) }" :aria-pressed="filters[facetTab].includes(item.value)" @click="toggleFacet(facetTab, item.value)">{{ item.value }} <small>{{ item.count }}</small></button><p v-if="!facetItems(facetTab).length" class="qb-help">当前范围没有可用标签。</p></div></div></details></div>
        <div v-if="facetDimensions.some(dimension => filters[dimension.key].length)" class="qb-filter-line"><template v-for="dimension in facetDimensions" :key="dimension.key"><button v-for="value in filters[dimension.key]" :key="value" class="qb-filter-chip is-active" :aria-label="`移除${value}筛选`" @click="filters[dimension.key] = filters[dimension.key].filter(item => item !== value)">{{ value }} ×</button></template></div>
      </div>
      <QuestionLedger v-if="selected || unlinked || (mode === 'tag' && !!tagValue)" :current-skill="mode === 'skill' ? selected?.stable_key : undefined" @skill="emit('skill', $event)" />
      <p v-else-if="mode !== 'tag' && (queryText('skill') || queryText('topic'))" class="qb-feedback is-warning" role="status">这个目标不在当前教学学期，请选择其他技能或知识主题。</p>
    </main>
  </div>
</template>
