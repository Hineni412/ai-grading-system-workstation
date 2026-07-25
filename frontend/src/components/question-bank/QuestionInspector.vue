<script setup lang="ts">
import { computed } from 'vue'

import {
  QUESTION_BANK_TAG_TYPES,
  type QuestionBankTag,
} from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

const store = useQuestionBankStore()

const tagLabels: Record<string, string> = {
  ability: '能力',
  canonical_knowledge_id: '标准知识点 ID',
  error_type: '错误类型',
  exam_scope: '教材章节/考试范围',
  knowledge_point: '知识点',
  measured_skill_name: '测量技能',
  method: '解题方法',
  model: '模型',
  prerequisite: '前置知识',
  student_level: '学生层级',
  sub_skill: '子技能',
  supporting_skill_name: '支撑技能',
  teaching_stage: '教学阶段',
}

const coreTagTypes = ['knowledge_point', 'ability', 'exam_scope', 'student_level']
const coreTagStatus = computed(() => coreTagTypes.map((type) => ({
  type,
  label: tagLabels[type],
  complete: store.tagDraft.some((tag) => tag.tag_type === type && tag.tag_value.trim()),
})))

function addTag(tagType: QuestionBankTag['tag_type'] = 'knowledge_point'): void {
  store.replaceTagDraft([
    ...store.tagDraft,
    { tag_type: tagType, tag_value: '', confidence: null },
  ])
}

function removeTag(index: number): void {
  store.replaceTagDraft(store.tagDraft.filter((_tag, itemIndex) => itemIndex !== index))
}

function updateConfidence(tag: QuestionBankTag, value: string): void {
  tag.confidence = value === '' ? null : Number(value)
}

function validTags(): boolean {
  return store.tagDraft.every((tag) => (
    tag.tag_value.trim().length > 0 &&
    tag.tag_value.trim().length <= 36 &&
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
            />
          </section>

          <details class="qb-paper-section qb-answer-section">
            <summary>答案与解析</summary>
            <QuestionContentRenderer
              :blocks="store.detail.rich_content.answer_blocks"
              :fallback="store.detail.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
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

          <section class="qb-tags" aria-labelledby="qb-tags-title">
            <header class="qb-section-heading">
              <div>
                <p class="qb-eyebrow">TEACHER CONFIRMATION</p>
                <h3 id="qb-tags-title">标签核对</h3>
              </div>
              <button type="button" class="qb-link" @click="addTag()">添加标签</button>
            </header>

            <div class="qb-core-tags" aria-label="核心标签完整度">
              <button
                v-for="status in coreTagStatus"
                :key="status.type"
                type="button"
                :class="{ 'is-complete': status.complete }"
                @click="!status.complete && addTag(status.type as QuestionBankTag['tag_type'])"
              >
                <span aria-hidden="true">{{ status.complete ? '✓' : '+' }}</span>
                {{ status.label }}
              </button>
            </div>
            <p class="qb-help">知识点、能力、教材章节/范围和学生层级四项齐全后，才计入试卷的完整进度。</p>

            <div v-if="store.tagDraft.length" class="qb-tag-editor">
              <div v-for="(tag, index) in store.tagDraft" :key="index" class="qb-tag-row">
                <select v-model="tag.tag_type" :aria-label="`第 ${index + 1} 个标签类型`">
                  <option v-for="type in QUESTION_BANK_TAG_TYPES" :key="type" :value="type">
                    {{ tagLabels[type] || type }}
                  </option>
                </select>
                <input
                  v-model="tag.tag_value"
                  maxlength="36"
                  :aria-label="`第 ${index + 1} 个标签值`"
                  placeholder="标签值"
                >
                <input
                  :value="tag.confidence ?? ''"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  :aria-label="`第 ${index + 1} 个标签置信度`"
                  placeholder="置信度"
                  @input="updateConfidence(tag, ($event.currentTarget as HTMLInputElement).value)"
                >
                <button type="button" class="qb-link is-danger" @click="removeTag(index)">移除</button>
              </div>
            </div>
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
