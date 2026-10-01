<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  knowledgeLeafLabel,
  QUESTION_BANK_TAG_TYPES,
  questionBankApi,
  questionTypeWithSubtype,
  type CurriculumCatalog,
  type QuestionErrorPattern,
  type QuestionBankTag,
} from '../../api/question-bank'
import { CAUSE_CATEGORIES } from '../../api/class-analysis'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import TrainingCriterionReview from './TrainingCriterionReview.vue'

const store = useQuestionBankStore()
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
  (store.detail?.error_patterns ?? []).filter((item) => item.trigger_kind !== 'option')
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
  if (action === 'reject' && !window.confirm(`驳回“${item.pattern}”这条典型错法？`)) return
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
  (type) => !['student_level', 'canonical_knowledge_id', 'curriculum_section', 'skill'].includes(type),
)
const newTagType = ref<QuestionBankTag['tag_type']>('knowledge_point')

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

const coreTagTypes: QuestionBankTag['tag_type'][] = ['knowledge_point', 'ability', 'exam_scope']
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

async function removeCurrent(): Promise<void> {
  if (!store.detail) return
  const confirmed = window.confirm(
    `确认把第 ${store.detail.question_number || store.detail.id} 题移出当前题库吗？删除后可立即恢复。`,
  )
  if (confirmed) await store.deleteCurrent()
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="store.detailState !== 'idle'"
      class="qb-drawer-layer"
      role="presentation"
      @click.self="store.selectQuestion(null)"
    >
      <aside class="qb-inspector" role="dialog" aria-modal="true" aria-labelledby="qb-inspector-title">
        <div v-if="store.detailState === 'loading'" class="qb-inspector__empty" role="status">
          正在打开题目详情…
        </div>
        <div v-else-if="store.detailState === 'error'" class="qb-inspector__empty" role="alert">
          <span>{{ store.detailError }}</span>
          <button
            v-if="store.selectedQuestionId"
            type="button"
            class="qb-link"
            @click="store.selectQuestion(store.selectedQuestionId)"
          >
            重新打开
          </button>
        </div>
        <template v-else-if="store.detail">
          <header class="qb-inspector__heading">
            <div>
              <p class="qb-eyebrow">QUESTION DETAIL</p>
              <h2 id="qb-inspector-title" tabindex="-1">
                第 {{ store.detail.question_number || store.detail.id }} 题
              </h2>
              <p>{{ store.detail.paper_title || '未命名试卷' }}</p>
              <RouterLink
                class="qb-link"
                :to="{ path: '/authoring', query: { source: store.detail.id } }"
              >
                用这道题练习
              </RouterLink>
            </div>
            <button type="button" class="qb-drawer-close" aria-label="关闭题目详情" @click="store.selectQuestion(null)">×</button>
          </header>
          <p v-if="store.detail.duplicate_of_question_id" class="qb-feedback">
            与题库 <button type="button" class="qb-link" @click="store.selectQuestion(store.detail.duplicate_of_question_id)">#{{ store.detail.duplicate_of_question_id }}</button> 相同，{{ store.detail.duplicate_labels_reused ? '已复用标签' : '已关联，标签待补齐' }}。
          </p>
          <p
            v-if="store.detail.criteria_needs_review"
            class="qb-feedback is-warning"
            role="status"
          >
            本题判定点待审核。请到下方「判定点」核对、修正或重新生成。
          </p>

          <section class="qb-tags" aria-labelledby="qb-tags-title">
            <header class="qb-section-heading">
              <div>
                <p class="qb-eyebrow">TEACHER CONFIRMATION</p>
                <h3 id="qb-tags-title">标签核对</h3>
              </div>
              <div class="qb-tag-add">
                <select class="app-input" v-model="newTagType" aria-label="要添加的标签类别">
                  <option v-for="type in editableTagTypes" :key="type" :value="type">
                    {{ tagLabels[type] || type }}
                  </option>
                </select>
                <button type="button" class="qb-link" @click="addSelectedTag">添加</button>
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
            <p class="qb-help">知识点、能力、教材章节/范围和有效难度四项齐全后，才计入试卷的完整进度。</p>

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
              <small>选择后会同时校准所属章节；旧题未选择时继续显示“待标定”。</small>
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
                  <button type="button" class="qb-link" @click="addTag(group.type)">添加</button>
                </div>
                <div class="qb-tag-group__items">
                  <div
                    v-for="{ tag, index } in group.items"
                    :key="index"
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
                    >
                    <button
                      type="button"
                      class="qb-tag-chip__remove"
                      :aria-label="`移除${tag.tag_value || group.label}标签`"
                      @click="removeTag(index)"
                    >
                      ×
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <p v-else-if="store.tagDraft.length" class="qb-help">
              当前仅有历史学生层级标签；它会继续保留，但不再参与新筛选和完整度判断。
            </p>
            <p v-else class="qb-help">当前没有标签。可手动添加，或在导入任务区选择题目后启动 AI 标注。</p>
            <p
              v-if="store.writeMessage"
              class="qb-feedback"
              :class="{ 'is-error': store.writeState !== 'idle' }"
              role="status"
            >
              {{ store.writeMessage }}
            </p>
            <AppButton
              variant="primary"
              :disabled="store.writeState === 'saving' || !validTags()"
              @click="save"
            >
              {{ store.writeState === 'saving' ? '正在保存…' : '保存标签' }}
            </AppButton>
          </section>

          <TrainingCriterionReview
            :question-id="store.detail.id"
            :skill-labels="skillLabels"
          />

          <section class="qb-paper-section qb-patterns" aria-labelledby="qb-patterns-title">
            <header class="qb-section-heading">
              <div>
                <p class="qb-eyebrow">TYPICAL ERRORS</p>
                <h3 id="qb-patterns-title">本题典型错法</h3>
              </div>
            </header>
            <p class="qb-help">预测可用于提醒和讲评；只有结合实际作答，才算学生出现。这里的调整不会自动改写已有考试成绩、历史报告或学生错因记录。</p>
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
                      <button type="button" class="qb-link" @click="beginPatternEdit(item)">调整</button>
                      <button type="button" class="qb-link" :disabled="patternSaving" @click="changePattern(item, 'reject')">驳回</button>
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
                  <button type="button" class="qb-link" @click="beginPatternEdit(item)">调整</button>
                  <button type="button" class="qb-link" :disabled="patternSaving" @click="changePattern(item, 'reject')">驳回</button>
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
                <button type="button" class="qb-link" @click="patternEdit = null">取消</button>
                <AppButton variant="primary" :disabled="patternSaving || !patternName.trim() || !patternCategory" @click="changePattern(patternEdit, 'edit')">{{ patternSaving ? '正在保存…' : '保存错法' }}</AppButton>
              </div>
            </div>
            <p v-if="patternError" class="qb-feedback is-error" role="alert">{{ patternError }}</p>
            <p v-if="patternMessage" class="qb-feedback" role="status">{{ patternMessage }}</p>
          </section>

          <dl class="qb-facts">
            <div><dt>题型</dt><dd>{{ questionTypeWithSubtype(store.detail.question_type, store.detail.tags) }}</dd></div>
            <div><dt>难度</dt><dd>{{ store.detail.difficulty || '待定' }}</dd></div>
            <div><dt>页码</dt><dd>{{ store.detail.page_range || '未记录' }}</dd></div>
            <div><dt>图片</dt><dd>{{ store.detail.has_images ? '包含' : '无' }}</dd></div>
          </dl>

          <details class="qb-paper-section qb-answer-section">
            <summary>查看题干</summary>
            <QuestionContentRenderer
              :blocks="store.detail.rich_content.question_blocks"
              :fallback="store.detail.question_text"
              image-alt="题目配图"
              media-mode="detail"
            />
          </details>

          <details class="qb-paper-section qb-answer-section">
            <summary>答案与解析</summary>
            <QuestionContentRenderer
              :blocks="store.detail.rich_content.answer_blocks"
              :fallback="store.detail.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
              media-mode="detail"
            />
          </details>

          <section v-if="store.detail.previews.length" class="qb-paper-section">
            <h3>原卷预览</h3>
            <div class="qb-preview-grid">
              <template v-for="preview in store.detail.previews" :key="preview.preview_type">
                <a v-if="preview.url" :href="preview.url" target="_blank" rel="noopener">
                  {{ preview.preview_type === 'question' ? '打开题目原卷' : '打开答案原卷' }}
                  <span v-if="preview.page_number"> · 第 {{ preview.page_number }} 页</span>
                </a>
                <span v-else>
                  {{ preview.preview_type === 'question' ? '题目原卷' : '答案原卷' }}暂不可用
                </span>
              </template>
            </div>
          </section>

          <section class="qb-danger">
            <h3>移出当前题库</h3>
            <p>题目会从活动列表隐藏，但不会物理删除标签，可立即恢复。</p>
            <AppButton
              variant="danger"
              :disabled="store.writeState === 'saving'"
              @click="removeCurrent"
            >
              删除这道题
            </AppButton>
          </section>
        </template>
      </aside>
    </div>
  </Teleport>
</template>
