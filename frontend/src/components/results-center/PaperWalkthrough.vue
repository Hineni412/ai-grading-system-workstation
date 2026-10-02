<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  fetchReviewItems,
  fetchReviewQuestions,
  fetchReviewRubric,
  resolveReviewItem,
  type ResolvedReviewItem,
  type ReviewRubricSection,
} from '../../api/review'
import type { ClassAnalysisQuestion } from '../../api/class-analysis'
import type { ResultsCenterStudent } from '../../api/results-center'
import AppButton from '../design-system/AppButton.vue'
import PaperWalkthroughCard from './PaperWalkthroughCard.vue'
import {
  WALKTHROUGH_CATEGORY_LABELS,
  buildWalkthroughDeck,
  type WalkthroughCard,
  type WalkthroughCategory,
} from './paper-walkthrough'

/**
 * 「看卷 10 分钟」：AI 批改后按本地规则抽 11–15 张答卷卡，
 * 分数先藏后翻，帮老师重新形成对每个学生的印象。
 * 只发 GET 请求；不改分数、名次、报告、复核状态或训练证据。
 */

const props = defineProps<{
  sessionId: number
  scopeKey: string | null
  scopeLabel: string
  students: readonly ResultsCenterStudent[]
  questionIds: readonly string[]
  analysisQuestions: ClassAnalysisQuestion[] | null
  previousStudents: readonly ResultsCenterStudent[] | null
  /** 待复核条目数；>0 时提示分数可能还会变 */
  pendingCount: number
  initialIndex?: number
}>()

const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const RESUME_KEY = 'ai-grading:paper-walkthrough:v1'

const overlay = ref<HTMLElement | null>(null)
const cardRef = ref<InstanceType<typeof PaperWalkthroughCard> | null>(null)
const ready = ref(false)
const loadError = ref<string | null>(null)
const deck = ref<WalkthroughCard[]>([])
const idx = ref(0)
const revealed = ref(false)
const elapsed = ref(0)
const zoomUrls = ref<string[] | null>(null)
const viewed = new Map<number, Set<number>>()
const cardOf = computed(() => deck.value[idx.value] ?? null)
const ended = computed(() => deck.value.length > 0 && idx.value >= deck.value.length)

const itemCache = new Map<string, Promise<ResolvedReviewItem[]>>()
const rubricCache = new Map<string, Promise<ReviewRubricSection | null>>()
const studentById = new Map(props.students.map((s) => [s.student_id, s] as const))

function ensureItems(qid: string): Promise<ResolvedReviewItem[]> {
  const cached = itemCache.get(qid)
  if (cached) return cached
  const p = fetchReviewItems(props.sessionId, qid, { scope: 'all' })
    .then((items) => items.map(resolveReviewItem))
  p.catch(() => { if (itemCache.get(qid) === p) itemCache.delete(qid) })
  itemCache.set(qid, p)
  return p
}
async function loadItem(studentId: number, qid: string): Promise<ResolvedReviewItem | null> {
  try {
    const items = await ensureItems(qid)
    const rcItem = studentById.get(studentId)?.items.find((i) => i.question_id === qid)
    return items.find((i) => i.review_item_id === rcItem?.review_item_id)
      ?? items.find((i) => i.student_id === studentId)
      ?? null
  } catch { return null }
}
function loadRubric(qid: string): Promise<ReviewRubricSection | null> {
  let p = rubricCache.get(qid)
  if (!p) { p = fetchReviewRubric(props.sessionId, qid).catch(() => null); rubricCache.set(qid, p) }
  return p
}

async function mapLimit<T>(list: T[], limit: number, fn: (t: T) => Promise<void>): Promise<void> {
  let next = 0
  await Promise.all(Array.from({ length: Math.min(limit, list.length) }, async () => {
    while (next < list.length) { const i = next; next += 1; await fn(list[i]!) }
  }))
}

async function prepare(): Promise<void> {
  try {
    const questions = await fetchReviewQuestions(props.sessionId, { scope: 'all' })
    const qidSet = new Set(props.questionIds)
    const solutionQids = questions
      .filter((q) => qidSet.has(q.question_id)
        && q.question_type !== 'choice' && q.question_type !== 'fill_blank')
      .map((q) => q.question_id)
    const alternatives = new Set<string>()
    await mapLimit(solutionQids, 3, async (qid) => {
      const items = await ensureItems(qid)
      for (const it of items) {
        if ((it.metadata as Record<string, unknown>).alternative_solution_detected === true) {
          alternatives.add(`${it.student_id}|${qid}`)
        }
      }
    })
    deck.value = buildWalkthroughDeck({
      scopeKey: props.scopeKey,
      students: props.students,
      questionIds: props.questionIds,
      analysisQuestions: props.analysisQuestions,
      previousStudents: props.previousStudents,
      alternatives,
      seed: `${props.sessionId}|${props.scopeKey ?? 'all'}`,
    })
    idx.value = Math.min(Math.max(props.initialIndex ?? 0, 0), Math.max(deck.value.length - 1, 0))
    ready.value = true
  } catch {
    loadError.value = '答卷数据加载失败，请稍后重试'
  }
}

