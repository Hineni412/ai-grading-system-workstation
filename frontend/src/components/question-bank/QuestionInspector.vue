<script setup lang="ts">
import { ref } from 'vue'

import { QUESTION_BANK_TAG_TYPES, type QuestionBankTag } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'

const store = useQuestionBankStore()
const missingMedia = ref<string[]>([])

const tagLabels: Record<string, string> = {
  ability: '能力',
  canonical_knowledge_id: '标准知识点 ID',
  error_type: '错误类型',
  exam_scope: '考试范围',
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

function addTag(): void {
  store.replaceTagDraft([
    ...store.tagDraft,
    { tag_type: 'knowledge_point', tag_value: '', confidence: null },
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
    tag.tag_value.trim().length > 0
    && tag.tag_value.trim().length <= 36
    && (tag.confidence === null || (
      Number.isFinite(tag.confidence)
      && tag.confidence >= 0
      && tag.confidence <= 1
    ))
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

function mediaFailed(url: string): void {
  if (!missingMedia.value.includes(url)) missingMedia.value.push(url)
}
</script>

<template>
  <aside class="qb-inspector" aria-labelledby="qb-inspector-title">
    <div v-if="store.detailState === 'idle'" class="qb-inspector__empty">
      <span class="qb-binding-mark" aria-hidden="true">题页</span>
      <strong>打开一道题开始核对</strong>
      <p>完整题干、答案、图片、预览和标签会在这里展开。</p>
    </div>
    <div v-else-if="store.detailState === 'loading'" class="qb-inspector__empty" role="status">
      正在摊开题页……
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
          <p class="qb-eyebrow">摊开的题页</p>
          <h2 id="qb-inspector-title" tabindex="-1">
            第 {{ store.detail.question_number || store.detail.id }} 题
          </h2>
        </div>
        <button type="button" class="qb-link" @click="store.selectQuestion(null)">收起</button>
      </header>

      <dl class="qb-facts">
        <div><dt>题型</dt><dd>{{ store.detail.question_type || '未分类' }}</dd></div>
        <div><dt>难度</dt><dd>{{ store.detail.difficulty || '—' }}</dd></div>
        <div><dt>页码</dt><dd>{{ store.detail.page_range || '—' }}</dd></div>
        <div><dt>来源</dt><dd>{{ store.detail.paper_title || '未命名试卷' }}</dd></div>
      </dl>

      <section class="qb-paper-section">
        <h3>题干</h3>
        <template v-if="store.detail.rich_content.available && store.detail.rich_content.question_blocks.length">
          <div v-for="(block, index) in store.detail.rich_content.question_blocks" :key="index" class="qb-rich-block">
            <p>{{ block.text || '此段只有图片素材' }}</p>
            <template v-for="url in block.asset_urls" :key="url">
              <img
                v-if="!missingMedia.includes(url)"
                :src="url"
                alt="题目图片素材"
                @error="mediaFailed(url)"
              >
              <p v-else class="qb-media-missing">这张图片暂时无法读取，题干文字仍可查看。</p>
            </template>
          </div>
        </template>
        <p v-else class="qb-question-copy">{{ store.detail.question_text || '题干暂缺' }}</p>
      </section>

      <details class="qb-paper-section">
        <summary>答案与解析</summary>
        <template v-if="store.detail.rich_content.answer_blocks.length">
          <div v-for="(block, index) in store.detail.rich_content.answer_blocks" :key="index" class="qb-rich-block">
            <p>{{ block.text || '此段只有图片素材' }}</p>
            <template v-for="url in block.asset_urls" :key="url">
              <img
                v-if="!missingMedia.includes(url)"
                :src="url"
                alt="答案图片素材"
                @error="mediaFailed(url)"
              >
              <p v-else class="qb-media-missing">这张答案图片暂时无法读取，答案文字仍可查看。</p>
            </template>
          </div>
        </template>
        <p v-else>{{ store.detail.answer_text || '暂未录入答案或解析。' }}</p>
      </details>

      <section v-if="store.detail.previews.length" class="qb-paper-section">
        <h3>原卷预览</h3>
        <div class="qb-preview-grid">
          <template v-for="preview in store.detail.previews" :key="preview.preview_type">
            <a v-if="preview.url" :href="preview.url" target="_blank" rel="noopener">
              {{ preview.preview_type === 'question' ? '打开题目预览' : '打开答案预览' }}
              <span v-if="preview.page_number"> · 第 {{ preview.page_number }} 页</span>
            </a>
            <span v-else>{{ preview.preview_type === 'question' ? '题目预览' : '答案预览' }}暂不可用</span>
          </template>
        </div>
      </section>

      <section class="qb-tags" aria-labelledby="qb-tags-title">
        <div class="qb-section-heading">
          <div>
            <p class="qb-eyebrow">教师确认</p>
            <h3 id="qb-tags-title">当前标签全集</h3>
          </div>
          <button type="button" class="qb-link" @click="addTag">添加标签</button>
        </div>
        <p class="qb-help">保存会替换这道题的完整标签集；不会改写题干和答案。</p>
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
        <p v-else class="qb-help">当前没有标签。可手动添加，或在批量任务中使用 AI 生成候选结果。</p>
        <p v-if="store.writeMessage" class="qb-feedback" :class="{ 'is-error': store.writeState !== 'idle' }" role="status">
          {{ store.writeMessage }}
        </p>
        <button
          type="button"
          class="qb-button qb-button--primary"
          :disabled="store.writeState === 'saving' || !validTags()"
          @click="save"
        >
          {{ store.writeState === 'saving' ? '正在保存……' : '保存标签' }}
        </button>
      </section>

      <section class="qb-danger">
        <h3>移出当前题库</h3>
        <p>该题将不再出现在活动题库列表中，但不会物理删除标签。</p>
        <button type="button" class="qb-button qb-button--danger" :disabled="store.writeState === 'saving'" @click="removeCurrent">
          删除这道题
        </button>
      </section>
    </template>
  </aside>
</template>
