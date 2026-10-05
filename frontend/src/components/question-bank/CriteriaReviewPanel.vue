<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { DialogRoot, DialogPortal, DialogContent, DialogTitle } from 'reka-ui'

import {
  questionBankApi,
  type QuestionBankListItem,
} from '../../api/question-bank'
import AppIconButton from '../design-system/AppIconButton.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'

const props = defineProps<{
  open: boolean
  returnFocus?: HTMLElement | null
}>()

const emit = defineEmits<{
  close: []
  openQuestion: [question: QuestionBankListItem]
}>()

const closeButton = ref<HTMLButtonElement | null>(null)
const loadState = ref<'idle' | 'loading' | 'ready' | 'empty' | 'error'>('idle')
const items = ref<QuestionBankListItem[]>([])
const total = ref(0)
const message = ref('')
let loadController: AbortController | null = null

watch(
  () => props.open,
  (open) => {
    if (!open) {
      loadController?.abort()
      return
    }
    void load()
    void nextTick(() => closeButton.value?.focus())
  },
  { immediate: true },
)

function snippet(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 80)
}

async function load(): Promise<void> {
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  loadState.value = 'loading'
  message.value = ''
  try {
    const result = await questionBankApi.listQuestions({
      page: 1,
      pageSize: 100,
      criteriaNeedsReview: true,
    }, controller.signal)
    if (controller.signal.aborted) return
    items.value = result.items
    total.value = result.total
    loadState.value = result.total === 0 ? 'empty' : 'ready'
  } catch {
    if (controller.signal.aborted) return
    loadState.value = 'error'
    message.value = '待审核题目暂时读不出来，请稍后重试。'
  }
}

function openQuestion(question: QuestionBankListItem): void {
  emit('openQuestion', question)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  loadController?.abort()
})
</script>

<template>
  <DialogRoot :open="open" @update:open="!$event && emit('close')"><DialogPortal>
    <div
      v-if="open"
      class="qb-drawer-layer taxonomy-review-layer"
      role="presentation"
      @click.self="emit('close')"
    >
      <DialogContent as="aside"
        class="taxonomy-review criteria-review"
        :aria-describedby="undefined"
        aria-labelledby="criteria-review-title"
        @close-auto-focus="returnFocus && ($event.preventDefault(), returnFocus.focus())"
      >
        <header class="taxonomy-review__header">
          <div>
            <DialogTitle as="h2" id="criteria-review-title">判定点待审核</DialogTitle>
            <p>正常通过质检的判定点已自动可用。这里只列出仍无法确定的个别题目；点开后可修正或重新生成。</p>
          </div><AppIconButton label="关闭判定点待审核"
            ref="closeButton"
           
           
           
            @click="emit('close')"
           icon="close" />
        </header>

        <div class="taxonomy-review__toolbar">
          <div class="taxonomy-review__summary">
            <strong>{{ loadState === 'loading' ? '…' : total }}</strong>
            <span>道题等待核对</span>
          </div>
          <AppButton
            variant="ghost"
            :disabled="loadState === 'loading'"
            @click="load"
          >
            {{ loadState === 'loading' ? '正在读取…' : '刷新' }}
          </AppButton>
        </div>

        <StatePanel v-if="loadState === 'loading'" kind="loading" title="正在读取需要核对的判定点…" />
        <StatePanel v-else-if="loadState === 'error'" kind="error" :title="message || '判定点暂时无法读取'" retry-label="重新读取" @retry="load" />
        <StatePanel v-else-if="loadState === 'empty'" kind="empty" title="当前没有需要核对的判定点。" />
        <ul v-else class="criteria-review__list">
          <li v-for="question in items" :key="question.id">
            <article class="criteria-review__item">
              <div>
                <p>{{ question.paper_title || '未命名试卷' }}</p>
                <h3>第 {{ question.question_number || question.id }} 题</h3>
                <p>{{ snippet(question.question_text) || '本题暂无题干预览' }}</p>
              </div>
              <AppButton variant="primary" @click="openQuestion(question)">
                打开核对
              </AppButton>
            </article>
          </li>
        </ul>
        <p v-if="loadState === 'ready' && total > items.length" class="criteria-review__empty">
          仅显示前 {{ items.length }} 道，其余请刷新或逐卷打开处理。
        </p>
      </DialogContent>
    </div>
  </DialogPortal></DialogRoot>
</template>
