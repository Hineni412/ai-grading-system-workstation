<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  knowledgeLeafLabel,
  skillLeafLabel,
  type QuestionSolutionEvidenceResponse,
  QUESTION_BANK_TAG_TYPES,
  questionBankApi,
  questionTypeWithSubtype,
  type CurriculumCatalog,
  type QuestionErrorPattern,
  type QuestionBankTag,
  type QuestionSkillIndex,
} from '../../api/question-bank'
import { questionBankCriteriaApi, type TrainingCriterionWorkspace } from '../../api/question-bank-criteria'
import { CAUSE_CATEGORIES } from '../../api/class-analysis'
import { useConfirm } from '../../composables/useConfirm'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppIconButton from '../design-system/AppIconButton.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import TrainingCriterionReview from './TrainingCriterionReview.vue'

defineProps<{ currentSkill?: string }>()
const emit = defineEmits<{ skill: [key: string] }>()
const store = useQuestionBankStore()
const { confirm } = useConfirm()
const evidence = ref<QuestionSolutionEvidenceResponse | null>(null)
const criterion = ref<TrainingCriterionWorkspace | null>(null)
const evidenceError = ref('')
const editing = ref(false)
const review = ref<InstanceType<typeof TrainingCriterionReview> | null>(null)
let controller = new AbortController()
let evidenceRequest: Promise<QuestionSolutionEvidenceResponse>
let criterionRequest: Promise<TrainingCriterionWorkspace>
function loadEvidence() { return evidenceRequest }
function loadCriterion() { return criterionRequest }
watch(() => store.selectedQuestionId, (id) => {
  controller.abort()
  controller = new AbortController()
  evidence.value = null
  criterion.value = null
  evidenceError.value = ''
  editing.value = false
  if (!id) return
  const signal = controller.signal
  evidenceRequest = questionBankApi.getSolutionEvidence(id, signal)
  criterionRequest = questionBankCriteriaApi.getWorkspace(id, signal)
  void evidenceRequest.then((value) => { if (!signal.aborted) evidence.value = value })
    .catch(() => { if (!signal.aborted) evidenceError.value = '判定点关联暂时无法读取，请重新打开题目。' })
  void criterionRequest.then((value) => { if (!signal.aborted) criterion.value = value }).catch(() => {})
}, { immediate: true })
onBeforeUnmount(() => controller.abort())
const points = computed(() => evidence.value?.available ? evidence.value.evidence?.parts.flatMap((part) => part.evidence_points) ?? [] : [])
function directSkills(point: typeof points.value[number]) {
  return point.fine_term_links.filter(link => link.role === 'direct' && link.core_resolution.status === 'resolved')
    .flatMap(link => link.core_resolution.stable_keys.filter(key => key.startsWith('sk_')).map(key => ({ key, label: skillLeafLabel(link.fine_term_name) })))
}
function mappedPatterns(pointId: string) {
  if (points.value.filter(point => point.evidence_point_id === pointId).length !== 1) return []
  return (store.detail?.error_patterns ?? []).filter(item => item.trigger_kind === 'step' && item.trigger_value === pointId)
}
function attributeTags(type: string) { return (store.detail?.tags ?? []).filter(tag => tag.tag_type === type).map(tag => tag.tag_value) }
const attributes = computed(() => [
  { label: '章节', values: attributeTags('exam_scope'), tone: 'curriculum' },
  { label: '知识点', values: store.detail?.labels?.knowledge_points.length ? store.detail.labels.knowledge_points.map(point => point.label) : ['待补知识点'], tone: 'curriculum' },
  { label: '能力', values: attributeTags('ability'), tone: 'ability' },
  { label: '数学思想', values: attributeTags('thought'), tone: 'thought' },
  { label: '解题方法', values: attributeTags('method'), tone: 'method' },
  { label: '数学模型', values: attributeTags('model'), tone: 'model' },
  { label: '特殊考法', values: attributeTags('special_type'), tone: 'special' },
].filter(row => row.values.length))
async function saveAnnotation() {
  const saved = await review.value?.saveDraft()
  if (saved) { editing.value = false; await store.loadQuestions(store.appliedFilters) }
}
function cancelAnnotation() { review.value?.cancelDraft(); editing.value = false }

