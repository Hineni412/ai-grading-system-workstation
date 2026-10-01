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
import AppIconButton from '../design-system/AppIconButton.vue'

const props = defineProps<{ index: QuestionSkillIndex | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: []; skill: [key: string] }>()
const bank = useQuestionBankStore()
const scope = useCurriculumScopeStore()
const route = useRoute()
const router = useRouter()
const railOpen = ref(false)
const sort = ref('curriculum')
const facetTab = ref<'abilities' | 'methods' | 'models' | 'thoughts' | 'specialTypes'>('abilities')
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
const currentChapter = computed(() => selectedChapter.value ?? props.index?.chapters.find(chapter => chapter.sections.some(section => section.id === sectionId.value)))
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
const duplicateCount = computed(() => bank.questions.reduce((count, question) => count + (question.duplicate_members?.length ?? 0), 0))
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
    const result = await trainingApi.overview({ scope: { mode: 'all', student_ids: [] }, exam_scope: { mode: 'semester', session_ids: [], curriculum_volume_id: scope.selectedVolumeId } }, controller.signal)
    if (!controller.signal.aborted) overview.value = result.nodes
  } catch { if (!controller.signal.aborted) { overview.value = []; sort.value = 'curriculum' } }
}
watch(() => scope.selectedVolumeId, () => { overviewController?.abort(); overview.value = []; sort.value = 'curriculum'; clearTimeout(idleTimer); idleTimer = setTimeout(() => void loadMastery(), 1500) }, { immediate: true })
onBeforeUnmount(() => { clearTimeout(timer); clearTimeout(idleTimer); facetController?.abort(); overviewController?.abort() })
</script>

