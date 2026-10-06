<script setup lang="ts">
import { ref, watch } from 'vue'

import { questionBankApi, type QuestionBankDetail } from '../../api/question-bank'
import { ApiError } from '../../api/errors'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
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

function onUpdateOpen(open: boolean): void {
  if (!open) close()
}
</script>

<template>
  <Sheet :open="panelState !== 'idle'" @update:open="onUpdateOpen">
    <SheetContent
      class="question-preview-sheet"
      :aria-describedby="undefined"
    >
      <StatePanel v-if="panelState === 'loading'" kind="loading" title="正在打开题库题目…" />
      <StatePanel
        v-else-if="panelState === 'missing'"
        kind="empty"
        title="该题已不在题库中"
        description="草稿内容不受影响，可关闭预览继续审核。"
      >
        <template #actions><AppButton variant="ghost" size="small" @click="close">关闭</AppButton></template>
      </StatePanel>
      <StatePanel
        v-else-if="panelState === 'error'"
        kind="error"
        title="题目暂时无法读取"
        description="草稿内容不受影响。"
        retry-label="重新加载"
        @retry="retry"
      />
      <template v-else-if="detail">
        <SheetHeader class="question-preview__heading">
          <SheetTitle tabindex="-1">
            {{ title || `第 ${detail.question_number || detail.id} 题` }}
          </SheetTitle>
          <p class="question-preview__sub">{{ detail.paper_title || '未命名试卷' }}</p>
        </SheetHeader>

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
    </SheetContent>
  </Sheet>
</template>

<style scoped>
.question-preview-sheet { width: min(620px, 94vw); max-width: none; padding: 0 var(--space-5) var(--space-5); overflow-y: auto; }
.question-preview__heading p,
.question-preview__sub { margin: 0; color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.question-preview__section { margin-top: var(--space-4); }
.question-preview__section h3 { margin: 0 0 var(--space-2); }
.question-preview__section summary { cursor: pointer; font-weight: var(--font-weight-semibold); }
.question-preview__section summary + * { margin-top: var(--space-2); }
</style>
