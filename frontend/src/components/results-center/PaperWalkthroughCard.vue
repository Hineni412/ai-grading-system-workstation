<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'

import type {
  ResolvedReviewItem,
  ReviewRubricSection,
} from '../../api/review'
import type { ResultsCenterStudent } from '../../api/results-center'
import { translateGradingReason } from '../../utils/grading-reasons'
import ReviewAnswerPanel from '../review/ReviewAnswerPanel.vue'
import AppButton from '../design-system/AppButton.vue'
import {
  classDisplayLabel,
  formatScore,
  isCompleteStudent,
  resolvedItemScore,
} from './results-overview'
import {
  itemOf,
  type WalkthroughCard,
} from './paper-walkthrough'

const props = defineProps<{
  card: WalkthroughCard
  /** 同类别的全部卡（含本卡），>1 时头部显示题号切换条 */
  siblings: ReadonlyArray<{ card: WalkthroughCard; deckIndex: number }>
  students: readonly ResultsCenterStudent[]
  sessionId: number
  revealed: boolean
  loadItem: (studentId: number, questionId: string) => Promise<ResolvedReviewItem | null>
  loadRubric: (questionId: string) => Promise<ReviewRubricSection | null>
}>()

const emit = defineEmits<{
  viewed: [studentId: number]
  'go-review': [studentId: number, questionId: string, reviewItemId: string]
  zoom: [urls: string[]]
  jump: [deckIndex: number]
}>()

const byId = new Map(props.students.map((s) => [s.student_id, s] as const))

// ---- 名单浏览：W/S 与「上一位 / 下一位」跨组连续移动，1–9 跳组首 ----
const groupIdx = ref(0)
const memberIdx = ref(0)
const group = computed(() => props.card.groups[groupIdx.value] ?? null)
const member = computed(() => group.value?.members[memberIdx.value] ?? null)
const shownStudentId = computed(() => member.value?.studentId ?? 0)
const shownStudent = computed(() => byId.get(shownStudentId.value) ?? null)

const flatRoster = computed(() => props.card.groups.flatMap(
  (g, gi) => g.members.map((m, mi) => ({ gi, mi })),
))
const flatPos = computed(() => flatRoster.value.findIndex(
  (entry) => entry.gi === groupIdx.value && entry.mi === memberIdx.value,
))
const canPrev = computed(() => flatPos.value > 0)
const canNext = computed(() => (
  flatPos.value >= 0 && flatPos.value < flatRoster.value.length - 1
))

function selectMember(gi: number, mi: number): void {
  if (!props.card.groups[gi]?.members[mi]) return
  groupIdx.value = gi
  memberIdx.value = mi
}
function shiftMember(delta: number): void {
  const target = flatRoster.value[
    Math.min(Math.max(flatPos.value + delta, 0), flatRoster.value.length - 1)
  ]
  if (target) selectMember(target.gi, target.mi)
}
function selectGroup(index: number): void {
  if (index < 0 || index >= props.card.groups.length) return
  groupIdx.value = index
  memberIdx.value = 0
}
defineExpose({ shiftMember, selectGroup })

// 本卡已看过的学生：键盘或点击切人时滚动到当前行
const viewedLocal = reactive(new Set<number>())
const rosterEl = ref<HTMLElement | null>(null)
watch([groupIdx, memberIdx], async () => {
  await nextTick()
  rosterEl.value
    ?.querySelector('.wtc__member.is-current')
    ?.scrollIntoView({ block: 'nearest' })
})

function siblingLabel(sibling: WalkthroughCard): string {
  const people = new Set(
    sibling.groups.flatMap((g) => g.members.map((m) => m.studentId)),
  ).size
  return sibling.questionId === null ? `${people}人` : `第 ${sibling.questionId} 题 · ${people}人`
}
function memberStudent(studentId: number): ResultsCenterStudent | null {
  return byId.get(studentId) ?? null
}

