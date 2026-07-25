<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  questionBankApi,
  type QuestionBankListItem,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useQuestionBankStore } from '../../stores/question-bank'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

const store = useQuestionBankStore()
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

function tagsFor(question: QuestionBankListItem, tagType: string): string[] {
  return question.tags
    .filter((tag) => tag.tag_type === tagType)
    .map((tag) => tag.tag_value)
}

function isInBasket(questionId: number): boolean {
  return assembly.draft.basket_ids.includes(questionId)
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
</script>

<template>
  <section class="qb-ledger" aria-labelledby="qb-ledger-title">
    <header class="qb-section-heading">
      <div>
        <p class="qb-eyebrow">QUESTION LIST</p>
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
        <button
          type="button"
          class="qb-button is-primary"
          :disabled="assembly.saveState === 'saving'"
          @click="addSelectionToAssembly"
        >
          加入试卷篮
        </button>
        <button type="button" class="qb-link" @click="store.clearSelection">清空选择</button>
      </span>
    </div>

    <p v-if="store.listState === 'stale-error'" class="qb-feedback is-warning" role="alert">
      新数据暂时无法读取，当前仍显示上一次成功结果。
    </p>
    <div v-if="store.listState === 'loading' && store.questions.length === 0" class="qb-empty" role="status">
      正在读取试题…
    </div>
    <div v-else-if="store.listState === 'error'" class="qb-empty" role="alert">
      <span>{{ store.listError }}</span>
      <button type="button" class="qb-link" @click="store.loadQuestions(store.appliedFilters)">重新读取</button>
    </div>
    <div v-else-if="store.listState === 'empty'" class="qb-empty">
      当前条件下没有试题，可以清除筛选后再查看。
    </div>
    <div v-else class="qb-question-list">
      <article
        v-for="question in store.questions"
        :key="question.id"
        class="qb-question-card"
        :class="{
          'is-current': store.selectedQuestionId === question.id,
          'is-in-basket': isInBasket(question.id),
        }"
      >
        <header class="qb-question-card__header">
          <label class="qb-question-card__check">
            <input
              type="checkbox"
              :aria-label="`选择第 ${question.question_number || question.id} 题`"
              :checked="store.selectedQuestionIds.includes(question.id)"
              :disabled="store.selectionIsFull && !store.selectedQuestionIds.includes(question.id)"
              @change="store.toggleQuestionSelection(
                question.id,
                ($event.currentTarget as HTMLInputElement).checked,
              )"
            >
            <span>第 {{ question.question_number || question.id }} 题</span>
          </label>
          <div class="qb-question-card__source">
            <strong>{{ question.paper_title || '未命名试卷' }}</strong>
            <span>{{ [question.year, question.grade, question.exam_type].filter(Boolean).join(' · ') || '来源信息待补充' }}</span>
          </div>
        </header>

        <div class="qb-question-card__content">
          <QuestionContentRenderer
            :blocks="question.rich_content?.question_blocks"
            :fallback="question.question_text"
            image-alt="题目配图"
          />
        </div>

        <div class="qb-question-card__tags">
          <span>{{ question.question_type || '未分类' }}</span>
          <span>难度 {{ question.difficulty || '待定' }}</span>
          <span v-for="tag in tagsFor(question, 'knowledge_point').slice(0, 3)" :key="`knowledge:${tag}`">
            {{ tag }}
          </span>
          <span v-for="tag in tagsFor(question, 'exam_scope').slice(0, 2)" :key="`scope:${tag}`">
            {{ tag }}
          </span>
        </div>

        <footer class="qb-question-card__actions">
          <span class="qb-question-card__updated">
            {{ question.tags.length ? `${question.tags.length} 个标签` : '待标注' }}
            <template v-if="question.has_images"> · 含图片</template>
          </span>
          <span>
            <button type="button" class="qb-link" @click="store.selectQuestion(question.id)">查看详情与标注</button>
            <button type="button" class="qb-link" @click="openSimilar(question)">相似题</button>
            <button
              type="button"
              class="qb-button"
              :class="{ 'is-selected': isInBasket(question.id) }"
              :disabled="assembly.saveState === 'saving'"
              @click="toggleBasket(question.id)"
            >
              {{ isInBasket(question.id) ? '移出试卷篮' : '加入试卷篮' }}
            </button>
          </span>
        </footer>
      </article>
    </div>

    <footer v-if="store.totalPages > 1" class="qb-pagination">
      <button type="button" class="qb-button is-quiet" :disabled="store.page <= 1" @click="changePage(store.page - 1)">
        上一页
      </button>
      <span>第 {{ store.page }} / {{ store.totalPages }} 页</span>
      <button type="button" class="qb-button is-quiet" :disabled="store.page >= store.totalPages" @click="changePage(store.page + 1)">
        下一页
      </button>
    </footer>
  </section>

  <Teleport to="body">
    <div v-if="similarSource" class="qb-drawer-layer" role="presentation" @click.self="closeSimilar">
      <aside class="qb-similar-drawer" role="dialog" aria-modal="true" aria-labelledby="similar-title">
        <header>
          <div>
            <p class="qb-eyebrow">LOCAL SIMILARITY</p>
            <h2 id="similar-title">相似题推荐</h2>
            <p>基于本机题库文本和标签匹配，不会调用大模型或产生费用。</p>
          </div>
          <button type="button" class="qb-drawer-close" aria-label="关闭相似题" @click="closeSimilar">×</button>
        </header>

        <div v-if="similarState === 'loading'" class="qb-empty" role="status">正在查找相似题…</div>
        <div v-else-if="similarState === 'error'" class="qb-empty" role="alert">
          <span>相似题暂时无法读取。</span>
          <button type="button" class="qb-link" @click="openSimilar(similarSource)">重新查找</button>
        </div>
        <div v-else-if="similarItems.length === 0" class="qb-empty">当前题库中没有找到足够相似的题目。</div>
        <div v-else class="qb-similar-list">
          <article v-for="item in similarItems" :key="item.id" class="qb-similar-card">
            <div class="qb-similar-card__score">
              <strong>{{ Math.round(item.similarity_score * 100) }}%</strong>
              <span>{{ item.similarity_reasons.join(' · ') || '内容相近' }}</span>
            </div>
            <QuestionContentRenderer
              :blocks="item.rich_content?.question_blocks"
              :fallback="item.question_text"
              image-alt="相似题配图"
              compact
            />
            <footer>
              <span>{{ item.paper_title || '未命名试卷' }}</span>
              <button
                type="button"
                class="qb-button"
                :class="{ 'is-selected': isInBasket(item.id) }"
                :disabled="assembly.saveState === 'saving'"
                @click="toggleBasket(item.id)"
              >
                {{ isInBasket(item.id) ? '移出试卷篮' : '加入试卷篮' }}
              </button>
            </footer>
          </article>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
