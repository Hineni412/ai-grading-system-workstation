<script setup lang="ts">
import { ref, watch } from 'vue'

import { questionBankApi, type QuestionBankDetail } from '../../api/question-bank'
import { ApiError } from '../../api/errors'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const props = defineProps<{
  questionId: number | null
  title?: string
}>()
const emit = defineEmits<{ close: [] }>()

type PanelState = 'idle' | 'loading' | 'ready' | 'error' | 'missing'

const detail = ref<QuestionBankDetail | null>(null)
const panelState = ref<PanelState>('idle')
let controller: AbortController | null = null

async function load(questionId: number): Promise<void> {
  controller?.abort()
  const active = new AbortController()
  controller = active
  detail.value = null
  panelState.value = 'loading'
  try {
    const loaded = await questionBankApi.getQuestion(questionId, active.signal)
    if (active.signal.aborted) return
    detail.value = loaded
    panelState.value = 'ready'
  } catch (error) {
    if (active.signal.aborted) return
    panelState.value = error instanceof ApiError && error.status === 404
      ? 'missing'
      : 'error'
  } finally {
    if (controller === active) controller = null
  }
}

watch(
  () => props.questionId,
  (questionId) => {
    controller?.abort()
    controller = null
    if (questionId === null) {
      detail.value = null
      panelState.value = 'idle'
      return
    }
    void load(questionId)
  },
  { immediate: true },
)

function retry(): void {
  if (props.questionId !== null) void load(props.questionId)
}

function close(): void {
  controller?.abort()
  controller = null
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="panelState !== 'idle'"
      class="question-preview-layer"
      role="presentation"
      @click.self="close"
    >
      <aside
        class="question-preview"
        role="dialog"
        aria-modal="true"
        aria-labelledby="question-preview-title"
      >
        <div v-if="panelState === 'loading'" class="question-preview__empty" role="status">
          正在打开题库题目…
        </div>
        <div v-else-if="panelState === 'missing'" class="question-preview__empty" role="alert">
          <span>该题已不在题库中；草稿内容不受影响，可关闭预览继续审核。</span>
          <button type="button" class="question-preview__close-inline" @click="close">关闭</button>
        </div>
        <div v-else-if="panelState === 'error'" class="question-preview__empty" role="alert">
          <span>题目暂时无法读取，草稿内容不受影响。</span>
          <button type="button" class="question-preview__retry" @click="retry">重试</button>
        </div>
        <template v-else-if="detail">
          <header class="question-preview__heading">
            <div>
              <h2 id="question-preview-title" tabindex="-1">
                {{ title || `第 ${detail.question_number || detail.id} 题` }}
              </h2>
              <p>{{ detail.paper_title || '未命名试卷' }}</p>
            </div>
            <button type="button" class="question-preview__close" aria-label="关闭题目预览" @click="close">×</button>
          </header>

          <section class="question-preview__section">
            <h3>题干</h3>
            <QuestionContentRenderer
              :blocks="detail.rich_content.question_blocks"
              :fallback="detail.question_text"
              image-alt="题目配图"
              media-mode="detail"
            />
          </section>

          <details class="question-preview__section">
            <summary>答案与解析</summary>
            <QuestionContentRenderer
              :blocks="detail.rich_content.answer_blocks"
              :fallback="detail.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
              media-mode="detail"
            />
          </details>
        </template>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.question-preview-layer { position: fixed; inset: 0; z-index: 60; display: flex; justify-content: flex-end; background: color-mix(in srgb, var(--color-text-primary) 24%, transparent); }
.question-preview { width: min(620px, 94vw); max-width: 100%; height: 100%; overflow-y: auto; padding: 20px; border-left: 1px solid var(--color-border-default); background: var(--color-bg-surface); box-shadow: -18px 0 44px color-mix(in srgb, var(--color-text-primary) 14%, transparent); }
.question-preview__empty { display: grid; gap: var(--space-3); justify-items: start; padding: var(--space-6) 0; color: var(--color-text-secondary); }
.question-preview__retry,
.question-preview__close-inline { padding: 6px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-accent-active); cursor: pointer; }
.question-preview__heading { display: flex; justify-content: space-between; align-items: start; gap: var(--space-3); }
.question-preview__heading h2 { margin: 0 0 var(--space-1); }
.question-preview__heading p { margin: 0; color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.question-preview__close { flex: none; width: 32px; height: var(--control-height-default); border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-secondary); font-size: var(--font-size-h2); line-height: 1; cursor: pointer; }
.question-preview__section { margin-top: var(--space-4); }
.question-preview__section h3 { margin: 0 0 var(--space-2); }
.question-preview__section summary { cursor: pointer; font-weight: var(--font-weight-semibold); }
.question-preview__section summary + * { margin-top: var(--space-2); }
</style>
