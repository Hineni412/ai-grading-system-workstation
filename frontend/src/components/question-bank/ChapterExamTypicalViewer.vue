<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import {
  questionBankApi,
  type ChapterExamCellColumn,
  type ChapterExamTypicalPick,
  type QuestionBankDetail,
  type QuestionBankListItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import AppDialog from '../design-system/AppDialog.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import { examTargetKind, examTargetName, type ChapterExamTypicalCard } from './chapterExamTypical'

const props = defineProps<{
  cards: ChapterExamTypicalCard[]
  openKey: string | null
  items: ReadonlyMap<number, QuestionBankListItem>
}>()

const emit = defineEmits<{ close: [] }>()

const TIER_ORDER: ChapterExamCellColumn[] = ['basic', 'mid', 'hard']
const TIER_LABELS: Record<ChapterExamCellColumn, string> = { basic: '基础', mid: '中档', hard: '提高' }

const assembly = useAssemblyStore()
const index = ref(0)
const pickIndex = ref(0)
const showAnswer = ref(false)
const detail = ref<QuestionBankDetail | null>(null)
const detailState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const detailCache = new Map<number, QuestionBankDetail>()
let requestToken = 0

const card = computed(() => props.cards[index.value] ?? null)
const openCard = computed(() => props.cards.find(row => row.cardKey === props.openKey) ?? null)
const pick = computed<ChapterExamTypicalPick | null>(
  () => card.value?.row.typical[Math.min(pickIndex.value, (card.value?.row.typical.length ?? 1) - 1)] ?? null,
)
const summaryItem = computed(() => (pick.value ? props.items.get(pick.value.question_id) ?? null : null))
const question = computed(() => (detail.value?.id === pick.value?.question_id ? detail.value : summaryItem.value))
const inBasket = computed(() => (pick.value ? assembly.draft.basket_ids.includes(pick.value.question_id) : false))

watch(() => props.openKey, key => {
  if (!key) return
  const found = props.cards.findIndex(row => row.cardKey === key)
  if (found >= 0) index.value = found
}, { immediate: true })

watch(() => pick.value?.question_id, () => { void loadDetail() })

async function loadDetail(): Promise<void> {
  const id = pick.value?.question_id
  if (!id) {
    detail.value = null
    detailState.value = 'idle'
    return
  }
  const cached = detailCache.get(id)
  if (cached) {
    detail.value = cached
    detailState.value = 'ready'
    prefetchNext()
    return
  }
  const token = ++requestToken
  detailState.value = 'loading'
  try {
    const value = await questionBankApi.getQuestion(id)
    detailCache.set(id, value)
    if (token !== requestToken) return
    detail.value = value
    detailState.value = 'ready'
    prefetchNext()
  } catch {
    if (token === requestToken) detailState.value = 'error'
  }
}

function prefetchNext(): void {
  const next = props.cards[index.value + 1]?.row.typical[0]?.question_id
  if (!next || detailCache.has(next)) return
  void questionBankApi.getQuestion(next).then(value => detailCache.set(next, value)).catch(() => {})
}

function step(delta: number): void {
  const next = index.value + delta
  if (next < 0 || next >= props.cards.length) return
  index.value = next
  pickIndex.value = 0
  showAnswer.value = false
}

function selectTier(tier: ChapterExamCellColumn): void {
  const found = card.value?.row.typical.findIndex(row => row.tier === tier) ?? -1
  if (found >= 0) pickIndex.value = found
}

async function toggleBasket(): Promise<void> {
  if (!pick.value) return
  if (assembly.loadState === 'idle') await assembly.load()
  if (inBasket.value) await assembly.removeQuestion(pick.value.question_id)
  else await assembly.addQuestions([pick.value.question_id])
}

function retry(): void {
  const id = pick.value?.question_id
  if (id) detailCache.delete(id)
  void loadDetail()
}

/* 配图：题图放大到可读宽度（自然宽 ×2，至少 420px），点击在对话框内整宽/还原切换。 */
const bodyEl = ref<HTMLElement | null>(null)

function sizeFigure(img: HTMLImageElement): void {
  if (img.closest('.question-content__media--options')) return
  img.style.width = `min(100%, ${Math.max(img.naturalWidth * 2, 420)}px)`
}

function sizeFigures(): void {
  bodyEl.value?.querySelectorAll<HTMLImageElement>('img')
    .forEach(img => { if (img.complete) sizeFigure(img) })
}

function onFigureLoad(event: Event): void {
  if (event.target instanceof HTMLImageElement) sizeFigure(event.target)
}

function onFigureClick(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  const img = target instanceof HTMLImageElement
    ? target
    : target?.closest('.question-content__image-button')?.querySelector('img') ?? null
  if (!img || !bodyEl.value?.contains(img)) return
  event.preventDefault()
  event.stopPropagation()
  img.classList.toggle('is-zoom')
}

function toggleFirstFigure(): void {
  bodyEl.value?.querySelector<HTMLImageElement>('.question-content__media img, .question-html img')
    ?.classList.toggle('is-zoom')
}

watch([() => question.value?.id, showAnswer], () => void nextTick(sizeFigures))

// 快捷键在对话框内捕获；输入控件与嵌套浮层（相似题等）不触发。
function onKeydown(event: KeyboardEvent): void {
  if (event.defaultPrevented) return
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, select, [contenteditable="true"]')) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    step(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    step(1)
  } else if (['1', '2', '3'].includes(event.key)) {
    event.preventDefault()
    selectTier(TIER_ORDER[Number(event.key) - 1]!)
  } else if (event.key === 'a' || event.key === 'A') {
    event.preventDefault()
    showAnswer.value = !showAnswer.value
  } else if (event.key === 'f' || event.key === 'F') {
    event.preventDefault()
    toggleFirstFigure()
  } else if (event.key === 'b' || event.key === 'B') {
    event.preventDefault()
    void toggleBasket()
  }
}