<template>
  <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请在侧栏选择教学学期" description="" />
  <StatePanel v-else-if="loading && !index" kind="loading" title="正在读取技能与题目…" description="" />
  <StatePanel v-else-if="error && !index" kind="error" :title="error" description="" retry-label="重新读取" @retry="emit('retry')" />
  <div v-else class="qb-skill-layout" :class="{ 'is-narrow': bank.selectedQuestionId && !railOpen }">
    <aside class="qb-chapter-pane qb-browse-pane">
      <div v-if="bank.selectedQuestionId" class="qb-chapter-rail"><AppIconButton class="qb-rail-toggle" :label="railOpen ? '收窄教材章节' : '展开教材章节'" icon="book-open" variant="secondary" :aria-expanded="railOpen" @click="railOpen = !railOpen" /><span v-if="!railOpen">{{ selectedSection?.label || selectedChapter?.label || '教材章节' }}</span></div>
      <div class="qb-chapter-content">
        <header><h2>教材章节</h2><span class="qb-volume-badge">{{ scope.selectedVolume?.label }}</span></header>
        <div class="qb-chapter-tree"><details v-for="chapter in index?.chapters ?? []" :key="chapter.id" :open="chapter.id === currentChapter?.id">
          <summary><span>{{ chapter.label }}</span><small>{{ chapter.question_count }}</small></summary>
          <button v-for="section in chapter.sections" :key="section.id" class="qb-tree-row" :class="{ 'is-active': section.id === sectionId }" :aria-pressed="section.id === sectionId" @click="selectSection(section.id)">{{ section.label }} <small>{{ section.question_count }}</small></button>
          <button v-if="chapter.cross_section_skills.length" class="qb-tree-row" :class="{ 'is-active': chapter.id === sectionId }" @click="selectSection(chapter.id)">跨小节 <small>{{ chapter.cross_section_skills.length }} 项技能</small></button>
        </details></div>
        <button class="qb-unlinked" @click="router.replace({ query: { tab: 'skill', skill: 'unlinked' } })"><span>⚠ 未挂技能</span><strong>{{ (index?.unlinked.no_usable_evidence ?? 0) + (index?.unlinked.no_skill_link ?? 0) }} 题</strong></button>
      </div>
    </aside>
    <aside class="qb-skill-pane qb-browse-pane">
      <header class="qb-skill-list-heading"><h2>{{ unlinked ? '未挂技能' : selectedSection?.label || (selectedChapter ? `${selectedChapter.label} · 跨小节` : '技能与知识主题') }}</h2><p>{{ entries.length }} 项{{ mode === 'skill' ? '技能' : '主题' }} · {{ selectedSection?.question_count ?? selectedChapter?.question_count ?? 0 }} 题</p>
        <div class="qb-skill-list-tools"><div class="qb-segment"><button :aria-pressed="mode === 'skill'" :class="{ 'is-active': mode === 'skill' }" @click="setMode('skill')">按技能</button><button :aria-pressed="mode === 'topic'" :class="{ 'is-active': mode === 'topic' }" @click="setMode('topic')">按知识主题</button></div><select v-model="sort" class="app-input" aria-label="技能排序"><option value="curriculum">教材顺序</option><option value="count">题数多 → 少</option><option v-if="overview.length" value="mastery">掌握度低 → 高</option></select></div>
      </header>
      <div class="qb-skill-rows"><button v-for="entry in entries" :key="entry.stable_key" class="qb-skill-row" :class="{ 'is-active': entry.stable_key === selected?.stable_key }" :aria-pressed="entry.stable_key === selected?.stable_key" @click="selectEntry(entry)">
        <div class="qb-skill-row__title"><strong>{{ entry.display_name }}</strong><span class="qb-row-badges"><span :class="entry.question_count === 0 ? 'is-empty' : entry.question_count < 5 ? 'is-low' : 'is-ok'">{{ entry.question_count === 0 ? '暂无题' : entry.question_count < 5 ? '题量少' : '正常' }}</span><span v-if="entry.criteria_needs_review_count" class="is-low">{{ entry.criteria_needs_review_count }} 待审</span><span v-if="'cross_section' in entry && entry.cross_section" class="is-empty">跨小节</span></span></div>
        <div class="qb-skill-row__meta"><span><b>{{ entry.question_count }}</b> 题</span><span>选{{ (entry.type_counts['选择题'] ?? 0) + (entry.type_counts['多选题'] ?? 0) }}·填{{ entry.type_counts['填空题'] ?? 0 }}·解{{ entry.type_counts['解答题'] ?? 0 }}</span><template v-if="entry.difficulty"><span>难度 {{ entry.difficulty.min }}–{{ entry.difficulty.max }}</span><span class="qb-mini-range" :title="`难度 ${entry.difficulty.min}–${entry.difficulty.max}，中位 ${entry.difficulty.median}`"><i :style="{ left: `${(entry.difficulty.min - 1) / 9 * 100}%`, width: `${(entry.difficulty.max - entry.difficulty.min) / 9 * 100}%` }" /><b :style="{ left: `${(entry.difficulty.median - 1) / 9 * 100}%` }" /></span></template></div>
        <span v-if="mastery.get(entry.stable_key)?.evidence_student_count" class="qb-mastery-copy">{{ mastery.get(entry.stable_key)?.evidence_student_count }} 人有证据 · 均值 {{ Math.round((mastery.get(entry.stable_key)?.group_mastery ?? 0) * 100) }}%</span>
      </button><p v-if="!entries.length" class="qb-help">这个范围暂无{{ mode === 'skill' ? '技能' : '知识主题' }}。</p></div>
    </aside>
    <main class="qb-question-pane qb-browse-pane">
      <header class="qb-target-heading"><div class="qb-target-heading__line"><h2>{{ unlinked ? '未挂技能的题目' : selected?.display_name || '请选择技能或知识主题' }}</h2><span v-if="selected" class="qb-row-badges"><span :class="!selected.question_count ? 'is-empty' : selected.question_count < 5 ? 'is-low' : 'is-ok'">{{ !selected.question_count ? '暂无题' : selected.question_count < 5 ? '题量少' : '正常' }}</span></span><span v-if="selected" class="qb-target-count">共 {{ selected.question_count }} 题</span><RouterLink v-if="selected && mode === 'skill'" class="qb-link" :to="{ path: '/question-assembly', query: { mode: 'assistant', skill: selected.stable_key } }">按班级学情挑这个技能的题 →</RouterLink></div>
        <details v-if="selected && 'definition' in selected" class="qb-skill-definition"><summary><b>技能定义</b><span>{{ selected.definition.observable_evidence || '展开查看技能的纳入与排除范围' }}</span></summary><div><p>可观察操作：{{ selected.definition.observable_evidence || '待补充' }}</p><p>纳入：{{ selected.definition.include_scope || '待补充' }}</p><p>不纳入：{{ selected.definition.exclude_scope || '待补充' }}</p></div></details>
      </header>
      <div v-if="selected || unlinked" class="qb-skill-filters">
        <div class="qb-filter-line"><input v-model="filters.keyword" type="search" class="app-input qb-stem-search" aria-label="搜题干" placeholder="搜题干"><div class="qb-type-chips"><button class="qb-filter-chip" :class="{ 'is-active': !filters.questionTypes.length }" :aria-pressed="!filters.questionTypes.length" @click="filters.questionTypes = []">全部</button><button class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.some(type => /选择/.test(type)) }" :aria-pressed="filters.questionTypes.some(type => /选择/.test(type))" @click="toggleChoice">选择</button><button v-for="type in ['填空题', '解答题']" :key="type" class="qb-filter-chip" :class="{ 'is-active': filters.questionTypes.includes(type) }" :aria-pressed="filters.questionTypes.includes(type)" @click="toggleType(type)">{{ type.slice(0, 2) }}</button></div><DifficultyRangeFilter v-model:min="filters.difficultyMin" v-model:max="filters.difficultyMax" compact /><select v-model="filters.paper" class="app-input qb-source-filter" aria-label="来源"><option value="">全部来源</option><option v-for="paper in bank.papers.filter(paper => paper.curriculum_volume_id === scope.selectedVolumeId)" :key="paper.id" :value="String(paper.id)">{{ paper.title }}</option></select>
        <details class="qb-label-popover qb-more-filter"><summary>更多 <span>⌄</span></summary><div><label>标签完整度<select v-model="filters.tagStatus" class="app-input"><option value="all">全部</option><option value="tagged">完整</option><option value="untagged">待完善</option></select></label><label><input v-model="filters.criteriaReview" type="checkbox">判定点待审核</label><label><input v-model="filters.questionTypes" type="checkbox" value="多选题">多选题</label><QuestionSortControl v-model="filters.questionSort" /><label><input type="checkbox" :checked="bank.questions.length > 0 && bank.questions.every(question => bank.selectedQuestionIds.includes(question.id))" @change="bank.selectCurrentPage(($event.target as HTMLInputElement).checked)">选择本页</label></div></details>
        <label class="qb-duplicate-toggle" :class="{ 'is-active': filters.collapse }"><input v-model="filters.collapse" type="checkbox">折叠重复题<template v-if="filters.collapse && duplicateCount"> · 已折叠 {{ duplicateCount }}</template></label><label class="qb-progress-filter">教学进度：<select v-model="filters.progress" class="app-input" aria-label="教学进度"><option value="">不限</option><option v-for="chapter in scope.selectedVolume?.chapters ?? []" :key="chapter.id" :value="chapter.id">已学到{{ chapter.label }}</option></select></label>
        <details class="qb-label-popover qb-tag-filter"><summary>标签筛选</summary><div><p v-if="facetsError" role="alert">{{ facetsError }}</p><div class="qb-segment"><button v-for="dimension in facetDimensions" :key="dimension.key" :class="{ 'is-active': facetTab === dimension.key }" :aria-pressed="facetTab === dimension.key" @click="facetTab = dimension.key">{{ dimension.label }}</button></div><div class="qb-facet-chips"><button v-for="item in facetItems(facetTab)" :key="item.value" class="qb-filter-chip" :class="{ 'is-active': filters[facetTab].includes(item.value) }" :aria-pressed="filters[facetTab].includes(item.value)" @click="toggleFacet(facetTab, item.value)">{{ item.value }} <small>{{ item.count }}</small></button><p v-if="!facetItems(facetTab).length" class="qb-help">当前范围没有可用标签。</p></div></div></details></div>
        <div v-if="facetDimensions.some(dimension => filters[dimension.key].length)" class="qb-filter-line"><template v-for="dimension in facetDimensions" :key="dimension.key"><button v-for="value in filters[dimension.key]" :key="value" class="qb-filter-chip is-active" :aria-label="`移除${value}筛选`" @click="filters[dimension.key] = filters[dimension.key].filter(item => item !== value)">{{ value }} ×</button></template></div>
      </div>
      <QuestionLedger v-if="selected || unlinked" :current-skill="mode === 'skill' ? selected?.stable_key : undefined" @skill="emit('skill', $event)" />
      <p v-else-if="queryText('skill') || queryText('topic')" class="qb-feedback is-warning" role="status">这个目标不在当前教学学期，请选择其他技能或知识主题。</p>
    </main>
  </div>
</template>