const curriculum = ref<CurriculumCatalog | null>(null)
const patternEdit = ref<QuestionErrorPattern | null>(null)
const patternName = ref('')
const patternCategory = ref('')
const patternSkill = ref('')
const patternSaving = ref(false)
const patternError = ref('')
const patternMessage = ref('')
watch(() => store.selectedQuestionId, () => {
  patternEdit.value = null
  patternName.value = ''
  patternCategory.value = ''
  patternSkill.value = ''
  patternError.value = ''
  patternMessage.value = ''
})
const selectableSkills = computed(() => store.detail?.selectable_skills ?? [])
const patternSkillOptions = computed(() => {
  const options = [...selectableSkills.value]
  const current = patternSkill.value
  if (current && !options.some((skill) => skill.key === current)) {
    options.unshift({ key: current, label: patternEdit.value?.skill_label || current })
  }
  return options
})
const availableCategories = CAUSE_CATEGORIES.filter((category) => category !== '未作答')
const wrongOptionRows = computed(() => {
  const patterns = store.detail?.error_patterns ?? []
  const letters = new Set([
    ...(store.detail?.wrong_option_letters ?? []),
    ...patterns.filter((item) => item.trigger_kind === 'option').map((item) => item.trigger_value),
  ])
  return [...letters].sort().map((letter) => ({
    letter,
    patterns: patterns.filter((item) => item.trigger_kind === 'option' && item.trigger_value === letter),
  }))
})
const otherPatterns = computed(() => (
  (store.detail?.error_patterns ?? []).filter((item) => item.trigger_kind !== 'option' && !points.value.some(point => mappedPatterns(point.evidence_point_id).some(mapped => mapped.id === item.id)))
))

function patternSource(source: string): string {
  if (source === 'teacher_edit' || source === 'teacher_confirm') return '教师调整'
  if (source === 'ai_predicted') return '题目预测'
  if (source === 'ai_pre_analysis') return '选项预分析'
  if (source === 'ai_auto') return '考后整理'
  return '已有资料'
}

function patternTrigger(item: QuestionErrorPattern): string {
  if (item.trigger_kind === 'wrong_answer') return `错误答案：${item.trigger_value}`
  if (item.trigger_kind === 'step') return `判定点：${item.trigger_value}`
  return '本题常见表现'
}

function patternSkillHint(item: QuestionErrorPattern): string {
  const labels = item.skill_labels?.length ? item.skill_labels : (item.skill_label ? [item.skill_label] : [])
  if (!labels.length) return ''
  const suffix = item.skill_source === 'teacher' ? '（教师设定）'
    : item.skill_source === 'question' ? '（由本题唯一技能得出）'
    : '（由判定点得出）'
  return `关联技能：${labels.join('、')}${suffix}`
}

function beginPatternEdit(item: QuestionErrorPattern): void {
  patternEdit.value = item
  patternName.value = item.pattern
  patternCategory.value = item.category ?? ''
  patternSkill.value = item.skill_key ?? ''
  patternError.value = ''
  patternMessage.value = ''
}

async function changePattern(item: QuestionErrorPattern, action: 'edit' | 'reject'): Promise<void> {
  const questionId = store.detail?.id
  if (!questionId || patternSaving.value) return
  if (action === 'reject' && !await confirm({
    title: `驳回“${item.pattern}”这条典型错法？`,
    confirmLabel: '驳回',
    danger: true,
  })) return
  if (action === 'edit' && (!patternName.value.trim() || !patternCategory.value)) return
  patternSaving.value = true
  patternError.value = ''
  patternMessage.value = ''
  try {
    const editPayload: {
      action: 'edit'
      pattern: string
      category: string
      skill_key?: string | null
    } = { action: 'edit', pattern: patternName.value.trim(), category: patternCategory.value }
    if (patternSkill.value !== (item.skill_key ?? '')) {
      editPayload.skill_key = patternSkill.value || null
    }
    const detail = await questionBankApi.editErrorPattern(
      questionId,
      item,
      action === 'edit'
        ? editPayload
        : { action },
    )
    if (store.selectedQuestionId === questionId) store.detail = detail
    patternEdit.value = null
    patternMessage.value = action === 'edit' ? '典型错法已保存。' : '典型错法已驳回。'
  } catch {
    patternError.value = '保存失败，资料可能已变化，请重新打开题目后再试。'
  } finally {
    patternSaving.value = false
  }
}
// 教材小节不单独成行显示：位置信息统一由下方"精确标定教材小节"选择器呈现，
// 选择器会同时校准教材章节（exam_scope），标签区不再重复出现两个位置标签。
// 技能（skill）由判定点归属产生、不参与手输编辑，在下方判定点区块只读展示。
const editableTagTypes = QUESTION_BANK_TAG_TYPES.filter(
  (type) => !['student_level', 'canonical_knowledge_id', 'curriculum_section', 'skill', 'knowledge_point', 'prerequisite'].includes(type),
)
const newTagType = ref<QuestionBankTag['tag_type']>('ability')