function onOpenChange(open: boolean): void {
  if (!open) emit('close')
}
</script>

<template>
  <AppDialog
    :open="Boolean(openCard)"
    :title="card ? examTargetName(card.row) : '典型题'"
    width="wide"
    class="cep-tv-dialog"
    @update:open="onOpenChange"
    @keydown="onKeydown"
  >
    <template v-if="card" #header-extra>
      <div class="cep-tv-head">
        <small class="cep-tv-meta">{{ card.sectionLabel }} · 本节第{{ card.rank }}常考 · 共 {{ card.row.total }} 题 · 出现在 {{ card.row.group_count }} 套卷 · 第 {{ index + 1 }} / {{ cards.length }} 个{{ examTargetKind(card.row) }}</small>
        <div v-if="pick" class="cep-tv-tiers" role="tablist" :aria-label="`${examTargetName(card.row)}难度档`">
          <button
            v-for="(row, rowIndex) in card.row.typical"
            :key="row.question_id"
            type="button"
            role="tab"
            class="cep-tier-tab"
            :class="[`is-${row.tier}`, { 'is-on': pick === row }]"
            :aria-selected="pick === row"
            @click="pickIndex = rowIndex"
          >{{ TIER_LABELS[row.tier] }}</button>
        </div>
      </div>
    </template>
    <div v-if="card && pick" class="cep-tv">
      <div
        ref="bodyEl"
        class="cep-tv-body"
        @load.capture="onFigureLoad"
        @click.capture="onFigureClick"
      >
        <StatePanel v-if="detailState === 'loading' && !question" kind="loading" title="正在读取题目…" />
        <StatePanel v-else-if="detailState === 'error' && !question" kind="error" title="题目暂时无法读取。" retry-label="重试" @retry="retry" />
        <template v-else>
          <QuestionContentRenderer
            :blocks="detail?.id === pick.question_id ? detail.rich_content.question_blocks : summaryItem?.rich_content?.question_blocks"
            :fallback="question?.question_text ?? ''"
            image-alt="题目配图"
            media-mode="detail"
          />
          <section class="cep-tv-answer">
            <button
              type="button"
              class="cep-tv-answer-toggle"
              :aria-expanded="showAnswer"
              @click="showAnswer = !showAnswer"
            >答案与解析 {{ showAnswer ? '▲' : '▼' }}</button>
            <div v-if="showAnswer" class="cep-tv-answer-body">
              <QuestionContentRenderer
                :blocks="detail?.id === pick.question_id ? detail.rich_content.answer_blocks : undefined"
                :fallback="detail?.id === pick.question_id ? (detail.answer_text ?? '答案暂未录入') : '答案暂未录入'"
                image-alt="答案配图"
                media-mode="detail"
              />
            </div>
          </section>
        </template>
      </div>
    </div>
    <template v-if="card" #footer>
      <span class="cep-tv-source">{{ question?.paper_title || '未命名试卷' }} · 第 {{ question?.question_number || question?.id || '—' }} 题</span>
      <AppButton type="button" variant="secondary" :disabled="assembly.saveState === 'saving'" @click="toggleBasket">{{ inBasket ? '移出试卷篮' : '加入试卷篮' }}</AppButton>
      <AppButton type="button" variant="ghost" :disabled="index <= 0" @click="step(-1)">‹ 上一{{ examTargetKind(card.row) }}</AppButton>
      <AppButton type="button" variant="ghost" :disabled="index >= cards.length - 1" @click="step(1)">下一{{ examTargetKind(card.row) }} ›</AppButton>
      <small class="cep-tv-hint">← → 切换{{ examTargetKind(card.row) }} · 1 2 3 难度档 · A 答案 · B 试卷篮 · F 放大图 · Esc 关闭</small>
    </template>
  </AppDialog>
</template>
