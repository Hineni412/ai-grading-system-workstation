<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { questionBankApi, type QuestionBankFacets, type QuestionBankFilters, type QuestionBankSort, type QuestionSkillIndex, type QuestionSkillEntry, type QuestionTopicEntry } from '../../api/question-bank'
import { trainingApi, type TrainingOverviewNode } from '../../api/training'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import DifficultyRangeFilter from './DifficultyRangeFilter.vue'
import QuestionSortControl from './QuestionSortControl.vue'
import QuestionLedger from './QuestionLedger.vue'
import StatePanel from '../design-system/StatePanel.vue'

const props = defineProps<{ index: QuestionSkillIndex | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: []; skill: [key: string] }>()
const bank = useQuestionBankStore()
const scope = useCurriculumScopeStore()
const route = useRoute()
const router = useRouter()
const railOpen = ref(false)
const sort = ref('curriculum')
const overview = ref<TrainingOverviewNode[]>([])
const mastery = computed(() => new Map(overview.value.map(node => [node.knowledge_key, node])))
let overviewController: AbortController | null = null
let idleTimer: ReturnType<typeof setTimeout> | undefined
const mode = computed(() => route.query.topic ? 'topic' : 'skill')
const queryText = (key: string) => typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const unlinked = computed(() => queryText('skill') === 'unlinked')
const sections = computed(() => props.index?.chapters.flatMap(chapter => chapter.sections) ?? [])
const sectionId = computed(() => queryText('section') || sections.value.find(section => section.skills.length)?.id || sections.value[0]?.id || '')
const selectedSection = computed(() => sections.value.find(section => section.id === sectionId.value))
const selectedChapter = computed(() => props.index?.chapters.find(chapter => chapter.id === sectionId.value))
type Entry = QuestionSkillEntry | QuestionTopicEntry
const allSkills = computed(() => props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]) ?? [])
const allTopics = computed(() => sections.value.flatMap(section => section.topics))
const selected = computed<Entry | undefined>(() => {
  if (unlinked.value) return undefined
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
let facetController: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
function requestFilters(): QuestionBankFilters {
  const entry = selected.value
  return { page: 1, pageSize: 20, includeSkills: true, skillKeys: mode.value === 'skill' && entry ? [entry.stable_key] : [],
    skillUnlinked: unlinked.value, knowledgePoints: entry && 'filter_value' in entry ? [entry.filter_value] : [],
    curriculumVolumeIds: scope.selectedVolumeId ? [scope.selectedVolumeId] : [],
    keyword: filters.keyword, questionTypes: [...filters.questionTypes], difficultyMin: filters.difficultyMin, difficultyMax: filters.difficultyMax,
    paperIds: filters.paper ? [Number(filters.paper)] : [], collapseDuplicates: filters.collapse, teachingProgressChapter: filters.progress,
    tagStatus: filters.tagStatus, criteriaNeedsReview: filters.criteriaReview, sort: filters.questionSort,
    abilities: [...filters.abilities], methods: [...filters.methods], models: [...filters.models], thoughts: [...filters.thoughts], specialTypes: [...filters.specialTypes] }
}
async function reload() {
  if (!props.index || (!selected.value && !unlinked.value)) { bank.questions = []; bank.total = 0; bank.listState = 'empty'; return }
  const next = requestFilters()
  void bank.loadQuestions(next)
  facetController?.abort()
  const controller = new AbortController()
  facetController = controller
  facetsError.value = ''
  try { const result = await questionBankApi.listFacets(next, controller.signal); if (!controller.signal.aborted) facets.value = result }
  catch { if (!controller.signal.aborted) facetsError.value = '标签计数暂时无法读取'; }
}
watch([selected, unlinked, () => props.index], () => { bank.clearSelection(); void reload() }, { immediate: true })
watch(filters, () => { clearTimeout(timer); timer = setTimeout(() => void reload(), 250) })
function selectSection(id: string) {
  void bank.selectQuestion(null)
  void router.replace({ query: { tab: 'skill', section: id } })
}
function selectEntry(entry: Entry) {
  void bank.selectQuestion(null)
  void router.replace({ query: { tab: 'skill', section: sectionId.value, ...('filter_value' in entry ? { topic: entry.stable_key } : { skill: entry.stable_key }) } })
}
function setMode(next: 'skill' | 'topic') {
  const first = next === 'topic' ? selectedSection.value?.topics[0] : selectedSection.value?.skills[0]
  void router.replace({ query: { tab: 'skill', section: sectionId.value, ...(next === 'topic' ? { topic: first?.stable_key || 'none' } : {}) } })
}
function toggleType(value: string) { const i = filters.questionTypes.indexOf(value); if (i < 0) filters.questionTypes.push(value); else filters.questionTypes.splice(i, 1) }
function facetItems(key: typeof facetDimensions[number]['key']) { return facets.value?.[key === 'specialTypes' ? 'special_types' : key] ?? [] }
async function loadMastery() {
  if (!scope.selectedVolumeId) return
  overviewController?.abort()
  const controller = new AbortController()
  overviewController = controller
  try {
    const result = await trainingApi.overview({ scope: { mode: 'all', student_ids: [] }, exam_scope: { mode: 'semester', session_ids: [], curriculum_volume_id: scope.selectedVolumeId } }, controller.signal)
    if (!controller.signal.aborted) overview.value = result.nodes
  } catch { if (!controller.signal.aborted) { overview.value = []; sort.value = 'curriculum' } }
}
watch(() => scope.selectedVolumeId, () => { overview.value = []; sort.value = 'curriculum'; clearTimeout(idleTimer); idleTimer = setTimeout(() => void loadMastery(), 1500) }, { immediate: true })
onBeforeUnmount(() => { clearTimeout(timer); clearTimeout(idleTimer); facetController?.abort(); overviewController?.abort() })
</script>

<template>
  <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请在侧栏选择教学学期" description="" />
  <StatePanel v-else-if="loading && !index" kind="loading" title="正在读取技能与题目…" description="" />
  <StatePanel v-else-if="error && !index" kind="error" :title="error" description="" retry-label="重新读取" @retry="emit('retry')" />
  <div v-else class="qb-skill-layout" :class="{ 'is-narrow': bank.selectedQuestionId && !railOpen }">
    <aside class="qb-chapter-pane qb-browse-pane">
      <button v-if="bank.selectedQuestionId" class="qb-rail-toggle" type="button" :aria-expanded="railOpen" @click="railOpen = !railOpen">{{ railOpen ? '收窄' : '章节' }}</button>
      <div class="qb-chapter-content"><h2>教材章节</h2><details v-for="chapter in index?.chapters ?? []" :key="chapter.id" open>
        <summary>{{ chapter.label }} <small>{{ chapter.question_count }}</small></summary>
        <button v-for="section in chapter.sections" :key="section.id" class="qb-tree-row" :class="{ 'is-active': section.id === sectionId }" :aria-pressed="section.id === sectionId" @click="selectSection(section.id)">{{ section.label }} <small>{{ section.question_count }}</small></button>
        <button v-if="chapter.cross_section_skills.length" class="qb-tree-row" @click="selectSection(chapter.id)">跨小节 <small>{{ chapter.cross_section_skills.length }} 项技能</small></button>
      </details>
      <button class="qb-unlinked" @click="router.replace({ query: { tab: 'skill', skill: 'unlinked' } })">⚠ 未挂技能 · {{ (index?.unlinked.no_usable_evidence ?? 0) + (index?.unlinked.no_skill_link ?? 0) }} 题</button></div>
    </aside>
    <aside class="qb-skill-pane qb-browse-pane">
      <header><div class="qb-segment"><button :aria-pressed="mode === 'skill'" :class="{ 'is-active': mode === 'skill' }" @click="setMode('skill')">按技能</button><button :aria-pressed="mode === 'topic'" :class="{ 'is-active': mode === 'topic' }" @click="setMode('topic')">按知识主题</button></div><select v-model="sort" class="app-input" aria-label="技能排序"><option value="curriculum">教材顺序</option><option value="count">题数多→少</option><option v-if="overview.length" value="mastery">掌握度低→高</option></select></header>
      <div class="qb-skill-rows"><button v-for="entry in entries" :key="entry.stable_key" class="qb-skill-row" :class="{ 'is-active': entry.stable_key === selected?.stable_key }" :aria-pressed="entry.stable_key === selected?.stable_key" @click="selectEntry(entry)"><strong>{{ entry.display_name }}</strong><span>{{ entry.question_count }} 题 · 选 {{ (entry.type_counts['选择题'] ?? 0) + (entry.type_counts['多选题'] ?? 0) }} · 填 {{ entry.type_counts['填空题'] }} · 解 {{ entry.type_counts['解答题'] }}</span><span v-if="entry.difficulty" class="qb-difficulty-summary"><meter min="1" max="10" :low="entry.difficulty.min" :high="entry.difficulty.max" :value="entry.difficulty.median" /> {{ entry.difficulty.min }}–{{ entry.difficulty.max }} · 中位 {{ entry.difficulty.median }}</span><span v-if="mastery.get(entry.stable_key)?.evidence_student_count" class="qb-mastery-copy">{{ mastery.get(entry.stable_key)?.evidence_student_count }} 人有证据 · 均值 {{ Math.round((mastery.get(entry.stable_key)?.group_mastery ?? 0) * 100) }}%</span><span class="qb-row-badges"><span v-if="!entry.question_count">暂无题</span><span v-else-if="entry.question_count < 5">题量少</span><span v-if="entry.criteria_needs_review_count">{{ entry.criteria_needs_review_count }} 待审</span><span v-if="'cross_section' in entry && entry.cross_section">跨小节</span></span></button><p v-if="!entries.length" class="qb-help">这个范围暂无{{ mode === 'skill' ? '技能' : '知识主题' }}。</p></div>
    </aside>
    <main class="qb-question-pane qb-browse-pane">
      <header><h2>{{ unlinked ? '未挂技能的题目' : selected?.display_name || '请选择技能或知识主题' }}</h2><span v-if="selected">{{ selected.question_count }} 题 <span v-if="selected.question_count < 5"> · {{ selected.question_count ? '题量少' : '暂无题' }}</span></span><RouterLink v-if="selected && mode === 'skill'" class="qb-link" :to="{ path: '/question-assembly', query: { mode: 'assistant', skill: selected.stable_key } }">按班级学情挑这个技能的题 →</RouterLink><details v-if="selected && 'definition' in selected"><summary>技能定义</summary><p>可观察操作：{{ selected.definition.observable_evidence }}</p><p>纳入：{{ selected.definition.include_scope }}</p><p>不纳入：{{ selected.definition.exclude_scope }}</p></details></header>
      <div v-if="selected || unlinked" class="qb-skill-filters">
        <input v-model="filters.keyword" type="search" class="app-input" aria-label="搜题干" placeholder="搜题干">
        <button v-for="type in ['选择题', '多选题', '填空题', '解答题']" :key="type" class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.includes(type) }" :aria-pressed="filters.questionTypes.includes(type)" @click="toggleType(type)">{{ type }}</button>
        <DifficultyRangeFilter v-model:min="filters.difficultyMin" v-model:max="filters.difficultyMax" compact />
        <select v-model="filters.paper" class="app-input" aria-label="来源"><option value="">全部来源</option><option v-for="paper in bank.papers.filter(paper => paper.curriculum_volume_id === scope.selectedVolumeId)" :key="paper.id" :value="String(paper.id)">{{ paper.title }}</option></select>
        <label><input v-model="filters.collapse" type="checkbox">折叠重复题</label>
        <label>教学进度<select v-model="filters.progress" class="app-input"><option value="">不限</option><option v-for="chapter in scope.selectedVolume?.chapters ?? []" :key="chapter.id" :value="chapter.id">已学到{{ chapter.label }}</option></select></label>
        <details class="qb-label-popover"><summary>标签筛选</summary><div><p v-if="facetsError" role="alert">{{ facetsError }}</p><fieldset v-for="dimension in facetDimensions" :key="dimension.key"><legend>{{ dimension.label }}</legend><label v-for="item in facetItems(dimension.key)" :key="item.value"><input v-model="filters[dimension.key]" type="checkbox" :value="item.value">{{ item.value }} · {{ item.count }}</label></fieldset></div></details>
        <details class="qb-label-popover"><summary>更多</summary><div><label>标签完整度<select v-model="filters.tagStatus" class="app-input"><option value="all">全部</option><option value="tagged">完整</option><option value="untagged">待完善</option></select></label><label><input v-model="filters.criteriaReview" type="checkbox">判定点待审核</label></div></details>
        <QuestionSortControl v-model="filters.questionSort" />
        <template v-for="dimension in facetDimensions" :key="dimension.key"><button v-for="value in filters[dimension.key]" :key="value" class="qb-filter-chip is-active" :aria-label="`移除${value}筛选`" @click="filters[dimension.key] = filters[dimension.key].filter(item => item !== value)">{{ value }} ×</button></template>
      </div>
      <QuestionLedger v-if="selected || unlinked" :current-skill="mode === 'skill' ? selected?.stable_key : undefined" @skill="emit('skill', $event)" />
      <p v-else-if="queryText('skill') || queryText('topic')" class="qb-feedback is-warning" role="status">这个目标不在当前教学学期，请选择其他技能或知识主题。</p>
    </main>
  </div>
</template>
