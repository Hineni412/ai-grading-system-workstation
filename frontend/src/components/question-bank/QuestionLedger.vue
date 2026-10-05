<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle } from 'reka-ui'

import {
  knowledgeLeafLabel,
  questionBankApi,
  type QuestionBankListItem,
  type SimilarityReason,
  type SimilarityReasonKind,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppIconButton from '../design-system/AppIconButton.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionCard from './QuestionCard.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

withDefaults(defineProps<{
  paperMode?: boolean
  currentSkill?: string
}>(), {
  paperMode: false,
})

const emit = defineEmits<{ skill: [key: string] }>()
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
const similarItems = ref<SimilarQuestionItem[]>([])
const similarSource = ref<QuestionBankListItem | null>(null)
const similarState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')

const allOnPage = computed(() => (
  store.questions.length > 0 &&
  store.selectedOnPageCount === store.questions.length
))

function changePage(next: number): void {
  if (next < 1 || next > store.totalPages || next === store.page) return
  void store.loadQuestions({ ...store.appliedFilters, page: next })
}

const basketIdSet = computed(() => new Set(assembly.draft.basket_ids))

function isInBasket(questionId: number): boolean {
  return basketIdSet.value.has(questionId)
}

async function ensureAssembly(): Promise<void> {
  if (assembly.loadState === 'idle') await assembly.load()
}

async function toggleBasket(questionId: number): Promise<void> {
  await ensureAssembly()
  if (isInBasket(questionId)) {
    await assembly.removeQuestion(questionId)
  } else {
    await assembly.addQuestions([questionId])
  }
}

async function addSelectionToAssembly(): Promise<void> {
  if (store.selectedCount === 0 || assembly.saveState === 'saving') return
  await ensureAssembly()
  await assembly.addQuestions(store.selectedQuestionIds)
}

async function openSimilar(question: QuestionBankListItem): Promise<void> {
  similarSource.value = question
  similarItems.value = []
  similarState.value = 'loading'
  try {
    similarItems.value = (await questionBankApi.listSimilar(question.id)).items
    similarState.value = 'ready'
  } catch {
    similarState.value = 'error'
  }
}

function closeSimilar(): void {
  similarSource.value = null
  similarItems.value = []
  similarState.value = 'idle'
}

// 标签类维度显示维度名 + 叶子名；信号类维度 values 已是完整短语。
const SIMILAR_REASON_KIND_LABELS: Partial<Record<SimilarityReasonKind, string>> = {
  knowledge_point: '同知识点',
  skill: '同技能',
  method: '同解法',
  model: '同模型',
}

function similarReasonKindLabel(kind: SimilarityReasonKind): string {
  return SIMILAR_REASON_KIND_LABELS[kind] ?? ''
}

function similarReasonText(reason: SimilarityReason): string {
  const values =
    reason.kind === 'knowledge_point' || reason.kind === 'skill'
      ? reason.values.map(
          (value) => knowledgeLeafLabel(value).replace(/^技能[·：:]\s*/, ''),
        )
      : reason.values
  return values.join('、')
}

function similarReasonTitle(reason: SimilarityReason): string | undefined {
  return reason.kind === 'knowledge_point' || reason.kind === 'skill'
    ? reason.values.join('、')
    : undefined
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
    <StatePanel v-if="store.listState === 'loading' && store.questions.length === 0" kind="loading" title="正在读取试题…" description="" />
    <StatePanel v-else-if="store.listState === 'error'" kind="error" :title="store.listError" description="" retry-label="重新读取" @retry="store.loadQuestions(store.appliedFilters)" />
    <StatePanel v-else-if="store.listState === 'empty'" kind="empty" title="当前条件下没有试题，可以清除筛选后再查看。" description="" />
    <div v-else class="qb-question-list">
      <QuestionCard v-for="question in store.questions" :key="question.id" :question="question" :paper-mode="paperMode" :current-skill="currentSkill" @similar="openSimilar" @skill="emit('skill', $event)" />
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

  <DialogRoot :open="Boolean(similarSource)" @update:open="!$event && closeSimilar()"><DialogPortal>
    <DialogOverlay class="qb-drawer-layer" />
      <DialogContent v-if="similarSource" as="aside" class="qb-similar-drawer qb-similar-dialog" :aria-describedby="undefined">
        <header>
          <div>
            <DialogTitle as="h2">相似题推荐</DialogTitle>
            <p>基于本机题库文本和标签匹配，不会调用大模型或产生费用。</p>
          </div><AppIconButton label="关闭相似题" @click="closeSimilar" icon="close" />
        </header>

        <StatePanel v-if="similarState === 'loading'" kind="loading" title="正在查找相似题…" description="" />
        <StatePanel v-else-if="similarState === 'error'" kind="error" title="相似题暂时无法读取。" description="" retry-label="重新查找" @retry="openSimilar(similarSource)" />
        <StatePanel v-else-if="similarItems.length === 0" kind="empty" title="当前题库中没有找到足够相似的题目。" description="" />
        <div v-else class="qb-similar-list">
          <article v-for="item in similarItems" :key="item.id" class="qb-similar-card">
            <div class="qb-similar-card__score">
              <strong title="用于排列当前相似题，不代表题目相同的概率">推荐分 {{ Math.round(item.similarity_score * 100) }}</strong>
              <ul v-if="item.similarity_reasons.length" class="qb-similar-reasons">
                <li
                  v-for="(reason, index) in item.similarity_reasons"
                  :key="`${reason.kind}:${index}`"
                  class="qb-similar-reason"
                  :class="`is-${reason.kind}`"
                  :title="similarReasonTitle(reason)"
                >
                  <span
                    v-if="similarReasonKindLabel(reason.kind)"
                    class="qb-similar-reason__kind"
                  >{{ similarReasonKindLabel(reason.kind) }}</span>
                  <span class="qb-similar-reason__values">{{ similarReasonText(reason) }}</span>
                </li>
              </ul>
              <span v-else class="qb-similar-reasons__empty">内容相近</span>
            </div>
            <QuestionContentRenderer
              :blocks="item.rich_content?.question_blocks"
              :fallback="item.question_text"
              image-alt="相似题配图"
              media-mode="list"
              compact
            />
            <footer>
              <span>{{ item.paper_title || '未命名试卷' }}</span>
              <AppButton variant="secondary"
                type="button"
                :class="{ 'is-selected': isInBasket(item.id) }"
                :disabled="assembly.saveState === 'saving'"
                @click="toggleBasket(item.id)"
              >
                {{ isInBasket(item.id) ? '移出试卷篮' : '加入试卷篮' }}
              </AppButton>
            </footer>
          </article>
        </div>
      </DialogContent>
  </DialogPortal></DialogRoot>
</template>