// ---- 答卷条目与图片 ----
const effectiveQid = computed(() => member.value?.questionId ?? '')
const reviewItem = ref<ResolvedReviewItem | null>(null)
const itemPending = ref(false)
const imageFailed = ref<string | null>(null) // 失败提示文本
let itemGeneration = 0

const rcItem = computed(() => {
  const s = shownStudent.value
  return s && member.value ? itemOf(s, member.value.questionId) : null
})

const imageUrls = computed<string[]>(() => {
  const url = reviewItem.value?.media?.crop_url
  return url ? [url] : []
})

async function loadCurrent(): Promise<void> {
  const generation = ++itemGeneration
  const sid = shownStudentId.value
  emit('viewed', sid)
  viewedLocal.add(sid)
  reviewItem.value = null
  imageFailed.value = null
  itemPending.value = true
  try {
    const item = await props.loadItem(sid, effectiveQid.value)
    if (generation !== itemGeneration) return
    reviewItem.value = item
  } catch {
    if (generation !== itemGeneration) return
    reviewItem.value = null
  } finally {
    if (generation === itemGeneration) itemPending.value = false
  }
  if (imageUrls.value.length === 0 && !itemPending.value) {
    imageFailed.value = reviewItem.value ? '暂无答卷图' : '答卷记录加载失败'
  }
}
// 名次变化大卡的成员可能同学生换题，按「学生|题」触发重载
const memberKey = computed(() => `${shownStudentId.value}|${effectiveQid.value}`)
watch(memberKey, loadCurrent, { immediate: true })

async function probeImage(url: string): Promise<void> {
  const generation = itemGeneration
  try {
    const res = await fetch(url)
    if (generation !== itemGeneration) return
    imageFailed.value = res.status === 410
      ? '原卷已清理，分数和作答记录仍保留'
      : '暂无答卷图'
  } catch {
    if (generation !== itemGeneration) return
    imageFailed.value = '暂无答卷图'
  }
}
function onImageError(url: string): void { void probeImage(url) }

// ---- 翻开分数后的面板 ----
const rubric = ref<ReviewRubricSection | null>(null)
watch([() => props.revealed, effectiveQid], async ([on, qid]) => {
  if (!on || !qid) return
  rubric.value = null
  try { rubric.value = await props.loadRubric(qid) } catch { rubric.value = null }
}, { immediate: true })

const STATUS_LABELS: Record<string, string> = {
  ai_ready: 'AI 评分',
  ai_review: '待复核',
  teacher_final: '教师确认',
  ungraded: '未评分',
  failed: '评分失败',
}
const statusLabel = computed(() => (
  STATUS_LABELS[reviewItem.value?.score_status ?? rcItem.value?.score_status ?? ''] ?? ''
))

const finalScore = computed(() => (
  reviewItem.value?.score_awarded ?? resolvedItemScore(rcItem.value)
))
const finalMax = computed(() => (
  reviewItem.value?.max_score ?? rcItem.value?.max_score ?? null
))

const steps = computed(() => {
  const item = reviewItem.value
  const section = rubric.value
  if (!item || !section) return null
  const review = item.metadata.teacher_review as
    | { revision?: number; steps?: Record<string, unknown>[] }
    | undefined
  const source = item.teacher_locked && review?.revision === item.revision
    ? review.steps
    : item.metadata.step_assessments
  if (!Array.isArray(source)) return null
  const rows = section.points.map((point) => {
    // 步骤记录可能只有 step_id（小问场景），有 part_id 时才要求一致
    const hit = source.find(
      (entry) => entry.step_id === point.step_id
        && (entry.part_id === undefined
          || entry.part_id === null
          || entry.part_id === point.part_id),
    )
    const got = typeof hit?.score_awarded === 'number' ? hit.score_awarded : null
    return { name: point.core_goal, got, max: point.score }
  })
  if (rows.length === 0 || rows.some((row) => row.got === null)) return null
  const sum = rows.reduce((acc, row) => acc + (row.got ?? 0), 0)
  if (finalScore.value === null || sum !== finalScore.value) return null
  return rows
})

