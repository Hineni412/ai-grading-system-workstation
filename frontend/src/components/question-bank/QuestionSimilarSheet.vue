<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  knowledgeLeafLabel,
  questionBankApi,
  type QuestionBankListItem,
  type SimilarityReason,
  type SimilarityReasonKind,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

const props = defineProps<{
  source: QuestionBankListItem | null
}>()

const emit = defineEmits<{
  close: []
}>()

const assembly = useAssemblyStore()
const items = ref<SimilarQuestionItem[]>([])
const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let requestToken = 0

async function load(question: QuestionBankListItem): Promise<void> {
  const token = ++requestToken
  items.value = []
  state.value = 'loading'
  try {
    const result = await questionBankApi.listSimilar(question.id)
    if (token !== requestToken) return
    items.value = result.items
    state.value = 'ready'
  } catch {
    if (token === requestToken) state.value = 'error'
  }
}

watch(() => props.source, question => {
  if (question) {
    void load(question)
  } else {
    requestToken += 1
    items.value = []
    state.value = 'idle'
  }
}, { immediate: true })

function retry(): void {
  if (props.source) void load(props.source)
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

// 标签类维度显示维度名 + 叶子名；信号类维度 values 已是完整短语。
const SIMILAR_REASON_KIND_LABELS: Partial<Record<SimilarityReasonKind, string>> = {
  type: '同题型',
  knowledge_point: '同知识点',
  skill: '同关键步骤',
  method: '同解法',
  model: '同模型',
}

function similarReasonKindLabel(kind: SimilarityReasonKind): string {
  return SIMILAR_REASON_KIND_LABELS[kind] ?? ''
}

function similarReasonText(reason: SimilarityReason): string {
  const values =
    reason.kind === 'type' || reason.kind === 'knowledge_point' || reason.kind === 'skill'
      ? reason.values.map(
          (value) => knowledgeLeafLabel(value).replace(/^技能[·：:]\s*/, ''),
        )
      : reason.values
  return values.join('、')
}

function similarReasonTitle(reason: SimilarityReason): string | undefined {
  return reason.kind === 'type' || reason.kind === 'knowledge_point' || reason.kind === 'skill'
    ? reason.values.join('、')
    : undefined
}
</script>

<template>
  <Sheet :open="Boolean(source)" @update:open="(value: boolean) => { if (!value) emit('close') }">
    <SheetContent class="qb-similar-drawer gap-0" :aria-describedby="undefined">
      <template v-if="source">
        <SheetHeader class="qb-similar-head">
          <SheetTitle as="h2">相似题推荐</SheetTitle>
        </SheetHeader>

        <StatePanel v-if="state === 'loading'" kind="loading" title="正在查找相似题…" />
        <StatePanel v-else-if="state === 'error'" kind="error" title="相似题暂时无法读取。" retry-label="重新查找" @retry="retry" />
        <StatePanel v-else-if="items.length === 0" kind="empty" title="当前题库中没有找到足够相似的题目。" />
        <div v-else class="qb-similar-list">
          <article v-for="item in items" :key="item.id" class="qb-similar-card">
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
      </template>
    </SheetContent>
  </Sheet>
</template>