let triggerEl: HTMLElement | null = null
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  triggerEl = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const start = Date.now()
  timer = setInterval(() => { elapsed.value = Math.floor((Date.now() - start) / 1000) }, 1000)
  // 模态层：按键全局监听，否则焦点不在层内时 Esc 不生效
  window.addEventListener('keydown', onKeydown)
  void prepare()
  void nextTickFocus()
})
async function nextTickFocus(): Promise<void> {
  await Promise.resolve(); overlay.value?.focus()
}
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKeydown)
  triggerEl?.focus()
})

watch(idx, () => {
  revealed.value = false
  const next = deck.value[idx.value + 1]
  if (next) void ensureItems(next.questionId ?? props.questionIds[0]!)
})
watch(ended, (on) => { if (on && timer) { clearInterval(timer); timer = null } })

const elapsedText = computed(() => {
  const m = Math.floor(elapsed.value / 60); const s = elapsed.value % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})

const catChips = computed(() => {
  const order: WalkthroughCategory[] = [1, 2, 3, 4, 5]
  return order.map((cat) => {
    const first = deck.value.findIndex((c) => c.category === cat)
    const count = deck.value.filter((c) => c.category === cat).length
    return { cat, first, count }
  }).filter((c) => c.count > 0)
})

const endRows = computed(() => catChips.value.map(({ cat }) => ({
  cat,
  rows: deck.value
    .map((card, i) => ({ card, i }))
    .filter(({ card }) => card.category === cat),
})))

function studentLabel(sid: number): string {
  const s = studentById.get(sid)
  return s ? s.student_name : '—'
}
function cardScoreText(card: WalkthroughCard): string {
  const s = studentById.get(card.studentId)
  if (!s) return ''
  if (card.questionId === null) return `${s.current_score}/${s.max_score}`
  const item = s.items.find((i) => i.question_id === card.questionId)
  return item && item.score_awarded !== null ? `${item.score_awarded}/${item.max_score}` : ''
}

function onViewed(sid: number): void {
  const set = viewed.get(idx.value) ?? new Set<number>()
  set.add(sid); viewed.set(idx.value, set)
}
function onGoReview(sid: number, qid: string, reviewItemId: string): void {
  try {
    sessionStorage.setItem(RESUME_KEY, JSON.stringify({
      sessionId: props.sessionId, scope: props.scopeKey, index: idx.value,
    }))
  } catch { /* 私密模式等场景下可跳过恢复 */ }
  void router.push({
    path: '/grading',
    query: {
      session: String(props.sessionId), scope: 'all', question: qid,
      item: reviewItemId, student: String(sid), entry: 'results',
    },
  })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    if (zoomUrls.value) { zoomUrls.value = null; return }
    emit('close')
    return
  }
  if (zoomUrls.value || !ready.value || !deck.value.length) return
  switch (event.key) {
    case ' ':
      event.preventDefault(); if (!ended.value) revealed.value = !revealed.value; break
    case 'ArrowRight':
      event.preventDefault(); idx.value = Math.min(idx.value + 1, deck.value.length); break
    case 'ArrowLeft':
      event.preventDefault(); idx.value = Math.max(idx.value - 1, 0); break
    case 'ArrowUp': case 'ArrowDown':
      if (cardOf.value?.category === 1) {
        event.preventDefault()
        cardRef.value?.shiftMember(event.key === 'ArrowUp' ? -1 : 1)
      }
      break
    default:
      if (/^[1-9]$/.test(event.key) && cardOf.value?.category === 1) {
        cardRef.value?.selectGroup(Number(event.key) - 1)
      }
  }
}

function onTrapTab(event: KeyboardEvent): void {
  if (event.key !== 'Tab' || !overlay.value) return
  const focusable = [...overlay.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), [href], input:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )]
  if (!focusable.length) { event.preventDefault(); return }
  const first = focusable[0]!; const last = focusable[focusable.length - 1]!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
</script>