const reasonText = computed(() => {
  const reason = reviewItem.value?.deduction_reason ?? rcItem.value?.review_reason
  return reason ? translateGradingReason(reason) : null
})

const classStats = computed(() => {
  const qid = member.value?.questionId
  const s = shownStudent.value
  if (!qid || !s) return null
  const peers = props.students.filter(
    (peer) => (peer.class_name ?? '') === (s.class_name ?? '') && isCompleteStudent(peer),
  )
  let resolved = 0; let full = 0; let sum = 0; let max = 0
  for (const peer of peers) {
    const item = itemOf(peer, qid)
    const score = resolvedItemScore(item)
    if (item === null || score === null) continue
    resolved += 1; sum += score; max += item.max_score
    if (score >= item.max_score) full += 1
  }
  if (resolved === 0) return null
  return { rate: max > 0 ? sum / max : null, fullFrac: full / resolved }
})

const pct = (v: number) => `${Math.round(v * 100)}%`

function goReview(): void {
  const s = shownStudent.value
  const qid = member.value?.questionId
  const rid = reviewItem.value?.review_item_id ?? rcItem.value?.review_item_id
  if (!s || !qid || !rid) return
  emit('go-review', s.student_id, qid, rid)
}
</script>

<template>
  <div class="wtc">
    <div class="wtc__head">
      <h2 class="wtc__title">{{ card.title }}</h2>
      <div
        v-if="siblings.length > 1"
        class="app-segmented wtc__seg"
        role="group"
        aria-label="本类答卷卡"
      >
        <button
          v-for="sib in siblings" :key="sib.card.id"
          type="button"
          :aria-pressed="sib.card.id === card.id"
          @click="emit('jump', sib.deckIndex)"
        >{{ siblingLabel(sib.card) }}</button>
      </div>
    </div>

    <div class="wtc__columns">
      <nav ref="rosterEl" class="wtc__roster" aria-label="本卡学生名单">
        <section v-for="(g, gi) in card.groups" :key="g.label" class="wtc__roster-group">
          <header class="wtc__roster-head">
            <span class="wtc__roster-label"><span v-if="gi < 9" class="wtc__roster-key">{{ gi + 1 }}</span>{{ g.label }}</span>
            <span class="wtc__roster-count">{{ g.members.length }}人</span>
          </header>
          <button
            v-for="(m, mi) in g.members" :key="`${m.studentId}|${m.questionId}`"
            type="button"
            class="wtc__member"
            :class="{ 'is-current': gi === groupIdx && mi === memberIdx }"
            :aria-current="gi === groupIdx && mi === memberIdx ? 'true' : undefined"
            @click="selectMember(gi, mi)"
          >
            <span class="wtc__member-name">{{ memberStudent(m.studentId)?.student_name ?? '—' }}</span>
            <span class="wtc__member-class">{{ classDisplayLabel(memberStudent(m.studentId)?.class_name) }}</span>
            <span v-if="card.questionId === null" class="wtc__member-q">第 {{ m.questionId }} 题</span>
            <span v-if="viewedLocal.has(m.studentId)" class="wtc__member-seen" title="已看">✓</span>
          </button>
        </section>
        <p v-if="card.otherLabel" class="wtc__roster-other">{{ card.otherLabel }}</p>
      </nav>

      <div class="wtc__left">
        <div v-if="group && shownStudent" class="wtc__namebar">
          <strong class="wtc__name">{{ shownStudent.student_name }}</strong>
          <span class="wtc__name-class">{{ classDisplayLabel(shownStudent.class_name) }}</span>
          <span class="wtc__name-meta">
            {{ group.label }} · 第 {{ memberIdx + 1 }} / {{ group.members.length }} 人<template v-if="card.questionId === null && member"> · 第 {{ member.questionId }} 题</template>
          </span>
          <span class="wtc__name-actions">
            <AppButton variant="ghost" size="small" :disabled="!canPrev" @click="shiftMember(-1)">上一位</AppButton>
            <AppButton variant="ghost" size="small" :disabled="!canNext" @click="shiftMember(1)">下一位</AppButton>
          </span>
        </div>
        <div class="wtc__body">
          <div class="wtc__paper">
            <p v-if="itemPending" class="wtc__loading">答卷加载中…</p>
            <template v-else-if="imageUrls.length">
              <img
                v-for="url in imageUrls"
                :key="url"
                class="wtc__img"
                :src="url"
                alt="答卷图片"
                @click="emit('zoom', imageUrls)"
                @error="onImageError(url)"
              >
            </template>
            <p v-else class="wtc__loading">{{ imageFailed ?? '暂无答卷图' }}</p>
          </div>
        </div>
      </div>

      <aside v-if="member" class="wtc__aside">
        <section v-if="revealed" class="wtc__reveal">
          <p class="wtc__score">
            <strong>{{ formatScore(finalScore) }}</strong>
            <small v-if="finalMax !== null">/ {{ formatScore(finalMax) }}</small>
            <em v-if="statusLabel">{{ statusLabel }}</em>
          </p>
          <ul v-if="steps" class="wtc__steps">
            <li v-for="(row, i) in steps" :key="i" :data-ok="row.got !== null && row.got >= row.max">
              {{ row.got !== null && row.got >= row.max ? '✓' : '✗' }}
              {{ row.name }} {{ formatScore(row.got) }}/{{ formatScore(row.max) }}
            </li>
          </ul>
          <p v-if="classStats && classStats.rate !== null" class="wtc__note">
            本题本班得分率 {{ pct(classStats.rate) }} · 满分 {{ pct(classStats.fullFrac) }}
          </p>
          <p v-if="reasonText" class="wtc__note">{{ reasonText }}</p>
          <p class="wtc__why">入选理由：{{ member.reason }}</p>
          <button
            type="button"
            class="wtc__review"
            @click="goReview"
          >去复核 / 改分</button>
        </section>
        <p v-else class="wtc__hint">先看作答，想一想这题能得几分 · 空格 翻开分数</p>
        <ReviewAnswerPanel
          :session-id="sessionId"
          :question-id="member.questionId"
          embedded
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.wtc { display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }
.wtc__head { display: flex; align-items: baseline; gap: var(--space-3); flex-wrap: wrap; }
.wtc__title {
  margin: 0; font-size: var(--font-size-h3); font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.wtc__seg { margin-left: auto; align-self: center; }
.wtc__columns { display: flex; gap: 12px; flex: 1; min-height: 0; }
.wtc__roster {
  flex: 0 0 220px; min-height: 0; overflow-y: auto; padding-right: 2px;
}
.wtc__roster-group + .wtc__roster-group { margin-top: var(--space-3); }
.wtc__roster-head {
  display: flex; align-items: baseline; gap: var(--space-2); margin-bottom: 2px;
  font-size: var(--font-size-dense); font-weight: var(--font-weight-medium);
  color: var(--color-text-secondary);
}
.wtc__roster-key {
  display: inline-block; min-width: 16px; margin-right: var(--space-1); padding: 0 3px;
  border: 1px solid var(--color-border-subtle); border-radius: 4px;
  color: var(--color-text-muted); font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular); text-align: center;
}
.wtc__roster-count {
  margin-left: auto; color: var(--color-text-muted);
  font-size: var(--font-size-caption); font-weight: var(--font-weight-regular);
}
.wtc__member {
  display: flex; align-items: baseline; gap: var(--space-2); width: 100%;
  padding: 4px 6px; border: 0; border-left: 2px solid transparent; border-radius: 0 6px 6px 0;
  background: transparent; color: var(--color-text-primary);
  font-size: var(--font-size-body); text-align: left; cursor: pointer;
}
.wtc__member:hover { background: var(--color-bg-subtle); }
.wtc__member.is-current {
  background: var(--color-accent-subtle); border-left-color: var(--color-accent);
}
.wtc__member-name { font-weight: var(--font-weight-medium); }
.wtc__member-class { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.wtc__member-q { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.wtc__member-seen {
  margin-left: auto; color: var(--color-text-muted); font-size: var(--font-size-caption);
}
.wtc__roster-other {
  margin: var(--space-2) 0 0; color: var(--color-text-muted); font-size: var(--font-size-caption);
}
.wtc__left {
  flex: 1; min-width: 0; min-height: 0;
  display: flex; flex-direction: column; gap: 6px;
}
.wtc__namebar {
  display: flex; align-items: baseline; gap: var(--space-2); flex-wrap: wrap;
}
.wtc__name {
  font-size: var(--font-size-h2); font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.wtc__name-class { font-size: var(--font-size-body); color: var(--color-text-secondary); }
.wtc__name-meta { font-size: var(--font-size-dense); color: var(--color-text-muted); }
.wtc__name-actions {
  margin-left: auto; align-self: center; display: inline-flex; gap: var(--space-1);
}
.wtc__aside {
  width: clamp(320px, 30vw, 440px); flex-shrink: 0; min-height: 0;
  display: flex; flex-direction: column; gap: 8px; overflow-y: auto;
}
@media (max-width: 1100px) {
  .wtc__columns { flex-direction: column; overflow-y: auto; }
  .wtc__roster { flex: none; width: auto; max-height: 30vh; }
  .wtc__left { flex: none; min-height: 40vh; }
  .wtc__aside { width: auto; overflow-y: visible; }
}
.wtc__body { display: flex; flex: 1; min-height: 0; }
.wtc__paper {
  flex: 1; min-height: 0; display: flex; gap: 8px; overflow: auto;
  background: var(--color-bg-subtle); border: 1px solid var(--color-border-subtle);
  border-radius: 10px; padding: 10px; justify-content: center; align-items: flex-start;
}
.wtc__img {
  width: 100%; height: 100%; object-fit: contain; cursor: zoom-in;
  background: #fff; border-radius: 4px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
/* 右栏内嵌答案面板铺满列宽（覆盖面板自带的 align-self: start） */
.wtc__aside :deep(.review-answer-panel) {
  width: 100%;
  align-self: stretch;
}
.wtc__loading { margin: auto; color: var(--color-text-muted); font-size: var(--font-size-body); }
.wtc__reveal {
  border: 1px solid var(--color-border-subtle); border-radius: 10px;
  background: var(--card); padding: 10px 14px; font-size: var(--font-size-dense); flex-shrink: 0;
}
.wtc__score { margin: 0 0 4px; display: flex; align-items: baseline; gap: 8px; }
.wtc__score strong { font-size: var(--font-size-h2); color: var(--color-accent-hover); }
.wtc__score em { font-style: normal; font-size: var(--font-size-caption); color: var(--color-text-muted); }
.wtc__steps { margin: 4px 0; padding: 0; list-style: none; font-size: var(--font-size-dense); }
.wtc__steps li[data-ok="true"] { color: var(--color-accent-hover); }
.wtc__steps li[data-ok="false"] { color: var(--color-danger); }
.wtc__note { margin: 4px 0; color: var(--color-text-secondary); }
.wtc__why { margin: 4px 0; color: var(--color-text-muted); font-size: var(--font-size-caption); }
.wtc__review {
  margin-top: 6px; border: 1px solid var(--color-accent); color: var(--color-accent-hover);
  background: transparent; border-radius: 8px; padding: 4px 14px; cursor: pointer;
}
.wtc__hint { margin: 0; text-align: center; color: var(--color-text-muted); font-size: var(--font-size-dense); }
</style>