// 手动"添加"产生的空标签在保存前保持可编辑输入框；
// 已有知识点标签只显示最末端节点名，不参与文本编辑。
const freshlyAddedTags = new WeakSet<QuestionBankTag>()

const tagLabels: Record<string, string> = {
  ability: '能力',
  curriculum_section: '教材小节',
  error_type: '错误类型',
  exam_scope: '教材章节/考试范围',
  knowledge_point: '知识点',
  measured_skill_name: '测量技能',
  skill: '技能',
  method: '解题方法',
  thought: '数学思想',
  model: '模型',
  special_type: '特殊题型/考法',
  prerequisite: '前置知识',
  student_level: '学生层级',
  sub_skill: '子技能',
  supporting_skill_name: '支撑技能',
  teaching_stage: '教学阶段',
}

const selectedSectionId = computed(() => (
  store.tagDraft.find((tag) => tag.tag_type === 'curriculum_section')?.tag_value ?? ''
))

const hasLegacySection = computed(() => (
  Boolean(selectedSectionId.value)
  && !curriculumGroups.value.some((group) => (
    group.chapter.sections.some((section) => section.id === selectedSectionId.value)
  ))
))

const curriculumGroups = computed(() => (
  (curriculum.value?.volumes ?? []).flatMap((volume) => (
    volume.chapters.map((chapter) => ({
      id: chapter.id,
      label: `${volume.label} · ${chapter.label}`,
      chapter,
    }))
  ))
))

onMounted(async () => {
  try {
    curriculum.value = await questionBankApi.getCurriculum()
  } catch {
    curriculum.value = null
  }
})

function chooseCurriculumSection(sectionId: string): void {
  const retained = store.tagDraft.filter((tag) => (
    tag.tag_type !== 'curriculum_section' && (sectionId === '' || tag.tag_type !== 'exam_scope')
  ))
  if (!sectionId) {
    store.replaceTagDraft(retained)
    return
  }
  for (const group of curriculumGroups.value) {
    const section = group.chapter.sections.find((item) => item.id === sectionId)
    if (!section) continue
    store.replaceTagDraft([
      ...retained,
      {
        tag_type: 'exam_scope',
        tag_value: group.chapter.exam_scope_values[0] ?? group.chapter.label,
        confidence: null,
      },
      { tag_type: 'curriculum_section', tag_value: section.id, confidence: null },
    ])
    return
  }
}

// 技能标签供判定点区块展示：只保留末端操作名（"技能·求立方根"→"求立方根"）。
const skillLabels = computed(() => [
  ...new Set(store.tagDraft
    .filter((tag) => tag.tag_type === 'skill')
    .map((tag) => knowledgeLeafLabel(tag.tag_value).replace(/^技能·/, ''))
    .filter(Boolean)),
])

const coreTagTypes: QuestionBankTag['tag_type'][] = ['ability', 'exam_scope']
const coreTagStatus = computed(() => [
  ...coreTagTypes.map((type) => ({
    type,
    label: tagLabels[type],
    complete: store.tagDraft.some((tag) => tag.tag_type === type && tag.tag_value.trim()),
  })),
  {
    type: null,
    label: '难度',
    complete: Number(store.detail?.difficulty) >= 1 && Number(store.detail?.difficulty) <= 10,
  },
])

const tagKeys = new WeakMap<QuestionBankTag, number>()
let nextTagKey = 0
function tagKey(tag: QuestionBankTag): number {
  let key = tagKeys.get(tag)
  if (key === undefined) {
    key = ++nextTagKey
    tagKeys.set(tag, key)
  }
  return key
}

