<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { questionBankApi, type QuestionBankFilters, type QuestionBankListItem, type QuestionRepairKind, type QuestionRepairPart, type QuestionSkillIndex, type SkillCandidateSummary } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionBankStandardSummary from './QuestionBankStandardSummary.vue'
import SkillCandidateRunDialog from './SkillCandidateRunDialog.vue'
import SkillCandidateReview from './SkillCandidateReview.vue'
import '../../styles/skill-candidates.css'
const props = defineProps<{ index: QuestionSkillIndex | null; pendingCount: number }>()
const emit = defineEmits<{ question: [question: QuestionBankListItem]; openQuestion: [ref: { questionId: number; paperId: number | null }]; skill: [key: string]; criteria: []; taxonomy: []; repair: [kind: QuestionRepairKind] }>()
const scope = useCurriculumScopeStore()
const bank = useQuestionBankStore()
const summaryRef = ref<InstanceType<typeof QuestionBankStandardSummary> | null>(null)
const candidateSummary = ref<SkillCandidateSummary | null>(null)
const runDialogOpen = ref(false)
const reviewOpen = ref(false)
const gapCount = computed(() => (candidateSummary.value?.counts.ready ?? 0) + (candidateSummary.value?.counts.pending_review ?? 0))
const repairPreview = computed(() =>
  scope.selectedVolumeId ? bank.repairPreviews.get(`${scope.selectedVolumeId}:all`) ?? null : null)
