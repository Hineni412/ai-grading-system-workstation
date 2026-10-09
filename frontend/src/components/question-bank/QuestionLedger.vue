<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { QuestionBankListItem } from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useQuestionBankStore } from '../../stores/question-bank'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionCard from './QuestionCard.vue'
import QuestionSimilarSheet from './QuestionSimilarSheet.vue'

withDefaults(defineProps<{
  paperMode?: boolean
  questionLabels?: Record<number, string>
}>(), {
  paperMode: false,
  questionLabels: undefined,
})

const store = useQuestionBankStore()
function onEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented) return
  const popover = document.querySelector<HTMLDetailsElement>('.qb-label-popover[open], .qb-card-menu[open], .qb-paper-more[open], .paper-library__maintenance[open]')
  if (popover) { popover.open = false; popover.querySelector<HTMLElement>('summary')?.focus(); event.preventDefault(); return }
  if (similarSource.value) closeSimilar()
  else if (!document.querySelector('[role="dialog"][aria-modal="true"]')) void store.selectQuestion(null)
}
onMounted(() => document.addEventListener('keydown', onEscape))
onBeforeUnmount(() => document.removeEventListener('keydown', onEscape))
const assembly = useAssemblyStore()
const similarSource = ref<QuestionBankListItem | null>(null)

const allOnPage = computed(() => (
  store.questions.length > 0 &&
  store.selectedOnPageCount === store.questions.length
))

function changePage(next: number): void {
  if (next < 1 || next > store.totalPages || next === store.page) return
  void store.loadQuestions({ ...store.appliedFilters, page: next })
}

async function ensureAssembly(): Promise<void> {
  if (assembly.loadState === 'idle') await assembly.load()
}

async function addSelectionToAssembly(): Promise<void> {
  if (store.selectedCount === 0 || assembly.saveState === 'saving') return
  await ensureAssembly()
  await assembly.addQuestions(store.selectedQuestionIds)
}

function openSimilar(question: QuestionBankListItem): void {
  similarSource.value = question
}

function closeSimilar(): void {
  similarSource.value = null
}
</script>

<template>
  <section class="qb-ledger" aria-labelledby="qb-ledger-title">
    <header class="qb-section-heading">
      <div>
        <h2 id="qb-ledger-title">试题内容</h2>
      </div>
      <div class="qb-ledger__heading-actions">
        <label class="qb-select-page">
          <input
            type="checkbox"
            :checked="allOnPage"
            @change="store.selectCurrentPage(($event.currentTarget as HTMLInputElement).checked)"
          >
          <span>选择本页</span>
        </label>
        <strong>共 {{ store.total }} 题</strong>
      </div>
    </header>

    <div v-if="store.selectedCount" class="qb-selection-bar" role="status">
      <span>
        已选 <strong>{{ store.selectedCount }}</strong> 题
        <template v-if="store.hiddenSelectionCount">，其中 {{ store.hiddenSelectionCount }} 题在其他页</template>
      </span>
      <span class="qb-selection-bar__actions">
        <AppButton
          variant="primary"
          :disabled="assembly.saveState === 'saving'"
          @click="addSelectionToAssembly"
        >
          加入试卷篮
        </AppButton>
        <AppButton type="button" variant="ghost" size="small" @click="store.clearSelection">清空选择</AppButton>
      </span>
    </div>

    <FeedbackBanner v-if="store.listState === 'stale-error'" role="alert" tone="warning" description="新数据暂时无法读取，当前仍显示上一次成功结果。" />
    <StatePanel v-if="store.listState === 'loading' && store.questions.length === 0" kind="loading" title="正在读取试题…" />
    <StatePanel v-else-if="store.listState === 'error'" kind="error" :title="store.listError" retry-label="重新加载" @retry="store.loadQuestions(store.appliedFilters)" />
    <StatePanel v-else-if="store.listState === 'empty'" kind="empty" title="当前条件下没有试题，可以清除筛选后再查看。" />
    <div v-else class="qb-question-list">
      <QuestionCard v-for="question in store.questions" :key="question.id" :question="question" :paper-mode="paperMode" :badge-label="questionLabels?.[question.id]" @similar="openSimilar" />
    </div>

    <footer v-if="store.totalPages > 1" class="qb-pagination">
      <AppButton variant="ghost" :disabled="store.page <= 1" @click="changePage(store.page - 1)">
        上一页
      </AppButton>
      <span>第 {{ store.page }} / {{ store.totalPages }} 页</span>
      <AppButton variant="ghost" :disabled="store.page >= store.totalPages" @click="changePage(store.page + 1)">
        下一页
      </AppButton>
    </footer>
  </section>

  <QuestionSimilarSheet :source="similarSource" @close="closeSimilar" />
</template>
