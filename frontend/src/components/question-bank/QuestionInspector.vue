<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  QUESTION_BANK_TAG_TYPES,
  questionBankApi,
  type CurriculumCatalog,
  type QuestionBankTag,
} from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import TrainingCriterionReview from './TrainingCriterionReview.vue'
import SolutionEvidenceReview from './SolutionEvidenceReview.vue'

const store = useQuestionBankStore()
const curriculum = ref<CurriculumCatalog | null>(null)
const editableTagTypes = QUESTION_BANK_TAG_TYPES.filter(
  (type) => !['student_level', 'canonical_knowledge_id'].includes(type),
)
const newTagType = ref<QuestionBankTag['tag_type']>('knowledge_point')

const tagLabels: Record<string, string> = {
  ability: '能力',
  curriculum_section: '教材小节',
  error_type: '错误类型',
  exam_scope: '教材章节/考试范围',
  knowledge_point: '知识点',
  measured_skill_name: '测量技能',
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

const curriculumGroups = computed(() => (
  (curriculum.value?.volumes ?? []).flatMap((volume) => (
    volume.chapters.map((chapter) => ({
      id: chapter.id,
      label: `${volume.label} · ${chapter.label}`,
      chapter,
    }))
  ))
))

function curriculumSectionLabel(sectionId: string): string {
  for (const group of curriculumGroups.value) {
    const section = group.chapter.sections.find(item => item.id === sectionId)
    if (section) return `${group.label} · ${section.label}`
  }
  return '旧版教材小节（目录中已找不到，请重新选择）'
}

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
  store.replaceTagDraft([
    ...store.tagDraft,
    { tag_type: tagType, tag_value: '', confidence: null },
  ])
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
            </div>
            <button type="button" class="qb-drawer-close" aria-label="关闭题目详情" @click="store.selectQuestion(null)">×</button>
          </header>

          <dl class="qb-facts">
            <div><dt>题型</dt><dd>{{ store.detail.question_type || '未分类' }}</dd></div>
            <div><dt>难度</dt><dd>{{ store.detail.difficulty || '待定' }}</dd></div>
            <div><dt>页码</dt><dd>{{ store.detail.page_range || '未记录' }}</dd></div>
            <div><dt>图片</dt><dd>{{ store.detail.has_images ? '包含' : '无' }}</dd></div>
          </dl>

          <section class="qb-paper-section">
            <h3>题干</h3>
            <QuestionContentRenderer
              :blocks="store.detail.rich_content.question_blocks"
              :fallback="store.detail.question_text"
              image-alt="题目配图"
              media-mode="detail"
            />
          </section>

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

          <SolutionEvidenceReview :question-id="store.detail.id" />
          <TrainingCriterionReview :question-id="store.detail.id" />

          <section class="qb-tags" aria-labelledby="qb-tags-title">
            <header class="qb-section-heading">
              <div>
                <p class="qb-eyebrow">TEACHER CONFIRMATION</p>
                <h3 id="qb-tags-title">标签核对</h3>
              </div>
              <div class="qb-tag-add">
                <select v-model="newTagType" aria-label="要添加的标签类别">
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
              <select
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
                      v-if="group.type === 'curriculum_section'"
                      class="qb-tag-chip__localized-value"
                    >{{ curriculumSectionLabel(tag.tag_value) }}</span>
                    <input
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
            <button
              type="button"
              class="qb-button is-primary"
              :disabled="store.writeState === 'saving' || !validTags()"
              @click="save"
            >
              {{ store.writeState === 'saving' ? '正在保存…' : '保存标签' }}
            </button>
          </section>

          <section class="qb-danger">
            <h3>移出当前题库</h3>
            <p>题目会从活动列表隐藏，但不会物理删除标签，可立即恢复。</p>
            <button
              type="button"
              class="qb-button is-danger"
              :disabled="store.writeState === 'saving'"
              @click="removeCurrent"
            >
              删除这道题
            </button>
          </section>
        </template>
      </aside>
    </div>
  </Teleport>
</template>
