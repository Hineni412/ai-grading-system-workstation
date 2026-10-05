<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  type ResolvedReviewItem,
  type ReviewRubricSection,
} from '../../api/review'
import type { ClassAnalysisQuestion } from '../../api/class-analysis'
import type { ResultsCenterStudent } from '../../api/results-center'
import AppButton from '../design-system/AppButton.vue'
import BackButton from '../design-system/BackButton.vue'
import PageHeader from '../design-system/PageHeader.vue'
import PaperWalkthroughCard from './PaperWalkthroughCard.vue'
import {
  WALKTHROUGH_CATEGORY_LABELS,
  buildWalkthroughDeck,
  type WalkthroughCard,
  type WalkthroughCategory,
  type WalkthroughDataCache,
} from './paper-walkthrough'

/**
 * 「看卷 10 分钟」：AI 批改后按本地规则组成四类答卷卡，
 * 每张卡按错法/表现分组学生，分数先藏后翻，
 * 帮老师重新形成对每个学生的印象。
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
  dataCache: WalkthroughDataCache
}>()

const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const RESUME_KEY = 'ai-grading:paper-walkthrough:v1'

const overlay = ref<HTMLElement | null>(null)
const cardRef = ref<InstanceType<typeof PaperWalkthroughCard> | null>(null)
const ready = ref(false)
const loadError = ref<string | null>(null)
const deck = ref<WalkthroughCard[]>([])
const deckGeneration = ref(0)
const idx = ref(0)
const revealed = ref(false)
const zoomUrls = ref<string[] | null>(null)
const viewed = new Map<number, Set<number>>()
const cardOf = computed(() => deck.value[idx.value] ?? null)
const ended = computed(() => deck.value.length > 0 && idx.value >= deck.value.length)

const studentById = computed(() => new Map(props.students.map((s) => [s.student_id, s] as const)))

function ensureItems(qid: string): Promise<ResolvedReviewItem[]> {
  return props.dataCache.items(qid)
}
async function loadItem(studentId: number, qid: string): Promise<ResolvedReviewItem | null> {
  try {
    const items = await ensureItems(qid)
    const rcItem = studentById.value.get(studentId)?.items.find((i) => i.question_id === qid)
    return items.find((i) => i.review_item_id === rcItem?.review_item_id)
      ?? items.find((i) => i.student_id === studentId)
      ?? null
  } catch { return null }
}
function loadRubric(qid: string): Promise<ReviewRubricSection | null> {
  return props.dataCache.rubric(qid).catch(() => null)
}

function prepare(): void {
  try {
    const currentIndex = ready.value ? idx.value : props.initialIndex ?? 0
    deck.value = buildWalkthroughDeck({
      scopeKey: props.scopeKey,
      students: props.students,
      questionIds: props.questionIds,
      analysisQuestions: props.analysisQuestions,
      previousStudents: props.previousStudents,
    })
    idx.value = Math.min(Math.max(currentIndex, 0), Math.max(deck.value.length - 1, 0))
    deckGeneration.value += 1
    revealed.value = false
    viewed.clear()
    ready.value = true
  } catch {
    loadError.value = '答卷数据加载失败，请稍后重试'
  }
}
watch([
  () => props.dataCache, () => props.analysisQuestions,
  () => props.previousStudents, () => props.scopeKey,
], prepare)

let triggerEl: HTMLElement | null = null
onMounted(() => {
  triggerEl = document.activeElement instanceof HTMLElement ? document.activeElement : null
  // 模态层：按键全局监听，否则焦点不在层内时 Esc 不生效
  window.addEventListener('keydown', onKeydown)
  void prepare()
  void nextTickFocus()
})
async function nextTickFocus(): Promise<void> {
  await Promise.resolve(); overlay.value?.focus()
}
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  triggerEl?.focus()
})

watch(idx, () => {
  revealed.value = false
  const next = deck.value[idx.value + 1]
  const qid = next?.questionId ?? next?.groups[0]?.members[0]?.questionId ?? props.questionIds[0]
  if (next && qid) void ensureItems(qid).catch(() => {})
})

const catChips = computed(() => {
  const order: WalkthroughCategory[] = [1, 2, 3, 4]
  return order.map((cat) => {
    const first = deck.value.findIndex((c) => c.category === cat)
    const count = deck.value.filter((c) => c.category === cat).length
    return { cat, first, count }
  }).filter((c) => c.count > 0)
})
const currentChip = computed(() => (
  catChips.value.find((chip) => chip.cat === cardOf.value?.category) ?? null
))
const metaText = computed(() => {
  if (ended.value) return `${props.scopeLabel} · 已看完 ${deck.value.length} 张`
  const chip = currentChip.value
  if (!chip) return props.scopeLabel
  const catIndex = catChips.value.indexOf(chip) + 1
  const inCat = idx.value - chip.first + 1
  return `${props.scopeLabel} · 第 ${catIndex} 类 ${WALKTHROUGH_CATEGORY_LABELS[chip.cat]}` +
    ` · 第 ${inCat} / ${chip.count} 张 · 全部 ${idx.value + 1} / ${deck.value.length}`
})

const endRows = computed(() => catChips.value.map(({ cat }) => ({
  cat,
  rows: deck.value
    .map((card, i) => ({ card, i }))
    .filter(({ card }) => card.category === cat),
})))

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
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (event.target instanceof HTMLElement
    && event.target.closest('input, textarea, select, [contenteditable]')) return
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
    case 'ArrowRight': case 'd': case 'D':
      event.preventDefault(); idx.value = Math.min(idx.value + 1, deck.value.length); break
    case 'ArrowLeft': case 'a': case 'A':
      event.preventDefault(); idx.value = Math.max(idx.value - 1, 0); break
    case 'ArrowUp': case 'w': case 'W':
      event.preventDefault(); cardRef.value?.shiftMember(-1); break
    case 'ArrowDown': case 's': case 'S':
      event.preventDefault(); cardRef.value?.shiftMember(1); break
    default:
      if (/^[1-9]$/.test(event.key)) cardRef.value?.selectGroup(Number(event.key) - 1)
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
      <PageHeader title="看卷 10 分钟">
        <template #back>
          <BackButton label="考情总览" @click="emit('close')" />
        </template>
        <template #meta>{{ ready ? metaText : scopeLabel }}</template>
        <template #navigation>
          <nav class="page-tabs" aria-label="按类别跳转">
            <button
              v-for="chip in catChips" :key="chip.cat" type="button"
              :class="{ 'is-active': currentChip?.cat === chip.cat }"
              :aria-current="currentChip?.cat === chip.cat ? 'page' : undefined"
              @click="idx = chip.first"
            >{{ WALKTHROUGH_CATEGORY_LABELS[chip.cat] }} {{ chip.count }}</button>
          </nav>
        </template>
      </PageHeader>
      <p v-if="pendingCount > 0" class="wt__warn">
        还有 {{ pendingCount }} 题待复核，看到的分数可能还会变
      </p>

      <main class="wt__main">
        <p v-if="!ready && !loadError" class="wt__loading">正在挑选答卷…</p>
        <p v-else-if="loadError" class="wt__loading">{{ loadError }}</p>
        <p v-else-if="!deck.length" class="wt__loading">当前范围暂无可看的学生答卷</p>

        <div v-else-if="ended" class="wt__end">
          <h2 class="wt__end-title">看完了 {{ deck.length }} 张</h2>
          <section v-for="group in endRows" :key="group.cat" class="wt__end-cat">
            <h3>{{ WALKTHROUGH_CATEGORY_LABELS[group.cat] }}</h3>
            <button
              v-for="{ card, i } in group.rows" :key="card.id" type="button"
              class="wt__end-row" @click="idx = i"
            >{{ card.title }} · 看了 {{ viewed.get(i)?.size ?? 0 }} 人</button>
          </section>
          <AppButton variant="primary" @click="emit('close')">返回考情总览</AppButton>
        </div>

        <PaperWalkthroughCard
          v-else-if="cardOf"
          ref="cardRef"
          :key="`${deckGeneration}:${cardOf.id}`"
          :card="cardOf"
          :students="students"
          :session-id="sessionId"
          :revealed="revealed"
          :load-item="loadItem"
          :load-rubric="loadRubric"
          @viewed="onViewed"
          @go-review="onGoReview"
          @zoom="(urls) => { zoomUrls = urls }"
        />
      </main>

      <footer class="wt__foot">
        W/S 换同学 · 1–9 换分组 · A/D 换卡 · 空格 翻开分数 · Esc 返回
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
.wt__warn {
  margin: 0; padding: 6px 18px; font-size: var(--font-size-dense);
  color: var(--color-warning); background: var(--color-warning-subtle);
}
.wt__main { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 10px 18px; }
.wt__loading { margin: auto; color: var(--color-text-muted); }
.wt__end { margin: auto; max-width: 560px; width: 100%; overflow: auto; }
.wt__end-title { font-size: var(--font-size-h2); }
.wt__end-cat h3 { font-size: var(--font-size-dense); color: var(--color-text-muted); margin: 10px 0 4px; }
.wt__end-row {
  display: block; width: 100%; text-align: left; padding: 6px 10px; margin: 2px 0;
  border: 1px solid var(--color-border-subtle); border-radius: 8px;
  background: var(--card); cursor: pointer; font-size: var(--font-size-dense);
}
.wt__end-row:hover { border-color: var(--color-accent); }
.wt__foot {
  padding: 8px 18px; border-top: 1px solid var(--color-border-subtle);
  color: var(--color-text-muted); font-size: var(--font-size-caption); text-align: center; background: var(--card);
}
.wt__zoom {
  position: fixed; inset: 0; z-index: 70; background: rgba(20, 26, 30, 0.85);
  display: flex; gap: 16px; overflow: auto; padding: 24px; cursor: zoom-out;
}
.wt__zoom img { max-width: none; height: 92vh; margin: auto; background: #fff; }
</style>