<template>
  <Teleport to="body">
    <div
      ref="overlay"
      class="wt"
      role="dialog"
      aria-modal="true"
      aria-label="看卷 10 分钟"
      tabindex="-1"
      @keydown.tab="onTrapTab"
    >
      <header class="wt__bar">
        <strong class="wt__title">看卷 10 分钟 · {{ scopeLabel }}</strong>
        <span v-if="ready" class="wt__progress">{{ Math.min(idx + 1, deck.length) }} / {{ deck.length }}</span>
        <nav v-if="ready" class="wt__cats" aria-label="按类别跳转">
          <button
            v-for="chip in catChips" :key="chip.cat" type="button" class="wt__cat"
            @click="idx = chip.first"
          >{{ WALKTHROUGH_CATEGORY_LABELS[chip.cat] }} {{ chip.count }}</button>
        </nav>
        <span class="wt__time">{{ elapsedText }}</span>
        <AppButton variant="ghost" @click="emit('close')">退出</AppButton>
      </header>
      <p v-if="pendingCount > 0" class="wt__warn">
        还有 {{ pendingCount }} 题待复核，看到的分数可能还会变
      </p>

      <main class="wt__main">
        <p v-if="!ready && !loadError" class="wt__loading">正在挑选答卷…</p>
        <p v-else-if="loadError" class="wt__loading">{{ loadError }}</p>
        <p v-else-if="!deck.length" class="wt__loading">当前范围暂无可看的学生答卷</p>

        <div v-else-if="ended" class="wt__end">
          <h2 class="wt__end-title">看完了 {{ deck.length }} 张，用时 {{ elapsedText }}</h2>
          <section v-for="group in endRows" :key="group.cat" class="wt__end-cat">
            <h3>{{ WALKTHROUGH_CATEGORY_LABELS[group.cat] }}</h3>
            <button
              v-for="{ card, i } in group.rows" :key="card.id" type="button"
              class="wt__end-row" @click="idx = i"
            >
              <template v-if="card.category === 1">
                第 {{ card.questionId }} 题 · {{ card.groups?.length ?? 0 }} 种典型错法 ·
                看了 {{ viewed.get(i)?.size ?? 0 }} 人
              </template>
              <template v-else>
                {{ studentLabel(card.studentId) }} ·
                {{ card.questionId === null ? '整卷' : `第 ${card.questionId} 题` }} ·
                {{ cardScoreText(card) }}
              </template>
            </button>
          </section>
          <AppButton variant="primary" @click="emit('close')">返回考情总览</AppButton>
        </div>

        <PaperWalkthroughCard
          v-else-if="cardOf"
          ref="cardRef"
          :key="cardOf.id"
          :card="cardOf"
          :students="students"
          :revealed="revealed"
          :media-question-id="questionIds[0] ?? ''"
          :load-item="loadItem"
          :load-rubric="loadRubric"
          @viewed="onViewed"
          @go-review="onGoReview"
          @zoom="(urls) => { zoomUrls = urls }"
        />
      </main>

      <footer class="wt__foot">
        <template v-if="cardOf?.category === 1">↑↓ 换同学 · 1–9 换错法 · </template>
        ← → 换卡 · 空格 翻开分数 · Esc 退出
      </footer>

      <div v-if="zoomUrls" class="wt__zoom" @click="zoomUrls = null">
        <img v-for="url in zoomUrls" :key="url" :src="url" alt="答卷放大图">
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.wt {
  position: fixed; inset: 0; z-index: 60; display: flex; flex-direction: column;
  background: var(--color-bg-app); color: var(--color-text-primary); outline: none;
}
.wt__bar {
  display: flex; align-items: center; gap: 12px; padding: 10px 18px;
  border-bottom: 1px solid var(--color-border-subtle); background: var(--card);
}
.wt__progress { font-variant-numeric: tabular-nums; color: var(--color-text-secondary); }
.wt__cats { display: flex; gap: 6px; flex: 1; justify-content: center; flex-wrap: wrap; }
.wt__cat {
  border: 1px solid var(--color-border-default); background: transparent;
  border-radius: 999px; padding: 2px 10px; font-size: 12px; cursor: pointer;
  color: var(--color-text-secondary);
}
.wt__cat:hover { border-color: var(--color-accent); color: var(--color-accent-hover); }
.wt__time { font-variant-numeric: tabular-nums; color: var(--color-text-muted); }
.wt__warn {
  margin: 0; padding: 6px 18px; font-size: 13px;
  color: var(--color-warning); background: var(--color-warning-subtle);
}
.wt__main { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 10px 18px; }
.wt__loading { margin: auto; color: var(--color-text-muted); }
.wt__end { margin: auto; max-width: 560px; width: 100%; overflow: auto; }
.wt__end-title { font-size: 18px; }
.wt__end-cat h3 { font-size: 13px; color: var(--color-text-muted); margin: 10px 0 4px; }
.wt__end-row {
  display: block; width: 100%; text-align: left; padding: 6px 10px; margin: 2px 0;
  border: 1px solid var(--color-border-subtle); border-radius: 8px;
  background: var(--card); cursor: pointer; font-size: 13px;
}
.wt__end-row:hover { border-color: var(--color-accent); }
.wt__foot {
  padding: 8px 18px; border-top: 1px solid var(--color-border-subtle);
  color: var(--color-text-muted); font-size: 12px; text-align: center; background: var(--card);
}
.wt__zoom {
  position: fixed; inset: 0; z-index: 70; background: rgba(20, 26, 30, 0.85);
  display: flex; gap: 16px; overflow: auto; padding: 24px; cursor: zoom-out;
}
.wt__zoom img { max-width: none; height: 92vh; margin: auto; background: #fff; }
</style>