const analysisParts: QuestionRepairPart[] = ['tags', 'evidence', 'criteria']
const analysisPartLabels: Record<string, string> = { tags: '题目标签', evidence: '解题证据', criteria: '判定点' }
const analysisItems = computed(() => (repairPreview.value?.items ?? []).filter(item => item.missing.some(part => analysisParts.includes(part))))
const analysisCount = computed(() => analysisItems.value.length)
const analysisLoading = computed(() => !!scope.selectedVolumeId && !repairPreview.value && bank.repairPreviewLoading.has(`${scope.selectedVolumeId}:all`))
const analysisLimit = ref(20)
function analysisMissingText(missing: QuestionRepairPart[]): string {
  return missing.filter(part => part !== 'skills').map(part => analysisPartLabels[part] ?? part).join('、')
}
const gapActive = computed(() => !!candidateSummary.value?.active_run)
const reviewDisabled = computed(() => !candidateSummary.value || (!candidateSummary.value.pending_suggestion_count && !candidateSummary.value.approved_unpublished_skill_count))
watch(() => scope.selectedVolumeId, () => { runDialogOpen.value = false; reviewOpen.value = false })
function afterCandidateChange() { void summaryRef.value?.refresh(); void load() }
const groups = ref<{ key: string; label: string; total: number; items: QuestionBankListItem[]; page: number; filter: QuestionBankFilters }[]>([])
const loading = ref(false)
const error = ref('')
let controller: AbortController | null = null
const underused = computed(() => [...new Map(props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).filter(skill => skill.question_count < 5).map(skill => [skill.stable_key, skill])).values()])
const underusedLimit = ref(20)
const descriptions: Record<string, string> = { review: '判定点需要人工确认后才能作为作答证据。', types: '这些题尚无主题型。尚未整理题型的章会保留待归类。', knowledge: '这些题的判定点尚无本册细项知识点关联。' }
async function load() {
  controller?.abort()
  const volumeId = scope.selectedVolumeId
  if (!volumeId) { groups.value = []; return }
  const previewKey = `${volumeId}:all`
  if (!bank.repairPreviews.has(previewKey) && !bank.repairPreviewLoading.has(previewKey)) {
    void bank.loadRepairPreview(volumeId, 'all').catch(() => {})
  }
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  const base: QuestionBankFilters = { page: 1, pageSize: 20, includeSkills: true, curriculumVolumeIds: [volumeId] }
  const definitions = [{ key: 'review', label: '判定点待审核', filter: { ...base, criteriaNeedsReview: true } }, { key: 'types', label: '缺题型', filter: { ...base, missingType: true } }, { key: 'knowledge', label: '缺知识点', filter: { ...base, missingKnowledge: true } }]
  try {
    const results = await Promise.all(definitions.map(async group => ({ ...group, ...(await questionBankApi.listQuestions(group.filter, request.signal)) })))
    if (!request.signal.aborted) groups.value = results
  } catch { if (!request.signal.aborted) error.value = '待处理题目暂时无法读取，请重试。' }
  finally { if (!request.signal.aborted) loading.value = false }
}
async function more(group: typeof groups.value[number]) {
  try { const page = await questionBankApi.listQuestions({ ...group.filter, page: group.page + 1 }, controller?.signal); group.items.push(...page.items); group.page = page.page }
  catch { error.value = '后续题目读取失败，请重试。' }
}
watch([() => scope.selectedVolumeId, () => props.index], () => void load(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <section class="qb-todo">
    <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
    <template v-else>
    <QuestionBankStandardSummary ref="summaryRef" :volume-id="scope.selectedVolumeId" @candidate="candidateSummary = $event" />
    <div class="qb-todo-stats"><span v-for="group in groups" :key="group.key">{{ group.label }} <strong>{{ group.total }}</strong></span><span>分析未完成 <strong>{{ analysisLoading ? '…' : analysisCount }}</strong></span><span>新词例外 <strong>{{ pendingCount }}</strong></span></div>
    <StatePanel v-if="loading" kind="loading" title="正在读取待处理题目…" />
    <StatePanel v-else-if="error" kind="error" :title="error" retry-label="重试" @retry="load" />
    <div class="qb-todo-list"><section v-for="group in groups" :key="group.key" class="qb-todo-group"><header><h2>{{ group.label }}</h2><button v-if="group.key === 'review'" class="qb-link" @click="emit('criteria')">判定点批量审核</button><button v-if="group.total && group.key !== 'review'" class="qb-link" @click="emit('repair', 'all')">AI 补齐</button><span class="qb-todo-count">{{ group.total }}</span></header><p>{{ group.total ? descriptions[group.key] : '当前学期没有这类待处理题目。' }}</p><article v-for="question in group.items" :key="question.id"><strong>第{{ question.question_number }}题</strong><span>{{ question.paper_title }}</span><small>{{ group.label }}</small><AppButton variant="secondary" @click="emit('question', question)">去处理 →</AppButton></article><button v-if="group.items.length < group.total" class="qb-link" @click="more(group)">查看更多</button></section>
    <section class="qb-todo-group"><header><h2>分析未完成</h2><button v-if="!analysisLoading && analysisCount" class="qb-link" @click="emit('repair', 'analysis')">AI 补齐分析</button><span class="qb-todo-count">{{ analysisLoading ? '…' : analysisCount }}</span></header><StatePanel v-if="analysisLoading" kind="loading" title="正在读取待处理题目…" /><template v-else><p>{{ analysisCount ? '题目标签、解题证据或判定点缺失，或题目内容变化后需要重新确认。' : '当前学期没有这类待处理题目。' }}</p><article v-for="item in analysisItems.slice(0, analysisLimit)" :key="item.id"><strong>第{{ item.question_number }}题</strong><span>{{ item.paper_title }}</span><small>缺：{{ analysisMissingText(item.missing) }}</small><AppButton variant="secondary" @click="emit('openQuestion', { questionId: item.id, paperId: null })">去处理 →</AppButton></article><button v-if="analysisLimit < analysisCount" class="qb-link" @click="analysisLimit += 20">查看更多</button></template></section>
    <section class="qb-todo-group"><header><h2>新词例外</h2><button class="qb-link" @click="emit('taxonomy')">去处理 →</button><span class="qb-todo-count">{{ pendingCount }}</span></header><p>分析中遇到的未收录表述，确认后归入知识点或细条目。</p></section>
    <details class="qb-todo-group"><summary>辅助维护 · 技能关联与技能候选</summary><p>未挂技能 {{ (index?.unlinked.no_usable_evidence ?? 0) + (index?.unlinked.no_skill_link ?? 0) }} 题 <button class="qb-link" @click="emit('repair', 'skills')">AI 补挂技能</button></p><header><h2>技能缺口</h2><button v-if="candidateSummary" class="qb-link" :disabled="gapActive || !candidateSummary.counts.ready" @click="runDialogOpen = true">{{ gapActive ? '正在整理…' : '整理成技能候选' }}</button><button v-if="candidateSummary" class="qb-link" :disabled="reviewDisabled" @click="reviewOpen = true">审核技能候选（{{ candidateSummary.pending_suggestion_count }}）</button><span class="qb-todo-count">{{ gapCount }}</span></header><p>AI 补挂技能后仍挂不上任何技能的判定点：{{ gapCount }} 个判定点，涉及 {{ candidateSummary?.gap_question_count ?? 0 }} 题。「AI 补齐」不处理这类缺口；整理后可归入已有技能、提出新技能，或确认只归小节。</p></details>
    <section class="qb-todo-group"><header><h2>题量少技能</h2><span class="qb-todo-count">{{ underused.length }}</span></header><p>当前题量少于 5 道的技能。</p><article v-for="skill in underused.slice(0, underusedLimit)" :key="skill.stable_key"><span>{{ skill.display_name }} · {{ skill.question_count }} 题</span><AppButton variant="secondary" @click="emit('skill', skill.stable_key)">去处理 →</AppButton></article><button v-if="underusedLimit < underused.length" class="qb-link" @click="underusedLimit += 20">查看更多</button></section>
    </div><p class="qb-todo-note">查看清单和人工审核不产生模型费用；AI 补齐会先说明题目和缺失部分，确认后开始。</p>
    <SkillCandidateRunDialog :open="runDialogOpen" :volume-id="scope.selectedVolumeId || ''" :active-run-id="candidateSummary?.active_run?.run_id ?? null" @close="runDialogOpen = false" @changed="afterCandidateChange" @review="runDialogOpen = false; reviewOpen = true" />
    <SkillCandidateReview :open="reviewOpen" :volume-id="scope.selectedVolumeId || ''" :index="index" @close="reviewOpen = false" @changed="afterCandidateChange" @open-question="emit('openQuestion', $event)" />
    </template>
  </section>
</template>