const tagGroups = computed(() => editableTagTypes
  .map((type) => ({
    type,
    label: tagLabels[type] || type,
    items: store.tagDraft
      .map((tag, index) => ({ tag, index }))
      .filter(({ tag }) => tag.tag_type === type),
  }))
  .filter((group) => group.items.length > 0))

function tagTone(type: QuestionBankTag['tag_type']): string {
  const tones: Partial<Record<QuestionBankTag['tag_type'], string>> = {
    knowledge_point: 'teal',
    ability: 'blue',
    exam_scope: 'amber',
    curriculum_section: 'amber',
    method: 'violet',
    thought: 'indigo',
    model: 'violet',
    special_type: 'rose',
    error_type: 'red',
    prerequisite: 'slate',
  }
  return tones[type] ?? 'slate'
}

function addTag(tagType: QuestionBankTag['tag_type'] = 'knowledge_point'): void {
  const tag: QuestionBankTag = { tag_type: tagType, tag_value: '', confidence: null }
  freshlyAddedTags.add(tag)
  store.replaceTagDraft([...store.tagDraft, tag])
}

function addSelectedTag(): void {
  addTag(newTagType.value)
}

function removeTag(index: number): void {
  store.replaceTagDraft(store.tagDraft.filter((_tag, itemIndex) => itemIndex !== index))
}

function validTags(): boolean {
  return store.tagDraft.every((tag) => (
    tag.tag_value.trim().length > 0 &&
    tag.tag_value.trim().length <= 160 &&
    (
      tag.confidence === null ||
      (
        Number.isFinite(tag.confidence) &&
        tag.confidence >= 0 &&
        tag.confidence <= 1
      )
    )
  ))
}

async function save(): Promise<void> {
  if (!validTags()) return
  store.replaceTagDraft(store.tagDraft.map((tag) => ({
    ...tag,
    tag_value: tag.tag_value.trim(),
  })))
  await store.saveTags()
}

const scope = useCurriculumScopeStore()
const typeOptions = ref<NonNullable<QuestionSkillIndex['types']>>([])
const typeEditing = ref(false)
const typeSaving = ref(false)
const primaryType = ref('')
const secondaryTypes = ref<string[]>([])
const typeError = ref('')
const responseModeLabels = { exact_objective: '客观作答', short_answer_points: '简答', process_required: '过程作答', visual_construction: '画图作答' }
function partDifficulty(partId: string) { return evidence.value?.part_assessments?.find(part => part.part_id === partId)?.difficulty ?? '待定' }
const fineKnowledgeKeys = computed(() => new Set([...(store.detail?.labels?.knowledge_points.map(point => point.key) ?? []), ...(curriculum.value?.volumes.flatMap(volume => volume.chapters.flatMap(chapter => chapter.sections.flatMap(section => section.knowledge_points.map(point => point.id)))) ?? [])]))
function pointKnowledge(point: typeof points.value[number]) {
  return point.fine_term_links.filter(link => link.role === 'direct' && link.core_resolution.status === 'resolved'
    && link.core_resolution.stable_keys.some(key => fineKnowledgeKeys.value.has(key))).map(link => knowledgeLeafLabel(link.fine_term_name))
}
function pointPrerequisites(point: typeof points.value[number]) {
  return point.fine_term_links.filter(link => link.role === 'supporting_prerequisite').map(link => knowledgeLeafLabel(link.fine_term_name))
}
async function editTypes() {
  typeError.value = ''
  const id = store.detail?.id
  const volumeId = scope.selectedVolumeId
  if (!id || !volumeId) return
  primaryType.value = store.detail?.labels?.primary_type?.key ?? ''
  secondaryTypes.value = store.detail?.labels?.secondary_types.map(type => type.key) ?? []
  try {
    const index = await questionBankApi.skillIndex(volumeId)
    if (store.detail?.id !== id || scope.selectedVolumeId !== volumeId) return
    const chapterKey = curriculumGroups.value.find(group => group.chapter.sections.some(section => section.id === selectedSectionId.value))?.chapter.knowledge_id
      ?? index.chapters.find(chapter => primaryType.value.startsWith(`${chapter.id}_`))?.id
    typeOptions.value = chapterKey ? (index.types ?? []).filter(type => type.value.startsWith(`${chapterKey}_`)) : []
    if (!chapterKey) { typeError.value = '请先确认本题教材小节，再修改题型。'; return }
    typeEditing.value = true
  } catch { typeError.value = '题型清单暂时无法读取，请重试。' }
}
async function saveTypes() {
  const detail = store.detail
  if (!detail || !primaryType.value || typeSaving.value) return
  const primary = primaryType.value
  const secondary = secondaryTypes.value.filter(key => key !== primary)
  typeSaving.value = true
  typeError.value = ''
  try {
    await questionBankApi.replaceQuestionTypes(detail.id, detail.revision, primary, secondary)
    if (store.selectedQuestionId !== detail.id) return
    typeEditing.value = false
    await store.selectQuestion(detail.id)
    if (store.selectedQuestionId !== detail.id) return
    const refreshedEvidence = await questionBankApi.getSolutionEvidence(detail.id, controller.signal)
    if (store.selectedQuestionId !== detail.id) return
    evidence.value = refreshedEvidence
    await store.loadQuestions(store.appliedFilters)
  } catch { if (store.selectedQuestionId === detail.id) typeError.value = '题型保存或最新资料读取未完成。请重新打开核对。' }
  finally { typeSaving.value = false }
}
watch(() => store.selectedQuestionId, () => { typeEditing.value = false; typeError.value = '' })

</script>
<template>
  <div class="qb-annotation">
    <p v-if="store.detailState === 'loading'" role="status">正在打开题目详情…</p>
    <div v-else-if="store.detailState === 'error'" role="alert">{{ store.detailError }} <AppButton variant="ghost" size="small" @click="store.selectQuestion(store.selectedQuestionId)">重新打开</AppButton></div>
    <template v-else-if="store.detail">
      <div class="qb-expanded-columns">
        <div class="qb-expanded-stem">
          <QuestionContentRenderer :blocks="store.detail.rich_content.question_blocks" :fallback="store.detail.question_text" image-alt="题目配图" media-mode="detail" />
          <details class="qb-answer-section"><summary>答案与解析</summary><QuestionContentRenderer :blocks="store.detail.rich_content.answer_blocks" :fallback="store.detail.answer_text" empty-label="暂未录入答案或解析" image-alt="答案配图" media-mode="detail" /></details>
          <div class="qb-expanded-links">
            <template v-for="preview in store.detail.previews" :key="preview.preview_type"><AppButton v-if="preview.url" :as="'a'" variant="ghost" size="small" :href="preview.url" target="_blank" rel="noopener">{{ preview.preview_type === 'question' ? '打开原卷' : '打开答案原卷' }}<span v-if="preview.page_number"> · 第 {{ preview.page_number }} 页</span></AppButton></template>
            <slot name="similar" />
          </div>
        </div>
        <div class="qb-expanded-markings">
          <section class="qb-properties"><div class="qb-section-heading"><h3>整题标签</h3><AppButton variant="secondary" size="small" :disabled="typeSaving" @click="editTypes">修改题型</AppButton></div><dl>
            <div><dt>作答形式</dt><dd>{{ questionTypeWithSubtype(store.detail.question_type, store.detail.tags) }}</dd></div>
            <div><dt>主题型</dt><dd>{{ store.detail.labels?.primary_type?.label || '待归类' }}</dd></div>
            <div><dt>次题型</dt><dd>{{ store.detail.labels?.secondary_types.map(type => type.label).join('、') || '无' }}</dd></div>
            <div><dt>难度</dt><dd><meter min="1" max="10" :value="Number(store.detail.difficulty) || 1" /> {{ store.detail.difficulty || '待定' }}<small v-for="part in evidence?.part_assessments ?? []" :key="part.part_id"> · {{ part.part_id }}：{{ part.difficulty ?? '待定' }}</small></dd></div>
            <div v-for="row in attributes" :key="row.label"><dt>{{ row.label }}</dt><dd :data-tone="row.tone"><span v-for="value in row.values" :key="value" :title="value">{{ row.tone === 'curriculum' ? value.split('｜').join(' › ') : knowledgeLeafLabel(value) }}</span></dd></div>
            <div v-if="!selectedSectionId"><dt>教材小节</dt><dd class="qb-warning">小节待标定</dd></div>
          </dl><div v-if="typeEditing" class="qb-type-editor">
            <label>主题型<select v-model="primaryType" class="app-input" aria-label="主题型"><option value="">请选择题型</option><option v-for="type in typeOptions" :key="type.value" :value="type.value">{{ type.label }}</option></select></label>
            <fieldset><legend>次题型，最多 2 个</legend><label v-for="type in typeOptions.filter(type => type.value !== primaryType)" :key="type.value"><input v-model="secondaryTypes" type="checkbox" :value="type.value" :disabled="secondaryTypes.length >= 2 && !secondaryTypes.includes(type.value)">{{ type.label }}</label></fieldset>
            <p class="qb-help">保存时同时更新整题标签与当前判定点的题型副本。</p>
            <AppButton variant="primary" :disabled="!primaryType || typeSaving" @click="saveTypes">{{ typeSaving ? '正在保存…' : '保存题型' }}</AppButton><AppButton variant="ghost" :disabled="typeSaving" @click="typeEditing = false">取消</AppButton>
          </div><FeedbackBanner v-if="typeError" role="alert" tone="error" :description="typeError" /></section>

          <section class="qb-evidence-summary">
            <header class="qb-section-heading">
              <h3>判定点（评分依据） <small v-if="points.length && criterion?.available && criterion.current_version?.quality_status === 'passed' && !store.detail.criteria_needs_review && points.every(point => directSkills(point).length)">{{ points.length }} 个判定点 · 全部质检通过</small><small v-else-if="points.length">{{ points.length }} 个判定点</small></h3>
              <div><template v-if="editing"><AppButton variant="primary" @click="saveAnnotation">保存</AppButton><AppButton variant="ghost" @click="cancelAnnotation">取消</AppButton></template><AppButton v-else variant="secondary" @click="editing = true">编辑标注</AppButton></div>
            </header>
            <p v-if="editing" class="qb-help">修改不会改写已有考试成绩与报告</p>
            <FeedbackBanner v-if="evidenceError" role="alert" tone="error" :description="evidenceError" />
            <p v-else-if="!points.length" class="qb-help">尚无可用判定点，请核对判定点。</p>
            <div v-if="points.length && !editing" class="qb-evidence-columns"><span>编号</span><span>判定点内容</span><span>状态</span></div>
            <template v-for="part in evidence?.available ? evidence.evidence?.parts ?? [] : []" :key="part.part_id">
            <p v-show="!editing" class="qb-part-heading">{{ part.label || part.part_id }} · {{ responseModeLabels[part.response_mode] }} · 小问难度 {{ partDifficulty(part.part_id) }}</p>
            <div v-for="(point, index) in part.evidence_points" v-show="!editing" :key="point.evidence_point_id" class="qb-evidence-row">
              <span>{{ index + 1 }}</span>
              <div><strong>{{ point.target }}</strong><p v-if="point.observable_evidence !== point.target">{{ point.observable_evidence }}</p>
                <div class="qb-skill-capsules"><button v-for="skill in directSkills(point)" :key="skill.key" type="button" class="qb-skill-capsule" :class="{ 'is-current': skill.key === currentSkill }" @click="emit('skill', skill.key)">{{ skill.label }}</button>
                  <span v-for="name in pointKnowledge(point)" :key="name" class="qb-topic-capsule">知识点 · {{ name }}</span>
                </div>
                <p v-if="pointPrerequisites(point).length" class="qb-help">前置知识 · {{ pointPrerequisites(point).join('、') }}</p>
                <div v-for="item in mappedPatterns(point.evidence_point_id)" :key="item.id" class="qb-point-error"><strong>↳ {{ item.pattern }}</strong><p>{{ item.explanation }}</p><AppButton variant="ghost" size="small" @click="beginPatternEdit(item)">调整</AppButton> <AppButton :disabled="patternSaving" variant="ghost" size="small" @click="changePattern(item, 'reject')">驳回</AppButton></div>
              </div>
              <small><span v-if="store.detail.criteria_needs_review">待审核</span><span v-if="!directSkills(point).length">未挂技能</span></small>
            </div>
            </template>
            <div v-show="editing"><TrainingCriterionReview v-if="store.selectedQuestionId" ref="review" :question-id="store.selectedQuestionId" :skill-labels="skillLabels" :loader="loadCriterion" :evidence-loader="loadEvidence" @updated="criterion = $event" /></div>
            <AppButton v-if="!editing" type="button" variant="ghost" size="small" @click="editing = true">核对判定点 / 重新生成</AppButton>
          </section>
          <section v-if="wrongOptionRows.length || otherPatterns.length || patternEdit || patternError || patternMessage" class="qb-paper-section qb-patterns" aria-labelledby="qb-patterns-title">
            <header class="qb-section-heading">
              <div>
                <h3 id="qb-patterns-title">本题典型错法</h3>
              </div>
            </header>
            <p v-if="patternEdit" class="qb-help">修改不会改写已有考试成绩与报告。</p>
            <div v-if="wrongOptionRows.length" class="qb-patterns__group">
              <h4>错误选项</h4>
              <div v-for="row in wrongOptionRows" :key="row.letter" class="qb-patterns__option">
                <strong class="qb-patterns__letter">{{ row.letter }}</strong>
                <div class="qb-patterns__content">
                  <p v-if="!row.patterns.length" class="qb-help">暂无该选项的错法说明</p>
                  <div v-for="item in row.patterns" :key="item.id" class="qb-patterns__item">
                    <strong>{{ item.pattern }}</strong>
                    <p v-if="item.explanation">{{ item.explanation }}</p>
                    <p v-if="patternSkillHint(item)" class="qb-patterns__meta">{{ patternSkillHint(item) }}</p>
                    <p class="qb-patterns__meta">{{ item.category || '未分类' }} · {{ patternSource(item.source) }} · {{ item.has_evidence ? '已有实际作答记录' : '尚无实际作答记录' }}</p>
                    <div class="qb-patterns__actions">
                      <AppButton type="button" variant="ghost" size="small" @click="beginPatternEdit(item)">调整</AppButton>
                      <AppButton type="button" :disabled="patternSaving" variant="ghost" size="small" @click="changePattern(item, 'reject')">驳回</AppButton>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="otherPatterns.length" class="qb-patterns__group">
              <h4>其他典型错法</h4>
              <div v-for="item in otherPatterns" :key="item.id" class="qb-patterns__item">
                <strong>{{ item.pattern }}</strong>
                <p v-if="item.explanation">{{ item.explanation }}</p>
                <p v-if="patternSkillHint(item)" class="qb-patterns__meta">{{ patternSkillHint(item) }}</p>
                <p class="qb-patterns__meta">{{ item.category || '未分类' }} · {{ patternTrigger(item) }} · {{ patternSource(item.source) }} · {{ item.has_evidence ? '已有实际作答记录' : '尚无实际作答记录' }}</p>
                <div class="qb-patterns__actions">
                  <AppButton type="button" variant="ghost" size="small" @click="beginPatternEdit(item)">调整</AppButton>
                  <AppButton type="button" :disabled="patternSaving" variant="ghost" size="small" @click="changePattern(item, 'reject')">驳回</AppButton>
                </div>
              </div>
            </div>
            <p v-if="!wrongOptionRows.length && !otherPatterns.length" class="qb-help">本题暂无典型错法。</p>
            <div v-if="patternEdit" class="qb-patterns__editor">
              <h4>调整典型错法</h4>
              <label>错法名称<input class="app-input" v-model="patternName" maxlength="80"></label>
              <label>错误大类<select class="app-input" v-model="patternCategory"><option value="">请选择</option><option v-for="category in availableCategories" :key="category" :value="category">{{ category }}</option></select></label>
              <label>关联技能<select class="app-input" v-model="patternSkill"><option value="">不关联</option><option v-for="skill in patternSkillOptions" :key="skill.key" :value="skill.key">{{ skill.label }}</option></select></label>
              <p v-if="patternEdit.skill_source === 'criterion' && (patternEdit.skill_labels?.length || patternEdit.skill_label)" class="qb-help">
                当前技能由判定点得出；选择其他技能将作为教师设定保存。
              </p>
              <div class="qb-patterns__actions">
                <AppButton type="button" variant="ghost" size="small" @click="patternEdit = null">取消</AppButton>
                <AppButton variant="primary" :disabled="patternSaving || !patternName.trim() || !patternCategory" @click="changePattern(patternEdit, 'edit')">{{ patternSaving ? '正在保存…' : '保存错法' }}</AppButton>
              </div>
            </div>
            <FeedbackBanner v-if="patternError" role="alert" tone="error" :description="patternError" />
            <FeedbackBanner v-if="patternMessage" role="status" tone="info" :description="patternMessage" />
          </section>


          <details class="qb-tags qb-property-editor"><summary>编辑题目属性 / 选择小节</summary>
            <header class="qb-section-heading">
              <div>
                <h3 id="qb-tags-title">标签核对</h3>
              </div>
              <div class="qb-tag-add">
                <select class="app-input" v-model="newTagType" aria-label="要添加的标签类别">
                  <option v-for="type in editableTagTypes" :key="type" :value="type">
                    {{ tagLabels[type] || type }}
                  </option>
                </select>
                <AppButton type="button" variant="ghost" size="small" @click="addSelectedTag">添加</AppButton>
              </div>
            </header>

            <div class="qb-core-tags" aria-label="核心标签完整度">
              <button
                v-for="status in coreTagStatus"
                :key="status.label"
                type="button"
                :class="{ 'is-complete': status.complete }"
                @click="!status.complete && status.type && addTag(status.type)"
              >
                <span aria-hidden="true">{{ status.complete ? '✓' : '+' }}</span>
                {{ status.label }}
              </button>
            </div>
            

            <label v-if="curriculum" class="qb-section-picker">
              <span>精确标定教材小节</span>
              <select class="app-input"
                :value="selectedSectionId"
                @change="chooseCurriculumSection(($event.currentTarget as HTMLSelectElement).value)"
              >
                <option value="">待标定（不猜测）</option>
                <optgroup
                  v-for="group in curriculumGroups"
                  :key="group.id"
                  :label="group.label"
                >
                  <option
                    v-for="section in group.chapter.sections"
                    :key="section.id"
                    :value="section.id"
                  >
                    {{ section.label }}
                  </option>
                </optgroup>
              </select>
              
              <small v-if="hasLegacySection" class="qb-help">
                当前教材小节来自旧版目录，目录中已找不到，请重新选择。
              </small>
            </label>

            <div v-if="tagGroups.length" class="qb-tag-editor">
              <div
                v-for="group in tagGroups"
                :key="group.type"
                class="qb-tag-group"
                :data-tone="tagTone(group.type)"
              >
                <div class="qb-tag-group__heading">
                  <strong>{{ group.label }}</strong>
                  <AppButton type="button" variant="ghost" size="small" @click="addTag(group.type)">添加</AppButton>
                </div>
                <div class="qb-tag-group__items">
                  <div
                    v-for="{ tag, index } in group.items"
                    :key="tagKey(tag)"
                    class="qb-tag-chip"
                  >
                    <span
                      v-if="group.type === 'knowledge_point' && !freshlyAddedTags.has(tag)"
                      :title="tag.tag_value"
                    >{{ knowledgeLeafLabel(tag.tag_value) }}</span>
                    <input class="app-input"
                      v-else
                      v-model="tag.tag_value"
                      maxlength="160"
                      :aria-label="`${group.label}标签值`"
                      placeholder="标签值"
                    ><AppIconButton :label="`移除${tag.tag_value || group.label}标签`"
                     
                     
                     
                      @click="removeTag(index)"
                     icon="close" />
                  </div>
                </div>
              </div>
            </div>
            <p v-else-if="store.tagDraft.length" class="qb-help">
              当前仅有历史学生层级标签；它会继续保留，但不再参与新筛选和完整度判断。
            </p>
            <p v-else class="qb-help">当前没有标签。</p>
            <FeedbackBanner v-if="store.writeMessage" :class="{ 'is-error': store.writeState !== 'idle' }" role="status" tone="info" :description="store.writeMessage" />
            <AppButton
              variant="primary"
              :disabled="store.writeState === 'saving' || !validTags()"
              @click="save"
            >
              {{ store.writeState === 'saving' ? '正在保存…' : '保存标签' }}
            </AppButton>
          </details>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.qb-part-heading { margin: 12px 0 4px; padding: 7px 10px; background: var(--color-bg-subtle); color: var(--color-text-secondary); font-size: var(--font-size-caption); }
.qb-type-editor { display: grid; gap: 10px; margin-top: 12px; }
.qb-type-editor fieldset { max-height: 180px; overflow: auto; border: 1px solid var(--color-border-subtle); }
.qb-type-editor fieldset label { display: block; font-size: var(--font-size-caption); }
</style>
